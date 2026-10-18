"""Parcours patient de l'éditeur visuel (page_journey) : cohérence de la
définition avec l'adaptateur et états calculés selon les réglages."""

import json
from pathlib import Path

import pytest

from page_editor import ADAPTERS
from page_journey import (
    CONDITIONAL,
    HIDDEN,
    PATIENT_JOURNEY,
    SHOWN,
    STATES,
    evaluate,
    iter_steps,
    journey_overview,
    public_journey,
)

ROOT = Path(__file__).resolve().parents[1]


def _factory_config():
    data = json.loads((ROOT / "static/json/default_config.json").read_text(encoding="utf-8"))
    return data["configurations"]


def _patient_fields():
    return {
        field["key"]: field
        for component in ADAPTERS["patient"]["components"].values()
        for field in component["config"]
    }


def test_journey_definition_matches_patient_adapter():
    """Chaque étape vise un scénario d'aperçu, des composants et des
    réglages qui existent : une faute de frappe casserait la frise."""
    adapter = ADAPTERS["patient"]
    scenarios = {scenario["id"] for scenario in adapter["scenarios"]}
    fields = _patient_fields()
    seen = set()
    for step in iter_steps(PATIENT_JOURNEY):
        assert step["id"] not in seen
        seen.add(step["id"])
        assert step["scenario"] in scenarios
        assert set(step.get("components", [])) <= set(adapter["components"])
        assert step["status"], step["id"]
        assert all(rule["state"] in STATES for rule in step["status"])
        # La dernière règle sert de repli : elle ne doit pas être conditionnée.
        assert "when" not in step["status"][-1]
        for choice in step.get("choices", []):
            assert choice["key"] in fields, choice["key"]
        # Chaque étape dit si elle se masque (interrupteur) ou pourquoi non.
        assert ("toggle" in step) != ("fixed" in step), step["id"]
        if "toggle" in step:
            assert step["toggle"]["key"] in fields
    # Tous les scénarios de l'aperçu ont leur place dans le parcours.
    assert {step["scenario"] for step in iter_steps(PATIENT_JOURNEY)} == scenarios


def test_public_journey_completes_choices_from_component_fields():
    journey = public_journey("patient", _patient_fields())
    choices = {
        choice["key"]: choice
        for step in iter_steps(journey) for choice in step.get("choices", [])
    }
    behavior = choices["page_patient_print_fail_behavior"]
    assert behavior["type"] == "select"
    assert [value for value, _ in behavior["choices"]] == ["ask", "keep", "cancel"]
    assert choices["page_patient_end_timer"]["unit"] == "s"
    toggles = {step["id"]: step["toggle"] for step in iter_steps(journey) if "toggle" in step}
    assert set(toggles) == {"activity_inactive", "validation", "staff_called", "conclusion"}
    assert toggles["conclusion"]["type"] == "bool"
    assert toggles["validation"]["invert"] is True
    # Libellé repris du champ quand le parcours n'en impose pas.
    assert behavior["label"] == _patient_fields()["page_patient_print_fail_behavior"]["label"]
    assert public_journey("announce", {}) is None
    # La définition partagée n'est pas modifiée par la complétion.
    assert "type" not in PATIENT_JOURNEY["steps"][2]["choices"][0]


def test_evaluate_condition_language():
    config = {"a": True, "b": "ask", "c": False}
    assert evaluate(None, config)
    assert evaluate({"config": "a"}, config)
    assert evaluate({"config": "b", "is": ["ask", "keep"]}, config)
    assert not evaluate({"config": "b", "is": "cancel"}, config)
    assert evaluate({"not": {"config": "c"}}, config)
    assert evaluate({"all": [{"config": "a"}, {"not": {"config": "c"}}]}, config)
    assert evaluate({"any": [{"config": "c"}, {"config": "a"}]}, config)
    assert not evaluate({"fact": "parents"}, config, {"parents": []})
    assert evaluate({"fact": "parents"}, config, {"parents": ["Tests"]})


def test_factory_settings_journey():
    """Réglages d'usine : validation affichée, pas de QR ni de scan → chaque
    inscription imprime un ticket."""
    overview = journey_overview("patient", _factory_config(), {"children_parents": ["Tests"]})
    assert overview["home"][0] == SHOWN
    assert overview["children"] == (
        CONDITIONAL, "Seulement après un bouton de regroupement : « Tests ».")
    assert overview["validation"][0] == SHOWN
    assert overview["conclusion"][0] == SHOWN
    assert overview["print_error"][0] == CONDITIONAL
    assert overview["print_uncertain"][0] == CONDITIONAL


@pytest.mark.parametrize(("changes", "expected"), [
    ({"page_patient_direct_print": True},
     {"validation": HIDDEN, "printing": SHOWN}),
    ({"page_patient_direct_print": False, "page_patient_display_button_scan": True},
     {"validation": SHOWN, "printing": CONDITIONAL}),
    # QR « adresse web » : le scan inscrit sans ticket, même sans bouton.
    ({"page_patient_direct_print": False, "page_patient_display_button_scan": False,
      "page_patient_qrcode_display": True, "page_patient_qrcode_web_page": True},
     {"printing": CONDITIONAL}),
    # QR « données brutes » sans bouton : rien n'inscrit sans ticket.
    ({"page_patient_direct_print": False, "page_patient_display_button_scan": False,
      "page_patient_qrcode_display": True, "page_patient_qrcode_web_page": False},
     {"printing": SHOWN}),
    # L'impression directe coupe la route « scan » (pas d'écran de validation).
    ({"page_patient_direct_print": True, "page_patient_display_button_scan": True,
      "page_patient_qrcode_display": True, "page_patient_qrcode_web_page": True},
     {"validation": HIDDEN, "printing": SHOWN}),
    ({"page_patient_disable_button": False}, {"activity_inactive": HIDDEN}),
    ({"page_patient_disable_button": True}, {"activity_inactive": CONDITIONAL}),
    ({"page_patient_print_fail_show_staff": False}, {"staff_called": HIDDEN}),
    ({"page_patient_print_fail_show_staff": True}, {"staff_called": CONDITIONAL}),
    ({"page_patient_display_conclusion": False}, {"conclusion": HIDDEN}),
    ({"page_patient_display_conclusion": True}, {"conclusion": SHOWN}),
    # Validation et conclusion masquées : accueil puis impression seulement.
    ({"page_patient_direct_print": True, "page_patient_display_conclusion": False},
     {"validation": HIDDEN, "printing": SHOWN, "conclusion": HIDDEN}),
])
def test_journey_states_follow_settings(changes, expected):
    config = {**_factory_config(), **changes}
    overview = journey_overview("patient", config, {})
    assert {key: overview[key][0] for key in expected} == expected


def test_hidden_conclusion_disables_its_content_choices():
    conclusion = next(step for step in PATIENT_JOURNEY["steps"] if step["id"] == "conclusion")
    config = {**_factory_config(), "page_patient_display_conclusion": False}
    assert not any(evaluate(choice.get("enabled_when"), config) for choice in conclusion["choices"])
    config["page_patient_display_conclusion"] = True
    assert evaluate(conclusion["choices"][0].get("enabled_when"), config)


def test_factory_config_shows_conclusion():
    assert _factory_config()["page_patient_display_conclusion"] is True


def test_factory_uncertain_timeout_is_short():
    """Valeur d'usine courte : le patient suivant ne doit pas se retrouver
    devant « J'ai récupéré mon ticket / Je n'ai pas de ticket »."""
    config = _factory_config()
    assert config["page_patient_print_uncertain_timer"] == 15
    assert config["page_patient_print_uncertain_timeout_action"] == "received"


def test_children_step_hidden_without_grouping_button():
    overview = journey_overview("patient", _factory_config(), {"children_parents": []})
    assert overview["children"][0] == HIDDEN


def test_scan_warning_and_disabled_choices():
    validation = next(step for step in PATIENT_JOURNEY["steps"] if step["id"] == "validation")
    conclusion = next(step for step in PATIENT_JOURNEY["steps"] if step["id"] == "conclusion")
    config = {**_factory_config(), "page_patient_direct_print": False,
              "page_patient_display_button_scan": True, "page_patient_qrcode_display": False}
    assert [w["text"] for w in validation["warnings"] if evaluate(w["when"], config)] == [
        "Le bouton « Scanner » est proposé sans QR code : le patient n’a rien à scanner."]
    after_scan = next(c for c in conclusion["choices"] if c["key"] == "page_patient_print_after_scan")
    assert evaluate(after_scan["enabled_when"], config)
    config["page_patient_direct_print"] = True
    assert not evaluate(after_scan["enabled_when"], config)
    assert not any(evaluate(w["when"], config) for w in validation["warnings"])


def test_staff_button_offered_on_both_print_incident_screens():
    """« Appeler le personnel » se règle depuis les écrans où il apparaît,
    pas seulement depuis « Personnel appelé »."""
    steps = {step["id"]: step for step in iter_steps(PATIENT_JOURNEY)}
    for step_id in ("print_error", "print_uncertain"):
        keys = [choice["key"] for choice in steps[step_id]["choices"]]
        assert "page_patient_print_fail_show_staff" in keys, step_id
    assert steps["staff_called"]["toggle"]["key"] == "page_patient_print_fail_show_staff"


@pytest.mark.parametrize(("changes", "warned"), [
    ({"page_patient_print_fail_behavior": "ask", "page_patient_print_fail_show_retry": False,
      "page_patient_print_fail_show_staff": False}, True),
    ({"page_patient_print_fail_behavior": "ask", "page_patient_print_fail_show_retry": True,
      "page_patient_print_fail_show_staff": False}, False),
    ({"page_patient_print_fail_behavior": "ask", "page_patient_print_fail_show_retry": False,
      "page_patient_print_fail_show_staff": True}, False),
    # Petite officine : « Conserver » sans aucun bouton est cohérent.
    ({"page_patient_print_fail_behavior": "keep", "page_patient_print_fail_show_retry": False,
      "page_patient_print_fail_show_staff": False}, False),
])
def test_print_error_warns_when_only_back_button_remains(changes, warned):
    step = next(step for step in iter_steps(PATIENT_JOURNEY) if step["id"] == "print_error")
    config = {**_factory_config(), **changes}
    active = [w for w in step["warnings"] if evaluate(w["when"], config)]
    assert bool(active) is warned
    if warned:
        assert "Conserver dans la file" in active[0]["text"]


def test_editor_js_mirrors_condition_language():
    """Le navigateur évalue la même définition : les opérateurs reconnus par
    page_journey.evaluate doivent l'être aussi par page_editor.js."""
    source = (ROOT / "static/js/page_editor.js").read_text(encoding="utf-8")
    for fragment in ("condition.all", "condition.any", "condition.not",
                     "condition.fact", "condition.config", "journey_facts",
                     "adapter.journey"):
        assert fragment in source
