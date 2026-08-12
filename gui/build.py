#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Build the desktop app for the platform it is run on.

    python gui/build.py

Windows produces ``dist/WU-LPIS.exe``, macOS ``dist/WU-LPIS.app``. The build
has to run on the target system - PyInstaller cannot cross compile.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SPEC = os.path.join(HERE, "wulpis.spec")


def main():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller fehlt - installieren mit:")
        print("  %s -m pip install -r gui/requirements.txt" % os.path.basename(sys.executable))
        return 1

    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", SPEC]
    print("$ %s" % " ".join(command))
    result = subprocess.call(command, cwd=ROOT)
    if result == 0:
        target = "dist/WU-LPIS.app" if sys.platform == "darwin" else "dist/WU-LPIS.exe"
        print("\nfertig: %s" % os.path.join(ROOT, target))
    return result


if __name__ == "__main__":
    sys.exit(main())
