"""Telegram delivery: push the briefing to one chat via the Bot API.

Configured by two env vars:
- TELEGRAM_BOT_TOKEN  the token from @BotFather
- TELEGRAM_CHAT_ID    your numeric chat id (the bot must have received one message
                      from you first; https://t.me/getmyid_bot can show it too)

Articles are sent as MarkdownV2-safe plain text with links, not the HTML email,
so the message stays readable on a phone. Best-effort by design: the caller
decides whether a Telegram failure should fail the whole run.
"""

import os
import json
import logging
import urllib.request

logger = logging.getLogger("briefing.telegram")

API = "https://api.telegram.org/bot{token}/{method}"


def _configured() -> bool:
    return bool(
        (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        and (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    )


_MD2_SPECIALS = r'_*[]()~`>#+-=|{}.!\/'


def _escape(text: str) -> str:
    """Escape every Telegram MarkdownV2 special char so titles never break layout."""
    return "".join("\\" + c if c in _MD2_SPECIALS else c for c in text or "")


def format_message(subject: str, payload: dict) -> str:
    lines = [f"*{_escape(subject)}*", ""]
    for i, a in enumerate(payload.get("articles", []), 1):
        title = _escape(a.get("title", "").strip()) or f"Story {i}"
        summary = _escape(a.get("summary", "").strip())
        url = a.get("url", "")
        lines.append(f"{i}\\. *{title}*")
        if summary:
            lines.append(summary)
        if url:
            lines.append(f"🔗 {url}")
        lines.append("")
    word = payload.get("word", {})
    if word.get("word"):
        lines.append(f"📖 _Word of the day_: {_escape(word['word'])}")
    quote = payload.get("quote", {})
    if quote.get("text"):
        author = _escape(quote.get("author", ""))
        lines.append(f"💬 {_escape(quote['text'])} — {author}")
    lines.append("")
    lines.append("🤖 daily\\-briefing · AI 日报")
    return "\n".join(lines)


def send_message(subject: str, payload: dict) -> None:
    """Send the briefing to TELEGRAM_CHAT_ID. Raises on failure."""
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    text = format_message(subject, payload)
    body = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        API.format(token=token, method="sendMessage"),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")
    logger.info("Telegram message sent to chat %s", chat_id)


def is_configured() -> bool:
    return _configured()
