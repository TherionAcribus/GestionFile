from flask import current_app as app
from sqlalchemy import inspect

def init_database(database, db):
    if database == "sqlite":
        db.create_all()
    elif database == "mysql":
        # Migrations are handled by `python manage.py migrate` during container startup.
        # Running Alembic migrations again inside the app startup is error-prone (and can
        # deadlock / race in multi-worker deployments).
        tables = set(inspect(db.engine).get_table_names())
        if "patient" not in tables:
            raise RuntimeError(
                "Database schema is not initialized (missing table 'patient'). "
                "Run `python manage.py migrate` before starting the app."
            )
        app.logger.info("Database schema detected (startup migrations skipped).")
