# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = [('config', 'config'), ('README.md', '.'), ('gemini_translator/scripts/chatgpt_workascii_bridge.cjs', 'gemini_translator/scripts'), ('gemini_translator/scripts/chatgpt_profile_launcher.cjs', 'gemini_translator/scripts'), ('qidian_rulate/tags.txt', 'qidian_rulate'), ('ranobelib/__init__.py', 'ranobelib'), ('ranobelib/api_upload.py', 'ranobelib'), ('ranobelib/constants.py', 'ranobelib'), ('ranobelib/dependencies.py', 'ranobelib'), ('ranobelib/dialogs.py', 'ranobelib'), ('ranobelib/main.py', 'ranobelib'), ('ranobelib/main_window.py', 'ranobelib'), ('ranobelib/models.py', 'ranobelib'), ('ranobelib/parsers.py', 'ranobelib'), ('ranobelib/ranobelib-upload.mjs', 'ranobelib'), ('ranobelib/ranobelib_uploader_v12.py', 'ranobelib'), ('ranobelib/utils.py', 'ranobelib'), ('ranobelib/workers.py', 'ranobelib')]
datas += collect_data_files('PyQt6')
datas += collect_data_files('certifi')
datas += collect_data_files('docx')
datas += collect_data_files('emoji')
datas += collect_data_files('jieba')
datas += collect_data_files('lxml')
datas += collect_data_files('werkzeug')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['PyQt6.sip', 'docx', 'playwright.sync_api', 'google.genai', 'google.genai.types'],
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
    name='translatorFork_MOD',
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
    icon=['gemini_translator/GT.ico'],
)

import sys
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='GeminiTranslator.app',
        icon=None,
        bundle_identifier='com.siberianteam.translatorfork',
    )
