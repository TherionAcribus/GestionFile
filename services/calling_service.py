"""Service « appel patient » — propriétaire unique du flux d'appel.

Avant ce module, la séquence *appeler un patient → activer le comptoir →
annoncer à l'écran* existait en **trois copies** divergentes (``app.py``
``validate_and_call_next``, ``app.py`` ``auto_calling``, ``routes/counter.py``
``update_counter_auto_calling`` — deux d'entre elles portaient jusqu'au même
commentaire recopié), et la **réclamation atomique** du patient en deux versions
(``python/engine.call_next`` et ``app.call_specific_patient_action``, cette
dernière sans nettoyage des ``calling`` périmés, sans réessai et sans
notification du téléphone).

Répartition des rôles :

* ``python/engine.py`` reste le **moteur de sélection** : quel patient appeler
  (algorithme, règles, réclamation atomique en base) ;
* ce module y ajoute la **diffusion** : activation du comptoir et messages temps
  réel, plus les enchaînements de haut niveau (valider puis appeler, appel
  ciblé, mise en pause, appel automatique) ;
* les vues (``app.py``, ``routes/counter.py``) ne font plus qu'appeler ces
  fonctions et traduire leur retour en réponse HTTP.

Aucune fonction d'ici n'émet de réponse HTTP : elles renvoient des données ou un
triplet ``(ok, charge_utile, code_statut)`` que la vue sérialise.
"""

import random
from datetime import datetime

from flask import current_app

from communication import communikation, notify_patient_phone, send_app_notification
from config import time_tz
from models import Counter, Patient, db
from python.engine import (
    call_next,
    claim_patient,
    counter_become_active,
    counter_become_inactive,
    trigger_async_audio_calling,
)
from utils import replace_balise_announces


# ---------------------------------------------------------------------------
# Diffusion d'un appel
# ---------------------------------------------------------------------------

def announce_call(counter_id, patient):
    """Diffuse l'appel d'un patient : activation du comptoir + temps réel.

    C'est le bloc qui était recopié à trois endroits. Il enchaîne :

    1. ``counter_become_active`` — idempotent (n'écrit que si le comptoir était
       inactif), et de toute façon déjà positionné par la réclamation atomique ;
       conservé comme filet pour les chemins qui n'y passent pas.
    2. ``update_patient`` — rafraîchit la file chez tous les clients web.
       **Seule** ``validate_and_call_next`` l'émettait ; les deux chemins d'appel
       automatique ne le faisaient pas, alors que la file change tout autant :
       les pages web restaient donc périmées après un appel automatique.
    3. ``add_calling`` — l'annonce sur l'écran d'affichage, texte construit à
       partir du gabarit ``ANNOUNCE_CALL_TEXT``.

    Le son d'appel n'est PAS déclenché ici : il l'est par ``call_next`` (chemin
    automatique) ou explicitement par ``call_specific`` (chemin manuel).
    """
    counter_become_active(counter_id)
    communikation("update_patient")

    text = replace_balise_announces(current_app.config["ANNOUNCE_CALL_TEXT"], patient)
    communikation(
        "update_screen",
        event="add_calling",
        data={"id": patient.id, "counter_id": counter_id, "text": text},
    )


def call_next_for_counter(counter_id):
    """Appelle le patient suivant pour ce comptoir et diffuse l'appel.

    Renvoie ``(True, patient)`` ou ``(False, raison)`` où *raison* est l'une des
    chaînes de ``python.engine.call_next`` (``no_patient``,
    ``no_patient_for_counter``, ``max_loop``).

    La transition du comptoir vers l'état *inactif* quand il n'y a plus personne
    est laissée à l'appelant : tous les chemins ne la veulent pas (l'appel
    automatique, lui, laisse le comptoir tel quel).
    """
    ok, resultat = call_next(counter_id)
    if ok:
        announce_call(counter_id, resultat)
    return ok, resultat


# ---------------------------------------------------------------------------
# Appel ciblé (le comptoir choisit un patient précis dans la file)
# ---------------------------------------------------------------------------

def call_specific(counter_id, patient_id):
    """Appelle un patient désigné. Renvoie ``(ok, charge_utile, code_statut)``.

    Utilise la **même** réclamation atomique que ``call_next`` (``claim_patient``)
    au lieu de la réimplémenter : deux comptoirs qui cliquent le même patient ne
    peuvent pas le décrocher tous les deux, le perdant reçoit un 423.
    """
    validate_current(counter_id)

    next_patient = Patient.query.get(patient_id)
    if not next_patient:
        return False, {"error": "not_found"}, 404

    try:
        claimed = claim_patient(patient_id, counter_id)
    except Exception:
        current_app.logger.exception(
            "Reclamation du patient %s par le comptoir %s impossible", patient_id, counter_id
        )
        return False, {"error": "claim_failed"}, 500

    if not claimed:
        current_app.logger.info(
            "Patient %s deja appele par un autre comptoir", patient_id
        )
        send_app_notification(
            origin="patient_taken", data={"counter_id": counter_id, "patient": next_patient}
        )
        return False, {"error": "already_called"}, 423

    announce_call(counter_id, next_patient)
    trigger_async_audio_calling(counter_id, next_patient.id, next_patient.language.code)
    # Notification du telephone du patient : elle etait faite par l'appelant web
    # (counter_select_patient) mais PAS par la route utilisee par l'App, qui
    # n'avertissait donc jamais le patient. Centralisee ici, les deux chemins se
    # comportent pareil.
    notify_patient_phone(next_patient.call_number)

    return True, next_patient.to_dict(), 200


# ---------------------------------------------------------------------------
# Fin de prise en charge
# ---------------------------------------------------------------------------

def validate_current(counter_id):
    """Clôt les patients réellement en cours à ce comptoir (calling/ongoing).

    Filtrer sur le statut évite de recharger et réécrire tous les patients déjà
    « done » de la journée : sinon le coût devient quadratique au fil des appels
    et les anciens ``timestamp_end`` sont écrasés par l'heure du dernier appel
    (statistiques de durée faussées). On garde volontairement ``counter_id`` sur
    les patients terminés : les statistiques du jour regroupent les « done » par
    comptoir (``routes/admin_stats``).
    """
    active_patients = Patient.query.filter(
        Patient.counter_id == counter_id,
        Patient.status.in_(("calling", "ongoing")),
    ).all()

    if not active_patients:
        return []

    now = datetime.now(time_tz)
    for patient in active_patients:
        if patient.status == "calling":
            communikation("update_screen", event="remove_calling", data={"id": patient.id})
        patient.status = "done"
        patient.timestamp_end = now
    db.session.commit()
    return active_patients


def validate_and_call_next(counter_id):
    """Valide le patient en cours puis appelle le suivant.

    Renvoie ``(True, patient)`` si un patient a été appelé, ``(False, raison)``
    sinon — dans ce cas le comptoir est repassé inactif.
    """
    current_patient = Patient.query.filter_by(counter_id=counter_id, status="calling").first()
    if current_patient:
        communikation("update_screen", event="remove_calling", data={"id": current_patient.id})

    validate_current(counter_id)

    ok, resultat = call_next_for_counter(counter_id)
    if not ok:
        counter_become_inactive(counter_id)
    return ok, resultat


def pause(counter_id, patient_id):
    """Clôt le patient en cours et met le comptoir en pause (inactif).

    Si le comptoir était en appel automatique, on l'en sort : rester en appel
    automatique tout en étant « en pause » ferait immédiatement rappeler un
    patient.
    """
    current_patient = Patient.query.get(patient_id)
    if current_patient:
        current_patient.status = "done"
        current_patient.timestamp_end = datetime.now(time_tz)
        db.session.commit()

    counter_become_inactive(counter_id)
    communikation("update_patient")

    counter = Counter.query.get(counter_id)
    if counter is not None and counter.auto_calling:
        disable_auto_calling(counter_id)

    return {"id": None, "counter_id": counter_id}


# ---------------------------------------------------------------------------
# Appel automatique
# ---------------------------------------------------------------------------

def counters_en_appel_automatique():
    """Comptoirs en appel automatique, libres et pourvus d'un membre d'équipe.

    Lit ``Counter.auto_calling`` **en base**, seule source de vérité. Ce statut
    était auparavant dupliqué dans ``app.config["AUTO_CALLING"]``, une liste
    Python mutée en place depuis deux modules : par construction propre à un
    processus, elle divergeait entre le conteneur ``web`` et le conteneur
    ``scheduler``, et le rechargement périodique de configuration la
    reconstruisait depuis la base en écrasant les mutations en vol.
    """
    counters = Counter.query.filter(
        Counter.auto_calling.is_(True),
        Counter.is_active.is_(False),
        Counter.staff_id.isnot(None),
    ).all()

    ordre = current_app.config.get("COUNTER_ORDER")
    if ordre == "order":
        counters = sorted(counters, key=lambda c: c.sort_order)
    elif ordre == "random":
        random.shuffle(counters)
    return counters


def run_auto_calling():
    """Sert un comptoir en appel automatique, s'il y en a un de libre.

    S'arrête au premier comptoir servi (un patient appelé à la fois), comme la
    version d'origine.
    """
    for counter in counters_en_appel_automatique():
        ok, resultat = call_next_for_counter(counter.id)
        if not ok:
            # Aucun patient ne convient à CE comptoir : on tente le suivant.
            # L'ancienne version ne testait pas le drapeau de retour et faisait
            # directement `patient.id` : quand call_next renvoyait
            # (False, "no_patient_for_counter"), `patient` valait la chaine
            # d'erreur et l'inscription d'un patient plantait en AttributeError.
            current_app.logger.debug(
                "Appel automatique : rien a appeler pour le comptoir %s (%s)", counter.id, resultat
            )
            continue

        communikation(
            "app_counter",
            event="update_auto_calling",
            data={"counter_id": counter.id, "patient": resultat.to_dict()},
        )
        return True, resultat

    return False, "no_counter"


def set_auto_calling(counter_id, actif):
    """Active/désactive l'appel automatique d'un comptoir.

    Renvoie ``(ok, charge_utile, code_statut)``. Quand on (ré)active un comptoir
    inactif, on lui sert immédiatement un patient.
    """
    counter = Counter.query.get(counter_id)
    if not counter:
        current_app.logger.error("Comptoir introuvable : %s", counter_id)
        return False, "Counter not found", 404

    counter.auto_calling = actif
    db.session.commit()

    if actif and not counter.is_active:
        ok, resultat = call_next_for_counter(counter.id)
        if ok:
            communikation(
                "app_counter",
                event="update_auto_calling",
                data={"counter_id": counter.id, "patient": resultat.to_dict()},
            )

    return True, {"status": counter.auto_calling}, 200


def disable_auto_calling(counter_id):
    """Sort un comptoir de l'appel automatique et en informe les clients web.

    Remplace l'ancien ``call_update_switch_auto_calling``, qui fabriquait une
    fausse requête (``test_request_context()`` puis écrasement de
    ``request.values`` par un dictionnaire) pour pouvoir appeler une *vue* depuis
    du code métier.
    """
    ok, resultat, statut = set_auto_calling(counter_id, False)
    communikation("counter", event="refresh_auto_calling", data={"auto_calling": False})
    return ok, resultat, statut
