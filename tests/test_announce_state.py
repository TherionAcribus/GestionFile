"""Point 6 — endpoint ``/announce/state`` de resynchronisation de l'écran.

Les évènements ``add_calling``/``remove_calling`` envoyés à l'écran sont
incrémentaux : Socket.IO ne rejoue rien, un message perdu hors coupure franche
laissait une bannière fantôme ou manquante jusqu'au prochain évènement (la
reconnexion rechargait toute la page). ``/announce/state`` renvoie le snapshot
autoritatif — bannières d'appel + révision de file — que le client utilise pour
réconcilier son DOM, comme ``/api/counter/<id>/state`` le fait pour l'App
comptoir.

Verrouillé ici :

1. forme et contenu du snapshot (id, ``counter_id``, texte rendu) ;
2. la révision reflète bien le compteur monotone de la file ;
3. ``patient_list_for_init_display`` inclut ``counter_id`` — le gabarit
   l'utilisait déjà (``data-counter``) alors que l'ancienne fonction ne le
   fournissait pas : les bannières initiales avaient un ``data-counter`` vide.
"""

import os

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from models import (
    db, Activity, ConfigOption, Counter, Language, Patient, Pharmacist,
    bump_queue_revision, get_queue_revision,
)


@pytest.fixture
def application():
    app = Flask(__name__, template_folder=os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "templates"))
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        # Pas de SQLALCHEMY_BINDS : l'extension db est partagée entre tous les
        # fichiers de test et db.metadatas accumule les binds vus — déclarer
        # 'users' ici ferait échouer le create_all() des tests ultérieurs dont
        # l'app ne définit pas ce bind.
        TESTING=True,
        PHARMACY_NAME="Pharmacie de test",
        ALGO_IS_ACTIVATED=False,
        ALGO_OVERTAKEN_LIMIT=3,
    )
    db.init_app(app)
    from routes.announce import announce_bp
    app.register_blueprint(announce_bp)
    with app.app_context():
        db.create_all()
        yield app


@pytest.fixture
def client(application):
    return application.test_client()


def _jeu(nb_appels=2):
    """Comptoir pourvu d'un membre d'équipe + patients 'calling' et 'standing'."""
    langue = Language(code="fr", name="Français", translation="Français")
    activite = Activity(name="Ordonnance", letter="O")
    option = ConfigOption(config_key="announce_call_text",
                          value_str="Patient {N} au comptoir {C}")
    membre = Pharmacist(name="Alice", initials="AL")
    comptoir = Counter(name="1", sort_order=1, is_active=True)
    comptoir.staff = membre
    db.session.add_all([langue, activite, option, membre, comptoir])
    db.session.commit()

    appels = []
    for i in range(nb_appels):
        p = Patient(call_number=100 + i, status="calling",
                    activity_id=activite.id, language_id=langue.id,
                    counter_id=comptoir.id)
        db.session.add(p)
        appels.append(p)
    attente = Patient(call_number=200, status="standing",
                      activity_id=activite.id, language_id=langue.id)
    db.session.add(attente)
    db.session.commit()
    return comptoir, appels


def test_state_vide_renvoi_liste_vide_et_revision(client, application):
    with application.app_context():
        db.session.add(ConfigOption(config_key="announce_call_text",
                                    value_str="Patient {N} au comptoir {C}"))
        db.session.commit()
    reponse = client.get("/announce/state")
    assert reponse.status_code == 200
    corps = reponse.get_json()
    assert corps["calling"] == []
    assert isinstance(corps["revision"], int)


def test_state_ne_renvoi_que_les_appels(client, application):
    with application.app_context():
        comptoir, appels = _jeu(nb_appels=2)
        ids_appels = [p.id for p in sorted(appels, key=lambda p: p.call_number)]
        comptoir_id = comptoir.id
    corps = client.get("/announce/state").get_json()
    # Tri par numéro d'appel, comme l'affichage initial de /display.
    assert [c["id"] for c in corps["calling"]] == ids_appels
    for c in corps["calling"]:
        assert c["counter_id"] == comptoir_id
        assert "au comptoir 1" in c["text"]


def test_state_revision_suit_le_compteur_de_file(client, application):
    with application.app_context():
        _jeu(nb_appels=1)
        rev_avant = get_queue_revision()
    assert client.get("/announce/state").get_json()["revision"] == rev_avant
    with application.app_context():
        bump_queue_revision()
        rev_apres = get_queue_revision()
    assert rev_apres == rev_avant + 1
    assert client.get("/announce/state").get_json()["revision"] == rev_apres


def test_state_sans_ligne_configoption_en_base(client, application):
    """Régression : la lecture directe de ConfigOption levait un
    AttributeError si la ligne announce_call_text manquait. Le gabarit vient
    désormais d'app.config (chargement groupé + repli par défaut)."""
    with application.app_context():
        # Pas de ConfigOption announce_call_text — seulement le reste du jeu.
        langue = Language(code="fr", name="Français", translation="Français")
        activite = Activity(name="Ordonnance", letter="O")
        membre = Pharmacist(name="Alice", initials="AL")
        comptoir = Counter(name="1", sort_order=1, is_active=True)
        comptoir.staff = membre
        db.session.add_all([langue, activite, membre, comptoir])
        db.session.commit()
        db.session.add(Patient(call_number=100, status="calling",
                               activity_id=activite.id, language_id=langue.id,
                               counter_id=comptoir.id))
        db.session.commit()
    reponse = client.get("/announce/state")
    assert reponse.status_code == 200
    appel = reponse.get_json()["calling"][0]
    assert "100" in appel["text"]


def test_reponses_announce_sans_cache(client, application):
    """L'écran reflète un état temps réel : ni le navigateur (bfcache) ni un
    proxy ne doivent servir une file périmée — Cache-Control: no-store sur
    toutes les routes du blueprint."""
    for chemin in ("/announce/state", "/announce/patients_next"):
        reponse = client.get(chemin)
        assert reponse.headers.get("Cache-Control") == "no-store", chemin


def test_init_display_inclut_counter_id(application):
    """Régression : le gabarit lit call_patient.counter_id (data-counter)."""
    from routes.announce import patient_list_for_init_display
    with application.app_context():
        comptoir, appels = _jeu(nb_appels=1)
        comptoir_id, appel_id = comptoir.id, appels[0].id
        liste = patient_list_for_init_display()
    assert liste[0]["counter_id"] == comptoir_id
    assert liste[0]["id"] == appel_id
    assert liste[0]["text"]
