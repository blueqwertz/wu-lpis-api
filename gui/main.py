#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Start the desktop app:  python gui/main.py"""

import os
import sys

if __package__ in (None, ""):
    # started as a script, so the repository has to be importable
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    from gui import runtime
    from gui.bridge import UiBridge

    bridge = UiBridge()
    # replaces stdout/stderr and sets the working directory - has to happen
    # before the core modules are imported
    directory = runtime.prepare(on_line=bridge.log, on_status=bridge.status)

    try:
        from gui.app import LpisGui
        LpisGui(bridge).mainloop()
    except Exception:
        # a windowed build has no console to print to, so a crash would leave
        # nothing behind at all
        import traceback
        report = traceback.format_exc()
        try:
            with open(os.path.join(directory, "crash.log"), "w", encoding="utf-8") as handle:
                handle.write(report)
        except OSError:
            pass
        try:
            import tkinter.messagebox as messagebox
            messagebox.showerror("WU LPIS", "Das Programm konnte nicht starten:\n\n%s"
                                            % report.strip().splitlines()[-1])
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
