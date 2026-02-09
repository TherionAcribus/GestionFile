import argparse
import os
import sys


TRUE_VALUES = {"1", "true", "yes", "on"}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUE_VALUES


def _ensure_env(name: str, value: str) -> None:
    # Only set defaults for the current process (do not leak to the app runtime
    # that starts after this script in the same shell).
    if os.getenv(name) is None:
        os.environ[name] = value


def _acquire_mysql_lock(engine) -> object | None:
    """
    Serialize concurrent migrations on MySQL using GET_LOCK.

    Important: the lock is held by the connection, so we keep it open until the
    migrations/bootstrapping are done.
    """
    if getattr(engine.dialect, "name", "") != "mysql":
        return None

    lock_name = os.getenv("MIGRATION_LOCK_NAME", "gestionfile_migrations")
    try:
        timeout = int(os.getenv("MIGRATION_LOCK_TIMEOUT", "120"))
    except ValueError:
        timeout = 120

    conn = engine.connect()
    got = conn.exec_driver_sql("SELECT GET_LOCK(%s, %s)", (lock_name, timeout)).scalar()
    if got != 1:
        conn.close()
        raise RuntimeError(
            f"Could not acquire MySQL migration lock {lock_name!r} within {timeout}s."
        )
    return conn


def _release_mysql_lock(conn: object | None) -> None:
    if conn is None:
        return
    lock_name = os.getenv("MIGRATION_LOCK_NAME", "gestionfile_migrations")
    try:
        conn.exec_driver_sql("SELECT RELEASE_LOCK(%s)", (lock_name,))
    finally:
        conn.close()


def migrate() -> None:
    # Avoid side effects during app import (startup hooks + eventlet monkey patch).
    _ensure_env("SKIP_STARTUP_HOOKS", "1")
    _ensure_env("SKIP_EVENTLET_PATCH", "1")

    from sqlalchemy import inspect

    from app import app, db  # pylint: disable=import-error
    from flask_migrate import stamp, upgrade

    force_bootstrap = _truthy(os.getenv("FORCE_BOOTSTRAP_DB"))

    with app.app_context():
        lock_conn = None
        try:
            lock_conn = _acquire_mysql_lock(db.engine)

            inspector = inspect(db.engine)
            tables = set(inspector.get_table_names())

            # If a previous `alembic upgrade` attempt failed early, Alembic may have
            # created `alembic_version` even though no app tables exist yet.
            # In that case, treat the DB as empty and bootstrap from models.
            has_patient = "patient" in tables
            if not has_patient:
                if not tables or tables.issubset({"alembic_version"}) or force_bootstrap:
                    db.create_all()
                    stamp(revision="head")
                    return
                raise RuntimeError(
                    "Database has tables but no 'patient' table. Refusing to bootstrap automatically. "
                    "Use a new/empty database, or set FORCE_BOOTSTRAP_DB=1 if you intend to bootstrap "
                    "from current models and stamp Alembic head."
                )

            if "alembic_version" in tables:
                upgrade()
                return

            if tables and not force_bootstrap:
                raise RuntimeError(
                    "Database already contains tables but has no alembic_version table. "
                    "Refusing to bootstrap automatically. If this is a fresh install, "
                    "use a new/empty database. If you know what you are doing, set "
                    "FORCE_BOOTSTRAP_DB=1 to run db.create_all() and stamp head."
                )

            # Fresh install: current migrations do not contain a true baseline that
            # creates all tables. For new deployments we bootstrap from current models
            # and stamp Alembic to head.
            db.create_all()
            stamp(revision="head")
        finally:
            _release_mysql_lock(lock_conn)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="manage.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="Bootstrap/upgrade database schema safely.")
    args = parser.parse_args(argv)

    if args.cmd == "migrate":
        migrate()
        return 0

    parser.error(f"Unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
