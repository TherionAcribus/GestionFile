"""Erreur du serveur ≠ connexion perdue, sur la borne patient.

Avant : toute réponse en erreur (4xx/5xx) affichait l'écran « connexion
perdue », et un bouton sans activité faisait renvoyer RIEN à la vue
(erreur 500). Par ailleurs, une activité fermée entre l'affichage et le clic
renvoyait le seul message « indisponible » dans la zone centrale : les
boutons de l'accueil disparaissaient, le patient n'avait plus rien à toucher.

Verrouillé ici :

1. le COMPORTEMENT de ``static/js/patient_request_errors.js`` (Node, faux
   environnement) : classement réseau / serveur / ignoré, message dédié,
   retour à l'accueil (bouton ou automatique), jamais pendant une impression ;
2. le câblage dans ``patients.js`` et le gabarit ;
3. côté serveur : bouton sans activité ou activité fermée -> accueil
   (marqué ``X-Kiosk-Screen: home``) AVEC le message, sans inscription.
"""

import json
import os
import re
import shutil
import subprocess
import textwrap

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from models import db, Activity, ActivitySchedule, Button, Language, Patient, Weekday

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_SCRIPT = os.path.join(_SERVEUR, "static", "js", "patient_request_errors.js")


def _lire(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# --- 1. Comportement du script (Node) -----------------------------------------

_HARNESS = textwrap.dedent(r"""
    const fs = require('fs');
    const vm = require('vm');
    const assert = require('assert');
    const source = fs.readFileSync(process.argv[2], 'utf8');
    const plain = v => JSON.parse(JSON.stringify(v));

    function kiosk() {
        const timers = [];
        let nextId = 1;
        const screens = [];   // appels à setKioskOffline
        const homes = [];
        const ctx = {
            console: { error() {} },
            setTimeout(fn, ms) { const t = { id: nextId++, fn, ms, live: true }; timers.push(t); return t.id; },
            clearTimeout(id) { timers.forEach(t => { if (t.id === id) { t.live = false; } }); },
            setKioskOffline(show, options) { screens.push({ show, options: options || null }); },
            kioskOfflineLabels() {
                return { server_title: 'Problème', server_message: 'Recommencez', home: 'Accueil' };
            },
            printFlowActive: () => false,
            PatientIdle: { goHome() { homes.push(1); } },
        };
        vm.createContext(ctx);
        vm.runInContext(source, ctx);
        return {
            ctx, screens, homes,
            live() { return timers.filter(t => t.live); },
        };
    }

    function responseError(status, eltId) {
        return { type: 'htmx:responseError',
                 detail: { xhr: { status }, elt: { id: eltId || 'div_validation' },
                           pathInfo: { requestPath: '/patients_submit' } } };
    }

    const results = {};
    function scenario(name, fn) {
        try { fn(); results[name] = 'ok'; }
        catch (e) { results[name] = String(e && e.stack || e); }
    }

    scenario('classement_des_echecs', () => {
        const k = kiosk();
        const kind = k.ctx.kioskFailureKind;
        assert.strictEqual(kind(undefined), 'network', 'sonde (pas d’évènement)');
        assert.strictEqual(kind({ type: 'htmx:sendError', detail: {} }), 'network');
        assert.strictEqual(kind({ type: 'htmx:timeout', detail: {} }), 'network');
        [500, 400, 404, 410].forEach(s =>
            assert.strictEqual(kind(responseError(s)), 'server', String(s)));
        // Proxy (Coolify…) : serveur applicatif absent = panne de connexion.
        [0, 502, 503, 504].forEach(s =>
            assert.strictEqual(kind(responseError(s)), 'network', String(s)));
        assert.strictEqual(kind(responseError(500, 'div_title_area')), 'ignore');
    });

    scenario('message_dedie_avec_bouton_accueil', () => {
        const k = kiosk();
        k.ctx.showKioskServerError(responseError(500));
        assert.strictEqual(k.screens.length, 1);
        const shown = k.screens[0];
        assert.strictEqual(shown.show, true);
        assert.strictEqual(shown.options.title, 'Problème');
        assert.strictEqual(shown.options.message, 'Recommencez');
        assert.deepStrictEqual(plain(shown.options.buttons.map(b => b.label)), ['Accueil']);
        shown.options.buttons[0].onClick();
        assert.deepStrictEqual(plain(k.screens[1]), { show: false, options: null });
        assert.strictEqual(k.homes.length, 1);
        assert.strictEqual(k.live().length, 0, 'retour automatique annulé');
    });

    scenario('retour_automatique_apres_10_s', () => {
        const k = kiosk();
        k.ctx.showKioskServerError(responseError(500));
        const live = k.live();
        assert.strictEqual(live.length, 1);
        assert.strictEqual(live[0].ms, 10000);
        live[0].live = false;
        live[0].fn();
        assert.strictEqual(k.screens[k.screens.length - 1].show, false);
        assert.strictEqual(k.homes.length, 1);
    });

    scenario('jamais_pendant_une_impression', () => {
        const k = kiosk();
        k.ctx.printFlowActive = () => true;
        k.ctx.showKioskServerError(responseError(500));
        k.live()[0].fn();
        assert.strictEqual(k.screens[k.screens.length - 1].show, false, 'message masqué');
        assert.strictEqual(k.homes.length, 0, 'écran d’impression conservé');
    });

    scenario('une_seule_minuterie_et_annulation', () => {
        const k = kiosk();
        k.ctx.showKioskServerError(responseError(500));
        k.ctx.showKioskServerError(responseError(404));
        assert.strictEqual(k.live().length, 1);
        k.ctx.clearKioskServerError();
        assert.strictEqual(k.live().length, 0);
    });

    process.stdout.write(JSON.stringify(results));
""")

_SCENARIOS = [
    "classement_des_echecs",
    "message_dedie_avec_bouton_accueil",
    "retour_automatique_apres_10_s",
    "jamais_pendant_une_impression",
    "une_seule_minuterie_et_annulation",
]


@pytest.fixture(scope="module")
def node_results(tmp_path_factory):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js absent : comportement JS non exécutable ici")
    harness = tmp_path_factory.mktemp("errors") / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    proc = subprocess.run([node, str(harness), _SCRIPT],
                          capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_comportement(node_results, scenario):
    assert node_results.get(scenario) == "ok", node_results.get(scenario)


def test_tous_les_scenarios_sont_verifies(node_results):
    assert sorted(node_results) == sorted(_SCENARIOS)


# --- 2. Câblage -----------------------------------------------------------------

def _corps_fonction(source, nom):
    debut = source.index(f"function {nom}(")
    suite = re.search(r"\n}\n", source[debut:])
    return source[debut:debut + suite.end()]


def test_patients_js_distingue_erreur_serveur_et_reseau():
    corps = _corps_fonction(_lire("static/js/patients.js"), "handleKioskRequestFailure")
    assert "kioskFailureKind(evt)" in corps
    assert "showKioskServerError(evt)" in corps
    # Panne réseau : le retour automatique d'un message serveur est annulé.
    assert "clearKioskServerError()" in corps
    assert corps.index("showKioskServerError") < corps.index("setKioskOffline(true")


def test_libelles_traduits():
    source = _lire("static/js/patients.js")
    for cle in ("server_title", "server_message", "home"):
        assert source.count(f"{cle}:") >= 2, cle   # fr + en


def test_gabarit_charge_le_script_avant_le_minuteur():
    source = _lire("templates/patient/patient_front_page.html")
    assert (source.index("js/patients.js")
            < source.index("js/patient_request_errors.js")
            < source.index("js/patient_idle.js"))


def test_minuteur_reconnait_l_accueil_par_l_en_tete():
    source = _lire("static/js/patient_idle.js")
    assert "X-Kiosk-Screen" in source
    assert "goHome: goHome" in source


# --- 3. Serveur ------------------------------------------------------------------

@pytest.fixture
def application(monkeypatch):
    import routes.patient as patient_routes
    monkeypatch.setattr(patient_routes, "communikation", lambda *a, **k: None)
    app = Flask(__name__, template_folder=os.path.join(_SERVEUR, "templates"))
    app.config.update(
        SECRET_KEY="secret-test-request-errors",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        TESTING=True,
        PAGE_PATIENT_SUBTITLE="Touchez le motif de votre visite",
        PAGE_PATIENT_INTERFACE_VALIDATE_CANCEL="Annuler",
        PAGE_PATIENT_DISABLE_DEFAULT_MESSAGE="Activité indisponible",
        PAGE_PATIENT_TIMER_ACTIVITY_INACTIVE=10,
    )
    db.init_app(app)
    from routes.patient import patient_bp
    app.register_blueprint(patient_bp)
    with app.app_context():
        db.create_all()
        db.session.add(Language(code="fr", name="Français", translation="Français"))
        db.session.add(Button(label="Ordonnance", is_present=True, is_active=True,
                              shape="circle", sort_order=1))
        db.session.commit()
        yield app


@pytest.fixture
def client(application):
    return application.test_client()


def _activite_fermee(application):
    """Seule plage horaire sur un AUTRE jour : fermée à toute heure du test."""
    from datetime import datetime, time
    from activity_explain import ENGLISH_DAY_NAMES
    from config import time_tz

    autre_jour = ENGLISH_DAY_NAMES[(datetime.now(time_tz).weekday() + 1) % 7]
    with application.app_context():
        jour = Weekday(name="autre", english_name=autre_jour, abbreviation="AUT")
        plage = ActivitySchedule(name="autre jour",
                                 start_time=time(0, 0), end_time=time(23, 59))
        plage.weekdays.append(jour)
        activite = Activity(name="Fermée", letter="F", notification=False,
                            specific_message="", inactivity_message="Réouverture demain")
        activite.schedules.append(plage)
        db.session.add_all([jour, plage, activite])
        db.session.commit()
        return activite.id


def _accueil_avec_message(reponse, message):
    html = reponse.get_data(as_text=True)
    assert reponse.status_code == 200
    assert reponse.headers.get("X-Kiosk-Screen") == "home"
    assert "Ordonnance" in html, "les boutons de l'accueil restent affichés"
    assert message in html
    # Le message prend la place du sous-titre (échange hors bande)…
    assert 'id="div_buttons_children"' in html and 'hx-swap-oob="true"' in html
    # … au lieu du sous-titre standard (qui n'apparaît que dans l'attribut
    # servant à le rétablir après le délai).
    assert "data-activity-inactive" in html
    assert not re.search(r'div_text_explanation"\s*>\s*Touchez', html)


@pytest.mark.parametrize("activity_id", ["", "abc", "999999"])
def test_bouton_sans_activite_valide_renvoie_l_accueil(client, activity_id):
    reponse = client.post("/patients_submit", data={
        "activity_id": activity_id, "is_active": "True", "is_parent": "False"})
    _accueil_avec_message(reponse, "Activité indisponible")


def test_activite_fermee_au_clic_renvoie_l_accueil(client, application):
    activite_id = _activite_fermee(application)
    reponse = client.post("/patients_submit", data={
        "activity_id": str(activite_id), "is_active": "True", "is_parent": "False"})
    _accueil_avec_message(reponse, "Réouverture demain")
    with application.app_context():
        assert Patient.query.count() == 0


@pytest.mark.parametrize("route", ["/patient/print_and_validate", "/patient/scan_and_validate"])
def test_validation_perimee_renvoie_l_accueil(client, application, route):
    activite_id = _activite_fermee(application)
    reponse = client.post(route, data={"activity_id": str(activite_id), "journey": "j-x"},
                          headers={"HX-Request": "true"})
    _accueil_avec_message(reponse, "Réouverture demain")
    with application.app_context():
        assert Patient.query.count() == 0


def test_bouton_grise_garde_le_message_dans_le_sous_titre(client, application):
    """Bouton grisé (is_active=False) : la requête vise déjà le sous-titre —
    réponse inchangée, sans accueil ni marqueur."""
    activite_id = _activite_fermee(application)
    reponse = client.post("/patients_submit", data={
        "activity_id": str(activite_id), "is_active": "False"})
    html = reponse.get_data(as_text=True)
    assert "Réouverture demain" in html
    assert "X-Kiosk-Screen" not in reponse.headers
    assert "Ordonnance" not in html


def test_accueil_marque_et_annulation(client):
    for route in ("/patient/patient_buttons", "/patient/patient_cancel",
                  "/patient/cancel_patient"):
        reponse = client.get(route)
        assert reponse.headers.get("X-Kiosk-Screen") == "home", route
        assert "Touchez le motif de votre visite" in reponse.get_data(as_text=True)


def test_accueil_sans_bouton_garde_le_message(client, application):
    with application.app_context():
        Button.query.delete()
        db.session.commit()
    reponse = client.post("/patients_submit", data={
        "activity_id": "", "is_active": "True", "is_parent": "False"})
    html = reponse.get_data(as_text=True)
    assert reponse.headers.get("X-Kiosk-Screen") == "home"
    assert "Aucun bouton configuré" in html
    assert "Activité indisponible" in html
    # Un seul échange hors bande du sous-titre : celui du message.
    assert html.count('id="div_buttons_children"') == 1


# --- Titre : rafraîchi par la borne concernée, plus diffusé à toutes ---------

def test_accueil_ne_diffuse_plus_refresh_title(application, monkeypatch):
    import routes.patient as patient_routes
    emissions = []
    monkeypatch.setattr(patient_routes, "communikation",
                        lambda *a, **k: emissions.append((a, k)))
    client = application.test_client()
    for route in ("/patient/patient_buttons", "/patient/patient_cancel"):
        assert client.get(route).headers.get("X-Kiosk-Screen") == "home"
    client.post("/patients_submit", data={
        "activity_id": "", "is_active": "True", "is_parent": "False"})
    assert not [e for e in emissions if e[1].get("event") == "refresh_title"]


def test_borne_rafraichit_son_titre_au_retour_accueil():
    source = _lire("static/js/patients.js")
    debut = source.index("document.body.addEventListener('htmx:afterSwap'")
    gestionnaire = source[debut:debut + 2500]
    assert "getResponseHeader('X-Kiosk-Screen') === 'home'" in gestionnaire
    assert "refresh_title();" in gestionnaire
