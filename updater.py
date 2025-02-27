import os
import sys
import requests
import subprocess

# GitHub Repository Information
GITHUB_USER = "blueqwertz"
REPO_NAME = "wu-lpis-api"
VERSION_FILE_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{REPO_NAME}/main/version.txt"
SCRIPT_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{REPO_NAME}/main/api.py"
LOCAL_VERSION_FILE = "version.txt"
LOCAL_SCRIPT_FILE = "api.py"

def get_remote_version():
    """Fetch the latest version from GitHub."""
    try:
        response = requests.get(VERSION_FILE_URL, timeout=5)
        if response.status_code == 200:
            return response.text.strip()
    except requests.RequestException:
        pass
    return None

def get_local_version():
    """Read the local version from the file."""
    if os.path.exists(LOCAL_VERSION_FILE):
        with open(LOCAL_VERSION_FILE, "r") as f:
            return f.read().strip()
    return "0.0"

def update_script():
    """Download the latest script version and replace the existing one."""
    try:
        response = requests.get(SCRIPT_URL, timeout=5)
        if response.status_code == 200:
            with open(LOCAL_SCRIPT_FILE, "w", encoding="utf-8") as f:
                f.write(response.text)
            print("Update downloaded successfully!")
            return True
    except requests.RequestException:
        pass
    return False

def restart_program():
    """Restart the updated program."""
    print("Restarting program...")
    os.execv(sys.executable, [sys.executable, LOCAL_SCRIPT_FILE])

def check_for_update():
    """Checks for updates and applies them if available."""
    remote_version = get_remote_version()
    local_version = get_local_version()

    if remote_version and remote_version != local_version:
        print(f"New version {remote_version} found! Updating...")
        if update_script():
            with open(LOCAL_VERSION_FILE, "w") as f:
                f.write(remote_version)
            restart_program()
        else:
            print("Failed to update.")
    else:
        print("No update needed.")