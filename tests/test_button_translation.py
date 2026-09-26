"""Traduction des boutons : lecture sans mutation du modèle persisté.

Régression : ``get_buttons_translation`` remplaçait ``Button.label`` sur
l'instance ORM. La requête marquait alors le bouton comme modifié et un
``db.session.commit()`` ultérieur pouvait remplacer le libellé source français
par sa traduction.
"""

import os

import pytest
from flask import Flask

from models import Activity, Button, Translation, db
from routes.patient import patient_bp
from utils import get_button_translations

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


@pytest.fixture
def application():
    app = Flask(
        __name__,
        template_folder=os.path.join(_SERVEUR, "templates"),
    )
    app.config.update(
        SECRET_KEY="secret-test-button-translation",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        TESTING=True,
        PAGE_PATIENT_SUBTITLE="Sous-titre",
        PAGE_PATIENT_INTERFACE_VALIDATE_CANCEL="Annuler",
    )
    db.init_app(app)
    app.register_blueprint(patient_bp)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_traduction_chargee_sans_modifier_le_bouton_persiste(application):
    with application.app_context():
        button = Button(label="Ordonnance")
        db.session.add(button)
        db.session.commit()
        db.session.add(Translation(
            table_name="Button",
            column_name="label",
            key_name="",
            row_id=button.id,
            language_code="en",
            translated_text="Prescription",
        ))
        db.session.commit()
        button_id = button.id

        translations = get_button_translations([button], "en")

        assert translations == {button_id: "Prescription"}
        assert button.label == "Ordonnance"
        assert button not in db.session.dirty

        db.session.commit()
        db.session.expire_all()
        assert db.session.get(Button, button_id).label == "Ordonnance"


def test_sans_traduction_le_libelle_source_est_conserve(application):
    with application.app_context():
        button = Button(label="Ordonnance")
        db.session.add(button)
        db.session.commit()

        assert get_button_translations([button], "en") == {}
        assert button.label == "Ordonnance"
        assert button not in db.session.dirty


def test_page_patient_affiche_la_traduction_sans_changer_la_source(application):
    client = application.test_client()
    with application.app_context():
        activity = Activity(name="Ordonnances", letter="O")
        button = Button(
            label="Déposer une ordonnance",
            activity=activity,
            is_present=True,
            shape="square",
        )
        db.session.add_all([activity, button])
        db.session.commit()
        db.session.add(Translation(
            table_name="Button",
            column_name="label",
            key_name="",
            row_id=button.id,
            language_code="en",
            translated_text="Drop off a prescription",
        ))
        db.session.commit()
        button_id = button.id

    with client.session_transaction() as session:
        session["language_code"] = "en"
    response = client.get("/patient/patient_buttons")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Drop off a prescription" in html
    assert "Déposer une ordonnance" not in html
    with application.app_context():
        assert db.session.get(Button, button_id).label == "Déposer une ordonnance"
