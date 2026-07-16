"""Point 5.1 — intégration pagination : rendu des fragments + régression statique.

1. Chaque gabarit de table paginée se rend sans erreur Jinja (macros de
   `pagination.html` + en-têtes triables + barre de navigation).
2. Chaque route de liste ciblée parse les paramètres via `parse_page_params`
   (donc plafond serveur) et pagine via `paginate_query`.
3. La nouvelle page Historique détaillé est bien exposée et protégée par la
   permission 'stats'.
"""

import datetime
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import pytest  # noqa: E402
from flask import Flask  # noqa: E402

from pagination import PageParams  # noqa: E402
from permissions_registry import permissions_by_category  # noqa: E402

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_TEMPLATES = os.path.join(_SERVEUR, "templates")


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------
# Faux objets minimaux
# --------------------------------------------------------------------------

class FakePager:
    def __init__(self, items, page=1, per_page=25, total=60):
        self.items = items
        self.page = page
        self.per_page = per_page
        self.total = total
        self.pages = max(1, -(-total // per_page))
        self.has_prev = page > 1
        self.has_next = page < self.pages
        self.prev_num = page - 1 if self.has_prev else None
        self.next_num = page + 1 if self.has_next else None

    def iter_pages(self, **kw):
        return range(1, self.pages + 1)


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture
def app():
    app = Flask(__name__, template_folder=_TEMPLATES)
    app.config["SERVER_NAME"] = "localhost"  # pour url_for('static', ...)
    return app


def _render(app, name, **ctx):
    with app.app_context():
        return app.jinja_env.get_template(name).render(**ctx)


PARAMS = PageParams(page=1, per_page=25, sort="name", direction="asc", search="")
TS = datetime.datetime(2026, 7, 15, 10, 0, 0)


def test_queue_fragment_renders(app):
    pat = Obj(id=1, call_number="A1", status="standing",
              activity=Obj(id=1, name="Ordonnance"), counter=None, timestamp=TS)
    out = _render(app, "admin/queue_htmx_table.html",
                  patients=[pat], pager=FakePager([pat]),
                  params=PageParams(1, 25, "timestamp", "asc", ""),
                  activities=[Obj(id=1, name="Ordonnance")],
                  status_list=["ongoing", "standing", "done", "calling"], counters=[])
    assert "pagination-nav" in out
    assert 'hx-post="/admin/queue/table"' in out
    assert "sortable" in out


def test_users_fragment_renders(app):
    user = Obj(id=1, username="bob", email="b@x.fr", roles=[])
    out = _render(app, "admin/security_htmx_table.html",
                  users=[user], pager=FakePager([user]), params=PARAMS,
                  roles=[Obj(id=1, name="admin", description="desc")])
    assert "pagination-nav" in out
    assert 'hx-get="/admin/security/table"' in out


def test_roles_fragment_renders(app):
    role = Obj(id=1, name="ops", description="d",
               to_dict=lambda: {"permissions": {}})
    out = _render(app, "admin/security_htmx_role_table.html",
                  roles=[role], pager=FakePager([role]), params=PARAMS,
                  permissions_by_category=permissions_by_category())
    assert "pagination-nav" in out


def test_languages_fragment_renders(app):
    lang = Obj(id=1, code="fr", name="Français", translation="French",
               is_active=True, voice_is_active=True, flag_url=None)
    out = _render(app, "admin/translations_languages_htmx_table.html",
                  languages=[lang], pager=FakePager([lang]),
                  params=PageParams(1, 25, "code", "asc", ""))
    assert "pagination-nav" in out
    assert 'hx-get="/admin/languages/table"' in out


def test_history_fragment_renders(app):
    row = Obj(call_number="A1", timestamp=TS, timestamp_counter=None,
              timestamp_end=None, status="done", day_of_week="Mon",
              activity_id=1, counter_id=None, language_id=None, overtaken=0)
    out = _render(app, "admin/history_htmx_table.html",
                  rows=[row], pager=FakePager([row]),
                  params=PageParams(1, 25, "timestamp", "desc", ""),
                  activity_names={1: "Ordonnance"}, counter_names={},
                  language_names={})
    assert "pagination-nav" in out
    assert "Ordonnance" in out


def test_empty_list_renders_placeholder(app):
    out = _render(app, "admin/history_htmx_table.html",
                  rows=[], pager=FakePager([], total=0),
                  params=PageParams(1, 25, "timestamp", "desc", ""),
                  activity_names={}, counter_names={}, language_names={})
    assert "Aucun historique" in out


# --------------------------------------------------------------------------
# Régression statique : les routes utilisent bien le cœur de pagination
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rel", [
    "routes/admin_queue.py",
    "routes/admin_security.py",
    "routes/admin_translation.py",
    "routes/admin_stats.py",
])
def test_routes_use_pagination_core(rel):
    src = _read(rel)
    assert "parse_page_params(" in src, f"{rel} ne parse pas les paramètres de page"
    assert "paginate_query(" in src, f"{rel} ne pagine pas via paginate_query"


def test_paginated_model_no_longer_uses_unbounded_query_all():
    """Le modèle paginé ne doit plus être chargé en entier (`<Model>.query.all()`).

    (Les petites listes de config — activités, comptoirs, rôles pour les menus —
    peuvent rester en `.query.all()` : elles ne grandissent pas.)
    """
    for rel, needle, model in [
        ("routes/admin_queue.py", "def display_queue_table", "Patient"),
        ("routes/admin_security.py", "def display_security_table", "User"),
        ("routes/admin_translation.py", "def display_languages_table", "Language"),
    ]:
        src = _read(rel)
        body = src[src.index(needle):]
        body = body[: body.index("\n@")] if "\n@" in body else body
        assert f"{model}.query.all()" not in body, (
            f"{rel}:{needle} charge encore tout {model} via query.all()")


def test_history_page_registered_and_permissioned():
    src = _read("routes/admin_stats.py")
    assert "/admin/stats/history" in src
    assert "/admin/stats/history/table" in src
    # Les deux vues d'historique portent la permission 'stats'.
    for view in ("def admin_history", "def display_history_table"):
        head = src[: src.index(view)]
        # dernier décorateur require_permission avant la vue
        assert re.search(r"require_permission\(\s*['\"]stats['\"]\s*\)[^\n]*\n(@[^\n]*\n)*def "
                         + view.split()[1], src), f"{view} sans permission 'stats'"


def test_default_and_cap_constants():
    from pagination import DEFAULT_PER_PAGE, MAX_PER_PAGE
    assert DEFAULT_PER_PAGE == 25
    assert MAX_PER_PAGE == 100


def test_nav_link_added():
    assert "/admin/stats/history" in _read("templates/admin/base.html")
