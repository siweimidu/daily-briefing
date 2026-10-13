"""Email delivery via SMTP (Gmail by default; any SSL provider via SMTP_HOST/SMTP_PORT)."""

import os
import ssl
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger("briefing.email")

DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 465


def _smtp_host() -> str:
    return (os.getenv("SMTP_HOST") or "").strip() or DEFAULT_SMTP_HOST


def _smtp_port() -> int:
    try:
        return int((os.getenv("SMTP_PORT") or "").strip() or DEFAULT_SMTP_PORT)
    except ValueError:
        return DEFAULT_SMTP_PORT


def _send(subject: str, html_body: str) -> None:
    """Low-level SMTP SSL send to RECIPIENT_EMAIL. Raises on failure."""
    sender_address = os.getenv("GMAIL_ADDRESS")
    sender_password = os.getenv("GMAIL_APP_PASSWORD")
    recipient = os.getenv("RECIPIENT_EMAIL")

    if not all([sender_address, sender_password, recipient]):
        raise RuntimeError(
            "Missing email config: GMAIL_ADDRESS, GMAIL_APP_PASSWORD or RECIPIENT_EMAIL"
        )

    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = sender_address
    message["To"] = recipient
    message.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    host, port = _smtp_host(), _smtp_port()
    with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
        server.login(sender_address, sender_password)
        server.sendmail(sender_address, recipient, message.as_string())
    logger.info("Email sent to %s via %s:%d", recipient, host, port)


def send_email(subject: str, html_body: str) -> None:
    """Send the briefing HTML email. Raises on failure so the caller can retry."""
    try:
        _send(subject, html_body)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send email: %s", exc)
        raise


def send_failure_alert(error: str) -> None:
    """Notify the recipient that today's briefing could not be produced/sent.

    Best-effort: never raises, so a failed alert can't crash the job further.
    """
    from datetime import datetime
    import config

    today = datetime.now().strftime("%B %d, %Y")
    subject = f"[{config.load().title}] Failed — {today}"
    html = f"""<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;">
      <p>Today's briefing could not be sent.</p>
      <p style="color:#b00020;"><strong>Reason:</strong> {error}</p>
      <p style="color:#666;font-size:12px;">It will be retried automatically tomorrow at 9am.</p>
    </div>"""
    try:
        _send(subject, html)
        logger.info("Failure alert sent")
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not send failure alert: %s", exc)
