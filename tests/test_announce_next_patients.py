"""« Prochains patients » — liste bornée, déterministe, cachée par révision.

Verrouillé ici (audit « liste coûteuse et indicative ») :

1. ``/announce/patients_next`` n'affiche que ``NEXT_PATIENTS_DISPLAY_LIMIT``
   (5) numéros — la file globale n'est plus ordonnée en entier pour en
   montrer 5 : ``get_global_patient_queue(limit=...)`` sort de la boucle
   dès la borne atteinte.
2. Le tri de base est ``(timestamp, id)`` : à timestamps égaux, l'id le
   plus petit passe devant — l'ordre affiché est déterministe.
3. Le résultat est mémorisé par révision de file dans ``app.extensions`` :
   deux requêtes à révision égale = un seul calcul ; une mutation
   (``bump_queue_revision``) le relance. Portée application : pas de fuite
   entre les apps des tests.
4. L'écran présente la liste comme un « ordre indicatif » : la simulation
   ignore les compétences propres à chaque comptoir.
5. Côté client, un appel ne déclenche plus deux rafraîchissements : le
   handler ``add_calling`` ne re-déclenche pas ``refresh_next_patients``
   (l'évènement ``update`` du namespace général couvre déjà ce cas).
"""

import os
import re
from datetime import datetime, timedelta

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from models import db, Activity, Language, Patient, bump_queue_revision


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


@pytest.fixture
def application():
    app = Flask(__name__, template_folder=os.path.join(_SERVEUR, "templates"))
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        # Pas de SQLALCHEMY_BINDS : db.metadatas accumule les binds vus entre
        # fichiers de test (même motif que test_announce_state).
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


def _file(application, numeros, debut=None, pas=60):
    """Patients 'standing' aux timestamps croissants (un par minute)."""
    debut = debut or datetime(2024, 1, 1, 9, 0, 0)
    with application.app_context():
        langue = Language(code="fr", name="Français", translation="Français")
        activite = Activity(name="Ordonnance", letter="O")
        db.session.add_all([langue, activite])
        db.session.commit()
        for i, numero in enumerate(numeros):
            db.session.add(Patient(
                call_number=numero, status="standing",
                timestamp=debut + timedelta(seconds=pas * i),
                activity_id=activite.id, language_id=langue.id))
        db.session.commit()


# --- 1. Liste bornée ----------------------------------------------------------

def test_affichage_borne_a_cinq_numeros(client, application):
    _file(application, [f"3{i:02d}" for i in range(8)])  # 300..307
    corps = client.get("/announce/patients_next").get_data(as_text=True)
    assert "300" in corps and "304" in corps
    assert "305" not in corps and "307" not in corps


def test_calcul_borne_sans_ordonner_toute_la_file(application):
    from python.engine import get_global_patient_queue
    _file(application, [f"4{i:02d}" for i in range(10)])
    with application.app_context():
        queue = get_global_patient_queue(limit=3)
        assert len(queue) == 3
        # Sans borne, la file complète est ordonnée (comportement conservé).
        assert len(get_global_patient_queue()) == 10


# --- 2. Tri déterministe (timestamp, id) ---------------------------------------

def test_tri_timestamp_puis_id_a_timestamps_egaux(application):
    from python.engine import get_global_patient_queue
    instant = datetime(2024, 1, 1, 9, 0, 0)
    with application.app_context():
        langue = Language(code="fr", name="Français", translation="Français")
        activite = Activity(name="Ordonnance", letter="O")
        db.session.add_all([langue, activite])
        db.session.commit()
        # Même timestamp : l'id départage — 501 est inséré AVANT 500, c'est
        # donc lui qui doit passer premier (un tri par numéro d'appel
        # donnerait 500, 501).
        for numero in ("501", "500"):
            db.session.add(Patient(
                call_number=numero, status="standing", timestamp=instant,
                activity_id=activite.id, language_id=langue.id))
        db.session.add(Patient(
            call_number="499", status="standing",
            timestamp=instant - timedelta(minutes=1),
            activity_id=activite.id, language_id=langue.id))
        db.session.commit()
        queue = get_global_patient_queue()
    assert [p.call_number for p in queue] == ["499", "501", "500"]


def test_seuls_les_patients_en_attente_sont_listes(application):
    from python.engine import get_global_patient_queue
    _file(application, ["601", "602"])
    with application.app_context():
        appele = Patient.query.filter_by(call_number="602").first()
        appele.status = "calling"
        db.session.commit()
        queue = get_global_patient_queue()
    assert [p.call_number for p in queue] == ["601"]


# --- 3. Cache par révision ------------------------------------------------------

def test_resultat_memorise_par_revision(client, application, monkeypatch):
    import python.engine as engine
    _file(application, ["701", "702"])

    calculs = []
    reelle = engine.get_global_patient_queue
    def espion(limit=None):
        calculs.append(limit)
        return reelle(limit=limit)
    monkeypatch.setattr(engine, "get_global_patient_queue", espion)

    client.get("/announce/patients_next")
    client.get("/announce/patients_next")
    assert len(calculs) == 1, "même révision : le calcul ne doit pas être relancé"

    with application.app_context():
        bump_queue_revision()
    client.get("/announce/patients_next")
    assert len(calculs) == 2, "révision modifiée : le calcul doit être relancé"


def test_cache_porte_par_application(client, application):
    """Le cache vit dans app.extensions : une autre app ne le voit pas."""
    _file(application, ["801"])
    client.get("/announce/patients_next")
    entree = application.extensions["gestionfile_announce"]
    assert entree["next_patients"] == ["801"]

    autre = Flask(__name__)
    assert "gestionfile_announce" not in autre.extensions


# --- 4. Ordre indicatif ---------------------------------------------------------

def test_gabarit_presente_l_ordre_comme_indicatif(client, application):
    _file(application, ["901"])
    corps = client.get("/announce/patients_next").get_data(as_text=True)
    assert "ordre indicatif" in corps


# --- 5. Pas de double rafraîchissement par appel ---------------------------------

def test_add_calling_ne_redeclenche_pas_next_patients():
    """Un appel émet 'update' (général) ET 'add_calling' (écran) : le second
    ne doit pas relancer la requête /announce/patients_next déjà déclenchée
    par refresh_calling_list via le premier."""
    with open(os.path.join(_SERVEUR, "static/js/announce.js"), encoding="utf-8") as fh:
        source = fh.read()
    debut = source.index("screenSocket.on('add_calling'")
    fin = source.index("});", debut)
    corps = re.sub(r"//[^\n]*", "", source[debut:fin])  # ignore le commentaire
    assert "htmx.trigger" not in corps, (
        "add_calling ne doit pas déclencher refresh_next_patients — "
        "l'évènement 'update' du namespace général le fait déjà"
    )
    # Le rafraîchissement reste couvert par l'évènement général.
    corps_update = source[source.index("generalSocket.on('update'"):]
    assert "refresh_calling_list()" in corps_update
