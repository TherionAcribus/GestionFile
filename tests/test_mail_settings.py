"""Réglages d'envoi d'e-mails — noyau pur (mail_settings.py)."""

import smtplib
import socket
import ssl

import pytest

from mail_settings import (
    DEFAULT_PORTS,
    PROVIDERS,
    SECURITY_MODES,
    MailSettings,
    explain_smtp_error,
    guess_provider,
    is_configured,
    provider_by_id,
    provider_for_host,
    providers_payload,
    security_flags,
    security_mode,
    settings_from_config,
    validate_mail_form,
)


# --- Préréglages ----------------------------------------------------------------

def test_preréglages_coherents():
    ids = [p.id for p in PROVIDERS]
    assert len(ids) == len(set(ids))
    assert "other" not in ids  # réservé au choix « Autre » du formulaire
    for p in PROVIDERS:
        assert p.security in SECURITY_MODES
        assert 1 <= p.port <= 65535
        assert " " not in p.host and "://" not in p.host
    # Un domaine ne mène qu'à une seule messagerie.
    domains = [d for p in PROVIDERS for d in p.domains]
    assert len(domains) == len(set(domains))


@pytest.mark.parametrize("address,expected", [
    ("pharmacie@gmail.com", "gmail"),
    ("Pharmacie@GoogleMail.com ", "gmail"),
    ("x@hotmail.fr", "outlook"),
    ("x@wanadoo.fr", "orange"),
    ("x@free.fr", "free"),
    ("x@neuf.fr", "sfr"),
    ("x@ma-pharmacie.fr", None),
    ("pas-une-adresse", None),
    ("", None),
])
def test_messagerie_devinee_par_le_domaine(address, expected):
    provider = guess_provider(address)
    assert (provider.id if provider else None) == expected


def test_messagerie_retrouvee_depuis_le_serveur():
    assert provider_for_host("SMTP.gmail.com").id == "gmail"
    assert provider_for_host("smtp.inconnu.fr") is None
    assert provider_by_id("brevo").host == "smtp-relay.brevo.com"


def test_payload_serialisable():
    payload = providers_payload()
    assert payload[0].keys() == {"id", "label", "host", "port", "security", "hint", "domains"}
    assert isinstance(payload[0]["domains"], list)


# --- Sécurité : un seul choix au lieu de deux interrupteurs ----------------------

@pytest.mark.parametrize("tls,ssl_,mode", [
    (True, False, "starttls"),
    (False, True, "ssl"),
    (False, False, "none"),
    # Ancienne configuration incohérente (les deux cochés) : STARTTLS.
    (True, True, "starttls"),
    (None, None, "none"),
])
def test_mode_de_securite(tls, ssl_, mode):
    assert security_mode(tls, ssl_) == mode


def test_drapeaux_jamais_les_deux():
    for mode in SECURITY_MODES:
        tls, ssl_ = security_flags(mode)
        assert not (tls and ssl_)
    assert security_flags("ssl") == (False, True)


# --- Validation du formulaire ---------------------------------------------------

_GOOD = {"host": "smtp.gmail.com", "port": "587", "security": "starttls",
         "username": "pharmacie@gmail.com", "password": "app-pass", "sender": ""}


def test_formulaire_valide_et_expediteur_par_defaut():
    settings, errors = validate_mail_form(_GOOD)
    assert errors == []
    assert settings == MailSettings("smtp.gmail.com", 587, "starttls",
                                    "pharmacie@gmail.com", "app-pass", "pharmacie@gmail.com")
    assert settings.use_tls and not settings.use_ssl


def test_formulaire_nettoie_le_serveur_et_deduit_le_port():
    settings, errors = validate_mail_form({**_GOOD, "host": " smtps://SSL0.ovh.net/ ",
                                           "port": "", "security": "ssl"})
    assert errors == []
    assert settings.host == "ssl0.ovh.net"
    assert settings.port == DEFAULT_PORTS["ssl"] == 465


def test_mot_de_passe_vide_conserve_celui_enregistre():
    settings, errors = validate_mail_form({**_GOOD, "password": ""}, stored_password="ancien")
    assert errors == []
    assert settings.password == "ancien"


@pytest.mark.parametrize("override,fragment", [
    ({"host": ""}, "serveur"),
    ({"host": "smtp gmail.com"}, "nom du serveur"),
    ({"port": "70000"}, "port"),
    ({"port": "abc"}, "port"),
    ({"security": "tls+ssl"}, "sécurité"),
    ({"password": ""}, "mot de passe"),
    ({"username": "", "sender": "pas-une-adresse"}, "expéditeur"),
])
def test_formulaire_erreurs_explicites(override, fragment):
    settings, errors = validate_mail_form({**_GOOD, **override})
    assert settings is None
    assert any(fragment in e.lower() for e in errors), errors


def test_compte_sans_authentification_accepte():
    settings, errors = validate_mail_form({**_GOOD, "username": "", "password": "",
                                           "sender": "file@pharmacie.fr"})
    assert errors == []
    assert settings.username == ""


# --- Réglages courants (app.config) ---------------------------------------------

def test_reglages_depuis_la_config():
    config = {"MAIL_SERVER": "smtp.orange.fr", "MAIL_PORT": "", "MAIL_USE_TLS": False,
              "MAIL_USE_SSL": True, "MAIL_USERNAME": "x@orange.fr", "MAIL_PASSWORD": "p",
              "MAIL_DEFAULT_SENDER": ""}
    settings = settings_from_config(config)
    assert settings.port == 465
    assert settings.sender == "x@orange.fr"
    assert is_configured(config)


def test_non_configure():
    assert settings_from_config({}) is None
    assert not is_configured({})
    # Serveur sans expéditeur exploitable : pas configuré.
    assert not is_configured({"MAIL_SERVER": "smtp.x.fr", "MAIL_USERNAME": "login"})


# --- Messages d'erreur ----------------------------------------------------------

_GMAIL = MailSettings("smtp.gmail.com", 587, "starttls", "a@gmail.com", "p", "a@gmail.com")
_OTHER = MailSettings("smtp.x.fr", 587, "starttls", "a@x.fr", "p", "a@x.fr")


def test_authentification_gmail_conseille_le_mot_de_passe_application():
    exc = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
    message = explain_smtp_error(exc, _GMAIL)
    assert "identifiant ou le mot de passe" in message
    assert "mot de passe d'application" in message
    assert "535 5.7.8 Username and Password not accepted" in message


def test_authentification_autre_fournisseur_conseil_generique():
    exc = smtplib.SMTPAuthenticationError(535, b"refused")
    assert "mot de passe d'application" in explain_smtp_error(exc, _OTHER)


@pytest.mark.parametrize("exc,fragment", [
    (smtplib.SMTPSenderRefused(553, b"no", "a@x.fr"), "expéditeur"),
    (smtplib.SMTPRecipientsRefused({"b@x.fr": (550, b"no")}), "destinataire"),
    (smtplib.SMTPNotSupportedError("STARTTLS"), "465"),
    (smtplib.SMTPServerDisconnected("closed"), "587 → STARTTLS"),
    (ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number"), "465 → SSL"),
    (ssl.SSLError(1, "certificate verify failed"), "certificat"),
    (socket.gaierror(-2, "Name or service not known"), "introuvable"),
    (ConnectionRefusedError(111, "refused"), "refuse la connexion"),
    (TimeoutError("timed out"), "bloqué par l'hébergeur"),
    (OSError("network unreachable"), "joindre"),
    (ValueError("boom"), "échoué"),
])
def test_chaque_cause_a_son_explication(exc, fragment):
    assert fragment in explain_smtp_error(exc, _OTHER)


def test_explication_sans_reglages():
    assert explain_smtp_error(TimeoutError(), None)
