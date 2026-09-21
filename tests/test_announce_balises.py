"""Rendu des balises des annonces et textes configurables.

Régression initiale : ``replace_balise_announces`` lisait le nom de la
pharmacie via ``ConfigOption.query.get("pharmacy_name")``. La clé primaire de
``ConfigOption`` est l'``id`` entier (``config_key`` n'est qu'une colonne
unique), donc la requête renvoyait toujours ``None`` et ``{P}`` était vide
dans les annonces. Le nom est lu dans ``app.config["PHARMACY_NAME"]``, comme
le font déjà ``replace_balise_phone`` et ``replace_balise_welcome`` (le
chargeur de config recopie l'option ``pharmacy_name`` dans cette clé).

Régression « balises configurables » : l'administration autorise {P} {D} {H}
{A} {N} {M} {C} (``params_registry.BALISE_LETTERS``) mais le rendu ne fournissait
que {P} {N} {M} {C} via ``str.format`` — une balise {D}, {H} ou {A} levait un
``KeyError`` APRÈS que le patient était marqué 'calling' : bannière absente,
annonce audio perdue. Le moteur centralisé (``render_balises`` /
``replace_balises``) prend en charge toutes les balises annoncées et ne lève
jamais d'exception. Ce fichier teste chaque balise.
"""

import os
import re
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from flask import Flask

import utils


@pytest.fixture
def flask_app():
    app = Flask(__name__)
    app.config["PHARMACY_NAME"] = "Pharmacie du Centre"
    return app


def _patient(with_staff=True):
    staff = SimpleNamespace(name="Alice") if with_staff else None
    return SimpleNamespace(
        call_number="A12",
        activity_id=1,
        activity=SimpleNamespace(name="Ordonnance"),
        counter=SimpleNamespace(name="1", staff=staff),
    )


def test_balise_p_utilise_le_nom_de_la_pharmacie(flask_app):
    with flask_app.app_context():
        rendu = utils.replace_balise_announces("{P} : comptoir {C}, {N} avec {M}", _patient())
    assert rendu == "Pharmacie du Centre : comptoir 1, A12 avec Alice"


def test_balise_p_vide_si_nom_non_configure(flask_app):
    flask_app.config.pop("PHARMACY_NAME")
    with flask_app.app_context():
        rendu = utils.replace_balise_announces("{P}{N}", _patient())
    assert rendu == "A12"


def test_sans_staff_le_repli_reste_rendu(flask_app, monkeypatch):
    envoyees = []
    monkeypatch.setattr(utils, "send_app_notification", lambda **kw: envoyees.append(kw))
    with flask_app.app_context():
        rendu = utils.replace_balise_announces("{P} : {N}", _patient(with_staff=False))
    assert rendu == "Comptoir 1: A12"
    assert envoyees and envoyees[0]["origin"] == "erreur"


def test_source_ne_requete_plus_configoption_par_cle():
    source = open(os.path.join(os.path.dirname(__file__), os.pardir, "utils.py"), encoding="utf-8").read()
    assert 'ConfigOption.query.get(' not in source


# --- Un test par balise annoncée --------------------------------------------

def test_balise_n_numero_d_appel(flask_app):
    with flask_app.app_context():
        rendu = utils.replace_balise_announces("Numéro {N}", _patient())
    assert rendu == "Numéro A12"


def test_balise_c_nom_du_comptoir(flask_app):
    with flask_app.app_context():
        rendu = utils.replace_balise_announces("Comptoir {C}", _patient())
    assert rendu == "Comptoir 1"


def test_balise_m_membre_equipe(flask_app):
    with flask_app.app_context():
        rendu = utils.replace_balise_announces("avec {M}", _patient())
    assert rendu == "avec Alice"


def test_balise_a_activite(flask_app):
    """{A} : sans bouton associé en base, repli sur le nom de l'activité."""
    with flask_app.app_context():
        rendu = utils.replace_balise_announces("Activité {A}", _patient())
    assert rendu == "Activité Ordonnance"


def test_balise_d_date_du_jour(flask_app):
    with flask_app.app_context():
        rendu = utils.replace_balise_announces("le {D}", _patient())
    assert rendu == "le " + date.today().strftime("%d/%m/%y")


def test_balise_h_heure(flask_app):
    with flask_app.app_context():
        rendu = utils.replace_balise_announces("à {H}", _patient())
    assert re.fullmatch(r"à \d{2}:\d{2}", rendu)


def test_toutes_les_balises_ensemble_ne_levent_pas_d_exception(flask_app):
    """Régression : {D}, {H} et {A} étaient autorisées à la saisie
    (after_call = PDHANMC) mais absentes du ``str.format`` — KeyError après le
    passage du patient en 'calling'."""
    with flask_app.app_context():
        rendu = utils.replace_balise_announces("{P} {D} {H} {A} {N} {M} {C}", _patient())
    for balise in ("{P}", "{D}", "{H}", "{A}", "{N}", "{M}", "{C}"):
        assert balise not in rendu
    assert "A12" in rendu and "Alice" in rendu and "Pharmacie du Centre" in rendu


# --- Robustesse du moteur : jamais d'exception -------------------------------

def test_balise_inconnue_laissee_telle_quelle(flask_app):
    with flask_app.app_context():
        rendu = utils.replace_balise_announces("Ticket {N} {Z}", _patient())
    assert rendu == "Ticket A12 {Z}"


def test_accolades_malformees_ne_plantent_pas(flask_app):
    """str.format levait KeyError/ValueError/IndexError sur ces gabarits ; le
    moteur les rend sans exception."""
    with flask_app.app_context():
        for template in ("{", "}", "{0}", "{{N}}", "Patient {N", "{N}{", ""):
            rendu = utils.replace_balise_announces(template, _patient())
            assert isinstance(rendu, str)


def test_patient_sans_comptoir_degrade_sans_exception(flask_app, monkeypatch):
    """Un patient sans comptoir ni membre dégrade en « Comptoir {C}: {N} »
    au lieu de lever AttributeError (ancien « Erreur »)."""
    monkeypatch.setattr(utils, "send_app_notification", lambda **kw: None)
    patient = SimpleNamespace(call_number="B7", activity_id=1,
                              activity=SimpleNamespace(name="Test"),
                              counter=None)
    with flask_app.app_context():
        rendu = utils.replace_balise_announces("{N} au comptoir {C} avec {M}", patient)
    assert rendu == "Comptoir : B7"


# --- replace_balise_phone : même moteur --------------------------------------

def test_replace_balise_phone_toutes_les_balises(flask_app):
    with flask_app.app_context():
        rendu = utils.replace_balise_phone("{P} {N} {A}", _patient())
    assert rendu == "Pharmacie du Centre A12 Ordonnance"


def test_replace_balise_phone_ne_plante_pas_sur_accolades(flask_app):
    with flask_app.app_context():
        rendu = utils.replace_balise_phone("{N} {0} {Z}", _patient())
    assert rendu == "A12 {0} {Z}"


# --- replace_balise_welcome : textes hors contexte patient -------------------

def test_replace_balise_welcome_resout_p_d_h(flask_app):
    with flask_app.app_context():
        rendu = utils.replace_balise_welcome("Bienvenue à la {P} — {D} {H}")
    attendu = ("Bienvenue à la Pharmacie du Centre — "
               + date.today().strftime("%d/%m/%y") + " ")
    assert rendu.startswith(attendu)
    assert re.search(r"\d{2}:\d{2}$", rendu)


def test_replace_balise_welcome_vide_les_balises_patient(flask_app):
    with flask_app.app_context():
        rendu = utils.replace_balise_welcome("File {N} {C} {M}")
    assert "{" not in rendu


# --- Route /display : le titre doit être rendu, pas affiché brut -------------

def test_display_remplace_la_balise_p_du_titre(monkeypatch):
    """Régression : le titre par défaut « Bienvenue à la {P} » était injecté
    tel quel dans la page — {P} restait affiché littéralement."""
    from routes import announce as announce_module

    app = Flask(__name__)
    app.config.update(
        ANNOUNCE_INFOS_DISPLAY=False,
        ANNOUNCE_TITLE="Bienvenue à la {P}",
        ANNOUNCE_SUBTITLE="",
        ANNOUNCE_TEXT_UP_PATIENTS="",
        ANNOUNCE_TEXT_UP_PATIENTS_DISPLAY="never",
        ANNOUNCE_TEXT_UP_PATIENTS_SIZE=20,
        ANNOUNCE_TEXT_DOWN_PATIENTS="",
        ANNOUNCE_TEXT_DOWN_PATIENTS_DISPLAY="never",
        ANNOUNCE_TEXT_DOWN_PATIENTS_SIZE=20,
        ANNOUNCE_ONGOING_DISPLAY=False,
        ANNOUNCE_TITLE_SIZE=50,
        ANNOUNCE_CALL_TEXT_SIZE=80,
        PHARMACY_NAME="Pharmacie du Centre",
    )
    captures = {}
    monkeypatch.setattr(announce_module, "render_template",
                        lambda tpl, **kw: captures.update(kw) or tpl)
    monkeypatch.setattr(announce_module, "patient_list_for_init_display", lambda: [])

    with app.app_context():
        announce_module.display()

    assert captures["announce_title"] == "Bienvenue à la Pharmacie du Centre"


# --- Macro admin : le bouton « membre de l'équipe » doit insérer {M} ---------

def test_macro_after_call_insere_m_pour_le_membre():
    """Régression : le bouton affiché {M} insérait {A} dans le champ."""
    source = open(os.path.join(os.path.dirname(__file__), os.pardir,
                               "templates", "admin", "macros.html"),
                  encoding="utf-8").read()
    bloc = source.split("balises_after_call", 1)[1].split("endmacro", 1)[0]
    assert 'data-placeholder-text="{M}">{M}<' in bloc
    assert 'data-placeholder-text="{A}">{M}<' not in bloc
