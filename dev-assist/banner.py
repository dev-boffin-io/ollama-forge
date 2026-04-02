import os
import shutil


_BANNER_FULL = r"""
  ██████╗ ███████╗██╗   ██╗      █████╗ ███████╗███████╗██╗███████╗████████╗
  ██╔══██╗██╔════╝██║   ██║     ██╔══██╗██╔════╝██╔════╝██║██╔════╝╚══██╔══╝
  ██║  ██║█████╗  ██║   ██║     ███████║███████╗███████╗██║███████╗   ██║
  ██║  ██║██╔══╝  ╚██╗ ██╔╝     ██╔══██║╚════██║╚════██║██║╚════██║   ██║
  ██████╔╝███████╗ ╚████╔╝      ██║  ██║███████║███████║██║███████║   ██║
  ╚═════╝ ╚══════╝  ╚═══╝       ╚═╝  ╚═╝╚══════╝╚══════╝╚═╝╚══════╝   ╚═╝
"""

_BANNER_COMPACT = r"""
  ██████╗ ███████╗██╗   ██╗
  ██╔══██╗██╔════╝██║   ██║
  ██║  ██║█████╗  ██║   ██║
  ██║  ██║██╔══╝  ╚██╗ ██╔╝
  ██████╔╝███████╗ ╚████╔╝
  ╚═════╝ ╚══════╝  ╚═══╝
  ASSIST
"""

_BANNER_MINIMAL = "\n  ⚡ DEV-ASSIST\n"

_TAGLINE = "  v0.1 | Terminal AI DevOps Assistant | Termux & Debian Ready"


def show_banner() -> None:
    cols = shutil.get_terminal_size(fallback=(80, 24)).columns
    if cols >= 82:
        print(_BANNER_FULL)
        print(_TAGLINE)
    elif cols >= 32:
        print(_BANNER_COMPACT)
        print(_TAGLINE)
    else:
        print(_BANNER_MINIMAL)
    print()
