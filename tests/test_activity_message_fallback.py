"""Point 4 — repli français uniforme pour les textes d'activité.

``get_activity_message_translation`` rendait ``""`` quand aucune traduction
n'existait : le message spécifique d'une activité disparaissait de la borne,
du téléphone, du ticket et du QR pour les patients en langue étrangère, alors
que le texte français existait. ``inactivity_message`` ne survivait que parce
que SON appelant ajoutait ``or activity.inactivity_message``.

La règle commune est désormais portée par les helpers eux-mêmes :
traduction non vide -> texte source (le français), quel que soit l'appelant.

Verrouillé ici :

1. helper : traduction présente -> traduite ; absente ou vide -> source ; 'fr'
   -> source ; activité absente -> "" ;
2. ticket en langue étrangère sans traduction -> message français conservé
   (avant : ligne vide) ;
3. les appelants de la page téléphone passent la langue de la requête au
   helper (la session pouvait diverger du formulaire).
"""

import base64
import os
import re

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask, session

from models import db, Activity, Language, Patient, Translation


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


@pytest.fixture
def application():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="secret-test-activity-fallback",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        TESTING=True,
        PHARMACY_NAME="Pharmacie de test",
        TICKET_HEADER="Pharmacie",
        TICKET_MESSAGE="Ticket {N}",
        TICKET_FOOTER="Merci",
        TICKET_DISPLAY_SPECIFIC_MESSAGE=True,
        PRINTER_WIDTH=42,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all(bind_key=None)
        yield app
        db.drop_all(bind_key=None)


def _activite(specific_message="Préparez votre ordonnance",
              inactivity_message="Revenez demain"):
    francais = Language(code="fr", name="Français", translation="Français")
    activite = Activity(name="Ordonnance", letter="O",
                        specific_message=specific_message,
                        inactivity_message=inactivity_message)
    db.session.add_all([francais, activite])
    db.session.commit()
    return activite


def _traduit(activite, column_name, texte, language_code="en"):
    db.session.add(Translation(
        table_name="Activity", column_name=column_name, key_name="",
        row_id=activite.id, language_code=language_code,
        translated_text=texte))
    db.session.commit()


# --- 1. Règle commune au niveau des helpers --------------------------------

def test_specific_message_traduit(application):
    from utils import get_activity_message_translation
    with application.app_context():
        activite = _activite()
        _traduit(activite, "specific_message", "Prepare your prescription")
        assert get_activity_message_translation(activite, "en") == \
            "Prepare your prescription"


def test_specific_message_sans_traduction_repli_source(application):
    """Régression : "" rendu avant — le message disparaissait pour les
    patients en langue étrangère."""
    from utils import get_activity_message_translation
    with application.app_context():
        activite = _activite()
        assert get_activity_message_translation(activite, "en") == \
            "Préparez votre ordonnance"


def test_specific_message_traduction_vide_repli_source(application):
    """Une traduction vide enregistrée vaut « absente » : même règle que
    get_text_translation pour les clés de configuration."""
    from utils import get_activity_message_translation
    with application.app_context():
        activite = _activite()
        _traduit(activite, "specific_message", "")
        assert get_activity_message_translation(activite, "en") == \
            "Préparez votre ordonnance"


def test_specific_message_francais_rend_la_source(application):
    from utils import get_activity_message_translation
    with application.app_context():
        activite = _activite()
        assert get_activity_message_translation(activite, "fr") == \
            "Préparez votre ordonnance"


def test_inactivity_message_meme_regle(application):
    """La règle est mutualisée : inactivity_message se comporte pareil, sans
    avoir besoin du `or` que son appelant ajoutait."""
    from utils import get_activity_inactivity_message_translation
    with application.app_context():
        activite = _activite()
        assert get_activity_inactivity_message_translation(activite, "en") == \
            "Revenez demain"
        _traduit(activite, "inactivity_message", "Come back tomorrow")
        assert get_activity_inactivity_message_translation(activite, "en") == \
            "Come back tomorrow"


def test_helpers_sans_activite_ni_exception(application):
    """Appel défensif (activité supprimée entre-temps) : "" plutôt qu'un
    AttributeError — les routes téléphone y étaient exposées."""
    from utils import (
        get_activity_message_translation,
        get_activity_inactivity_message_translation,
    )
    with application.app_context():
        assert get_activity_message_translation(None, "en") == ""
        assert get_activity_inactivity_message_translation(None, "en") == ""


# --- 2. Intégration : le ticket garde le message en langue étrangère --------

def test_ticket_langue_etrangere_garde_le_message_source(application):
    """Régression : le ticket EN imprimait une ligne vide quand le message
    spécifique n'était pas traduit — le texte français doit rester imprimé."""
    from utils import format_ticket_text
    with application.app_context():
        activite = _activite()
        patient = Patient(call_number=7, status="calling",
                          activity_id=activite.id)
        db.session.add(patient)
        db.session.commit()
        with application.test_request_context():
            session["language_code"] = "en"
            encoded = format_ticket_text(patient, activite)
    ticket = base64.b64decode(encoded).decode("utf-8")
    assert "Préparez votre ordonnance" in ticket


def test_ticket_langue_etrangere_utilise_la_traduction(application):
    from utils import format_ticket_text
    with application.app_context():
        activite = _activite()
        _traduit(activite, "specific_message", "Prepare your prescription")
        patient = Patient(call_number=7, status="calling",
                          activity_id=activite.id)
        db.session.add(patient)
        db.session.commit()
        with application.test_request_context():
            session["language_code"] = "en"
            encoded = format_ticket_text(patient, activite)
    ticket = base64.b64decode(encoded).decode("utf-8")
    assert "Prepare your prescription" in ticket


# --- 3. Garde statique : les appelants passent bien par le helper -----------

def test_page_telephone_utilise_le_helper_avec_la_langue_de_la_requete():
    """Les deux handlers téléphone résolvent le message via le helper avec le
    language_code du formulaire — pas session.get() ni .specific_message nu,
    qui cassaient respectivement la cohérence et le repli."""
    source = open(os.path.join(_SERVEUR, "routes", "patient.py"),
                  encoding="utf-8").read()
    assert source.count(
        "get_activity_message_translation(activity, language_code)") >= 2
    assert "specific_message= Activity.query.get" not in source
    assert not re.search(
        r"get_activity_message_translation\(activity, session\.get", source), (
        "le message du téléphone doit suivre la langue du formulaire, "
        "comme les lignes affichées juste au-dessus")
