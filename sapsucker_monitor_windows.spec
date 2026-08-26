# -*- coding: utf-8 -*-
"""Canonical PyInstaller spec for the Windows monitor .exe.

Built by ``.github/workflows/build_executable.yml`` and attached to releases.
Windows only: SAP GUI Scripting is a Windows COM API, so there is no other
platform to target.

``pywin32`` needs explicit help here. :mod:`sapsucker._com` imports
``pythoncom`` and ``win32com.client`` inside a ``try/except ImportError`` so the
package stays importable on other platforms — which means PyInstaller's static
analysis does not see them, and ``win32com.client`` resolves dispatch
dynamically on top of that. Hence the hidden imports below; without them the
binary builds fine and then fails at runtime on the first COM call.

Set ``SAPSUCKER_BUILD_NAME`` to control the output filename.
"""

import os

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = [
    "pythoncom",
    "pywintypes",
    "win32com",
    "win32com.client",
]
hiddenimports += collect_submodules("win32com.client")

name = os.environ.get("SAPSUCKER_BUILD_NAME", "sapsucker_monitor_windows")

a = Analysis(
    ["src\\sapsucker\\monitor_cli.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
