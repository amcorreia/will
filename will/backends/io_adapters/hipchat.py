import json
import logging
import requests
import random
import sys
import traceback

from will import settings
from .base import IOBackend
from .xmpp import WillXMPPClientMixin
from multiprocessing import Process, Queue
from will.backends.io_adapters.base import Event, Message
from multiprocessing.queues import Empty


ROOM_NOTIFICATION_URL = "https://%(server)s/v2/room/%(room_id)s/notification?auth_token=%(token)s"
ROOM_TOPIC_URL = "https://%(server)s/v2/room/%(room_id)s/topic?auth_token=%(token)s"
PRIVATE_MESSAGE_URL = "https://%(server)s/v2/user/%(user_id)s/message?auth_token=%(token)s"
SET_TOPIC_URL = "https://%(server)s/v2/room/%(room_id)s/topic?auth_token=%(token)s"
USER_DETAILS_URL = "https://%(server)s/v2/user/%(user_id)s?auth_token=%(token)s"
ALL_USERS_URL = ("https://%(server)s/v2/user?auth_token=%(token)s&start-index"
                 "=%(start_index)s&max-results=%(max_results)s")

UNSURE_REPLIES = [
    "Hmm.  I'm not sure what to say.",
    "I didn't understand that.",
    "I heard you, but I'm not sure what to do.",
    "Darn.  I'm not sure what that means.  Maybe you can teach me?",
    "I really wish I knew how to do that.",
    "Hm. I understood you, but I'm not sure what to do.",
]


class HipChatBackend(IOBackend):
    friendly_name = "HipChat"
    internal_name = "will.backends.io_adapters.hipchat"
    use_stdin = False
    stdin_processes = []
    io_processes = ["start", "event_handler_loop"]

    def send_direct_message(self, user_id, message_body, html=False, notify=False, **kwargs):
        # user_id = settings.USERNAME.split('@')[0].split('_')[1]

        if kwargs:
            logging.warn("Unknown keyword args for send_direct_message: %s" % kwargs)

        format = "text"
        if html:
            format = "html"

        try:
            # https://www.hipchat.com/docs/apiv2/method/private_message_user
            url = PRIVATE_MESSAGE_URL % {"server": settings.HIPCHAT_SERVER,
                                         "user_id": user_id,
                                         "token": settings.V2_TOKEN}
            data = {
                "message": message_body,
                "message_format": format,
                "notify": notify,
            }
            headers = {'Content-type': 'application/json', 'Accept': 'text/plain'}
            requests.post(url, headers=headers, data=json.dumps(data), **settings.REQUESTS_OPTIONS)
        except:
            import traceback; traceback.print_exc();
            logging.critical("Error in send_direct_message: \n%s" % traceback.format_exc())


    def send_room_message(self, room_id, message_body, html=False, color="green", notify=False, **kwargs):
        if kwargs:
            logging.warn("Unknown keyword args for send_room_message: %s" % kwargs)

        format = "text"
        if html:
            format = "html"

        try:
            # https://www.hipchat.com/docs/apiv2/method/send_room_notification
            url = ROOM_NOTIFICATION_URL % {"server": settings.HIPCHAT_SERVER,
                                           "room_id": room_id,
                                           "token": settings.V2_TOKEN}
            data = {
                "message": message_body,
                "message_format": format,
                "color": color,
                "notify": notify,
            }
            headers = {'Content-type': 'application/json', 'Accept': 'text/plain'}
            requests.post(url, headers=headers, data=json.dumps(data), **settings.REQUESTS_OPTIONS)
        except:
            logging.critical("Error in send_room_message: \n%s" % traceback.format_exc())

    def set_room_topic(self, room_id, topic):
        try:
            # https://www.hipchat.com/docs/apiv2/method/send_room_notification
            url = ROOM_TOPIC_URL % {"server": settings.HIPCHAT_SERVER,
                                    "room_id": room_id,
                                    "token": settings.V2_TOKEN}
            data = {
                "topic": topic,
            }
            headers = {'Content-type': 'application/json', 'Accept': 'text/plain'}
            requests.put(url, headers=headers, data=json.dumps(data), **settings.REQUESTS_OPTIONS)
        except:
            logging.critical("Error in set_room_topic: \n%s" % traceback.format_exc())

    def get_hipchat_user(self, user_id, q=None):
        url = USER_DETAILS_URL % {"server": settings.HIPCHAT_SERVER,
                                  "user_id": user_id,
                                  "token": settings.V2_TOKEN}
        r = requests.get(url, **settings.REQUESTS_OPTIONS)
        if q:
            q.put(r.json())
        else:
            return r.json()

    @property
    def full_hipchat_user_list(self):
        if not hasattr(self, "_full_hipchat_user_list"):
            full_roster = {}

            # Grab the first roster page, and populate full_roster
            url = ALL_USERS_URL % {"server": settings.HIPCHAT_SERVER,
                                   "token": settings.V2_TOKEN,
                                   "start_index": 0,
                                   "max_results": 1000}
            r = requests.get(url, **settings.REQUESTS_OPTIONS)
            for user in r.json()['items']:
                full_roster["%s" % (user['id'],)] = user

            # Keep going through the next pages until we're out of pages.
            while 'next' in r.json()['links']:
                url = "%s&auth_token=%s" % (r.json()['links']['next'], settings.V2_TOKEN)
                r = requests.get(url, **settings.REQUESTS_OPTIONS)

                for user in r.json()['items']:
                    full_roster["%s" % (user['id'],)] = user

            self._full_hipchat_user_list = full_roster
        return self._full_hipchat_user_list

    def event_handler_loop(self, name, input_queue, output_queue):
        self.name = name
        self.input_queue = input_queue
        self.output_queue = output_queue

        while True:
            # Output Queue
            try:
                output_event = self.output_queue.get(timeout=0.1)
                kwargs = {}
                if hasattr(output_event, "kwargs"):
                    kwargs.update(output_event.kwargs)
                if output_event.type in ["say", "reply"]:
                    if output_event.source_message.hipchat_message.type == "groupchat":
                        self.send_room_message(
                            output_event.source_message.hipchat_message.room.room_id,
                            output_event.content,
                            **kwargs
                        )
                    else:
                        output_event.source_message
                        user_id = output_event.source_message.hipchat_message.sender["hipchat_id"]
                        self.send_direct_message(
                            user_id,
                            output_event.content,
                            **kwargs
                        )

                elif output_event.type == "no_response" and output_event.source_message.is_direct:
                    if output_event.source_message.hipchat_message.type == "groupchat":
                        self.send_room_message(
                            output_event.source_message.hipchat_message.room.room_id,
                            random.choice(UNSURE_REPLIES),
                            **kwargs
                        )
                    else:
                        self.send_direct_message(
                            output_event.source_message.hipchat_message.sender["hipchat_id"],
                            random.choice(UNSURE_REPLIES),
                            **kwargs
                        )

            except Empty:
                pass

    def start(self, name, input_queue, output_queue):
        self.name = name
        self.input_queue = input_queue
        self.output_queue = output_queue

        self.client = WillXMPPClientMixin("%s/bot" % settings.USERNAME, settings.PASSWORD)
        # Sort this out.
        bootstrapped = False
        try:
            self.client.start_xmpp_client(input_queue=self.input_queue, output_queue=output_queue)
            self.client.connect()
            bootstrapped = True
        except Exception, e:
            self.startup_error("Error bootstrapping xmpp", e)

        if bootstrapped:
            self.xmpp_thread = Process(target=self.client.process, kwargs={"block": True})
            self.xmpp_thread.start()
            self._event_handler_loop_thread = Process(target=self._event_handler_loop)
            self._event_handler_loop_thread.start()
