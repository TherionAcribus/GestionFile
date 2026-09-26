"""Point 5.3 — assemblage des infos du tableau de bord « Planifications ».

Centralise la construction de la liste des tâches du scheduler affichée par la
carte de tableau de bord. Objectif perf : ne plus émettre **une requête SQL par
tâche** pour retrouver sa dernière exécution (N+1), mais **une seule requête
groupée** couvrant l'ensemble des tâches (motif « greatest-n-per-group »).

La page « Tâches planifiées » (``/admin/database/schedule_tasks_list``) réutilise
le même assembleur avec ``per_job`` > 1 et ``detailed=True`` : une requête
fenêtrée unique (ROW_NUMBER) pour les N dernières exécutions, plus les champs
d'affichage lisibles (libellé, planification traduite, statuts traduits) —
l'ancienne page affichait le nom de la fonction Python, le déclencheur brut
(``cron[...]``), ``coalesce``/``max_instances`` et colorait « skipped » comme
une erreur.

Ce module remplace la logique jusque-là dupliquée entre la route
``/admin/appschedule/dashboard`` (app.py) et la reconstruction des cartes dans
``save_dashboard_configuration`` (routes/admin_dashboard.py) — cette dernière
référençait de surcroît un modèle ``SchedulerLog`` inexistant.

Les imports de modèles sont faits à l'intérieur des fonctions pour éviter toute
dépendance circulaire à l'import du module.
"""

import re

# Tâches « principales » mises en avant hors accordéon.
MAIN_JOBS = ('Clear Patient Table', 'Clear Announce Calls')

# Libellés lisibles des tâches système (affichage administrateur : plus de nom
# de fonction Python ni d'identifiant technique seul).
_JOB_LABELS = {
    'Clear Patient Table': "Vidage quotidien de la file de patients",
    'Clear Announce Calls': "Purge quotidienne des appels annoncés",
    'Auto Archive Data': "Archivage automatique de l'historique",
    'Purge App Messaging': "Nettoyage quotidien de la messagerie interne",
    'Scheduler Heartbeat': "Synchronisation du planificateur",
}

_WEEKDAYS_FR = {
    'mon': 'lundi', 'tue': 'mardi', 'wed': 'mercredi', 'thu': 'jeudi',
    'fri': 'vendredi', 'sat': 'samedi', 'sun': 'dimanche',
}

# Jobs d'activité : enable_{activity_id}_{jour}_{HHMM} (routes/admin_activity).
_ACTIVITY_JOB_RE = re.compile(r'^(enable|disable)_(\d+)_([a-z]{3})_(\d{2})(\d{2})$')

# Statut d'exécution -> (libellé, classe CSS). « skipped » n'est PAS une erreur :
# le job a volontairement sauté son passage (option désactivée) — affichage en
# avertissement discret, pas en rouge.
_EXECUTION_STATUSES = {
    'success': ('Réussie', 'text-success'),
    'skipped': ('Ignorée', 'text-warning'),
    'failed': ('Échouée', 'text-danger'),
}


def execution_status_display(status):
    """``(libellé français, classe CSS)`` d'un statut d'exécution."""
    return _EXECUTION_STATUSES.get(status, (status or '—', 'text-muted'))


def describe_job(job_id, activity_names=None):
    """Libellé lisible d'une tâche planifiée.

    Tâche système connue -> libellé métier ; job d'activité
    ``enable_{id}_{jour}_{HHMM}`` -> « Ouverture de « Nom » (lundi à 09:00) » ;
    sinon l'identifiant tel quel.
    """
    if job_id in _JOB_LABELS:
        return _JOB_LABELS[job_id]
    match = _ACTIVITY_JOB_RE.match(job_id or '')
    if match:
        verb = "Ouverture" if match.group(1) == 'enable' else "Fermeture"
        name = (activity_names or {}).get(
            int(match.group(2)), f"activité {match.group(2)}")
        day = _WEEKDAYS_FR.get(match.group(3), match.group(3))
        return (f"{verb} de « {name} » "
                f"({day} à {match.group(4)}:{match.group(5)})")
    return job_id


def describe_trigger(trigger):
    """Description lisible d'un déclencheur APScheduler.

    ``cron[day_of_week='mon', hour='9', minute='0']`` -> « chaque lundi à
    09:00 » ; ``interval[0:01:00]`` -> « chaque minute ». Les formes non
    reconnues retombent sur la représentation brute.
    """
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    if isinstance(trigger, CronTrigger):
        fields = {field.name: str(field) for field in trigger.fields}
        hour, minute = fields.get('hour', '*'), fields.get('minute', '*')
        moment = ("{:02d}:{:02d}".format(int(hour), int(minute))
                  if hour.isdigit() and minute.isdigit()
                  else f"{hour}:{minute}")
        days = fields.get('day_of_week', '*')
        if days == '*':
            return f"tous les jours à {moment}"
        names = [_WEEKDAYS_FR.get(day, day) for day in days.split(',')]
        if len(names) == 1:
            return f"chaque {names[0]} à {moment}"
        return f"les {', '.join(names[:-1])} et {names[-1]} à {moment}"
    if isinstance(trigger, IntervalTrigger):
        seconds = trigger.interval.total_seconds()
        if seconds >= 3600 and seconds % 3600 == 0:
            hours = int(seconds // 3600)
            return f"toutes les {hours} heure" + ("s" if hours > 1 else "")
        if seconds == 60:
            return "chaque minute"
        if seconds > 60 and seconds % 60 == 0:
            return f"toutes les {int(seconds // 60)} minutes"
        return f"toutes les {seconds:g} secondes"
    return str(trigger) if trigger is not None else ""


def latest_execution_by_job(job_ids):
    """Dernière exécution de chaque job en **une seule** requête.

    Renvoie ``{job_id: JobExecutionLog}``. Sans ``job_ids`` -> dict vide (évite
    un ``IN ()`` inutile).
    """
    from sqlalchemy import func
    from models import db, JobExecutionLog

    job_ids = list(job_ids)
    if not job_ids:
        return {}

    # max(execution_time) par job_id…
    latest_time = (
        db.session.query(
            JobExecutionLog.job_id.label('job_id'),
            func.max(JobExecutionLog.execution_time).label('max_time'),
        )
        .filter(JobExecutionLog.job_id.in_(job_ids))
        .group_by(JobExecutionLog.job_id)
        .subquery()
    )
    # …puis on récupère la ligne complète correspondante.
    rows = (
        db.session.query(JobExecutionLog)
        .join(
            latest_time,
            (JobExecutionLog.job_id == latest_time.c.job_id)
            & (JobExecutionLog.execution_time == latest_time.c.max_time),
        )
        .all()
    )
    return {row.job_id: row for row in rows}


def recent_executions_by_job(job_ids, per_job=5):
    """Les ``per_job`` dernières exécutions de chaque job en **une** requête.

    Même objectif que ``latest_execution_by_job`` (fin du N+1 de la page
    « Tâches planifiées ») mais pour l'historique récent : une fonction fenêtre
    ``ROW_NUMBER()`` classe les exécutions par tâche (MySQL 8, MariaDB 10.2+,
    SQLite) puis on ne conserve que les ``per_job`` premières de chacune.

    Renvoie ``{job_id: [JobExecutionLog, ...]}`` ordonné du plus récent au plus
    ancien.
    """
    from sqlalchemy import func
    from models import db, JobExecutionLog

    job_ids = list(job_ids)
    if not job_ids:
        return {}

    ranked = (
        db.session.query(
            JobExecutionLog.id.label('log_id'),
            func.row_number().over(
                partition_by=JobExecutionLog.job_id,
                order_by=JobExecutionLog.execution_time.desc(),
            ).label('rn'),
        )
        .filter(JobExecutionLog.job_id.in_(job_ids))
        .subquery()
    )
    rows = (
        db.session.query(JobExecutionLog)
        .join(ranked, JobExecutionLog.id == ranked.c.log_id)
        .filter(ranked.c.rn <= per_job)
        .order_by(JobExecutionLog.execution_time.desc())
        .all()
    )
    executions = {}
    for row in rows:
        executions.setdefault(row.job_id, []).append(row)
    return executions


def _activity_names(jobs):
    """``{activity_id: nom}`` des activités référencées par les jobs
    ``enable_{id}_*``/``disable_{id}_*`` — une seule requête pour tous les
    libellés de tâches d'activité."""
    from models import Activity

    ids = {int(match.group(2)) for job in jobs
           for match in [_ACTIVITY_JOB_RE.match(job.id)] if match}
    if not ids:
        return {}
    return {activity.id: activity.name
            for activity in Activity.query.filter(Activity.id.in_(ids))}


def _execution_entry(log, detailed=False):
    """Forme un dictionnaire d'exécution pour les gabarits.

    ``detailed`` ajoute le statut traduit (``status_label``/``status_class``) et
    l'horodatage en heure de Paris — c'est la forme consommée par la liste des
    ``last_executions`` ; ``last_execution`` garde la forme historique à trois
    champs utilisée par la carte de tableau de bord.
    """
    entry = {
        'time': log.local_time if detailed else log.execution_time,
        'status': log.status,
        'error': log.error_message,
    }
    if detailed:
        entry['status_label'], entry['status_class'] = \
            execution_status_display(log.status)
    return entry


def build_jobs_info(jobs, per_job=1, detailed=False):
    """Sépare ``jobs`` en ``(main_jobs_info, other_jobs_info)``.

    ``jobs`` : itérable d'objets APScheduler (attributs ``.id``,
    ``.next_run_time`` et, si ``detailed``, ``.trigger``). Quel que soit
    ``per_job``, **une seule requête SQL** est émise pour les exécutions —
    ``latest_execution_by_job`` pour la dernière seulement,
    ``recent_executions_by_job`` sinon.

    ``detailed=True`` ajoute les champs de la page « Tâches planifiées » :
    ``label`` (libellé lisible), ``schedule`` (déclencheur traduit) et
    ``last_executions`` (statuts traduits, heure locale).
    """
    jobs = list(jobs)
    if per_job <= 1:
        executions = {job_id: [log] for job_id, log in
                      latest_execution_by_job(job.id for job in jobs).items()}
    else:
        executions = recent_executions_by_job(
            (job.id for job in jobs), per_job=per_job)
    activity_names = _activity_names(jobs) if detailed else {}

    main_jobs_info = []
    other_jobs_info = []
    for job in jobs:
        logs = executions.get(job.id, [])
        job_info = {
            'id': job.id,
            'next_run_time': job.next_run_time,
            'last_execution': _execution_entry(logs[0]) if logs else None,
        }
        if detailed:
            job_info['label'] = describe_job(job.id, activity_names)
            job_info['schedule'] = describe_trigger(
                getattr(job, 'trigger', None))
            job_info['last_executions'] = [
                _execution_entry(log, detailed=True) for log in logs]
        if job.id in MAIN_JOBS:
            main_jobs_info.append(job_info)
        else:
            other_jobs_info.append(job_info)

    return main_jobs_info, other_jobs_info
