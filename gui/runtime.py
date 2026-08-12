"""Environment setup that has to happen before the core modules are imported.

Two things need to be in place first:

* ``sys.stdout``/``sys.stderr`` are replaced by streams that hand every line to
  the gui. ``logger.py`` binds loguru to ``sys.stdout`` at import time, so the
  replacement only takes effect if it happens before that import. In a windowed
  build there is no console at all and ``sys.stdout`` is ``None``, which would
  otherwise break the import outright.
* the working directory, because ``logs/`` and ``sessions/`` are relative paths.
"""

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

# loguru is told to colorize unconditionally, and the course listing prints its
# own colour codes - none of that belongs in a text widget
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def is_frozen():
    return getattr(sys, "frozen", False)


def resource_dir():
    """Where the bundled read-only files (version.txt) live."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return REPO_ROOT


def data_dir():
    """Where logs/ and sessions/ are written.

    From source that is the repository itself, so the gui and the command line
    tool share their stored sessions. A frozen app must not write next to its
    executable (read-only on macOS), so it uses the usual per-user location.
    """
    if not is_frozen():
        return REPO_ROOT
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "wu-lpis-api")


def version():
    try:
        with open(os.path.join(resource_dir(), "version.txt"), "r") as handle:
            return handle.read().strip()
    except OSError:
        return "?"


class TeeStream(io.TextIOBase):
    """Sends everything written to it to the gui (and to the real console).

    Output that ends in a carriage return is a progress line (the course
    listing draws a bar, the registration counts down): it replaces itself
    instead of piling up, so it goes to the status bar rather than the log.
    """

    encoding = "utf-8"

    def __init__(self, original, on_line, on_status):
        self._original = original
        self._on_line = on_line
        self._on_status = on_status
        self._pending = ""

    def isatty(self):
        return False

    def writable(self):
        return True

    def write(self, text):
        if not text:
            return 0
        if self._original is not None:
            try:
                self._original.write(text)
            except Exception:
                self._original = None
        self._pending += ANSI.sub("", text)
        while True:
            match = re.search(r"[\r\n]", self._pending)
            if not match:
                break
            line = self._pending[:match.start()]
            separator = self._pending[match.start()]
            self._pending = self._pending[match.start() + 1:]
            if separator == "\r" and self._pending.startswith("\n"):
                self._pending = self._pending[1:]
                separator = "\n"
            if separator == "\n":
                self._on_line(line.rstrip())
            else:
                self._on_status(line.strip())
        return len(text)

    def flush(self):
        if self._original is not None:
            try:
                self._original.flush()
            except Exception:
                self._original = None


def prepare(on_line, on_status):
    """Install the streams, fix up sys.path and the working directory."""
    directory = data_dir()
    os.makedirs(directory, exist_ok=True)
    try:
        os.chdir(directory)
    except OSError:
        pass

    for path in (resource_dir(), REPO_ROOT):
        if path not in sys.path:
            sys.path.insert(0, path)

    sys.stdout = TeeStream(sys.stdout, on_line, on_status)
    sys.stderr = TeeStream(sys.stderr, on_line, on_status)
    return directory
