import logging
import smtplib
from email.message import EmailMessage

from . import config

log = logging.getLogger(__name__)


def send_mail(to: str, subject: str, body: str) -> bool:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.SMTP_FROM, to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as s:
            if config.SMTP_STARTTLS:
                s.starttls()
            if config.SMTP_USER:
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException):
        log.exception("sending mail to %s failed", to)
        return False
