"""
dev-assist — Chainlit Web Interface

Auth UX (Gitea-style):
  - Action buttons: [Login] [Register] [Forgot Password]
  - Field-by-field prompts using cl.AskUserMessage (no password in chat history)
  - OTP verification flow with resend option
  - Email or username login

Features:
  - Per-user persistent chat history (SQLite)
  - Per-user model settings (never overrides global config)
  - Conversation memory across sessions
  - File upload → AI reads and answers
  - Rate limiting on auth (5 attempts / 5 min)
  - bcrypt-only password hashing
  - SMTP OTP for email verify + password reset
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import secrets
import smtplib
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

# -- Path bootstrap -----------------------------------------------------------
def _bootstrap_path() -> None:
    if getattr(sys, "frozen", False):
        _mei = getattr(sys, "_MEIPASS", None)
        if _mei and _mei not in sys.path:
            sys.path.insert(0, _mei)
    else:
        _root = os.path.dirname(os.path.abspath(__file__))
        if _root not in sys.path:
            sys.path.insert(0, _root)

_bootstrap_path()
# -----------------------------------------------------------------------------

import chainlit as cl
from chainlit.input_widget import Select

MAX_CONTEXT_MESSAGES = 20

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _get_db_path() -> str:
    data_dir = os.environ.get("DEV_ASSIST_DATA_DIR") or str(
        Path.home() / ".config" / "dev-assist"
    )
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "web_users.db")


@contextmanager
def _db():
    conn = sqlite3.connect(_get_db_path(), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _init_db() -> None:
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                username       TEXT    UNIQUE NOT NULL,
                email          TEXT    UNIQUE,
                pw_hash        TEXT    NOT NULL,
                email_verified INTEGER NOT NULL DEFAULT 0,
                user_config    TEXT    NOT NULL DEFAULT '{}',
                created        INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role       TEXT    NOT NULL,
                content    TEXT    NOT NULL,
                created    INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS otp_tokens (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token      TEXT    NOT NULL,
                purpose    TEXT    NOT NULL,
                expires_at INTEGER NOT NULL,
                used       INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id, created);
            CREATE INDEX IF NOT EXISTS idx_otp_user     ON otp_tokens(user_id, purpose);
        """)
        # ── Schema migrations (safe to run on every startup) ─────────────────
        existing = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "email" not in existing:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
        if "email_verified" not in existing:
            conn.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
        if "user_config" not in existing:
            conn.execute("ALTER TABLE users ADD COLUMN user_config TEXT NOT NULL DEFAULT '{}'")


_init_db()

# ---------------------------------------------------------------------------
# Rate limiting (in-memory)
# ---------------------------------------------------------------------------

_rate_data: dict[str, list[float]] = {}

def _check_rate_limit(key: str, max_attempts: int = 5, window: int = 300) -> bool:
    now = time.time()
    attempts = [t for t in _rate_data.get(key, []) if now - t < window]
    if len(attempts) >= max_attempts:
        _rate_data[key] = attempts
        return False
    attempts.append(now)
    _rate_data[key] = attempts
    return True

# ---------------------------------------------------------------------------
# Password hashing (bcrypt mandatory)
# ---------------------------------------------------------------------------

def _hash_pw(password: str) -> str:
    try:
        import bcrypt
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        raise RuntimeError("bcrypt is required: pip install bcrypt")


def _verify_pw(password: str, pw_hash: str) -> bool:
    try:
        import bcrypt
        return bcrypt.checkpw(password.encode(), pw_hash.encode())
    except ImportError:
        raise RuntimeError("bcrypt is required: pip install bcrypt")

# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def _is_valid_email(email: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def _register_user(username: str, email: str, password: str) -> tuple[bool, str, Optional[int]]:
    username = username.strip().lower()
    email    = email.strip().lower()
    if len(username) < 3:
        return False, "Username must be at least 3 characters.", None
    if not re.match(r"^[a-z0-9_.\-]+$", username):
        return False, "Username may only contain letters, numbers, _ . -", None
    if not _is_valid_email(email):
        return False, "Please enter a valid email address.", None
    if len(password) < 8:
        return False, "Password must be at least 8 characters.", None
    try:
        pw_hash = _hash_pw(password)
    except RuntimeError as e:
        return False, str(e), None
    try:
        with _db() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, email, pw_hash, email_verified, created) "
                "VALUES (?, ?, ?, 0, ?)",
                (username, email, pw_hash, int(time.time())),
            )
            return True, "Account created!", cur.lastrowid
    except sqlite3.IntegrityError as e:
        msg = str(e).lower()
        if "username" in msg:
            return False, "Username is already taken.", None
        if "email" in msg:
            return False, "An account with this email already exists.", None
        return False, "Registration failed.", None


def _login_user(identifier: str, password: str) -> Optional[dict]:
    """Login by email or username."""
    identifier = identifier.strip().lower()
    with _db() as conn:
        row = conn.execute(
            "SELECT id, username, email, pw_hash, email_verified FROM users "
            "WHERE email = ? OR username = ? LIMIT 1",
            (identifier, identifier),
        ).fetchone()
    if row:
        try:
            if _verify_pw(password, row["pw_hash"]):
                return dict(row)
        except RuntimeError:
            pass
    return None


def _get_user_by_email(email: str) -> Optional[dict]:
    with _db() as conn:
        row = conn.execute(
            "SELECT id, username, email, email_verified FROM users WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
    return dict(row) if row else None


def _update_password(user_id: int, new_password: str) -> bool:
    try:
        pw_hash = _hash_pw(new_password)
    except RuntimeError:
        return False
    with _db() as conn:
        conn.execute("UPDATE users SET pw_hash = ? WHERE id = ?", (pw_hash, user_id))
    return True


def _set_email_verified(user_id: int) -> None:
    with _db() as conn:
        conn.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,))

# ---------------------------------------------------------------------------
# OTP
# ---------------------------------------------------------------------------

def _generate_otp(user_id: int, purpose: str) -> str:
    token = str(secrets.randbelow(900000) + 100000)
    expires_at = int(time.time()) + 600  # 10 minutes
    with _db() as conn:
        conn.execute(
            "UPDATE otp_tokens SET used = 1 WHERE user_id = ? AND purpose = ? AND used = 0",
            (user_id, purpose),
        )
        conn.execute(
            "INSERT INTO otp_tokens (user_id, token, purpose, expires_at, used) "
            "VALUES (?, ?, ?, ?, 0)",
            (user_id, token, purpose, expires_at),
        )
    return token


def _verify_otp(user_id: int, token: str, purpose: str) -> bool:
    now = int(time.time())
    with _db() as conn:
        row = conn.execute(
            "SELECT id FROM otp_tokens "
            "WHERE user_id = ? AND token = ? AND purpose = ? AND expires_at > ? AND used = 0",
            (user_id, token, purpose, now),
        ).fetchone()
        if row:
            conn.execute("UPDATE otp_tokens SET used = 1 WHERE id = ?", (row["id"],))
            return True
    return False

# ---------------------------------------------------------------------------
# Email / SMTP
# ---------------------------------------------------------------------------

def _smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER"))


def _send_email(to: str, subject: str, text: str, html: str = "") -> bool:
    host  = os.environ.get("SMTP_HOST", "")
    port  = int(os.environ.get("SMTP_PORT", "587"))
    user  = os.environ.get("SMTP_USER", "")
    passwd = os.environ.get("SMTP_PASS", "")
    from_ = os.environ.get("SMTP_FROM", user)
    if not host or not user:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = from_
        msg["To"]      = to
        msg.attach(MIMEText(text, "plain", "utf-8"))
        if html:
            msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP(host, port, timeout=10) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            if passwd:
                s.login(user, passwd)
            s.sendmail(from_, [to], msg.as_string())
        return True
    except Exception as exc:
        print(f"[dev-assist] SMTP error: {exc}", flush=True)
        return False


def _otp_email(to: str, otp: str, purpose: str) -> bool:
    if purpose == "verify":
        subject = "dev-assist — Email Verification"
        text    = f"Your verification code: {otp}\n\nValid for 10 minutes."
    else:
        subject = "dev-assist — Password Reset"
        text    = f"Your password reset code: {otp}\n\nValid for 10 minutes.\nIgnore this email if you did not request it."
    html = f"""<!DOCTYPE html>
<html><body style="font-family:monospace;background:#f4f4f4;padding:40px;margin:0;">
<div style="max-width:420px;margin:0 auto;background:#fff;border:1px solid #ddd;
            border-radius:8px;padding:32px;">
  <h2 style="margin:0 0 4px;font-size:20px;">dev-assist</h2>
  <p style="color:#666;margin:0 0 24px;font-size:14px;">
    {"Email Verification" if purpose == "verify" else "Password Reset"}
  </p>
  <div style="font-size:38px;font-weight:700;letter-spacing:12px;padding:20px 0;
              text-align:center;color:#111;border-top:1px solid #eee;
              border-bottom:1px solid #eee;">{otp}</div>
  <p style="color:#999;font-size:13px;margin-top:20px;">
    Valid for 10 minutes. Do not share this code.
  </p>
</div></body></html>"""
    return _send_email(to, subject, text, html)

# ---------------------------------------------------------------------------
# Per-user config (stored in DB — never touches global settings.json)
# ---------------------------------------------------------------------------

def _get_user_config(user_id: int) -> dict:
    with _db() as conn:
        row = conn.execute(
            "SELECT user_config FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    try:
        return json.loads(row["user_config"]) if row else {}
    except Exception:
        return {}


def _save_user_config(user_id: int, cfg: dict) -> None:
    with _db() as conn:
        conn.execute(
            "UPDATE users SET user_config = ? WHERE id = ?",
            (json.dumps(cfg), user_id),
        )


def _load_global_config() -> dict:
    try:
        from core.config import load_config
        cfg = load_config()
        return cfg.model_dump() if hasattr(cfg, "model_dump") else (cfg if isinstance(cfg, dict) else {})
    except Exception:
        p = os.path.join(
            os.environ.get("DEV_ASSIST_CONFIG_DIR", ""),
            "settings.json",
        ) or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config", "settings.json"
        )
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            return {}


def _get_ollama_models() -> list[str]:
    try:
        import ollama
        names = [m.model for m in ollama.list().models]
        return names or ["qwen2.5-coder:7b", "llama3.2:3b"]
    except Exception:
        return ["qwen2.5-coder:7b", "llama3.2:3b", "codellama:7b", "mistral:7b"]

# ---------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------

def _save_msg(user_id: int, role: str, content: str) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO messages (user_id, role, content, created) VALUES (?, ?, ?, ?)",
            (user_id, role, content, int(time.time())),
        )


def _get_history(user_id: int, limit: int = 100) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages "
            "WHERE user_id = ? ORDER BY created DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _clear_history(user_id: int) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))

# ---------------------------------------------------------------------------
# AI streaming
# ---------------------------------------------------------------------------

async def _stream_response(user_id: int, user_text: str, file_context: str = ""):
    from core.ai import _load_config as _ai_cfg, _get_engine, _get_ollama_model

    cfg    = _ai_cfg()
    engine = _get_engine(cfg)

    user_cfg = _get_user_config(user_id)
    if user_cfg.get("ai_engine"):
        engine = user_cfg["ai_engine"]

    history = _get_history(user_id, limit=MAX_CONTEXT_MESSAGES)
    msgs    = [{"role": m["role"], "content": m["content"]} for m in history]
    full_q  = f"{user_text}\n\n--- Attached file ---\n{file_context}" if file_context else user_text
    msgs.append({"role": "user", "content": full_q})

    if engine == "ollama":
        try:
            import ollama
            model = user_cfg.get("ollama_model") or _get_ollama_model(cfg)
            token_queue: queue.Queue = queue.Queue()

            def _producer() -> None:
                try:
                    for chunk in ollama.chat(model=model, messages=msgs, stream=True):
                        token = (
                            chunk.get("message", {}).get("content", "")
                            if isinstance(chunk, dict)
                            else getattr(getattr(chunk, "message", None), "content", "")
                        )
                        if token:
                            token_queue.put(token)
                except Exception as exc:
                    token_queue.put(f"\n\n⚠️ Ollama error: {exc}")
                finally:
                    token_queue.put(None)  # sentinel

            t = threading.Thread(target=_producer, daemon=True)
            t.start()

            loop = asyncio.get_running_loop()
            while True:
                token = await loop.run_in_executor(None, token_queue.get)
                if token is None:
                    break
                yield token
        except Exception as exc:
            yield f"\n\n⚠️ Ollama error: {exc}"
    else:
        try:
            from core.ai import _get_api_key, _get_api_url, _get_api_model
            import urllib.request, json as _j

            api_key = _get_api_key(cfg)
            api_url = user_cfg.get("api_url") or _get_api_url(cfg)
            model   = user_cfg.get("api_model") or _get_api_model(cfg)
            payload = _j.dumps({"model": model, "messages": msgs, "stream": False}).encode()
            req = urllib.request.Request(
                api_url, data=payload,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {api_key}"},
            )

            def _call():
                with urllib.request.urlopen(req, timeout=60) as r:
                    return _j.loads(r.read())

            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, _call)
            yield data["choices"][0]["message"]["content"]
        except Exception as exc:
            yield f"\n\n⚠️ API error: {exc}"

# ---------------------------------------------------------------------------
# Auth UX helpers  (Gitea-style step-by-step prompts)
# ---------------------------------------------------------------------------

async def _ask(prompt: str, timeout: int = 120) -> Optional[str]:
    """Ask the user for a single field. Returns the text or None on timeout."""
    res = await cl.AskUserMessage(content=prompt, timeout=timeout).send()
    if res:
        return res["output"].strip()
    return None


async def _show_auth_menu() -> None:
    """Show login / register / forgot-password action buttons."""
    actions = [
        cl.Action(name="auth_login",    payload={"action": "login"},    label="🔑  Sign in"),
        cl.Action(name="auth_register", payload={"action": "register"}, label="✨  Create account"),
        cl.Action(name="auth_forgot",   payload={"action": "forgot"},   label="🔒  Forgot password"),
    ]
    await cl.Message(
        content="## dev-assist\n\nPersonal AI DevOps Assistant\n\nChoose an option to continue:",
        actions=actions,
        author="dev-assist",
    ).send()


# ---------------------------------------------------------------------------
# Auth flows
# ---------------------------------------------------------------------------

async def _flow_login() -> None:
    """Step-by-step login: identifier → password."""
    sid = cl.context.session.id

    identifier = await _ask("**Sign in**\n\nEnter your **email or username**:")
    if not identifier:
        await _show_auth_menu(); return

    password = await _ask("Enter your **password**:")
    if not password:
        await _show_auth_menu(); return

    if not _check_rate_limit(f"login:{sid}"):
        await cl.Message(content="⚠️ Too many attempts. Please wait 5 minutes.").send()
        await _show_auth_menu(); return

    user = _login_user(identifier, password)
    if not user:
        await cl.Message(content="❌ Incorrect email/username or password.").send()
        await _show_auth_menu(); return

    if not user["email_verified"]:
        await _flow_verify_email(user["id"], user["email"], newly_registered=False)
        return

    await _post_login(user["id"], user["username"])


async def _flow_register() -> None:
    """Step-by-step registration: username → email → password → OTP verify."""
    sid = cl.context.session.id

    if not _check_rate_limit(f"reg:{sid}", max_attempts=5):
        await cl.Message(content="⚠️ Too many attempts. Please wait 5 minutes.").send()
        await _show_auth_menu(); return

    # Username
    username = await _ask("**Create account — Step 1 of 3**\n\nChoose a **username** (letters, numbers, _ . -):")
    if not username:
        await _show_auth_menu(); return

    # Email
    email = await _ask("**Create account — Step 2 of 3**\n\nEnter your **email address**:")
    if not email:
        await _show_auth_menu(); return

    # Password
    password = await _ask(
        "**Create account — Step 3 of 3**\n\n"
        "Choose a **password** (minimum 8 characters):\n\n"
        "*(Your password is never stored in chat history.)*"
    )
    if not password:
        await _show_auth_menu(); return

    ok, msg, uid = _register_user(username, email, password)
    if not ok:
        await cl.Message(content=f"❌ {msg}").send()
        await _show_auth_menu(); return

    await _flow_verify_email(uid, email.strip().lower(), newly_registered=True)


async def _flow_verify_email(user_id: int, email: str, newly_registered: bool) -> None:
    """Send OTP and ask user to enter it."""
    otp = _generate_otp(user_id, "verify")

    if _smtp_configured():
        sent = _otp_email(email, otp, "verify")
        if sent:
            await cl.Message(
                content=f"📧 A 6-digit verification code has been sent to **{email}**."
            ).send()
        else:
            await cl.Message(
                content=f"⚠️ Could not send email (check SMTP config).\n\n**Debug code: `{otp}`**"
            ).send()
    else:
        # No SMTP — auto-verify in development
        _set_email_verified(user_id)
        with _db() as conn:
            row = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        await cl.Message(content="ℹ️ *SMTP not configured — email verification skipped (dev mode).*").send()
        await _post_login(user_id, row["username"] if row else "user")
        return

    for attempt in range(3):
        code = await _ask(
            f"Enter the **6-digit code** sent to {email}:\n\n"
            f"*(Didn't receive it? Type `resend` to get a new one.)*"
        )
        if not code:
            await _show_auth_menu(); return

        if code.lower() == "resend":
            otp = _generate_otp(user_id, "verify")
            sent = _otp_email(email, otp, "verify")
            await cl.Message(
                content=(f"📧 New code sent to **{email}**." if sent
                         else f"⚠️ Email failed. Debug code: `{otp}`")
            ).send()
            continue

        if _verify_otp(user_id, code, "verify"):
            _set_email_verified(user_id)
            with _db() as conn:
                row = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
            await _post_login(user_id, row["username"] if row else "user")
            return
        else:
            remaining = 2 - attempt
            if remaining > 0:
                await cl.Message(content=f"❌ Incorrect code. {remaining} attempt(s) remaining.").send()

    await cl.Message(content="❌ Too many incorrect attempts.").send()
    await _show_auth_menu()


async def _flow_forgot_password() -> None:
    """Forgot-password flow: email → OTP → new password."""
    email = await _ask("**Forgot password**\n\nEnter your **email address**:")
    if not email:
        await _show_auth_menu(); return

    if not _check_rate_limit(f"forgot:{cl.context.session.id}", max_attempts=3):
        await cl.Message(content="⚠️ Too many attempts. Please wait 5 minutes.").send()
        await _show_auth_menu(); return

    # Same message regardless — prevents email enumeration
    await cl.Message(
        content=f"📧 If **{email.strip().lower()}** has an account, a reset code has been sent."
    ).send()

    user = _get_user_by_email(email)
    if not user:
        await _show_auth_menu(); return

    otp = _generate_otp(user["id"], "reset")
    if _smtp_configured():
        _otp_email(email.strip().lower(), otp, "reset")
    else:
        await cl.Message(content=f"⚠️ SMTP not configured. Debug code: `{otp}`").send()

    for attempt in range(3):
        code = await _ask("Enter the **6-digit reset code** from your email:")
        if not code:
            await _show_auth_menu(); return

        if not _verify_otp(user["id"], code, "reset"):
            remaining = 2 - attempt
            if remaining > 0:
                await cl.Message(content=f"❌ Incorrect or expired code. {remaining} attempt(s) remaining.").send()
            else:
                await cl.Message(content="❌ Too many incorrect attempts.").send()
                await _show_auth_menu()
            continue

        # OTP valid — ask for new password
        new_pass = await _ask(
            "✅ Code verified!\n\nEnter your **new password** (minimum 8 characters):"
        )
        if not new_pass or len(new_pass) < 8:
            await cl.Message(content="❌ Password must be at least 8 characters.").send()
            await _show_auth_menu(); return

        if _update_password(user["id"], new_pass):
            await cl.Message(content="✅ Password updated successfully! Please sign in.").send()
        else:
            await cl.Message(content="❌ Failed to update password.").send()
        await _show_auth_menu()
        return

# ---------------------------------------------------------------------------
# Chainlit lifecycle
# ---------------------------------------------------------------------------

@cl.on_chat_start
async def on_start():
    cl.user_session.set("user_id",  None)
    cl.user_session.set("username", None)
    await _show_auth_menu()


@cl.action_callback("auth_login")
async def on_auth_login(action: cl.Action):
    await _flow_login()


@cl.action_callback("auth_register")
async def on_auth_register(action: cl.Action):
    await _flow_register()


@cl.action_callback("auth_forgot")
async def on_auth_forgot(action: cl.Action):
    await _flow_forgot_password()


@cl.on_settings_update
async def on_settings_update(settings: dict):
    user_id = cl.user_session.get("user_id")
    if not user_id:
        return
    # Stored per-user in DB — never writes to global settings.json
    user_cfg = _get_user_config(user_id)
    user_cfg["ai_engine"]    = settings.get("engine", "ollama")
    user_cfg["ollama_model"] = settings.get("ollama_model", "qwen2.5-coder:7b")
    _save_user_config(user_id, user_cfg)
    cl.user_session.set("settings", settings)
    engine = user_cfg["ai_engine"]
    model  = user_cfg["ollama_model"] if engine == "ollama" else "api"
    await cl.Message(content=f"✅ Model updated → `{engine}/{model}`").send()


@cl.on_message
async def on_message(message: cl.Message):
    user_id = cl.user_session.get("user_id")
    raw     = message.content.strip()
    cmd     = raw.lower()

    if not user_id:
        await _show_auth_menu()
        return

    # ── Built-in commands ──────────────────────────────────────────────────
    if cmd in ("clear", "/clear", "clear history"):
        _clear_history(user_id)
        await cl.Message(content="🗑️ Chat history cleared.").send()
        return

    if cmd in ("logout", "/logout"):
        cl.user_session.set("user_id",  None)
        cl.user_session.set("username", None)
        await _show_auth_menu()
        return

    if cmd in ("history", "/history"):
        hist = _get_history(user_id, limit=20)
        if not hist:
            await cl.Message(content="No history yet.").send()
            return
        lines = []
        for m in hist:
            role  = "**You**" if m["role"] == "user" else "**AI**"
            short = m["content"][:120].replace("\n", " ")
            lines.append(f"{role}: {short}{'…' if len(m['content']) > 120 else ''}")
        await cl.Message(content="### 📜 Recent history\n\n" + "\n\n".join(lines)).send()
        return

    # ── File upload ────────────────────────────────────────────────────────
    file_context = ""
    if message.elements:
        parts = []
        for el in message.elements:
            try:
                if hasattr(el, "path") and el.path:
                    content = Path(el.path).read_text(errors="replace")
                elif hasattr(el, "content") and el.content:
                    content = (el.content.decode("utf-8", errors="replace")
                               if isinstance(el.content, bytes) else str(el.content))
                else:
                    continue
                parts.append(f"[File: {el.name}]\n{content[:8000].replace(chr(0), '')}")
            except Exception as e:
                parts.append(f"[Could not read {getattr(el, 'name', '?')}: {e}]")
        file_context = "\n\n".join(parts)

    saved = raw
    if file_context:
        names = ", ".join(el.name for el in message.elements if hasattr(el, "name"))
        saved += f"\n[attached: {names}]"
    _save_msg(user_id, "user", saved)

    # ── Stream AI response ─────────────────────────────────────────────────
    reply_msg  = cl.Message(content="")
    await reply_msg.send()
    full_reply = []
    async for token in _stream_response(user_id, raw, file_context):
        await reply_msg.stream_token(token)
        full_reply.append(token)
    await reply_msg.update()
    _save_msg(user_id, "assistant", "".join(full_reply))

# ---------------------------------------------------------------------------
# Post-login
# ---------------------------------------------------------------------------

async def _post_login(user_id: int, username: str) -> None:
    cl.user_session.set("user_id",  user_id)
    cl.user_session.set("username", username)

    global_cfg   = _load_global_config()
    user_cfg     = _get_user_config(user_id)
    engine       = user_cfg.get("ai_engine") or global_cfg.get("ai_engine", "ollama")
    ollama_model = (user_cfg.get("ollama_model")
                    or global_cfg.get("ollama_model", "qwen2.5-coder:7b"))
    api_cfg      = global_cfg.get("api_engine", {})
    api_model    = (api_cfg.get("api_model", "llama3-70b-8192")
                    if isinstance(api_cfg, dict) else "llama3-70b-8192")
    ollama_models = _get_ollama_models()

    settings = await cl.ChatSettings([
        Select(id="engine", label="AI Engine",
               values=["ollama", "api"], initial_value=engine),
        Select(id="ollama_model", label="Ollama Model",
               values=ollama_models,
               initial_value=ollama_model if ollama_model in ollama_models else ollama_models[0]),
    ]).send()
    cl.user_session.set("settings", settings)

    active_model = ollama_model if engine == "ollama" else api_model
    hist_count   = len(_get_history(user_id, limit=1000))

    await cl.Message(content=f"""✅ **Welcome back, {username}!**

🤖 `{engine}/{active_model}` · 💬 {hist_count} saved message(s)

---
📎 Attach files · `clear` clear history · `logout` sign out · `history` view recent chat
""").send()
