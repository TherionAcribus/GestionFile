"""Tests du point 11 — synchronisation des paramètres entre processus.

Trois niveaux, tous exécutables **sans MySQL ni serveur** :

1. Logique pure (throttle, comparaison de génération, clés réservées) et
   reclassement dynamique / redémarrage du registre.
2. Orchestrateur ``maybe_reload_configuration`` / ``mark_current_generation``
   avec une fausse application (points d'injection ``_now`` /
   ``_read_generation`` / ``_load_configuration``).
3. Compteur de génération réel (``bump_generation`` / ``read_generation`` /
   ``ensure_generation_row``) sur une base SQLite en mémoire, plus une
   régression statique sur les sources (routes ``update_*``, ``before_request``,
   tâches scheduler).
"""

import os
import re
import types

import pytest
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

import config_sync
import params_registry as reg


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Logique pure + registre
# ---------------------------------------------------------------------------

def test_should_check_now_throttle():
    # Première vérification toujours autorisée.
    assert config_sync.should_check_now(None, 100.0, 2.0) is True
    # Dans la fenêtre de throttle : refusé.
    assert config_sync.should_check_now(100.0, 101.0, 2.0) is False
    # Au bord (>=) : autorisé.
    assert config_sync.should_check_now(100.0, 102.0, 2.0) is True
    assert config_sync.should_check_now(100.0, 105.0, 2.0) is True


def test_should_reload_on_generation_change():
    # Pas encore de génération mémorisée -> recharger.
    assert config_sync.should_reload(None, 0) is True
    assert config_sync.should_reload(None, 5) is True
    # Identique -> ne pas recharger.
    assert config_sync.should_reload(3, 3) is False
    # Différente -> recharger.
    assert config_sync.should_reload(3, 4) is True
    assert config_sync.should_reload(4, 3) is True


def test_is_reserved_key():
    assert config_sync.is_reserved_key(config_sync.CONFIG_GENERATION_KEY) is True
    assert config_sync.is_reserved_key("__config_generation__") is True
    assert config_sync.is_reserved_key("pharmacy_name") is False
    assert config_sync.is_reserved_key(None) is False
    assert config_sync.is_reserved_key(42) is False


def test_reserved_key_not_in_registry_or_mappings():
    """La clé de génération ne doit jamais être une clé métier."""
    assert config_sync.CONFIG_GENERATION_KEY not in reg.PARAM_REGISTRY
    assert config_sync.CONFIG_GENERATION_KEY not in reg.CONFIG_MAPPINGS
    assert reg.get_spec(config_sync.CONFIG_GENERATION_KEY) is None


def test_network_adress_is_dynamic_now():
    """network_adress est relu en direct (engine.set_server_url) : dynamique,
    donc synchronisable à chaud — plus « redémarrage requis »."""
    assert reg.is_restart_required("network_adress") is False
    assert "network_adress" not in reg.RESTART_REQUIRED_KEYS


def test_start_rabbitmq_requires_restart():
    """start_rabbitmq est consommé à l'init du process (message_queue SocketIO)."""
    assert reg.is_restart_required("start_rabbitmq") is True
    assert "start_rabbitmq" in reg.RESTART_REQUIRED_KEYS
    assert reg.get_spec("start_rabbitmq").restart_required is True


def test_is_restart_required_unknown_key():
    assert reg.is_restart_required("does_not_exist") is False
    assert reg.is_restart_required(None) is False


# ---------------------------------------------------------------------------
# 2. Orchestrateur avec fausse application
# ---------------------------------------------------------------------------

class _FakeApp:
    def __init__(self):
        self.config = {}
        self.load_calls = 0

    def load_configuration(self, app):
        self.load_calls += 1


def test_maybe_reload_triggers_reload_on_change():
    app = _FakeApp()
    app._config_generation = 1
    app._config_gen_last_check = None
    reloaded = config_sync.maybe_reload_configuration(
        app, _now=1000.0, _read_generation=lambda: 2)
    assert reloaded is True
    assert app.load_calls == 1
    assert app._config_generation == 2  # cache mis à jour


def test_maybe_reload_skips_when_unchanged():
    app = _FakeApp()
    app._config_generation = 7
    app._config_gen_last_check = None
    reloaded = config_sync.maybe_reload_configuration(
        app, _now=1000.0, _read_generation=lambda: 7)
    assert reloaded is False
    assert app.load_calls == 0
    assert app._config_generation == 7


def test_maybe_reload_respects_throttle():
    app = _FakeApp()
    app._config_generation = 1
    app._config_gen_last_check = 1000.0
    calls = {"n": 0}

    def reader():
        calls["n"] += 1
        return 999

    # 0.5s après la dernière vérif, avec min_interval=2 -> throttlé, pas de lecture.
    reloaded = config_sync.maybe_reload_configuration(
        app, min_interval=2.0, _now=1000.5, _read_generation=reader)
    assert reloaded is False
    assert calls["n"] == 0          # la base n'a même pas été interrogée
    assert app.load_calls == 0


def test_maybe_reload_force_bypasses_throttle():
    app = _FakeApp()
    app._config_generation = 1
    app._config_gen_last_check = 1000.0
    reloaded = config_sync.maybe_reload_configuration(
        app, force=True, min_interval=2.0, _now=1000.1,
        _read_generation=lambda: 2)
    assert reloaded is True
    assert app.load_calls == 1


def test_maybe_reload_db_error_keeps_config():
    app = _FakeApp()
    app._config_generation = 1
    app._config_gen_last_check = None

    def boom():
        raise RuntimeError("db down")

    # Ne lève pas ; conserve la config en mémoire.
    reloaded = config_sync.maybe_reload_configuration(
        app, _now=1000.0, _read_generation=boom)
    assert reloaded is False
    assert app.load_calls == 0
    assert app._config_generation == 1


def test_maybe_reload_loader_error_keeps_cache():
    app = _FakeApp()
    app._config_generation = 1
    app._config_gen_last_check = None

    def failing_loader(_app):
        raise RuntimeError("reload failed")

    reloaded = config_sync.maybe_reload_configuration(
        app, _now=1000.0, _read_generation=lambda: 2,
        _load_configuration=failing_loader)
    assert reloaded is False
    # La génération mémorisée ne doit PAS avancer si le rechargement a échoué.
    assert app._config_generation == 1


def test_maybe_reload_uses_app_loader_by_default():
    app = _FakeApp()
    app._config_generation = None      # jamais chargé -> should_reload True
    app._config_gen_last_check = None
    reloaded = config_sync.maybe_reload_configuration(
        app, _now=1000.0, _read_generation=lambda: 3)
    assert reloaded is True
    assert app.load_calls == 1          # a bien appelé app.load_configuration
    assert app._config_generation == 3


def test_min_interval_read_from_app_config():
    app = _FakeApp()
    app.config["CONFIG_SYNC_MIN_INTERVAL"] = 10.0
    app._config_generation = 1
    app._config_gen_last_check = 1000.0
    # 5s plus tard : throttlé car min_interval=10 vient de app.config.
    reloaded = config_sync.maybe_reload_configuration(
        app, _now=1005.0, _read_generation=lambda: 2)
    assert reloaded is False


# ---------------------------------------------------------------------------
# 3a. Compteur de génération réel (SQLite en mémoire)
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_config():
    """Petite app Flask + SQLAlchemy avec un modèle ConfigOption minimal."""
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db = SQLAlchemy(app)

    class ConfigOption(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        config_key = db.Column(db.String(50), unique=True, nullable=False)
        value_int = db.Column(db.Integer)

    with app.app_context():
        db.create_all()
        yield db, ConfigOption


def test_bump_generation_creates_then_increments(sqlite_config):
    db, ConfigOption = sqlite_config
    # Ligne absente au départ.
    assert config_sync.read_generation(ConfigOption) == 0

    # Premier bump : crée la ligne à 1.
    config_sync.bump_generation(db=db, ConfigOption=ConfigOption)
    db.session.commit()
    assert config_sync.read_generation(ConfigOption) == 1

    # Bumps suivants : incrément SQL atomique.
    config_sync.bump_generation(db=db, ConfigOption=ConfigOption)
    config_sync.bump_generation(db=db, ConfigOption=ConfigOption)
    db.session.commit()
    assert config_sync.read_generation(ConfigOption) == 3


def test_ensure_generation_row_idempotent(sqlite_config):
    db, ConfigOption = sqlite_config
    config_sync.ensure_generation_row(db=db, ConfigOption=ConfigOption)
    assert config_sync.read_generation(ConfigOption) == 0
    # Deux appels ne créent pas de doublon (contrainte d'unicité respectée).
    config_sync.ensure_generation_row(db=db, ConfigOption=ConfigOption)
    rows = ConfigOption.query.filter_by(
        config_key=config_sync.CONFIG_GENERATION_KEY).all()
    assert len(rows) == 1


def test_bump_after_ensure_starts_at_one(sqlite_config):
    db, ConfigOption = sqlite_config
    config_sync.ensure_generation_row(db=db, ConfigOption=ConfigOption)
    config_sync.bump_generation(db=db, ConfigOption=ConfigOption)
    db.session.commit()
    assert config_sync.read_generation(ConfigOption) == 1


def test_read_generation_ignores_null(sqlite_config):
    db, ConfigOption = sqlite_config
    db.session.add(ConfigOption(
        config_key=config_sync.CONFIG_GENERATION_KEY, value_int=None))
    db.session.commit()
    assert config_sync.read_generation(ConfigOption) == 0


# ---------------------------------------------------------------------------
# 3b. Régression statique sur les sources
# ---------------------------------------------------------------------------

def _route_body(source, route):
    m = re.search(r"def " + route + r"\(.*?\n(.*?)(?=\ndef |\n@app\.route)",
                  source, re.DOTALL)
    assert m, f"fonction {route} introuvable"
    return m.group(1)


def test_update_routes_bump_generation_before_commit():
    """Chaque route update_* doit incrémenter la génération DANS la transaction
    (avant le commit) pour la convergence inter-processus."""
    source = _read("app.py")
    for route in ("update_switch", "update_input", "update_select"):
        body = _route_body(source, route)
        bump_idx = body.find("config_sync.bump_generation()")
        commit_idx = body.find("db.session.commit()")
        assert bump_idx != -1, f"{route} doit appeler config_sync.bump_generation()"
        assert commit_idx != -1, f"{route}: commit introuvable"
        assert bump_idx < commit_idx, (
            f"{route} doit incrémenter la génération AVANT le commit")


def test_update_routes_guard_restart_required():
    """Les routes update_* ne doivent pas prétendre appliquer un paramètre
    nécessitant un redémarrage : message dédié + pas de bump pour ces clés."""
    source = _read("app.py")
    for route in ("update_switch", "update_input", "update_select"):
        body = _route_body(source, route)
        assert "spec.restart_required" in body, (
            f"{route} doit tester spec.restart_required")
        assert "RESTART_REQUIRED_MESSAGE" in body, (
            f"{route} doit renvoyer le message « redémarrage requis »")


def test_before_request_hook_registered():
    source = _read("app.py")
    assert "@app.before_request" in source
    assert "maybe_reload_configuration(app)" in source


def test_start_fonctions_marks_generation():
    source = _read("app.py")
    assert "mark_current_generation(app)" in source
    assert "ensure_generation_row()" in source


def test_scheduler_jobs_refresh_config():
    """Le processus scheduler ne reçoit pas de requêtes : chaque tâche doit
    resynchroniser la configuration explicitement."""
    source = _read("scheduler_functions.py")
    assert "def _refresh_config(app)" in source
    assert "config_sync.maybe_reload_configuration(app, force=True)" in source
    # Chaque wrapper de job doit appeler _refresh_config après app_context.
    for job in ("clear_all_patients_job", "clear_announce_calls_job",
                "auto_archive_job", "disable_buttons_for_activity_job",
                "enable_buttons_for_activity_job"):
        m = re.search(r"def " + job + r"\(.*?\n(.*?)(?=\ndef )",
                      source, re.DOTALL)
        assert m, f"job {job} introuvable"
        assert "_refresh_config(app)" in m.group(1), (
            f"{job} doit appeler _refresh_config(app)")


def test_backup_restore_bumps_generation():
    """Une restauration de sauvegarde modifie la config -> doit propager."""
    source = _read("backup_service.py")
    assert source.count("config_sync.bump_generation()") >= 2
    assert "config_sync.is_reserved_key" in source
