"""Point 5.1 — cœur pur de pagination (`pagination.parse_page_params`).

Tests sans Flask ni base : on vérifie la normalisation et surtout les
garde-fous de sécurité (plafond serveur de `per_page`, liste blanche de tri).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from pagination import (  # noqa: E402
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
    MAX_SEARCH_LEN,
    PageParams,
    parse_page_params,
    _escape_like,
)


class Args(dict):
    """Substitut minimal de ``request.args`` (mapping avec ``.get``)."""


def _parse(raw, **kw):
    return parse_page_params(Args(raw), **kw)


# --- page -------------------------------------------------------------------

def test_page_defaults_to_one_when_absent():
    assert _parse({}).page == 1


def test_page_non_numeric_falls_back_to_one():
    assert _parse({"page": "abc"}).page == 1
    assert _parse({"page": ""}).page == 1


def test_page_below_one_is_clamped_to_one():
    assert _parse({"page": "0"}).page == 1
    assert _parse({"page": "-5"}).page == 1


def test_valid_page_is_kept():
    assert _parse({"page": "7"}).page == 7


# --- per_page + plafond serveur (sécurité) ----------------------------------

def test_per_page_defaults_to_module_default():
    assert _parse({}).per_page == DEFAULT_PER_PAGE == 25


def test_per_page_custom_default_is_honored():
    assert _parse({}, default_per_page=50).per_page == 50


def test_per_page_over_cap_is_clamped_to_cap():
    # Un client demande 10000 → le serveur plafonne à MAX_PER_PAGE.
    assert _parse({"per_page": "10000"}).per_page == MAX_PER_PAGE == 100


def test_per_page_cap_applies_even_with_large_default():
    # Le plafond est appliqué en dernier : un default mal réglé ne le franchit pas.
    assert _parse({}, default_per_page=999).per_page == MAX_PER_PAGE


def test_per_page_custom_cap():
    assert _parse({"per_page": "500"}, max_per_page=200).per_page == 200


def test_per_page_invalid_or_negative_uses_default():
    assert _parse({"per_page": "abc"}).per_page == DEFAULT_PER_PAGE
    assert _parse({"per_page": "0"}).per_page == DEFAULT_PER_PAGE
    assert _parse({"per_page": "-10"}).per_page == DEFAULT_PER_PAGE


# --- tri : liste blanche (sécurité) -----------------------------------------

def test_sort_not_in_allowlist_is_dropped():
    p = _parse({"sort": "password"}, allowed_sort=("name", "id"))
    assert p.sort is None


def test_sort_in_allowlist_is_kept():
    p = _parse({"sort": "name"}, allowed_sort=("name", "id"))
    assert p.sort == "name"


def test_default_sort_applied_when_absent():
    p = _parse({}, allowed_sort=("name", "id"), default_sort="id")
    assert p.sort == "id"


def test_default_sort_ignored_if_not_allowed():
    # Un default_sort mal configuré ne contourne pas la liste blanche.
    p = _parse({}, allowed_sort=("name",), default_sort="secret")
    assert p.sort is None


def test_invalid_sort_falls_back_to_default_sort():
    p = _parse({"sort": "evil"}, allowed_sort=("name",), default_sort="name")
    assert p.sort == "name"


def test_no_allowlist_means_no_sort():
    assert _parse({"sort": "name"}).sort is None


# --- direction --------------------------------------------------------------

def test_direction_defaults_to_asc():
    assert _parse({}).direction == "asc"


def test_direction_desc_is_kept():
    assert _parse({"dir": "desc"}).direction == "desc"
    assert _parse({"dir": "DESC"}).direction == "desc"


def test_direction_invalid_falls_back_to_asc():
    assert _parse({"dir": "sideways"}).direction == "asc"


# --- search -----------------------------------------------------------------

def test_search_is_trimmed():
    assert _parse({"search": "  bob  "}).search == "bob"


def test_search_absent_is_empty_string():
    assert _parse({}).search == ""


def test_search_is_length_bounded():
    p = _parse({"search": "x" * 500})
    assert len(p.search) == MAX_SEARCH_LEN


# --- helpers ----------------------------------------------------------------

def test_escape_like_neutralizes_wildcards():
    assert _escape_like("100%_a") == "100\\%\\_a"
    assert _escape_like("a\\b") == "a\\\\b"


def test_as_query_omits_empty_and_supports_overrides():
    p = PageParams(page=2, per_page=25, sort=None, direction="asc", search="")
    assert p.as_query() == {"page": 2, "per_page": 25}
    # override page sans muter
    assert p.as_query(page=3)["page"] == 3
    assert p.page == 2

    p2 = PageParams(page=1, per_page=50, sort="name", direction="desc", search="bob")
    q = p2.as_query()
    assert q == {"page": 1, "per_page": 50, "sort": "name", "dir": "desc", "search": "bob"}
