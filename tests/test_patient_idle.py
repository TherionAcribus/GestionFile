"""Retour automatique à l'accueil de la borne après inactivité.

Avant : seule la conclusion avait un minuteur. Un patient parti sur l'écran
de validation (QR + « Imprimer »), sur les sous-activités ou après avoir
choisi une autre langue laissait la borne figée : le suivant trouvait le
choix du précédent et pouvait scanner son QR (inscription dans la mauvaise
activité).

Verrouillé ici :

1. le COMPORTEMENT de ``static/js/patient_idle.js``, exécuté sous Node avec
   un faux DOM et de faux minuteurs (retour à l'accueil en français, jamais
   pendant une impression, sur la conclusion, hors ligne ou requête en cours) ;
2. le câblage serveur du réglage ``page_patient_idle_timer`` (registre,
   bornes, valeur par défaut, gabarit, vue, éditeur visuel, frise).
"""

import json
import os
import shutil
import subprocess
import textwrap

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_SCRIPT = os.path.join(_SERVEUR, "static", "js", "patient_idle.js")


def _lire(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# --- 1. Comportement du script (Node) -----------------------------------------

_HARNESS = textwrap.dedent(r"""
    const fs = require('fs');
    const vm = require('vm');
    const assert = require('assert');
    const source = fs.readFileSync(process.argv[2], 'utf8');

    function target() {
        const listeners = {};
        return {
            addEventListener(name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
            dispatch(name, evt) { (listeners[name] || []).forEach(fn => fn(evt || {})); },
        };
    }

    function kiosk(opts) {
        opts = opts || {};
        const doc = target();
        doc.documentElement = { lang: opts.lang || 'fr' };
        const body = target();
        body.getAttribute = name => name === 'data-idle-timer'
            ? (opts.idle === undefined ? '60' : opts.idle) : null;
        doc.body = body;
        const elements = {};
        doc.getElementById = id => elements[id] || null;

        const timers = [];
        let nextId = 1;
        const calls = [];
        const ctx = {
            document: doc,
            setTimeout(fn, ms) { const t = { id: nextId++, fn, ms, live: true }; timers.push(t); return t.id; },
            clearTimeout(id) { timers.forEach(t => { if (t.id === id) { t.live = false; } }); },
            htmx: { ajax(verb, path, options) { calls.push({ verb, path, options }); } },
            printFlowActive: () => false,
            _htmxPending: 0,
        };
        vm.createContext(ctx);
        vm.runInContext(source, ctx);
        doc.dispatch('DOMContentLoaded');

        const k = {
            ctx, doc, elements, calls,
            live() { return timers.filter(t => t.live); },
            // Déclenche le minuteur en cours et renvoie son délai.
            fire() {
                const live = k.live();
                assert.strictEqual(live.length, 1, 'un seul minuteur armé attendu');
                live[0].live = false;
                live[0].fn();
                return live[0].ms;
            },
            // screen : valeur de l'en-tête X-Kiosk-Screen de la réponse.
            swap(screen, id) {
                body.dispatch('htmx:afterSwap', { detail: {
                    target: { id: id || 'div_buttons_parents' },
                    xhr: { getResponseHeader: name =>
                        name === 'X-Kiosk-Screen' ? (screen || null) : null } } });
            },
        };
        return k;
    }

    // Objets créés dans le contexte vm : comparés via leur forme JSON.
    const plain = v => JSON.parse(JSON.stringify(v));
    const HOME_CALL = {
        verb: 'GET', path: '/patient/change_language/fr',
        options: { target: '#main', select: '#main', swap: 'outerHTML' },
    };
    const results = {};
    function scenario(name, fn) {
        try { fn(); results[name] = 'ok'; }
        catch (e) { results[name] = String(e && e.stack || e); }
    }

    scenario('accueil_francais_rien_a_faire', () => {
        const k = kiosk();
        assert.strictEqual(k.fire(), 60000);
        assert.deepStrictEqual(plain(k.calls), []);
    });

    scenario('validation_abandonnee_retour_accueil_francais', () => {
        const k = kiosk();
        k.swap('validation');
        assert.strictEqual(k.fire(), 60000);
        assert.deepStrictEqual(plain(k.calls), [HOME_CALL]);
        assert.strictEqual(k.doc.documentElement.lang, 'fr');
    });

    scenario('reponse_sans_marqueur_compte_hors_accueil', () => {
        const k = kiosk();
        k.swap(null);
        k.fire();
        assert.deepStrictEqual(plain(k.calls), [HOME_CALL]);
    });

    scenario('autre_langue_a_l_accueil_revient_au_francais', () => {
        const k = kiosk({ lang: 'en' });
        k.fire();
        assert.deepStrictEqual(plain(k.calls), [HOME_CALL]);
    });

    scenario('retour_accueil_par_le_patient_annule', () => {
        const k = kiosk();
        k.swap('validation');
        k.swap('home');
        k.fire();
        assert.deepStrictEqual(plain(k.calls), []);
    });

    scenario('contact_relance_le_delai_complet', () => {
        const k = kiosk();
        k.swap('validation');
        k.doc.dispatch('pointerdown');
        k.doc.dispatch('touchstart');
        assert.strictEqual(k.live().length, 1, 'le contact remplace le minuteur');
        assert.strictEqual(k.fire(), 60000);
    });

    scenario('jamais_pendant_une_impression', () => {
        const k = kiosk();
        k.swap('validation');
        k.ctx.printFlowActive = () => true;
        k.fire();
        assert.deepStrictEqual(plain(k.calls), []);
        k.ctx.printFlowActive = () => false;
        assert.strictEqual(k.fire(), 5000, 'nouvel essai rapproché');
        assert.deepStrictEqual(plain(k.calls), [HOME_CALL]);
    });

    scenario('conclusion_garde_son_propre_minuteur', () => {
        const k = kiosk();
        k.swap('validation');
        k.elements.timer_gauge = { id: 'timer_gauge' };
        k.fire();
        assert.deepStrictEqual(plain(k.calls), []);
    });

    scenario('pas_sous_l_ecran_connexion_perdue', () => {
        const k = kiosk();
        k.swap('validation');
        k.elements.kiosk_offline_overlay = { hidden: false };
        k.fire();
        assert.deepStrictEqual(plain(k.calls), []);
        k.elements.kiosk_offline_overlay.hidden = true;
        k.fire();
        assert.deepStrictEqual(plain(k.calls), [HOME_CALL]);
    });

    scenario('pas_pendant_une_requete', () => {
        const k = kiosk();
        k.swap('validation');
        k.ctx._htmxPending = 1;
        k.fire();
        assert.deepStrictEqual(plain(k.calls), []);
    });

    scenario('swap_hors_zone_centrale_ne_change_pas_l_etat', () => {
        // Activité indisponible : message dans la zone du sous-titre, les
        // boutons de l'accueil restent affichés.
        const k = kiosk();
        k.swap('validation', 'div_buttons_children');
        k.fire();
        assert.deepStrictEqual(plain(k.calls), []);
    });

    scenario('delai_absent_ou_nul_desactive', () => {
        const k = kiosk({ idle: '0' });
        k.swap('validation');
        assert.strictEqual(k.live().length, 0);
    });

    process.stdout.write(JSON.stringify(results));
""")

_SCENARIOS = [
    "accueil_francais_rien_a_faire",
    "validation_abandonnee_retour_accueil_francais",
    "reponse_sans_marqueur_compte_hors_accueil",
    "autre_langue_a_l_accueil_revient_au_francais",
    "retour_accueil_par_le_patient_annule",
    "contact_relance_le_delai_complet",
    "jamais_pendant_une_impression",
    "conclusion_garde_son_propre_minuteur",
    "pas_sous_l_ecran_connexion_perdue",
    "pas_pendant_une_requete",
    "swap_hors_zone_centrale_ne_change_pas_l_etat",
    "delai_absent_ou_nul_desactive",
]


@pytest.fixture(scope="module")
def node_results(tmp_path_factory):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js absent : comportement JS non exécutable ici")
    harness = tmp_path_factory.mktemp("idle") / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    proc = subprocess.run([node, str(harness), _SCRIPT],
                          capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_comportement_minuteur(node_results, scenario):
    assert node_results.get(scenario) == "ok", node_results.get(scenario)


def test_tous_les_scenarios_sont_verifies(node_results):
    assert sorted(node_results) == sorted(_SCENARIOS)


# --- 2. Câblage serveur ---------------------------------------------------------

def test_reglage_enregistre_avec_bornes():
    from params_registry import PARAM_REGISTRY
    spec = PARAM_REGISTRY["page_patient_idle_timer"]
    assert spec.config_name == "PAGE_PATIENT_IDLE_TIMER"
    assert spec.value_type == "value_int"
    # Pas de « 0 = jamais » : c'est le garde-fou d'une borne abandonnée.
    assert (spec.min_value, spec.max_value) == (15, 600)
    assert spec.permission == "patient"


def test_valeur_par_defaut_et_version_de_config():
    data = json.loads(_lire("static/json/default_config.json"))
    assert data["configurations"]["page_patient_idle_timer"] == 60
    # Version relevée : sans elle, les bases existantes ne reçoivent pas la clé.
    assert data["version"] != "0.44"
    assert "PAGE_PATIENT_IDLE_TIMER\", 60)" in _lire("app.py")


def test_gabarit_transmet_le_delai_et_charge_le_script():
    source = _lire("templates/patient/patient_front_page.html")
    assert 'data-idle-timer="{{ page_patient_idle_timer' in source
    # Après patients.js : printFlowActive et _htmxPending y sont définis.
    assert source.index("js/patients.js") < source.index("js/patient_idle.js")


def test_vue_patient_transmet_le_reglage(monkeypatch):
    from flask import Flask
    import routes.patient as patient_routes
    from models import db

    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", TESTING=True,
                      SECRET_KEY="test", PAGE_PATIENT_DISPLAY_TRANSLATIONS=True)
    db.init_app(app)
    captured = {}
    monkeypatch.setattr(patient_routes, "render_template",
                        lambda name, **kw: captured.update(kw) or "")
    monkeypatch.setattr(patient_routes, "choose_text_translation", lambda key: "")
    monkeypatch.setattr(patient_routes, "replace_balises", lambda text: text)
    monkeypatch.setattr(patient_routes, "page_editor_published_revision", lambda page: 0)

    with app.app_context():
        db.create_all()
        with app.test_request_context("/patient"):
            patient_routes.patients_front_page()
            assert captured["page_patient_idle_timer"] == 60
            app.config["PAGE_PATIENT_IDLE_TIMER"] = 90
            patient_routes.patients_front_page()
            assert captured["page_patient_idle_timer"] == 90


def test_reglage_dans_l_editeur_visuel_et_la_frise():
    from page_editor import ADAPTERS
    from page_journey import JOURNEYS, step_exit
    fields = {f["key"]: f for f in ADAPTERS["patient"]["components"]["buttons"]["config"]}
    field = fields["page_patient_idle_timer"]
    assert (field["min"], field["max"]) == (15, 600)
    assert set(field["scenarios"]) == {"home", "children", "validation"}

    steps = {s["id"]: s for s in JOURNEYS["patient"]["steps"]}
    config = {"page_patient_idle_timer": 45, "page_patient_display_translations": True}
    for step_id in ("home", "children", "validation"):
        assert "45 s" in step_exit(steps[step_id], config), step_id
