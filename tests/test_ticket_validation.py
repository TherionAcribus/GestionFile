"""Validation des textes de ticket : balises {X} et balisage d'impression.

Avant : ``ticket_header`` / ``ticket_message`` / ``ticket_footer`` n'avaient
que le validateur générique ``text`` alors que l'interface propose {P} {N}
{A} {D} {H} et le balisage imprimante ([center] [double] [separator] ** __).
Une balise inconnue (``{X}``) ou une mise en forme non fermée (``[double]``
sans ``[/double]``, ``**`` impair) était enregistrée puis imprimée
littéralement. La sauvegarde des traductions n'appliquait aucune validation.

Désormais : validateur ``ticket`` dans le registre (famille « before_call » :
PDHAN), ``utils.validate_ticket_text`` contrôle balises ET équilibre du
balisage, ``utils.validate_config_text`` dispatche selon le registre, et
``save_translations`` applique la même règle aux textes ConfigOption traduits.
"""

import os
import re

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

import params_registry as reg
from utils import validate_config_text, validate_ticket_text

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


@pytest.fixture(autouse=True)
def app_ctx():
    """validate_and_transform_text journalise via app.logger : contexte requis."""
    app = Flask(__name__)
    with app.app_context():
        yield


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


_TICKET_KEYS = ("ticket_header", "ticket_message", "ticket_footer")


# ---------------------------------------------------------------------------
# 1. Registre : validateur dédié « ticket »
# ---------------------------------------------------------------------------

def test_ticket_keys_use_ticket_validator():
    for key in _TICKET_KEYS:
        spec = reg.get_spec(key)
        assert spec.validator == "ticket", key
        # Les lettres proposées par l'interface ({P} {N} {A} {D} {H}).
        assert reg.BALISE_LETTERS[spec.validator] == "PDHAN"


# ---------------------------------------------------------------------------
# 2. validate_ticket_text : balises et balisage équilibré
# ---------------------------------------------------------------------------

def test_texte_valide_accepte():
    assert validate_ticket_text("Bienvenue à la {P}")["success"]
    assert validate_ticket_text(
        "[center][double]**N° {N}**[/double][/center]\n[separator]")["success"]
    assert validate_ticket_text("__Merci__ de patienter {A}")["success"]


def test_balise_minuscule_corrigee_en_majuscule():
    check = validate_ticket_text("Numéro {n}")
    assert check["success"] and check["value"] == "Numéro {N}"


def test_balise_inconnue_rejetee():
    check = validate_ticket_text("Votre tour : {X}")
    assert not check["success"]


def test_balise_non_permise_rejetee():
    """{M} (membre) et {C} (comptoir) n'existent pas au moment de l'impression
    du ticket — l'interface ne les propose pas."""
    for balise in ("{M}", "{C}"):
        assert not validate_ticket_text(f"Ticket {balise}")["success"]


def test_balisage_double_non_ferme_rejete():
    assert not validate_ticket_text("[double]Titre sans fin")["success"]
    assert not validate_ticket_text("Titre[/double]")["success"]


def test_balisage_center_non_ferme_rejete():
    assert not validate_ticket_text("[center]Texte")["success"]


def test_marqueurs_impairs_rejetés():
    assert not validate_ticket_text("**gras non fermé")["success"]
    assert not validate_ticket_text("__souligné non fermé")["success"]


def test_separator_n_exige_pas_de_fermant():
    """[separator] est autonome : il ne doit pas être compté comme non fermé."""
    assert validate_ticket_text("A\n[separator]\nB")["success"]


# ---------------------------------------------------------------------------
# 3. validate_config_text : dispatch selon le registre
# ---------------------------------------------------------------------------

def test_config_text_dispatche_le_validateur_ticket():
    check = validate_config_text("ticket_message", "{X} inconnu")
    assert not check["success"]


def test_config_text_applique_les_familles_de_balises():
    """Une clé « welcome » refuse {N} (pas de patient à l'accueil)."""
    assert not validate_config_text("announce_title", "Bienvenue {N}")["success"]
    assert validate_config_text("announce_title", "Bienvenue {P}")["success"]


def test_config_text_cles_sans_regle_inchangees():
    """« text » / clé inconnue : valeur inchangée, toujours valide."""
    assert validate_config_text("pharmacy_name", "Officine {N}")["value"] == "Officine {N}"
    assert validate_config_text("cle_inconnue", "n'importe quoi")["success"]


def test_config_text_validate_hour_dispatch():
    """Point f : les clés ``*_hour`` passent par ``validate_hour`` — un format
    invalide ou hors plage est refusé AVANT persistance, sinon la création de
    la tâche échouait silencieusement après « Option mise à jour »."""
    key = "cron_delete_patient_table_hour"
    for bad in ("25:99", "abc", "24:00", "12:60", "9h30", "", "12:345"):
        assert not validate_config_text(key, bad)["success"], (
            f"{bad!r} aurait dû être refusé")
    for good in ("00:00", "09:05", "23:59"):
        assert validate_config_text(key, good)["success"], (
            f"{good!r} aurait dû être accepté")
    # Normalisation vers « HH:MM » (alimente <input type="time"> et le
    # découpage heure/minute du job cron).
    assert validate_config_text(key, "9:5")["value"] == "09:05"
    # Même contrat sur la seconde clé horaire.
    assert not validate_config_text("cron_delete_announce_calls_hour", "25:99")["success"]


# ---------------------------------------------------------------------------
# 4. Régressions statiques : la validation est branchée sur les deux routes
# ---------------------------------------------------------------------------

def test_update_input_valide_via_le_registre():
    source = _read("routes/admin_config.py")
    m = re.search(r"def update_input\(.*?\n(.*?)(?=\ndef |\n@admin_config_bp)",
                  source, re.DOTALL)
    assert m, "fonction update_input introuvable"
    assert "validate_config_text" in m.group(1)


def test_save_translations_valide_les_textes_configoption():
    """La sauvegarde des traductions applique la même validation que le champ
    source (ConfigOption uniquement — les traductions de boutons/activités
    n'ont pas de balises)."""
    source = _read("routes/admin_translation.py")
    m = re.search(r"def save_translations\(.*?\n(.*?)(?=\ndef |\n@admin_translation_bp|\Z)",
                  source, re.DOTALL)
    assert m, "fonction save_translations introuvable"
    body = m.group(1)
    assert "validate_config_text" in body
    assert 'table_name == "ConfigOption"' in body
