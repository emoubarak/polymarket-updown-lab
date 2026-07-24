"""Optional Telegram notifications — env-guarded, dependency-free, NEVER raises.

Enable by setting both env vars (e.g. in ~/pmlab/.live_env on the VPS):
    TELEGRAM_BOT_TOKEN=123456:ABC...     (from @BotFather)
    TELEGRAM_CHAT_ID=987654321           (your numeric id, e.g. via @userinfobot)

Without them, notify() is a silent no-op, so the pilot behaves identically whether
or not Telegram is configured. A notification failure must NEVER touch trading:
every call is wrapped and swallows all errors. Stdlib only (urllib) so it doesn't
pull deps into the otherwise-stdlib runner.
"""
from __future__ import annotations

import os
import urllib.parse
import urllib.request


def enabled() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def notify(text: str) -> bool:
    """Send a Telegram message; return True on success. Returns False (silently)
    if not configured or on any error — callers must not depend on the result."""
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return False
    try:
        data = urllib.parse.urlencode({
            "chat_id": chat, "text": text,
            "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=8) as r:
            return 200 <= r.status < 300
    except Exception:
        return False
