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
    assert '>Existing English label</textarea>' in html
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


# --- Purge des références orphelines ------------------------------------------

def test_collecte_purge_les_sources_supprimees(authenticated_client, application):
    """Régression : un bouton/une activité supprimé(e) laissait sa référence
    'fr' ET ses traductions au catalogue — éditables mais jamais affichées.
    La collecte supprime désormais toutes les lignes de la source morte."""
    app, _ = application
    with app.app_context():
        language = Language(code="en", name="Anglais", translation="English")
        button = Button(label="Libellé vivant")
        db.session.add_all([language, button])
        db.session.commit()
        button_id = button.id
        # Orphelin : row_id sans bouton correspondant ; vivant : le bouton.
        db.session.add_all([
            Translation(table_name="Button", column_name="label",
                        key_name="", row_id=9999, language_code="fr",
                        translated_text="Source disparue"),
            Translation(table_name="Button", column_name="label",
                        key_name="", row_id=9999, language_code="en",
                        translated_text="Gone source"),
            Translation(table_name="Button", column_name="label",
                        key_name="", row_id=button_id, language_code="en",
                        translated_text="Living label"),
        ])
        db.session.commit()

    response = authenticated_client.post("/admin/translations/collect")
    assert response.status_code == 200
    with app.app_context():
        assert Translation.query.filter_by(row_id=9999).count() == 0
        # La source vivante garde sa traduction et sa référence 'fr'.
        assert _translation(
            table_name="Button", column_name="label", row_id=button_id,
            language_code="en").translated_text == "Living label"
        assert _translation(
            table_name="Button", column_name="label", row_id=button_id,
            language_code="fr").translated_text == "Libellé vivant"


def test_collecte_purge_la_cle_retiree_du_registre(
    authenticated_client, application, monkeypatch,
):
    """Une clé retirée du registre traduisible (ou une ligne de config dont
    le row_id ne correspond plus) n'a plus de source : référence et
    traductions sont purgées."""
    app, _ = application
    monkeypatch.setattr(
        "routes.admin_translation.load_config_keys_to_translate",
        lambda: ["page_patient_title"],
    )
    with app.app_context():
        language = Language(code="en", name="Anglais", translation="English")
        config = ConfigOption(
            config_key="page_patient_title", value_str="Titre")
        db.session.add_all([language, config])
        db.session.commit()
        config_id = config.id
        # Orpheline : même table/colonne mais une clé qui n'est plus
        # traduisible — key_name fait partie de l'identité de la source.
        db.session.add_all([
            Translation(table_name="ConfigOption", column_name="value_str",
                        key_name="ancienne_cle", row_id=config_id,
                        language_code="fr", translated_text="Ancien"),
            Translation(table_name="ConfigOption", column_name="value_str",
                        key_name="ancienne_cle", row_id=config_id,
                        language_code="en", translated_text="Old"),
        ])
        db.session.commit()

    authenticated_client.post("/admin/translations/collect")
    with app.app_context():
        assert Translation.query.filter_by(
            key_name="ancienne_cle").count() == 0
        assert _translation(
            table_name="ConfigOption", column_name="value_str",
            key_name="page_patient_title", row_id=config_id,
            language_code="fr").translated_text == "Titre"


def test_collecte_purge_les_tables_inconnues(authenticated_client, application):
    """Une ligne forgée ou issue d'un ancien schéma (table non traduisible)
    n'a pas de source vivante : elle est purgée aussi."""
    app, _ = application
    with app.app_context():
        db.session.add(Translation(
            table_name="Patient", column_name="name", key_name="",
            row_id=1, language_code="fr", translated_text="Fantôme"))
        db.session.commit()
    authenticated_client.post("/admin/translations/collect")
    with app.app_context():
        assert Translation.query.count() == 0


def test_collecte_purge_key_name_null(authenticated_client, application):
    """Une restauration peut créer key_name=NULL là où la collecte écrit ''.
    La contrainte d'unicité ignore key_name : insérer la canonique ''
    violerait (table, colonne, row_id, langue) — la synchro normalise donc
    la NULL en place, et la purge fait de même pour les autres langues."""
    app, _ = application
    with app.app_context():
        language = Language(code="en", name="Anglais", translation="English")
        button = Button(label="Libellé")
        db.session.add_all([language, button])
        db.session.commit()
        button_id = button.id
        db.session.add_all([
            Translation(table_name="Button", column_name="label",
                        key_name=None, row_id=button_id,
                        language_code="fr", translated_text="Ancien"),
            Translation(table_name="Button", column_name="label",
                        key_name=None, row_id=button_id,
                        language_code="en", translated_text="Restored EN"),
        ])
        db.session.commit()

    authenticated_client.post("/admin/translations/collect")
    with app.app_context():
        fr = Translation.query.filter_by(
            table_name="Button", row_id=button_id,
            language_code="fr").all()
        assert len(fr) == 1
        assert fr[0].key_name == ""
        assert fr[0].translated_text == "Libellé"
        en = _translation(
            table_name="Button", column_name="label", row_id=button_id,
            language_code="en")
        assert en.key_name == ""
        assert en.translated_text == "Restored EN"
