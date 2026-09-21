"""Page « activité inactive » en langue étrangère.

Régression : ``display_activity_inactive`` ne définissait ``message`` que
dans la branche française — pour toute autre langue, le rendu levait un
``NameError`` et le patient voyait une erreur 500.

Verrouillé ici :

1. langue étrangère + message d'inactivité traduit -> la traduction est
   affichée ;
2. langue étrangère + message d'inactivité SANS traduction -> repli sur le
   texte français de l'activité ;
3. langue étrangère + pas de message d'activité -> message par défaut
   (traduit si la clé existe, sinon la valeur de configuration) ;
4. français -> comportement inchangé.
"""

import os

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from models import db, Activity, Language, Translation

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


@pytest.fixture
def application():
    app = Flask(
        __name__,
        template_folder=os.path.join(_SERVEUR, "templates"),
    )
    app.config.update(
        SECRET_KEY="secret-test-activity-inactive",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        # Pas de SQLALCHEMY_BINDS : db.metadatas est partagé entre fichiers de
        # test ; déclarer 'users' ici (ce fichier s'exécute tôt dans la suite)
        # casserait le create_all() des fichiers suivants sans ce bind.
        TESTING=True,
        PAGE_PATIENT_SUBTITLE="Sous-titre FR",
        PAGE_PATIENT_DISABLE_DEFAULT_MESSAGE="Activité indisponible",
        PAGE_PATIENT_TIMER_ACTIVITY_INACTIVE=10,
    )
    db.init_app(app)
    from routes.patient import patient_bp
    app.register_blueprint(patient_bp)
    with app.app_context():
        db.create_all()
        yield app


@pytest.fixture
def client(application):
    return application.test_client()


def _activite(application, inactivity_message="Fermé pour aujourd'hui"):
    """Crée une activité inactive avec son message, renvoie son id."""
    with application.app_context():
        db.session.add(Language(code="fr", name="Français", translation="Français"))
        activite = Activity(name="Guichet", letter="G", notification=False,
                            specific_message="", inactivity_message=inactivity_message)
        db.session.add(activite)
        db.session.commit()
        return activite.id


def _post_inactive(client, activite_id, language_code=None):
    if language_code is not None:
        with client.session_transaction() as sess:
            sess["language_code"] = language_code
    return client.post("/patients_submit", data={
        "is_active": "False",
        "activity_id": str(activite_id),
    })


def test_inactive_langue_etrangere_message_traduit(client, application):
    """EN + traduction du message d'inactivité -> texte traduit affiché."""
    activite_id = _activite(application)
    with application.app_context():
        db.session.add(Translation(
            table_name="Activity", column_name="inactivity_message",
            row_id=activite_id, language_code="en",
            translated_text="Closed for today"))
        db.session.commit()

    reponse = _post_inactive(client, activite_id, "en")

    html = reponse.get_data(as_text=True)
    assert reponse.status_code == 200
    assert "Closed for today" in html


def test_inactive_langue_etrangere_sans_traduction(client, application):
    """EN + message d'activité non traduit -> repli sur le texte français."""
    activite_id = _activite(application)

    reponse = _post_inactive(client, activite_id, "en")

    html = reponse.get_data(as_text=True)
    assert reponse.status_code == 200
    assert "Fermé pour aujourd&#39;hui" in html or "Fermé pour aujourd'hui" in html


def test_inactive_langue_etrangere_sans_message_activite(client, application):
    """EN + pas de message d'activité -> message par défaut traduit
    (clé ``page_patient_disable_default_message``)."""
    activite_id = _activite(application, inactivity_message="")
    with application.app_context():
        db.session.add(Translation(
            table_name="ConfigOption", column_name="",
            key_name="page_patient_disable_default_message",
            row_id=0, language_code="en",
            translated_text="This service is unavailable"))
        db.session.commit()

    reponse = _post_inactive(client, activite_id, "en")

    html = reponse.get_data(as_text=True)
    assert reponse.status_code == 200
    assert "This service is unavailable" in html


def test_inactive_langue_etrangere_repli_config(client, application):
    """EN + rien de traduit du tout -> repli sur la valeur de configuration
    (plus d'erreur 500)."""
    activite_id = _activite(application, inactivity_message="")

    reponse = _post_inactive(client, activite_id, "en")

    html = reponse.get_data(as_text=True)
    assert reponse.status_code == 200
    assert "Activité indisponible" in html


def test_inactive_francais_inchange(client, application):
    """FR : le message d'activité prime sur le message par défaut."""
    activite_id = _activite(application)

    reponse = _post_inactive(client, activite_id, "fr")

    html = reponse.get_data(as_text=True)
    assert reponse.status_code == 200
    assert "Fermé pour aujourd&#39;hui" in html or "Fermé pour aujourd'hui" in html


def test_inactive_francais_sans_session(client, application):
    """Pas de session (défaut fr) : idem, pas d'erreur."""
    activite_id = _activite(application)

    reponse = _post_inactive(client, activite_id)

    assert reponse.status_code == 200
