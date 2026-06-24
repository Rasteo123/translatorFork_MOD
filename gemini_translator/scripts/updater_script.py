import sys
import os
import time
import subprocess
import shutil
import zipfile

def main():
    if len(sys.argv) < 3:
        print("Usage: updater_script.py <path_to_update_file> <path_to_main_executable>")
        sys.exit(1)
        
    update_file = sys.argv[1]
    main_exe = sys.argv[2]
    
    # Wait for the main application to close
    print("Waiting for application to close...")
    time.sleep(3)
    
    try:
        if update_file.endswith('.exe'):
            # For Windows: run the installer silently
            print(f"Running installer: {update_file}")
            # MAGICAL FIX FOR WINDOWS: Remove Mark of the Web (Zone.Identifier) to bypass SmartScreen
            try:
                subprocess.call(['powershell', '-Command', f"Unblock-File -LiteralPath '{update_file}'"])
            except Exception:
                pass
            subprocess.Popen([update_file, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/FORCECLOSEAPPLICATIONS'])
        elif update_file.endswith('.zip') and sys.platform == "darwin":
            # For macOS: Extract the zip and replace the .app bundle
            # Find the path to the current .app bundle
            # main_exe usually points to GeminiTranslator.app/Contents/MacOS/GeminiTranslator
            app_bundle_path = main_exe
            while app_bundle_path != '/' and not app_bundle_path.endswith('.app'):
                app_bundle_path = os.path.dirname(app_bundle_path)
                
            if app_bundle_path.endswith('.app'):
                parent_dir = os.path.dirname(app_bundle_path)
                
                # Extract zip to a temporary folder next to the app
                temp_extract_dir = os.path.join(parent_dir, "update_extracted")
                os.makedirs(temp_extract_dir, exist_ok=True)
                
                with zipfile.ZipFile(update_file, 'r') as zip_ref:
                    zip_ref.extractall(temp_extract_dir)
                    
                # Find the new .app inside the extracted folder
                new_app_path = None
                for item in os.listdir(temp_extract_dir):
                    if item.endswith('.app'):
                        new_app_path = os.path.join(temp_extract_dir, item)
                        break
                        
                if new_app_path:
                    # Remove the old app and move the new one in place
                    shutil.rmtree(app_bundle_path)
                    shutil.move(new_app_path, app_bundle_path)
                    
                    # MAGICAL FIX FOR MACOS: Remove quarantine attribute!
                    # This prevents macOS from asking for Gatekeeper permission again
                    subprocess.call(['xattr', '-cr', app_bundle_path])
                    
                    # Cleanup and restart
                    shutil.rmtree(temp_extract_dir)
                    subprocess.Popen(['open', app_bundle_path])
                else:
                    print("Could not find .app in the downloaded zip.")
        elif update_file.endswith('.dmg'):
            # Fallback for DMG
            subprocess.Popen(['open', update_file])
    except Exception as e:
        print(f"Error during update: {e}")

if __name__ == "__main__":
    main()
