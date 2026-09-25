"""Page /admin/algo refaite : explications lisibles et état en direct.

Couvre :
- le noyau pur ``algo_explain`` (phrase de règle, jours, état à l'instant —
  mêmes critères que ``python.engine.get_applicable_algo_rules``) ;
- les routes : bloc d'état (désactivé / pause garde-fou / règles en vigueur),
  liste en cartes, édition via formulaire (erreur = 204 sans remplacement,
  succès = liste re-rendue + événement algoChanged), jours tous décochés.

Nom de fichier trié après test_upload_security : ce fichier enregistre le
bind 'users' dans db.metadatas, partagé entre fichiers de test.
"""

import json
from datetime import datetime, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import routes.admin_algo as admin_algo
from algo_explain import (
    STATE_ACTIVE, STATE_OFF_DAY, STATE_OFF_HOURS, STATE_QUEUE_RANGE,
    describe_days, rule_state, summarize_rule,
)
from models import Activity, AlgoRule, ConfigOption, Language, Patient, Role, User, db

SERVEUR_DIR = Path(__file__).resolve().parents[1]
_LUNDI_10H = datetime(2024, 1, 1, 10, 0, 0)  # 1er janvier 2024 : un lundi


def _rule(**kw):
    base = dict(name="R", activity=SimpleNamespace(name="Retrait rapide"),
                priority_level=1, min_patients=0, max_patients=999,
                max_overtaken=3, start_time=time(9, 0), end_time=time(12, 0),
                days_of_week="Mon,Tue,Wed,Thu,Fri")
    base.update(kw)
    return SimpleNamespace(**base)


# --- Noyau pur ----------------------------------------------------------------

@pytest.mark.parametrize("days,expected", [
    ("Mon,Tue,Wed,Thu,Fri,Sat,Sun", "tous les jours"),
    ("Mon,Tue,Wed,Thu,Fri", "du lundi au vendredi"),
    ("Fri,Mon,Wed,Thu,Tue", "du lundi au vendredi"),  # ordre de saisie indifférent
    ("Sat", "le samedi"),
    ("Mon,Wed", "lun., mer."),
    ("Sat,Sun", "sam., dim."),
    ("", "aucun jour"),
])
def test_describe_days(days, expected):
    assert describe_days(days) == expected


def test_summarize_rule_phrase_complete():
    assert summarize_rule(_rule()) == (
        "Les patients « Retrait rapide » passent devant 3 patients au maximum, "
        "du lundi au vendredi, de 09:00 à 12:00.")


def test_summarize_rule_bornes_et_limites():
    s = summarize_rule(_rule(max_overtaken=999, start_time=time(0, 0),
                             end_time=time(23, 59), min_patients=5))
    assert "passent devant tous les patients en attente" in s
    assert "toute la journée" in s
    assert "à partir de 5 patients en attente" in s
    s = summarize_rule(_rule(max_overtaken=1, max_patients=10))
    assert "passent devant 1 patient au maximum" in s
    assert "tant qu'il y a au plus 10 patients en attente" in s
    assert "quand 2 à 8 patients attendent" in summarize_rule(
        _rule(min_patients=2, max_patients=8))
    assert "règle sans effet" in summarize_rule(_rule(max_overtaken=0))


def test_rule_state_memes_criteres_que_le_moteur():
    r = _rule(min_patients=2, max_patients=8)
    assert rule_state(r, now_time=time(10), day_abbr="Mon", waiting=5)[0] == STATE_ACTIVE
    # Bornes inclusives (comme get_applicable_algo_rules).
    assert rule_state(r, now_time=time(12), day_abbr="Mon", waiting=8)[0] == STATE_ACTIVE
    assert rule_state(r, now_time=time(9), day_abbr="Mon", waiting=2)[0] == STATE_ACTIVE
    assert rule_state(r, now_time=time(10), day_abbr="Sat", waiting=5)[0] == STATE_OFF_DAY
    assert rule_state(r, now_time=time(13), day_abbr="Mon", waiting=5)[0] == STATE_OFF_HOURS
    code, text = rule_state(r, now_time=time(10), day_abbr="Mon", waiting=1)
    assert code == STATE_QUEUE_RANGE and "1 patient en attente" in text


# --- Routes ---------------------------------------------------------------------

@pytest.fixture()
def app(tmp_path):
    app = Flask(__name__, root_path=str(SERVEUR_DIR))
    app.config.update(
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
        ALGO_IS_ACTIVATED=True,
        ALGO_OVERTAKEN_LIMIT=3,
    )
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=user_id).first()
        return user if user and user.active else None

    app.register_blueprint(admin_algo.admin_algo_bp)
    app.add_url_rule("/login", endpoint="security.login", view_func=lambda: "")

    def test_login():
        from flask_login import login_user
        login_user(User.query.filter_by(username="admin").first())
        return jsonify(authenticated=True)

    app.add_url_rule("/_test/login", view_func=test_login, methods=["POST"])

    with app.app_context():
        db.create_all()
        admin = User(username="admin", email="a@a.a",
                     password=generate_password_hash("x"), active=True)
        admin.roles.append(Role(name="admin", admin_algo=True))
        db.session.add(admin)
        rapide = Activity(name="Retrait rapide", letter="R")
        normale = Activity(name="Normale", letter="N")
        db.session.add_all([rapide, normale,
                            Language(code="fr", name="Français", translation="Français"),
                            ConfigOption(config_key="algo_activate", value_bool=True)])
        db.session.flush()
        db.session.add_all([
            AlgoRule(name="Matin rapide", activity_id=rapide.id, priority_level=1,
                     max_overtaken=3, start_time=time(9), end_time=time(12),
                     days_of_week="Mon,Tue,Wed,Thu,Fri"),
            AlgoRule(name="Samedi", activity_id=normale.id, priority_level=2,
                     max_overtaken=999, start_time=time(0), end_time=time(23, 59),
                     days_of_week="Sat"),
        ])
        db.session.commit()
    with patch.object(admin_algo, "_now", return_value=_LUNDI_10H):
        yield app


@pytest.fixture()
def client(app):
    c = app.test_client()
    c.post("/_test/login")
    return c


def _ids(app):
    with app.app_context():
        rule = AlgoRule.query.filter_by(name="Matin rapide").one()
        return rule.id, rule.activity_id


def _form(activity_id, **over):
    data = {"name": "Matin rapide", "activity_id": str(activity_id),
            "priority_level": "1", "min_patients": "0", "max_patients": "999",
            "max_overtaken": "3", "start_time": "09:00", "end_time": "12:00",
            "days_of_week": ["Mon", "Tue"], "days_of_week_present": "1"}
    data.update(over)
    return data


def test_liste_en_cartes_avec_resume_et_etat(client):
    html = client.get("/admin/algo/table").get_data(as_text=True)
    assert "data-filter-item" in html
    assert "Les patients « Retrait rapide » passent devant 3 patients au maximum" in html
    # Lundi 10h : « Matin rapide » en vigueur, « Samedi » non.
    assert html.count("En vigueur") == 1
    assert "Pas aujourd&#39;hui" in html or "Pas aujourd'hui" in html
    # Tri par niveau : la règle de niveau 1 d'abord.
    assert html.index("Matin rapide") < html.index("Samedi")


def test_etat_regle_en_vigueur(client):
    html = client.get("/admin/button_des_activate_algo").get_data(as_text=True)
    assert 'role="switch"' in html and "checked" in html
    assert "1 règle en vigueur en ce moment" in html and "Matin rapide" in html
    assert "action=deactivate" in html


def test_etat_pause_garde_fou(app, client):
    with app.app_context():
        act = Activity.query.first().id
        db.session.add(Patient(call_number="R-7", status="standing",
                               activity_id=act, overtaken=4,
                               language_id=Language.query.first().id))
        db.session.commit()
    html = client.get("/admin/button_des_activate_algo").get_data(as_text=True)
    assert "En pause" in html and "R-7" in html and "4 fois" in html


def test_etat_desactive(app, client):
    app.config["ALGO_IS_ACTIVATED"] = False
    html = client.get("/admin/button_des_activate_algo").get_data(as_text=True)
    assert "ordre d&#39;arrivée" in html or "ordre d'arrivée" in html
    assert "action=activate" in html


def test_bascule_renvoie_le_bloc_d_etat(app, client):
    response = client.post("/admin/algo/toggle_activation?action=deactivate")
    assert response.status_code == 200
    assert "action=activate" in response.get_data(as_text=True)
    assert app.config["ALGO_IS_ACTIVATED"] is False


def test_modification_reussie_renvoie_la_liste_et_algo_changed(app, client):
    rule_id, act = _ids(app)
    response = client.post(f"/admin/algo/rule_update/{rule_id}",
                           data=_form(act, max_overtaken="5"))
    assert response.status_code == 200
    assert "algoChanged" in json.loads(response.headers["HX-Trigger"])
    assert "passent devant 5 patients au maximum" in response.get_data(as_text=True)
    with app.app_context():
        rule = db.session.get(AlgoRule, rule_id)
        assert rule.max_overtaken == 5 and rule.days_of_week == "Mon,Tue"


def test_modification_invalide_ne_remplace_pas_la_liste(app, client):
    rule_id, act = _ids(app)
    response = client.post(f"/admin/algo/rule_update/{rule_id}",
                           data=_form(act, start_time="13:00", end_time="09:00"))
    # 204 : htmx ne remplace rien, la saisie reste dans le formulaire.
    assert response.status_code == 204
    with app.app_context():
        assert db.session.get(AlgoRule, rule_id).start_time == time(9)


def test_formulaire_tous_jours_decoches_refuse(app, client):
    rule_id, act = _ids(app)
    data = _form(act)
    del data["days_of_week"]
    response = client.post(f"/admin/algo/rule_update/{rule_id}", data=data)
    assert response.status_code == 204
    with app.app_context():
        assert db.session.get(AlgoRule, rule_id).days_of_week == "Mon,Tue,Wed,Thu,Fri"


def test_creation_via_formulaire(app, client):
    _, act = _ids(app)
    response = client.post("/admin/algo/add_new_rule",
                           data=_form(act, name="Après-midi", start_time="14:00",
                                      end_time="18:00"))
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Après-midi" in body and 'hx-swap-oob="innerHTML:#div_add_rule_form"' in body
    assert "algoChanged" in json.loads(response.headers["HX-Trigger"])


def test_formulaire_de_creation(client):
    html = client.get("/admin/algo/add_rule_form").get_data(as_text=True)
    assert 'name="days_of_week"' in html and 'name="days_of_week_present"' in html
    assert "Créer la règle" in html
