"""
Tunnel Helper — Start cloudflared or ngrok tunnel with auto-expiry warnings.

Improvements:
- Auto-expiry warning after configurable lifetime
- Session timer display
- Rich status output
- Config-driven defaults
"""

from __future__ import annotations

import re
import os
import time
import threading
import subprocess
import shutil

DEFAULT_PORT = "3000"
DEFAULT_MAX_MINUTES = 120   # warn after 2 hours


def run(text: str = "") -> None:
    """Start a tunnel on specified port."""
    # Load config for defaults
    try:
        from core.config import load_config
        cfg = load_config()
        if hasattr(cfg, "tunnel"):
            default_port = cfg.tunnel.default_port
            auto_restart = cfg.tunnel.auto_restart
            restart_delay = cfg.tunnel.restart_delay_seconds
            max_minutes = cfg.tunnel.max_lifetime_minutes
        else:
            d = cfg.get("tunnel", {})
            default_port = d.get("default_port", DEFAULT_PORT)
            auto_restart = d.get("auto_restart", True)
            restart_delay = d.get("restart_delay_seconds", 3)
            max_minutes = d.get("max_lifetime_minutes", DEFAULT_MAX_MINUTES)
    except Exception:
        default_port = DEFAULT_PORT
        auto_restart = True
        restart_delay = 3
        max_minutes = DEFAULT_MAX_MINUTES

    port_match = re.search(r"(\d+)", text)
    port = port_match.group(1) if port_match else default_port

    _print(f"🚇 Starting tunnel on port [cyan]{port}[/cyan]...")
    _print(f"   ⏱  Auto-expiry warning after [yellow]{max_minutes} minutes[/yellow]\n")

    if shutil.which("cloudflared"):
        _start_cloudflared(port, auto_restart=auto_restart,
                           restart_delay=restart_delay, max_minutes=max_minutes)
    elif shutil.which("ngrok"):
        _start_ngrok(port, max_minutes=max_minutes)
    else:
        _print("⚠️  No tunnel tool found!\n")
        _print("📦 Install options:")
        _print("   Termux : pkg install cloudflared")
        _print("   Debian : wget -q https://github.com/cloudflare/cloudflared/releases/"
               "latest/download/cloudflared-linux-amd64.deb && dpkg -i cloudflared*.deb")
        _print("   ngrok  : snap install ngrok  [or download from ngrok.com]")


def _start_cloudflared(
    port: str,
    *,
    auto_restart: bool = True,
    restart_delay: int = 3,
    max_minutes: int = DEFAULT_MAX_MINUTES,
) -> None:
    cmd = ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"]
    _print(f"▶  cloudflared tunnel --url http://localhost:{port}")
    _print("   Press Ctrl+C to stop.\n")

    tunnel_start = time.time()
    warned = False

    def _expiry_monitor() -> None:
        """Background thread: warn when tunnel nears expiry."""
        nonlocal warned
        while True:
            time.sleep(60)
            elapsed = (time.time() - tunnel_start) / 60
            if not warned and elapsed >= max_minutes:
                warned = True
                _print(
                    f"\n⚠️  [yellow]Tunnel has been running for {int(elapsed)} minutes.[/yellow]\n"
                    "   cloudflared free tunnels expire after ~2 hours.\n"
                    "   Consider restarting: Ctrl+C → then run tunnel again.\n"
                )

    def _read_output(proc: subprocess.Popen) -> None:
        for line in proc.stderr:
            decoded = line.decode("utf-8", errors="ignore").strip()
            if "trycloudflare.com" in decoded or ".cfargotunnel.com" in decoded:
                urls = re.findall(
                    r"https://\S+\.(?:trycloudflare|cfargotunnel)\.com\S*",
                    decoded,
                )
                if urls:
                    _print(f"\n🌐 [bold green]Public URL:[/bold green] {urls[0]}\n")
            elif decoded and not _is_noise(decoded):
                _print(f"   [dim]{decoded}[/dim]")

    monitor = threading.Thread(target=_expiry_monitor, daemon=True)
    monitor.start()

    try:
        while True:
            proc = subprocess.Popen(cmd, stderr=subprocess.PIPE)
            t = threading.Thread(target=_read_output, args=(proc,), daemon=True)
            t.start()
            proc.wait()

            if proc.returncode == 0 or not auto_restart:
                break

            _print(f"\n⚠️  Tunnel crashed. Restarting in {restart_delay}s...")
            time.sleep(restart_delay)

    except KeyboardInterrupt:
        elapsed_min = int((time.time() - tunnel_start) / 60)
        _print(f"\n🛑 Tunnel stopped. (ran for ~{elapsed_min} minute(s))")
        try:
            proc.terminate()
        except Exception:
            pass


def _start_ngrok(port: str, max_minutes: int = DEFAULT_MAX_MINUTES) -> None:
    cmd = ["ngrok", "http", port]
    _print(f"▶  ngrok http {port}")
    _print("   Press Ctrl+C to stop.")
    _print("   Public URL: check http://localhost:4040\n")
    _print(f"   ⏱  ngrok free tunnels expire after ~2 hours (warning at {max_minutes}m)\n")

    start = time.time()
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        elapsed_min = int((time.time() - start) / 60)
        _print(f"\n🛑 ngrok stopped. (ran for ~{elapsed_min} minute(s))")


def _is_noise(line: str) -> bool:
    """Filter out noisy cloudflared log lines."""
    noise = ["INF", "WRN", "metrics", "heart", "ping", "conn"]
    return any(n in line for n in noise)


def _print(msg: str) -> None:
    try:
        from rich.console import Console
        Console().print(msg)
    except ImportError:
        # Strip rich markup for plain output
        plain = re.sub(r"\[/?[^\]]*\]", "", msg)
        print(plain)
