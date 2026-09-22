"""Service « file d'attente » — purge globale des patients.

Point audit : la purge vivait dans la VUE ``clear_all_patients_from_db`` de
``routes/admin_queue.py``, décorée par ``@require_permission`` :

* la variante « vider avec sauvegarde » l'appelait sans renvoyer sa réponse —
  la purge réussissait puis Flask renvoyait 500 (« view did not return ») ;
* le planificateur (``scheduler_functions.clear_all_patients_job``) appelait la
  même vue décorée : hors requête HTTP, ``current_user`` est indisponible — la
  tâche échouait avant d'atteindre la purge.

Le métier vit désormais ici, **sans décorateur ni réponse HTTP** : la route
protégée et la tâche planifiée appellent chacune ``purge_all_patients`` et
traduisent le résultat (toast / JobExecutionLog).
"""

from flask import current_app

from audit_log import ACTION_CLEAR, OUTCOME_FAILURE, OUTCOME_SUCCESS
from audit_service import record_audit
from communication import communikation
from init_restore import clear_counter_table
from models import Patient, db
from routes.announce import refresh_announce_screens


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

    current_app.logger.info("La table Patient a été vidée (%s lignes)", deleted)
    record_audit(ACTION_CLEAR, "queue", outcome=OUTCOME_SUCCESS,
                 details=f"{deleted} patient(s) supprimé(s)")
    communikation("update_patient")
    refresh_announce_screens()
    clear_counter_table()
    communikation("app_counter", event="refresh_after_clear_patient_list")
    return deleted
