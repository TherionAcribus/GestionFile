"""Point 7 — éditeur de traductions.

La liste des traductions utilisait des ``<input type="text">`` monolignes
pour des textes par nature multilignes (messages d'activité, lignes
téléphone, bannières d'annonce), sans libellé métier ni indication des
balises autorisées — et un reste de débogage ``{{translations_dict}}``
ouvrait le gabarit.

Verrouillé ici :

1. chaque traduction s'édite dans un ``<textarea>`` — les retours à la
   ligne survivent à l'aller-retour formulaire ;
2. chaque champ porte un libellé métier (« Bouton », « Activité « X » —
   message spécifique », clé de configuration) et les balises autorisées ;
3. une traduction absente est marquée (``data-missing`` + bordure) et
   filtrable via la case « manquantes » / la recherche, délégués dans
   ``admin_translations.js``.
"""

import os
import re

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask
from flask_login import LoginManager

from models import (
    Activity, Button, ConfigOption, Language, Role, Translation, User, db,
)
from routes.admin_translation import admin_translation_bp

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


@pytest.fixture
def application():
    app = Flask(
        __name__,
        template_folder=os.path.join(_SERVEUR, "templates"),
        static_folder=os.path.join(_SERVEUR, "static"),
    )
    app.config.update(
        SECRET_KEY="secret-test-translation-editor",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        TESTING=True,
    )
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    app.register_blueprint(admin_translation_bp)
    with app.app_context():
        db.create_all(bind_key=None)
        role = Role(name="traducteur", admin_translation=True)
        user = User(username="traducteur", password="unused", active=True)
        user.roles.append(role)
        db.session.add(user)
        db.session.commit()
        yield app, user.id

    with app.app_context():
        db.session.remove()
        db.drop_all(bind_key=None)


@pytest.fixture
def client(application, monkeypatch):
    app, user_id = application
    monkeypatch.setattr(
        "routes.admin_translation.display_toast", lambda **kwargs: None)
    test_client = app.test_client()
    with test_client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return test_client


def _jeu(application):
    """'en' cible + références françaises collectées : bouton, activité
    (message multiligne) et clé de configuration avec balises."""
    app, _ = application
    with app.app_context():
        francais = Language(code="fr", name="Français", translation="Français")
        anglais = Language(code="en", name="Anglais", translation="English")
        activite = Activity(name="Ordonnance", letter="O",
                            specific_message="Ligne 1\nLigne 2")
        bouton = Button(label="Ordonnance", activity=activite)
        option = ConfigOption(config_key="announce_call_text",
                              value_str="Patient {N} comptoir {C}")
        db.session.add_all([francais, anglais, activite, bouton, option])
        db.session.commit()
        for table, column, row_id, key_name, text in (
                ("Button", "label", bouton.id, "", "Ordonnance"),
                ("Activity", "specific_message", activite.id, "",
                 "Ligne 1\nLigne 2"),
                ("ConfigOption", "value_str", option.id,
                 "announce_call_text", "Patient {N} comptoir {C}")):
            db.session.add(Translation(
                table_name=table, column_name=column, row_id=row_id,
                key_name=key_name, language_code="fr", translated_text=text))
        db.session.commit()


def _liste(client):
    response = client.post("/admin/translations/change_language_target",
                           data={"language_code": "en"})
    assert response.status_code == 200
    return response.get_data(as_text=True)


# --- Champs multilignes -------------------------------------------------------

def test_champs_en_textarea(client, application):
    _jeu(application)
    html = _liste(client)
    assert "<textarea" in html
    # Plus de champ monoligne pour les textes traduits.
    assert not re.search(r'<input[^>]+name="translation\|', html)


def test_texte_multiligne_survit_a_la_sauvegarde(client, application):
    """Un textarea soumet les retours à la ligne : ils doivent être
    persistés, pas tronqués à la première ligne."""
    _jeu(application)
    app, _ = application
    with app.app_context():
        row_id = Translation.query.filter_by(
            table_name="Activity").one().row_id
    response = client.post("/admin/translations/save_translations", data={
        "language_code": "en",
        f"translation|Activity|specific_message|{row_id}|":
            "Line 1\nLine 2\nLine 3",
    })
    assert response.status_code == 200
    with app.app_context():
        saved = Translation.query.filter_by(
            table_name="Activity", language_code="en").one()
        assert saved.translated_text == "Line 1\nLine 2\nLine 3"


def test_texte_avec_guillemets_et_chevrons_echappe(client, application):
    """La valeur existante est échappée dans le textarea : elle ne peut
    casser le fragment HTML."""
    _jeu(application)
    app, _ = application
    with app.app_context():
        row_id = Translation.query.filter_by(
            table_name="Button").one().row_id
        db.session.add(Translation(
            table_name="Button", column_name="label", row_id=row_id,
            key_name="", language_code="en",
            translated_text='Say "hi" </textarea>'))
        db.session.commit()
    html = _liste(client)
    assert 'Say "hi" </textarea>' not in html
    assert "&lt;/textarea&gt;" in html


# --- Libellés et aide ---------------------------------------------------------

def test_libelles_metiers_affiches(client, application):
    """Le libellé dit CE qui est traduit : plus besoin de deviner à partir
    du seul texte français."""
    _jeu(application)
    html = _liste(client)
    assert "Bouton" in html
    assert "Activité « Ordonnance » — message spécifique" in html
    assert "announce_call_text" in html


def test_balises_autorisees_affichees(client, application):
    """announce_call_text est en famille « after_call » : {M} et {C} doivent
    être listés — une traduction hors balises serait rejetée."""
    _jeu(application)
    html = _liste(client)
    assert "Balises autorisées" in html
    assert "{C}" in html and "{M}" in html


# --- Marquage et filtres ------------------------------------------------------

def test_traduction_manquante_marquee(client, application):
    _jeu(application)
    app, _ = application
    with app.app_context():
        row_id = Translation.query.filter_by(
            table_name="Button").one().row_id
        db.session.add(Translation(
            table_name="Button", column_name="label", row_id=row_id,
            key_name="", language_code="en", translated_text="Prescription"))
        db.session.commit()
    html = _liste(client)
    # Le bouton est traduit, pas l'activité ni la clé : 2 items manquants.
    assert html.count("data-missing") == 2
    assert 'id="translation_only_missing"' in html
    assert 'id="translation_filter"' in html


def test_debug_translations_dict_supprime():
    """Régression : le gabarit commençait par un dump de debug
    ``{{translations_dict}}`` (variable inexistante)."""
    source = open(os.path.join(
        _SERVEUR, "templates", "admin",
        "translations_texts_list.html"), encoding="utf-8").read()
    assert "translations_dict" not in source


def test_filtres_delegues_dans_js_dedie():
    """La liste est réinjectée par HTMX : le filtrage est délégué sur
    document dans un fichier chargé par la page, pas dans un <script> du
    fragment (dont l'exécution après swap n'est pas garantie)."""
    js = open(os.path.join(
        _SERVEUR, "static", "js", "admin_translations.js"),
        encoding="utf-8").read()
    assert "data-missing" in js and "translation_only_missing" in js
    page = open(os.path.join(
        _SERVEUR, "templates", "admin", "translations.html"),
        encoding="utf-8").read()
    assert "admin_translations.js" in page
