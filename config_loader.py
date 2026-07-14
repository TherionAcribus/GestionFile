"""Chargement en masse des options de configuration depuis la base.

Point 12 (performances) — ``app.load_configuration`` peuplait ``app.config`` en
émettant **une requête ``ConfigOption`` par clé** (~130 allers-retours SQL à
chaque démarrage ou rechargement à chaud). Ce module regroupe ces lectures en
**une seule requête** : on récupère toutes les lignes utiles d'un coup, on les
indexe par ``config_key`` puis on applique le registre typé (``CONFIG_MAPPINGS``)
en mémoire.

Le module est volontairement indépendant de ``app.py`` (qui exige MySQL et
monkey-patch eventlet) afin d'être importable et testable isolément, comme
``params_registry``.
"""

from __future__ import annotations

from typing import Any


def load_config_options(ConfigOption, config_mappings) -> dict[str, Any]:
    """Charge en **une seule requête** les options listées dans ``config_mappings``.

    :param ConfigOption: modèle SQLAlchemy exposant ``config_key`` et les colonnes
        typées (``value_str`` / ``value_int`` / ``value_bool`` / ``value_text`` /
        ``value_json``) ainsi que l'attribut de classe ``query``.
    :param config_mappings: table ``{config_key: (config_name, value_type)}`` (en
        pratique ``params_registry.CONFIG_MAPPINGS``).
    :returns: dict ``{config_name: valeur}`` ne contenant que les clés réellement
        présentes en base — l'appelant peut donc l'appliquer tel quel à
        ``app.config`` sans écraser les défauts des clés absentes.
    """
    wanted_keys = list(config_mappings)
    if not wanted_keys:
        return {}

    # Une seule requête : SELECT ... WHERE config_key IN (...).
    rows = ConfigOption.query.filter(
        ConfigOption.config_key.in_(wanted_keys)
    ).all()
    options_by_key = {row.config_key: row for row in rows}

    resolved: dict[str, Any] = {}
    for key, (config_name, value_type) in config_mappings.items():
        option = options_by_key.get(key)
        if option is not None:
            resolved[config_name] = getattr(option, value_type)
    return resolved
