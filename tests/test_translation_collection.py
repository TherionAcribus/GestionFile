"""Synchronisation du catalogue de traductions.

Régression : la collecte était désactivée alors que l'interface l'appelait, et
elle n'insérait que les références françaises absentes. Un libellé modifié dans
l'administration conservait donc son ancienne référence française.
"""

import os

import pytest
from flask import Flask
from flask_login import LoginManager

from models import Activity, Button, ConfigOption, Language, Role, Translation, User, db
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
        SECRET_KEY="secret-test-translation-collection",
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
        # db.metadatas est partagé avec d'autres modules de tests qui peuvent
        # ajouter un bind 'users' non configuré ici : ne créer que le bind
        # principal, comme dans la fixture editor_app.
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
def authenticated_client(application):
    app, user_id = application
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return client


def _translation(**kwargs):
    return Translation.query.filter_by(**kwargs).one()


def test_collecte_requiert_post(authenticated_client):
    assert authenticated_client.get("/admin/translations/collect").status_code == 405
    assert authenticated_client.post("/admin/translations/collect").status_code == 200


def test_langue_de_reference_non_modifiable_comme_cible(
    authenticated_client, monkeypatch,
):
    toasts = []
    monkeypatch.setattr(
        "routes.admin_translation.display_toast",
        lambda **kwargs: toasts.append(kwargs),
    )

    response = authenticated_client.post(
        "/admin/translations/change_language_target",
        data={"language_code": "fr"},
    )

    assert response.status_code == 200
    assert response.get_data(as_text=True) == ""
    assert toasts == [{
        "success": False,
        "message": "Le français est la langue de référence.",
    }]


def test_collecte_met_a_jour_la_reference_sans_ecraser_la_cible(
    authenticated_client, application, monkeypatch,
):
    app, _ = application
    monkeypatch.setattr(
        "routes.admin_translation.load_config_keys_to_translate",
        lambda: ["page_patient_title"],
    )

    with app.app_context():
        language = Language(code="en", name="Anglais", translation="English")
        activity = Activity(
            name="Ordonnances",
            letter="O",
            specific_message="Nouveau message spécifique",
            inactivity_message="",
        )
        button = Button(label="Nouveau libellé", activity=activity)
        config = ConfigOption(
            config_key="page_patient_title",
            value_str="Nouveau titre patient",
        )
        db.session.add_all([language, activity, button, config])
        db.session.commit()
        activity_id = activity.id
        button_id = button.id
        config_id = config.id

        db.session.add_all([
            Translation(
                table_name="Button", column_name="label", key_name="",
                row_id=button_id, language_code="fr",
                translated_text="Ancien libellé",
            ),
            Translation(
                table_name="Button", column_name="label", key_name="",
                row_id=button_id, language_code="en",
                translated_text="Existing English label",
            ),
            Translation(
                table_name="Activity", column_name="specific_message",
                key_name="", row_id=activity_id, language_code="fr",
                translated_text="Ancien message spécifique",
            ),
            Translation(
                table_name="Activity", column_name="specific_message",
                key_name="", row_id=activity_id, language_code="en",
                translated_text="Existing English message",
            ),
            Translation(
                table_name="Activity", column_name="inactivity_message",
                key_name="", row_id=activity_id, language_code="fr",
                translated_text="Ancien message d'inactivité",
            ),
        ])
        db.session.commit()

    response = authenticated_client.post(
        "/admin/translations/collect",
        data={"language_code": "en"},
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'hx-swap-oob="innerHTML"' in html
    assert 'value="Existing English label"' in html
    with app.app_context():
        assert _translation(
            table_name="Button", column_name="label", key_name="",
            row_id=button_id, language_code="fr",
        ).translated_text == "Nouveau libellé"
        assert _translation(
            table_name="Button", column_name="label", key_name="",
            row_id=button_id, language_code="en",
        ).translated_text == "Existing English label"

        assert _translation(
            table_name="Activity", column_name="specific_message", key_name="",
            row_id=activity_id, language_code="fr",
        ).translated_text == "Nouveau message spécifique"
        assert _translation(
            table_name="Activity", column_name="specific_message", key_name="",
            row_id=activity_id, language_code="en",
        ).translated_text == "Existing English message"
        assert _translation(
            table_name="Activity", column_name="inactivity_message", key_name="",
            row_id=activity_id, language_code="fr",
        ).translated_text == ""

        assert _translation(
            table_name="ConfigOption", column_name="value_str",
            key_name="page_patient_title", row_id=config_id,
            language_code="fr",
        ).translated_text == "Nouveau titre patient"
