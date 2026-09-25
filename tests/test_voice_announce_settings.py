"""Onglet « Son et voix » de la page Annonce (fusion de l'ancien onglet Google Voice).

Couvre :
- le noyau pur ``tts_voice_defaults`` (voix Google conseillée par langue) ;
- le repli gTTS du moteur (Google en échec, modèle jamais réglé) ;
- les routes du tableau récapitulatif, du passage « tout sur Google », de la
  présélection de voix par langue et du panneau de langue.

Nom de fichier volontairement trié après test_upload_security : ce fichier
enregistre le bind 'users' dans db.metadatas (partagé entre fichiers), ce que
ne tolèrent pas les fichiers antérieurs qui ne déclarent pas SQLALCHEMY_BINDS.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import python.engine as engine
import routes.admin_announce as admin_announce
from models import Language, Role, User, db
from tts_voice_defaults import pick_default_google_voice, voice_type

SERVEUR_DIR = Path(__file__).resolve().parents[1]


def _voice(name, *codes, gender="FEMALE"):
    return {"name": name, "language_codes": list(codes) or [name[:5]],
            "ssml_gender": gender, "natural_sample_rate_hertz": 24000}


VOICES = [
    _voice("fr-CA-Neural2-A", "fr-CA"),
    _voice("fr-FR-Standard-A", "fr-FR"),
    _voice("fr-FR-Wavenet-B", "fr-FR"),
    _voice("fr-FR-Neural2-B", "fr-FR"),
    _voice("fr-FR-Neural2-A", "fr-FR"),
    _voice("fr-FR-Studio-A", "fr-FR"),
    _voice("en-US-Neural2-C", "en-US"),
    _voice("en-GB-Neural2-A", "en-GB"),
    _voice("cmn-CN-Wavenet-A", "cmn-CN"),
    _voice("de-DE-Standard-A", "de-DE"),
]


# --- Noyau pur ----------------------------------------------------------------

def test_voice_type_reconnait_les_familles():
    assert voice_type("fr-FR-Neural2-A") == "Neural2"
    assert voice_type("fr-FR-Wavenet-B") == "Wavenet"
    assert voice_type("de-DE-Standard-A") == "Standard"
    assert voice_type("fr-FR-Studio-A") is None
    assert voice_type("") is None


def test_pick_prefere_neural2_puis_region_canonique_puis_nom():
    # Neural2 > Wavenet > Standard ; fr-FR (canonique) avant fr-CA ; « -A » avant « -B ».
    assert pick_default_google_voice(VOICES, "fr") == ("fr-FR-Neural2-A", "fr-FR")


def test_pick_region_canonique_explicite_et_alias():
    assert pick_default_google_voice(VOICES, "en") == ("en-GB-Neural2-A", "en-GB")
    # zh → voix Google « cmn-CN » (préfixe différent du code ISO).
    assert pick_default_google_voice(VOICES, "zh") == ("cmn-CN-Wavenet-A", "cmn-CN")
    assert pick_default_google_voice(VOICES, "de") == ("de-DE-Standard-A", "de-DE")


def test_pick_exclut_familles_premium_et_langues_inconnues():
    assert pick_default_google_voice([_voice("fr-FR-Studio-A", "fr-FR")], "fr") is None
    assert pick_default_google_voice(VOICES, "xx") is None
    assert pick_default_google_voice(VOICES, "") is None
    assert pick_default_google_voice([], "fr") is None


# --- Moteur : repli gTTS -------------------------------------------------------

@pytest.fixture
def engine_app():
    app = Flask(__name__)
    app.config.update(VOICE_MODEL="google", ANNOUNCE_CALL_TRANSLATION="vo",
                      VOICE_GOOGLE_NAME="", VOICE_GOOGLE_REGION="",
                      VOICE_GTTS_NAME="fr")
    return app


def _patient(**lang):
    defaults = dict(code="de", voice_is_active=True, voice_model="google",
                    voice_gtts_name=None, voice_google_name="de-DE-Neural2-A",
                    voice_google_region="de-DE")
    defaults.update(lang)
    return SimpleNamespace(language=SimpleNamespace(**defaults))


def test_google_en_echec_repli_sur_gtts(engine_app, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("PermissionDenied")
    monkeypatch.setattr(engine, "create_google_tts_sound", boom)
    monkeypatch.setattr(engine, "create_tts_sound", lambda p, t, c: f"gtts:{c}")
    with engine_app.app_context():
        assert engine.choose_voice_model(_patient(), "Bonjour", "de") == "gtts:de"


def test_modele_jamais_regle_utilise_gtts(engine_app, monkeypatch):
    # voice_model NULL (langue ajoutée après coup) : auparavant aucune annonce.
    monkeypatch.setattr(engine, "create_tts_sound", lambda p, t, c: "gtts")
    with engine_app.app_context():
        assert engine.choose_voice_model(_patient(voice_model=None), "x", "de") == "gtts"


def test_google_sans_voix_choisie_leve_sans_appel_reseau(engine_app):
    with engine_app.app_context():
        with pytest.raises(RuntimeError):
            engine.create_google_tts_sound(_patient(voice_google_name=None), "x", "de")


def test_gtts_sans_voix_choisie_utilise_le_code_langue(engine_app, monkeypatch):
    captured = {}
    monkeypatch.setattr(engine, "cached_announcement_url",
                        lambda **kw: captured.update(kw) or "url")
    with engine_app.app_context():
        engine.create_tts_sound(_patient(voice_gtts_name=None), "x", "de")
    assert captured["voice"] == "de"


# --- Routes ---------------------------------------------------------------------

@pytest.fixture()
def app(tmp_path):
    # root_path = Serveur : gabarits et dossier static réels (url_for des drapeaux).
    app = Flask(__name__, root_path=str(SERVEUR_DIR))
    app.config.update(
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
        BASE32_KEY=Fernet.generate_key(),
        VOICE_GOOGLE_NAME="",
        VOICE_GOOGLE_REGION="",
        VOICE_GTTS_NAME="fr",
        VOICE_MODEL="gtts",
        ANNOUNCE_CALL_TRANSLATION="vo",
    )
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=user_id).first()
        return user if user and user.active else None

    app.register_blueprint(admin_announce.admin_announce_bp)
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
        admin.roles.append(Role(name="admin", admin_announce=True))
        db.session.add(admin)
        db.session.add_all([
            Language(code="fr", name="Français", translation="Français",
                     sort_order=1, voice_model="gtts", voice_gtts_name="fr"),
            Language(code="en", name="Anglais", translation="English",
                     sort_order=2, voice_model="google",
                     voice_google_name="en-GB-Wavenet-B", voice_google_region="en-GB"),
            Language(code="de", name="Allemand", translation="Deutsch",
                     sort_order=3, voice_model="google", voice_is_active=True),
            Language(code="xx", name="Inconnue", translation="Xx",
                     sort_order=4, voice_model=None),
            Language(code="it", name="Italien", translation="Italiano",
                     sort_order=5, is_active=False),
        ])
        db.session.commit()
    yield app


@pytest.fixture()
def client(app):
    c = app.test_client()
    c.post("/_test/login")
    return c


def _lang(app, code):
    with app.app_context():
        lang = Language.query.filter_by(code=code).one()
        return SimpleNamespace(id=lang.id, voice_model=lang.voice_model,
                               voice_google_name=lang.voice_google_name,
                               voice_google_region=lang.voice_google_region)


def test_recapitulatif_reflete_le_moteur_reellement_utilise(client):
    with patch.object(admin_announce, "get_google_credentials", return_value=b"k"):
        html = client.get("/admin/announce/voices_summary").get_data(as_text=True)
    assert "Français" in html and "Anglais" in html
    assert "Italien" not in html  # langue inactive : jamais annoncée
    assert "en-GB-Wavenet-B" in html
    # Allemand : Google demandé mais aucune voix choisie → repli annoncé.
    assert "gTTS (repli)" in html and "Aucune voix Google choisie" in html


def test_recapitulatif_sans_cle_signale_le_repli(client):
    with patch.object(admin_announce, "get_google_credentials", return_value=None):
        html = client.get("/admin/announce/voices_summary").get_data(as_text=True)
    assert "Aucune clé Google enregistrée" in html


def test_recapitulatif_toujours_en_francais(app, client):
    app.config["ANNOUNCE_CALL_TRANSLATION"] = "fr"
    with patch.object(admin_announce, "get_google_credentials", return_value=b"k"):
        html = client.get("/admin/announce/voices_summary").get_data(as_text=True)
    assert html.count("Voix du français") == 3  # en, de, xx


def test_tout_sur_google_choisit_une_voix_et_signale_les_langues_sans_voix(app, client):
    voices = VOICES + [_voice("en-GB-Wavenet-B", "en-GB")]
    with patch.object(admin_announce, "get_google_credentials", return_value=b"k"), \
            patch.object(admin_announce, "list_google_voices", return_value=voices):
        response = client.post("/admin/announce/google/use_for_all")
    assert response.status_code == 200
    trigger = json.loads(response.headers["HX-Trigger"])
    assert "voicesChanged" in trigger
    fr, en, de, xx = (_lang(app, c) for c in ("fr", "en", "de", "xx"))
    assert (fr.voice_model, fr.voice_google_name, fr.voice_google_region) == \
        ("google", "fr-FR-Neural2-A", "fr-FR")
    # Voix déjà choisie : conservée.
    assert en.voice_google_name == "en-GB-Wavenet-B"
    assert (de.voice_model, de.voice_google_name) == ("google", "de-DE-Standard-A")
    # Aucune voix Google pour « xx » : reste tel quel (gTTS par défaut).
    assert xx.voice_model is None
    # Le français est reflété dans la configuration de ce processus.
    assert app.config["VOICE_MODEL"] == "google"
    assert app.config["VOICE_GOOGLE_NAME"] == "fr-FR-Neural2-A"


def test_tout_sur_google_sans_cle_ne_modifie_rien(app, client):
    with patch.object(admin_announce, "get_google_credentials", return_value=None):
        response = client.post("/admin/announce/google/use_for_all")
    assert response.status_code == 204
    assert _lang(app, "fr").voice_model == "gtts"


def test_tout_sur_google_si_google_refuse_ne_modifie_rien(app, client):
    with patch.object(admin_announce, "get_google_credentials", return_value=b"k"), \
            patch.object(admin_announce, "list_google_voices",
                         side_effect=RuntimeError("PermissionDenied")):
        client.post("/admin/announce/google/use_for_all")
    assert _lang(app, "fr").voice_model == "gtts"


def test_liste_des_voix_preselectionne_la_voix_de_la_langue_editee(app, client):
    en = _lang(app, "en")
    voices = [_voice("en-GB-Wavenet-B", "en-GB"), _voice("en-GB-Neural2-A", "en-GB")]
    admin_announce._GOOGLE_VOICES_CACHE.clear()
    with patch.object(admin_announce, "get_google_credentials", return_value=b"k"), \
            patch.object(admin_announce, "_fetch_google_voices", return_value=voices):
        html = client.post("/admin/announce/google/filter_voices",
                           data={"voice_google_language": "en",
                                 "language_id": str(en.id)}).get_data(as_text=True)
    assert 'value="en-GB-Wavenet-B|en-GB" selected' in html
    assert "féminine, Wavenet (en-GB)" in html
    assert "Choisir une voix" not in html


def test_liste_des_voix_sans_voix_enregistree_propose_un_choix(app, client):
    de = _lang(app, "de")
    with patch.object(admin_announce, "get_google_credentials", return_value=b"k"), \
            patch.object(admin_announce, "_fetch_google_voices",
                         return_value=[_voice("de-DE-Neural2-A", "de-DE")]):
        admin_announce._GOOGLE_VOICES_CACHE.clear()
        html = client.post("/admin/announce/google/filter_voices",
                           data={"language_id": str(de.id)}).get_data(as_text=True)
    assert "Choisir une voix" in html


def test_panneau_de_langue_sans_cle_desactive_google(client):
    with patch.object(admin_announce, "get_google_credentials", return_value=None):
        html = client.post("/admin/announce/select_language_voice",
                           data={"language_code": "fr"}).get_data(as_text=True)
    assert 'id="voice_model_google"' in html
    google_radio = html.split('id="voice_model_google"', 1)[1].split(">", 1)[0]
    assert "disabled" in google_radio
    # Le français n'a pas d'interrupteur « annoncer dans sa langue ».
    assert 'id="voice_is_active"' not in html
    assert 'data-test-language="fr"' in html


def test_panneau_de_langue_etrangere_avec_cle(client):
    with patch.object(admin_announce, "get_google_credentials", return_value=b"k"):
        html = client.post("/admin/announce/select_language_voice",
                           data={"language_code": "de"}).get_data(as_text=True)
    assert 'id="voice_is_active"' in html
    assert "voice-picker-google" in html
    assert "Aucune voix Google choisie" in html


def test_sauvegarde_du_moteur_rafraichit_le_recapitulatif(app, client):
    de = _lang(app, "de")
    response = client.post("/admin/announce/save_voice_model",
                           data={"language_id": str(de.id), "voice_model": "gtts"})
    assert response.status_code == 200
    trigger = json.loads(response.headers["HX-Trigger"])
    assert "voicesChanged" in trigger
    assert _lang(app, "de").voice_model == "gtts"
