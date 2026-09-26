"""Gardes-fous sur l'arbre des migrations Alembic.

Une seule tête doit exister : le déploiement lance ``upgrade head``
(manage.py, docker-compose) qui échoue avec « Multiple head revisions »
dès que deux branches coexistent — c'est arrivé avec les migrations
contraintes algo_rule (a9b8c7d6e5f4) et messagerie (f0a1b2c3d4e5).
"""
from pathlib import Path

from alembic.script import ScriptDirectory


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _script():
    return ScriptDirectory(str(MIGRATIONS_DIR))


def test_alembic_une_seule_tete():
    heads = _script().get_heads()
    assert len(heads) == 1, (
        f"Plusieurs têtes Alembic : {heads}. "
        "Ajoutez une migration de fusion (down_revision = tuple des têtes)."
    )


def test_alembic_chaque_revision_a_un_parent_existant():
    """Une down_revision inconnue casse upgrade/downgrade de la même façon."""
    script = _script()
    revisions = {rev.revision for rev in script.walk_revisions()}
    orphelines = []
    for rev in script.walk_revisions():
        parents = rev.down_revision or ()
        if isinstance(parents, str):
            parents = (parents,)
        orphelines.extend(p for p in parents if p not in revisions)
    assert not orphelines, f"Révisions parentes introuvables : {orphelines}"
