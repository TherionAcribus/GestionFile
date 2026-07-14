"""Point 5 — Protection des secrets (côté serveur).

Exécutable **sans MySQL ni serveur**. Couvre :

1. Registre (``params_registry``) : ``mail_password`` et ``music_spotify_key``
   sont marqués secrets ; ``is_secret_key`` ; les autres clés ne le sont pas.
2. Exclusion des secrets à l'export (``ConfigSection`` / ``_PageConfigSection``)
   avec ``ConfigOption`` factice, et agrégation dans ``export_sections``.
3. Refus de restaurer une clé secrète (anti-injection).
4. ``preview_backup`` propage ``excluded_secrets``.
5. Régression statique : les gabarits/routes n'exposent plus la valeur.
"""

import os
import types

import pytest

import params_registry as reg
import backup_service as bs


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Registre
# ---------------------------------------------------------------------------

def test_known_secret_keys():
    assert "mail_password" in reg.SECRET_CONFIG_KEYS
    assert "music_spotify_key" in reg.SECRET_CONFIG_KEYS


def test_is_secret_key():
    assert reg.is_secret_key("mail_password")
    assert reg.is_secret_key("music_spotify_key")
    assert not reg.is_secret_key("mail_username")
    assert not reg.is_secret_key("music_spotify_user")
    assert not reg.is_secret_key(None)
    assert not reg.is_secret_key(123)


def test_spec_secret_flag():
    for key in reg.SECRET_CONFIG_KEYS:
        assert reg.get_spec(key).secret is True
    # Une clé voisine non secrète ne l'est pas.
    assert reg.get_spec("mail_username").secret is False
    assert reg.get_spec("music_spotify_user").secret is False


def test_no_unexpected_secret_flags():
    flagged = {k for k, s in reg.PARAM_REGISTRY.items() if s.secret}
    assert flagged == set(reg.SECRET_CONFIG_KEYS)


# ---------------------------------------------------------------------------
# 2. Exclusion à l'export (ConfigOption factice)
# ---------------------------------------------------------------------------

class _FakeOption:
    def __init__(self, key, value_str=None, value_bool=None, value_int=None,
                 value_text=None, value_json=None):
        self.config_key = key
        self.value_str = value_str
        self.value_bool = value_bool
        self.value_int = value_int
        self.value_text = value_text
        self.value_json = value_json


def _patch_config_options(monkeypatch, options):
    fake = types.SimpleNamespace(
        query=types.SimpleNamespace(all=lambda: list(options))
    )
    monkeypatch.setattr(bs, "ConfigOption", fake)


def test_config_section_excludes_secrets(monkeypatch):
    options = [
        _FakeOption("pharmacy_name", value_str="Ma Pharmacie"),
        _FakeOption("mail_username", value_str="user@example.com"),
        _FakeOption("mail_password", value_str="super-secret"),
        _FakeOption("music_spotify_key", value_str="spotify-secret"),
    ]
    _patch_config_options(monkeypatch, options)

    section = bs.ConfigSection()
    result = section.export_data()

    assert "pharmacy_name" in result
    assert "mail_username" in result
    # Les valeurs secrètes ne figurent nulle part dans l'export.
    assert "mail_password" not in result
    assert "music_spotify_key" not in result
    assert "super-secret" not in result.values()
    assert "spotify-secret" not in result.values()
    # Elles sont mémorisées pour l'avertissement (nom seulement).
    assert set(section.excluded_secrets) == {"mail_password", "music_spotify_key"}


def test_export_sections_aggregates_excluded_secrets(monkeypatch):
    options = [
        _FakeOption("pharmacy_name", value_str="X"),
        _FakeOption("mail_password", value_str="secret"),
    ]
    _patch_config_options(monkeypatch, options)

    backup = bs.export_sections(["config"])
    assert backup["excluded_secrets"] == ["mail_password"]
    assert "mail_password" not in backup["data"]["config"]


def test_export_without_secrets_reports_empty_list(monkeypatch):
    options = [_FakeOption("pharmacy_name", value_str="X")]
    _patch_config_options(monkeypatch, options)
    backup = bs.export_sections(["config"])
    assert backup["excluded_secrets"] == []


# ---------------------------------------------------------------------------
# 3. Refus de restaurer une clé secrète (anti-injection)
# ---------------------------------------------------------------------------

def test_restore_skips_secret_keys(monkeypatch):
    """Un fichier forgé contenant un secret ne doit rien écrire pour cette clé.

    Avec uniquement des clés secrètes en entrée, la boucle les ignore toutes :
    ``ConfigOption`` n'est jamais interrogé et rien n'est ajouté à la session."""
    filtered = []

    class _FakeConfigOption:
        class query:
            @staticmethod
            def filter_by(**kw):
                filtered.append(kw.get("config_key"))
                return types.SimpleNamespace(first=lambda: None)

    monkeypatch.setattr(bs, "ConfigOption", _FakeConfigOption)

    added = []
    monkeypatch.setattr(bs.db.session, "add", lambda o: added.append(o))
    monkeypatch.setattr(bs.db.session, "commit", lambda: None)

    # Le bump de génération (point 11) touche ConfigOption ; hors sujet ici
    # (ce test vérifie uniquement l'exclusion des secrets) : neutralisé, comme
    # commit/add/load_configuration.
    monkeypatch.setattr(bs.config_sync, "bump_generation", lambda *a, **k: None)

    # current_app.load_configuration est appelé en fin de restore : neutralisé.
    monkeypatch.setattr(
        bs, "current_app",
        types.SimpleNamespace(
            load_configuration=lambda _app: None,
            _config_generation=0,
        ),
    )

    section = bs.ConfigSection()
    section.restore_data({"mail_password": "attacker", "music_spotify_key": "x"})

    # Aucune des clés secrètes n'a été recherchée ni créée.
    assert filtered == []
    assert added == []


# ---------------------------------------------------------------------------
# 4. preview_backup propage l'avertissement
# ---------------------------------------------------------------------------

def test_preview_backup_carries_excluded_secrets():
    backup = {
        "app": "GestionFile",
        "format_version": "2.0",
        "timestamp": "2026-07-13 10:00:00",
        "sections": ["config"],
        "excluded_secrets": ["mail_password"],
        "data": {"config": {"pharmacy_name": "X"}},
    }
    info = bs.preview_backup(backup)
    assert info["excluded_secrets"] == ["mail_password"]


def test_preview_backup_defaults_excluded_secrets_empty():
    backup = {
        "app": "GestionFile", "format_version": "2.0", "timestamp": "",
        "sections": [], "data": {},
    }
    assert bs.preview_backup(backup)["excluded_secrets"] == []


# ---------------------------------------------------------------------------
# 5. Régression statique : plus de valeur secrète exposée
# ---------------------------------------------------------------------------

def test_mail_template_uses_secret_macro():
    html = _read("templates/admin/app_mail.html")
    assert "secret_full(\"mail_password\"" in html
    # L'ancienne forme qui rendait la valeur ne doit plus exister.
    assert "textarea_full(\"mail_password\"" not in html


def test_music_template_uses_secret_macro():
    html = _read("templates/admin/music_options.html")
    assert "secret_full(\"music_spotify_key\"" in html
    assert "textarea_full(\"music_spotify_key\"" not in html


def test_mail_route_does_not_pass_password_value():
    src = _read("routes/admin_app.py")
    assert "mail_password_set" in src
    assert 'mail_password=app.config["MAIL_PASSWORD"]' not in src


def test_music_route_does_not_pass_key_value():
    src = _read("routes/admin_music.py")
    assert "music_spotify_key_set" in src
    assert 'music_spotify_key = app.config["MUSIC_SPOTIFY_KEY"]' not in src


def test_update_input_keeps_secret_on_empty():
    """La route ``update_input`` conserve un secret quand le champ est vide."""
    src = _read("app.py")
    assert "spec.secret and value.strip()" in src


def test_secret_field_macro_never_renders_value():
    macros = _read("templates/admin/macros.html")
    assert "macro secret_field(" in macros
    assert "macro secret_full(" in macros
    # Le champ secret est de type password et rendu vide.
    assert 'type="password"' in macros
