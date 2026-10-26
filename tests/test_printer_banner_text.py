"""Texte du bandeau « imprimante indisponible » de la borne, réglable.

Avant : texte codé en dur dans patients.js (« Votre numéro vous sera indiqué
à l'écran ou remis par l'accueil »). Désormais : réglage traduisible
``page_patient_interface_printer_down`` (Admin « Page Patient » + éditeur
visuel), qui explique par défaut qu'aucun ticket ne sera remis mais que le
numéro sera affiché. Les libellés suivent la langue choisie par le patient.
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
_KEY = "page_patient_interface_printer_down"


def _lire(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def test_reglage_enregistre_et_traduisible():
    from params_registry import PARAM_REGISTRY
    spec = PARAM_REGISTRY[_KEY]
    assert spec.config_name == "PAGE_PATIENT_INTERFACE_PRINTER_DOWN"
    assert spec.value_type == "value_str"
    assert spec.permission == "patient"
    assert spec.translatable
    # Mêmes balises que l'orientation ({P} {D} {H}), pas {N}.
    assert spec.validator == PARAM_REGISTRY["page_patient_interface_orientation"].validator


def test_texte_par_defaut_explique_absence_de_ticket():
    data = json.loads(_lire("static/json/default_config.json"))
    texte = data["configurations"][_KEY]
    assert "aucun ticket" in texte
    assert "numéro" in texte and "affiché" in texte
    assert data["version"] not in ("0.44", "0.45"), "version relevée pour les bases existantes"
    assert f'"PAGE_PATIENT_INTERFACE_PRINTER_DOWN", "{texte}"' in _lire("app.py")


def test_reglage_dans_l_admin_et_l_editeur_visuel():
    assert f'textbloc_before_call("{_KEY}"' in _lire("templates/admin/page_patient_page.html")
    assert f"{_KEY} = app.config.get" in _lire("routes/admin_patient.py")
    from page_editor import ADAPTERS
    fields = {f["key"]: f for f in ADAPTERS["patient"]["components"]["print_error"]["config"]}
    assert fields[_KEY]["type"] == "text"
    assert fields[_KEY]["scenarios"]


def test_vue_patient_transmet_le_texte(monkeypatch):
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
    monkeypatch.setattr(patient_routes, "choose_text_translation",
                        lambda key: f"<{key}>")
    monkeypatch.setattr(patient_routes, "replace_balises", lambda text: text)
    monkeypatch.setattr(patient_routes, "page_editor_published_revision", lambda page: 0)
    with app.app_context():
        db.create_all()
        with app.test_request_context("/patient"):
            patient_routes.patients_front_page()
    assert captured["kiosk_ui_labels"]["printer_down"] == f"<{_KEY}>"


def test_libelles_dans_la_zone_rechargee_au_changement_de_langue():
    source = _lire("templates/patient/patient_front_page.html")
    main = source.index('<div id="main">')
    labels = source.index('id="kiosk_ui_labels"')
    # Après l'ouverture de #main, avant les éléments hors #main (bandeau).
    assert main < labels < source.index('id="printer_state_banner"')


# --- Comportement de kioskOfflineLabels (Node) ---------------------------------

_HARNESS = textwrap.dedent(r"""
    const fs = require('fs');
    const vm = require('vm');
    const source = fs.readFileSync(process.argv[2], 'utf8');
    // Seules les deux fonctions de libellés sont extraites de patients.js.
    function extract(name) {
        const start = source.indexOf('function ' + name + '(');
        // Fin de la fonction : accolade seule en début de ligne (LF ou CRLF).
        const close = /\r?\n}\r?\n/g;
        close.lastIndex = start;
        const match = close.exec(source);
        return source.slice(start, match.index + match[0].length);
    }
    const code = extract('kioskUiLabels') + extract('kioskOfflineLabels');
    function labels(lang, injected) {
        const ctx = {
            console: { error() {} },
            document: {
                documentElement: { lang },
                getElementById: id => id === 'kiosk_ui_labels' && injected !== undefined
                    ? { textContent: JSON.stringify(injected) } : null,
            },
        };
        vm.createContext(ctx);
        vm.runInContext(code + '\nresult = kioskOfflineLabels();', ctx);
        return ctx.result.printer_down;
    }
    process.stdout.write(JSON.stringify({
        configured: labels('fr', { printer_down: 'Texte Admin', orientation: '' }),
        fallback_fr: labels('fr', {}),
        fallback_en: labels('en', undefined),
    }));
""")


@pytest.fixture(scope="module")
def node_labels(tmp_path_factory):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js absent : comportement JS non exécutable ici")
    harness = tmp_path_factory.mktemp("banner") / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    proc = subprocess.run([node, str(harness), os.path.join(_SERVEUR, "static", "js", "patients.js")],
                          capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_texte_admin_prioritaire(node_labels):
    assert node_labels["configured"] == "Texte Admin"


def test_repli_sans_reglage(node_labels):
    assert "aucun ticket" in node_labels["fallback_fr"]
    assert "no ticket" in node_labels["fallback_en"]


def test_bandeau_reecrit_apres_un_echange():
    source = _lire("static/js/patients.js")
    debut = source.index("function setPrinterAvailable(")
    suite = source[debut:debut + 2500]
    assert "document.addEventListener('htmx:afterSwap'" in suite
    assert "setPrinterAvailable(false)" in suite
