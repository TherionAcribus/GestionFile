"""Point 5.3 — assemblage des infos du tableau de bord « Planifications ».

Centralise la construction de la liste des tâches du scheduler affichée par la
carte de tableau de bord. Objectif perf : ne plus émettre **une requête SQL par
tâche** pour retrouver sa dernière exécution (N+1), mais **une seule requête
groupée** couvrant l'ensemble des tâches (motif « greatest-n-per-group »).

Ce module remplace la logique jusque-là dupliquée entre la route
``/admin/appschedule/dashboard`` (app.py) et la reconstruction des cartes dans
``save_dashboard_configuration`` (routes/admin_dashboard.py) — cette dernière
référençait de surcroît un modèle ``SchedulerLog`` inexistant.

Les imports de modèles sont faits à l'intérieur des fonctions pour éviter toute
dépendance circulaire à l'import du module.
"""

# Tâches « principales » mises en avant hors accordéon.
MAIN_JOBS = ('Clear Patient Table', 'Clear Announce Calls')


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


def build_jobs_info(jobs):
    """Sépare ``jobs`` en ``(main_jobs_info, other_jobs_info)``.

    ``jobs`` : itérable d'objets APScheduler (attributs ``.id`` et
    ``.next_run_time``). Une seule requête SQL est émise pour l'ensemble des
    dernières exécutions, quel que soit le nombre de tâches.
    """
    jobs = list(jobs)
    latest = latest_execution_by_job(job.id for job in jobs)

    main_jobs_info = []
    other_jobs_info = []
    for job in jobs:
        last = latest.get(job.id)
        job_info = {
            'id': job.id,
            'next_run_time': job.next_run_time,
            'last_execution': {
                'time': last.execution_time,
                'status': last.status,
                'error': last.error_message,
            } if last else None,
        }
        if job.id in MAIN_JOBS:
            main_jobs_info.append(job_info)
        else:
            other_jobs_info.append(job_info)

    return main_jobs_info, other_jobs_info
