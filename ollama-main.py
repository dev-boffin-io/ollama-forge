#!/usr/bin/env python3

import argparse
import re
import subprocess
import requests
from packaging import version


# --------------------------------------------------
# Constants
# --------------------------------------------------

GITHUB_API = "https://api.github.com/repos/ollama/ollama/releases/latest"
INSTALL_CMD = "curl -fsSL https://ollama.com/install.sh | sh"


# --------------------------------------------------
# Colors
# --------------------------------------------------

class Color:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    RESET = "\033[0m"


# --------------------------------------------------
# Helper
# --------------------------------------------------

def run(cmd):
    """Run subprocess command."""
    return subprocess.run(cmd, capture_output=True, text=True)


# --------------------------------------------------
# Version Detection
# --------------------------------------------------

def get_current_version():
    """Return installed Ollama version."""
    try:
        result = run(["ollama", "--version"])
        output = result.stdout + result.stderr

        match = re.search(r"(\d+\.\d+\.\d+)", output)
        return match.group(1) if match else None

    except FileNotFoundError:
        return None


def get_latest_version():
    """Fetch latest version from GitHub."""
    try:
        headers = {"Accept": "application/vnd.github+json"}

        response = requests.get(GITHUB_API, headers=headers, timeout=10)
        response.raise_for_status()

        return response.json()["tag_name"].lstrip("v")

    except Exception:
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
    latest = get_latest_version()

    # Auto install if not installed
    if not current:
        print(f"{Color.YELLOW}Ollama not installed. Installing...{Color.RESET}")
        install()
        return

    # Already latest
    if latest and version.parse(current) >= version.parse(latest):
        print(f"{Color.GREEN}Already latest version ({current}){Color.RESET}")
        return

    print(f"{Color.BLUE}Upgrading Ollama...{Color.RESET}")

    subprocess.run(["sh", "-c", INSTALL_CMD])


# --------------------------------------------------
# Update (Version Status)
# --------------------------------------------------

def update():

    current = get_current_version()
    latest = get_latest_version()

    if not current:
        print(f"{Color.RED}Ollama not installed{Color.RESET}")
        return

    print(f"{Color.BLUE}Current version:{Color.RESET} {current}")
    print(f"{Color.BLUE}Latest version :{Color.RESET} {latest}")

    if latest and version.parse(current) < version.parse(latest):
        print(f"{Color.YELLOW}Update available{Color.RESET}")
    else:
        print(f"{Color.GREEN}Up to date{Color.RESET}")


# --------------------------------------------------
# Uninstall
# --------------------------------------------------

def uninstall():

    print(f"{Color.YELLOW}Removing Ollama...{Color.RESET}")

    subprocess.run(["sudo", "rm", "-rf", "/usr/local/bin/ollama"])
    subprocess.run(["sudo", "rm", "-rf", "/usr/local/lib/ollama"])

    print(f"{Color.GREEN}Ollama removed{Color.RESET}")


# --------------------------------------------------
# CLI
# --------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        prog="ollama-manager",
        description="Professional Ollama Manager CLI",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("install", help="Install Ollama")
    sub.add_parser("upgrade", help="Upgrade Ollama")
    sub.add_parser("update", help="Check for updates")
    sub.add_parser("uninstall", help="Remove Ollama")

    args = parser.parse_args()

    commands = {
        "install": install,
        "upgrade": upgrade,
        "update": update,
        "uninstall": uninstall,
    }

    if args.command in commands:
        commands[args.command]()
    else:
        parser.print_help()


# --------------------------------------------------

if __name__ == "__main__":
    main()
