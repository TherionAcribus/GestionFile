"""Tests purs du diagnostic de la page borne (diagnostics.py).

Aucune base : on utilise de faux boutons (objets légers) exposant les mêmes
attributs que le modèle ``Button`` lus par le module.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from diagnostics import (  # noqa: E402
    find_empty_present_parents,
    has_any_usable_button,
    collect_patient_page_alerts,
)


class FakeButton:
    def __init__(self, id, label="B", is_parent=False, is_present=True,
                 parent_button_id=None):
        self.id = id
        self.label = label
        self.is_parent = is_parent
        self.is_present = is_present
        self.parent_button_id = parent_button_id


# --------------------------------------------------------------------------
# find_empty_present_parents
# --------------------------------------------------------------------------

def test_parent_without_children_is_flagged():
    parent = FakeButton(7, "Membre de l'équipe", is_parent=True)
    leaf = FakeButton(1, "Ordonnance")
    empties = find_empty_present_parents([parent, leaf])
    assert [b.id for b in empties] == [7]


def test_parent_with_present_child_is_not_flagged():
    parent = FakeButton(6, "Test", is_parent=True)
    child = FakeButton(8, "Covid", parent_button_id=6)
    assert find_empty_present_parents([parent, child]) == []


def test_parent_with_only_hidden_children_is_flagged():
    parent = FakeButton(6, "Test", is_parent=True)
    hidden_child = FakeButton(8, "Covid", parent_button_id=6, is_present=False)
    empties = find_empty_present_parents([parent, hidden_child])
    assert [b.id for b in empties] == [6]


def test_hidden_parent_is_not_flagged():
    parent = FakeButton(7, "Staff", is_parent=True, is_present=False)
    assert find_empty_present_parents([parent]) == []


# --------------------------------------------------------------------------
# has_any_usable_button
# --------------------------------------------------------------------------

def test_usable_with_a_present_leaf():
    assert has_any_usable_button([FakeButton(1, "Ordonnance")]) is True


def test_not_usable_with_only_empty_parent():
    parent = FakeButton(7, "Staff", is_parent=True)
    assert has_any_usable_button([parent]) is False


def test_usable_with_parent_and_child():
    parent = FakeButton(6, "Test", is_parent=True)
    child = FakeButton(8, "Covid", parent_button_id=6)
    assert has_any_usable_button([parent, child]) is True


def test_not_usable_when_all_hidden():
    assert has_any_usable_button([FakeButton(1, "X", is_present=False)]) is False


# --------------------------------------------------------------------------
# collect_patient_page_alerts
# --------------------------------------------------------------------------

def test_empty_parent_produces_warning_with_button_id():
    parent = FakeButton(7, "Membre de l'équipe", is_parent=True)
    leaf = FakeButton(1, "Ordonnance")
    alerts = collect_patient_page_alerts([parent, leaf])
    codes = [a["code"] for a in alerts]
    assert "empty_parent_button" in codes
    warn = next(a for a in alerts if a["code"] == "empty_parent_button")
    assert warn["level"] == "warning"
    assert warn["button_id"] == 7
    assert "Membre de l'équipe" in warn["message"]


def test_no_usable_button_is_danger_and_sorted_first():
    parent = FakeButton(7, "Staff", is_parent=True)
    alerts = collect_patient_page_alerts([parent])
    assert alerts[0]["code"] == "no_usable_button"
    assert alerts[0]["level"] == "danger"


def test_clean_config_has_no_alerts():
    leaf = FakeButton(1, "Ordonnance")
    assert collect_patient_page_alerts([leaf]) == []


def test_print_picture_enabled_but_missing_is_flagged():
    leaf = FakeButton(1, "Ordonnance")
    config = {
        "PAGE_PATIENT_BUTTON_PRINT_TICKET_DISPLAY_PICTURE": True,
        "PAGE_PATIENT_BUTTON_PRINT_TICKET_PICTURE": "",
        "PAGE_PATIENT_BUTTON_CANCEL_DISPLAY_PICTURE": True,
        "PAGE_PATIENT_BUTTON_CANCEL_PICTURE": "",
    }
    codes = [a["code"] for a in collect_patient_page_alerts([leaf], config)]
    assert "print_button_picture_missing" in codes
    assert "cancel_button_picture_missing" in codes


def test_picture_set_is_not_flagged():
    leaf = FakeButton(1, "Ordonnance")
    config = {
        "PAGE_PATIENT_BUTTON_PRINT_TICKET_DISPLAY_PICTURE": True,
        "PAGE_PATIENT_BUTTON_PRINT_TICKET_PICTURE": "print.png",
    }
    codes = [a["code"] for a in collect_patient_page_alerts([leaf], config)]
    assert "print_button_picture_missing" not in codes
