"""Service « file d'attente » — purge des patients.

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
``archive_and_purge_*`` valide copie et suppression dans **une seule
transaction**, et la colonne unique ``patient_source_id`` interdit les
doublons même en cas de concurrence.

Troisième point audit : la purge de démarrage (``clear_old_patients_table``,
patients antérieurs à aujourd'hui) supprimait directement, sans
historisation ni audit. Les variantes ``*_old_patients`` ci-dessous partagent
le même cœur transactionnel : le chemin de démarrage respecte désormais
``CRON_TRANSFER_PATIENT_TO_HISTORY`` comme le job nocturne.
"""

from datetime import datetime

from flask import current_app

from config import time_tz

from audit_log import ACTION_CLEAR, OUTCOME_FAILURE, OUTCOME_SUCCESS
from audit_service import record_audit
from communication import communikation
from init_restore import clear_counter_table
from models import Patient, PatientHistory, PatientStep, db
from routes.announce import refresh_announce_screens


def record_patient_step(patient, outcome, new_activity_id=None, now=None):
    """Consigne la clôture de l'étape courante du parcours patient.

    À appeler AVANT de réécrire ``activity_id``/``counter_id`` sur la ligne
    ``Patient`` : c'est elle qui préserve le passage par l'activité et le
    comptoir précédents quand le patient est renvoyé en file, transféré ou
    retiré. La ligne est flushée avec la transaction de l'appelant (pas de
    commit ici : l'étape et la mutation du patient sont atomiques).
    """
    db.session.add(PatientStep(
        patient_id=patient.id,
        outcome=outcome,
        activity_id=patient.activity_id,
        new_activity_id=new_activity_id,
        counter_id=patient.counter_id,
        timestamp=now or datetime.now(time_tz),
        timestamp_counter=patient.timestamp_counter,
    ))


def _history_row_for(patient):
    """Ligne ``PatientHistory`` produite par un ``Patient`` de la file.

    ``patient_source_id`` (unique) sert de clé d'idempotence : un patient ne
    peut produire qu'une seule ligne d'historique.
    """
    return PatientHistory(
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
    )


def _post_purge_effects(deleted, label, archived=False):
    """Effets de bord communs après une purge validée : audit, puis
    rafraîchissement des clients web, écrans d'annonce et comptoirs."""
    current_app.logger.info("%s (%s lignes)", label, deleted)
    details = f"{deleted} patient(s) supprimé(s)"
    if archived:
        details += ", copiés dans l'historique"
    record_audit(ACTION_CLEAR, "queue", outcome=OUTCOME_SUCCESS,
                 details=details)
    communikation("update_patient")
    refresh_announce_screens()
    clear_counter_table()
    communikation("app_counter", event="refresh_after_clear_patient_list")


def _purge(query, label):
    """Supprime les patients de ``query``, sans archivage. Commit unique."""
    try:
        deleted = query.delete(synchronize_session=False)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        # L'audit d'échec vit dans le service (et non dans la vue) : la trace
        # est ainsi garantie aussi pour l'exécution planifiée.
        record_audit(ACTION_CLEAR, "queue", outcome=OUTCOME_FAILURE,
                     details=f"{label} : {e}")
        raise
    _post_purge_effects(deleted, label)
    return deleted


def _archive_and_purge(query, label):
    """Copie les patients de ``query`` dans ``PatientHistory`` PUIS les
    supprime — **une seule transaction** pour les deux.

    Plus d'état intermédiaire où la copie serait validée sans la suppression
    (ni l'inverse), et ``patient_source_id`` unique transforme toute
    exécution concurrente ou relancée en échec propre plutôt qu'en doublons.

    Boucle ORM plutôt qu'un ``INSERT … SELECT`` : ``day_of_week`` est calculé
    en Python (``strftime('%A')``, nom anglais stable quel que soit le
    dialecte/locale SQL) — la transaction unique fournit déjà l'atomicité.
    """
    try:
        for patient in query.all():
            db.session.add(_history_row_for(patient))
        deleted = query.delete(synchronize_session=False)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_CLEAR, "queue", outcome=OUTCOME_FAILURE,
                     details=f"{label} : {e}")
        raise
    _post_purge_effects(deleted, label, archived=True)
    return deleted


def purge_all_patients():
    """Vide la table Patient et synchronise les effets de bord.

    Retourne le nombre de lignes supprimées. En cas d'échec : rollback,
    audit ``OUTCOME_FAILURE`` (tracé ici pour couvrir aussi l'exécution
    planifiée), puis relance l'exception d'origine — c'est l'appelant (vue
    HTTP ou tâche planifiée) qui choisit sa traduction (toast /
    JobExecutionLog).
    """
    return _purge(db.session.query(Patient), "La table Patient a été vidée")


def archive_and_purge_all_patients():
    """Copie toute la file dans ``PatientHistory`` PUIS la vide — atomique.

    Mêmes audit et effets de bord que :func:`purge_all_patients`. Retourne le
    nombre de lignes supprimées de la file.
    """
    return _archive_and_purge(
        db.session.query(Patient), "La table Patient a été vidée")


def purge_old_patients(today):
    """Supprime les patients antérieurs à ``today`` (date), sans archivage.

    Chemin de la purge de démarrage : passe par le même service que le job
    nocturne — audit et rollback garantis.
    """
    return _purge(
        db.session.query(Patient).filter(Patient.timestamp < today),
        f"Purge des patients antérieurs à {today}")


def archive_and_purge_old_patients(today):
    """Copie dans l'historique puis supprime les patients antérieurs à
    ``today`` — atomique, même contrat que :func:`archive_and_purge_all_patients`.
    """
    return _archive_and_purge(
        db.session.query(Patient).filter(Patient.timestamp < today),
        f"Archivage+purge des patients antérieurs à {today}")
