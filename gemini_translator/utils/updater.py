# -*- coding: utf-8 -*-
import sys
import os
import requests
from PyQt6.QtCore import QThread, pyqtSignal
from gemini_translator.api.config import GITHUB_REPO
from gemini_translator.version import APP_VERSION

class UpdateChecker(QThread):
    update_available = pyqtSignal(str, str, str) # version, description, download_url
    error_occurred = pyqtSignal(str)
    no_update = pyqtSignal()

    def run(self):
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            # Disable verify=False if possible, but keep simple timeout
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                latest_version = data.get("tag_name", "").lstrip("v")
                current_version = APP_VERSION.lstrip("v")
                
                # Basic version comparison (assuming format like "10.5.15")
                def parse_version(v):
                    return [int(x) for x in v.split('.') if x.isdigit()]
                
                latest_parsed = parse_version(latest_version)
                current_parsed = parse_version(current_version)
                
                if latest_parsed > current_parsed:
                    body = data.get("body", "Доступно новое обновление.")
                    assets = data.get("assets", [])
                    download_url = ""
                    
                    # Try to find the right asset for the platform
                    for asset in assets:
                        name = asset["name"].lower()
                        if sys.platform == "win32" and name.endswith(".exe"):
                            download_url = asset["browser_download_url"]
                            break
                        elif sys.platform == "darwin" and (name.endswith(".dmg") or name.endswith(".zip")):
                            download_url = asset["browser_download_url"]
                            break
                    
                    # Fallback to first asset if platform specific is not found
                    if not download_url and assets:
                        download_url = assets[0]["browser_download_url"]
                        
                    self.update_available.emit(latest_version, body, download_url)
                else:
                    self.no_update.emit()
            else:
                self.error_occurred.emit(f"HTTP {response.status_code}")
        except Exception as e:
            self.error_occurred.emit(str(e))
