"""Point 11 — Synchronisation des paramètres de configuration entre processus.

Contexte
--------
Le déploiement peut comporter plusieurs processus : un ou plusieurs processus
« web » (répliques) **et** un processus « scheduler » (``APP_ROLE``). Chacun
appelle ``load_configuration(app)`` **une seule fois** au démarrage, ce qui
recopie la table ``ConfigOption`` dans ``app.config`` (mémoire du processus).

Une modification via ``/admin/update_switch`` / ``update_input`` /
``update_select`` n'écrit qu'en base **et** dans la mémoire du **seul** processus
qui reçoit la requête. Les autres répliques web et le scheduler conservent alors
une copie périmée de ``app.config`` (nom de pharmacie, textes, options crons
lues à l'exécution des tâches, etc.).

Mécanisme retenu (pragmatique, sans infrastructure supplémentaire)
------------------------------------------------------------------
La base de données est la **source de vérité**. On y maintient un *compteur de
génération* : une ligne ``ConfigOption`` réservée (``__config_generation__``,
hors registre et hors ``CONFIG_MAPPINGS``) dont ``value_int`` est incrémenté à
chaque écriture de configuration, **dans la même transaction** que le
changement (atomicité).

Chaque processus mémorise la génération qu'il a chargée
(``app._config_generation``) et, à intervalle borné, la compare à celle de la
base :

* côté **web** : dans un ``before_request`` *throttlé* (au plus une lecture
  mono-ligne toutes les ``CONFIG_SYNC_MIN_INTERVAL`` secondes) ;
* côté **scheduler** : au début de **chaque** tâche planifiée (``force=True``),
  car ce processus ne sert aucune requête HTTP.

Si la génération a changé, le processus relance ``load_configuration(app)`` pour
repeupler ``app.config`` depuis la base. Tant que rien ne bouge, une seule
lecture d'une ligne indexée suffit : aucun rechargement complet.

Incrément **atomique au niveau SQL** (``value_int = value_int + 1``) : deux
répliques qui écrivent simultanément produisent deux incréments distincts (pas
de mise à jour perdue), et il suffit que la valeur *change* pour déclencher le
rechargement — la valeur exacte n'a pas d'importance.

Paramètres nécessitant un redémarrage
-------------------------------------
Les clés marquées ``restart_required`` dans ``params_registry`` (ex.
``start_rabbitmq``) sont consommées à l'**initialisation** du processus
(file de messages SocketIO). Un simple rechargement de ``app.config`` ne les
appliquerait pas ; pire, il rendrait la mémoire incohérente avec ce qui tourne
réellement. Pour celles-ci, on **n'incrémente pas** la génération et on **ne
mute pas** ``app.config`` : la base porte l'intention, appliquée au prochain
redémarrage, et l'interface affiche « Enregistré — redémarrage requis ».
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


#: Clé réservée stockant le compteur de génération dans ``ConfigOption``.
#: Le préfixe ``__`` la distingue des clés métier et permet de l'exclure des
#: sauvegardes (cf. ``backup_service``) et du chargement (elle n'est pas dans
#: ``CONFIG_MAPPINGS``).
CONFIG_GENERATION_KEY = "__config_generation__"

#: Intervalle minimal (secondes) entre deux vérifications de génération par
#: processus web, afin de borner la charge en base même sous fort trafic.
DEFAULT_MIN_INTERVAL = 2.0

#: Message affiché après l'enregistrement d'un paramètre nécessitant un
#: redémarrage du serveur pour prendre effet.
RESTART_REQUIRED_MESSAGE = (
    "Enregistré — redémarrage du serveur requis pour appliquer ce changement."
)


def is_reserved_key(key) -> bool:
    """``True`` si ``key`` est une clé technique interne (jamais métier).

    Sert à exclure la ligne de génération des exports/restaurations de
    sauvegarde et de toute itération sur les clés de configuration.
    """
    return isinstance(key, str) and key.startswith("__")


# ---------------------------------------------------------------------------
# Fonctions pures (testables sans base ni Flask)
# ---------------------------------------------------------------------------

def should_check_now(last_check, now, min_interval) -> bool:
    """Faut-il interroger la base maintenant (throttle) ?

    ``last_check``/``now`` sont des instants monotones (secondes) ; une première
    vérification (``last_check is None``) est toujours autorisée.
    """
    if last_check is None:
        return True
    return (now - last_check) >= min_interval


def should_reload(cached_generation, db_generation) -> bool:
    """Faut-il recharger ``app.config`` ?

    On recharge si le processus n'a pas encore mémorisé de génération, ou si
    celle de la base diffère de celle en mémoire.
    """
    if cached_generation is None:
        return True
    return db_generation != cached_generation


# ---------------------------------------------------------------------------
# Accès base (import paresseux de ``models`` pour rester testable isolément)
# ---------------------------------------------------------------------------

def _models():
    from models import db, ConfigOption
    return db, ConfigOption


def ensure_generation_row(db=None, ConfigOption=None) -> None:
    """Crée la ligne de génération (à 0) si elle n'existe pas encore.

    Appelée à l'amorçage (``run_bootstrap``) pour que la toute première écriture
    de configuration se contente d'incrémenter une ligne existante — évitant une
    éventuelle insertion concurrente (violation d'unicité) entre répliques.
    Committe elle-même (opération d'initialisation autonome).
    """
    if db is None or ConfigOption is None:
        _db, _CO = _models()
        db = db if db is not None else _db
        ConfigOption = ConfigOption if ConfigOption is not None else _CO
    existing = ConfigOption.query.filter_by(config_key=CONFIG_GENERATION_KEY).first()
    if existing is None:
        db.session.add(ConfigOption(config_key=CONFIG_GENERATION_KEY, value_int=0))
        db.session.commit()


def read_generation(ConfigOption=None) -> int:
    """Lit le compteur de génération en base (0 si la ligne n'existe pas)."""
    if ConfigOption is None:
        _, ConfigOption = _models()
    row = ConfigOption.query.filter_by(config_key=CONFIG_GENERATION_KEY).first()
    if row is None or row.value_int is None:
        return 0
    return int(row.value_int)


def bump_generation(db=None, ConfigOption=None) -> None:
    """Incrémente le compteur de génération **dans la transaction courante**.

    L'écriture est *stagée* : c'est l'appelant qui exécute ``commit()``, de
    sorte que l'incrément soit atomique avec le changement de configuration
    (même transaction). L'incrément est réalisé au niveau SQL
    (``value_int + 1``) pour éviter les mises à jour perdues entre répliques
    concurrentes.
    """
    from sqlalchemy import func

    if db is None or ConfigOption is None:
        _db, _CO = _models()
        db = db if db is not None else _db
        ConfigOption = ConfigOption if ConfigOption is not None else _CO

    updated = (
        ConfigOption.query
        .filter_by(config_key=CONFIG_GENERATION_KEY)
        .update(
            {ConfigOption.value_int: func.coalesce(ConfigOption.value_int, 0) + 1},
            synchronize_session=False,
        )
    )
    if not updated:
        # Première écriture : la ligne n'existe pas encore.
        db.session.add(ConfigOption(config_key=CONFIG_GENERATION_KEY, value_int=1))


# ---------------------------------------------------------------------------
# Orchestration côté processus
# ---------------------------------------------------------------------------

def mark_current_generation(app, ConfigOption=None) -> int:
    """Mémorise sur ``app`` la génération actuellement chargée.

    À appeler juste après un ``load_configuration(app)`` réussi (démarrage) pour
    éviter un rechargement inutile à la première requête.
    """
    try:
        gen = read_generation(ConfigOption)
    except Exception:
        logger.warning(
            "Lecture initiale de la génération de configuration impossible.",
            exc_info=True,
        )
        gen = getattr(app, "_config_generation", 0) or 0
    app._config_generation = gen
    app._config_gen_last_check = time.monotonic()
    return gen


def maybe_reload_configuration(
    app,
    *,
    force=False,
    min_interval=None,
    _now=None,
    _read_generation=None,
    _load_configuration=None,
) -> bool:
    """Recharge ``app.config`` si un autre processus a modifié la configuration.

    Renvoie ``True`` si un rechargement a effectivement eu lieu. Ne lève jamais :
    en cas d'erreur de base, la configuration en mémoire est conservée (la
    requête/tâche courante continue avec l'ancienne valeur plutôt que d'échouer).

    Les paramètres ``_now`` / ``_read_generation`` / ``_load_configuration`` sont
    des points d'injection réservés aux tests.
    """
    now = _now if _now is not None else time.monotonic()
    if min_interval is None:
        min_interval = app.config.get("CONFIG_SYNC_MIN_INTERVAL", DEFAULT_MIN_INTERVAL)

    last_check = getattr(app, "_config_gen_last_check", None)
    if not force and not should_check_now(last_check, now, min_interval):
        return False
    app._config_gen_last_check = now

    reader = _read_generation or read_generation
    loader = _load_configuration or getattr(app, "load_configuration", None)
    if loader is None:
        return False

    try:
        db_gen = reader()
    except Exception:
        logger.warning(
            "Lecture de la génération de configuration impossible ; "
            "conservation de la configuration en mémoire.",
            exc_info=True,
        )
        return False

    cached = getattr(app, "_config_generation", None)
    if not should_reload(cached, db_gen):
        return False

    try:
        loader(app)
    except Exception:
        logger.warning(
            "Rechargement de la configuration impossible ; "
            "conservation de la configuration en mémoire.",
            exc_info=True,
        )
        return False

    app._config_generation = db_gen
    logger.info("Configuration rechargée (génération %s -> %s).", cached, db_gen)
    return True
