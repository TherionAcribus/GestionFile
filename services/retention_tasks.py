"""Tâche de fond pour l'archivage / la purge manuels de l'historique.

Point audit : ces opérations s'exécutaient **dans la requête web** — sur un
gros historique, risque de timeout HTTP et de relance concurrente (double
exécution en parallèle). Elles tournent désormais dans un thread dédié :

* **verrou double** — ``threading.Lock`` pour le processus courant et une
  ligne ``idempotency_key`` (clé primaire) comme verrou inter-processus
  (répliques web, scheduler) ;
* **progression** — chaque journée commitée met à jour un état exposé par
  ``GET /admin/data/task`` (journées faites/total, lignes, jour courant,
  détail borné aux 50 dernières journées) ;
* **traçabilité** — audit + ``JobExecutionLog`` à l'issue ; un échec après
  journées partielles reste explicite via ``PartialHistoryError``.
"""

import threading
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from audit_log import (
    ACTION_ARCHIVE, ACTION_DELETE, OUTCOME_FAILURE, OUTCOME_SUCCESS,
)
from audit_service import record_audit
from config import time_tz
from models import IdempotencyKey, db
from scheduler_functions import (
    PartialHistoryError, _record_job_execution, aggregate_history,
    count_history_before, purge_history,
)

# Ligne idempotency_key utilisée comme verrou inter-processus (sa PK rend
# l'acquisition atomique : deux processus ne peuvent pas l'insérer tous deux).
_LOCK_KEY = 'retention_task_lock'
# Au-delà, un verrou résiduel est considéré comme celui d'un processus mort.
_LOCK_STALE_AFTER = timedelta(hours=24)
# Détail par journée borné : un premier archivage peut traiter des années.
_MAX_RECENT_DAYS = 50

_task_lock = threading.Lock()
_state = {}

_OPERATIONS = {
    'archive': (aggregate_history, ACTION_ARCHIVE, 'Manual Archive'),
    'purge': (purge_history, ACTION_DELETE, 'Manual Purge'),
}


def _as_aware(dt):
    """Les timestamps SQLite reviennent naïfs ; on les rattache à time_tz."""
    return dt.replace(tzinfo=time_tz) if dt.tzinfo is None else dt


def _acquire_db_lock():
    """Réserve le verrou inter-processus. ``False`` s'il est déjà détenu."""
    row = IdempotencyKey.query.get(_LOCK_KEY)
    if row is not None:
        age = datetime.now(time_tz) - _as_aware(row.created_at)
        if age < _LOCK_STALE_AFTER:
            return False
        # Verrou résiduel d'un processus interrompu : on le reprend.
        db.session.delete(row)
        db.session.commit()
    db.session.add(IdempotencyKey(key=_LOCK_KEY))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return False
    return True


def _release_db_lock():
    row = IdempotencyKey.query.get(_LOCK_KEY)
    if row is not None:
        db.session.delete(row)
        db.session.commit()


def _reset_state(operation, days, backup):
    global _state
    _state = {
        'status': 'running',
        'operation': operation,
        'days': days,
        'backup': backup,
        'started_at': datetime.now(time_tz).isoformat(),
        'days_total': None,   # rempli au démarrage du worker
        'days_done': 0,
        'rows_done': 0,
        'current_day': None,
        'recent_days': [],
        'result': None,
        'error': None,
        'partial': False,
    }


def retention_task_state():
    """État courant pour le polling ``GET /admin/data/task``.

    Si le verrou DB est détenu sans tâche locale, l'opération tourne dans un
    autre processus — on le signale plutôt que de prétendre « idle ».
    """
    state = dict(_state) if _state else {'status': 'idle'}
    if state.get('status') == 'idle':
        try:
            if IdempotencyKey.query.get(_LOCK_KEY) is not None:
                state = {'status': 'running', 'external': True}
        except Exception:
            db.session.rollback()
    return state


def _spawn_worker(target, args, name):
    """Démarre le worker dédié. Isolé pour que les tests puissent le rendre
    synchrone sans monkey-patcher ``threading`` (module partagé)."""
    threading.Thread(target=target, args=args, name=name, daemon=True).start()


def start_retention_task(app, operation, days, backup=False):
    """Lance l'opération en tâche de fond. ``(démarré, état)`` — ``False`` si
    une opération de rétention est déjà en cours (quel que soit le processus).
    """
    func, _, _ = _OPERATIONS[operation]

    if not _task_lock.acquire(blocking=False):
        return False, retention_task_state()
    try:
        if not _acquire_db_lock():
            return False, retention_task_state()
        _reset_state(operation, days, backup)
        _spawn_worker(_run, (app, func, operation, days, backup),
                      f'retention-{operation}')
        return True, retention_task_state()
    except Exception:
        try:
            _release_db_lock()
        except Exception:
            db.session.rollback()
        raise
    finally:
        _task_lock.release()


def _run(app, func, operation, days, backup):
    """Worker : contexte applicatif, progression par journée, issue tracée."""
    _, action, job_id = _OPERATIONS[operation]

    def progress(process_date, rows, days_done):
        _state['days_done'] = days_done
        _state['rows_done'] += rows
        _state['current_day'] = str(process_date)
        recent = _state['recent_days']
        recent.append({'date': str(process_date), 'rows': rows})
        del recent[:-_MAX_RECENT_DAYS]

    with app.app_context():
        try:
            _state['days_total'] = count_history_before(days)['days']
            result = func(days, export_csv=backup, progress=progress)
            _state['status'] = 'done'
            _state['result'] = result
            record_audit(action, 'patient_history', outcome=OUTCOME_SUCCESS,
                         details=f"{operation} manuel en tâche de fond "
                                 f">{days}j, export_csv={backup} : {result}")
            _record_job_execution(job_id, 'success', result)
        except PartialHistoryError as e:
            # Journées déjà commitées : résultat partiel, présenté comme tel.
            _state.update(status='failed', partial=True, error=str(e))
            record_audit(action, 'patient_history', outcome=OUTCOME_FAILURE,
                         details=str(e))
            _record_job_execution(job_id, 'failed', str(e))
        except Exception as e:
            _state.update(status='failed', error=str(e))
            record_audit(action, 'patient_history', outcome=OUTCOME_FAILURE,
                         details=f"{operation} manuel >{days}j : {e}")
            _record_job_execution(job_id, 'failed', str(e))
            app.logger.exception("Tâche de rétention '%s' en échec", operation)
        finally:
            try:
                _release_db_lock()
            except Exception:
                db.session.rollback()
                app.logger.exception(
                    "Libération du verrou de rétention impossible")
            db.session.remove()
