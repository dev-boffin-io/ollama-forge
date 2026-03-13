#!/usr/bin/env python3

import argparse
import os
import re
import subprocess
import requests
from packaging import version


# --------------------------------------------------
# Constants
# --------------------------------------------------

GITHUB_API   = "https://api.github.com/repos/ollama/ollama/releases/latest"
FALLBACK_API = "https://api.github.com/repos/ollama/ollama/releases"
INSTALL_CMD  = "curl -fsSL https://ollama.com/install.sh | sh"

# All paths created by ollama's official install.sh
OLLAMA_PATHS = [
    "/usr/local/bin/ollama",
    "/usr/local/lib/ollama",
    "/usr/share/ollama",
    "/etc/systemd/system/ollama.service",
    "/etc/systemd/system/ollama.service.d",
]


# --------------------------------------------------
# Colors
# --------------------------------------------------

class Color:
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    RESET  = "\033[0m"


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def sudo_prefix():
    """Return ['sudo'] if not already root, else []."""
    return [] if os.geteuid() == 0 else ["sudo"]


def extract_version(text):
    """
    Extract semantic version including rc/beta/dev tags.
    Examples: 0.6.0 / 0.6.0-rc1 / 0.6.0-beta
    """
    match = re.search(r"(\d+\.\d+\.\d+(?:[-\w\.]+)?)", text)
    return match.group(1) if match else None


# --------------------------------------------------
# Version Detection
# --------------------------------------------------

def get_current_version():
    """Return installed Ollama version string, or None."""
    try:
        result = run(["ollama", "--version"])
        return extract_version(result.stdout + result.stderr)
    except FileNotFoundError:
        return None


def get_latest_version():
    """Fetch latest version from GitHub with fallback."""
    headers = {"Accept": "application/vnd.github+json"}

    # Primary endpoint
    try:
        r = requests.get(GITHUB_API, headers=headers, timeout=8)
        if r.status_code == 200:
            return r.json()["tag_name"].lstrip("v")
    except requests.RequestException:
        pass

    # Fallback — list endpoint (less rate-limited)
    try:
        r = requests.get(FALLBACK_API, headers=headers, timeout=8)
        if r.status_code == 200 and r.json():
            return r.json()[0]["tag_name"].lstrip("v")
    except requests.RequestException:
        pass

    return None


# --------------------------------------------------
# Install
# --------------------------------------------------

def install():
    if get_current_version():
        print(f"{Color.GREEN}✔ Ollama already installed{Color.RESET}")
        return

    print(f"{Color.BLUE}Installing Ollama...{Color.RESET}")
    subprocess.run(["sh", "-c", INSTALL_CMD])


# --------------------------------------------------
# Upgrade
# --------------------------------------------------

def upgrade():
    current = get_current_version()
    latest  = get_latest_version()

    if not current:
        print(f"{Color.YELLOW}Ollama not installed — installing...{Color.RESET}")
        install()
        return

    if not latest:
        print(f"{Color.RED}Unable to check latest version (network/API issue){Color.RESET}")
        return

    if version.parse(current) >= version.parse(latest):
        print(f"{Color.GREEN}Already latest version ({current}){Color.RESET}")
        return

    print(f"{Color.BLUE}Upgrading Ollama {current} → {latest}{Color.RESET}")
    subprocess.run(["sh", "-c", INSTALL_CMD])


# --------------------------------------------------
# Update (version status check)
# --------------------------------------------------

def update():
    current = get_current_version()
    latest  = get_latest_version()

    if not current:
        print(f"{Color.RED}Ollama not installed{Color.RESET}")
        return

    print(f"{Color.BLUE}Current version:{Color.RESET} {current}")

    if not latest:
        print(f"{Color.RED}Could not fetch latest version (network issue){Color.RESET}")
        return

    print(f"{Color.BLUE}Latest version :{Color.RESET} {latest}")

    if version.parse(current) < version.parse(latest):
        print(f"{Color.YELLOW}Update available — run: ollama-main upgrade{Color.RESET}")
    else:
        print(f"{Color.GREEN}Up to date{Color.RESET}")


# --------------------------------------------------
# Uninstall
# --------------------------------------------------

def uninstall():
    current = get_current_version()
    if not current:
        print(f"{Color.YELLOW}Ollama is not installed{Color.RESET}")
        return

    print(f"{Color.YELLOW}Removing Ollama...{Color.RESET}")

    prefix = sudo_prefix()

    # Stop and disable systemd service if present
    if subprocess.run(
        ["systemctl", "is-active", "--quiet", "ollama"],
        capture_output=True
    ).returncode == 0:
        subprocess.run(prefix + ["systemctl", "stop",    "ollama"])
        subprocess.run(prefix + ["systemctl", "disable", "ollama"])

    # Remove all known ollama paths
    existing = [p for p in OLLAMA_PATHS if os.path.exists(p)]
    if existing:
        subprocess.run(prefix + ["rm", "-rf"] + existing)

    # Reload systemd if service file was removed
    if any("systemd" in p for p in existing):
        subprocess.run(prefix + ["systemctl", "daemon-reload"],
                       capture_output=True)

    print(f"{Color.GREEN}Ollama removed{Color.RESET}")


# --------------------------------------------------
# CLI
# --------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="ollama-main",
        description="Ollama CLI manager — install, upgrade, check, remove",
    )

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("install",   help="Install Ollama")
    sub.add_parser("upgrade",   help="Upgrade Ollama to latest")
    sub.add_parser("update",    help="Check for available updates")
    sub.add_parser("uninstall", help="Remove Ollama from system")

    args = parser.parse_args()

    commands = {
        "install":   install,
        "upgrade":   upgrade,
        "update":    update,
        "uninstall": uninstall,
    }

    if args.command in commands:
        commands[args.command]()
    else:
        parser.print_help()


# --------------------------------------------------

if __name__ == "__main__":
    main()
