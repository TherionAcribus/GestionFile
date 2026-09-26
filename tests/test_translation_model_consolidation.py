"""Point 9 — consolidation sur un seul modèle de traduction.

Trois tables coexistaient avec ``Translation`` sans jamais être lues au
rendu : ``Text``/``TextTranslation`` (alimentées par ``default_texts.json``
et ``default_translations.json`` au démarrage, sauvegardées/restaurées) et
``TextInterface``. Elles suggéraient un second système de traduction —
seedé avec des données de test obsolètes (« HHHHH », clés
``patient_page_*`` qui ne correspondent plus aux clés actuelles) — pendant
que le rendu n'utilise que ``Translation`` + le catalogue collecté.

Verrouillé ici :

1. les modèles et leurs fonctions d'initialisation n'existent plus ;
2. les sections de sauvegarde correspondantes ne sont plus exportées ni
   proposées dans l'UI ;
3. une **ancienne** sauvegarde contenant ``texts``/``text_interface``
   reste restaurable sans erreur — les sections mortes sont ignorées ;
4. les tables sont supprimées par une migration (pas un create_all
   silencieux).
"""

import os

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from models import db, Translation

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _source(*parts):
    with open(os.path.join(_SERVEUR, *parts), encoding="utf-8") as handle:
        return handle.read()


# --- Modèle unique ------------------------------------------------------------

def test_modeles_historiques_supprimes():
    import models
    for nom in ("Text", "TextTranslation", "TextInterface"):
        assert not hasattr(models, nom), f"{nom} existe encore dans models.py"


def test_plus_de_seed_des_tables_mortes():
    import init_restore
    for fonction in ("init_or_update_default_texts_db_from_json",
                     "init_update_default_translations_db_from_json"):
        assert not hasattr(init_restore, fonction)
    app_source = _source("app.py")
    assert "init_or_update_default_texts_db_from_json" not in app_source
    assert "init_update_default_translations_db_from_json" not in app_source


def test_json_de_seed_supprimes():
    """Les fichiers contenaient des données de test obsolètes — ils ne
    doivent pas subsister comme fausse source de vérité."""
    for nom in ("default_texts.json", "default_translations.json"):
        assert not os.path.exists(
            os.path.join(_SERVEUR, "static", "json", nom))


# --- Sauvegardes ---------------------------------------------------------------

def test_sections_mortes_hors_export():
    import backup_service
    assert "translations" in backup_service.BACKUP_SECTIONS
    for cle in ("texts", "text_interface"):
        assert cle not in backup_service.BACKUP_SECTIONS
        assert not any(cle in groupe
                       for groupe in backup_service.SECTION_GROUPS.values())


def test_ui_n_exporte_plus_les_sections_mortes():
    html = _source("templates", "admin", "app_backups.html")
    assert 'value="translations"' in html
    assert 'value="texts"' not in html
    assert 'value="text_interface"' not in html


@pytest.fixture
def application():
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
                      TESTING=True)
    db.init_app(app)
    with app.app_context():
        db.create_all(bind_key=None)
        yield app
        db.drop_all(bind_key=None)


def test_ancienne_sauvegarde_restaurable_sans_erreur(application):
    """Un backup produit avant la suppression contient encore 'texts' et
    'text_interface' : ils sont ignorés, pas signalés en erreur — sinon
    chaque restauration complète d'une vieille archive échouerait."""
    import backup_service
    backup = {
        "app": backup_service.APP_NAME,
        "format_version": "2.0",
        "sections": ["texts", "text_interface", "translations"],
        "data": {
            "texts": [{"text_key": "k", "text_value": "v",
                       "translations": []}],
            "text_interface": [{"text_id": "x", "value": "y"}],
            "translations": [{
                "table_name": "Button", "column_name": "label",
                "key_name": "", "row_id": 7, "language_code": "en",
                "translated_text": "Prescription",
            }],
        },
    }
    with application.app_context():
        report = backup_service.restore_sections(backup)
    assert report["success"] is True
    assert report["errors"] == []
    assert sorted(report["ignored"]) == ["text_interface", "texts"]
    assert report["restored"] == ["translations"]
    with application.app_context():
        assert Translation.query.filter_by(
            language_code="en").one().translated_text == "Prescription"


def test_migration_de_suppression_en_chainee():
    """La suppression passe par alembic, chaînée sur la migration
    d'élargissement de language.code."""
    import re
    migration = os.path.join(
        _SERVEUR, "migrations", "versions",
        "a2b3c4d5e6f7_drop_dead_translation_tables.py")
    source = _source("migrations", "versions",
                     "a2b3c4d5e6f7_drop_dead_translation_tables.py")
    assert os.path.exists(migration)
    assert re.search(r"down_revision\s*=\s*'f1e2d3c4b5a6'", source)
    for table in ("text", "text_translation", "text_interface"):
        assert f'"{table}"' in source
