"""Tests du point 12 — chargement des paramètres serveur en une seule requête.

Exécutables **sans MySQL ni serveur** : on reconstruit un modèle ``ConfigOption``
minimal sur SQLite en mémoire, on compte les requêtes SQL réellement émises et on
vérifie que ``config_loader.load_config_options`` :

1. n'émet **qu'une seule** requête quel que soit le nombre de clés ;
2. renvoie les valeurs typées selon ``config_mappings`` ;
3. ignore les clés absentes de la base (les défauts restent intacts).
"""

import os

import pytest
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event

import config_loader
import params_registry as reg


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


@pytest.fixture
def sqlite_config():
    """App Flask + SQLAlchemy avec un ConfigOption calqué sur le vrai modèle."""
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db = SQLAlchemy(app)

    class ConfigOption(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        config_key = db.Column(db.String(50), unique=True, nullable=False)
        value_str = db.Column(db.String(200))
        value_int = db.Column(db.Integer)
        value_bool = db.Column(db.Boolean)
        value_text = db.Column(db.Text)
        value_json = db.Column(db.JSON)

    with app.app_context():
        db.create_all()
        yield db, ConfigOption


class _QueryCounter:
    """Compte les instructions SQL SELECT émises sur le moteur SQLAlchemy."""

    def __init__(self, engine):
        self._engine = engine
        self.selects = 0

    def _on_execute(self, conn, cursor, statement, *args, **kwargs):
        if statement.lstrip().upper().startswith("SELECT"):
            self.selects += 1

    def __enter__(self):
        event.listen(self._engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc):
        event.remove(self._engine, "before_cursor_execute", self._on_execute)
        return False


def _seed(db, ConfigOption, **by_key):
    for key, (column, value) in by_key.items():
        db.session.add(ConfigOption(config_key=key, **{column: value}))
    db.session.commit()


def test_single_query_regardless_of_key_count(sqlite_config):
    """Le chargement de N clés ne doit émettre qu'UNE requête SELECT."""
    db, ConfigOption = sqlite_config
    mappings = {
        "pharmacy_name": ("PHARMACY_NAME", "value_str"),
        "some_delay": ("SOME_DELAY", "value_int"),
        "some_flag": ("SOME_FLAG", "value_bool"),
    }
    _seed(
        db, ConfigOption,
        pharmacy_name=("value_str", "Officine du Centre"),
        some_delay=("value_int", 42),
        some_flag=("value_bool", True),
    )

    with _QueryCounter(db.engine) as counter:
        resolved = config_loader.load_config_options(ConfigOption, mappings)

    assert counter.selects == 1
    assert resolved == {
        "PHARMACY_NAME": "Officine du Centre",
        "SOME_DELAY": 42,
        "SOME_FLAG": True,
    }


def test_single_query_with_full_registry(sqlite_config):
    """Même avec le vrai CONFIG_MAPPINGS (~130 clés) : une seule requête."""
    db, ConfigOption = sqlite_config
    _seed(db, ConfigOption, pharmacy_name=("value_str", "Test"))

    with _QueryCounter(db.engine) as counter:
        resolved = config_loader.load_config_options(
            ConfigOption, reg.CONFIG_MAPPINGS)

    assert counter.selects == 1
    # Seule la clé présente en base ressort ; les autres sont ignorées.
    assert resolved == {"PHARMACY_NAME": "Test"}


def test_absent_keys_are_omitted(sqlite_config):
    """Une clé sans ligne en base ne figure pas dans le résultat."""
    db, ConfigOption = sqlite_config
    mappings = {
        "present": ("PRESENT", "value_str"),
        "absent": ("ABSENT", "value_str"),
    }
    _seed(db, ConfigOption, present=("value_str", "ok"))

    resolved = config_loader.load_config_options(ConfigOption, mappings)

    assert resolved == {"PRESENT": "ok"}
    assert "ABSENT" not in resolved


def test_empty_mappings_makes_no_query(sqlite_config):
    """Une table de mappings vide n'émet aucune requête."""
    db, ConfigOption = sqlite_config

    with _QueryCounter(db.engine) as counter:
        resolved = config_loader.load_config_options(ConfigOption, {})

    assert resolved == {}
    assert counter.selects == 0


def test_values_read_from_correct_column(sqlite_config):
    """Chaque valeur est lue dans la colonne indiquée par value_type."""
    db, ConfigOption = sqlite_config
    mappings = {
        "k_text": ("K_TEXT", "value_text"),
        "k_json": ("K_JSON", "value_json"),
    }
    _seed(
        db, ConfigOption,
        k_text=("value_text", "long texte…"),
        k_json=("value_json", {"a": 1, "b": [2, 3]}),
    )

    resolved = config_loader.load_config_options(ConfigOption, mappings)

    assert resolved["K_TEXT"] == "long texte…"
    assert resolved["K_JSON"] == {"a": 1, "b": [2, 3]}
