"""The briefing jobs, and the local daily schedule at 09:00 Europe/Paris.

`run_briefing` is idempotent per Paris day: it sends at most one briefing per day,
no matter how many times it is triggered. This lets the GitHub workflow fire several
times in the morning window (robust to GitHub cron delays) without ever double-sending.
On failure it retries once after a short delay, then emails a failure alert and
exits non-zero so the run is visibly red in GitHub Actions.

`run_dry_run` builds exactly the same briefing but sends nothing and remembers
nothing — the 2-minute way to see the output with only the two free API keys.
"""

import os
import time
import logging
import logging.handlers
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import history
from agent import build_briefing, render_text
from email_sender import send_email, send_failure_alert

TIMEZONE = os.getenv("TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai"
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "briefing.log")
PREVIEW_FILE = os.path.join("data", "preview.html")
BRIEFING_FILE = os.path.join("data", "briefing.json")
RETRY_DELAY_SECONDS = 120


def _delivery_configured() -> bool:
    return all((os.getenv(k) or "").strip() for k in config.DELIVERY_ENV)

logger = logging.getLogger("briefing")


def setup_logging() -> None:
    """Configure logging to both stdout and logs/briefing.log."""
    os.makedirs(LOG_DIR, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    root = logging.getLogger("briefing")
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)


def _today() -> str:
    return datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")


def _build_and_send() -> None:
    subject, html_body, payload = build_briefing()
    send_email(subject, html_body)
    history.record_sent(
        payload["articles"], payload.get("word", {}), payload.get("quote", {}), _today()
    )


def run_briefing(force: bool = False) -> None:
    """Send today's briefing unless it was already sent (idempotent per Paris day).

    `force` bypasses the once-a-day guard (used for manual test runs).
    """
    # Checked before any API call so a missing secret is reported as a readable
    # message instead of surfacing as a failure halfway through the run.
    config.check_env(delivery=True)

    today = _today()
    if not force and history.last_sent_date() == today:
        logger.info("Briefing already sent today (%s) — skipping.", today)
        return

    logger.info("=== Starting daily briefing job (%s) ===", today)
    try:
        _build_and_send()
        logger.info("=== Briefing sent successfully ===")
        return
    except Exception as exc:  # noqa: BLE001
        # exception() keeps the traceback in the log, so a failed GitHub Actions run
        # can be diagnosed from the Actions output alone.
        logger.exception("Briefing failed: %s. Retrying once in %ds.", exc, RETRY_DELAY_SECONDS)

    time.sleep(RETRY_DELAY_SECONDS)
    try:
        _build_and_send()
        logger.info("=== Briefing sent successfully on retry ===")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Retry also failed: %s. Sending failure alert.", exc)
        send_failure_alert(str(exc))
        # Re-raise so the process exits non-zero and the GitHub Actions run is marked
        # failed. Swallowing this made twelve days of broken briefings show a green
        # check, which is how a decommissioned model went unnoticed for that long.
        raise


def run_dry_run() -> str:
    """Build today's briefing and preview it — no email, no history written.

    This is the low-friction way to evaluate the project: it needs only GROQ_API_KEY
    and TAVILY_API_KEY, touches neither Gmail nor data/history.json, and leaves the
    rendered email at data/preview.html to open in a browser.
    """
    config.check_env(delivery=False)
    cfg = config.load()
    logger.info(
        "=== Dry run: %r, %d article(s), topics=%d, focus=%s ===",
        cfg.title, cfg.num_articles, len(cfg.topics),
        cfg.focus["label"] if cfg.focus else "none",
    )

    subject, html_body, payload = build_briefing()

    _write_preview(html_body)

    try:
        print("\n" + render_text(subject, payload))
    except UnicodeEncodeError:
        # Windows consoles default to GBK, which chokes on characters like U+2011.
        print("\n" + render_text(subject, payload).encode("ascii", "replace").decode("ascii"))
    print(f"Rendered email written to {PREVIEW_FILE} — open it in a browser.")
    print("Dry run: nothing was emailed, and no history was recorded.\n")
    logger.info("=== Dry run complete (nothing sent, nothing recorded) ===")
    return html_body


def _write_preview(html_body: str) -> None:
    os.makedirs(os.path.dirname(PREVIEW_FILE), exist_ok=True)
    with open(PREVIEW_FILE, "w", encoding="utf-8") as fh:
        fh.write(html_body)


def run_publish(force: bool = False) -> None:
    """Build the briefing, persist its memory, archive it for n8n — email optional.

    This is the headless mode for an n8n pipeline: it writes two artifacts into data/
    (preview.html for humans, briefing.json for machines), records the sent articles so
    tomorrow's run never repeats them, and only emails if the Gmail secrets exist. The
    GitHub Action commits both files back to the repo, so n8n can just HTTP-GET the raw
    briefing.json off main.
    """
    config.check_env(delivery=False)
    today = _today()
    if not force and history.last_sent_date() == today:
        logger.info("Briefing already published today (%s) — skipping.", today)
        return

    import json

    logger.info("=== Publishing daily briefing (%s) ===", today)
    subject, html_body, payload = build_briefing()

    _write_preview(html_body)

    doc = {
        "date": today,
        "subject": subject,
        "generated_at": datetime.now(ZoneInfo(TIMEZONE)).isoformat(),
        "articles": payload["articles"],
        "word": payload.get("word", {}),
        "quote": payload.get("quote", {}),
    }
    with open(BRIEFING_FILE, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)

    # Record for de-duplication before deciding on email, so a missing SMTP config
    # can never cause the same stories to be re-sent the next morning.
    history.record_sent(payload["articles"], payload.get("word", {}), payload.get("quote", {}), today)

    if _delivery_configured():
        try:
            send_email(subject, html_body)
            logger.info("Email sent to %s", os.getenv("RECIPIENT_EMAIL"))
        except Exception as exc:  # noqa: BLE001 - archive already landed; don't fail the run
            logger.error("Archived OK but email failed: %s", exc)
    else:
        logger.info("No Gmail secrets set — archived only (n8n reads data/briefing.json).")

    # Telegram push: same content, phone-readable. A failure here never fails the run.
    import telegram_sender

    if telegram_sender.is_configured():
        try:
            telegram_sender.send_message(subject, payload)
        except Exception as exc:  # noqa: BLE001
            logger.error("Telegram push failed: %s", exc)
    else:
        logger.info("Telegram not configured (TELEGRAM_BOT_TOKEN/CHAT_ID) — skipped.")

    print(f"Published: {len(doc['articles'])} article(s) -> {PREVIEW_FILE}, {BRIEFING_FILE}")


def start_scheduler() -> None:
    """Start the blocking scheduler that fires every day at 09:00 local time."""
    scheduler = BlockingScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        run_briefing,
        CronTrigger(hour=9, minute=0, timezone=TIMEZONE),
        id="daily_briefing",
        name=f"Daily Briefing at 09:00 {TIMEZONE}",
        misfire_grace_time=3600,
    )
    logger.info("Scheduler started. Next briefing every day at 09:00 %s.", TIMEZONE)
    scheduler.start()
