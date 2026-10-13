"""Telegram delivery: push the briefing to one chat via the Bot API.

Configured by two env vars:
- TELEGRAM_BOT_TOKEN  the token from @BotFather
- TELEGRAM_CHAT_ID    your numeric chat id (the bot must have received one message
                      from you first; https://t.me/getmyid_bot can show it too)

Articles are sent as an HTML-formatted message with clickable headline links,
so the briefing stays readable on a phone. Best-effort by design: the caller
decides whether a Telegram failure should fail the whole run.
"""

import os
import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger("briefing.telegram")

API = "https://api.telegram.org/bot{token}/{method}"


def _configured() -> bool:
    return bool(
        (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        and (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    )


def _h(text: str) -> str:
    """Escape for Telegram HTML parse mode (only &, <, > are special)."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_message(subject: str, payload: dict) -> str:
    lines = [f"<b>{_h(subject)}</b>", ""]
    for i, a in enumerate(payload.get("articles", []), 1):
        title = _h(a.get("title", "").strip()) or f"Story {i}"
        summary = _h(a.get("summary", "").strip())
        url = (a.get("url", "") or "").strip()
        heading = f'<a href="{_h(url)}">{title}</a>' if url else title
        lines.append(f"{i}. <b>{heading}</b>")
        if summary:
            lines.append(summary)
        lines.append("")
    word = payload.get("word", {})
    if word.get("word"):
        meaning = _h(word.get("meaning", "").strip())
        lines.append(f"📖 <b>每日一词</b>：{_h(word['word'])}" + (f" — {meaning}" if meaning else ""))
    quote = payload.get("quote", {})
    if quote.get("text"):
        lines.append(f"💬 {_h(quote['text'])} — {_h(quote.get('author', ''))}")
    lines.append("")
    lines.append("🤖 daily-briefing · AI 日报")
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
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        API.format(token=token, method="sendMessage"),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Telegram HTTP {err.code}: {detail}") from err
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")
    logger.info("Telegram message sent to chat %s", chat_id)


def is_configured() -> bool:
    return _configured()
