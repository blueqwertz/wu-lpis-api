# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build description, used by gui/build.py.

Windows gets a single .exe, macOS an .app bundle. Both are built from the same
description - only the parts that the platforms really need differ.
"""

import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
NAME = "WU-LPIS"

# the core modules are imported at runtime, so they are named explicitly
HIDDEN = [
    "WuLpisApiClass", "ms_login", "logger", "users", "updater",
    "mechanize", "bs4", "lxml", "lxml.etree", "lxml._elementpath",
    "ntplib", "loguru", "requests", "questionary",
]

a = Analysis(
    [os.path.join(ROOT, "gui", "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[(os.path.join(ROOT, "version.txt"), ".")],
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tests", "numpy", "pandas", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    # one directory inside the .app - a onefile bundle would unpack itself on
    # every start
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME,
              console=False, debug=False, strip=False, upx=False)
    collect = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=NAME)
    app = BUNDLE(
        collect,
        name=NAME + ".app",
        icon=None,
        bundle_identifier="at.ac.wu.lpis.gui",
        info_plist={
            "CFBundleName": "WU LPIS",
            "CFBundleDisplayName": "WU LPIS",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name=NAME,
        console=False,
        debug=False,
        strip=False,
        upx=False,
        disable_windowed_traceback=False,
    )
