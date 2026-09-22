"""Service « file d'attente » — purge globale des patients.

Point audit : la purge vivait dans la VUE ``clear_all_patients_from_db`` de
``routes/admin_queue.py``, décorée par ``@require_permission`` :

* la variante « vider avec sauvegarde » l'appelait sans renvoyer sa réponse —
  la purge réussissait puis Flask renvoyait 500 (« view did not return ») ;
* le planificateur (``scheduler_functions.clear_all_patients_job``) appelait la
  même vue décorée : hors requête HTTP, ``current_user`` est indisponible — la
  tâche échouait avant d'atteindre la purge.

Le métier vit désormais ici, **sans décorateur ni réponse HTTP** : la route
protégée et la tâche planifiée appellent chacune ``purge_all_patients`` /
``archive_and_purge_all_patients`` et traduisent le résultat (toast /
JobExecutionLog).

Second point audit (atomicité) : la copie vers ``PatientHistory`` et la purge
étaient validées en **deux transactions** — une copie réussie suivie d'une
suppression en échec laissait la file intacte, et la relance du lendemain
recopiait les mêmes patients (doublons d'historique, rien ne les bloquait).
``archive_and_purge_all_patients`` valide copie et suppression dans **une
seule transaction**, et la colonne unique ``patient_source_id`` interdit les
doublons même en cas de concurrence.
"""

from flask import current_app

from audit_log import ACTION_CLEAR, OUTCOME_FAILURE, OUTCOME_SUCCESS
from audit_service import record_audit
from communication import communikation
from init_restore import clear_counter_table
from models import Patient, PatientHistory, db
from routes.announce import refresh_announce_screens


def _post_purge_effects(deleted, archived=False):
    """Effets de bord communs après une purge validée : audit, puis
    rafraîchissement des clients web, écrans d'annonce et comptoirs."""
    current_app.logger.info("La table Patient a été vidée (%s lignes)", deleted)
    details = f"{deleted} patient(s) supprimé(s)"
    if archived:
        details += ", copiés dans l'historique"
    record_audit(ACTION_CLEAR, "queue", outcome=OUTCOME_SUCCESS,
                 details=details)
    communikation("update_patient")
    refresh_announce_screens()
    clear_counter_table()
    communikation("app_counter", event="refresh_after_clear_patient_list")


def purge_all_patients():
    """Vide la table Patient et synchronise les effets de bord.

    Enchaîne, comme le faisait l'ancienne vue :

    1. suppression de toutes les lignes ``Patient`` (commit) ;
    2. journal d'audit métier (succès) ;
    3. rafraîchissement de la file chez les clients web ;
    4. rechargement des écrans d'annonce ;
    5. remise à disposition des comptoirs ;
    6. notification des applications comptoir.

    Retourne le nombre de lignes supprimées. En cas d'échec : rollback,
    audit ``OUTCOME_FAILURE`` (tracé ici pour couvrir aussi l'exécution
    planifiée), puis relance l'exception d'origine — c'est l'appelant (vue
    HTTP ou tâche planifiée) qui choisit sa traduction (toast /
    JobExecutionLog).
    """
    try:
        deleted = db.session.query(Patient).delete()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        # L'audit d'échec vit dans le service (et non dans la vue) : la trace
        # est ainsi garantie aussi pour l'exécution planifiée.
        record_audit(ACTION_CLEAR, "queue", outcome=OUTCOME_FAILURE,
                     details=str(e))
        raise

    _post_purge_effects(deleted)
    return deleted


def archive_and_purge_all_patients():
    """Copie toute la file dans ``PatientHistory`` PUIS la vide — atomique.

    Une **seule transaction** stage les INSERT d'historique et le DELETE de
    la file : il n'existe plus d'état intermédiaire où la copie serait
    validée sans la suppression (ni l'inverse). Chaque ligne historique porte
    ``patient_source_id`` (unique) : une exécution concurrente ou relancée
    lève ``IntegrityError`` et fait tout échouer proprement, plutôt que de
    dupliquer les dossiers.

    Boucle ORM plutôt qu'un ``INSERT … SELECT`` : ``day_of_week`` est calculé
    en Python (``strftime('%A')``, nom anglais stable quel que soit le
    dialecte/locale SQL) — la transaction unique fournit déjà l'atomicité.

    Mêmes audit et effets de bord que :func:`purge_all_patients`. Retourne le
    nombre de lignes supprimées de la file.
    """
    try:
        for patient in Patient.query.all():
            db.session.add(PatientHistory(
                call_number=patient.call_number,
                timestamp=patient.timestamp,
                timestamp_counter=patient.timestamp_counter,
                timestamp_end=patient.timestamp_end,
                day_of_week=patient.timestamp.strftime('%A'),
                status=patient.status,
                counter_id=patient.counter_id,
                activity_id=patient.activity_id,
                overtaken=patient.overtaken,
                language_id=patient.language_id,
                patient_source_id=patient.id,
            ))
        deleted = db.session.query(Patient).delete()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_CLEAR, "queue", outcome=OUTCOME_FAILURE,
                     details=f"archivage+purge : {e}")
        raise

    _post_purge_effects(deleted, archived=True)
    return deleted
