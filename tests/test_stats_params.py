"""Point 5.4 — cœur pur de validation des paramètres de statistiques.

Tests sans Flask ni base : validation stricte (type/style/granularité/filtres/
dates) et bornage des périodes selon la granularité.
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from stats_params import (  # noqa: E402
    CHART_TYPES,
    MAX_HOURLY_DAYS,
    MAX_DAILY_DAYS,
    parse_chart_request,
    parse_int_list,
    resolve_date_range,
    _parse_date,
    _VALID_WEEKDAYS,
)

NOW = datetime(2026, 7, 16, 15, 0, 0)


class Args:
    """Substitut minimal de ``request.args`` (``.get`` + ``.getlist``)."""

    def __init__(self, single=None, multi=None):
        self._single = single or {}
        self._multi = multi or {}

    def get(self, key, default=None):
        return self._single.get(key, default)

    def getlist(self, key):
        return list(self._multi.get(key, []))


def _req(single=None, multi=None, now=NOW):
    return parse_chart_request(Args(single, multi), now=now)


# --- parse_int_list ---------------------------------------------------------

def test_parse_int_list_keeps_valid_ints():
    assert parse_int_list(['1', '2', '3']) == (1, 2, 3)


def test_parse_int_list_drops_non_numeric_instead_of_raising():
    # Auparavant int('abc') levait ValueError → 500.
    assert parse_int_list(['1', 'abc', '2', '']) == (1, 2)


def test_parse_int_list_dedupes_preserving_order():
    assert parse_int_list(['2', '2', '1', '2']) == (2, 1)


def test_parse_int_list_valid_set_filters_out_of_range():
    assert parse_int_list(['0', '1', '7', '8'], valid=_VALID_WEEKDAYS) == (1, 7)


def test_parse_int_list_empty_or_none():
    assert parse_int_list(None) == ()
    assert parse_int_list([]) == ()


# --- _parse_date ------------------------------------------------------------

def test_parse_date_valid():
    assert _parse_date('2026-07-16') == datetime(2026, 7, 16)


def test_parse_date_invalid_returns_none():
    assert _parse_date('2026-13-40') is None
    assert _parse_date('not-a-date') is None
    assert _parse_date('') is None
    assert _parse_date(None) is None


# --- validation stricte des sélecteurs --------------------------------------

def test_valid_selectors_pass():
    r = _req({'chart_type': 'languages', 'chart_style': 'bar',
              'time_granularity': 'day', 'date_type': 'current'})
    assert r.ok
    assert r.chart_type == 'languages'
    assert r.chart_style == 'bar'


def test_absent_selectors_use_defaults_without_error():
    r = _req({})
    assert r.ok
    assert r.chart_type in CHART_TYPES
    assert r.chart_style == 'pie'
    assert r.time_granularity == 'day'
    assert r.date_type == 'current'


def test_invalid_chart_type_rejected():
    r = _req({'chart_type': 'DROP TABLE'})
    assert not r.ok
    assert 'Type de données' in r.error


def test_invalid_chart_style_rejected():
    r = _req({'chart_style': 'radar'})
    assert not r.ok


def test_invalid_granularity_rejected():
    r = _req({'time_granularity': 'week'})
    assert not r.ok


def test_invalid_date_type_rejected():
    r = _req({'date_type': 'forever'})
    assert not r.ok


# --- filtres numériques -----------------------------------------------------

def test_numeric_filters_parsed_and_sanitized():
    r = _req(multi={'counter_filter': ['1', 'x', '2'],
                    'activity_filter': ['3'],
                    'language_filter': ['bad'],
                    'day_of_week_filter': ['1', '9', '7']})
    assert r.ok
    assert r.counter_ids == (1, 2)
    assert r.activity_ids == (3,)
    assert r.language_ids == ()
    assert r.day_of_week == (1, 7)  # 9 hors 1..7 écarté


# --- bornage des périodes ---------------------------------------------------

def test_current_is_single_day():
    start, end, err = resolve_date_range('current', '7', None, None, 'day', NOW)
    assert err is None
    assert start.date() == end.date() == NOW.date()


def test_hourly_short_preset_allowed():
    start, end, err = resolve_date_range('history', '7', None, None, 'hour', NOW)
    assert err is None
    assert (end - start).days <= MAX_HOURLY_DAYS


def test_hourly_long_preset_rejected():
    _, _, err = resolve_date_range('history', '365', None, None, 'hour', NOW)
    assert err is not None
    assert 'horaire' in err


def test_hourly_28_days_rejected():
    _, _, err = resolve_date_range('history', '28', None, None, 'hour', NOW)
    assert err is not None


def test_daily_365_allowed():
    _, _, err = resolve_date_range('history', '365', None, None, 'day', NOW)
    assert err is None


def test_custom_range_within_daily_cap_allowed():
    _, _, err = resolve_date_range('history', 'custom', '2026-01-01', '2026-03-01', 'day', NOW)
    assert err is None


def test_custom_range_exceeding_daily_cap_rejected():
    _, _, err = resolve_date_range('history', 'custom', '2020-01-01', '2026-01-01', 'day', NOW)
    assert err is not None
    assert 'quotidienne' in err


def test_custom_range_exceeding_hourly_cap_rejected():
    _, _, err = resolve_date_range('history', 'custom', '2026-01-01', '2026-02-01', 'hour', NOW)
    assert err is not None


def test_custom_missing_dates_rejected():
    _, _, err = resolve_date_range('history', 'custom', '2026-01-01', None, 'day', NOW)
    assert err is not None


def test_custom_end_before_start_rejected():
    _, _, err = resolve_date_range('history', 'custom', '2026-03-01', '2026-01-01', 'day', NOW)
    assert err is not None


def test_custom_dates_span_full_days():
    start, end, err = resolve_date_range('history', 'custom', '2026-07-01', '2026-07-01', 'day', NOW)
    assert err is None
    assert start.hour == 0 and start.minute == 0
    assert end.hour == 23 and end.minute == 59


def test_invalid_period_type_rejected():
    _, _, err = resolve_date_range('history', 'weird', None, None, 'day', NOW)
    assert err is not None


# --- intégration parse_chart_request ---------------------------------------

def test_full_request_history_custom_hourly_too_long():
    r = _req({'date_type': 'history', 'period_type': 'custom',
              'start_date': '2026-01-01', 'end_date': '2026-06-01',
              'time_granularity': 'hour', 'chart_style': 'line',
              'chart_type': 'waiting_times'})
    assert not r.ok
    assert r.start_date is None


def test_full_request_history_valid():
    r = _req({'date_type': 'history', 'period_type': '7',
              'time_granularity': 'day', 'chart_style': 'line',
              'chart_type': 'counters'})
    assert r.ok
    assert r.is_history
    assert r.start_date is not None and r.end_date is not None


def test_is_time_property():
    assert _req({'chart_type': 'waiting_times'}).is_time
    assert not _req({'chart_type': 'counters'}).is_time


def test_selector_error_takes_precedence_over_range():
    # Sélecteur invalide ET plage douteuse : on renvoie l'erreur de sélecteur.
    r = _req({'chart_type': 'bogus', 'date_type': 'history',
              'period_type': 'custom'})
    assert not r.ok
    assert 'Type de données' in r.error
