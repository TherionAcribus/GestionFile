"""Textes des notifications de l'App Comptoir réglables dans l'administration.

Couvre :
- le catalogue pur ``app_notification_texts`` (repli, balises, validation) ;
- le registre des paramètres (clé, colonne, permission, validateur) et la
  validation passée par ``utils.validate_config_text`` (route update_input) ;
- les valeurs par défaut (default_config.json, version relevée) ;
- les notifications poussées par le serveur (``send_app_notification``) ;
- l'envoi des textes « App » dans l'état du comptoir et la diffusion à chaud ;
- l'onglet d'administration et la recherche globale.
"""

import json
from pathlib import Path

import pytest
from flask import Flask, render_template_string

import app_notification_texts as t
from params_registry import PARAM_REGISTRY

SERVEUR_DIR = Path(__file__).resolve().parents[1]


def _read(rel):
    return (SERVEUR_DIR / rel).read_text(encoding="utf-8")


# --- Catalogue pur ------------------------------------------------------------------

def test_catalogue_bien_forme():
    ids = [item.message_id for item in t.CATALOGUE]
    assert len(ids) == len(set(ids))
    groups = {g for g, _title, _icon in t.GROUPS}
    for item in t.CATALOGUE:
        assert item.key == "app_notif_" + item.message_id
        assert item.group in groups, item
        assert item.rendered_by in (t.APP, t.SERVER, t.BOTH)
        assert item.label and item.when and item.default
        assert set(item.markers) <= set(t.MARKER_LABELS)
        # Le texte d'origine passe sa propre validation.
        assert t.validate(item.key, item.default) == {"success": True, "value": item.default}


def test_repli_sur_le_texte_d_origine():
    assert t.text_for({}, "no_paper") == "Il n'y a plus de papier dans la borne !"
    assert t.text_for({"APP_NOTIF_NO_PAPER": "   "}, "no_paper") == "Il n'y a plus de papier dans la borne !"
    assert t.text_for({"APP_NOTIF_NO_PAPER": None}, "no_paper") == "Il n'y a plus de papier dans la borne !"
    assert t.text_for({"APP_NOTIF_NO_PAPER": "Rouleau vide"}, "no_paper") == "Rouleau vide"


def test_rendu_des_balises():
    assert t.render("{N} pour {A} {Z}", N="O-3", A="Conseil") == "O-3 pour Conseil {Z}"
    assert t.render("{n}", N=7) == "7"
    assert t.render_message({}, "kiosk_offline", B=2) == "Borne 2 injoignable : aucun contact récent."


@pytest.mark.parametrize("value,expected", [
    ("Validez {n} vite", "Validez {N} vite"),
    ("  Validez  ", "Validez"),
    ("", ""),  # vide = texte d'origine
])
def test_validation_accepte(value, expected):
    assert t.validate("app_notif_please_validate", value) == {"success": True, "value": expected}


@pytest.mark.parametrize("key,value", [
    ("app_notif_please_validate", "Comptoir {C}"),      # balise non prévue
    ("app_notif_server_unreachable", "Serveur {N}"),    # aucune balise permise
    ("app_notif_please_validate", "Patient {NN}"),
    ("app_notif_please_validate", "Patient {}"),
    ("app_notif_please_validate", "x" * (t.MAX_LENGTH + 1)),
])
def test_validation_refuse(key, value):
    result = t.validate(key, value)
    assert result["success"] is False
    assert result["value"]


def test_textes_envoyes_a_l_app():
    texts = t.client_texts({"APP_NOTIF_ACTION_BUSY": "Patience…"})
    assert texts["action_busy"] == "Patience…"
    assert texts["realtime_lost"].startswith("La connexion temps réel a été perdue")
    # Les textes fabriqués par le serveur ne partent pas à l'App ; le papier
    # est fabriqué des deux côtés.
    assert "kiosk_offline" not in texts and "activity" not in texts
    assert {"low_paper", "paper_ok"} <= set(texts)


# --- Registre et validation de la route -----------------------------------------------

def test_registre():
    for item in t.CATALOGUE:
        spec = PARAM_REGISTRY[item.key]
        assert spec.config_name == item.config_name
        # value_text : le message « connexion perdue » dépasse 200 caractères.
        assert spec.value_type == "value_text"
        assert spec.permission == "app"
        assert spec.validator == "app_notification"
        assert not spec.secret and not spec.restart_required


def test_validation_par_la_route_generique():
    from utils import validate_config_text
    assert validate_config_text("app_notif_new_patient", "{n} → {a}") == \
        {"success": True, "value": "{N} → {A}"}
    assert validate_config_text("app_notif_new_patient", "{M}")["success"] is False


def test_valeurs_par_defaut_et_version():
    data = json.loads(_read("static/json/default_config.json"))
    for item in t.CATALOGUE:
        assert data["configurations"][item.key] == item.default
    # Version relevée : sans elle, les bases existantes ne reçoivent pas les clés.
    assert data["version"] not in ("0.44", "0.45", "0.46")


# --- Notifications poussées par le serveur ---------------------------------------------

@pytest.fixture()
def sent(monkeypatch):
    import communication
    app = Flask(__name__)
    captured = []
    monkeypatch.setattr(communication, "communikation",
                        lambda stream, **kw: captured.append(kw["data"]))
    with app.app_context():
        yield app, captured


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_notifications_serveur_texte_regle(sent):
    from communication import send_app_notification
    app, captured = sent
    app.config["APP_NOTIF_KIOSK_OFFLINE"] = "La borne {B} ne répond plus"
    app.config["APP_NOTIF_ACTIVITY"] = "{N} attend pour {A}"
    send_app_notification("kiosk_offline", {"borne_id": 3})
    send_app_notification("activity", {"activity": _Obj(name="Vaccin"),
                                       "patient": _Obj(call_number="V-2")})
    send_app_notification("printer_error", {"message": "bourrage"})
    assert [c["message"] for c in captured] == [
        "La borne 3 ne répond plus", "V-2 attend pour Vaccin", "Erreur d'impression : bourrage"]


def test_notifications_serveur_destinataires_conserves(sent):
    from communication import send_app_notification
    app, captured = sent
    app.config["APP_NOTIF_PATIENT_REDIRECTED"] = "{N} pour vous"
    send_app_notification("patient_for_staff_from_app",
                          {"patient": _Obj(call_number="A-1"), "counters": [2, 4]})
    send_app_notification("patient_taken", {"patient": _Obj(call_number="A-9"), "counter_id": 5})
    assert captured[0]["message"] == "A-1 pour vous" and captured[0]["for_counter"] == [2, 4]
    assert captured[1]["message"] == "Le patient A-9 vient d'être appelé par un autre comptoir."
    assert captured[1]["for_counter"] == 5


def test_bouton_papier_desactive_annonce_papier_remis(sent):
    """Désactiver le bouton papier envoyait « bout du rouleau »."""
    from communication import send_app_notification
    _app, captured = sent
    send_app_notification("low_paper", {"add_paper": 0})
    send_app_notification("low_paper", {"add_paper": 1})
    assert [(c["origin"], c["message"]) for c in captured] == [
        ("paper_ok", "Une gentille personne a remis du papier"),
        ("low_paper", "On est quasiment au bout du rouleau")]


# --- État du comptoir et diffusion à chaud -----------------------------------------------

def test_etat_du_comptoir_transmet_les_textes():
    source = _read("routes/counter.py")
    assert '"notification_texts": app_notification_texts.client_texts(app.config)' in source


def test_modification_relance_les_app(monkeypatch):
    import routes.admin_config as admin_config
    emitted = []
    monkeypatch.setattr(admin_config, "communikation",
                        lambda stream, **kw: emitted.append((stream, kw)))
    app = Flask(__name__)
    with app.app_context():
        admin_config.special_functions_with_input("app_notif_please_validate")
    assert emitted == [("app_counter", {"event": "counter_state_changed",
                                        "data": {"reason": "notification_texts_changed"}})]


# --- Onglet d'administration ----------------------------------------------------------------

def _render_tab(config):
    app = Flask(__name__, root_path=str(SERVEUR_DIR), template_folder="templates")
    with app.test_request_context("/admin/app"):
        return render_template_string(
            "{% import '/admin/macros.html' as macros %}"
            "{% include 'admin/app_notifications.html' %}",
            app_notification_groups=t.admin_groups(config))


def test_onglet_rend_chaque_message():
    html = _render_tab({"APP_NOTIF_PLEASE_VALIDATE": "Validez {N} !"})
    for item in t.CATALOGUE:
        assert f'id="{item.key}"' in html, item.key
        assert f'id="{item.key}_button"' in html, item.key
        assert f'data-notif-reset="{item.key}"' in html
    # Valeur réglée affichée, texte d'origine rappelé.
    assert "Validez {N} !</textarea>" in html
    assert "Texte d'origine : « Pensez à valider" in html
    # Balises proposées : seulement celles du message.
    validate = html.split('id="app_notif_please_validate"', 1)[1].split('id="app_notif_auto_release_warning"', 1)[0]
    assert 'data-placeholder-text="{N}"' in validate
    assert 'data-placeholder-text="{S}"' not in validate


def test_onglet_dans_la_page_application():
    page = _read("templates/admin/app.html")
    assert "('notifications', 'tab_notifications', 'Notifications du comptoir', 'bi-bell')" in page
    assert "{% include 'admin/app_notifications.html' %}" in page
    assert "js/admin_app_notifications.js" in page
    assert "'notifications'" in _read("routes/admin_app.py")
    # Aucun JavaScript inline dans le gabarit (CSP).
    assert "<script" not in _read("templates/admin/app_notifications.html")


def test_recherche_globale():
    import admin_search
    entries = admin_search.notification_text_settings()
    assert {e["url"].rsplit("=", 1)[1] for e in entries} == set(t.KEYS)
    assert all(e["url"].startswith("/admin/app?tab=notifications#focus=") for e in entries)
    titles = {e["title"] for e in admin_search.CATALOG}
    assert "Notifications du comptoir" in titles
