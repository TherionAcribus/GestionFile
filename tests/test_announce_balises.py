"""Balise {P} des annonces vocales — nom de la pharmacie.

Régression : ``replace_balise_announces`` lisait le nom via
``ConfigOption.query.get("pharmacy_name")``. La clé primaire de ``ConfigOption``
est l'``id`` entier (``config_key`` n'est qu'une colonne unique), donc la
requête renvoyait toujours ``None`` et ``{P}`` était vide dans les annonces.
Le nom est désormais lu dans ``app.config["PHARMACY_NAME"]``, comme le font déjà
``replace_balise_phone`` et ``replace_balise_welcome`` (le chargeur de config
recopie l'option ``pharmacy_name`` dans cette clé).
"""

import os
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
