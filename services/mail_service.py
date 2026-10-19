"""Service d'envoi d'e-mails.

Corrige un défaut d'origine : flask-mailman fige sa configuration lors de
``mail.init_app(app)``, appelé dans create_app AVANT le chargement des
réglages depuis la base (``load_configuration``). L'envoi partait donc vers
``localhost:25`` quels que soient les réglages saisis dans Administration ›
Application › E-mails, et un changement n'aurait de toute façon été pris en
compte qu'au redémarrage.

Ici, chaque envoi construit sa connexion à partir des réglages COURANTS
(``app.config``, synchronisé entre processus) ou de réglages fournis (test
avant enregistrement), avec un délai maximal.
"""

from __future__ import annotations

from flask import current_app
from flask_mailman import EmailMultiAlternatives

from extensions import mail
from mail_settings import SMTP_TIMEOUT_SECONDS, explain_smtp_error, settings_from_config

NOT_CONFIGURED_MESSAGE = ("L'envoi d'e-mails n'est pas configuré "
                          "(Administration › Application › E-mails).")


def send_email(to, subject, body, *, html=None, settings=None):
    """Envoie un e-mail. Renvoie ``(True, None)`` ou ``(False, message clair)``.

    ``settings`` : réglages à utiliser (test avant enregistrement) ; à défaut,
    les réglages enregistrés. Ne lève jamais : l'erreur détaillée est
    journalisée, l'appelant reçoit un message affichable (aucun secret).
    """
    settings = settings or settings_from_config(current_app.config)
    if settings is None or not settings.sender:
        return False, NOT_CONFIGURED_MESSAGE

    try:
        connection = mail.get_connection(
            host=settings.host,
            port=settings.port,
            username=settings.username or "",
            password=settings.password or "",
            use_tls=settings.use_tls,
            use_ssl=settings.use_ssl,
            timeout=SMTP_TIMEOUT_SECONDS,
        )
        message = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=settings.sender,
            to=[to],
            connection=connection,
        )
        if html:
            message.attach_alternative(html, "text/html")
        message.send()
    except Exception as exc:  # smtplib, ssl, socket… : tout devient un message
        current_app.logger.error("Échec d'envoi d'e-mail via %s:%s (%s) : %s",
                                 settings.host, settings.port, type(exc).__name__, exc)
        return False, explain_smtp_error(exc, settings)
    return True, None
