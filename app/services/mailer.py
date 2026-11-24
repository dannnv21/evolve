import logging
import smtplib
from email.message import EmailMessage

from flask import current_app

logger = logging.getLogger(__name__)


def send_email(recipient: str, subject: str, body: str) -> None:
    """Envía por SMTP si está configurado; si no, deja el correo en el log (modo desarrollo)."""
    config = current_app.config
    if not config["MAIL_SERVER"]:
        logger.info("Correo para %s · %s\n%s", recipient, subject, body)
        return
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = config["MAIL_FROM"], recipient, subject
    message.set_content(body)
    try:
        with smtplib.SMTP(config["MAIL_SERVER"], config["MAIL_PORT"], timeout=10) as server:
            if config["MAIL_USE_TLS"]:
                server.starttls()
            if config["MAIL_USERNAME"]:
                server.login(config["MAIL_USERNAME"], config["MAIL_PASSWORD"])
            server.send_message(message)
    except (OSError, smtplib.SMTPException):
        logger.exception("No se pudo enviar el correo a %s", recipient)
