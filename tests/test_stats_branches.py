"""Sélection de branche des graphiques de statistiques (cœur pur).

Ces règles vivaient dans ``routes/admin_stats.py``, mêlées à la construction
SQL : intestables sans MySQL, et porteuses de deux bugs silencieux que ce
fichier verrouille.

- ``chart_category`` : dimension de regroupement. L'ancien
  ``chart_type in ['activities', '_by_activity']`` comparait par **égalité** à
  un fragment de nom : les trois graphiques « par activité » ne groupaient donc
  pas du tout et renvoyaient une valeur globale unique étiquetée « Total ».

- ``aggregated_category_type`` : dimension à lire dans ``AggregatedStats``.
  L'ancien ``'activity' in chart_type`` est **faux** pour ``'activities'``
  (``activit|y`` vs ``activit|ies``) : les données archivées du graphique
  « Patients par activité » étaient purement et simplement absentes du
  résultat, sans le moindre message.

- ``compressed_filter_plan`` : les filtres n'étaient jamais appliqués aux
  données agrégées. Sur une période chevauchant l'archivage, un sous-ensemble
  filtré était additionné à un total non filtré — des chiffres faux, sans
  avertissement.
"""

import pytest

from stats_params import (
    CATEGORY_ACTIVITY,
    CATEGORY_COUNTER,
    CATEGORY_GLOBAL,
    CATEGORY_LANGUAGE,
    CHART_TYPES,
    aggregated_category_type,
    chart_category,
    compressed_filter_plan,
    compressed_skipped_warning,
    is_time_chart,
    mysql_weekdays,
    parse_chart_request,
    time_metric,
)


class Args:
    """Substitut minimal de ``request.args`` (``.get`` + ``.getlist``)."""

    def __init__(self, single=None, multi=None):
        self._single = single or {}
        self._multi = multi or {}

    def get(self, key, default=None):
        return self._single.get(key, default)

    def getlist(self, key):
        return list(self._multi.get(key, []))


from datetime import datetime  # noqa: E402

NOW = datetime(2026, 7, 16, 15, 0, 0)


def _req(single=None, multi=None):
    return parse_chart_request(Args(single, multi), now=NOW)


# --- chart_category ---------------------------------------------------------

@pytest.mark.parametrize('chart_type,attendu', [
    ('languages', CATEGORY_LANGUAGE),
    ('activities', CATEGORY_ACTIVITY),
    ('counters', CATEGORY_COUNTER),
    ('waiting_times_by_activity', CATEGORY_ACTIVITY),
    ('counter_times_by_activity', CATEGORY_ACTIVITY),
    ('total_times_by_activity', CATEGORY_ACTIVITY),
    ('waiting_times', None),
    ('counter_times', None),
    ('total_times', None),
])
def test_chart_category(chart_type, attendu):
    assert chart_category(chart_type) == attendu


def test_by_activity_charts_group_by_activity():
    """Régression : les trois types ``*_by_activity`` doivent grouper.

    ``chart_type in ['activities', '_by_activity']`` ne pouvait jamais être vrai
    pour eux ; ils tombaient dans la branche « pas de regroupement ».
    """
    for chart_type in ('waiting_times_by_activity', 'counter_times_by_activity',
                       'total_times_by_activity'):
        assert chart_category(chart_type) == CATEGORY_ACTIVITY, chart_type


def test_toute_la_liste_blanche_est_couverte():
    """Aucun type du menu ne doit tomber dans un trou de la sélection."""
    for chart_type in CHART_TYPES:
        categorie = chart_category(chart_type)
        assert categorie is not None or is_time_chart(chart_type), chart_type


# --- aggregated_category_type -----------------------------------------------

@pytest.mark.parametrize('chart_type,attendu', [
    ('languages', CATEGORY_LANGUAGE),
    ('activities', CATEGORY_ACTIVITY),
    ('counters', CATEGORY_COUNTER),
    ('waiting_times_by_activity', CATEGORY_ACTIVITY),
    ('waiting_times', CATEGORY_GLOBAL),
    ('counter_times', CATEGORY_GLOBAL),
    ('total_times', CATEGORY_GLOBAL),
])
def test_aggregated_category_type(chart_type, attendu):
    assert aggregated_category_type(chart_type) == attendu


def test_activities_lit_bien_les_donnees_archivees():
    """Régression : ``'activity' in 'activities'`` est faux.

    Le graphique « Patients par activité » ne remontait donc aucune donnée
    archivée.
    """
    assert aggregated_category_type('activities') == CATEGORY_ACTIVITY


def test_aggregated_category_type_pour_tous_les_types_du_menu():
    """Chaque type du menu doit savoir quelle dimension agrégée interroger."""
    for chart_type in CHART_TYPES:
        assert aggregated_category_type(chart_type) is not None, chart_type


# --- time_metric ------------------------------------------------------------

@pytest.mark.parametrize('chart_type,attendu', [
    ('waiting_times', 'waiting'),
    ('waiting_times_by_activity', 'waiting'),
    ('counter_times', 'counter'),
    ('counter_times_by_activity', 'counter'),
    ('total_times', 'total'),
    ('total_times_by_activity', 'total'),
    ('counters', None),
    ('activities', None),
    ('languages', None),
])
def test_time_metric(chart_type, attendu):
    assert time_metric(chart_type) == attendu


def test_counters_n_est_pas_une_metrique_de_temps():
    """``'counter' in 'counters'`` ne doit pas en faire une durée."""
    assert time_metric('counters') is None
    assert not is_time_chart('counters')


# --- mysql_weekdays ---------------------------------------------------------

def test_mysql_weekdays_conversion():
    # Gabarit 1=lundi … 7=dimanche ; MySQL DAYOFWEEK 1=dimanche … 7=samedi.
    assert mysql_weekdays((1,)) == (2,)      # lundi
    assert mysql_weekdays((6,)) == (7,)      # samedi
    assert mysql_weekdays((7,)) == (1,)      # dimanche
    assert mysql_weekdays((1, 7)) == (2, 1)


def test_mysql_weekdays_reste_dans_les_bornes():
    assert all(1 <= d <= 7 for d in mysql_weekdays(range(1, 8)))


# --- compressed_filter_plan -------------------------------------------------

def test_aucun_filtre_aucune_restriction():
    req = _req({'chart_type': 'counters', 'date_type': 'history'})
    assert compressed_filter_plan(req) == (None, ())


def test_filtre_sur_la_dimension_du_graphique_est_applique():
    """Un filtre comptoir sur un graphique comptoirs est représentable."""
    req = _req({'chart_type': 'counters', 'date_type': 'history'},
               {'counter_filter': ['3', '5']})
    ids, unsupported = compressed_filter_plan(req)
    assert ids == (3, 5)
    assert unsupported == ()


def test_filtre_sur_une_autre_dimension_est_signale():
    """Régression : un filtre comptoir sur un graphique langues n'est pas
    représentable dans ``AggregatedStats`` (une dimension par ligne).

    Auparavant aucun filtre n'était appliqué aux agrégats : un sous-ensemble
    filtré était additionné à un total non filtré.
    """
    req = _req({'chart_type': 'languages', 'date_type': 'history'},
               {'counter_filter': ['3']})
    ids, unsupported = compressed_filter_plan(req)
    assert ids is None
    assert unsupported == ('comptoir',)


def test_graphique_global_ne_supporte_aucun_filtre_d_entite():
    """Les lignes 'global' ont ``category_id`` NULL : rien n'y est filtrable."""
    req = _req({'chart_type': 'waiting_times', 'date_type': 'history'},
               {'activity_filter': ['1']})
    ids, unsupported = compressed_filter_plan(req)
    assert ids is None
    assert unsupported == ('activité',)


def test_plusieurs_dimensions_non_representables():
    req = _req({'chart_type': 'languages', 'date_type': 'history'},
               {'counter_filter': ['3'], 'activity_filter': ['1']})
    ids, unsupported = compressed_filter_plan(req)
    assert ids is None
    assert set(unsupported) == {'comptoir', 'activité'}


def test_dimension_du_graphique_appliquee_meme_avec_une_autre_non_supportee():
    """Le filtre représentable est bien reconnu, mais l'appelant écarte quand
    même les agrégats tant qu'une dimension non représentable subsiste."""
    req = _req({'chart_type': 'activities', 'date_type': 'history'},
               {'activity_filter': ['2'], 'language_filter': ['4']})
    ids, unsupported = compressed_filter_plan(req)
    assert ids == (2,)
    assert unsupported == ('langue',)


def test_by_activity_accepte_le_filtre_activite():
    req = _req({'chart_type': 'waiting_times_by_activity', 'date_type': 'history'},
               {'activity_filter': ['7']})
    ids, unsupported = compressed_filter_plan(req)
    assert ids == (7,)
    assert unsupported == ()


def test_jour_de_semaine_n_est_pas_une_dimension_non_supportee():
    """Le jour de la semaine se recalcule depuis ``AggregatedStats.date``."""
    req = _req({'chart_type': 'counters', 'date_type': 'history'},
               {'day_of_week_filter': ['1', '2']})
    assert compressed_filter_plan(req) == (None, ())


# --- compressed_skipped_warning ---------------------------------------------

def test_pas_d_avertissement_sans_dimension_ecartee():
    assert compressed_skipped_warning(()) is None


def test_avertissement_nomme_les_dimensions():
    message = compressed_skipped_warning(('comptoir', 'langue'))
    assert 'comptoir' in message
    assert 'langue' in message


# --- ChartRequest.category --------------------------------------------------

def test_propriete_category_de_la_requete():
    assert _req({'chart_type': 'languages'}).category == CATEGORY_LANGUAGE
    assert _req({'chart_type': 'total_times_by_activity'}).category == CATEGORY_ACTIVITY
    assert _req({'chart_type': 'total_times'}).category is None


# --- Fuseaux horaires -------------------------------------------------------

def test_bornes_toujours_naives_quel_que_soit_le_preset():
    """Régression : ``now`` est tz-aware, mais les colonnes DateTime sont naïves.

    Les presets renvoyaient des bornes aware et les branches ``current`` /
    ``custom`` des bornes naïves, toutes comparées aux mêmes colonnes.
    """
    import pytz

    now_aware = pytz.timezone('Europe/Paris').localize(datetime(2026, 7, 16, 15, 0, 0))

    for single in (
        {'date_type': 'current'},
        {'date_type': 'history', 'period_type': '7'},
        {'date_type': 'history', 'period_type': '365'},
        {'date_type': 'history', 'period_type': 'custom',
         'start_date': '2026-07-01', 'end_date': '2026-07-10'},
    ):
        req = parse_chart_request(Args(single), now=now_aware)
        assert req.ok, req.error
        assert req.start_date.tzinfo is None, single
        assert req.end_date.tzinfo is None, single
