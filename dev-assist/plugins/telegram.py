"""
Plugin: telegram
Trigger: "telegram" or "bot"

Sends dev-assist output to your Telegram bot.
Setup: add bot_token and chat_id to config/settings.json

config/settings.json:
{
  "telegram": {
    "bot_token": "YOUR_BOT_TOKEN",
    "chat_id": "YOUR_CHAT_ID"
  }
}
"""

import json
import os
import urllib.request
import urllib.parse

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "settings.json")

def run(text: str = ""):
    config = _load_config()
    tg = config.get("telegram", {})

    token = tg.get("bot_token", "")
    chat_id = tg.get("chat_id", "")

    if not token or not chat_id:
        print("⚠️  Telegram not configured.")
        print("   Add to config/settings.json:")
        print('   "telegram": {"bot_token": "...", "chat_id": "..."}')
        print("\n   Get bot token: @BotFather on Telegram")
        print("   Get chat_id:   @userinfobot on Telegram")
        return

    # Interactive send
    print("📨 Telegram Message Sender\n")
    message = input("  Message to send: ").strip()
    if not message:
        return

    success = send_message(token, chat_id, message)
    if success:
        print("  ✅ Sent!")
    else:
        print("  ❌ Failed. Check your token and chat_id.")

def send_message(token: str, chat_id: str, text: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }).encode()

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("ok", False)
    except Exception as e:
        print(f"  Telegram error: {e}")
        return False

def notify(message: str):
    """Convenience function for other modules to send Telegram notifications."""
    config = _load_config()
    tg = config.get("telegram", {})
    token = tg.get("bot_token", "")
    chat_id = tg.get("chat_id", "")
    if token and chat_id:
        send_message(token, chat_id, message)

def _load_config() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}
