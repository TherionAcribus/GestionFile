from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy

from flask import current_app
from markupsafe import Markup

from params_registry import get_spec
from utils import validate_config_text
from variables import is_safe_css_value


SCHEMA_VERSION = 1
ALIGNMENTS = frozenset({"left", "center", "right", "stretch"})
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_PAYLOAD_KEYS = frozenset({"schema_version", "page", "base_hash", "layout", "config", "css"})
_LAYOUT_KEYS = frozenset({"zone", "order", "visible", "span", "alignment"})


def _component(label, zone, selector, *, config=None, css=None, span=12):
    return {
        "label": label,
        "zone": zone,
        "zones": [zone],
        "selector": selector,
        "span": span,
        "config": config or [],
        "css": css or [],
    }


ADAPTERS = {
    "announce": {
        "label": "Écran d'annonce",
        "permission": "announce",
        "layout_key": "announce_layout",
        "config_name": "ANNOUNCE_LAYOUT",
        "css_source": "announce",
        "zones": ["header", "main", "aside", "footer"],
        "viewports": [
            {"id": "full-hd", "label": "1920 × 1080", "width": 1920, "height": 1080},
            {"id": "hd", "label": "1366 × 768", "width": 1366, "height": 768},
        ],
        "scenarios": [
            {"id": "empty", "label": "File vide"},
            {"id": "active", "label": "Appel actif"},
            {"id": "multiple", "label": "Plusieurs appels"},
            {"id": "gallery", "label": "Galerie visible"},
        ],
        "components": {
            "title": _component("Titre", "header", "#text_title",
                config=[{"key": "announce_title", "label": "Texte", "type": "text"}],
                css=[{"key": "title_font_size", "label": "Taille", "type": "size"},
                     {"key": "title_font_color", "label": "Couleur", "type": "color"},
                     {"key": "title_background_color", "label": "Fond", "type": "color"},
                     {"key": "title_background_height", "label": "Hauteur", "type": "size"},
                     {"key": "title_font_border_size", "label": "Épaisseur contour", "type": "size"},
                     {"key": "title_font_border_color", "label": "Couleur contour", "type": "color"}]),
            "subtitle": _component("Sous-titre", "header", "#text_subtitle",
                config=[{"key": "announce_subtitle", "label": "Texte", "type": "text"}],
                css=[{"key": "subtitle_font_size", "label": "Taille", "type": "size"},
                     {"key": "subtitle_font_color", "label": "Couleur", "type": "color"},
                     {"key": "subtitle_font_border_size", "label": "Épaisseur contour", "type": "size"},
                     {"key": "subtitle_font_border_color", "label": "Couleur contour", "type": "color"}]),
            "top_message": _component("Message supérieur", "main", "#div_display_text_up",
                config=[{"key": "announce_text_up_patients", "label": "Texte", "type": "text"}],
                css=[{"key": "text_up_font_size", "label": "Taille", "type": "size"},
                     {"key": "text_up_font_color", "label": "Couleur", "type": "color"},
                     {"key": "text_up_background_color", "label": "Fond", "type": "color"},
                     {"key": "text_up_font_border_size", "label": "Épaisseur contour", "type": "size"},
                     {"key": "text_up_font_border_color", "label": "Couleur contour", "type": "color"}]),
            "calls": _component("Appels en cours", "main", "#div_calling",
                config=[{"key": "announce_call_text", "label": "Format", "type": "text"}],
                css=[{"key": "calling_font_size", "label": "Taille", "type": "size"},
                     {"key": "calling_font_color", "label": "Couleur", "type": "color"},
                     {"key": "calling_background_color", "label": "Fond", "type": "color"},
                     {"key": "calling_font_border_size", "label": "Épaisseur contour", "type": "size"},
                     {"key": "calling_font_border_color", "label": "Couleur contour", "type": "color"}]),
            "empty_message": _component("Message file vide", "main", "#div_display_text_down",
                config=[{"key": "announce_text_down_patients", "label": "Texte", "type": "text"}],
                css=[{"key": "text_down_font_size", "label": "Taille", "type": "size"},
                     {"key": "text_down_font_color", "label": "Couleur", "type": "color"},
                     {"key": "text_down_background_color", "label": "Fond", "type": "color"},
                     {"key": "text_down_font_border_size", "label": "Épaisseur contour", "type": "size"},
                     {"key": "text_down_font_border_color", "label": "Couleur contour", "type": "color"}]),
            "ongoing": _component("Patients au comptoir", "main", "#div_ongoing",
                config=[{"key": "announce_ongoing_text", "label": "Format", "type": "text"}],
                css=[{"key": "ongoing_font_size", "label": "Taille", "type": "size"},
                     {"key": "ongoing_font_color", "label": "Couleur", "type": "color"},
                     {"key": "ongoing_background_color", "label": "Fond", "type": "color"},
                     {"key": "ongoing_font_border_size", "label": "Épaisseur contour", "type": "size"},
                     {"key": "ongoing_font_border_color", "label": "Couleur contour", "type": "color"}]),
            "gallery": _component("Galerie", "aside", "#div_pub", span=6,
                config=[{"key": "announce_infos_display", "label": "Afficher", "type": "bool"}]),
            "next": _component("Prochains patients", "footer", "#div_next_patients",
                config=[{"key": "announce_next_patients_text", "label": "Texte", "type": "text"}],
                css=[{"key": "next_patients_font_size", "label": "Taille", "type": "size"},
                     {"key": "next_patients_font_color", "label": "Couleur", "type": "color"},
                     {"key": "next_patients_background_color", "label": "Fond", "type": "color"},
                     {"key": "next_patients_font_border_size", "label": "Épaisseur contour", "type": "size"},
                     {"key": "next_patients_font_border_color", "label": "Couleur contour", "type": "color"}]),
        },
    },
    "patient": {
        "label": "Borne patient",
        "permission": "patient",
        "layout_key": "page_patient_layout",
        "config_name": "PAGE_PATIENT_LAYOUT",
        "css_source": "patient",
        "zones": ["header", "main", "footer", "overlay"],
        "viewports": [{"id": "kiosk", "label": "1280 × 800", "width": 1280, "height": 800}],
        "scenarios": [
            {"id": "home", "label": "Accueil"},
            {"id": "children", "label": "Sous-activités"},
            {"id": "validation", "label": "Validation QR / impression"},
            {"id": "conclusion", "label": "Conclusion"},
        ],
        "components": {
            "title": _component("Titre", "header", "#div_title_area",
                config=[{"key": "page_patient_title", "label": "Texte", "type": "text"}],
                css=[{"key": "patient_title_font_size", "label": "Taille", "type": "size"},
                     {"key": "patient_title_font_color", "label": "Couleur", "type": "color"},
                     {"key": "patient_title_background_color", "label": "Fond", "type": "color"},
                     {"key": "patient_title_background_height", "label": "Hauteur", "type": "size"},
                     {"key": "patient_title_border_size", "label": "Épaisseur contour", "type": "size"},
                     {"key": "patient_title_border_color", "label": "Couleur contour", "type": "color"}]),
            "buttons": _component("Boutons d'activité", "main", "#div_buttons_parents",
                css=[{"key": "circle_button_size", "label": "Bouton rond", "type": "size"},
                     {"key": "square_button_width", "label": "Largeur bouton", "type": "size"},
                     {"key": "square_button_height", "label": "Hauteur bouton", "type": "size"},
                     {"key": "square_button_color", "label": "Couleur bouton", "type": "color"},
                     {"key": "square_button_border_size", "label": "Épaisseur bordure", "type": "size"},
                     {"key": "square_button_border_color", "label": "Couleur bordure", "type": "color"}]),
            "subtitle": _component("Sous-titre / état", "footer", "#div_buttons_children",
                config=[{"key": "page_patient_subtitle", "label": "Texte", "type": "text"}],
                css=[{"key": "subtitle_font_size", "label": "Taille", "type": "size"},
                     {"key": "subtitle_font_color", "label": "Couleur", "type": "color"},
                     {"key": "subtitle_background_color", "label": "Fond", "type": "color"},
                     {"key": "subtitle_background_height", "label": "Hauteur", "type": "size"},
                     {"key": "subtitle_border_size", "label": "Épaisseur contour", "type": "size"},
                     {"key": "subtitle_border_color", "label": "Couleur contour", "type": "color"}]),
            "languages": _component("Choix de langue", "overlay", ".language-selector",
                config=[{"key": "page_patient_display_translations", "label": "Afficher", "type": "bool"}],
                css=[{"key": "flag_size", "label": "Taille drapeau", "type": "size"}]),
        },
    },
    "phone": {
        "label": "Téléphone patient",
        "permission": "phone",
        "layout_key": "phone_layout",
        "config_name": "PHONE_LAYOUT",
        "css_source": "phone",
        "zones": ["header", "main", "footer"],
        "viewports": [
            {"id": "mobile", "label": "390 × 844", "width": 390, "height": 844},
            {"id": "compact", "label": "360 × 800", "width": 360, "height": 800},
        ],
        "scenarios": [
            {"id": "waiting", "label": "Attente"},
            {"id": "confirmation", "label": "Confirmation"},
            {"id": "your-turn", "label": "Appel du patient"},
        ],
        "components": {},
    },
}


for index in range(1, 7):
    ADAPTERS["phone"]["components"][f"line{index}"] = _component(
        f"Ligne {index}", "main", f"#phone_line{index}",
        config=[{"key": f"phone_line{index}", "label": "Texte Markdown", "type": "text"}],
        css=[{"key": f"phone_line{index}_font_size", "label": "Taille", "type": "size"},
             {"key": f"phone_line{index}_font_color", "label": "Couleur", "type": "color"},
             {"key": f"phone_line{index}_font_weight", "label": "Graisse", "type": "number"},
             {"key": f"phone_line{index}_background_color", "label": "Fond", "type": "color"},
             {"key": f"phone_line{index}_background_height", "label": "Hauteur", "type": "size"},
             {"key": f"phone_line{index}_border_size", "label": "Épaisseur bordure", "type": "size"},
             {"key": f"phone_line{index}_border_color", "label": "Couleur bordure", "type": "color"}],
    )
    ADAPTERS["phone"]["components"][f"your_turn_line{index}"] = _component(
        f"Appel — ligne {index}", "main", f"#phone_your_turn_line{index}",
        config=[{"key": f"phone_your_turn_line{index}", "label": "Texte Markdown", "type": "text"}],
        css=[{"key": f"phone_your_turn_line{index}_font_size", "label": "Taille", "type": "size"},
             {"key": f"phone_your_turn_line{index}_font_color", "label": "Couleur", "type": "color"},
             {"key": f"phone_your_turn_line{index}_font_weight", "label": "Graisse", "type": "number"},
             {"key": f"phone_your_turn_line{index}_background_color", "label": "Fond", "type": "color"},
             {"key": f"phone_your_turn_line{index}_background_height", "label": "Hauteur", "type": "size"},
             {"key": f"phone_your_turn_line{index}_border_size", "label": "Épaisseur bordure", "type": "size"},
             {"key": f"phone_your_turn_line{index}_border_color", "label": "Couleur bordure", "type": "color"}],
    )

ADAPTERS["phone"]["components"]["title"] = _component(
    "Titre", "header", "#div_title_area",
    config=[{"key": "phone_title", "label": "Texte", "type": "text"}],
    css=[{"key": "phone_title_font_size", "label": "Taille", "type": "size"},
         {"key": "phone_title_font_color", "label": "Couleur", "type": "color"},
         {"key": "phone_title_font_weight", "label": "Graisse", "type": "number"},
         {"key": "phone_title_background_color", "label": "Fond", "type": "color"},
         {"key": "phone_title_background_height", "label": "Hauteur", "type": "size"},
         {"key": "phone_title_border_size", "label": "Épaisseur bordure", "type": "size"},
         {"key": "phone_title_border_color", "label": "Couleur bordure", "type": "color"}],
)
ADAPTERS["phone"]["components"]["specific"] = _component(
    "Message spécifique", "footer", "#specific_message",
    css=[{"key": "phone_specific_message_font_size", "label": "Taille", "type": "size"},
         {"key": "phone_specific_message_font_color", "label": "Couleur", "type": "color"},
         {"key": "phone_specific_message_background_color", "label": "Fond", "type": "color"},
         {"key": "phone_specific_message_background_height", "label": "Hauteur", "type": "size"},
         {"key": "phone_specific_message_border_size", "label": "Épaisseur bordure", "type": "size"},
         {"key": "phone_specific_message_border_color", "label": "Couleur bordure", "type": "color"}],
)


def enabled_pages():
    raw = current_app.config.get("PAGE_EDITOR_ENABLED_PAGES", "announce")
    if isinstance(raw, str):
        pages = {item.strip() for item in raw.split(",") if item.strip()}
    else:
        pages = set(raw or [])
    return pages & set(ADAPTERS)


def get_adapter(page):
    adapter = ADAPTERS.get(page)
    if adapter is None or page not in enabled_pages():
        return None
    return adapter


def default_layout(page):
    adapter = ADAPTERS[page]
    counters = {zone: 0 for zone in adapter["zones"]}
    layout = {}
    for component_id, component in adapter["components"].items():
        zone = component["zone"]
        counters[zone] += 10
        layout[component_id] = {
            "zone": zone,
            "order": counters[zone],
            "visible": True,
            "span": component.get("span", 12),
            "alignment": "stretch",
        }
    return layout


def _managed_keys(adapter, field):
    return {
        item["key"]
        for component in adapter["components"].values()
        for item in component[field]
    }


def _css_field_types(adapter):
    return {
        item["key"]: item["type"]
        for component in adapter["components"].values()
        for item in component["css"]
    }


def _normalize_css_value(value, field_type):
    if isinstance(value, str) and field_type == "color" and re.fullmatch(r"[0-9A-Fa-f]{3,8}", value):
        return f"#{value}"
    return value


def current_payload(page):
    adapter = ADAPTERS[page]
    config_values = {}
    for key in _managed_keys(adapter, "config"):
        spec = get_spec(key)
        config_values[key] = current_app.config.get(spec.config_name) if spec else None
    css_types = _css_field_types(adapter)
    css_values = {
        key: _normalize_css_value(
            current_app.css_variable_manager.get_variable(adapter["css_source"], key),
            css_types[key],
        )
        for key in _managed_keys(adapter, "css")
    }
    layout = default_layout(page)
    configured_layout = current_app.config.get(adapter["config_name"])
    if isinstance(configured_layout, dict):
        for component_id, item in configured_layout.items():
            if component_id in layout and isinstance(item, dict):
                layout[component_id].update({
                    key: value for key, value in item.items() if key in _LAYOUT_KEYS
                })
    payload = {
        "schema_version": SCHEMA_VERSION,
        "page": page,
        "layout": deepcopy(layout),
        "config": config_values,
        "css": css_values,
    }
    payload["base_hash"] = payload_hash(payload)
    return payload


def payload_hash(payload):
    canonical = {
        "schema_version": payload.get("schema_version"),
        "page": payload.get("page"),
        "layout": payload.get("layout", {}),
        "config": payload.get("config", {}),
        "css": payload.get("css", {}),
    }
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_payload(page, payload):
    adapter = ADAPTERS[page]
    if not isinstance(payload, dict):
        raise ValueError("Le brouillon doit être un objet JSON.")
    if set(payload) != _PAYLOAD_KEYS:
        raise ValueError("Le schéma du brouillon contient des clés inconnues ou manquantes.")
    if not isinstance(payload.get("base_hash"), str) or not _HASH_RE.fullmatch(payload["base_hash"]):
        raise ValueError("Empreinte de base invalide.")
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("page") != page:
        raise ValueError("Version ou page du brouillon invalide.")

    allowed_config = _managed_keys(adapter, "config")
    config_values = payload.get("config")
    if not isinstance(config_values, dict) or set(config_values) != allowed_config:
        raise ValueError("Paramètre de contenu inconnu.")
    normalized_config = {}
    for key, value in config_values.items():
        spec = get_spec(key)
        if spec is None:
            raise ValueError("Paramètre de contenu inconnu.")
        if spec.value_type == "value_bool":
            if not isinstance(value, bool):
                raise ValueError(f"Valeur booléenne invalide pour {key}.")
        elif spec.value_type == "value_int":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"Valeur numérique invalide pour {key}.")
        else:
            if not isinstance(value, str) or len(value) > 6000:
                raise ValueError(f"Texte invalide pour {key}.")
            if "<" in value or ">" in value:
                raise ValueError(f"Le HTML n'est pas autorisé pour {key}.")
            check = validate_config_text(key, value)
            if not check["success"]:
                raise ValueError(str(check["value"]))
            value = check["value"]
        if spec.allowed_values is not None and value not in spec.allowed_values:
            raise ValueError(f"Valeur non autorisée pour {key}.")
        normalized_config[key] = value

    allowed_css = _managed_keys(adapter, "css")
    css_types = _css_field_types(adapter)
    css_values = payload.get("css")
    if not isinstance(css_values, dict) or set(css_values) != allowed_css:
        raise ValueError("Variable d'apparence inconnue.")
    normalized_css = {}
    for key, value in css_values.items():
        value = _normalize_css_value(value, css_types[key])
        if not is_safe_css_value(value):
            raise ValueError(f"Valeur CSS invalide pour {key}.")
        normalized_css[key] = value.strip()

    raw_layout = payload.get("layout")
    if not isinstance(raw_layout, dict) or set(raw_layout) != set(adapter["components"]):
        raise ValueError("La structure des composants est incomplète.")
    normalized_layout = {}
    for component_id, raw in raw_layout.items():
        component = adapter["components"][component_id]
        if not isinstance(raw, dict) or set(raw) != _LAYOUT_KEYS:
            raise ValueError("Structure de composant invalide.")
        zone = raw.get("zone")
        if zone not in component["zones"]:
            raise ValueError(f"Zone invalide pour {component_id}.")
        order = raw.get("order")
        span = raw.get("span")
        visible = raw.get("visible")
        alignment = raw.get("alignment")
        if not isinstance(order, int) or not 0 <= order <= 1000:
            raise ValueError(f"Ordre invalide pour {component_id}.")
        if not isinstance(span, int) or not 1 <= span <= 12:
            raise ValueError(f"Largeur invalide pour {component_id}.")
        if not isinstance(visible, bool) or alignment not in ALIGNMENTS:
            raise ValueError(f"Présentation invalide pour {component_id}.")
        normalized_layout[component_id] = {
            "zone": zone,
            "order": order,
            "visible": visible,
            "span": span,
            "alignment": alignment,
        }

    result = {
        "schema_version": SCHEMA_VERSION,
        "page": page,
        "base_hash": str(payload.get("base_hash") or ""),
        "layout": normalized_layout,
        "config": normalized_config,
        "css": normalized_css,
    }
    return result


def layout_style(page, layout=None):
    adapter = ADAPTERS.get(page)
    if adapter is None:
        return Markup("")
    defaults = default_layout(page)
    layout = layout or current_app.config.get(adapter["config_name"]) or defaults
    if not isinstance(layout, dict):
        layout = defaults
    rules = []
    for component_id, component in adapter["components"].items():
        item = layout.get(component_id, {})
        if not isinstance(item, dict):
            item = defaults[component_id]
        selector = component["selector"]
        if not item.get("visible", True):
            rules.append(f"{selector}{{display:none!important}}")
            continue
        try:
            order = max(0, min(1000, int(item.get("order", defaults[component_id]["order"]))))
            span = max(1, min(12, int(item.get("span", defaults[component_id]["span"]))))
        except (TypeError, ValueError):
            order = defaults[component_id]["order"]
            span = defaults[component_id]["span"]
        alignment = item.get("alignment", "stretch")
        if alignment not in ALIGNMENTS:
            alignment = defaults[component_id]["alignment"]
        text_align = "center" if alignment == "stretch" else alignment
        flex_alignment = {
            "left": "flex-start",
            "center": "center",
            "right": "flex-end",
            "stretch": "stretch",
        }.get(alignment, "stretch")
        rules.append(
            f"{selector}{{order:{order};width:{span / 12 * 100:.4f}%;"
            f"align-self:{flex_alignment};"
            f"text-align:{text_align};box-sizing:border-box}}"
        )
    if page == "announce":
        rules.append("#div_title_area,#left_side,#div_footer{display:flex;flex-flow:row wrap;align-content:flex-start;align-items:stretch}")
    elif page == "patient":
        rules.append("#main{display:flex;flex-flow:row wrap;align-content:flex-start;align-items:stretch}")
    elif page == "phone":
        rules.append(".container,#div_infos{display:flex;flex-flow:row wrap;align-content:flex-start;align-items:stretch}")
    return Markup("<style data-page-editor-layout>" + "".join(rules) + "</style>")


def public_adapter_data(page):
    adapter = ADAPTERS.get(page)
    if adapter is None:
        return None
    return {
        "page": page,
        "label": adapter["label"],
        "zones": adapter["zones"],
        "viewports": adapter["viewports"],
        "scenarios": adapter["scenarios"],
        "components": adapter["components"],
        "default_layout": default_layout(page),
    }
