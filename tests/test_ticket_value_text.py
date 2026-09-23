"""Textes de ticket : stockage en ``value_text`` (sans borne de 200).

Avant : ``ticket_header`` / ``ticket_message`` / ``ticket_footer`` étaient
déclarés ``value_str`` (String(200)) alors que les zones de saisie de
l'administration n'ont aucune limite :

* au-delà de 200 caractères : erreur ou troncature silencieuse selon le mode
  SQL de MySQL ;
* à la restauration : ``backup_service`` rangeait les chaînes >= 200 dans
  ``value_text`` alors que le registre rechargeait ces clés depuis
  ``value_str`` — le texte du ticket disparaissait.

Désormais les trois clés sont déclarées ``value_text`` : écriture
(``update_input``), chargement (``config_loader``) et restauration
(``backup_service`` / ``init_restore``) visent tous la colonne déclarée par le
registre via ``column_values_for``, et une migration alembic déplace les
données existantes.
"""

import os
import re

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

import config_loader
import params_registry as reg

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


_TICKET_KEYS = ("ticket_header", "ticket_message", "ticket_footer")


# ---------------------------------------------------------------------------
# 1. Registre : les trois parties du ticket vivent en value_text
# ---------------------------------------------------------------------------

def test_ticket_keys_declared_value_text():
    for key in _TICKET_KEYS:
        assert reg.get_spec(key).value_type == "value_text", key


# ---------------------------------------------------------------------------
# 2. column_values_for : la colonne déclarée, pas la longueur de la chaîne
# ---------------------------------------------------------------------------

def test_column_values_for_ticket_uses_value_text_whatever_the_length():
    """Court ou long, un texte de ticket va en value_text : ranger une chaîne
    courte en value_str la rendrait invisible au chargement."""
    for value in ("court", "x" * 500):
        cols = reg.column_values_for("ticket_message", value)
        assert cols["value_text"] == value
        assert cols["value_str"] is None
        assert cols["value_int"] is None
        assert cols["value_bool"] is None


def test_column_values_for_known_key_clears_other_columns():
    """Une écriture n'alimente qu'une seule colonne : pas de source fantôme."""
    cols = reg.column_values_for("pharmacy_name", "Officine")
    assert cols == {"value_str": "Officine", "value_int": None,
                    "value_bool": None, "value_text": None,
                    "value_json": None}


def test_column_values_for_unknown_key_keeps_heuristic():
    """Clé hors registre (vieille sauvegarde, clé retirée) : comportement
    historique conservé — le choix dépend alors du type/longueur."""
    assert reg.column_values_for("inconnue", "abc")["value_str"] == "abc"
    assert reg.column_values_for("inconnue", "x" * 300)["value_text"] == "x" * 300
    assert reg.column_values_for("inconnue", True)["value_bool"] is True
    assert reg.column_values_for("inconnue", 7)["value_int"] == 7
    assert reg.column_values_for("inconnue", {"a": 1})["value_json"] == {"a": 1}


# ---------------------------------------------------------------------------
# 3. Chaîne complète : écriture/restauration -> lecture chargeur
# ---------------------------------------------------------------------------

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


def test_chargeur_lit_le_ticket_en_value_text(sqlite_config):
    """Un texte de ticket > 200 caractères en value_text ressort du chargement."""
    db, ConfigOption = sqlite_config
    long_texte = "y" * 300
    db.session.add(ConfigOption(config_key="ticket_message",
                                value_text=long_texte))
    db.session.commit()

    resolved = config_loader.load_config_options(ConfigOption,
                                                 reg.CONFIG_MAPPINGS)

    assert resolved["TICKET_MESSAGE"] == long_texte


@pytest.fixture
def app_db():
    """App Flask câblée sur le VRAI ``models.db`` : restauration réelle."""
    from models import db

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="secret-test-ticket-value-text",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        TESTING=True,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app


def test_restauration_ecrit_et_recharge_le_texte_long(app_db):
    """Régression du bug de restauration : un texte de ticket restauré doit
    atterrir dans value_text ET ressortir du chargement (plus de perte —
    avant, >=200 car. partait en value_text non relu, <200 en value_str)."""
    from backup_service import ConfigSection
    from models import ConfigOption

    with app_db.app_context():
        ConfigSection().restore_data({
            "ticket_footer": "z" * 250,   # long : partait en value_text non relu
            "ticket_header": "Bienvenue",  # court : repartait en value_str non relue
        })

        for key, attendu in (("ticket_footer", "z" * 250),
                             ("ticket_header", "Bienvenue")):
            option = ConfigOption.query.filter_by(config_key=key).one()
            assert option.value_text == attendu
            assert option.value_str is None

        resolved = config_loader.load_config_options(
            ConfigOption, reg.CONFIG_MAPPINGS)
        assert resolved["TICKET_FOOTER"] == "z" * 250
        assert resolved["TICKET_HEADER"] == "Bienvenue"


# ---------------------------------------------------------------------------
# 4. Régressions statiques : écriture admin, restaurations, migration
# ---------------------------------------------------------------------------

def test_update_input_ecrit_la_colonne_du_registre():
    """La route ne doit plus écrire ``value_str`` en dur : elle suit
    ``spec.value_type`` (via column_values_for)."""
    source = _read("routes/admin_config.py")
    m = re.search(r"def update_input\(.*?\n(.*?)(?=\ndef |\n@admin_config_bp)",
                  source, re.DOTALL)
    assert m, "fonction update_input introuvable"
    body = m.group(1)
    assert "column_values_for" in body
    assert ".value_str = value" not in body


def test_restaurations_utilisent_la_colonne_du_registre():
    """Ni backup_service ni init_restore ne choisissent la colonne selon la
    longueur : tous deux passent par column_values_for."""
    for rel in ("backup_service.py", "init_restore.py"):
        source = _read(rel)
        assert "len(value) < 200" not in source, rel
        assert "column_values_for" in source, rel


def test_migration_deplace_les_textes_de_ticket():
    """La migration value_str -> value_text existe, chaînée sur la tête
    alembic, et couvre les trois clés."""
    import glob
    fichiers = glob.glob(os.path.join(
        _SERVEUR, "migrations", "versions", "*ticket_texts_value_text*.py"))
    assert len(fichiers) == 1, "migration introuvable"
    source = _read(os.path.relpath(fichiers[0], _SERVEUR))

    # Chaînage : la tête courante est a1b2c3d4e5f7 (rien d'autre ne la révise).
    assert "down_revision = 'a1b2c3d4e5f7'" in source
    for key in _TICKET_KEYS:
        assert key in source
    assert "value_text = value_str" in source  # UPDATE config_option
    # Et purge des clés dérivées ESC/POS, pour qu'elles ne traînent ni en base
    # ni dans les sauvegardes exportées.
    assert "DELETE FROM config_option" in source
    for key in _TICKET_KEYS:
        assert f"{key}_printer" in source
