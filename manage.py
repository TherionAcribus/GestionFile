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


def _say(message: str = "") -> None:
    """Sortie destinée à l'opérateur du terminal (commandes de secours).

    Volontairement PAS app.logger : le mot de passe temporaire affiché ne doit
    jamais atterrir dans les journaux du conteneur. Les actions elles-mêmes
    sont journalisées (sans secret) par record_audit.
    """
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def _print_accounts() -> None:
    from services.account_recovery_service import list_accounts

    accounts = list_accounts()
    if not accounts:
        _say("Aucun compte n'existe.")
        return
    _say("Comptes existants :")
    for user in accounts:
        roles = ", ".join(r.name for r in user.roles) or "aucun rôle"
        state = "" if user.active else " (désactivé)"
        _say(f"  - {user.username}{state} — {roles}")


def list_users() -> int:
    """Liste les comptes (aide à choisir celui à réinitialiser)."""
    _ensure_env("SKIP_STARTUP_HOOKS", "1")
    _ensure_env("SKIP_EVENTLET_PATCH", "1")
    from app import app  # pylint: disable=import-error

    with app.app_context():
        _print_accounts()
    return 0


def reset_password(username: str | None, password: str | None, make_admin: bool) -> int:
    """Commande de secours : mot de passe perdu (account_recovery.py).

    À lancer dans le conteneur « web » (Coolify : onglet Terminal) :
        python manage.py reset-password admin
    """
    _ensure_env("SKIP_STARTUP_HOOKS", "1")
    _ensure_env("SKIP_EVENTLET_PATCH", "1")
    from app import app  # pylint: disable=import-error
    from services.account_recovery_service import (
        UnknownUserError,
        WeakPasswordError,
        reset_password_from_cli,
    )

    with app.app_context():
        if not username:
            _say("Indiquez le compte à réinitialiser : python manage.py reset-password <identifiant>")
            _print_accounts()
            return 2
        try:
            new_password, created = reset_password_from_cli(
                username, password=password, make_admin=make_admin)
        except UnknownUserError:
            _say(f"Le compte {username!r} n'existe pas.")
            _print_accounts()
            _say("Pour créer ce compte avec tous les droits : ajoutez --admin")
            return 1
        except WeakPasswordError as exc:
            _say(f"Mot de passe refusé : {exc}")
            return 1

    _say()
    _say(f"Compte {username!r} {'créé' if created else 'réinitialisé'}"
          f"{' avec le rôle admin' if make_admin else ''}.")
    if password:
        _say("Mot de passe : celui que vous avez indiqué.")
    else:
        _say(f"Mot de passe temporaire : {new_password}")
    _say("Il devra être changé à la prochaine connexion. Les sessions ouvertes "
          "de ce compte sont déconnectées.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="manage.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="Bootstrap/upgrade database schema safely.")
    sub.add_parser("list-users", help="Liste les comptes utilisateurs.")
    reset = sub.add_parser(
        "reset-password",
        help="Mot de passe perdu : pose un mot de passe temporaire sur un compte.")
    reset.add_argument("username", nargs="?", help="Identifiant du compte (ex. admin).")
    reset.add_argument("--password", help="Mot de passe à poser (sinon un mot de passe "
                                          "temporaire est généré et affiché).")
    reset.add_argument("--admin", action="store_true",
                       help="Réactive le compte et lui rend le rôle admin "
                            "(le crée s'il n'existe pas).")
    args = parser.parse_args(argv)

    if args.cmd == "migrate":
        migrate()
        return 0
    if args.cmd == "list-users":
        return list_users()
    if args.cmd == "reset-password":
        return reset_password(args.username, args.password, args.admin)

    parser.error(f"Unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
