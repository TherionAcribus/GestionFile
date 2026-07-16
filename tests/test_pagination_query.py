"""Point 5.1 — adaptateur `pagination.paginate_query` sur une vraie base.

SQLite en mémoire + un modèle jouet : on vérifie le découpage en pages, le tri
asc/desc via la liste blanche, la recherche insensible à la casse et surtout que
le **plafond serveur** de `per_page` est bien appliqué de bout en bout (le client
ne peut pas forcer une page géante).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import pytest  # noqa: E402
from flask import Flask  # noqa: E402
from flask_sqlalchemy import SQLAlchemy  # noqa: E402

from pagination import parse_page_params, paginate_query  # noqa: E402


class Args(dict):
    pass


@pytest.fixture
def ctx():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["TESTING"] = True
    db = SQLAlchemy(app)

    class Item(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(50))
        note = db.Column(db.String(50))

    with app.app_context():
        db.create_all()
        for i in range(1, 61):  # 60 lignes : name01 … name60
            db.session.add(Item(name=f"name{i:02d}", note="odd" if i % 2 else "even"))
        # une ligne piège contenant un vrai '%' pour tester l'échappement LIKE
        db.session.add(Item(name="wild%card", note="special"))
        db.session.commit()
        yield app, db, Item


_SORTS = None  # placeholder, real columns built per-test from the model


def _cols(Item):
    return (
        {"name": Item.name, "note": Item.note},
        [Item.name, Item.note],
    )


def test_default_page_and_size(ctx):
    app, db, Item = ctx
    sort_cols, search_cols = _cols(Item)
    params = parse_page_params(Args({}), allowed_sort=("name",), default_sort="name")
    pager = paginate_query(Item.query, params, sort_columns=sort_cols, search_columns=search_cols)
    assert pager.per_page == 25
    assert pager.total == 61  # 60 + ligne piège
    assert len(pager.items) == 25
    assert pager.page == 1
    assert pager.has_next and not pager.has_prev


def test_page_slicing_is_stable_with_sort(ctx):
    app, db, Item = ctx
    sort_cols, search_cols = _cols(Item)
    params = parse_page_params(
        Args({"page": "2", "per_page": "10"}), allowed_sort=("name",), default_sort="name"
    )
    pager = paginate_query(Item.query, params, sort_columns=sort_cols, search_columns=search_cols)
    names = [it.name for it in pager.items]
    # tri par name asc → page 2 (11..20) = name11 … name20
    assert names == [f"name{i:02d}" for i in range(11, 21)]


def test_sort_desc(ctx):
    app, db, Item = ctx
    sort_cols, search_cols = _cols(Item)
    params = parse_page_params(
        Args({"sort": "name", "dir": "desc", "per_page": "5"}),
        allowed_sort=("name",),
        default_sort="name",
    )
    pager = paginate_query(Item.query, params, sort_columns=sort_cols, search_columns=search_cols)
    # name60 puis descendant ; 'wild%card' commence par 'w' > 'n' donc il est premier
    assert pager.items[0].name == "wild%card"
    assert pager.items[1].name == "name60"


def test_per_page_cap_enforced_end_to_end(ctx):
    app, db, Item = ctx
    sort_cols, search_cols = _cols(Item)
    # Le client demande 100000 : le plafond serveur (100) s'applique, et
    # paginate reçoit max_per_page == per_page, donc pas de contournement.
    params = parse_page_params(Args({"per_page": "100000"}), allowed_sort=("name",))
    pager = paginate_query(Item.query, params, sort_columns=sort_cols, search_columns=search_cols)
    assert pager.per_page == 100
    assert len(pager.items) == 61  # tout tient sur une page de 100 max


def test_search_is_case_insensitive_and_substring(ctx):
    app, db, Item = ctx
    sort_cols, search_cols = _cols(Item)
    params = parse_page_params(Args({"search": "NAME0"}), allowed_sort=("name",))
    pager = paginate_query(Item.query, params, sort_columns=sort_cols, search_columns=search_cols)
    # name01..name09 → 9 résultats (insensible à la casse)
    assert pager.total == 9


def test_search_escapes_like_wildcards(ctx):
    app, db, Item = ctx
    sort_cols, search_cols = _cols(Item)
    # '%' doit être traité littéralement : ne matche QUE 'wild%card', pas tout.
    params = parse_page_params(Args({"search": "%"}), allowed_sort=("name",))
    pager = paginate_query(Item.query, params, sort_columns=sort_cols, search_columns=search_cols)
    assert pager.total == 1
    assert pager.items[0].name == "wild%card"


def test_out_of_range_page_returns_empty_not_error(ctx):
    app, db, Item = ctx
    sort_cols, search_cols = _cols(Item)
    params = parse_page_params(Args({"page": "9999", "per_page": "10"}), allowed_sort=("name",))
    pager = paginate_query(Item.query, params, sort_columns=sort_cols, search_columns=search_cols)
    assert pager.items == []
    assert pager.total == 61  # le total reste correct
