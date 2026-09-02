"""Phase 8, point 6 — écritures multi-tables atomiques.

Deux volets :

1. le gestionnaire ``atomic()`` lui-même, sur les **vrais** modèles en SQLite :
   il committe à la sortie du bloc et annule TOUT en cas d'erreur ;
2. une régression statique : les routes de création ne doivent plus enchaîner
   deux ``commit()``, motif qui laissait un objet créé sans ses relations quand
   la seconde étape échouait.
"""

import ast
import io
import os

import pytest
from flask import Flask

from models import db, Activity, ActivitySchedule, Counter, Pharmacist
from transactions import atomic

_RACINE = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    return io.open(os.path.join(_RACINE, rel), encoding="utf-8").read()


@pytest.fixture
def application():
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        TESTING=True,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app


# --- 1. Comportement d'atomic() --------------------------------------------

def test_atomic_committe_a_la_sortie(application):
    with atomic():
        db.session.add(Counter(name="C1", sort_order=1))

    assert Counter.query.count() == 1


def test_atomic_annule_tout_en_cas_derreur(application):
    """Le cas qui motive le point 6 : création + rattachement, échec au milieu."""
    activite = Activity(name="Ordonnance", letter="O")
    db.session.add(activite)
    db.session.commit()

    with pytest.raises(RuntimeError):
        with atomic():
            comptoir = Counter(name="C1", sort_order=1)
            db.session.add(comptoir)
            db.session.flush()          # l'INSERT part, la transaction reste ouverte
            comptoir.activities.append(activite)
            raise RuntimeError("echec du rattachement")

    # Rien ne doit subsister : ni le comptoir, ni l'association.
    assert Counter.query.count() == 0, (
        "le comptoir a survecu a l'echec : la transaction n'etait pas atomique"
    )


def test_atomic_propage_lexception(application):
    with pytest.raises(ValueError):
        with atomic():
            db.session.add(Counter(name="C1", sort_order=1))
            raise ValueError("boum")


def test_flush_donne_lidentifiant_sans_clore_la_transaction(application):
    with atomic():
        membre = Pharmacist(name="Membre", initials="M1")
        db.session.add(membre)
        db.session.flush()
        assert membre.id is not None, "flush doit attribuer la cle primaire"
    assert Pharmacist.query.count() == 1


def test_atomic_imbrique_des_ecritures_sur_plusieurs_tables(application):
    """Une création qui touche deux tables est validée d'un bloc."""
    with atomic():
        horaire = ActivitySchedule(name="Matin")
        db.session.add(horaire)
        db.session.flush()
        activite = Activity(name="Ordonnance", letter="O")
        activite.schedules.append(horaire)
        db.session.add(activite)

    assert Activity.query.count() == 1
    assert ActivitySchedule.query.count() == 1
    assert len(Activity.query.first().schedules) == 1


# --- 2. Régression statique : plus de double commit dans les créations ------

def _corps(rel, nom):
    arbre = ast.parse(_read(rel))
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.FunctionDef) and noeud.name == nom:
            return noeud
    raise AssertionError(f"{nom} introuvable dans {rel}")


def _compte_commits(noeud):
    return sum(
        1 for n in ast.walk(noeud)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "commit"
    )


def _utilise_atomic(noeud):
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "atomic"
        for n in ast.walk(noeud)
    )


@pytest.mark.parametrize("rel,nom", [
    ("routes/admin_counter.py", "add_new_counter"),
    ("routes/admin_staff.py", "add_new_staff"),
    ("routes/admin_schedule.py", "add_new_schedule"),
    ("routes/admin_activity.py", "add_new_activity"),
    ("routes/counter.py", "update_counter_staff"),
])
def test_creations_utilisent_une_transaction_unique(rel, nom):
    noeud = _corps(rel, nom)
    assert _utilise_atomic(noeud), f"{rel}::{nom} doit passer par atomic()"
    assert _compte_commits(noeud) == 0, (
        f"{rel}::{nom} committe encore explicitement : la transaction est "
        f"decoupee, un echec laisse un enregistrement incomplet"
    )


def test_deconnexion_ne_committe_plus_elle_meme():
    """La transaction appartient à l'appelant, sinon la séquence
    « déconnecter partout puis reconnecter ici » se scinde en deux."""
    noeud = _corps("routes/counter.py", "deconnect_staff_from_all_counters")
    assert _compte_commits(noeud) == 0


# --- 3. Régression statique : validation déclarative ------------------------

@pytest.mark.parametrize("rel,schema", [
    ("routes/admin_counter.py", "SCHEMA_COMPTOIR"),
    ("routes/admin_staff.py", "SCHEMA_MEMBRE"),
    ("routes/admin_schedule.py", "SCHEMA_HORAIRE"),
    ("routes/admin_activity.py", "SCHEMA_ACTIVITE"),
])
def test_creations_valident_par_schema(rel, schema):
    src = _read(rel)
    assert f"{schema} = (" in src, f"{rel} doit déclarer son formulaire"
    assert "valider(" in src and "extraire(" in src, (
        f"{rel} doit valider via form_validation, pas par des contrôles ad hoc"
    )
