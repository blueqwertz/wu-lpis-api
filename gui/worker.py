"""The background thread that runs the LPIS actions.

Every action is handed over as a job; the result comes back through the bridge
so the window stays usable while a request is running. Jobs that can take a
while get a cancel event they are expected to watch.
"""

import queue
import threading
import traceback

from gui.lpis import Cancelled, LpisError


class Worker(threading.Thread):

    def __init__(self, bridge):
        super().__init__(daemon=True)
        self.bridge = bridge
        self.jobs = queue.Queue()
        self.current = None
        self.cancel = threading.Event()

    def submit(self, name, function):
        """Queue a job. ``function`` takes no arguments and returns a result."""
        self.jobs.put((name, function))

    def stop_current(self):
        """Ask the running job to stop.

        Every wait in the client watches this event, so it takes effect right
        away; a request that is already on its way ends at its timeout.
        """
        if not self.current:
            return False
        self.cancel.set()
        self.bridge.log("Abbruch angefordert ...")
        return True

    def run(self):
        while True:
            name, function = self.jobs.get()
            self.cancel.clear()
            self.current = name
            self.bridge.post("job_start", job=name)
            result, error = None, None
            try:
                result = function()
            except Cancelled:
                error = "abgebrochen"
            except LpisError as failure:
                # an expected outcome (course gone, page changed) - the user
                # does not need a traceback for that
                error = str(failure)
            except BaseException:
                error = traceback.format_exc()
            finally:
                self.current = None
                if self.cancel.is_set() and error is None:
                    error = "abgebrochen"
                self.cancel.clear()
            self.bridge.post("job_done", job=name, result=result, error=error)
