"""Pagination, tri et recherche — cœur pur + adaptateur SQLAlchemy (point 5.1).

Séparation volontaire, calquée sur les autres modules « logique pure » du
serveur (login_guard, password_policy…) :

- :func:`parse_page_params` est **pur** (aucune dépendance Flask ni SQLAlchemy).
  Il normalise les paramètres de requête (``page``, ``per_page``, ``sort``,
  ``dir``, ``search``) et applique les **garde-fous de sécurité** :
    * ``per_page`` est plafonné côté serveur (``max_per_page``) : un client ne
      peut jamais forcer le serveur à matérialiser une page arbitrairement
      grande (déni de service / vidage de table) ;
    * ``sort`` est restreint à une **liste blanche** de clés fournie par
      l'appelant : impossible de trier sur une colonne non prévue (fuite de
      structure, injection dans ORDER BY).
  Entièrement testable sans base ni serveur.

- :func:`paginate_query` est un adaptateur mince : il applique recherche +
  tri + page à une requête Flask-SQLAlchemy et renvoie l'objet ``Pagination``
  (``.items``, ``.total``, ``.page``, ``.pages``, ``.has_next``…), directement
  exploitable dans les gabarits.

Le contrat « valeur par défaut raisonnable + plafond serveur » du point 5.1 est
porté par les constantes ci-dessous ; chaque appelant peut resserrer le défaut
mais **jamais** dépasser le plafond (le plafond est appliqué en dernier).
"""

from dataclasses import dataclass
from typing import Optional

# Défaut conservateur (tables souvent éditables : patients, utilisateurs…) et
# plafond serveur strict. Choix validé pour le point 5.1 : 25 / 100.
DEFAULT_PER_PAGE = 25
MAX_PER_PAGE = 100

# Longueur maximale d'un terme de recherche : borne défensive (un terme géant
# n'a aucun intérêt fonctionnel et alourdit la requête LIKE).
MAX_SEARCH_LEN = 100


@dataclass(frozen=True)
class PageParams:
    """Paramètres de pagination normalisés et sûrs.

    ``sort`` vaut ``None`` si aucune clé de tri valide n'a été demandée (l'ordre
    par défaut de la requête s'applique alors). ``direction`` est toujours
    ``'asc'`` ou ``'desc'``.
    """

    page: int
    per_page: int
    sort: Optional[str]
    direction: str
    search: str

    def as_query(self, **overrides):
        """Dict des paramètres pour reconstruire une URL/hx-vals.

        Les clés absentes (sort None, search vide) sont omises pour garder des
        URLs propres. ``overrides`` permet de forcer une valeur (ex. changer de
        page) sans muter l'objet.
        """
        data = {"page": self.page, "per_page": self.per_page}
        if self.sort:
            data["sort"] = self.sort
            data["dir"] = self.direction
        if self.search:
            data["search"] = self.search
        data.update(overrides)
        return data


def _coerce_int(value, default):
    """Convertit ``value`` en entier, ``default`` si ce n'est pas possible."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return default


def parse_page_params(
    args,
    *,
    allowed_sort=(),
    default_sort=None,
    default_per_page=DEFAULT_PER_PAGE,
    max_per_page=MAX_PER_PAGE,
):
    """Normalise les paramètres de requête en :class:`PageParams` sûrs.

    :param args: mapping type ``request.args`` (doit exposer ``.get(key)``).
    :param allowed_sort: itérable des clés de tri autorisées (liste blanche).
    :param default_sort: clé de tri appliquée si la demande est absente/invalide
        (ignorée si elle n'est pas elle-même dans ``allowed_sort``).
    :param default_per_page: taille de page par défaut (bornée par le plafond).
    :param max_per_page: **plafond serveur** de la taille de page (non
        dépassable par le client).
    """
    get = args.get

    page = _coerce_int(get("page"), 1)
    if page < 1:
        page = 1

    # Plafond appliqué en DERNIER : même un default_per_page mal réglé ne peut
    # pas franchir max_per_page.
    per_page = _coerce_int(get("per_page"), default_per_page)
    if per_page < 1:
        per_page = default_per_page
    if per_page > max_per_page:
        per_page = max_per_page

    allowed = set(allowed_sort)
    sort = get("sort") or None
    if sort not in allowed:
        sort = default_sort if default_sort in allowed else None

    direction = (get("dir") or "").strip().lower()
    if direction not in ("asc", "desc"):
        direction = "asc"

    search = (get("search") or "").strip()[:MAX_SEARCH_LEN]

    return PageParams(
        page=page,
        per_page=per_page,
        sort=sort,
        direction=direction,
        search=search,
    )


def _escape_like(term):
    """Échappe les métacaractères LIKE (``%``, ``_``) et l'échappement lui-même.

    Sans cela, un terme contenant ``%`` filtrerait tout : la recherche doit
    traiter la saisie comme du texte littéral, pas comme un motif.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def apply_search_sort(query, params, *, sort_columns=None, search_columns=None):
    """Applique recherche (OR ``ilike``) puis tri (colonne de la liste blanche).

    :param sort_columns: dict ``clé -> colonne SQLAlchemy`` ; ``params.sort`` a
        déjà été validé contre les clés par :func:`parse_page_params`, mais on
        revérifie l'appartenance ici (défense en profondeur).
    :param search_columns: colonnes texte sur lesquelles rechercher (OR).
    """
    if params.search and search_columns:
        from sqlalchemy import or_

        like = f"%{_escape_like(params.search)}%"
        clauses = [col.ilike(like, escape="\\") for col in search_columns]
        query = query.filter(or_(*clauses))

    sort_columns = sort_columns or {}
    if params.sort and params.sort in sort_columns:
        col = sort_columns[params.sort]
        query = query.order_by(col.desc() if params.direction == "desc" else col.asc())

    return query


def paginate_query(query, params, *, sort_columns=None, search_columns=None):
    """Applique recherche + tri + page et renvoie l'objet ``Pagination``.

    ``max_per_page`` est fixé à ``params.per_page`` (déjà plafonné) pour que
    Flask-SQLAlchemy ne puisse pas relire une valeur plus grande depuis la
    requête. ``error_out=False`` : une page hors bornes renvoie une page vide
    plutôt qu'un 404.
    """
    query = apply_search_sort(
        query, params, sort_columns=sort_columns, search_columns=search_columns
    )
    return query.paginate(
        page=params.page,
        per_page=params.per_page,
        max_per_page=params.per_page,
        error_out=False,
    )
