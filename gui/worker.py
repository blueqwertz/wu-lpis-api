"""The background thread that talks to LPIS.

Every action is handed over as a job; the result comes back through the bridge
so the window stays usable while a request is running.
"""

import ctypes
import queue
import threading
import time
import traceback
from types import SimpleNamespace


class Cancelled(Exception):
    """Raised inside the worker when the user stops a running job."""


class Worker(threading.Thread):

    def __init__(self, bridge):
        super().__init__(daemon=True)
        self.bridge = bridge
        self.jobs = queue.Queue()
        self.current = None
        self.cancelling = False

    def submit(self, name, function):
        self.jobs.put((name, function))
        self.bridge.post("queued", job=name)

    def stop_current(self):
        """Best effort cancel of the running job.

        The waiting loops of the registration are plain python loops, so an
        exception raised into the thread lands there. During a network call it
        only takes effect once the call returns.
        """
        if not self.current or not self.is_alive():
            return False
        self.cancelling = True
        ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(self.ident), ctypes.py_object(Cancelled))
        self.bridge.log("Abbruch angefordert ...")
        return True

    def _drop_pending_cancel(self):
        """Take back a cancel that arrived after the job was already over."""
        try:
            # an empty py_object is the NULL that clears a pending exception
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(self.ident), ctypes.py_object())
        except Exception:
            pass

    def run(self):
        while True:
            name, function = self.jobs.get()
            self.current = name
            self.bridge.post("job_start", job=name)
            result, error = None, None
            try:
                result = function()
            except Cancelled:
                error = "abgebrochen"
            except BaseException:
                error = traceback.format_exc()
            finally:
                self.current = None
                if self.cancelling:
                    # the cancel may have missed the job - it must not hit the
                    # next one
                    self._drop_pending_cancel()
                    self.cancelling = False
                    if error is None:
                        error = "abgebrochen"
            self.bridge.post("job_done", job=name, result=result, error=error)


class LpisSession():
    """Holds the logged in api object and the actions the gui offers."""

    def __init__(self, bridge):
        self.bridge = bridge
        self.api = None
        self.username = ""
        self.landing_url = None
        self.logfile_handler = None

    # ------------------------------------------------------------------ #

    @property
    def connected(self):
        return self.api is not None

    def login(self, user, password):
        from logger import logger, set_action, set_user_name
        from WuLpisApiClass import WuLpisApi

        username = user.username
        sessiondir = (user.get("sessiondir") or "").strip() or None
        if sessiondir and not sessiondir.endswith(("/", "\\")):
            import os
            sessiondir += os.sep

        args = SimpleNamespace(
            action="gui",
            msdomain=(user.get("msdomain") or "s.wu.ac.at").strip(),
            mfa_method=(user.get("mfa_method") or "").strip() or None,
            sectionpoint=(user.get("sectionpoint") or "").strip() or None,
            planobject=None, course=None, course2=None,
            offset=float(user.get("offset") or 0.7),
            sessiondir=sessiondir,
        )

        if self.logfile_handler is not None:
            logger.remove(self.logfile_handler)
        self.logfile_handler = logger.add(
            "logs/output-%s.log" % username, level="INFO", colorize=False)
        set_user_name(username)
        set_action("gui")

        self.api = None
        api = WuLpisApi(username, password, args, sessiondir)
        self.api = api
        self.username = username
        self.landing_url = api.browser.geturl()
        return self.sectionpoints()

    def logout(self):
        if self.api is not None:
            self.api.logout()
            self.api = None
            self.landing_url = None
        return True

    # ------------------------------------------------------------------ #

    def _require(self):
        if self.api is None:
            raise RuntimeError("nicht angemeldet")

    def _reset(self):
        """Go back to the page that carries the study selection form."""
        self._require()
        from logger import logger
        try:
            self.api.browser.open(self.landing_url)
            self.api.browser.select_form('ea_stupl')
            return
        except Exception:
            logger.info("LPIS-Seite nicht mehr gueltig, neuer Login")
        self.api.login()
        self.landing_url = self.api.browser.geturl()

    def sectionpoints(self):
        """The studies offered in the dropdown of the LPIS start page."""
        self._reset()
        form = self.api.browser.form
        control = form.find_control(form.controls[0].name)
        found = []
        for item in control.get_items():
            if item.attrs.get('id') == "abgewaehlt":
                continue
            labels = item.get_labels()
            found.append({"name": labels[0].text.strip() if labels else item.name,
                          "value": item.name})
        return found

    def infos(self, sectionpoint):
        self._reset()
        self.api.args.sectionpoint = sectionpoint or None
        data = self.api.infos()
        return (data or {}).get("pp") or {}

    def grades(self):
        self._require()
        return self.api.grades() or []

    def registration(self, sectionpoint, planobject, course, course2, offset):
        self._reset()
        self.api.args.sectionpoint = sectionpoint or None
        self.api.args.planobject = planobject
        self.api.args.course = course
        self.api.args.course2 = course2 or course
        self.api.args.offset = float(offset)
        self.api.registration()
        return True
