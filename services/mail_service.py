"""Service d'envoi d'e-mails : Brevo (API web) ou SMTP, selon les réglages.

Corrige un défaut d'origine : flask-mailman fige sa configuration lors de
``mail.init_app(app)``, appelé dans create_app AVANT le chargement des
réglages depuis la base (``load_configuration``). L'envoi partait donc vers
``localhost:25`` quels que soient les réglages saisis dans Administration ›
Application › E-mails, et un changement n'aurait de toute façon été pris en
compte qu'au redémarrage.

Ici, chaque envoi part des réglages COURANTS (``app.config``, synchronisé
entre processus) ou de réglages fournis (test avant enregistrement), avec un
délai maximal. La clé Brevo n'est jamais journalisée.
"""

from __future__ import annotations

import os

import requests
from flask import current_app
from flask_mailman import EmailMultiAlternatives

from extensions import mail
from mail_settings import (
    BACKEND_BREVO,
    BREVO_API_URL,
    BREVO_ENV_VARIABLE,
    SMTP_TIMEOUT_SECONDS,
    brevo_payload,
    explain_brevo_error,
    explain_brevo_exception,
    explain_smtp_error,
    is_configured,
    settings_from_config,
)

NOT_CONFIGURED_MESSAGE = ("L'envoi d'e-mails n'est pas configuré "
                          "(Administration › Application › E-mails).")


def env_brevo_api_key():
    """Clé fournie par l'hébergement (variable Coolify), ou chaîne vide."""
    return (os.environ.get(BREVO_ENV_VARIABLE) or "").strip()


def current_settings():
    return settings_from_config(current_app.config, env_brevo_api_key())


def mail_configured():
    return is_configured(current_app.config, env_brevo_api_key())


def send_email(to, subject, body, *, html=None, settings=None):
    """Envoie un e-mail. Renvoie ``(True, None)`` ou ``(False, message clair)``.

    ``settings`` : réglages à utiliser (test avant enregistrement) ; à défaut,
    les réglages enregistrés. Ne lève jamais : l'erreur détaillée est
    journalisée, l'appelant reçoit un message affichable (aucun secret).
    """
    settings = settings or current_settings()
    if settings is None or not settings.sender:
        return False, NOT_CONFIGURED_MESSAGE
    if settings.backend == BACKEND_BREVO:
        return _send_brevo(settings, to, subject, body, html)
    return _send_smtp(settings, to, subject, body, html)


def _send_brevo(settings, to, subject, body, html):
    if not settings.sender_name:
        settings = _with_site_name(settings)
    try:
        response = requests.post(
            BREVO_API_URL,
            json=brevo_payload(settings, to, subject, body, html),
            headers={"api-key": settings.api_key, "accept": "application/json"},
            timeout=SMTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        current_app.logger.error("Brevo injoignable (%s)", type(exc).__name__)
        return False, explain_brevo_exception(exc)

    if response.status_code in (200, 201, 202):
        return True, None
    try:
        data = response.json()
    except ValueError:
        data = {}
    current_app.logger.error("Brevo a refusé l'envoi : HTTP %s %s",
                             response.status_code, (data or {}).get("code", ""))
    return False, explain_brevo_error(response.status_code, data)


def _with_site_name(settings):
    from dataclasses import replace
    return replace(settings, sender_name=(current_app.config.get("PHARMACY_NAME") or "").strip())


def _send_smtp(settings, to, subject, body, html):
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
