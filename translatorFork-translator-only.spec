# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files
from pyinstaller_config import LAZY_HANDLER_HIDDEN_IMPORTS


PROJECT_ROOT = Path.cwd().resolve()
ICON_PATH = PROJECT_ROOT / "gemini_translator" / "GT.ico"


datas = [
    ('config', 'config'),
]
datas += collect_data_files('PyQt6')
datas += collect_data_files('certifi')
datas += collect_data_files('docx')
datas += collect_data_files('emoji')
datas += collect_data_files('jieba')
datas += collect_data_files('lxml')
datas += collect_data_files('qoder_agent_sdk')
datas += collect_data_files('werkzeug')


a = Analysis(
    ['main_translator_only.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'PyQt6.sip',
        'docx',
        'pypdf',
        'google.genai',
        'google.genai.types',
        *LAZY_HANDLER_HIDDEN_IMPORTS,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'edge_tts',
        'gemini_reader_v3',
        'nltk',
        'playwright',
        'playwright.async_api',
        'playwright.sync_api',
        'pyaudio',
        'pydub',
        'ranobelib',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='translatorFork-translator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ICON_PATH)] if sys.platform == "win32" and ICON_PATH.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='translatorFork-translator',
)
