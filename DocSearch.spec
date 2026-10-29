# -*- mode: python ; coding: utf-8 -*-
"""Configurație PyInstaller pentru DocSearch.

Build:  build_exe.bat  (pune rezultatul în ..\dist, LÂNGĂ folderul Project,
ca build-ul să nu stea în folderul care urcă pe GitHub)

Mod "onedir": un folder dist\DocSearch cu DocSearch.exe înăuntru.
Pornește mai repede decât varianta single-file și dă mult mai rar
alarme false la antivirus. Distribui întregul folder.
"""
import os

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Iconița e opțională: dacă există icon.ico lângă spec, o folosim.
ICON = "icon.ico" if os.path.exists("icon.ico") else None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    # icon.ico se copiază și în folderul aplicației (main_window îl caută la rulare)
    datas=[("icon.ico", ".")] if ICON else [],
    hiddenimports=[
        # Tot pachetul aplicației, explicit — nu depindem de detectarea automată
        *collect_submodules("docsearch"),
        # COM pentru citirea fișierelor .doc prin Word
        "win32com",
        "win32com.client",
        "pythoncom",
        "pywintypes",
        "win32timezone",
        # proprietățile fișierelor .doc vechi (cine a salvat ultima dată)
        "olefile",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # module mari pe care nu le folosim — micșorează executabilul
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL",
        "IPython",
        "jedi",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DocSearch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX declanșează des antivirusul; mai bine fără
    console=False,        # aplicație GUI, fără fereastră neagră de consolă
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="DocSearch",
)
