"""Parcours navigateur de l'éditeur visuel.

Prérequis : serveur local démarré, MySQL migré, Chromium Playwright installé et
compte admin/admin. Ces tests sont exclus de la suite standard par le marqueur
``e2e``. L'éditeur Annonce doit être activé (valeur par défaut).
"""

import os
from pathlib import Path

import pytest


pytestmark = pytest.mark.e2e
pytest.importorskip("playwright.sync_api", reason="playwright non installé")

from playwright.sync_api import Page, expect


BASE_URL = os.getenv("E2E_BASE_URL", "http://127.0.0.1:5000")


@pytest.fixture(scope="session")
def browser(playwright):
    instance = playwright.chromium.launch(headless=True)
    yield instance
    instance.close()


@pytest.fixture(scope="session")
def admin_page(browser):
    context = browser.new_context()
    page = context.new_page()
    page.goto(f"{BASE_URL}/admin")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "admin")
    page.click('input[type="submit"][value="Login"]')
    page.wait_for_url(f"{BASE_URL}/admin")
    yield page
    context.close()


def open_editor(page: Page):
    page.goto(f"{BASE_URL}/admin/page-editor/announce")
    expect(page.locator("#page-editor")).to_have_attribute("aria-busy", "false")
    expect(page.frame_locator("#editor-preview").locator("#text_title")).to_be_visible()


@pytest.mark.parametrize("route", ["announce", "patient", "phone"])
def test_advanced_mode_uses_shared_editor_design(admin_page: Page, route):
    admin_page.goto(f"{BASE_URL}/admin/{route}")
    expect(admin_page.locator(".advanced-editor-page")).to_be_visible()
    expect(admin_page.locator(".advanced-editor-workspace")).to_be_visible()
    expect(admin_page.get_by_role("link", name="Mode avancé")).to_have_attribute("aria-current", "page")

    color_control = admin_page.locator("[data-admin-color-control]").first
    expect(color_control).to_be_visible()
    value_input = color_control.locator(".admin-color-value")
    picker = color_control.locator(".admin-color-picker")
    original = value_input.input_value()
    value_input.fill("#123456")
    expect(picker).to_have_value("#123456")
    value_input.fill(original)

    marker = admin_page.locator(".admin-marker").first
    expect(marker).to_have_count(1)
    collapse = marker.locator("xpath=ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' accordion-collapse ')][1]")
    collapse_id = collapse.get_attribute("id")
    if collapse_id and not marker.is_visible():
        admin_page.locator(f'[data-bs-target="#{collapse_id}"]').click()
    expect(marker).to_be_visible()


def test_edit_resize_scenario_undo_and_keyboard_move(admin_page: Page):
    open_editor(admin_page)
    title = admin_page.locator('[data-component-id="title"]')
    title.click()
    textarea = admin_page.locator("#editor-config-announce_title")
    original = textarea.input_value()
    textarea.fill("Bienvenue ")
    admin_page.get_by_role("button", name="Insérer {P} : Nom de la pharmacie").click()
    expect(textarea).to_have_value("Bienvenue {P}")
    assert "{P}" not in admin_page.frame_locator("#editor-preview").locator("#text_title").inner_text()
    textarea.fill("Aperçu E2E")
    expect(admin_page.frame_locator("#editor-preview").locator("#text_title")).to_have_text("Aperçu E2E")

    admin_page.locator("#editor-field-span").evaluate(
        "element => { element.value = '8'; element.dispatchEvent(new Event('input', {bubbles:true})); element.dispatchEvent(new Event('change', {bubbles:true})); }"
    )
    applied_width = admin_page.frame_locator("#editor-preview").locator("#text_title").evaluate(
        "element => element.style.width"
    )
    assert applied_width == "66.6667%"

    admin_page.locator("#editor-palette-primary").fill("#123456")
    admin_page.locator("#editor-palette-primary").locator("xpath=following-sibling::button").click()
    preview_primary = admin_page.frame_locator("#editor-preview").locator("html").evaluate(
        "element => element.style.getPropertyValue('--title_background_color')"
    )
    assert preview_primary == "#123456"
    admin_page.locator("#editor-undo").click()

    admin_page.locator("#editor-scenario").select_option("gallery")
    expect(admin_page.frame_locator("#editor-preview").locator("#div_pub")).to_be_visible()
    admin_page.get_by_role("button", name="Descendre").click()
    admin_page.locator("#editor-undo").click()
    textarea = admin_page.locator("#editor-config-announce_title")
    textarea.fill(original)
    admin_page.once("dialog", lambda dialog: dialog.accept())
    admin_page.locator("#editor-discard").click()


def test_copy_published_palette_stays_in_visual_draft(admin_page: Page):
    open_editor(admin_page)
    original = admin_page.locator("#editor-palette-primary").input_value()
    admin_page.locator("#editor-palette-primary").fill("#123456")
    admin_page.locator("#editor-palette-primary").locator("xpath=following-sibling::button").click()
    admin_page.locator("#editor-palette-source").select_option("patient")
    admin_page.locator("#editor-palette-copy").click()

    expect(admin_page.locator("#editor-status")).to_contain_text("Palette « Borne patient » appliquée")
    assert admin_page.locator("#editor-palette-primary").input_value() != "#123456"
    expect(admin_page.locator("#editor-save")).to_be_enabled()

    admin_page.once("dialog", lambda dialog: dialog.accept())
    admin_page.reload()
    expect(admin_page.locator("#page-editor")).to_have_attribute("aria-busy", "false")
    expect(admin_page.locator("#editor-palette-primary")).to_have_value(original)


def test_publish_matches_new_real_screen_without_forced_reload(admin_page: Page):
    open_editor(admin_page)
    admin_page.locator('[data-component-id="title"]').click()
    title_field = admin_page.locator("#editor-config-announce_title")
    original = title_field.input_value()
    temporary = "Publication visuelle E2E"
    try:
        title_field.fill(temporary)
        admin_page.locator("#editor-save").click()
        expect(admin_page.locator("#editor-status")).to_contain_text("Brouillon partagé enregistré")
        admin_page.locator("#editor-publish").click()
        expect(admin_page.locator("#editor-status")).to_contain_text("Publication terminée")
        expect(admin_page.locator("#editor-apply")).to_be_enabled()

        public_page = admin_page.context.new_page()
        public_page.goto(f"{BASE_URL}/announce")
        expect(public_page.locator("#text_title")).to_have_text(temporary)
        public_page.close()
    finally:
        open_editor(admin_page)
        admin_page.locator('[data-component-id="title"]').click()
        admin_page.locator("#editor-config-announce_title").fill(original)
        admin_page.locator("#editor-save").click()
        expect(admin_page.locator("#editor-status")).to_contain_text("Brouillon partagé enregistré")
        admin_page.locator("#editor-publish").click()
        expect(admin_page.locator("#editor-status")).to_contain_text("Publication terminée")


@pytest.mark.parametrize("viewport", [(1920, 1080), (1366, 768)])
def test_reference_visual_capture(admin_page: Page, viewport):
    output_dir = Path("test-results/page-editor")
    output_dir.mkdir(parents=True, exist_ok=True)
    preview = admin_page.context.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    preview.goto(f"{BASE_URL}/admin/page-editor/announce/preview?scenario=multiple")
    expect(preview.locator("#full_page")).to_be_visible()
    preview.screenshot(
        path=str(output_dir / f"announce-{viewport[0]}x{viewport[1]}.png"),
        full_page=True,
    )
    preview.close()
