import os
import re
import sys
import requests
import zipfile
import shutil
from logger import logger

# GitHub Repository Information
GITHUB_USER = "blueqwertz"
REPO_NAME = "wu-lpis-api"
BRANCH = "master"
VERSION_FILE_URL = f"https://raw.githubusercontent.com/{GITHUB_USER}/{REPO_NAME}/{BRANCH}/version.txt"
ZIP_URL = f"https://github.com/{GITHUB_USER}/{REPO_NAME}/archive/{BRANCH}.zip"
LOCAL_VERSION_FILE = "version.txt"
LOCAL_REPO_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_ZIP_PATH = os.path.join(LOCAL_REPO_DIR, "update.zip")

def get_remote_version():
    """Fetch the latest version number from the remote version.txt file."""
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

def version_key(value):
    """"1.0.6" -> (1, 0, 6), so versions compare by number and not by text."""
    return tuple(int(part) for part in re.findall(r"\d+", value or "")) or (0,)


def download_and_extract_zip():
    """Download the latest repository ZIP file and extract it."""
    try:
        # Download ZIP file
        logger.info("downloading the latest version...")
        response = requests.get(ZIP_URL, stream=True, timeout=10)
        if response.status_code == 200:
            with open(TEMP_ZIP_PATH, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024):
                    f.write(chunk)
        else:
            logger.opt(colors=True).error("<red>failed to download the update</red>")
            return False

        # Extract the ZIP file
        logger.info("extracting update...")
        temp_extract_path = os.path.join(LOCAL_REPO_DIR, "temp_update")
        if os.path.exists(temp_extract_path):
            shutil.rmtree(temp_extract_path)
        with zipfile.ZipFile(TEMP_ZIP_PATH, "r") as zip_ref:
            zip_ref.extractall(temp_extract_path)

        # Find extracted folder (GitHub adds repo name and branch in extraction)
        extracted_folder = os.path.join(temp_extract_path, f"{REPO_NAME}-{BRANCH}")

        # Move files from extracted folder to the current directory
        for item in os.listdir(extracted_folder):
            s = os.path.join(extracted_folder, item)
            d = os.path.join(LOCAL_REPO_DIR, item)
            if os.path.isdir(s):
                if os.path.exists(d):
                    shutil.rmtree(d)
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)

        # Clean up temporary files
        shutil.rmtree(temp_extract_path)
        os.remove(TEMP_ZIP_PATH)

        logger.opt(colors=True).info("<green>update installed successfully!</green>")
        return True

    except Exception as e:
        logger.opt(colors=True).error("<red>updating error: %s</red>" % str(e))
        return False

def restart_program():
    """Restart the updated program."""
    logger.opt(colors=True).info("restarting program...")
    os.execv(sys.executable, [sys.executable] + sys.argv)

def check():
    """Checks for updates and applies them if available."""
    # An update overwrites every file in this directory with the released
    # tree. That is right for a normal checkout and wrong for a worktree
    # holding changes that are not on master yet: it would quietly replace
    # them and restart, so the run continues with code nobody asked for.
    if os.environ.get("LPIS_SKIP_UPDATE"):
        logger.opt(colors=True).info(
            "<yellow>LPIS_SKIP_UPDATE is set - not checking for updates</yellow>")
        return

    remote_version = get_remote_version()
    local_version = get_local_version()

    if remote_version and version_key(remote_version) > version_key(local_version):
        logger.opt(colors=True).info(f"<yellow>new version {remote_version} found! updating...</yellow>")
        if download_and_extract_zip():
            with open(LOCAL_VERSION_FILE, "w") as f:
                f.write(remote_version)
            restart_program()
        else:
            logger.opt(colors=True).error("<red>failed to update repository</red>")
    elif remote_version and version_key(remote_version) < version_key(local_version):
        # never pull an older tree over a newer one - that would silently
        # revert a local fix that has not been pushed yet
        logger.opt(colors=True).info(
            "<yellow>local version %s is ahead of %s on the server - skipping update</yellow>"
            % (local_version, remote_version))
    else:
        logger.opt(colors=True).info("<green>latest version %s is already installed</green>" % (local_version))
