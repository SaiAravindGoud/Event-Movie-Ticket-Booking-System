
import asyncio
import os
import smtplib
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()


def _send_email_sync(
    recipient: str,
    subject: str,
    body: str,
    attachment: bytes | None = None,
    attachment_filename: str = "ticket.pdf",
) -> bool:
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM_EMAIL")

    use_tls = (
        os.getenv("SMTP_USE_TLS", "true").strip().lower()
        in {"true", "1", "yes"}
    )

    if not smtp_host or not sender:
        print(
            "Email not sent: SMTP_HOST or SMTP_FROM_EMAIL is missing."
        )
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)

    if attachment is not None:
        message.add_attachment(
            attachment,
            maintype="application",
            subtype="pdf",
            filename=attachment_filename,
        )

    try:
        with smtplib.SMTP(
            smtp_host,
            smtp_port,
            timeout=15,
        ) as server:
            server.ehlo()

            if use_tls:
                server.starttls()
                server.ehlo()

            if smtp_username and smtp_password:
                server.login(smtp_username, smtp_password)

            server.send_message(message)

        print(f"Email sent successfully to {recipient}.")
        return True

    except Exception as exc:
        print(f"Email delivery failed for {recipient}: {exc}")
        return False


async def send_email(
    recipient: str,
    subject: str,
    body: str,
    attachment: bytes | None = None,
    attachment_filename: str = "ticket.pdf",
) -> bool:
    """Send an email without blocking the asynchronous event loop."""

    return await asyncio.to_thread(
        _send_email_sync,
        recipient,
        subject,
        body,
        attachment,
        attachment_filename,
    )
