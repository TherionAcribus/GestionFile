"""Activité sans équipier assigné : indisponible sur la borne.

Un patient d'une activité que personne ne peut appeler attendrait
indéfiniment — ``algo_choice_next_patient`` ne retient que les activités
des compétences du membre connecté. La borne applique donc la même
politique que « hors horaires » :

- bouton grisé par défaut (PAGE_PATIENT_DISABLE_BUTTON), cliquable : le
  message d'indisponibilité s'affiche ;
- bouton masqué quand l'option « griser les indisponibles » est off ;
- garde serveur : ``activity_accepting_registrations`` refuse l'inscription
  (écran périmé, QR scanné, POST direct) quoi qu'il arrive.
"""

import os

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from models import db, Activity, Button, Language, Pharmacist

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


@pytest.fixture
def application():
    app = Flask(
        __name__,
        template_folder=os.path.join(_SERVEUR, "templates"),
    )
    app.config.update(
        SECRET_KEY="secret-test-uncallable",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        TESTING=True,
        PAGE_PATIENT_SUBTITLE="Sous-titre",
        PAGE_PATIENT_INTERFACE_VALIDATE_CANCEL="Annuler",
        PAGE_PATIENT_INTERFACE_CHILDREN_BACK="Retour",
        PAGE_PATIENT_DISABLE_BUTTON=True,
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


def _setup(application, with_member=True, children=False):
    """Crée langue + activité + bouton ; ``with_member=False`` laisse
    l'activité sans équipier (non appelable)."""
    with application.app_context():
        db.session.add(Language(code="fr", name="Français", translation="Français"))
        activite = Activity(name="Ordonnance", letter="O")
        if with_member:
            db.session.add(Pharmacist(name="Marie", initials="MA",
                                      activities=[activite]))
        db.session.add(activite)
        db.session.flush()
        if children:
            parent = Button(label="Menu", is_parent=True, is_present=True,
                            shape="square", sort_order=1)
            db.session.add(parent)
            db.session.flush()
            db.session.add(Button(label="Ordonnance", is_present=True,
                                  shape="square", sort_order=2,
                                  activity=activite, parent_button_id=parent.id))
        else:
            db.session.add(Button(label="Ordonnance", is_present=True,
                                  shape="square", sort_order=1,
                                  activity=activite))
        db.session.commit()
        return activite.id


def test_bouton_grise_sans_equipier(client, application):
    _setup(application, with_member=False)
    html = client.get("/patient/patient_buttons").get_data(as_text=True)
    assert "div_square_button_inactive" in html
    assert '"is_active": "False"' in html


def test_bouton_actif_avec_equipier(client, application):
    _setup(application, with_member=True)
    html = client.get("/patient/patient_buttons").get_data(as_text=True)
    assert "div_square_button_active" in html
    assert "div_square_button_inactive" not in html


def test_bouton_masque_quand_option_masquage(client, application):
    application.config["PAGE_PATIENT_DISABLE_BUTTON"] = False
    _setup(application, with_member=False)
    html = client.get("/patient/patient_buttons").get_data(as_text=True)
    assert "Ordonnance" not in html


def test_inscription_refusee_sans_equipier(client, application):
    """Écran resté ouvert puis activité devenue non appelable : le POST est
    refusé côté serveur même avec is_active=True."""
    activite_id = _setup(application, with_member=False)
    reponse = client.post("/patients_submit", data={
        "is_active": "True", "is_parent": "False",
        "activity_id": str(activite_id),
    })
    html = reponse.get_data(as_text=True)
    assert "Activité indisponible" in html


def test_garde_activity_accepting_registrations(application):
    activite_id = _setup(application, with_member=False)
    from python.engine import activity_accepting_registrations
    with application.app_context():
        activite = db.session.get(Activity, activite_id)
        assert not activity_accepting_registrations(activite)
        activite.pharmacists = [Pharmacist(name="Jean", initials="JD")]
        db.session.commit()
        assert activity_accepting_registrations(activite)


def test_parent_grise_si_seul_enfant_non_appelable(client, application):
    """En mode masquage, un parent dont le seul enfant est non appelable est
    grisé (rien à montrer au clic)."""
    application.config["PAGE_PATIENT_DISABLE_BUTTON"] = False
    _setup(application, with_member=False, children=True)
    html = client.get("/patient/patient_buttons").get_data(as_text=True)
    assert "div_button_disabled" in html
