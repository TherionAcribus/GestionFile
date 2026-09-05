"""Point 5.4 — cœur pur de validation des paramètres de statistiques.

Sépare la **validation** (pure, testable sans Flask ni base) de l'**exécution**
des requêtes (dans ``routes/admin_stats.py``). Même découpage que les autres
modules « logique pure » du serveur (``pagination``, ``login_guard``,
``password_policy``…).

Contrat du point 5.4 :

- **Valider strictement** : type de données (``chart_type``), style de graphique
  (``chart_style``), granularité (``time_granularity``), filtres numériques
  (comptoirs / activités / langues / jours de semaine) et dates. Toute valeur
  hors liste blanche ou non convertible est rejetée (400) plutôt que de
  parvenir jusqu'au SQL (où elle provoquait auparavant une 500 via ``int(...)``
  / ``strptime`` non gardés, ou sélectionnait silencieusement une branche vide).

- **Borner les périodes** selon la granularité :
    * granularité **horaire** → fenêtre **courte** (``MAX_HOURLY_DAYS``) : une
      série horaire sur un an, c'est ~8760 points et un balayage massif ;
    * granularité **quotidienne** → fenêtre plus longue mais **plafonnée**
      (``MAX_DAILY_DAYS``).
  Le plafond borne à la fois le nombre de points tracés et la fenêtre balayée
  en base.

La fonction principale :func:`parse_chart_request` prend l'objet ``args`` (type
``request.args`` : expose ``.get`` et ``.getlist``) et l'instant courant ``now``
(injecté pour rester pur/testable), et renvoie un :class:`ChartRequest` figé.
En cas d'entrée invalide, ``error`` est renseigné et l'appelant répond 400.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple

# --- Listes blanches --------------------------------------------------------
# Doivent rester alignées sur les <option> du gabarit admin/stats.html et sur
# les branches de construction de requête dans routes/admin_stats.py.
CHART_TYPES = frozenset({
    'counters', 'languages', 'activities',
    'waiting_times', 'counter_times', 'total_times',
    'waiting_times_by_activity', 'counter_times_by_activity',
    'total_times_by_activity',
})
CHART_STYLES = frozenset({'pie', 'bar', 'line'})
GRANULARITIES = frozenset({'day', 'hour'})
DATE_TYPES = frozenset({'current', 'history'})
# Presets numériques de période (jours) + saisie personnalisée.
PERIOD_PRESETS = frozenset({'7', '28', '365', 'custom'})

# --- Valeurs par défaut (utilisées quand le paramètre est absent) -----------
DEFAULT_CHART_TYPE = 'counters'
DEFAULT_CHART_STYLE = 'pie'
DEFAULT_GRANULARITY = 'day'
DEFAULT_DATE_TYPE = 'current'
DEFAULT_PERIOD = '7'

# --- Bornes de période (jours) selon la granularité -------------------------
# Horaire = fenêtre courte (couvre le preset « 7 derniers jours » avec marge).
MAX_HOURLY_DAYS = 8
# Quotidienne = plus longue mais bornée (couvre le preset « 365 jours »).
MAX_DAILY_DAYS = 366

# Jours de semaine valides côté gabarit : 1 (lundi) … 7 (dimanche).
_VALID_WEEKDAYS = frozenset(range(1, 8))

DATE_FMT = '%Y-%m-%d'


@dataclass(frozen=True)
class ChartRequest:
    """Paramètres de statistiques validés et normalisés.

    ``error`` est ``None`` si tout est valide ; sinon il porte un message
    utilisateur et ``start_date``/``end_date`` peuvent être ``None``.
    """

    chart_type: str
    chart_style: str
    time_granularity: str
    date_type: str
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    counter_ids: Tuple[int, ...]
    activity_ids: Tuple[int, ...]
    language_ids: Tuple[int, ...]
    day_of_week: Tuple[int, ...]
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def is_history(self) -> bool:
        return self.date_type == 'history'

    @property
    def is_time(self) -> bool:
        """Vrai pour les métriques de durée (moyenne de temps, pas un comptage)."""
        return is_time_chart(self.chart_type)

    @property
    def category(self) -> Optional[str]:
        """Dimension de regroupement (``None`` = série unique)."""
        return chart_category(self.chart_type)


def _validated(raw, allowed, default, label):
    """Valide ``raw`` contre une liste blanche.

    Absent/vide → ``default`` (pas d'erreur : le premier chargement de page
    peut ne pas fournir tous les paramètres). Présent mais hors liste → erreur
    (entrée forgée : on refuse plutôt que de deviner).
    """
    if raw is None or raw == '':
        return default, None
    if raw in allowed:
        return raw, None
    return default, f"{label} invalide."


def parse_int_list(values, *, valid=None):
    """Convertit une liste de chaînes en entiers, en ignorant les invalides.

    Remplace les ``int(x) for x in ...`` non gardés qui levaient ``ValueError``
    (→ 500) sur une saisie forgée. Si ``valid`` est fourni, seules les valeurs
    de cet ensemble sont conservées. L'ordre est préservé et les doublons
    retirés.
    """
    out = []
    seen = set()
    for v in values or ():
        try:
            n = int(str(v).strip())
        except (TypeError, ValueError):
            continue
        if valid is not None and n not in valid:
            continue
        if n not in seen:
            seen.add(n)
            out.append(n)
    return tuple(out)


def _parse_date(s):
    """Parse une date ``YYYY-MM-DD`` ; ``None`` si vide ou mal formée."""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), DATE_FMT)
    except (ValueError, AttributeError):
        return None


def resolve_date_range(date_type, period_type, start_str, end_str, granularity, now):
    """Calcule (start, end, error) et **borne la période** selon la granularité.

    - ``current`` : la journée en cours (bornes min/max de ``now``).
    - ``history`` + preset numérique : ``now`` - N jours … ``now``.
    - ``history`` + ``custom`` : dates saisies, étendues aux bornes de journée.

    Le plafond (``MAX_HOURLY_DAYS`` / ``MAX_DAILY_DAYS``) est appliqué en dernier
    et rejette toute fenêtre trop longue pour la granularité demandée.
    """
    if date_type != 'history':
        today = now.date()
        start = datetime.combine(today, datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
        return start, end, None

    if period_type not in PERIOD_PRESETS:
        return None, None, "Type de période invalide."

    if period_type == 'custom':
        start = _parse_date(start_str)
        end = _parse_date(end_str)
        if start is None or end is None:
            return None, None, "Dates manquantes ou invalides."
        if end.date() < start.date():
            return None, None, "La date de fin précède la date de début."
        start = datetime.combine(start.date(), datetime.min.time())
        end = datetime.combine(end.date(), datetime.max.time())
    else:
        # period_type ∈ {'7','28','365'} : conversion sûre.
        days = int(period_type)
        end = now
        start = end - timedelta(days=days)

    # Les colonnes ``timestamp`` sont des DateTime **naïfs** en heure locale.
    # ``now`` étant tz-aware, les presets renvoyaient des bornes aware et les
    # branches ``current`` / ``custom`` des bornes naïves : les deux étaient
    # comparées aux mêmes colonnes, avec un décalage selon la branche. On
    # normalise en naïf local (l'heure murale est déjà la bonne).
    start = start.replace(tzinfo=None)
    end = end.replace(tzinfo=None)

    max_days = MAX_HOURLY_DAYS if granularity == 'hour' else MAX_DAILY_DAYS
    span_days = (end - start).days
    if span_days > max_days:
        return None, None, (
            f"Période trop longue pour la granularité « "
            f"{'horaire' if granularity == 'hour' else 'quotidienne'} » "
            f"(maximum {max_days} jours, demandé {span_days})."
        )

    return start, end, None


def parse_chart_request(args, *, now):
    """Valide l'ensemble des paramètres et renvoie un :class:`ChartRequest`.

    ``args`` doit exposer ``.get(key)`` et ``.getlist(key)`` (comme
    ``request.args``). ``now`` est injecté (datetime, idéalement tz-aware) pour
    garder la fonction pure et testable.
    """
    get = args.get
    getlist = getattr(args, 'getlist', lambda k: [])

    chart_type, err_type = _validated(
        get('chart_type'), CHART_TYPES, DEFAULT_CHART_TYPE, "Type de données")
    chart_style, err_style = _validated(
        get('chart_style'), CHART_STYLES, DEFAULT_CHART_STYLE, "Style de graphique")
    granularity, err_gran = _validated(
        get('time_granularity'), GRANULARITIES, DEFAULT_GRANULARITY, "Granularité")
    date_type, err_date = _validated(
        get('date_type'), DATE_TYPES, DEFAULT_DATE_TYPE, "Période")

    counter_ids = parse_int_list(getlist('counter_filter'))
    activity_ids = parse_int_list(getlist('activity_filter'))
    language_ids = parse_int_list(getlist('language_filter'))
    day_of_week = parse_int_list(getlist('day_of_week_filter'), valid=_VALID_WEEKDAYS)

    # Première erreur de sélecteur (le cas échéant), avant même de toucher aux dates.
    selector_error = err_type or err_style or err_gran or err_date

    period_type = get('period_type') or DEFAULT_PERIOD
    start_date, end_date, err_range = resolve_date_range(
        date_type, period_type, get('start_date'), get('end_date'), granularity, now)

    error = selector_error or err_range

    return ChartRequest(
        chart_type=chart_type,
        chart_style=chart_style,
        time_granularity=granularity,
        date_type=date_type,
        start_date=start_date,
        end_date=end_date,
        counter_ids=counter_ids,
        activity_ids=activity_ids,
        language_ids=language_ids,
        day_of_week=day_of_week,
        error=error,
    )


# ---------------------------------------------------------------------------
# Sélection de branche (dimension de regroupement / métrique)
# ---------------------------------------------------------------------------
# Ces fonctions étaient auparavant des chaînes de ``if`` dispersées dans
# ``routes/admin_stats.py``, mêlées à la construction SQL — donc intestables et
# porteuses de deux bugs silencieux :
#   * ``chart_type in ['activities', '_by_activity']`` ne pouvait jamais être
#     vrai pour ``waiting_times_by_activity`` & co. (comparaison d'égalité avec
#     un fragment de nom) : les trois graphiques « par activité » ne
#     groupaient pas et renvoyaient une valeur globale unique ;
#   * ``'activity' in chart_type`` est faux pour ``'activities'``
#     (``activit|y`` vs ``activit|ies``) : les données archivées du graphique
#     « Patients par activité » étaient purement et simplement absentes.
# Les remonter ici les rend testables sans base ni Flask.

# Dimension de regroupement d'un graphique, dans le vocabulaire de
# ``AggregatedStats.category_type``.
CATEGORY_LANGUAGE = 'language'
CATEGORY_ACTIVITY = 'activity'
CATEGORY_COUNTER = 'counter'
CATEGORY_GLOBAL = 'global'

# Suffixe des types « ventilés par activité » (waiting/counter/total).
_BY_ACTIVITY_SUFFIX = '_by_activity'

# Libellés utilisateur des dimensions de filtre (messages d'avertissement).
DIMENSION_LABELS = {
    CATEGORY_COUNTER: 'comptoir',
    CATEGORY_ACTIVITY: 'activité',
    CATEGORY_LANGUAGE: 'langue',
}


def is_time_chart(chart_type):
    """Vrai si la métrique est une durée moyenne (et non un comptage)."""
    return '_times' in (chart_type or '')


def chart_category(chart_type):
    """Dimension de regroupement du graphique, ou ``None`` s'il est global.

    ``None`` signifie « une seule série » : les graphiques de temps non
    ventilés (``waiting_times``, ``counter_times``, ``total_times``).
    """
    if chart_type == 'languages':
        return CATEGORY_LANGUAGE
    if chart_type == 'activities' or (chart_type or '').endswith(_BY_ACTIVITY_SUFFIX):
        return CATEGORY_ACTIVITY
    if chart_type == 'counters':
        return CATEGORY_COUNTER
    return None


def aggregated_category_type(chart_type):
    """``AggregatedStats.category_type`` à interroger, ou ``None`` si aucun.

    Les graphiques de temps non ventilés lisent les lignes ``global``
    (``category_id`` NULL) ; les autres lisent la dimension correspondante.
    """
    category = chart_category(chart_type)
    if category is not None:
        return category
    if is_time_chart(chart_type):
        return CATEGORY_GLOBAL
    return None


def time_metric(chart_type):
    """Nom de la métrique de durée : ``waiting`` / ``counter`` / ``total``.

    ``None`` pour un graphique de comptage. Sert à la fois à choisir la
    différence de timestamps (données détaillées) et la colonne pré-calculée
    de ``AggregatedStats`` (données compressées) : une seule source de vérité.
    """
    if not is_time_chart(chart_type):
        return None
    if chart_type.startswith('waiting'):
        return 'waiting'
    if chart_type.startswith('counter'):
        return 'counter'
    return 'total'


def mysql_weekdays(days):
    """Convertit les jours du gabarit (1=lundi … 7=dimanche) vers DAYOFWEEK.

    MySQL ``DAYOFWEEK`` numérote 1=dimanche … 7=samedi. lundi(1)→2,
    samedi(6)→7, dimanche(7)→1.
    """
    return tuple(d + 1 if d < 7 else 1 for d in days)


def compressed_filter_plan(req):
    """Décide ce que les données agrégées peuvent honorer des filtres actifs.

    ``AggregatedStats`` ne stocke **qu'une seule dimension par ligne**
    (``category_type`` + ``category_id``). Un filtre portant sur la dimension
    du graphique est donc applicable (``category_id IN (...)``) ; un filtre
    portant sur une autre dimension ne l'est pas — l'information a été perdue à
    l'agrégation.

    Renvoie ``(category_ids, unsupported)`` :

    - ``category_ids`` : identifiants auxquels restreindre ``category_id``
      (``None`` = aucune restriction) ;
    - ``unsupported`` : libellés des dimensions filtrées que les agrégats ne
      peuvent pas honorer. Non vide ⇒ l'appelant doit **écarter** les données
      compressées et le signaler, plutôt que d'additionner un sous-ensemble
      filtré et un total non filtré (ce que faisait le code précédent, qui
      n'appliquait aucun filtre aux agrégats).
    """
    category = chart_category(req.chart_type)
    active = (
        (CATEGORY_COUNTER, req.counter_ids),
        (CATEGORY_ACTIVITY, req.activity_ids),
        (CATEGORY_LANGUAGE, req.language_ids),
    )

    category_ids = None
    unsupported = []
    for dimension, ids in active:
        if not ids:
            continue
        if dimension == category:
            category_ids = ids
        else:
            unsupported.append(DIMENSION_LABELS[dimension])

    return category_ids, tuple(unsupported)


def compressed_skipped_warning(unsupported):
    """Message utilisateur quand les agrégats sont écartés par les filtres."""
    if not unsupported:
        return None
    dimensions = ', '.join(unsupported)
    return (
        "Les données archivées ne conservent pas le détail par "
        f"{dimensions} : seules les données récentes sont prises en compte "
        "pour ce filtre."
    )
