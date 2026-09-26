"""Point 3 — alignement du catalogue de traduction avec les textes utilisés.

Le catalogue proposé à l'administration (params_registry._TRANSLATABLE_KEYS,
exposé via ``TRANSLATABLE_CONFIG_KEYS``) divergeait des textes réellement
traduits :

* des clés consommées n'y figuraient pas — ``page_patient_interface_scan_explanation``,
  ``phone_your_turn_line1..6`` : impossibles à traduire dans l'interface ;
* des clés proposées n'étaient jamais lues avec une langue —
  ``announce_text_down_patients`` (texte d'accueil sans contexte patient) ;
* ``announce_call_text`` / ``announce_ongoing_text`` étaient traduisibles mais
  les bannières restaient en français, alors que le TTS parle déjà dans la
  langue du patient (``announce_call_sound``).

Verrouillé ici :

1. chaque clé du catalogue existe dans le registre avec une colonne textuelle
   (éditable) ;
2. chaque clé est réellement référencée par le code de traduction (recherche
   statique dans les sources hors registre/administration) ;
3. le fichier JSON historique, conservé à titre documentaire, reste aligné ;
4. les bannières d'annonce (snapshot ``/announce/state``, évènement socket
   ``add_calling``, fragment ``/announce/patients_ongoing``) suivent la langue
   de chaque patient, avec repli français.
"""

import json
import os
import re
from pathlib import Path

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from models import (
    db, Activity, ConfigOption, Counter, Language, Patient, Pharmacist,
    Translation,
)
from params_registry import TRANSLATABLE_CONFIG_KEYS, get_spec


ROOT = Path(__file__).resolve().parents[1]


# --- 1-3. Cohérence du catalogue -------------------------------------------

def test_cles_du_catalogue_enregistrees_et_textuelles():
    """Éditable : chaque clé traduisible est connue du registre et rangée dans
    une colonne textuelle de ConfigOption — sinon la collecte la rejetterait
    (clé ignorée) ou lirait la mauvaise colonne."""
    assert TRANSLATABLE_CONFIG_KEYS, "le catalogue ne doit pas être vide"
    for key in TRANSLATABLE_CONFIG_KEYS:
        spec = get_spec(key)
        assert spec is not None, f"{key} absente du registre"
        assert spec.translatable, f"{key} non marquée translatable dans le registre"
        assert spec.value_type in ("value_str", "value_text"), (
            f"{key} n'est pas un texte ({spec.value_type})")


def _usage_pattern(key):
    """Motif prouvant qu'une clé est réellement consommée par le code.

    Les familles numérotées sont résolues via f-string dans routes/patient.py
    (``f'phone_line{line}'``) ; les autres clés doivent apparaître sous forme
    littérale — appel direct à un helper ou référence dans un mapping lu par
    ``choose_text_translation``.
    """
    if re.fullmatch(r"phone_line\d", key):
        return re.compile(r"f['\"]phone_line\{line\}['\"]")
    if re.fullmatch(r"phone_your_turn_line\d", key):
        return re.compile(r"f['\"]phone_your_turn_line\{line\}['\"]")
    return re.compile(rf"['\"]{re.escape(key)}['\"]")


def test_cles_du_catalogue_reellement_consommees():
    """Utilisée : chaque clé du catalogue doit apparaître dans les sources
    applicatives — sinon l'administrateur traduit un texte jamais affiché."""
    sources = [
        p for p in ROOT.rglob("*.py")
        if ".venv" not in p.parts
        and "tests" not in p.parts
        and p.name not in ("params_registry.py", "admin_translation.py")
    ]
    corps = "\n".join(p.read_text(encoding="utf-8") for p in sources)
    for key in TRANSLATABLE_CONFIG_KEYS:
        assert _usage_pattern(key).search(corps), (
            f"{key} est proposée à la traduction mais jamais référencée "
            "par le code de rendu")


def test_cles_utilisees_presentes_dans_le_catalogue():
    """Régression : ces clés étaient traduites par le code mais absentes du
    catalogue — introuvables dans l'administration."""
    assert "page_patient_interface_scan_explanation" in TRANSLATABLE_CONFIG_KEYS
    for i in range(1, 7):
        assert f"phone_your_turn_line{i}" in TRANSLATABLE_CONFIG_KEYS


def test_cles_sans_contexte_de_langue_hors_catalogue():
    """Les textes de l'écran d'annonce sans patient (accueil, bandeaux) n'ont
    pas de langue à laquelle se rattacher : les proposer à la traduction
    produisait des lignes jamais affichées."""
    assert "announce_text_down_patients" not in TRANSLATABLE_CONFIG_KEYS
    assert "announce_title" not in TRANSLATABLE_CONFIG_KEYS
    assert "announce_text_up_patients" not in TRANSLATABLE_CONFIG_KEYS


def test_fichier_json_historique_aligne_sur_le_registre():
    """static/json/config_keys_to_translate.json n'est plus lu par
    l'application mais subsiste à titre documentaire : il ne doit pas dériver
    du registre, faute de quoi il redeviendrait une fausse source de vérité."""
    keys_file = ROOT / "static" / "json" / "config_keys_to_translate.json"
    keys = set(json.loads(keys_file.read_text(encoding="utf-8"))
               ["config_keys_to_translate"])
    assert keys == set(TRANSLATABLE_CONFIG_KEYS)


def test_collecte_lit_le_registre():
    """La collecte d'administration prend sa liste dans le registre."""
    from routes.admin_translation import load_config_keys_to_translate
    assert set(load_config_keys_to_translate()) == set(TRANSLATABLE_CONFIG_KEYS)


# --- 4. Bannières d'annonce dans la langue du patient ------------------------

@pytest.fixture
def application():
    app = Flask(__name__, template_folder=os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "templates"))
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        TESTING=True,
        ANNOUNCE_CALL_TEXT="Patient {N} au comptoir {C}",
        ANNOUNCE_ONGOING_TEXT="Comptoir {C} : Patient {N}",
        ALGO_IS_ACTIVATED=False,
        ALGO_OVERTAKEN_LIMIT=3,
        PHARMACY_NAME="Pharmacie de test",
    )
    db.init_app(app)
    from routes.announce import announce_bp
    app.register_blueprint(announce_bp)
    with app.app_context():
        db.create_all(bind_key=None)
        yield app
        db.drop_all(bind_key=None)


@pytest.fixture
def client(application):
    return application.test_client()


def _jeu_bilingue():
    """Un comptoir, deux patients 'calling' : un FR, un EN traduit."""
    francais = Language(code="fr", name="Français", translation="Français")
    anglais = Language(code="en", name="Anglais", translation="English")
    activite = Activity(name="Ordonnance", letter="O")
    membre = Pharmacist(name="Alice", initials="AL")
    comptoir = Counter(name="1", sort_order=1, is_active=True)
    comptoir.staff = membre
    option_appel = ConfigOption(config_key="announce_call_text",
                                value_str="Patient {N} au comptoir {C}")
    option_ongoing = ConfigOption(config_key="announce_ongoing_text",
                                  value_str="Comptoir {C} : Patient {N}")
    db.session.add_all([francais, anglais, activite, membre, comptoir,
                        option_appel, option_ongoing])
    db.session.commit()
    # row_id = id de la ConfigOption : la contrainte d'unicité porte sur
    # (table_name, column_name, row_id, language_code) — sans key_name.
    db.session.add(Translation(
        table_name="ConfigOption", column_name="value_str",
        key_name="announce_call_text", row_id=option_appel.id,
        language_code="en",
        translated_text="Patient {N} to counter {C}"))
    db.session.add(Translation(
        table_name="ConfigOption", column_name="value_str",
        key_name="announce_ongoing_text", row_id=option_ongoing.id,
        language_code="en",
        translated_text="Counter {C}: patient {N}"))
    patient_fr = Patient(call_number=100, status="calling",
                         activity_id=activite.id, language_id=francais.id,
                         counter_id=comptoir.id)
    patient_en = Patient(call_number=101, status="calling",
                         activity_id=activite.id, language_id=anglais.id,
                         counter_id=comptoir.id)
    db.session.add_all([patient_fr, patient_en])
    db.session.commit()
    return patient_fr, patient_en


def test_state_banniere_dans_la_langue_de_chaque_patient(client, application):
    with application.app_context():
        patient_fr, patient_en = _jeu_bilingue()
        id_fr, id_en = patient_fr.id, patient_en.id
    appels = {c["id"]: c["text"] for c in
              client.get("/announce/state").get_json()["calling"]}
    assert "to counter" in appels[id_en]
    assert "au comptoir" in appels[id_fr]


def test_state_repli_francais_sans_traduction(client, application):
    """Langue étrangère sans traduction enregistrée : le gabarit français
    s'affiche, jamais une bannière vide."""
    with application.app_context():
        _, patient_en = _jeu_bilingue()
        id_en = patient_en.id
        db.session.delete(Translation.query.filter_by(
            key_name="announce_call_text", language_code="en").one())
        db.session.commit()
    appels = {c["id"]: c["text"] for c in
              client.get("/announce/state").get_json()["calling"]}
    assert "au comptoir" in appels[id_en]


def test_add_calling_socket_dans_la_langue_du_patient(application, monkeypatch):
    """L'évènement incrémental add_calling doit suivre la même règle que le
    snapshot /announce/state — sinon l'écran afficherait un texte différent
    selon que la bannière vient du socket ou de la resynchronisation."""
    from services import calling_service
    emissions = []
    monkeypatch.setattr(
        calling_service, "communikation",
        lambda stream, data=None, flag=None, event="update", client_id=None:
            emissions.append((stream, event, data)))
    with application.app_context():
        _, patient_en = _jeu_bilingue()
        calling_service.announce_call(patient_en.counter_id, patient_en)
    ecran = next(d for s, e, d in emissions if e == "add_calling")
    assert "to counter" in ecran["text"]


def test_patients_ongoing_dans_la_langue_du_patient(client, application):
    with application.app_context():
        _, patient_en = _jeu_bilingue()
        patient_en.status = "ongoing"
        db.session.commit()
    corps = client.get("/announce/patients_ongoing").get_data(as_text=True)
    assert "Counter 1: patient 101" in corps


def test_get_announce_templates_requetes_groupees(application):
    """Le rendu groupé des bannières fait deux requêtes fixes (codes de langue
    puis traductions), quel que soit le nombre de patients — et sans exiger
    que l'appelant ait anticipé la relation ``patient.language``."""
    from utils import get_announce_templates
    with application.app_context():
        patient_fr, patient_en = _jeu_bilingue()
        id_fr, id_en = patient_fr.id, patient_en.id
        # Chargement paresseux volontaire : prouve que le helper ne fait pas
        # de N+1 via patient.language.
        patients = Patient.query.all()
        requetes = []
        from sqlalchemy import event
        from models import db as _db

        def compte(*args, **kwargs):
            requetes.append(args)

        event.listen(_db.engine, "before_cursor_execute", compte)
        try:
            gabarits = get_announce_templates(
                "announce_call_text", patients, "défaut")
        finally:
            event.remove(_db.engine, "before_cursor_execute", compte)
    assert gabarits[id_en] == "Patient {N} to counter {C}"
    assert gabarits[id_fr] == "Patient {N} au comptoir {C}"
    assert len(requetes) == 2, (
        f"{len(requetes)} requêtes pour {len(patients)} patients")
