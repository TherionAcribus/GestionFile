"""Repli vers le français de l'annonce vocale quand la traduction manque.

Régression : dans ``generate_audio_calling``, la branche « traduction absente »
écrivait ``language_code == "fr"`` (comparaison sans effet) au lieu de
``language_code = "fr"``. ``get_text_translation`` renvoyant déjà le texte FR de
repli, on synthétisait un texte français avec la voix de la langue étrangère
(modèle/voix gTTS ou Google de cette langue).
"""

import ast
import io
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import pytest  # noqa: E402
from flask import Flask  # noqa: E402

import python.engine as engine  # noqa: E402

_ENGINE_SOURCE = os.path.join(os.path.dirname(__file__), os.pardir, "python", "engine.py")


@pytest.fixture
def flask_app():
    app = Flask(__name__)
    app.config.update(
        ANNOUNCE_SOUND=True,
        ANNOUNCE_CALL_TRANSLATION="all",
        ANNOUNCE_CALL_SOUND="Numéro {N}, comptoir {C}",
    )
    return app


@pytest.fixture
def capture(monkeypatch):
    """Court-circuite la synthèse : on ne garde que (texte, langue) demandés."""
    appels = []
    monkeypatch.setattr(engine, "replace_balise_announces", lambda template, patient: template)
    monkeypatch.setattr(
        engine,
        "choose_voice_model",
        lambda patient, text, language_code: appels.append((text, language_code)),
    )
    return appels


def _patient(voice_is_active=True):
    return SimpleNamespace(
        call_number="A12",
        language=SimpleNamespace(code="ar", voice_is_active=voice_is_active),
    )


def test_traduction_manquante_repasse_la_voix_en_francais(flask_app, capture, monkeypatch):
    monkeypatch.setattr(
        engine,
        "get_text_translation",
        lambda key, code: {"success": False, "translation": "Numéro {N}", "error": "Translation not found"},
    )
    with flask_app.app_context():
        engine.generate_audio_calling(1, _patient(), language_code="ar")

    assert capture == [("Numéro {N}", "fr")]


def test_traduction_presente_garde_la_langue(flask_app, capture, monkeypatch):
    monkeypatch.setattr(
        engine,
        "get_text_translation",
        lambda key, code: {"success": True, "translation": "رقم {N}", "error": None},
    )
    with flask_app.app_context():
        engine.generate_audio_calling(1, _patient(), language_code="ar")

    assert capture == [("رقم {N}", "ar")]


def test_langue_sans_voix_activee_laisse_le_choix_a_choose_voice_model(flask_app, capture):
    """Ici le repli FR est assuré en aval (choose_voice_model reteste
    ``voice_is_active``) : la langue transmise reste donc celle du patient."""
    with flask_app.app_context():
        engine.generate_audio_calling(1, _patient(voice_is_active=False), language_code="ar")

    assert capture == [("Numéro {N}, comptoir {C}", "ar")]


def test_aucune_comparaison_sans_effet_dans_engine():
    arbre = ast.parse(io.open(_ENGINE_SOURCE, encoding="utf-8").read())
    orphelines = [
        f"ligne {n.lineno}: {ast.unparse(n.value)}"
        for n in ast.walk(arbre)
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Compare)
    ]
    assert orphelines == []
