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
    hiddenimports=['PyQt6.sip', 'docx', 'playwright.sync_api', 'google.genai', 'google.genai.types', 'qoder_agent_sdk'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

import sys
exe_name = 'GeminiTranslator' if sys.platform == 'darwin' else 'translatorFork_MOD'

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=exe_name,
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
    icon=['gemini_translator/GT.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=exe_name,
)

import sys
if sys.platform == 'win32':
    # Портативная one-file сборка из того же анализа: один exe со всем
    # содержимым внутри. Идёт в релиз рядом с инсталлером.
    portable_exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name='GeminiTranslator-Portable',
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
        icon=['gemini_translator/GT.ico'],
    )

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='GeminiTranslator.app',
        icon='gemini_translator/GT_mac.icns',
        bundle_identifier='com.siberianteam.translatorfork',
        info_plist={
            'NSPrincipalClass': 'NSApplication',
            'NSHighResolutionCapable': 'True',
            'NSUserNotificationAlertStyle': 'alert',
            'CFBundleName': 'GeminiTranslator',
            'CFBundleDisplayName': 'GeminiTranslator',
            'LSUIElement': False,
        }
    )
