"""Point 6 — cycle de vie des codes de langue.

``Translation.language_code`` est une chaîne sans clé étrangère vers
``Language`` : renommer un code laissait toutes les traductions attachées
à l'ancien code (introuvables), et supprimer une langue les laissait
orphelines — voire plantait le moteur d'annonce sur les patients encore
en file (``patient.language`` à None).

Verrouillé ici :

1. le code est normalisé et validé (``fr``, ``en``, ``pt-br``…) — un code
   invalide est refusé à l'ajout comme à la modification ;
2. 'fr' est la langue de référence : ni renommée, ni désactivée, ni
   supprimée ;
3. renommer un code rebascule les traductions dans la même transaction ;
4. supprimer une langue purge ses traductions, et est refusée tant que
   des patients en file la référencent (FK ``patient.language_id``) ;
5. un code de langue reçu dans une URL publique (borne, téléphone) qui ne
   correspond à aucune langue active retombe sur 'fr' au lieu d'être
   stocké tel quel en session.
"""

import os
import re

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask
from flask_login import LoginManager

from models import (
    Activity, Language, Patient, Role, Translation, User, db,
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
        SECRET_KEY="secret-test-language-lifecycle",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        TESTING=True,
        PAGE_PATIENT_DISPLAY_TRANSLATIONS=False,
        PHONE_TITLE="Titre téléphone",
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
    # Les chemins de rejet renvoient la table des langues : elle dépend du
    # reste de l'administration — on la neutralise pour tester la décision.
    monkeypatch.setattr(
        "routes.admin_translation.display_languages_table", lambda: "")
    test_client = app.test_client()
    with test_client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return test_client


@pytest.fixture
def toasts(monkeypatch):
    captures = []
    monkeypatch.setattr(
        "routes.admin_translation.display_toast",
        lambda **kwargs: captures.append(kwargs),
    )
    return captures


def _langues(application):
    """La référence 'fr' et une cible 'en'."""
    app, _ = application
    with app.app_context():
        francais = Language(code="fr", name="Français", translation="Français")
        anglais = Language(code="en", name="Anglais", translation="English")
        db.session.add_all([francais, anglais])
        db.session.commit()
        return {"fr": francais.id, "en": anglais.id}


def _update(client, language_id, **form):
    return client.post(
        f"/admin/languages/language_update/{language_id}", data=form)


# --- Format et normalisation du code ----------------------------------------

def test_colonne_code_elargie_a_5_caracteres():
    """Alignement avec Translation.language_code : un code régionalisé
    ('pt-br') doit tenir — String(2) le rendait impossible en MySQL."""
    assert Language.__table__.c.code.type.length == 5
    migration = os.path.join(
        _SERVEUR, "migrations", "versions",
        "f1e2d3c4b5a6_widen_language_code.py")
    assert os.path.exists(migration), "migration d'élargissement absente"


def test_code_invalide_refuse_a_l_ajout(client, application, toasts):
    _langues(application)
    for code in ("", "e", "e1", "english", "en!", "francais"):
        client.post("/admin/languages/add_new_language", data={
            "code": code, "name": "X", "translation": "X"})
        assert toasts[-1]["success"] is False, code
    with application[0].app_context():
        assert Language.query.count() == 2


def test_code_normalise_a_l_ajout(client, application, toasts):
    """' PT-BR ' est normalisé en 'pt-br' : sans normalisation, la saisie
    créait un code que les traductions (minuscules) ne retrouvaient pas."""
    _langues(application)
    response = client.post("/admin/languages/add_new_language", data={
        "code": " PT-BR ", "name": "Portugais", "translation": "Português"})
    assert response.status_code == 200
    assert toasts[-1]["success"] is True
    with application[0].app_context():
        assert Language.query.filter_by(code="pt-br").count() == 1


def test_code_invalide_refuse_a_la_modification(client, application, toasts):
    ids = _langues(application)
    _update(client, ids["en"], code="e1", name="Anglais",
            translation="English", is_active="true")
    assert toasts[-1]["success"] is False
    with application[0].app_context():
        assert db.session.get(Language, ids["en"]).code == "en"


# --- Protection de la langue de référence ------------------------------------

def test_renommage_reference_refuse(client, application, toasts):
    ids = _langues(application)
    _update(client, ids["fr"], code="es", name="Français",
            translation="Français", is_active="true")
    assert toasts[-1]["success"] is False
    with application[0].app_context():
        assert db.session.get(Language, ids["fr"]).code == "fr"


def test_desactivation_reference_refusee(client, application, toasts):
    """La borne et les replis partent de 'fr' : la désactiver casserait le
    sélecteur et la langue par défaut."""
    ids = _langues(application)
    _update(client, ids["fr"], code="fr", name="Français",
            translation="Français")  # pas de is_active -> False
    assert toasts[-1]["success"] is False
    with application[0].app_context():
        assert db.session.get(Language, ids["fr"]).is_active is True


def test_mise_a_jour_reference_possible_sans_le_casser(
        client, application, toasts):
    ids = _langues(application)
    _update(client, ids["fr"], code="fr", name="French",
            translation="French", is_active="true")
    assert toasts[-1]["success"] is True
    with application[0].app_context():
        assert db.session.get(Language, ids["fr"]).name == "French"


def test_suppression_reference_refusee(client, application, toasts):
    ids = _langues(application)
    client.delete(f"/admin/languages/delete/{ids['fr']}")
    assert toasts[-1]["success"] is False
    with application[0].app_context():
        assert Language.query.filter_by(code="fr").count() == 1


# --- Renommage : rebasculement des traductions --------------------------------

def test_renommage_rebascule_les_traductions(client, application, toasts):
    """Régression : 'en' renommé en 'pt' laissait les traductions sur 'en' —
    introuvables alors qu'elles étaient saisies."""
    ids = _langues(application)
    app, _ = application
    with app.app_context():
        db.session.add_all([
            Translation(table_name="Button", column_name="label", row_id=1,
                        language_code="en", translated_text="Prescription"),
            Translation(table_name="Button", column_name="label", row_id=1,
                        language_code="fr", translated_text="Ordonnance"),
        ])
        db.session.commit()
    _update(client, ids["en"], code="pt", name="Portugais",
            translation="Português", is_active="true")
    assert toasts[-1]["success"] is True
    with app.app_context():
        assert db.session.get(Language, ids["en"]).code == "pt"
        assert Translation.query.filter_by(language_code="en").count() == 0
        assert Translation.query.filter_by(
            language_code="pt").one().translated_text == "Prescription"
        # La référence française est intouchée.
        assert Translation.query.filter_by(
            language_code="fr").one().translated_text == "Ordonnance"


# --- Suppression : orphelins et patients en file ------------------------------

def test_suppression_purge_les_traductions(client, application, toasts):
    """Sans purge, les traductions du code supprimé restaient orphelines."""
    ids = _langues(application)
    app, _ = application
    with app.app_context():
        db.session.add(Translation(
            table_name="Button", column_name="label", row_id=1,
            language_code="en", translated_text="Prescription"))
        db.session.commit()
    client.delete(f"/admin/languages/delete/{ids['en']}")
    assert toasts[-1]["success"] is True
    with app.app_context():
        assert db.session.get(Language, ids["en"]) is None
        assert Translation.query.filter_by(language_code="en").count() == 0


def test_suppression_refusee_si_patients_en_file(client, application, toasts):
    """Un patient 'calling' dont la langue disparaît plantait le moteur
    d'annonce (patient.language -> None)."""
    ids = _langues(application)
    app, _ = application
    with app.app_context():
        activite = Activity(name="Ordonnance", letter="O")
        db.session.add(activite)
        db.session.commit()
        db.session.add(Patient(call_number=1, status="calling",
                               activity_id=activite.id,
                               language_id=ids["en"]))
        db.session.commit()
    client.delete(f"/admin/languages/delete/{ids['en']}")
    assert toasts[-1]["success"] is False
    with app.app_context():
        assert db.session.get(Language, ids["en"]) is not None


def test_suppression_possible_apres_depart_des_patients(
        client, application, toasts):
    """Le blocage vise la file active : une fois les patients partis, la
    langue se supprime normalement."""
    ids = _langues(application)
    app, _ = application
    with app.app_context():
        activite = Activity(name="Ordonnance", letter="O")
        db.session.add(activite)
        db.session.commit()
        patient = Patient(call_number=1, status="calling",
                          activity_id=activite.id, language_id=ids["en"])
        db.session.add(patient)
        db.session.commit()
        db.session.delete(patient)
        db.session.commit()
    client.delete(f"/admin/languages/delete/{ids['en']}")
    assert toasts[-1]["success"] is True


# --- Codes reçus dans les URL publiques ----------------------------------------

@pytest.fixture
def patient_app(application, monkeypatch):
    app, _ = application
    from routes.patient import patient_bp
    app.register_blueprint(patient_bp)
    # Le rendu complet dépend de toute l'application ; ce qui est testé ici
    # est la langue posée en session, pas le gabarit.
    monkeypatch.setattr(
        "routes.patient.render_template", lambda *a, **k: "")
    monkeypatch.setattr(
        "routes.patient.page_editor_published_revision", lambda page: None)
    monkeypatch.setattr(
        "routes.patient.get_text_translation",
        lambda key, code: {"translation": key})
    return app


def test_borne_code_inconnu_retombe_sur_fr(client, patient_app):
    response = client.get("/patient?language_code=zz")
    assert response.status_code == 200
    with client.session_transaction() as session:
        assert session["language_code"] == "fr"


def test_borne_langue_inactive_retombe_sur_fr(client, patient_app):
    """Une langue désactivée n'est plus proposée par les drapeaux : l'URL
    ne doit pas la réactiver en session."""
    with patient_app.app_context():
        espagnol = Language(code="es", name="Espagnol",
                            translation="Español", is_active=False)
        db.session.add(espagnol)
        db.session.commit()
    client.get("/patient?language_code=es")
    with client.session_transaction() as session:
        assert session["language_code"] == "fr"


def test_borne_langue_active_acceptee(client, patient_app):
    with patient_app.app_context():
        db.session.add(Language(code="en", name="Anglais",
                                translation="English", is_active=True))
        db.session.commit()
    client.get("/patient?language_code=en")
    with client.session_transaction() as session:
        assert session["language_code"] == "en"


def test_telephone_code_inconnu_retombe_sur_fr(client, patient_app):
    """Même repli sur l'URL du QR : '/patient/phone/zz/…' posait 'zz' en
    session."""
    client.get("/patient/phone/zz/1/1")
    with client.session_transaction() as session:
        assert session["language_code"] == "fr"


def test_telephone_langue_active_acceptee(client, patient_app):
    with patient_app.app_context():
        db.session.add(Language(code="en", name="Anglais",
                                translation="English", is_active=True))
        db.session.commit()
    client.get("/patient/phone/en/1/1")
    with client.session_transaction() as session:
        assert session["language_code"] == "en"
