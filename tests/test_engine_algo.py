"""P0 — moteur de priorité : les règles réellement applicables gouvernent.

Verrouille les défauts confirmés de l'audit :

1. Une règle hors créneau / hors jour / hors seuil ne doit plus annuler une
   règle active : ``max_overtaken`` est calculé sur les seules règles
   applicables (avant : ``min`` sur TOUTES les règles de l'activité).
2. « Dépassement Max » est une borne INCLUSIVE : un patient prioritaire avec
   exactement ``max_overtaken`` patients devant lui peut être appelé.
3. Les jours de la semaine sont appliqués : une règle « samedi » est inactive
   un lundi.
4. Les créneaux horaires s'évaluent dans le fuseau applicatif
   (``config.time_tz``), injectable via ``now`` pour les tests.
5. Le compteur ``overtaken`` ne grossit que pour les patients réellement
   dépassés — activités servies par le comptoir, plus anciens que le patient
   appelé — et seulement après une réclamation réussie.

Les tests passent par les VRAIS modèles sur SQLite mémoire ; les effets de
bord externes (audio, téléphone) sont neutralisés.
"""

import os
from datetime import datetime, time, timedelta

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from models import (
    db, Activity, AlgoRule, Counter, Language, Patient, Pharmacist,
)
import python.engine as engine

_LUNDI = datetime(2024, 1, 1, 10, 0, 0)   # 1er janvier 2024 : un lundi
_SAMEDI = datetime(2024, 1, 6, 10, 0, 0)  # 6 janvier 2024 : un samedi


@pytest.fixture
def application():
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        # db.metadatas est partagé entre fichiers de test : les fichiers
        # précédents ont déjà enregistré le bind 'users' — sans cette entrée,
        # le create_all échouerait (même motif que test_queue_purge).
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        TESTING=True,
        ALGO_IS_ACTIVATED=True,
        ALGO_OVERTAKEN_LIMIT=10,
        ANNOUNCE_CALL_TEXT="Patient {N} au comptoir {C}",
        PHARMACY_NAME="Pharmacie de test",
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app


@pytest.fixture(autouse=True)
def _pas_deffets_externes(monkeypatch):
    monkeypatch.setattr(engine, "trigger_async_audio_calling", lambda *a, **k: None)
    monkeypatch.setattr(engine, "notify_patient_phone", lambda *a, **k: None)


class _Figee(datetime):
    """datetime figé injecté dans ``engine.datetime`` pour piloter
    ``datetime.now(time_tz)`` dans les tests de bout en bout."""
    _instant = _LUNDI

    @classmethod
    def now(cls, tz=None):
        return cls._instant


@pytest.fixture
def fige_lundi(monkeypatch):
    _Figee._instant = _LUNDI
    monkeypatch.setattr(engine, "datetime", _Figee)
    return _Figee


# --- Jeu de données -----------------------------------------------------------

def _activites_et_comptoir(*noms):
    """Crée les activités demandées + un comptoir dont le membre les sert TOUTES."""
    langue = Language(code="fr", name="Français", translation="Français")
    db.session.add(langue)
    activites = {}
    for nom in noms:
        a = Activity(name=nom, letter=nom[0].upper())
        db.session.add(a)
        activites[nom] = a
    membre = Pharmacist(name="Membre", initials="M1")
    membre.activities.extend(activites.values())
    comptoir = Counter(name="C1", sort_order=1, is_active=False)
    comptoir.staff = membre
    db.session.add_all([membre, comptoir])
    db.session.commit()
    return comptoir, activites, langue


def _patient(numero, activite, instant, langue):
    p = Patient(call_number=numero, status="standing", timestamp=instant,
                activity_id=activite.id, language_id=langue.id)
    db.session.add(p)
    db.session.commit()
    return p


def _regle(activite, level=1, max_overtaken=999,
           days="Mon,Tue,Wed,Thu,Fri,Sat,Sun",
           debut="00:00", fin="23:59", min_p=0, max_p=999, nom=None):
    r = AlgoRule(
        name=nom or f"regle-{activite.id}-{level}-{debut}-{fin}",
        activity_id=activite.id, priority_level=level,
        min_patients=min_p, max_patients=max_p,
        max_overtaken=max_overtaken,
        start_time=time.fromisoformat(debut),
        end_time=time.fromisoformat(fin),
        days_of_week=days)
    db.session.add(r)
    db.session.commit()
    return r


# --- Applicabilité des règles --------------------------------------------------

def test_regle_hors_creneau_ne_bloque_pas_la_regle_active(application, fige_lundi):
    """Défaut 2 : le min() prenait aussi les règles inactives (ici max=0 hors
    créneau) et annulait la priorité accordée par la règle active."""
    comptoir, acts, langue = _activites_et_comptoir("Normale", "Prio")
    _regle(acts["Prio"], max_overtaken=5, debut="00:00", fin="23:59")
    _regle(acts["Prio"], max_overtaken=0, debut="03:00", fin="04:00")  # inapplicable à 10h

    _patient("N1", acts["Normale"], _LUNDI, langue)
    prio = _patient("P1", acts["Prio"], _LUNDI + timedelta(minutes=1), langue)

    choisi = engine.algo_choice_next_patient(comptoir.id)
    assert choisi.id == prio.id, "la règle active doit primer sur le FIFO"


def test_jour_hors_regle_rend_la_regle_inactive(application, fige_lundi):
    """Défaut 5 : le filtre sur les jours était commenté."""
    comptoir, acts, langue = _activites_et_comptoir("Normale", "Prio")
    _regle(acts["Prio"], days="Sat,Sun")  # applicable seulement le week-end

    ancien = _patient("N1", acts["Normale"], _LUNDI, langue)
    _patient("P1", acts["Prio"], _LUNDI + timedelta(minutes=1), langue)

    choisi = engine.algo_choice_next_patient(comptoir.id)
    assert choisi.id == ancien.id, "lundi : règle week-end inactive -> FIFO"

    # Le samedi, la même règle s'applique.
    _Figee._instant = _SAMEDI
    choisi = engine.algo_choice_next_patient(comptoir.id)
    assert choisi.call_number == "P1", "samedi : règle active -> prioritaire"


def test_regle_hors_seuil_effectif_inactive(application, fige_lundi):
    comptoir, acts, langue = _activites_et_comptoir("Normale", "Prio")
    _regle(acts["Prio"], min_p=5, max_p=999)  # exige >= 5 patients en file

    ancien = _patient("N1", acts["Normale"], _LUNDI, langue)
    _patient("P1", acts["Prio"], _LUNDI + timedelta(minutes=1), langue)

    choisi = engine.algo_choice_next_patient(comptoir.id)
    assert choisi.id == ancien.id, "seulement 2 patients : seuil non atteint -> FIFO"


# --- Borne de dépassement inclusive ---------------------------------------------

def test_borne_depassement_est_inclusive(application, fige_lundi):
    """Défaut 4 : ``patients_ahead < max_overtaken`` refusait la priorité à un
    patient ayant EXACTEMENT ``max_overtaken`` personnes devant lui."""
    comptoir, acts, langue = _activites_et_comptoir("Normale", "Prio")
    _regle(acts["Prio"], max_overtaken=1)

    _patient("N1", acts["Normale"], _LUNDI, langue)
    prio = _patient("P1", acts["Prio"], _LUNDI + timedelta(minutes=1), langue)

    choisi = engine.algo_choice_next_patient(comptoir.id)
    assert choisi.id == prio.id, "1 patient devant <= max_overtaken 1 : doit passer"


def test_borne_depassement_depassee_reste_fifo(application, fige_lundi):
    comptoir, acts, langue = _activites_et_comptoir("Normale", "Prio")
    _regle(acts["Prio"], max_overtaken=1)

    ancien = _patient("N1", acts["Normale"], _LUNDI, langue)
    _patient("N2", acts["Normale"], _LUNDI + timedelta(minutes=1), langue)
    _patient("P1", acts["Prio"], _LUNDI + timedelta(minutes=2), langue)

    choisi = engine.algo_choice_next_patient(comptoir.id)
    assert choisi.id == ancien.id, "2 patients devant > max_overtaken 1 : FIFO"


# --- Niveaux de priorité ---------------------------------------------------------

def test_niveau_1_est_la_plus_haute_priorite(application, fige_lundi):
    """Défaut 7 : le niveau 1 est examiné en premier = priorité la plus haute."""
    comptoir, acts, langue = _activites_et_comptoir("Normale", "PrioA", "PrioB")
    _regle(acts["PrioA"], level=2)
    _regle(acts["PrioB"], level=1)

    _patient("N1", acts["Normale"], _LUNDI, langue)
    _patient("PA", acts["PrioA"], _LUNDI + timedelta(minutes=1), langue)
    prio_b = _patient("PB", acts["PrioB"], _LUNDI + timedelta(minutes=2), langue)

    choisi = engine.algo_choice_next_patient(comptoir.id)
    assert choisi.id == prio_b.id, "niveau 1 doit passer devant niveau 2"


def test_sans_regle_applicable_fifo(application, fige_lundi):
    comptoir, acts, langue = _activites_et_comptoir("Normale", "Prio")
    ancien = _patient("N1", acts["Normale"], _LUNDI, langue)
    _patient("P1", acts["Prio"], _LUNDI + timedelta(minutes=1), langue)

    choisi = engine.algo_choice_next_patient(comptoir.id)
    assert choisi.id == ancien.id


def test_frein_famine_global(application, fige_lundi):
    """Un patient dépassé ALGO_OVERTAKEN_LIMIT fois désactive l'algo : FIFO."""
    comptoir, acts, langue = _activites_et_comptoir("Normale", "Prio")
    _regle(acts["Prio"])

    ancien = _patient("N1", acts["Normale"], _LUNDI, langue)
    ancien.overtaken = 10  # == ALGO_OVERTAKEN_LIMIT
    db.session.commit()
    _patient("P1", acts["Prio"], _LUNDI + timedelta(minutes=1), langue)

    choisi = engine.algo_choice_next_patient(comptoir.id)
    assert choisi.id == ancien.id, "le frein famine doit rendre la main au FIFO"


# --- Compteur de dépassements ----------------------------------------------------

def test_overtaken_ne_compte_que_les_patients_reellement_depases(application, fige_lundi):
    """Défaut 3 : l'ancien code incrémentait TOUTE la file, y compris les
    activités que le comptoir ne sait pas servir, même sans règle applicable."""
    comptoir, acts, langue = _activites_et_comptoir("Normale", "Prio", "Autre")
    _regle(acts["Prio"])

    ancien = _patient("N1", acts["Normale"], _LUNDI, langue)
    incompatible = _patient("A1", acts["Autre"], _LUNDI, langue)

    # Le membre perd la compétence "Autre" : ce patient ne peut pas être
    # dépassé par ce comptoir.
    comptoir.staff.activities = [acts["Normale"], acts["Prio"]]
    db.session.commit()

    prio = _patient("P1", acts["Prio"], _LUNDI + timedelta(minutes=1), langue)

    ok, appele = engine.call_next(comptoir.id)
    assert ok is True and appele.id == prio.id

    assert db.session.get(Patient, ancien.id).overtaken == 1, \
        "le patient serviable dépassé doit gagner un dépassement"
    assert db.session.get(Patient, incompatible.id).overtaken == 0, \
        "activité non servie par le comptoir : pas de dépassement"


def test_overtaken_zero_sans_regle_ni_saut(application, fige_lundi):
    """Appel FIFO : personne n'est dépassé, aucun compteur ne bouge."""
    comptoir, acts, langue = _activites_et_comptoir("Normale")
    p1 = _patient("N1", acts["Normale"], _LUNDI, langue)
    p2 = _patient("N2", acts["Normale"], _LUNDI + timedelta(minutes=1), langue)

    ok, appele = engine.call_next(comptoir.id)
    assert ok is True and appele.id == p1.id
    assert db.session.get(Patient, p2.id).overtaken == 0


def test_overtaken_apres_reclamation_uniquement(application, fige_lundi, monkeypatch):
    """Défaut 3 : les dépassements étaient comptés AVANT la réclamation —
    un comptoir perdant gonflait les compteurs sans avoir appelé."""
    comptoir, acts, langue = _activites_et_comptoir("Normale", "Prio")
    _regle(acts["Prio"])
    ancien = _patient("N1", acts["Normale"], _LUNDI, langue)
    _patient("P1", acts["Prio"], _LUNDI + timedelta(minutes=1), langue)

    # La réclamation échoue toujours (patient pris par un autre comptoir) :
    # les 5 essais épuisés finissent en max_loop, sans avoir appelé personne.
    monkeypatch.setattr(engine, "claim_patient", lambda *a, **k: False)

    ok, _ = engine.call_next(comptoir.id)

    assert ok is False
    assert db.session.get(Patient, ancien.id).overtaken == 0, \
        "aucun appel réussi -> aucun dépassement compté"


# --- Initialisation des règles par défaut ---------------------------------------

def test_init_charge_les_regles_par_defaut(application):
    """Défaut 1 : le JSON utilisait la clé « rules » (le chargeur lit
    « algo_rules »), des heures sans secondes et « days_of_the_week » —
    résultat : 0 règle chargée mais la version consignée, figeant l'échec."""
    from init_restore import init_default_algo_rules_db_from_json
    from models import ConfigVersion

    with application.app_context():
        init_default_algo_rules_db_from_json()

        rules = AlgoRule.query.all()
        assert len(rules) == 3, "les règles par défaut doivent être chargées"

        par_activite = {r.activity_id: r for r in rules}
        assert 6 in par_activite, "l'activité du bouton Prioritaire doit en avoir une"
        assert par_activite[6].priority_level == 1, "personne prioritaire = niveau 1"

        version = ConfigVersion.query.filter_by(config_key="algo_rules_version").first()
        assert version is not None

        # Second passage : aucune duplication.
        init_default_algo_rules_db_from_json()
        assert AlgoRule.query.count() == 3


# --- Validation serveur de l'administration --------------------------------------

class _Form(dict):
    """Bémol : MultiDict minimal — get/getlist, ce que valider_regle_algo lit."""

    def getlist(self, nom):
        valeur = self.get(nom)
        if valeur is None:
            return []
        return valeur if isinstance(valeur, list) else [valeur]


def _form_regle(activity_id, **overrides):
    form = _Form(
        name="regle", activity_id=str(activity_id), priority_level="1",
        min_patients="0", max_patients="999", max_overtaken="999",
        start_time="00:00", end_time="23:59",
        days_of_week=["Mon", "Tue", "Wed", "Thu", "Fri"],
    )
    form.update(overrides)
    return form


def test_validation_numerique_pas_de_comparaison_de_chaines(application):
    """Défaut admin : '9' > '10' en comparaison de chaînes. Ici 9 <= 10 doit
    passer — et le résultat est bien un entier."""
    from routes.admin_algo import valider_regle_algo

    _, acts, _ = _activites_et_comptoir("Normale")
    valeurs, erreur = valider_regle_algo(
        _form_regle(acts["Normale"].id, min_patients="9", max_patients="10"))
    assert erreur is None
    assert valeurs["min_patients"] == 9 and valeurs["max_patients"] == 10


def test_validation_rejette_min_superieur_max(application):
    from routes.admin_algo import valider_regle_algo

    _, acts, _ = _activites_et_comptoir("Normale")
    valeurs, erreur = valider_regle_algo(
        _form_regle(acts["Normale"].id, min_patients="10", max_patients="2"))
    assert valeurs is None and erreur, "min > max doit être refusé"


def test_validation_rejette_creneau_inverse_et_priorite_hors_plage(application):
    from routes.admin_algo import valider_regle_algo

    _, acts, _ = _activites_et_comptoir("Normale")
    _, erreur = valider_regle_algo(
        _form_regle(acts["Normale"].id, start_time="18:00", end_time="09:00"))
    assert erreur, "créneau inversé doit être refusé"

    _, erreur = valider_regle_algo(
        _form_regle(acts["Normale"].id, priority_level="0"))
    assert erreur, "priorité 0 doit être refusée"

    _, erreur = valider_regle_algo(
        _form_regle(acts["Normale"].id, days_of_week=["Mon", "Xxx"]))
    assert erreur, "jour inconnu doit être refusé"


def test_validation_jours_absents_tous_les_jours(application):
    from routes.admin_algo import valider_regle_algo

    _, acts, _ = _activites_et_comptoir("Normale")
    form = _form_regle(acts["Normale"].id)
    del form["days_of_week"]
    valeurs, erreur = valider_regle_algo(form)
    assert erreur is None
    assert valeurs["days_of_week"] == "Mon,Tue,Wed,Thu,Fri,Sat,Sun"


# --- Cohérence affichage / appel réel -------------------------------------------

def test_simulation_et_moteur_d_accord_sur_la_regle_active(application, fige_lundi):
    """Le scénario du rapport : l'écran affichait P1 en premier alors que le
    moteur appelait N1 — les deux chemins utilisent désormais les mêmes règles."""
    comptoir, acts, langue = _activites_et_comptoir("Normale", "Prio")
    _regle(acts["Prio"], max_overtaken=5)
    _regle(acts["Prio"], max_overtaken=0, debut="03:00", fin="04:00")

    _patient("N1", acts["Normale"], _LUNDI, langue)
    _patient("P1", acts["Prio"], _LUNDI + timedelta(minutes=1), langue)

    with application.app_context():
        queue = engine.get_global_patient_queue()
    assert [p.call_number for p in queue] == ["P1", "N1"], "affichage"

    choisi = engine.algo_choice_next_patient(comptoir.id)
    assert choisi.call_number == "P1", "appel réel identique à l'affichage"
