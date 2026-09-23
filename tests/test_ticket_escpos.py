"""Ticket ESC/POS : la largeur d'impression est appliquée À L'IMPRESSION.

Avant : à l'enregistrement d'un texte de ticket (``/admin/update_input``), le
Markdown était converti immédiatement en ESC/POS avec la largeur implicite de
42 caractères et stocké dans une clé dérivée ``ticket_*_printer``. Ce texte
préformaté était réutilisé à l'impression : après passage de ``printer_width``
à 48, les retours à la ligne restaient cuits à 42 (21 en double taille).

Désormais : seule la source Markdown est stockée ; ``format_ticket_text``
convertit en ESC/POS au moment de l'impression avec ``PRINTER_WIDTH`` courant.
Les clés ``ticket_*_printer`` sont retirées du registre (résidus en base
ignorés).

Verrouillé ici :

1. ``format_ticket_text`` lit le Markdown (``TICKET_*``), pas un éventuel
   résidu préformaté ``TICKET_*_PRINTER`` ;
2. l'enveloppe épouse la largeur courante — y compris en ``[double]``
   (demi-largeur) ;
3. ``update_input`` ne génère plus de version ``*_printer`` et le registre ne
   connaît plus ces clés.
"""

import base64
import os
import re

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask, session

import params_registry as reg
from utils import format_ticket_text

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def application():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="secret-test-ticket-escpos",
        TESTING=True,
        TICKET_HEADER="",
        TICKET_MESSAGE="",
        TICKET_FOOTER="",
        TICKET_DISPLAY_SPECIFIC_MESSAGE=False,
        PRINTER_WIDTH=48,
        PHARMACY_NAME="Pharmacie de test",
    )
    return app


def _ticket(application, **config):
    """Rend le ticket (chemin 'fr') et renvoie le texte ESC/POS décodé."""
    application.config.update(config)
    with application.test_request_context():
        session["language_code"] = "fr"
        encoded = format_ticket_text(None, None)
    return base64.b64decode(encoded).decode("utf-8")


# ---------------------------------------------------------------------------
# 1. La source lue est le Markdown, pas un résidu préformaté
# ---------------------------------------------------------------------------

def test_format_ticket_text_lit_le_markdown(application):
    """Un éventuel résidu TICKET_MESSAGE_PRINTER en mémoire (vieille ligne en
    base rechargée ailleurs) ne doit plus être la source du ticket."""
    application.config["TICKET_MESSAGE_PRINTER"] = "STALE\x1b\x61\x01"
    texte = _ticket(application, TICKET_MESSAGE="Bonjour")

    assert "Bonjour" in texte
    assert "STALE" not in texte


# ---------------------------------------------------------------------------
# 2. L'enveloppe épouse la largeur courante
# ---------------------------------------------------------------------------

def test_texte_simple_enveloppe_a_la_largeur_courante(application):
    """44 caractères tiennent sur UNE ligne à 48 ; à l'ancienne largeur figée
    de 42, la coupure tombait avant la fin."""
    ligne = "AAAA BBBB CCCC DDDD EEEE FFFF GGGG HHHH IIII"  # 44 caractères
    assert len(ligne) == 44

    texte = _ticket(application, TICKET_MESSAGE=ligne)

    assert ligne in texte.splitlines()


def test_double_taille_enveloppe_a_la_demi_largeur_courante(application):
    """En ``[double]``, l'enveloppe se fait à PRINTER_WIDTH/2 : 24 caractères
    tiennent sur une ligne à 48 ; cuits à 42, ils étaient coupés à 21."""
    ligne = "AAAA BBBB CCCC DDDD EEEE"  # 24 caractères
    assert len(ligne) == 24

    texte = _ticket(application, TICKET_MESSAGE=f"[double]{ligne}[/double]")

    corps = texte.split("\x1d\x21\x11")[1].split("\x1d\x21\x00")[0]
    assert corps == ligne


def test_changement_de_largeur_sans_reenregistrer(application):
    """Le même Markdown enregistré produit des enveloppes différentes selon
    PRINTER_WIDTH au moment de l'impression — c'est tout l'objet du correctif."""
    ligne = "AAAA BBBB CCCC DDDD EEEE FFFF GGGG HHHH IIII"  # 44 caractères

    a_48 = _ticket(application, TICKET_MESSAGE=ligne, PRINTER_WIDTH=48)
    a_32 = _ticket(application, TICKET_MESSAGE=ligne, PRINTER_WIDTH=32)

    assert ligne in a_48.splitlines()
    assert ligne not in a_32.splitlines()


# ---------------------------------------------------------------------------
# 3. Plus d'écriture ni de clé « *_printer »
# ---------------------------------------------------------------------------

def test_cles_ticket_printer_retirees_du_registre():
    """Les clés dérivées préformatées ne sont plus chargées ni modifiables."""
    assert not any(k.endswith("_printer") for k in reg.PARAM_REGISTRY)
    assert reg.get_spec("ticket_message_printer") is None


def test_update_input_ne_genere_plus_de_version_preformatee():
    """Régression statique : la route ne calcule ni n'écrit plus de compagnon
    ``key + "_printer"`` ESC/POS à l'enregistrement."""
    source = _read("routes/admin_config.py")
    m = re.search(r"def update_input\(.*?\n(.*?)(?=\ndef |\n@admin_config_bp)",
                  source, re.DOTALL)
    assert m, "fonction update_input introuvable"
    body = m.group(1)
    assert "convert_markdown_to_escpos" not in body
    assert "key_printer" not in body
    assert '"_printer")' not in body
