"""Point 5 — validation de la sauvegarde des traductions.

La route ``/admin/translations/save_translations`` faisait confiance aux
identifiants portés par les noms de champs : une langue inexistante, une table
ou une ligne source inconnue étaient enregistrées avec un HTTP 200 — créant
des traductions orphelines jamais affichées. Un champ mal formé levait un
``ValueError`` (500).

Verrouillé ici :

1. la langue cible doit exister et ne pas être la référence 'fr' ;
2. la source désignée (table, colonne, ligne, clé) doit être réellement
   traduisible : bouton/activité existants, clé de configuration déclarée
   traduisible dans le registre avec la colonne attendue ;
3. un champ mal formé est refusé proprement (toast + audit d'échec, pas 500) ;
4. la sauvegarde reste atomique : un seul champ invalide annule tout.
"""

import os

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
        SECRET_KEY="secret-test-translation-save",
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
def client(application):
    app, user_id = application
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


def _jeu(application):
    """Une langue cible 'en', un bouton, une activité et une clé traduisible."""
    app, _ = application
    with app.app_context():
        anglais = Language(code="en", name="Anglais", translation="English")
        francais = Language(code="fr", name="Français", translation="Français")
        activite = Activity(name="Ordonnance", letter="O",
                            specific_message="Message spécifique")
        bouton = Button(label="Ordonnance", activity=activite)
        option = ConfigOption(config_key="page_patient_title",
                              value_str="Bienvenue")
        db.session.add_all([anglais, francais, activite, bouton, option])
        db.session.commit()
        return {"activity": activite.id, "button": bouton.id,
                "option": option.id}


def _post(client, **form):
    return client.post("/admin/translations/save_translations", data=form)


# --- Sauvegardes valides ----------------------------------------------------

def test_sauvegarde_bouton_valide(client, application, toasts):
    ids = _jeu(application)
    response = _post(client, language_code="en", **{
        f"translation|Button|label|{ids['button']}|": "Prescription",
    })
    assert response.status_code == 200
    assert toasts[-1]["success"] is True
    with application[0].app_context():
        assert Translation.query.filter_by(
            table_name="Button", row_id=ids["button"],
            language_code="en").one().translated_text == "Prescription"


def test_sauvegarde_activite_et_configoption_valides(client, application, toasts):
    ids = _jeu(application)
    response = _post(client, language_code="en", **{
        f"translation|Activity|specific_message|{ids['activity']}|": "Specific message",
        f"translation|ConfigOption|value_str|{ids['option']}|page_patient_title": "Welcome",
    })
    assert response.status_code == 200
    assert toasts[-1]["success"] is True
    with application[0].app_context():
        assert Translation.query.filter_by(
            table_name="Activity", row_id=ids["activity"],
            language_code="en").one().translated_text == "Specific message"
        assert Translation.query.filter_by(
            table_name="ConfigOption", key_name="page_patient_title",
            language_code="en").one().translated_text == "Welcome"


# --- Langue cible -----------------------------------------------------------

def test_langue_inconnue_refusee(client, application, toasts):
    """Régression : une langue inexistante était enregistrée avec un 200."""
    ids = _jeu(application)
    response = _post(client, language_code="zz", **{
        f"translation|Button|label|{ids['button']}|": "X",
    })
    assert response.status_code == 200
    assert toasts[-1]["success"] is False
    with application[0].app_context():
        assert Translation.query.count() == 0


def test_langue_de_reference_refusee(client, application, toasts):
    """'fr' est synchronisée par la collecte, jamais éditée à la main : la
    soumettre contournerait le sélecteur et écrirait des références
    divergentes de la source."""
    ids = _jeu(application)
    response = _post(client, language_code="fr", **{
        f"translation|Button|label|{ids['button']}|": "Autre libellé",
    })
    assert toasts[-1]["success"] is False
    with application[0].app_context():
        assert Translation.query.count() == 0


# --- Source désignée --------------------------------------------------------

def test_table_inconnue_refusee(client, application, toasts):
    _jeu(application)
    _post(client, language_code="en", **{
        "translation|Patient|name|1|": "X",
    })
    assert toasts[-1]["success"] is False
    with application[0].app_context():
        assert Translation.query.count() == 0


def test_ligne_source_inexistante_refusee(client, application, toasts):
    _jeu(application)
    _post(client, language_code="en", **{
        "translation|Button|label|9999|": "X",
    })
    assert toasts[-1]["success"] is False
    with application[0].app_context():
        assert Translation.query.count() == 0


def test_colonne_non_traduisible_refusee(client, application, toasts):
    ids = _jeu(application)
    _post(client, language_code="en", **{
        f"translation|Activity|name|{ids['activity']}|": "X",
    })
    assert toasts[-1]["success"] is False


def test_cle_configoption_non_traduisible_refusee(client, application, toasts):
    """Une clé de configuration absente du catalogue n'est pas traduisible —
    même si la ligne existe en base."""
    app, _ = application
    _jeu(application)
    with app.app_context():
        option = ConfigOption(config_key="pharmacy_name",
                              value_str="Pharmacie")
        db.session.add(option)
        db.session.commit()
        option_id = option.id
    _post(client, language_code="en", **{
        f"translation|ConfigOption|value_str|{option_id}|pharmacy_name": "Pharmacy",
    })
    assert toasts[-1]["success"] is False
    with app.app_context():
        assert Translation.query.count() == 0


def test_colonne_configoption_incoherente_refusee(client, application, toasts):
    """La colonne doit être celle déclarée par le registre — une traduction
    sur 'value_text' pour une clé 'value_str' ne serait jamais lue."""
    ids = _jeu(application)
    _post(client, language_code="en", **{
        f"translation|ConfigOption|value_text|{ids['option']}|page_patient_title": "Welcome",
    })
    assert toasts[-1]["success"] is False


def test_row_id_et_cle_configoption_doves_correspondre(client, application, toasts):
    """row_id doit pointer sur la ConfigOption de cette clé : sinon la
    référence française synchronisée par la collecte divergerait de la
    traduction."""
    ids = _jeu(application)
    app, _ = application
    with app.app_context():
        autre = ConfigOption(config_key="pharmacy_name", value_str="Pharmacie")
        db.session.add(autre)
        db.session.commit()
        autre_id = autre.id
    _post(client, language_code="en", **{
        f"translation|ConfigOption|value_str|{autre_id}|page_patient_title": "Welcome",
    })
    assert toasts[-1]["success"] is False
    with app.app_context():
        assert Translation.query.count() == 0


# --- Champs mal formés et atomicité ------------------------------------------

def test_champ_mal_forme_refuse_sans_500(client, application, toasts):
    """Régression : 'translation|Button|label' levait ValueError (500)."""
    _jeu(application)
    response = _post(client, language_code="en", **{
        "translation|Button|label": "X",
    })
    assert response.status_code == 200
    assert toasts[-1]["success"] is False


def test_row_id_non_entier_refuse_sans_500(client, application, toasts):
    _jeu(application)
    response = _post(client, language_code="en", **{
        "translation|Button|label|abc|": "X",
    })
    assert response.status_code == 200
    assert toasts[-1]["success"] is False


def test_soumission_invalide_annule_tout(client, application, toasts):
    """Atomicité : un champ forgé dans un lot annule les champs valides —
    le commit est unique, en fin de route."""
    ids = _jeu(application)
    _post(client, language_code="en", **{
        f"translation|Button|label|{ids['button']}|": "Prescription",
        "translation|Patient|name|1|": "forgé",
    })
    assert toasts[-1]["success"] is False
    with application[0].app_context():
        assert Translation.query.count() == 0


def test_balise_invalide_toujours_refusee(client, application, toasts):
    """La validation métier existante est conservée : une balise inconnue
    dans un texte traduisible est rejetée."""
    ids = _jeu(application)
    _post(client, language_code="en", **{
        f"translation|ConfigOption|value_str|{ids['option']}|page_patient_title": "{Z} hello",
    })
    assert toasts[-1]["success"] is False
    with application[0].app_context():
        assert Translation.query.count() == 0
