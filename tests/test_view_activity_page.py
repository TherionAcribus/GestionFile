"""Page /admin/activity refaite : cartes lisibles, formulaires, planification.

Couvre :
- le noyau pur ``activity_explain`` (résumé de plage, « dans ses horaires »,
  lettres partagées) ;
- les routes : listes en cartes, modification/création par formulaire
  (erreur = 204 sans remplacement), plages horaires validées ;
- les corrections de planification : un renommage retire les anciennes
  tâches, une suppression aussi, la création passe par
  ``update_scheduler_for_activity`` et une plage modifiée replanifie les
  activités qui l'utilisent.

Nom de fichier trié après test_upload_security : ce fichier enregistre le
bind 'users' dans db.metadatas, partagé entre fichiers de test.
"""

from datetime import datetime, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import routes.admin_activity as admin_activity
import routes.admin_schedule as admin_schedule
from activity_explain import describe_schedule, is_continuous, is_open_at, shared_letters, weekday_codes
from models import Activity, ActivitySchedule, Pharmacist, Role, User, Weekday, db

SERVEUR_DIR = Path(__file__).resolve().parents[1]
_LUNDI_10H = datetime(2024, 1, 1, 10, 0, 0)  # 1er janvier 2024 : un lundi
_DAYS = [("Lundi", "monday", "mon"), ("Mardi", "tuesday", "tue"),
         ("Mercredi", "wednesday", "wed"), ("Jeudi", "thursday", "thu"),
         ("Vendredi", "friday", "fri"), ("Samedi", "saturday", "sat"),
         ("Dimanche", "sunday", "sun")]


def _day(english):
    return SimpleNamespace(english_name=english)


def _sched(start, end, *days):
    return SimpleNamespace(start_time=start, end_time=end,
                           weekdays=[_day(d) for d in days])


# --- Noyau pur ----------------------------------------------------------------

def test_weekday_codes_et_resume():
    s = _sched(time(9), time(19), "monday", "tuesday", "wednesday", "thursday", "friday")
    assert weekday_codes(s.weekdays) == "Mon,Tue,Wed,Thu,Fri"
    assert describe_schedule(s) == "du lundi au vendredi, 09:00–19:00"
    full = _sched(time(0), time(23, 59), *(d[1] for d in _DAYS))
    assert describe_schedule(full) == "tous les jours, toute la journée"


def test_is_open_at_bornes_incluses_et_sans_plage():
    s = [_sched(time(9), time(12), "monday")]
    assert is_open_at(s, "Monday", time(9))
    assert is_open_at(s, "Monday", time(12))
    assert not is_open_at(s, "Monday", time(12, 1))
    assert not is_open_at(s, "Tuesday", time(10))
    # Règle : sans plage, l'activité est proposée EN CONTINU (défaut).
    assert is_open_at([], "Sunday", time(3))


def test_en_continu_par_defaut_et_plage_pleine():
    full = _sched(time(0), time(23, 59), *(d[1] for d in _DAYS))
    assert is_continuous([]) and is_continuous([full])
    assert not is_continuous([_sched(time(9), time(12), "monday")])
    # Plage 00:00–23:59 : pas de « fermeture » pendant la dernière minute.
    assert is_open_at([full], "Friday", time(23, 59, 30))


def test_shared_letters():
    acts = [SimpleNamespace(id=1, name="Ordonnance", letter="A"),
            SimpleNamespace(id=2, name="Conseil", letter="a"),
            SimpleNamespace(id=3, name="Retrait", letter="R")]
    assert shared_letters(acts) == {1: ["Conseil"], 2: ["Ordonnance"]}


# --- Routes ---------------------------------------------------------------------

class _FakeScheduler:
    def __init__(self, job_ids=()):
        self.jobs = {j: SimpleNamespace(id=j) for j in job_ids}
        self.added = []

    def get_jobs(self):
        return list(self.jobs.values())

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)

    def add_job(self, id, func, args, trigger, **kw):
        self.added.append(SimpleNamespace(id=id, func=func, args=args, **kw))
        self.jobs[id] = SimpleNamespace(id=id)


@pytest.fixture()
def fake_scheduler():
    fake = _FakeScheduler(["enable_Ordonnance_mon_0900", "disable_Ordonnance_mon_1200",
                           "enable_Autre_mon_0900"])
    with patch.object(admin_activity, "scheduler", fake):
        yield fake


@pytest.fixture()
def app(tmp_path, fake_scheduler):
    app = Flask(__name__, root_path=str(SERVEUR_DIR))
    app.config.update(
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
    )
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=user_id).first()
        return user if user and user.active else None

    app.register_blueprint(admin_activity.admin_activity_bp)
    app.register_blueprint(admin_schedule.admin_schedule_bp)
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
        admin.roles.append(Role(name="admin", admin_activity=True, admin_schedule=True))
        db.session.add(admin)
        days = [Weekday(name=n, english_name=e, abbreviation=a) for n, e, a in _DAYS]
        db.session.add_all(days)
        matin = ActivitySchedule(name="Matin", start_time=time(9), end_time=time(12),
                                 weekdays=days[:5])
        samedi = ActivitySchedule(name="Samedi", start_time=time(9), end_time=time(12),
                                  weekdays=[days[5]])
        marie = Pharmacist(name="Marie", initials="MA")
        db.session.add_all([matin, samedi, marie])
        db.session.add_all([
            Activity(name="Ordonnance", letter="A", schedules=[matin], notification=True),
            Activity(name="Conseil", letter="A", schedules=[samedi]),
            Activity(name="Sans horaire", letter="S"),
            Activity(name="Voir Marie", letter="M", is_staff=True, staff=marie,
                     schedules=[matin]),
        ])
        db.session.commit()
    with patch.object(admin_activity, "_now", return_value=_LUNDI_10H), \
            patch.object(admin_schedule, "communikation", lambda *a, **k: None), \
            patch("ui_feedback.communikation", lambda *a, **k: None):
        yield app


@pytest.fixture()
def client(app):
    c = app.test_client()
    c.post("/_test/login")
    return c


def _get(app, model, **by):
    with app.app_context():
        return model.query.filter_by(**by).one()


def _form(**over):
    data = {"name": "Ordonnance", "letter": "a", "specific_message": "",
            "inactivity_message": "", "schedules": []}
    data.update(over)
    return data


def test_liste_en_cartes(client):
    html = client.get("/admin/activity/table").get_data(as_text=True)
    assert "data-filter-item" in html
    assert "Voir Marie" not in html  # liste équipier séparée
    # Lundi 10h : Ordonnance (Matin) ouverte, Conseil (Samedi) fermée.
    assert html.count("Dans ses horaires") == 1 and "Hors horaires" in html
    # « Sans horaire » : proposée en continu (et non plus « jamais proposée »).
    assert "En continu" in html and "Jamais proposée" not in html
    assert 'name="availability"' in html
    assert "même lettre que Conseil" in html
    assert "du lundi au vendredi, 09:00–12:00" in html
    assert 'name="schedules"' in html and 'name="notification"' in html


def test_liste_equipier(client):
    html = client.get("/admin/activity/table_staff").get_data(as_text=True)
    assert "Voir Marie" in html and "Pour : <strong>Marie</strong>" in html
    assert 'name="staff_id"' in html


def test_page_ouvre_l_onglet_demande(client):
    captured = {}
    with patch.object(admin_activity, "render_template",
                      lambda tpl, **kw: captured.update(kw) or ""):
        client.get("/admin/activity?tab=schedule")
        assert captured["active_tab"] == "schedule"
        client.get("/admin/activity?tab=nimporte")
        assert captured["active_tab"] == "activity"


def test_modification_renomme_et_retire_les_anciennes_taches(app, client, fake_scheduler):
    act = _get(app, Activity, name="Ordonnance")
    matin = _get(app, ActivitySchedule, name="Matin")
    response = client.post(f"/admin/activity/activity_update/{act.id}",
                           data=_form(name="Ordonnances", letter="o",
                                      schedules=[str(matin.id)], notification="true"))
    assert response.status_code == 200
    assert "Ordonnances" in response.get_data(as_text=True)
    updated = _get(app, Activity, id=act.id)
    assert (updated.name, updated.letter, updated.notification) == ("Ordonnances", "O", True)
    # Anciennes tâches (ancien nom) retirées, nouvelles créées, autres intactes.
    assert not any(j.startswith(("enable_Ordonnance_", "disable_Ordonnance_"))
                   for j in fake_scheduler.jobs)
    assert "enable_Ordonnances_mon_0900" in fake_scheduler.jobs
    assert "enable_Autre_mon_0900" in fake_scheduler.jobs


@pytest.mark.parametrize("over", [
    {"letter": "AB"},
    {"letter": "-"},
    {"name": ""},
])
def test_modification_invalide_ne_touche_rien(app, client, over):
    act = _get(app, Activity, name="Ordonnance")
    response = client.post(f"/admin/activity/activity_update/{act.id}", data=_form(**over))
    assert response.status_code == 204
    assert _get(app, Activity, id=act.id).letter == "A"


def test_en_continu_retire_les_plages(app, client):
    act = _get(app, Activity, name="Ordonnance")
    matin = _get(app, ActivitySchedule, name="Matin")
    # Même si des plages sont cochées, « En continu » l'emporte.
    response = client.post(f"/admin/activity/activity_update/{act.id}",
                           data=_form(availability="always", schedules=[str(matin.id)]))
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(Activity, act.id).schedules == []


def test_mode_plages_sans_plage_cochee_refuse(app, client):
    act = _get(app, Activity, name="Ordonnance")
    response = client.post(f"/admin/activity/activity_update/{act.id}",
                           data=_form(availability="schedules"))
    assert response.status_code == 204
    with app.app_context():
        assert len(db.session.get(Activity, act.id).schedules) == 1


def test_formulaire_de_creation_en_continu_par_defaut(client):
    html = client.get("/admin/activity/add_form").get_data(as_text=True)
    always = html.split('value="always"', 1)[1].split(">", 1)[0]
    assert "checked" in always
    restricted = html.split('value="schedules"', 1)[1].split(">", 1)[0]
    assert "checked" not in restricted


def test_demande_nominative_exige_un_membre(app, client):
    act = _get(app, Activity, name="Voir Marie")
    response = client.post(f"/admin/activity/activity_update/{act.id}",
                           data=_form(name="Voir Marie", letter="M"))
    assert response.status_code == 204
    assert _get(app, Activity, id=act.id).staff_id is not None


def test_creation_planifie_comme_la_modification(app, client, fake_scheduler):
    matin = _get(app, ActivitySchedule, name="Matin")
    response = client.post("/admin/activity/add_new_activity",
                           data=_form(name="Vaccin", letter="v", schedules=[str(matin.id)]))
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Vaccin" in body and "div_add_activity_form" in body
    created = [j for j in fake_scheduler.added if j.id.startswith("enable_Vaccin_")]
    assert created and created[0].func == "scheduler_functions:enable_buttons_for_activity_job"
    assert _get(app, Activity, name="Vaccin").letter == "V"


def test_creation_invalide_conserve_le_formulaire(client):
    response = client.post("/admin/activity/add_new_activity", data=_form(name="X", letter=""))
    assert response.status_code == 204


def test_suppression_retire_les_taches(app, client, fake_scheduler):
    act = _get(app, Activity, name="Ordonnance")
    response = client.delete(f"/admin/activity/delete/{act.id}")
    assert response.status_code == 200
    assert not any("Ordonnance" in j for j in fake_scheduler.jobs)


def test_plages_en_cartes(client):
    html = client.get("/admin/schedule/table").get_data(as_text=True)
    assert "Du lundi au vendredi, 09:00–12:00" in html
    assert "Utilisée par : Ordonnance, Voir Marie" in html or \
        "Utilisée par : Voir Marie, Ordonnance" in html
    assert 'type="time"' in html


@pytest.mark.parametrize("over,raison", [
    ({"start_time": "12:00", "end_time": "09:00"}, "fin avant début"),
    ({"start_time": "9h"}, "format"),
    ({"weekdays": []}, "aucun jour"),
    ({"name_schedule": ""}, "nom"),
])
def test_plage_invalide_refusee(app, client, over, raison):
    matin = _get(app, ActivitySchedule, name="Matin")
    data = {"name_schedule": "Matin", "start_time": "08:00", "end_time": "12:00",
            "weekdays": ["1"]}
    data.update(over)
    response = client.post(f"/admin/schedule/schedule_update/{matin.id}", data=data)
    assert response.status_code == 204, raison
    assert _get(app, ActivitySchedule, id=matin.id).start_time == time(9)


def test_plage_modifiee_replanifie_les_activites(app, client, fake_scheduler):
    matin = _get(app, ActivitySchedule, name="Matin")
    lundi = _get(app, Weekday, english_name="monday")
    response = client.post(f"/admin/schedule/schedule_update/{matin.id}",
                           data={"name_schedule": "Matin", "start_time": "08:30",
                                 "end_time": "12:00", "weekdays": [str(lundi.id)]})
    assert response.status_code == 200
    assert _get(app, ActivitySchedule, id=matin.id).start_time == time(8, 30)
    # Tâches de l'activité recréées à la nouvelle heure (auparavant : jamais).
    assert "enable_Ordonnance_mon_0830" in fake_scheduler.jobs
    assert "enable_Ordonnance_mon_0900" not in fake_scheduler.jobs


def test_creation_de_plage(client):
    response = client.post("/admin/schedule/add_new_schedule",
                           data={"name_schedule": "Soir", "start_time": "17:00",
                                 "end_time": "19:00", "weekdays": ["1", "2"]})
    assert response.status_code == 200
    assert "Soir" in response.get_data(as_text=True)


def test_confirmation_suppression_plage_liste_les_activites(app, client):
    matin = _get(app, ActivitySchedule, name="Matin")
    html = client.get(f"/admin/schedule/confirm_delete/{matin.id}").get_data(as_text=True)
    assert "Ordonnance" in html and "Voir Marie" in html
