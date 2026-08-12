"""Connection between the worker thread and the tk main loop.

Tk may only be touched from the thread that created the window, and the api
must not run there or the window would freeze during every request. Everything
the worker wants to show therefore travels through one queue, and everything it
wants to ask blocks the worker until the gui puts an answer back.

The core modules ask their questions on the terminal (questionary, input()).
Rather than changing them, the few functions they use for that are replaced
here by versions that open a dialog instead.
"""

import queue
import re
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


def install(bridge):
    """Route the terminal prompts of the core modules into the gui."""
    import ms_login
    import WuLpisApiClass

    def prompt(message):
        answer = bridge.ask("ask_text", message=message)
        if answer is None:
            raise ms_login.MicrosoftLoginError("2FA-Eingabe abgebrochen")
        return answer.strip()

    def select(message, choices):
        answer = bridge.ask("ask_choice", message=message, choices=list(choices))
        if answer is None:
            raise ms_login.MicrosoftLoginError("keine 2FA-Methode gewaehlt")
        return answer

    def show_number(message):
        # the number matching value is printed in a box for the terminal - in
        # the gui it gets its own window that closes once the poll is over
        found = re.search(r"(\d+)", message or "")
        bridge.post("mfa_number", number=found.group(1) if found else (message or "").strip())

    def ask_select(message, choices):
        """Replacement for the questionary prompt of the course listing."""
        labels, values = [], []
        for choice in choices:
            if isinstance(choice, dict):
                labels.append(str(choice.get("name") or choice.get("value")))
                values.append(choice.get("value"))
            else:
                labels.append(str(choice))
                values.append(choice)
        picked = bridge.ask("ask_choice", message=message, choices=labels)
        return values[labels.index(picked)] if picked in labels else None

    ms_login._prompt = prompt
    ms_login._select = select
    ms_login._print_box = show_number
    # the gui can answer questions without a terminal
    ms_login._require_tty = lambda what: None
    WuLpisApiClass.ask_select = ask_select

    original_poll = ms_login.LpisSSOSession._poll_mfa

    def poll(self, *args, **kwargs):
        try:
            return original_poll(self, *args, **kwargs)
        finally:
            bridge.post("mfa_number_done")

    ms_login.LpisSSOSession._poll_mfa = poll
