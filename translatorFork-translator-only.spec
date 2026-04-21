# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


PROJECT_ROOT = Path.cwd()


datas = [
    ('config', 'config'),
]
datas += collect_data_files('PyQt6')
datas += collect_data_files('emoji')
datas += collect_data_files('jieba')
datas += collect_data_files('lxml')
datas += collect_data_files('werkzeug')


a = Analysis(
    ['main_translator_only.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['PyQt6.sip', 'google.genai', 'google.genai.types'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'docx',
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
    a.binaries,
    a.datas,
    [],
    name='translatorFork-translator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['gemini_translator\\GT.ico'],
)
