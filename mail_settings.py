"""Réglages d'envoi d'e-mails — noyau pur, sans Flask.

Objectif : qu'une personne aux connaissances informatiques moyennes arrive à
configurer l'envoi sans connaître SMTP.

Deux modes d'envoi (``BACKENDS``) :

- ``brevo`` (recommandé) : service d'envoi Brevo par son API web. Une clé et
  une adresse d'expéditeur suffisent, en HTTPS (port 443, jamais bloqué par
  les hébergeurs). Chaque installation saisit SA clé : elle vit dans la base
  (secret jamais réaffiché ni sauvegardé) ou dans la variable d'environnement
  ``BREVO_API_KEY`` — jamais dans le code, qui est public ;
- ``smtp`` : la messagerie de la pharmacie (Gmail, Orange…).

- ``PROVIDERS`` : préréglages des messageries courantes (serveur, port,
  sécurité, conseil) ; ``guess_provider`` le devine depuis l'adresse ;
- une seule notion de « sécurité » (``starttls`` / ``ssl`` / ``none``) au lieu
  de deux interrupteurs TLS et SSL qui pouvaient être cochés ensemble ;
- ``validate_mail_form`` contrôle un formulaire complet avant test ou
  enregistrement ;
- ``explain_smtp_error`` traduit une exception d'envoi en message clair, avec
  le conseil qui débloque le plus souvent la situation.
"""

from __future__ import annotations

import re
import smtplib
import socket
import ssl
from dataclasses import dataclass

SECURITY_STARTTLS = "starttls"
SECURITY_SSL = "ssl"
SECURITY_NONE = "none"
SECURITY_MODES = (SECURITY_STARTTLS, SECURITY_SSL, SECURITY_NONE)

DEFAULT_PORTS = {SECURITY_STARTTLS: 587, SECURITY_SSL: 465, SECURITY_NONE: 25}

# Délai maximal d'une connexion SMTP : sans lui, un port filtré bloquait la
# requête (et le worker) jusqu'au délai TCP du système.
SMTP_TIMEOUT_SECONDS = 10

BACKEND_BREVO = "brevo"
BACKEND_SMTP = "smtp"
BACKENDS = (BACKEND_BREVO, BACKEND_SMTP)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_ENV_VARIABLE = "BREVO_API_KEY"
# Brevo délivre aussi des « clés SMTP » (xsmtpsib-…), souvent copiées par
# erreur à la place de la clé API (xkeysib-…).
_BREVO_SMTP_KEY_PREFIX = "xsmtpsib-"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    host: str
    port: int
    security: str
    hint: str = ""
    domains: tuple = ()


_APP_PASSWORD_HINT = (
    "Utilisez un « mot de passe d'application » et non votre mot de passe habituel "
    "(il se crée dans les réglages de sécurité du compte, validation en deux étapes activée)."
)

PROVIDERS = (
    Provider("gmail", "Gmail", "smtp.gmail.com", 587, SECURITY_STARTTLS,
             "Gmail exige un mot de passe d'application : compte Google › Sécurité › "
             "Validation en deux étapes › Mots de passe des applications.",
             ("gmail.com", "googlemail.com")),
    Provider("outlook", "Outlook.com / Hotmail", "smtp-mail.outlook.com", 587, SECURITY_STARTTLS,
             "Microsoft restreint de plus en plus l'envoi par mot de passe : si le test "
             "échoue, préférez un service d'envoi comme Brevo.",
             ("outlook.com", "outlook.fr", "hotmail.com", "hotmail.fr", "live.com", "live.fr",
              "msn.com")),
    Provider("office365", "Microsoft 365 (professionnel)", "smtp.office365.com", 587,
             SECURITY_STARTTLS,
             "L'envoi SMTP doit être autorisé pour ce compte par l'administrateur Microsoft 365."),
    Provider("ovh", "OVH (messagerie de votre domaine)", "ssl0.ovh.net", 465, SECURITY_SSL,
             "Identifiant : l'adresse e-mail complète."),
    Provider("orange", "Orange / Wanadoo", "smtp.orange.fr", 465, SECURITY_SSL,
             "Identifiant : l'adresse e-mail complète.",
             ("orange.fr", "wanadoo.fr")),
    Provider("free", "Free", "smtp.free.fr", 465, SECURITY_SSL,
             "L'envoi SMTP doit être activé dans l'espace abonné de la messagerie Free.",
             ("free.fr",)),
    Provider("sfr", "SFR", "smtp.sfr.fr", 465, SECURITY_SSL, "",
             ("sfr.fr", "neuf.fr")),
    Provider("laposte", "La Poste", "smtp.laposte.net", 465, SECURITY_SSL, "",
             ("laposte.net",)),
    Provider("infomaniak", "Infomaniak", "mail.infomaniak.com", 587, SECURITY_STARTTLS,
             "Identifiant : l'adresse e-mail complète."),
    Provider("ionos", "IONOS", "smtp.ionos.fr", 587, SECURITY_STARTTLS,
             "Identifiant : l'adresse e-mail complète."),
    Provider("brevo", "Brevo (service d'envoi, gratuit jusqu'à 300 e-mails/jour)",
             "smtp-relay.brevo.com", 587, SECURITY_STARTTLS,
             "Identifiant et clé SMTP : Brevo › SMTP et API › SMTP. L'adresse d'expéditeur "
             "doit être validée dans Brevo."),
)

_BY_ID = {p.id: p for p in PROVIDERS}


def provider_by_id(provider_id):
    return _BY_ID.get(provider_id)


def provider_for_host(host):
    """Préréglage correspondant à un serveur déjà saisi (pour l'afficher)."""
    host = (host or "").strip().lower()
    return next((p for p in PROVIDERS if p.host == host), None)


def guess_provider(address):
    """Préréglage déduit du domaine d'une adresse e-mail (ou None)."""
    address = (address or "").strip().lower()
    if "@" not in address:
        return None
    domain = address.rsplit("@", 1)[1]
    return next((p for p in PROVIDERS if domain in p.domains), None)


def providers_payload():
    """Préréglages sérialisables (attribut data-* lu par admin_mail.js)."""
    return [
        {"id": p.id, "label": p.label, "host": p.host, "port": p.port,
         "security": p.security, "hint": p.hint, "domains": list(p.domains)}
        for p in PROVIDERS
    ]


def security_mode(use_tls, use_ssl):
    """Mode unique à partir des deux booléens historiques."""
    if use_ssl and not use_tls:
        return SECURITY_SSL
    if use_tls:
        return SECURITY_STARTTLS
    return SECURITY_NONE


def security_flags(mode):
    """``(use_tls, use_ssl)`` pour un mode — jamais les deux à la fois."""
    return mode == SECURITY_STARTTLS, mode == SECURITY_SSL


def is_valid_email(address):
    return bool(address) and bool(EMAIL_RE.match(address.strip()))


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    security: str
    username: str
    password: str
    sender: str
    backend: str = BACKEND_SMTP
    api_key: str = ""
    # Nom affiché à côté de l'adresse d'expéditeur (ex. nom de la pharmacie).
    sender_name: str = ""

    @property
    def use_tls(self):
        return security_flags(self.security)[0]

    @property
    def use_ssl(self):
        return security_flags(self.security)[1]


def _as_port(value):
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def backend_from_config(config):
    """Mode d'envoi enregistré.

    Sans choix enregistré (installations antérieures) : SMTP si un serveur a
    déjà été saisi, sinon Brevo, le mode recommandé.
    """
    backend = (config.get("MAIL_BACKEND") or "").strip().lower()
    if backend in BACKENDS:
        return backend
    return BACKEND_SMTP if (config.get("MAIL_SERVER") or "").strip() else BACKEND_BREVO


def brevo_api_key(config, env_api_key=""):
    """Clé Brevo : celle de l'administration, sinon la variable d'environnement."""
    return (config.get("MAIL_BREVO_API_KEY") or "").strip() or (env_api_key or "").strip()


def settings_from_config(config, env_api_key=""):
    """Réglages courants (app.config), ou None si l'envoi n'est pas configuré."""
    if backend_from_config(config) == BACKEND_BREVO:
        api_key = brevo_api_key(config, env_api_key)
        if not api_key:
            return None
        return MailSettings(host="", port=0, security=SECURITY_NONE, username="", password="",
                            sender=(config.get("MAIL_DEFAULT_SENDER") or "").strip(),
                            backend=BACKEND_BREVO, api_key=api_key,
                            sender_name=(config.get("PHARMACY_NAME") or "").strip())

    host = (config.get("MAIL_SERVER") or "").strip()
    if not host:
        return None
    security = security_mode(config.get("MAIL_USE_TLS"), config.get("MAIL_USE_SSL"))
    username = (config.get("MAIL_USERNAME") or "").strip()
    sender = (config.get("MAIL_DEFAULT_SENDER") or "").strip()
    if not sender and is_valid_email(username):
        sender = username
    return MailSettings(
        host=host,
        port=_as_port(config.get("MAIL_PORT")) or DEFAULT_PORTS[security],
        security=security,
        username=username,
        password=config.get("MAIL_PASSWORD") or "",
        sender=sender,
    )


def is_configured(config, env_api_key=""):
    """Vrai si l'envoi est possible : serveur ou clé, ET adresse d'expéditeur."""
    settings = settings_from_config(config, env_api_key)
    return settings is not None and is_valid_email(settings.sender)


def validate_mail_form(data, *, stored_password="", stored_api_key=""):
    """Contrôle un formulaire complet. Renvoie ``(MailSettings | None, erreurs)``.

    Mot de passe ou clé vides = conserver ceux qui sont enregistrés (champs
    secrets). ``stored_api_key`` inclut la clé de l'environnement.
    """
    backend = (data.get("backend") or BACKEND_SMTP).strip().lower()
    if backend not in BACKENDS:
        return None, ["Choisissez le mode d'envoi."]
    if backend == BACKEND_BREVO:
        return _validate_brevo_form(data, stored_api_key)

    errors = []
    host = (data.get("host") or "").strip().lower()
    if host.startswith(("smtp://", "smtps://", "http://", "https://")):
        host = host.split("://", 1)[1].rstrip("/")
    if not host:
        errors.append("Indiquez le serveur d'envoi (ex. smtp.gmail.com).")
    elif not _HOST_RE.match(host):
        errors.append("Le nom du serveur n'est pas valide (lettres, chiffres, points et tirets).")

    security = (data.get("security") or "").strip().lower()
    if security not in SECURITY_MODES:
        errors.append("Choisissez le type de sécurité de la connexion.")
        security = SECURITY_STARTTLS

    raw_port = (str(data.get("port") or "")).strip()
    port = _as_port(raw_port) if raw_port else DEFAULT_PORTS[security]
    if port is None:
        errors.append("Le port doit être un nombre entre 1 et 65535.")

    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not password.strip():
        password = stored_password or ""
    if username and not password:
        errors.append("Indiquez le mot de passe du compte d'envoi.")

    sender = (data.get("sender") or "").strip()
    if not sender and is_valid_email(username):
        sender = username
    if not is_valid_email(sender):
        errors.append("Indiquez une adresse d'expéditeur valide (ex. pharmacie@exemple.fr).")

    if errors:
        return None, errors
    return MailSettings(host=host, port=port, security=security, username=username,
                        password=password, sender=sender), []


def _validate_brevo_form(data, stored_api_key):
    errors = []
    api_key = (data.get("api_key") or "").strip() or (stored_api_key or "").strip()
    if not api_key:
        errors.append("Collez la clé API Brevo (Brevo › SMTP et API › Clés API).")
    elif api_key.startswith(_BREVO_SMTP_KEY_PREFIX):
        errors.append("C'est une clé SMTP Brevo (xsmtpsib-…) : créez plutôt une clé API "
                      "(xkeysib-…) dans Brevo › SMTP et API › Clés API.")
    elif any(c.isspace() for c in api_key) or len(api_key) > 200:
        errors.append("La clé API ne doit contenir ni espace ni retour à la ligne.")
    sender = (data.get("sender") or "").strip()
    if not is_valid_email(sender):
        errors.append("Indiquez l'adresse d'expéditeur validée chez Brevo (ex. pharmacie@exemple.fr).")
    if errors:
        return None, errors
    return MailSettings(host="", port=0, security=SECURITY_NONE, username="", password="",
                        sender=sender, backend=BACKEND_BREVO, api_key=api_key), []


def brevo_payload(settings, to, subject, body, html=None):
    """Corps JSON de l'appel « envoyer un e-mail » de l'API Brevo."""
    sender = {"email": settings.sender}
    if settings.sender_name:
        sender["name"] = settings.sender_name
    payload = {"sender": sender, "to": [{"email": to}], "subject": subject,
               "textContent": body}
    if html:
        payload["htmlContent"] = html
    return payload


def explain_brevo_error(status, data=None):
    """Message clair pour un refus de l'API Brevo (statut HTTP + corps JSON)."""
    data = data if isinstance(data, dict) else {}
    detail = " ".join(str(data.get("message") or "").split())[:200]
    text = detail.lower()
    if status == 401 and "ip" in text:
        message = ("Brevo bloque l'adresse IP de ce serveur. Brevo › Sécurité › IP autorisées : "
                   "désactivez le blocage des IP inconnues, ou ajoutez l'adresse de ce serveur.")
    elif status == 401:
        message = ("Clé API refusée par Brevo. Vérifiez que vous avez collé une clé API "
                   "complète (elle commence par xkeysib-) et qu'elle n'a pas été supprimée.")
    elif "sender" in text or "expéditeur" in text:
        message = ("Adresse d'expéditeur refusée : elle doit être validée chez Brevo "
                   "(Brevo › Expéditeurs, domaines et IP dédiées › Expéditeurs).")
    elif status == 402:
        message = "Crédits d'envoi Brevo épuisés pour la période."
    elif status == 403 or "not activated" in text or "activ" in text:
        message = ("Le compte Brevo n'est pas encore activé pour l'envoi "
                   "(Brevo vérifie les nouveaux comptes, parfois sous quelques heures).")
    elif status == 429:
        message = "Trop d'envois en peu de temps : réessayez dans quelques minutes."
    elif status and status >= 500:
        message = "Brevo est momentanément indisponible : réessayez plus tard."
    else:
        message = "Brevo a refusé l'envoi."
    if detail:
        message += f" Réponse de Brevo : {detail}"
    return message


def explain_brevo_exception(exc):
    """Échec réseau avant toute réponse de Brevo."""
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "Brevo ne répond pas : la connexion Internet du serveur est peut-être lente ou coupée."
    if "ssl" in name:
        return "La connexion sécurisée à Brevo a échoué (date/heure du serveur ou proxy ?)."
    return ("Impossible de joindre Brevo depuis ce serveur : vérifiez sa connexion Internet "
            "(ou un pare-feu qui bloquerait les sorties HTTPS).")


def _server_detail(exc):
    """Réponse du serveur distant (code + texte), sûre à afficher à l'admin."""
    code = getattr(exc, "smtp_code", None)
    message = getattr(exc, "smtp_error", b"")
    if isinstance(message, bytes):
        message = message.decode("utf-8", "replace")
    message = " ".join(str(message).split())[:200]
    if code and message:
        return f"{code} {message}"
    return message or ""


def explain_smtp_error(exc, settings=None):
    """Message clair (français) pour un échec d'envoi, avec un conseil."""
    provider = provider_for_host(settings.host) if settings else None
    hint = provider.hint if provider else ""

    if isinstance(exc, smtplib.SMTPAuthenticationError):
        message = "Le serveur refuse l'identifiant ou le mot de passe."
        if not hint:
            hint = _APP_PASSWORD_HINT
    elif isinstance(exc, smtplib.SMTPSenderRefused):
        message = "Le serveur refuse l'adresse d'expéditeur."
        hint = "Utilisez la même adresse que l'identifiant du compte (ou une adresse validée chez votre fournisseur)."
    elif isinstance(exc, smtplib.SMTPRecipientsRefused):
        message = "Le serveur refuse l'adresse du destinataire."
        hint = "Vérifiez l'adresse saisie."
    elif isinstance(exc, smtplib.SMTPNotSupportedError):
        message = "Le serveur ne propose pas la sécurité STARTTLS."
        hint = "Essayez « SSL » avec le port 465."
    elif isinstance(exc, smtplib.SMTPServerDisconnected):
        message = "Le serveur a coupé la connexion."
        hint = "Souvent une sécurité qui ne correspond pas au port : 587 → STARTTLS, 465 → SSL."
    elif isinstance(exc, ssl.SSLError):
        text = str(exc).lower()
        if "certificate" in text:
            message = "Le certificat de sécurité du serveur n'est pas valide."
            hint = "Vérifiez le nom exact du serveur fourni par votre messagerie."
        else:
            message = "La connexion sécurisée a échoué."
            hint = "La sécurité ne correspond pas au port : 587 → STARTTLS, 465 → SSL."
    elif isinstance(exc, socket.gaierror):
        message = "Serveur introuvable."
        hint = "Vérifiez l'orthographe du nom du serveur."
    elif isinstance(exc, ConnectionRefusedError):
        message = "Le serveur refuse la connexion sur ce port."
        hint = "Vérifiez le port : 587 (STARTTLS) ou 465 (SSL) en général."
    elif isinstance(exc, (TimeoutError, socket.timeout)):
        message = "Le serveur ne répond pas."
        hint = ("Le port est peut-être bloqué par l'hébergeur de ce serveur (fréquent pour 25 "
                "et 465) : essayez le port 587 avec STARTTLS, ou demandez le déblocage.")
    elif isinstance(exc, smtplib.SMTPException):
        message = "Le serveur d'envoi a refusé le message."
    elif isinstance(exc, OSError):
        message = "Impossible de joindre le serveur d'envoi."
        hint = hint or "Vérifiez le serveur et le port."
    else:
        message = "L'envoi a échoué."

    detail = _server_detail(exc)
    parts = [message]
    if hint:
        parts.append(hint)
    if detail:
        parts.append(f"Réponse du serveur : {detail}")
    return " ".join(parts)
