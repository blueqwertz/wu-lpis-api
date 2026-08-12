"""Connection between the worker thread and the tk main loop.

Tk may only be touched from the thread that created the window, and the LPIS
requests must not run there or the window would freeze. Everything the worker
wants to show therefore travels through one queue, and everything it wants to
ask blocks the worker until the gui puts an answer back.
"""

import queue
import threading


class UiBridge():

    def __init__(self):
        self.events = queue.Queue()

    # -- worker -> gui -------------------------------------------------- #

    def post(self, kind, **payload):
        self.events.put((kind, payload))

    def log(self, line):
        self.events.put(("log", {"line": line}))

    def status(self, text):
        self.events.put(("status", {"text": text}))

    # -- worker -> gui -> worker ---------------------------------------- #

    def ask(self, kind, **payload):
        """Ask the gui something and wait for the answer."""
        answer = {"event": threading.Event(), "value": None}
        payload["answer"] = answer
        self.events.put((kind, payload))
        answer["event"].wait()
        return answer["value"]

    @staticmethod
    def reply(payload, value):
        answer = payload.get("answer")
        if answer is not None and not answer["event"].is_set():
            answer["value"] = value
            answer["event"].set()


class PromptUI():
    """What the login asks the user, answered by windows instead of a terminal.

    This is the interface ``ms_login.LpisSSOSession`` takes, so nothing in the
    core modules has to be replaced at runtime.
    """

    def __init__(self, bridge):
        self.bridge = bridge

    def text(self, message):
        return self.bridge.ask("ask_text", message=message) or ""

    def select(self, message, choices):
        return self.bridge.ask("ask_choice", message=message, choices=list(choices))

    def show_number(self, number):
        self.bridge.log("Zahl in der Authenticator-App eingeben: %s" % number)
        self.bridge.post("mfa_number", number=number)

    def hide_number(self):
        self.bridge.post("mfa_number_done")

    def waiting(self):
        self.bridge.log("warte auf die Bestaetigung in der Authenticator-App ...")
