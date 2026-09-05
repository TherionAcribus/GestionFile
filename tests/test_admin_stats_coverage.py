"""Point 15 (audit Admin) — Coverage des fonctions pures de admin_stats.py.

``routes/admin_stats.py`` contient plusieurs fonctions **pures** (sans
accès à la base ni à Flask) qui n'étaient pas testées :

- ``merge_datasets`` : fusionne les données détaillées et compressées en
  appliquant une moyenne pondérée pour les métriques de temps.
- ``format_chart_data`` : transforme les données fusionnées en structure
  Chart.js (pie/bar ou line).
- ``get_chart_title`` : retourne le titre du graphique selon le type.
- ``generate_colors`` : génère une palette de couleurs.
- ``get_random_color`` : génère une couleur RGBA aléatoire.

Ces fonctions sont extraites du source par ``exec`` (comme
``_split_sql_statements`` dans ``test_mysql_restore_safety.py``) car
``admin_stats.py`` importe Flask/SQLAlchemy/MySQL et ne peut pas être
importé directement en test.

On teste aussi statiquement que les routes sont protégées par
``@require_permission('stats')`` ou ``@require_permission_api('stats')``.
"""

import os
import re
import textwrap

import pytest

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def _extract_func(source, func_name):
    """Extrait une fonction top-level du source Python par exec."""
    pattern = rf"^def {func_name}\([^)]*\):(.*?)(?=^def |^@|\Z)"
    m = re.search(pattern, source, re.DOTALL | re.MULTILINE)
    if not m:
        pytest.fail(f"Fonction {func_name} introuvable")
    func_src = textwrap.dedent(m.group(1))
    ns = {}
    exec(func_src, ns)
    return ns[func_name]


# Namespace partagé pour les fonctions qui s'appellent entre elles.
def _build_namespace():
    """Extrait toutes les fonctions pures de admin_stats.py dans un namespace
    commun pour qu'elles puissent s'appeler."""
    source = _read("routes/admin_stats.py")
    ns = {}
    # Importer les dépendances nécessaires (random, datetime, timedelta).
    exec("import random", ns)
    exec("from datetime import datetime, timedelta", ns)
    # Extraire chaque fonction (avec sa signature def) et l'ajouter au namespace.
    for fname in ["merge_datasets", "format_chart_data",
                  "get_chart_title", "generate_colors", "get_random_color"]:
        # Capturer la fonction entière (signature + corps) jusqu'à la prochaine
        # fonction top-level ou un décorateur.
        pattern = rf"^(def {fname}\([^)]*\):.*?)(?=^def |^@|\Z)"
        m = re.search(pattern, source, re.DOTALL | re.MULTILINE)
        if not m:
            pytest.fail(f"Fonction {fname} introuvable")
        func_src = textwrap.dedent(m.group(1))
        exec(func_src, ns)
    return ns


@pytest.fixture(scope="module")
def ns():
    return _build_namespace()


# ---------------------------------------------------------------------------
# 1. merge_datasets
# ---------------------------------------------------------------------------

def _row(date, category, value, count):
    """Crée un objet factice simulant une ligne de résultat SQL."""
    return type('Row', (), {'date': date, 'category': category,
                            'value': value, 'count': count})


def test_merge_datasets_empty(ns):
    """Fusion de deux listes vides → liste vide."""
    assert ns["merge_datasets"]([], [], False) == []


def test_merge_datasets_count_simple(ns):
    """Pour les métriques de comptage (is_time=False), les valeurs s'additionnent."""
    detailed = [_row('2026-01-01', 'Comptoir A', 5, 5)]
    compressed = [_row('2026-01-01', 'Comptoir A', 3, 3)]
    result = ns["merge_datasets"](detailed, compressed, False)
    assert len(result) == 1
    assert result[0].value == 8  # 5 + 3


def test_merge_datasets_time_weighted_average(ns):
    """Pour les métriques de temps (is_time=True), on calcule la moyenne
    pondérée : sum(val * count) / sum(count)."""
    detailed = [_row('2026-01-01', 'Total', 60, 10)]  # 60s avg sur 10 patients
    compressed = [_row('2026-01-01', 'Total', 120, 30)]  # 120s avg sur 30 patients
    result = ns["merge_datasets"](detailed, compressed, True)
    assert len(result) == 1
    # (60*10 + 120*30) / (10+30) = (600 + 3600) / 40 = 105
    assert result[0].value == 105


def test_merge_datasets_different_categories(ns):
    """Deux catégories différentes restent séparées."""
    detailed = [_row('2026-01-01', 'A', 5, 5), _row('2026-01-01', 'B', 3, 3)]
    result = ns["merge_datasets"](detailed, [], False)
    assert len(result) == 2
    categories = {r.category for r in result}
    assert categories == {'A', 'B'}


def test_merge_datasets_different_dates(ns):
    """Deux dates différentes restent séparées."""
    detailed = [_row('2026-01-01', 'A', 5, 5), _row('2026-01-02', 'A', 3, 3)]
    result = ns["merge_datasets"](detailed, [], False)
    assert len(result) == 2


def test_merge_datasets_zero_count_time(ns):
    """Si total_count est 0 pour une métrique de temps, la valeur est 0
    (pas de division par zéro)."""
    detailed = [_row('2026-01-01', 'A', 60, 0)]
    result = ns["merge_datasets"](detailed, [], True)
    assert len(result) == 1
    assert result[0].value == 0


def test_merge_datasets_none_value_treated_as_zero(ns):
    """Une valeur None doit être traitée comme 0."""
    detailed = [_row('2026-01-01', 'A', None, 0)]
    result = ns["merge_datasets"](detailed, [], False)
    assert len(result) == 1
    assert result[0].value == 0


def test_merge_datasets_missing_date_defaults_to_total(ns):
    """Si une ligne n'a pas d'attribut 'date', la clé utilise 'Total'."""
    row = type('Row', (), {'category': 'A', 'value': 5, 'count': 5})
    result = ns["merge_datasets"]([row], [], False)
    assert len(result) == 1
    assert result[0].date == 'Total'


def test_merge_datasets_missing_category_defaults_to_total(ns):
    """Si une ligne n'a pas d'attribut 'category', la clé utilise 'Total'."""
    row = type('Row', (), {'date': '2026-01-01', 'value': 5, 'count': 5})
    result = ns["merge_datasets"]([row], [], False)
    assert len(result) == 1
    assert result[0].category == 'Total'


# ---------------------------------------------------------------------------
# 2. format_chart_data
# ---------------------------------------------------------------------------

def test_format_chart_data_pie(ns):
    """Format pie/bar : labels + values + couleurs."""
    data = [
        _row(None, 'Comptoir A', 10, 10),
        _row(None, 'Comptoir B', 20, 20),
    ]
    from datetime import datetime
    result = ns["format_chart_data"](data, 'counters', 'pie',
                                      datetime(2026, 1, 1), datetime(2026, 1, 7),
                                      'day')
    assert 'labels' in result
    assert 'datasets' in result
    assert result['labels'] == ['Comptoir A', 'Comptoir B']
    assert result['datasets'][0]['data'] == [10, 20]
    assert len(result['datasets'][0]['backgroundColor']) == 2
    assert result['isTime'] is False


def test_format_chart_data_bar(ns):
    """Format bar : même structure que pie."""
    data = [_row(None, 'A', 5, 5)]
    from datetime import datetime
    result = ns["format_chart_data"](data, 'activities', 'bar',
                                      datetime(2026, 1, 1), datetime(2026, 1, 7),
                                      'day')
    assert 'labels' in result
    assert result['labels'] == ['A']


def test_format_chart_data_line(ns):
    """Format line : datasets avec points {x, y} par catégorie."""
    data = [
        _row('2026-01-01', 'A', 5, 5),
        _row('2026-01-02', 'A', 10, 10),
    ]
    from datetime import datetime
    result = ns["format_chart_data"](data, 'counters', 'line',
                                      datetime(2026, 1, 1), datetime(2026, 1, 2),
                                      'day')
    assert 'datasets' in result
    assert len(result['datasets']) == 1
    ds = result['datasets'][0]
    assert ds['label'] == 'A'
    assert len(ds['data']) == 2  # 2 dates
    assert ds['data'][0] == {'x': '2026-01-01', 'y': 5}
    assert ds['data'][1] == {'x': '2026-01-02', 'y': 10}


def test_format_chart_data_line_hourly(ns):
    """Granularité horaire : format de date avec heure."""
    data = [_row('2026-01-01 14:00:00', 'A', 5, 5)]
    from datetime import datetime
    result = ns["format_chart_data"](data, 'counters', 'line',
                                      datetime(2026, 1, 1, 14, 0),
                                      datetime(2026, 1, 1, 15, 0),
                                      'hour')
    ds = result['datasets'][0]
    # Le point doit avoir une date au format '%Y-%m-%d %H:00:00'.
    assert '14:00:00' in ds['data'][0]['x']


def test_format_chart_data_time_converts_to_minutes(ns):
    """Pour les métriques de temps, les valeurs sont converties en minutes."""
    data = [_row(None, 'A', 120, 10)]  # 120 secondes
    from datetime import datetime
    result = ns["format_chart_data"](data, 'waiting_times', 'pie',
                                      datetime(2026, 1, 1), datetime(2026, 1, 7),
                                      'day')
    assert result['isTime'] is True
    assert result['datasets'][0]['data'] == [2.0]  # 120s / 60 = 2 min


def test_format_chart_data_line_time_converts_to_minutes(ns):
    """Line + time : conversion en minutes aussi."""
    data = [_row('2026-01-01', 'A', 120, 10)]
    from datetime import datetime
    result = ns["format_chart_data"](data, 'waiting_times', 'line',
                                      datetime(2026, 1, 1), datetime(2026, 1, 1),
                                      'day')
    assert result['isTime'] is True
    assert result['datasets'][0]['data'][0]['y'] == 2.0  # 120/60


def test_format_chart_data_line_missing_date_fills_zero(ns):
    """Line : une date sans donnée doit avoir y=0."""
    data = [_row('2026-01-01', 'A', 5, 5)]
    from datetime import datetime
    result = ns["format_chart_data"](data, 'counters', 'line',
                                      datetime(2026, 1, 1), datetime(2026, 1, 3),
                                      'day')
    ds = result['datasets'][0]
    assert len(ds['data']) == 3  # 3 dates
    assert ds['data'][0]['y'] == 5
    assert ds['data'][1]['y'] == 0  # 2026-01-02 manquant → 0
    assert ds['data'][2]['y'] == 0  # 2026-01-03 manquant → 0


def test_format_chart_data_has_title(ns):
    """Le résultat doit contenir un titre."""
    data = [_row(None, 'A', 5, 5)]
    from datetime import datetime
    result = ns["format_chart_data"](data, 'counters', 'pie',
                                      datetime(2026, 1, 1), datetime(2026, 1, 7),
                                      'day')
    assert 'title' in result


# ---------------------------------------------------------------------------
# 3. get_chart_title
# ---------------------------------------------------------------------------

def test_get_chart_title_known_types(ns):
    """Les types connus doivent retourner un titre spécifique."""
    assert ns["get_chart_title"]('languages') == 'Distribution des langues'
    assert ns["get_chart_title"]('activities') == 'Distribution des activités'
    assert ns["get_chart_title"]('counters') == 'Distribution des comptoirs'


def test_get_chart_title_time_types(ns):
    """Les types de temps doivent retourner un titre d'évolution."""
    assert "Évolution" in ns["get_chart_title"]('waiting_times')
    assert "Évolution" in ns["get_chart_title"]('counter_times')
    assert "Évolution" in ns["get_chart_title"]('total_times')


def test_get_chart_title_by_activity(ns):
    """Les types _by_activity doivent retourner un titre par activité."""
    assert "activité" in ns["get_chart_title"]('waiting_times_by_activity')
    assert "activité" in ns["get_chart_title"]('counter_times_by_activity')
    assert "activité" in ns["get_chart_title"]('total_times_by_activity')


def test_get_chart_title_unknown_type(ns):
    """Un type inconnu doit retourner le titre par défaut."""
    assert ns["get_chart_title"]('unknown') == 'Statistiques'


# ---------------------------------------------------------------------------
# 4. generate_colors
# ---------------------------------------------------------------------------

def test_generate_colors_small_count(ns):
    """Pour un petit nombre, utilise la palette prédéfinie."""
    colors = ns["generate_colors"](3)
    assert len(colors) == 3
    assert all(c.startswith('#') for c in colors)


def test_generate_colors_exact_palette_size(ns):
    """Pour exactement la taille de la palette, utilise la palette."""
    colors = ns["generate_colors"](6)
    assert len(colors) == 6
    assert all(c.startswith('#') for c in colors)


def test_generate_colors_large_count(ns):
    """Pour un grand nombre, génère des couleurs HSL."""
    colors = ns["generate_colors"](10)
    assert len(colors) == 10
    # Les couleurs au-delà de la palette sont en format hsl().
    assert any(c.startswith('hsl(') for c in colors)


def test_generate_colors_zero(ns):
    """Count=0 → liste vide."""
    assert ns["generate_colors"](0) == []


# ---------------------------------------------------------------------------
# 5. get_random_color
# ---------------------------------------------------------------------------

def test_get_random_color_format(ns):
    """La couleur doit être au format rgba(r, g, b, 1)."""
    color = ns["get_random_color"]()
    assert color.startswith('rgba(')
    assert color.endswith(', 1)')
    # Extraire les composantes et vérifier qu'elles sont entre 0 et 255.
    import re
    m = re.match(r'rgba\((\d+), (\d+), (\d+), 1\)', color)
    assert m
    r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
    assert 0 <= r <= 255
    assert 0 <= g <= 255
    assert 0 <= b <= 255


# ---------------------------------------------------------------------------
# 6. Permissions des routes (statiques)
# ---------------------------------------------------------------------------

def test_admin_stats_route_has_permission():
    """La route /admin/stats doit être protégée par require_permission('stats')."""
    source = _read("routes/admin_stats.py")
    # Trouver la route admin_stats et vérifier qu'elle a @require_permission('stats')
    m = re.search(r"@admin_stats_bp\.route\('/admin/stats'\)\s*\n@require_permission\('stats'\)", source)
    assert m, "Route /admin/stats non protégée par require_permission('stats')"


def test_admin_history_route_has_permission():
    """La route /admin/stats/history doit être protégée."""
    source = _read("routes/admin_stats.py")
    m = re.search(r"@admin_stats_bp\.route\('/admin/stats/history'\)\s*\n@require_permission\('stats'\)", source)
    assert m, "Route /admin/stats/history non protégée"


def test_history_table_route_has_permission():
    """La route /admin/stats/history/table doit être protégée."""
    source = _read("routes/admin_stats.py")
    m = re.search(r"@admin_stats_bp\.route\('/admin/stats/history/table'\)\s*\n@require_permission\('stats'\)", source)
    assert m, "Route /admin/stats/history/table non protégée"


def test_chart_data_route_has_permission():
    """La route /admin/stats/chart doit être protégée par require_permission_api."""
    source = _read("routes/admin_stats.py")
    m = re.search(r"@admin_stats_bp\.route\('/admin/stats/chart'[^)]*\)\s*\n@require_permission_api\('stats'\)", source)
    assert m, "Route /admin/stats/chart non protégée par require_permission_api('stats')"


def test_all_stats_routes_protected():
    """Toutes les routes du blueprint admin_stats doivent avoir une protection."""
    source = _read("routes/admin_stats.py")
    # Trouver toutes les routes.
    routes = re.findall(r"@admin_stats_bp\.route\('[^']+'[^)]*\)", source)
    assert len(routes) >= 4, "Au moins 4 routes attendues"
    # Pour chaque route, vérifier qu'elle est suivie d'un @require_permission.
    for route in routes:
        # Trouver la position de la route dans le source.
        idx = source.find(route)
        # Les 100 caractères suivants doivent contenir require_permission.
        after = source[idx:idx + 200]
        assert 'require_permission' in after, (
            f"Route {route} non protégée par require_permission"
        )


# ---------------------------------------------------------------------------
# 7. HISTORY_SORT_COLUMNS est une liste blanche
# ---------------------------------------------------------------------------

def test_history_sort_columns_is_dict():
    """HISTORY_SORT_COLUMNS doit être un dictionnaire (liste blanche)."""
    source = _read("routes/admin_stats.py")
    assert "HISTORY_SORT_COLUMNS" in source
    assert "= {" in source
    # Doit contenir les colonnes attendues.
    assert "call_number" in source
    assert "timestamp" in source
    assert "status" in source
    assert "day_of_week" in source
