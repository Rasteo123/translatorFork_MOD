Bundled Tomato Novel Downloader runtime for Fanqie chapter fetching.

Source: https://github.com/zhongbai2333/Tomato-Novel-Downloader
Bundled binary: TomatoNovelDownloader-Win64-v2.4.11.exe
License: MIT, see LICENSE in this directory.

The Qidian/Fanqie -> Rulate module starts this executable automatically with
--server when Fanqie chapter text is needed and the local Tomato Web UI is not
already running.

Optional environment overrides:
- TOMATO_NOVEL_DOWNLOADER_EXE: explicit path to another Tomato executable.
- TOMATO_NOVEL_WEB_URL: local Web UI URL, default http://127.0.0.1:18423.
- TOMATO_NOVEL_AUTO_START=0: disable automatic startup.
