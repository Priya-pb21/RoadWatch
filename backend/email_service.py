from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlsplit


def email_configuration() -> dict[str, object]:
    host = os.getenv("ROADWATCH_EMAIL_SMTP_HOST", "").strip()
    sender = os.getenv("ROADWATCH_EMAIL_FROM", "").strip()
    public_base_url = os.getenv("ROADWATCH_PUBLIC_BASE_URL", "").strip().rstrip("/")
    username = os.getenv("ROADWATCH_EMAIL_SMTP_USERNAME", "")
    password = os.getenv("ROADWATCH_EMAIL_SMTP_PASSWORD", "")
    try:
        port = int(os.getenv("ROADWATCH_EMAIL_SMTP_PORT", "587"))
        timeout = float(os.getenv("ROADWATCH_EMAIL_TIMEOUT_SECONDS", "10"))
    except ValueError:
        return {"configured": False}
    parsed_base = urlsplit(public_base_url)
    configured = bool(
        host
        and sender
        and parsed_base.scheme in {"http", "https"}
        and parsed_base.netloc
        and port > 0
        and timeout > 0
        and bool(username) == bool(password)
    )
    return {
        "configured": configured,
        "host": host,
        "sender": sender,
        "public_base_url": public_base_url,
        "username": username,
        "password": password,
        "port": port,
        "timeout": timeout,
        "use_ssl": os.getenv("ROADWATCH_EMAIL_USE_SSL", "false").strip().lower() in {"1", "true", "yes"},
        "use_starttls": os.getenv("ROADWATCH_EMAIL_STARTTLS", "true").strip().lower() in {"1", "true", "yes"},
    }


def send_email(recipient: str, subject: str, text: str) -> None:
    config = email_configuration()
    if not config.get("configured"):
        raise RuntimeError("RoadWatch email delivery is not configured.")

    message = EmailMessage()
    message["From"] = str(config["sender"])
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text)

    host = str(config["host"])
    port = int(config["port"])
    timeout = float(config["timeout"])
    if config["use_ssl"]:
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context()) as server:
            if config["username"]:
                server.login(str(config["username"]), str(config["password"]))
            server.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=timeout) as server:
        server.ehlo()
        if config["use_starttls"]:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if config["username"]:
            server.login(str(config["username"]), str(config["password"]))
        server.send_message(message)
