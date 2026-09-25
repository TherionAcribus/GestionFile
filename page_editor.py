from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy

from flask import current_app
from markupsafe import Markup

from params_registry import BALISE_LETTERS, get_spec
from utils import validate_config_text
from variables import is_safe_css_value


SCHEMA_VERSION = 1
ALIGNMENTS = frozenset({"left", "center", "right", "stretch"})
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_PAYLOAD_KEYS = frozenset({"schema_version", "page", "base_hash", "layout", "config", "css"})
_LAYOUT_KEYS = frozenset({"zone", "order", "visible", "span", "alignment"})
_PALETTE_ROLES = (
    {
        "id": "primary",
        "label": "Couleur principale",
        "description": "Fonds, bandeaux et boutons principaux",
    },
    {
        "id": "secondary",
        "label": "Couleur secondaire",
        "description": "Textes et éléments de contraste",
    },
    {
        "id": "border",
        "label": "Couleur des bordures",
        "description": "Contours et séparateurs de la page",
    },
)
_MARKER_LABELS = {
    "P": ("Pharmacie", "Nom de la pharmacie"),
    "N": ("Patient", "Numéro d’appel du patient"),
    "A": ("Activité", "Activité choisie par le patient"),
    "M": ("Équipe", "Nom du membre de l’équipe"),
    "C": ("Comptoir", "Nom du comptoir"),
    "D": ("Date", "Date du jour"),
    "H": ("Heure", "Heure actuelle"),
}


def _component(label, zone, selector, *, config=None, css=None, span=12,
               scenarios=None, managed_bool=None, hidden_when=None):
    return {
        "label": label,
        "zone": zone,
        "zones": [zone],
        "selector": selector,
        "span": span,
        "scenarios": scenarios,
        "managed_bool": managed_bool,
        "hidden_when": hidden_when,
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
                hidden_when={"key": "announce_text_up_patients_display", "values": ["never"]},
                config=[{"key": "announce_text_up_patients", "label": "Texte", "type": "text"},
                        {"key": "announce_text_up_patients_display", "label": "Mode d'affichage", "type": "select",
                         "choices": [["always", "Toujours affiché"],
                                     ["empty", "Affiché si liste vide"],
                                     ["full", "Affiché si patient(s)"],
                                     ["never", "Jamais affiché"]]}],
                css=[{"key": "text_up_font_size", "label": "Taille", "type": "size"},
                     {"key": "text_up_font_color", "label": "Couleur", "type": "color"},
                     {"key": "text_up_background_color", "label": "Fond", "type": "color"},
                     {"key": "text_up_font_border_size", "label": "Épaisseur contour", "type": "size"},
                     {"key": "text_up_font_border_color", "label": "Couleur contour", "type": "color"}]),
            "calls": _component("Appels en cours", "main", "#div_calling",
                scenarios=["active", "multiple", "gallery"],
                config=[{"key": "announce_call_text", "label": "Format", "type": "text"}],
                css=[{"key": "calling_font_size", "label": "Taille", "type": "size"},
                     {"key": "calling_font_color", "label": "Couleur", "type": "color"},
                     {"key": "calling_background_color", "label": "Fond", "type": "color"},
                     {"key": "calling_font_border_size", "label": "Épaisseur contour", "type": "size"},
                     {"key": "calling_font_border_color", "label": "Couleur contour", "type": "color"}]),
            "empty_message": _component("Message file vide", "main", "#div_display_text_down",
                scenarios=["empty"],
                hidden_when={"key": "announce_text_down_patients_display", "values": ["never"]},
                config=[{"key": "announce_text_down_patients", "label": "Texte", "type": "text"},
                        {"key": "announce_text_down_patients_display", "label": "Mode d'affichage", "type": "select",
                         "choices": [["empty", "Affiché si liste vide"],
                                     ["never", "Jamais affiché"]]}],
                css=[{"key": "text_down_font_size", "label": "Taille", "type": "size"},
                     {"key": "text_down_font_color", "label": "Couleur", "type": "color"},
                     {"key": "text_down_background_color", "label": "Fond", "type": "color"},
                     {"key": "text_down_font_border_size", "label": "Épaisseur contour", "type": "size"},
                     {"key": "text_down_font_border_color", "label": "Couleur contour", "type": "color"}]),
            "ongoing": _component("Patients au comptoir", "main", "#div_ongoing",
                managed_bool="announce_ongoing_display",
                config=[{"key": "announce_ongoing_display", "label": "Afficher les patients au comptoir", "type": "bool"},
                        {"key": "announce_ongoing_text", "label": "Format", "type": "text"}],
                css=[{"key": "ongoing_font_size", "label": "Taille", "type": "size"},
                     {"key": "ongoing_font_color", "label": "Couleur", "type": "color"},
                     {"key": "ongoing_background_color", "label": "Fond", "type": "color"},
                     {"key": "ongoing_font_border_size", "label": "Épaisseur contour", "type": "size"},
                     {"key": "ongoing_font_border_color", "label": "Couleur contour", "type": "color"}]),
            "gallery": _component("Galerie", "aside", "#div_pub", span=6,
                scenarios=["gallery"], managed_bool="announce_infos_display",
                config=[{"key": "announce_infos_display", "label": "Afficher", "type": "bool"}]),
            "next": _component("Prochains patients", "footer", "#div_next_patients",
                managed_bool="announce_next_patients_display",
                config=[{"key": "announce_next_patients_display", "label": "Afficher la liste des prochains patients", "type": "bool"},
                        {"key": "announce_next_patients_text", "label": "Texte", "type": "text"},
                        {"key": "announce_next_patients_alignment", "label": "Alignement du texte", "type": "select",
                         "choices": [["center", "Centré"], ["left", "Gauche"]]}],
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
                config=[{"key": "page_patient_disable_button", "label": "Griser les boutons si l'activité n'est pas en cours", "type": "bool"},
                        {"key": "page_patient_direct_print", "label": "Imprimer directement le ticket (sans écran de validation)", "type": "bool"},
                        {"key": "page_patient_display_button_scan", "label": "Afficher le bouton « Scanner et valider »", "type": "bool"},
                        {"key": "page_patient_display_scan_explanation", "label": "Afficher les explications pour scanner le QR-Code", "type": "bool"},
                        {"key": "page_patient_print_after_scan", "label": "Page de réimpression après « Scan »", "type": "bool"},
                        {"key": "page_patient_print_after_print", "label": "Page de réimpression après « Print »", "type": "bool"},
                        {"key": "page_patient_end_timer", "label": "Délai avant retour à l'accueil (s)", "type": "int"}],
                css=[{"key": "circle_button_size", "label": "Bouton rond", "type": "size"},
                     {"key": "square_button_width", "label": "Largeur bouton", "type": "size"},
                     {"key": "square_button_height", "label": "Hauteur bouton", "type": "size"},
                     {"key": "square_button_color", "label": "Couleur bouton", "type": "color"},
                     {"key": "square_button_border_size", "label": "Épaisseur bordure", "type": "size"},
                     {"key": "square_button_border_color", "label": "Couleur bordure", "type": "color"}]),
            "subtitle": _component("Sous-titre / état", "footer", "#div_buttons_children",
                config=[{"key": "page_patient_subtitle", "label": "Texte", "type": "text"},
                        {"key": "page_patient_disable_default_message", "label": "Texte si l'activité n'est pas en cours", "type": "text"},
                        {"key": "page_patient_display_specific_message", "label": "Message spécifique d'activité dans le pied de page", "type": "bool"},
                        {"key": "page_patient_timer_activity_inactive", "label": "Durée du message « indisponible » (s)", "type": "int"}],
                css=[{"key": "subtitle_font_size", "label": "Taille", "type": "size"},
                     {"key": "subtitle_font_color", "label": "Couleur", "type": "color"},
                     {"key": "subtitle_background_color", "label": "Fond", "type": "color"},
                     {"key": "subtitle_background_height", "label": "Hauteur", "type": "size"},
                     {"key": "subtitle_border_size", "label": "Épaisseur contour", "type": "size"},
                     {"key": "subtitle_border_color", "label": "Couleur contour", "type": "color"}]),
            "languages": _component("Choix de langue", "overlay", ".language-selector",
                managed_bool="page_patient_display_translations",
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
        f"Ligne {index}", "main", f"#phone_line{index}", scenarios=["confirmation"],
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
        scenarios=["your-turn"],
        managed_bool="phone_display_your_turn",
        config=(
            [{"key": "phone_display_your_turn", "label": "Afficher l'écran « Votre tour »", "type": "bool"}]
            if index == 1 else []
        ) + [{"key": f"phone_your_turn_line{index}", "label": "Texte Markdown", "type": "text"}],
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
    config=[{"key": "phone_title", "label": "Texte", "type": "text"},
            {"key": "phone_center", "label": "Centrer le contenu", "type": "bool"}],
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
    managed_bool="phone_display_specific_message",
    config=[{"key": "phone_display_specific_message", "label": "Afficher le message spécifique", "type": "bool"}],
    css=[{"key": "phone_specific_message_font_size", "label": "Taille", "type": "size"},
         {"key": "phone_specific_message_font_color", "label": "Couleur", "type": "color"},
         {"key": "phone_specific_message_background_color", "label": "Fond", "type": "color"},
         {"key": "phone_specific_message_background_height", "label": "Hauteur", "type": "size"},
         {"key": "phone_specific_message_border_size", "label": "Épaisseur bordure", "type": "size"},
         {"key": "phone_specific_message_border_color", "label": "Couleur bordure", "type": "color"}],
)


_ADDITIONAL_CSS_FIELDS = {
    "announce": {"title": [
        ("announce_secondary_color", "Fond de page", "color", "#B6F5F5"),
    ]},
    "phone": {"title": [
        ("phone_secondary_color", "Fond de page", "color", "#B6F5F5"),
    ]},
    "patient": {
        "buttons": [
            ("patient_secondary_color", "Fond de page", "color", "#B6F5F5"),
            ("square_button_text_color", "Texte des boutons", "color", "#FFFFFF"),
            ("square_button_text_size", "Taille du texte des boutons", "size", "30px"),
            ("square_button_text_border_size", "Contour du texte des boutons", "size", "0px"),
            ("square_button_image_size", "Taille de l'image", "size", "200px"),
            ("circle_button_color", "Fond des boutons ronds", "color", "#008B8B"),
            ("circle_button_text_color", "Texte sous les boutons ronds", "color", "#008B8B"),
            ("circle_button_text_size", "Taille du texte sous les boutons ronds", "size", "30px"),
            ("circle_button_text_border_size", "Contour du texte sous les boutons ronds", "size", "0px"),
            ("circle_button_border_size", "Bordure des boutons ronds", "size", "0px"),
            ("circle_button_border_color", "Couleur bordure des boutons ronds", "color", "#000000"),
            ("circle_button_image_size", "Taille de l'image des boutons ronds", "size", "200px"),
            ("square_cancel_button_width", "Largeur du bouton retour", "size", "500px"),
            ("square_cancel_button_height", "Hauteur du bouton retour", "size", "150px"),
            ("square_cancel_button_color", "Fond du bouton retour", "color", "#008B8B"),
            ("square_cancel_button_border_size", "Bordure du bouton retour", "size", "0px"),
            ("square_cancel_button_border_color", "Couleur bordure retour", "color", "#000000"),
            ("square_cancel_button_text_color", "Texte du bouton retour", "color", "#FFFFFF"),
            ("square_cancel_button_text_size", "Taille du texte retour", "size", "30px"),
            ("square_cancel_button_text_border_size", "Contour du texte retour", "size", "0px"),
            ("square_cancel_button_image_size", "Taille de l'image du bouton retour", "size", "200px"),
            ("validation_button_width", "Largeur des boutons de validation", "size", "500px"),
            ("validation_button_height", "Hauteur des boutons de validation", "size", "150px"),
            ("validation_button_color", "Fond des boutons de validation", "color", "#008B8B"),
            ("validation_button_border_size", "Bordure des boutons de validation", "size", "0px"),
            ("validation_button_border_color", "Couleur bordure validation", "color", "#000000"),
            ("validation_button_text_color", "Texte des boutons de validation", "color", "#FFFFFF"),
            ("validation_button_text_size", "Taille du texte validation", "size", "40px"),
            ("validation_button_text_border_size", "Contour du texte validation", "size", "0px"),
            ("validation_button_picture_size", "Taille de l'image validation", "size", "200px"),
            ("validation_text_font_color", "Texte du numéro", "color", "#008B8B"),
            ("validation_text_font_size", "Taille du numéro", "size", "50px"),
            ("validation_text_border_size", "Contour du numéro", "size", "0px"),
            ("validation_text_border_color", "Couleur contour du numéro", "color", "#000000"),
            ("validation_text_background_color", "Fond du texte de validation", "color", "#B6F5F5"),
            ("confirmation_text_font_color", "Texte de confirmation", "color", "#008B8B"),
            ("confirmation_text_font_size", "Taille de confirmation", "size", "50px"),
            ("confirmation_text_border_size", "Contour de confirmation", "size", "0px"),
            ("confirmation_text_border_color", "Couleur contour confirmation", "color", "#000000"),
            ("confirmation_text_background_color", "Fond de confirmation", "color", "#B6F5F5"),
            ("scan_explanation_font_color", "Texte des consignes QR", "color", "#008B8B"),
            ("scan_explanation_font_size", "Taille des consignes QR", "size", "50px"),
            ("scan_explanation_border_size", "Contour des consignes QR", "size", "0px"),
            ("scan_explanation_border_color", "Couleur contour des consignes QR", "color", "#000000"),
            ("scan_explanation_background_color", "Fond des consignes QR", "color", "#B6F5F5"),
        ],
        "subtitle": [
            ("subtitle_no_activity_font_color", "Texte sans activité", "color", "#FFFFFF"),
            ("subtitle_no_activity_font_size", "Taille sans activité", "size", "40px"),
            ("subtitle_no_activity_border_size", "Contour sans activité", "size", "0px"),
            ("subtitle_no_activity_border_color", "Couleur contour sans activité", "color", "#000000"),
            ("subtitle_no_activity_background_color", "Fond sans activité", "color", "#008B8B"),
            ("subtitle_specific_message_font_color", "Texte du message spécifique", "color", "#FFFFFF"),
            ("subtitle_specific_message_font_size", "Taille du message spécifique", "size", "40px"),
            ("subtitle_specific_message_border_size", "Contour du message spécifique", "size", "0px"),
            ("subtitle_specific_message_border_color", "Couleur contour message spécifique", "color", "#000000"),
            ("subtitle_specific_message_background_color", "Fond du message spécifique", "color", "#008B8B"),
        ],
        "languages": [
            ("flag_border_size", "Contour des drapeaux", "size", "0px"),
            ("flag_border_color", "Couleur contour des drapeaux", "color", "#FFFFFF"),
        ],
    },
}

for _page, _components in _ADDITIONAL_CSS_FIELDS.items():
    for _component_id, _fields in _components.items():
        ADAPTERS[_page]["components"][_component_id]["css"].extend(
            {"key": key, "label": label, "type": field_type, "default": default, "optional": True}
            for key, label, field_type, default in _fields
        )


_BUILTIN_THEMES = (
    ("officine", "Officine", "Clair et rassurant — recommandé pour commencer.",
     ("#F5F8F7", "#FFFFFF", "#18332F", "#176B52", "#E5F2EC", "#FFFFFF")),
    ("lisibilite", "Lisibilité renforcée", "Textes agrandis ; galerie masquée sur l’affichage si vous appliquez la disposition.",
     ("#FFFFFF", "#FFFFFF", "#111827", "#123B63", "#FFF1B8", "#FFFFFF")),
    ("sauge-lin", "Sauge & Lin", "Des tons naturels et chaleureux, sans sacrifier la lisibilité.",
     ("#FAF7F0", "#FFFEFA", "#29372F", "#365C46", "#E8EDE3", "#FFFFFF")),
    ("bleu-horizon", "Bleu Horizon", "Une présentation claire et structurée, dans des tons bleus.",
     ("#F3F6FA", "#FFFFFF", "#172B4D", "#245EA8", "#E7EFFA", "#FFFFFF")),
    ("ardoise", "Ardoise", "Variante sombre — à tester sur place selon l’éclairage et les reflets.",
     ("#17212B", "#243342", "#F3F6FA", "#7EDDB5", "#243342", "#17212B")),
    ("classique", "Classique", "Reprend les réglages actuels de l’installation — grands textes blancs sur bandeaux vert canard.",
     ("#B6F5F5", "#008B8B", "#FFFFFF", "#006666", "#5FB4B4", "#FFFFFF")),
)

#: Surcharges exactes du thème « Classique », page par page : il reproduit la
#: configuration réellement en service, contrairement aux autres thèmes dont
#: les valeurs sont dérivées de leur palette.
_BUILTIN_CSS_OVERRIDES = {
    "classique": {
        "announce": {
            "announce_secondary_color": "#B6F5F5",
            "title_font_color": "#FFFFFF",
            "title_font_size": "90px",
            "title_font_border_size": "2px",
            "title_font_border_color": "#000000",
            "title_background_color": "#006666",
            "title_background_height": "200px",
            "subtitle_font_color": "#FFFFFF",
            "subtitle_font_size": "75px",
            "subtitle_font_border_size": "0px",
            "subtitle_font_border_color": "#000000",
            "text_up_background_color": "#008B8B",
            "text_up_font_color": "#FFFFFF",
            "text_up_font_size": "75px",
            "text_up_font_border_size": "0px",
            "text_up_font_border_color": "#000000",
            "calling_font_size": "140px",
            "calling_font_color": "#FFFFFF",
            "calling_font_border_size": "2px",
            "calling_font_border_color": "#000000",
            "calling_background_color": "#008B8B",
            "text_down_background_color": "#5FB4B4",
            "text_down_font_color": "#FFFFFF",
            "text_down_font_size": "150px",
            "text_down_font_border_size": "2px",
            "text_down_font_border_color": "#000000",
            "ongoing_background_color": "#008B8B",
            "ongoing_font_color": "#FFFFFF",
            "ongoing_font_size": "75px",
            "ongoing_font_border_size": "0px",
            "ongoing_font_border_color": "#000000",
            "next_patients_font_color": "#FFFFFF",
            "next_patients_font_size": "90px",
            "next_patients_font_border_size": "2px",
            "next_patients_font_border_color": "#000000",
            "next_patients_background_color": "#006666",
        },
        "patient": {
            "patient_secondary_color": "#B6F5F5",
            "patient_title_font_size": "80px",
            "patient_title_font_color": "#FFFFFF",
            "patient_title_border_size": "2px",
            "patient_title_border_color": "#000000",
            "patient_title_background_color": "#008B8B",
            "patient_title_background_height": "150px",
            "circle_button_size": "200px",
            "circle_button_color": "#008B8B",
            "circle_button_border_size": "0px",
            "circle_button_border_color": "#000000",
            "circle_button_image_size": "200px",
            "circle_button_text_color": "#008B8B",
            "circle_button_text_size": "30px",
            "circle_button_text_border_size": "0px",
            "square_button_width": "500px",
            "square_button_height": "150px",
            "square_button_color": "#008B8B",
            "square_button_border_size": "0px",
            "square_button_border_color": "#000000",
            "square_button_image_size": "200px",
            "square_button_text_color": "#FFFFFF",
            "square_button_text_size": "30px",
            "square_button_text_border_size": "0px",
            "square_cancel_button_width": "500px",
            "square_cancel_button_height": "150px",
            "square_cancel_button_color": "#008B8B",
            "square_cancel_button_border_size": "0px",
            "square_cancel_button_border_color": "#000000",
            "square_cancel_button_image_size": "200px",
            "square_cancel_button_text_color": "#FFFFFF",
            "square_cancel_button_text_size": "30px",
            "square_cancel_button_text_border_size": "0px",
            "validation_button_width": "500px",
            "validation_button_height": "150px",
            "validation_button_color": "#008B8B",
            "validation_button_border_size": "0px",
            "validation_button_border_color": "#000000",
            "validation_button_text_color": "#FFFFFF",
            "validation_button_text_size": "40px",
            "validation_button_text_border_size": "0px",
            "validation_button_picture_size": "200px",
            "validation_text_font_color": "#008B8B",
            "validation_text_font_size": "50px",
            "validation_text_border_size": "0px",
            "validation_text_border_color": "#000000",
            "validation_text_background_color": "#B6F5F5",
            "confirmation_text_font_color": "#008B8B",
            "confirmation_text_font_size": "50px",
            "confirmation_text_border_size": "0px",
            "confirmation_text_border_color": "#000000",
            "confirmation_text_background_color": "#B6F5F5",
            "scan_explanation_font_color": "#008B8B",
            "scan_explanation_font_size": "50px",
            "scan_explanation_border_size": "0px",
            "scan_explanation_border_color": "#000000",
            "scan_explanation_background_color": "#B6F5F5",
            "flag_size": "100px",
            "flag_border_size": "0px",
            "flag_border_color": "#FFFFFF",
            "subtitle_font_size": "60px",
            "subtitle_font_color": "#FFFFFF",
            "subtitle_border_size": "2px",
            "subtitle_border_color": "#000000",
            "subtitle_background_color": "#008B8B",
            "subtitle_background_height": "150px",
            "subtitle_no_activity_font_color": "#FFFFFF",
            "subtitle_no_activity_font_size": "40px",
            "subtitle_no_activity_border_size": "0px",
            "subtitle_no_activity_border_color": "#000000",
            "subtitle_no_activity_background_color": "#008B8B",
            "subtitle_specific_message_font_color": "#FFFFFF",
            "subtitle_specific_message_font_size": "40px",
            "subtitle_specific_message_border_size": "0px",
            "subtitle_specific_message_border_color": "#000000",
            "subtitle_specific_message_background_color": "#008B8B",
        },
        "phone": {
            "phone_secondary_color": "#B6F5F5",
            "phone_title_font_size": "20px",
            "phone_title_font_color": "#FFFFFF",
            "phone_title_font_weight": "800",
            "phone_title_border_size": "0px",
            "phone_title_border_color": "#000000",
            "phone_title_background_color": "#008B8B",
            "phone_title_background_height": "10px",
            "phone_line1_font_size": "30px",
            "phone_line1_font_weight": "400",
            "phone_line1_font_color": "#000000",
            "phone_line1_background_color": "#B6F5F5",
            "phone_line1_border_size": "0px",
            "phone_line1_border_color": "#000000",
            "phone_line2_font_size": "40px",
            "phone_line2_font_weight": "800",
            "phone_line2_font_color": "#000000",
            "phone_line2_background_color": "#B6F5F5",
            "phone_line2_border_size": "0px",
            "phone_line2_border_color": "#000000",
            "phone_line3_font_size": "30px",
            "phone_line3_font_weight": "400",
            "phone_line3_font_color": "#000000",
            "phone_line3_background_color": "#B6F5F5",
            "phone_line3_border_size": "0px",
            "phone_line3_border_color": "#000000",
            "phone_line4_font_size": "30px",
            "phone_line4_font_weight": "800",
            "phone_line4_font_color": "#000000",
            "phone_line4_background_color": "#B6F5F5",
            "phone_line4_border_size": "0px",
            "phone_line4_border_color": "#000000",
            "phone_line5_font_size": "30px",
            "phone_line5_font_weight": "400",
            "phone_line5_font_color": "#000000",
            "phone_line5_background_color": "#B6F5F5",
            "phone_line5_border_size": "0px",
            "phone_line5_border_color": "#000000",
            "phone_line6_font_size": "30px",
            "phone_line6_font_weight": "400",
            "phone_line6_font_color": "#000000",
            "phone_line6_background_color": "#B6F5F5",
            "phone_line6_border_size": "0px",
            "phone_line6_border_color": "#000000",
            "phone_line1_background_height": "0px",
            "phone_line2_background_height": "2px",
            "phone_line3_background_height": "0px",
            "phone_line4_background_height": "0px",
            "phone_line5_background_height": "0px",
            "phone_line6_background_height": "0px",
            "phone_specific_message_font_size": "30px",
            "phone_specific_message_font_color": "#000000",
            "phone_specific_message_background_color": "#B6F5F5",
            "phone_specific_message_background_height": "50px",
            "phone_specific_message_border_size": "0px",
            "phone_specific_message_border_color": "#000000",
            "phone_your_turn_line1_font_size": "20px",
            "phone_your_turn_line1_font_weight": "400",
            "phone_your_turn_line1_font_color": "#000000",
            "phone_your_turn_line1_background_color": "#B1F1F1",
            "phone_your_turn_line1_background_height": "100px",
            "phone_your_turn_line1_border_size": "2px",
            "phone_your_turn_line1_border_color": "#000000",
            "phone_your_turn_line2_font_size": "20px",
            "phone_your_turn_line2_font_weight": "400",
            "phone_your_turn_line2_font_color": "#000000",
            "phone_your_turn_line2_background_color": "#B1F1F1",
            "phone_your_turn_line2_background_height": "100px",
            "phone_your_turn_line2_border_size": "2px",
            "phone_your_turn_line2_border_color": "#000000",
            "phone_your_turn_line3_font_size": "20px",
            "phone_your_turn_line3_font_weight": "400",
            "phone_your_turn_line3_font_color": "#000000",
            "phone_your_turn_line3_background_color": "#B1F1F1",
            "phone_your_turn_line3_background_height": "100px",
            "phone_your_turn_line3_border_size": "2px",
            "phone_your_turn_line3_border_color": "#000000",
            "phone_your_turn_line4_font_size": "20px",
            "phone_your_turn_line4_font_weight": "400",
            "phone_your_turn_line4_font_color": "#000000",
            "phone_your_turn_line4_background_color": "#B1F1F1",
            "phone_your_turn_line4_background_height": "100px",
            "phone_your_turn_line4_border_size": "2px",
            "phone_your_turn_line4_border_color": "#000000",
            "phone_your_turn_line5_font_size": "20px",
            "phone_your_turn_line5_font_weight": "400",
            "phone_your_turn_line5_font_color": "#000000",
            "phone_your_turn_line5_background_color": "#B1F1F1",
            "phone_your_turn_line5_background_height": "100px",
            "phone_your_turn_line5_border_size": "2px",
            "phone_your_turn_line5_border_color": "#000000",
            "phone_your_turn_line6_font_size": "20px",
            "phone_your_turn_line6_font_weight": "400",
            "phone_your_turn_line6_font_color": "#000000",
            "phone_your_turn_line6_background_color": "#B1F1F1",
            "phone_your_turn_line6_background_height": "100px",
            "phone_your_turn_line6_border_size": "2px",
            "phone_your_turn_line6_border_color": "#000000",
        },
    },
}


def builtin_themes(page):
    themes = []
    for slug, name, description, colors in _BUILTIN_THEMES:
        overrides = _BUILTIN_CSS_OVERRIDES.get(slug, {})
        if slug in _BUILTIN_CSS_OVERRIDES and page not in overrides:
            continue
        background, surface, text, primary, soft, on_primary = colors
        large = slug == "lisibilite"
        css = {}
        for key, field_type in _css_field_types(ADAPTERS[page]).items():
            if field_type == "color":
                css[key] = text if "font_color" in key or "text_color" in key or "border_color" in key else surface
            elif field_type == "number":
                css[key] = "600"
            else:
                css[key] = "0px" if "border_size" in key else "24px"

        def block(prefix, foreground, fill, size):
            css.update({f"{prefix}_font_color": foreground, f"{prefix}_background_color": fill,
                        f"{prefix}_font_size": size})

        css[f"{page}_secondary_color"] = background
        if page == "announce":
            block("title", on_primary, primary, "100px" if large else "90px")
            css.update(title_background_height="220px" if large else "200px",
                       subtitle_font_color=on_primary,
                       subtitle_font_size="85px" if large else "75px")
            block("calling", on_primary, primary, "160px" if large else "140px")
            block("text_up", text, surface, "90px" if large else "75px")
            block("text_down", text, surface, "170px" if large else "150px")
            block("ongoing", text, soft, "90px" if large else "75px")
            block("next_patients", text, soft, "100px" if large else "90px")
        elif page == "patient":
            block("patient_title", on_primary, primary, "90px" if large else "80px")
            block("subtitle", on_primary, primary, "70px" if large else "60px")
            css.update(patient_title_background_height="170px" if large else "150px",
                       subtitle_background_height="170px" if large else "150px",
                       circle_button_size="220px" if large else "200px", circle_button_color=primary,
                       circle_button_text_color=text,
                       circle_button_text_size="34px" if large else "30px",
                       circle_button_image_size="220px" if large else "200px",
                       square_button_width="540px" if large else "500px",
                       square_button_height="165px" if large else "150px", square_button_color=primary,
                       square_button_text_color=on_primary,
                       square_button_text_size="34px" if large else "30px",
                       square_button_image_size="200px",
                       square_cancel_button_width="540px" if large else "500px",
                       square_cancel_button_height="165px" if large else "150px",
                       square_cancel_button_color=primary, square_cancel_button_text_color=on_primary,
                       square_cancel_button_text_size="34px" if large else "30px",
                       square_cancel_button_image_size="200px",
                       validation_button_color=primary,
                       validation_button_text_color=on_primary,
                       validation_button_text_size="44px" if large else "40px",
                       validation_button_width="540px" if large else "500px",
                       validation_button_height="165px" if large else "150px",
                       validation_button_picture_size="200px",
                       validation_text_font_size="56px" if large else "50px",
                       confirmation_text_font_size="56px" if large else "50px",
                       scan_explanation_font_size="56px" if large else "50px",
                       subtitle_no_activity_font_size="46px" if large else "40px",
                       subtitle_specific_message_font_size="46px" if large else "40px",
                       flag_size="110px" if large else "100px")
        else:
            block("phone_title", on_primary, primary, "24px" if large else "20px")
            css.update(phone_title_font_weight="800",
                       phone_title_background_height="14px" if large else "10px")
            for prefix in ("phone_line", "phone_your_turn_line"):
                for index in range(1, 7):
                    block(f"{prefix}{index}", text, surface, "34px" if large else "30px")
                    css[f"{prefix}{index}_background_height"] = "2px"
                    css[f"{prefix}{index}_font_weight"] = "400"
            for index in (2, 4):
                css[f"phone_line{index}_font_weight"] = "800"
            css["phone_line2_font_size"] = "44px" if large else "40px"
            block("phone_your_turn_line1", on_primary, primary, "44px" if large else "40px")
            css["phone_your_turn_line1_font_weight"] = "700"
            block("phone_specific_message", text, soft, "34px" if large else "30px")
            css["phone_specific_message_background_height"] = "60px" if large else "50px"

        css.update(overrides.get(page, {}))

        layout = default_layout(page)
        for item in layout.values():
            item.pop("visible")
            item.update(span=12, alignment="center")
        if page == "announce" and large:
            layout["gallery"]["visible"] = False
        themes.append({
            "id": f"builtin-{slug}", "page": page, "name": name, "description": description,
            "builtin": True, "recommended": slug == "officine",
            "colors": list(colors[:5]),
            "snapshot": {"css": css, "layout": layout, "config": {}},
        })
    return themes


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


def _palette_data(adapter):
    color_keys = {
        item["key"]
        for component in adapter["components"].values()
        for item in component["css"]
        if item["type"] == "color"
    }
    keys_by_role = {
        "primary": sorted(
            key for key in color_keys
            if "background_color" in key or key == "square_button_color"
        ),
        "secondary": sorted(
            key for key in color_keys if "font_color" in key
        ),
        "border": sorted(
            key for key in color_keys if "border_color" in key
        ),
    }
    return [
        {**role, "keys": keys_by_role[role["id"]]}
        for role in _PALETTE_ROLES
        if keys_by_role[role["id"]]
    ]


def _normalize_css_value(value, field_type):
    if isinstance(value, str) and field_type == "color" and re.fullmatch(r"[0-9A-Fa-f]{3,8}", value):
        return f"#{value}"
    return value


#: Validation par type de champ CSS — en plus du filtre anti-injection
#: ``is_safe_css_value``, chaque valeur doit être une valeur CSS utilisable
#: pour sa propriété (une taille, une couleur, une graisse…).
_CSS_SIZE_RE = re.compile(
    r"^-?\d+(?:\.\d+)?(?:px|em|rem|%|vh|vw|vmin|vmax|pt|pc|cm|mm|in|ch|ex|lh|rlh)?$",
    re.IGNORECASE,
)
_CSS_FN_RE = re.compile(r"^(?:var|calc|min|max|clamp)\(.*\)$", re.IGNORECASE | re.DOTALL)
_CSS_COLOR_FN_RE = re.compile(
    r"^(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color|color-mix|var)\(.*\)$",
    re.IGNORECASE | re.DOTALL,
)
_CSS_NAMED_COLORS = frozenset({
    "aliceblue", "antiquewhite", "aqua", "aquamarine", "azure", "beige",
    "bisque", "black", "blanchedalmond", "blue", "blueviolet", "brown",
    "burlywood", "cadetblue", "chartreuse", "chocolate", "coral",
    "cornflowerblue", "cornsilk", "crimson", "cyan", "darkblue", "darkcyan",
    "darkgoldenrod", "darkgray", "darkgreen", "darkgrey", "darkkhaki",
    "darkmagenta", "darkolivegreen", "darkorange", "darkorchid", "darkred",
    "darksalmon", "darkseagreen", "darkslateblue", "darkslategray",
    "darkslategrey", "darkturquoise", "darkviolet", "deeppink",
    "deepskyblue", "dimgray", "dimgrey", "dodgerblue", "firebrick",
    "floralwhite", "forestgreen", "fuchsia", "gainsboro", "ghostwhite",
    "gold", "goldenrod", "gray", "green", "greenyellow", "grey", "honeydew",
    "hotpink", "indianred", "indigo", "ivory", "khaki", "lavender",
    "lavenderblush", "lawngreen", "lemonchiffon", "lightblue", "lightcoral",
    "lightcyan", "lightgoldenrodyellow", "lightgray", "lightgreen",
    "lightgrey", "lightpink", "lightsalmon", "lightseagreen",
    "lightskyblue", "lightslategray", "lightslategrey", "lightsteelblue",
    "lightyellow", "lime", "limegreen", "linen", "magenta", "maroon",
    "mediumaquamarine", "mediumblue", "mediumorchid", "mediumpurple",
    "mediumseagreen", "mediumslateblue", "mediumspringgreen",
    "mediumturquoise", "mediumvioletred", "midnightblue", "mintcream",
    "mistyrose", "moccasin", "navajowhite", "navy", "oldlace", "olive",
    "olivedrab", "orange", "orangered", "orchid", "palegoldenrod",
    "palegreen", "paleturquoise", "palevioletred", "papayawhip",
    "peachpuff", "peru", "pink", "plum", "powderblue", "purple",
    "rebeccapurple", "red", "rosybrown", "royalblue", "saddlebrown",
    "salmon", "sandybrown", "seagreen", "seashell", "sienna", "silver",
    "skyblue", "slateblue", "slategray", "slategrey", "snow",
    "springgreen", "steelblue", "tan", "teal", "thistle", "tomato",
    "turquoise", "violet", "wheat", "white", "whitesmoke", "yellow",
    "yellowgreen", "transparent", "currentcolor", "inherit", "initial",
    "unset", "revert",
})
_CSS_SIZE_KEYWORDS = frozenset({"auto", "inherit", "initial", "unset", "revert"})
_FONT_WEIGHT_KEYWORDS = frozenset({"normal", "bold", "bolder", "lighter"})


def _is_css_size(value):
    return bool(
        _CSS_SIZE_RE.match(value)
        or _CSS_FN_RE.match(value)
        or value.lower() in _CSS_SIZE_KEYWORDS
    )


def _is_css_color(value):
    lowered = value.lower()
    return bool(
        re.fullmatch(r"#[0-9a-f]{3,8}", lowered)
        or _CSS_COLOR_FN_RE.match(value)
        or lowered in _CSS_NAMED_COLORS
    )


def _is_css_number(value):
    lowered = value.lower()
    if lowered in _FONT_WEIGHT_KEYWORDS:
        return True
    try:
        return 1 <= int(value) <= 1000
    except ValueError:
        return False


def _is_valid_css_for_type(value, field_type):
    if field_type == "color":
        return _is_css_color(value)
    if field_type == "number":
        return _is_css_number(value)
    return _is_css_size(value)


#: Correspondance entre un composant et un réglage du mode avancé qui peut le
#: désactiver entièrement. Utilisée pour l'aperçu et les badges de l'éditeur.
_ADVANCED_DISABLED_RULES = {
    ("announce", "ongoing"): ("announce_ongoing_display", "Le bloc « patients au comptoir » est désactivé dans le mode avancé."),
    ("announce", "next"): ("announce_next_patients_display", "Le bloc « prochains patients » est désactivé dans le mode avancé."),
}
_ADVANCED_NEVER_RULES = {
    ("announce", "top_message"): ("announce_text_up_patients_display", "L'affichage de ce message est réglé sur « jamais » dans le mode avancé."),
    ("announce", "empty_message"): ("announce_text_down_patients_display", "L'affichage de ce message est réglé sur « jamais » dans le mode avancé."),
}


def advanced_disabled_reason(page, component_id):
    """Raison pour laquelle le mode avancé désactive ce composant, sinon ``None``."""
    flag = _ADVANCED_DISABLED_RULES.get((page, component_id))
    if flag is not None:
        spec = get_spec(flag[0])
        if spec and not current_app.config.get(spec.config_name, True):
            return flag[1]
    never = _ADVANCED_NEVER_RULES.get((page, component_id))
    if never is not None:
        spec = get_spec(never[0])
        if spec and current_app.config.get(spec.config_name) == "never":
            return never[1]
    return None


def advanced_disabled_components(page):
    """``{component_id: raison}`` pour les composants désactivés hors éditeur."""
    return {
        component_id: reason
        for component_id in ADAPTERS[page]["components"]
        if (reason := advanced_disabled_reason(page, component_id))
    }


def current_payload(page):
    adapter = ADAPTERS[page]
    config_values = {}
    for key in _managed_keys(adapter, "config"):
        spec = get_spec(key)
        config_values[key] = current_app.config.get(spec.config_name) if spec else None
    css_values = {
        field["key"]: _normalize_css_value(
            current_app.css_variable_manager.get_variable(adapter["css_source"], field["key"]) or field.get("default"),
            field["type"],
        )
        for component in adapter["components"].values()
        for field in component["css"]
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


def palette_source_data(page):
    """Expose la palette publiée d'une page sans révéler d'autres réglages."""
    adapter = ADAPTERS.get(page)
    if adapter is None:
        return None
    payload = current_payload(page)
    return {
        "page": page,
        "label": adapter["label"],
        "roles": [
            {
                "id": role["id"],
                "label": role["label"],
                "values": [payload["css"].get(key) for key in role["keys"]],
            }
            for role in _palette_data(adapter)
        ],
    }


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


def complete_config(page, config):
    """Ajoute à ``config`` les clés de contenu gérées qui y manquent, avec la
    valeur actuellement publiée. Les brouillons enregistrés avant l'ajout
    d'un réglage dans l'éditeur ne contiennent pas encore la clé."""
    completed = dict(config) if isinstance(config, dict) else {}
    for key in _managed_keys(ADAPTERS[page], "config") - set(completed):
        spec = get_spec(key)
        completed[key] = current_app.config.get(spec.config_name) if spec else None
    return completed


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
    # Sous-ensemble accepté : les clés ajoutées après coup (anciens
    # brouillons) sont complétées avec la configuration publiée.
    if not isinstance(config_values, dict) or not set(config_values) <= allowed_config:
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
    for key in allowed_config - set(normalized_config):
        spec = get_spec(key)
        normalized_config[key] = current_app.config.get(spec.config_name) if spec else None

    allowed_css = _managed_keys(adapter, "css")
    css_types = _css_field_types(adapter)
    css_values = payload.get("css")
    optional_css = {field["key"] for component in adapter["components"].values()
                    for field in component["css"] if field.get("optional")}
    if (not isinstance(css_values, dict) or not set(css_values) <= allowed_css
            or not allowed_css - optional_css <= set(css_values)):
        raise ValueError("Variable d'apparence inconnue.")
    normalized_css = {}
    for key, value in css_values.items():
        value = _normalize_css_value(value, css_types[key])
        if not is_safe_css_value(value):
            raise ValueError(f"Valeur CSS invalide pour {key}.")
        stripped = value.strip()
        if not _is_valid_css_for_type(stripped, css_types[key]):
            raise ValueError(f"Valeur CSS inutilisable pour {key}.")
        normalized_css[key] = stripped

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


#: Libellés des propriétés de disposition, pour le diff publié/brouillon.
_LAYOUT_PROP_LABELS = {
    "visible": "Affichage",
    "zone": "Zone",
    "order": "Ordre",
    "span": "Largeur",
    "alignment": "Alignement",
}


def payload_diff(page, working):
    """Différences libellées entre ``working`` et la version publiée.

    ``working`` doit être un payload déjà normalisé par ``validate_payload``.
    Le résultat sert au panneau « Comparer » et au récapitulatif affiché avant
    publication : chaque changement porte le libellé affiché dans l'inspecteur
    plutôt que la clé technique.
    """
    adapter = ADAPTERS[page]
    published = current_payload(page)
    field_labels = {}
    field_component = {}
    for component_id, component in adapter["components"].items():
        for section in ("config", "css"):
            for field in component[section]:
                field_labels[field["key"]] = field["label"]
                field_component[field["key"]] = component["label"]

    changes = []
    for section in ("config", "css"):
        keys = set(published[section]) | set(working.get(section) or {})
        for key in sorted(keys):
            old = published[section].get(key)
            new = (working.get(section) or {}).get(key)
            if old != new:
                changes.append({
                    "section": section,
                    "component": field_component.get(key),
                    "key": key,
                    "label": field_labels.get(key, key),
                    "old": old,
                    "new": new,
                })
    for component_id, component in adapter["components"].items():
        old_item = published["layout"].get(component_id) or {}
        new_item = (working.get("layout") or {}).get(component_id) or {}
        for prop in ("visible", "zone", "order", "span", "alignment"):
            if old_item.get(prop) != new_item.get(prop):
                changes.append({
                    "section": "layout",
                    "component": component["label"],
                    "key": prop,
                    "label": _LAYOUT_PROP_LABELS[prop],
                    "old": old_item.get(prop),
                    "new": new_item.get(prop),
                })
    counts = {"config": 0, "css": 0, "layout": 0}
    for change in changes:
        counts[change["section"]] += 1
    return {"identical": not changes, "counts": counts, "changes": changes}


def layout_style(page, layout=None, preview=False):
    adapter = ADAPTERS.get(page)
    if adapter is None:
        return Markup("")
    defaults = default_layout(page)
    layout = layout or current_app.config.get(adapter["config_name"]) or defaults
    if not isinstance(layout, dict):
        layout = defaults
    rules = []
    if preview:
        # En aperçu, la visibilité est pilotée par l'attribut
        # ``data-page-editor-hidden`` (page_editor_preview.js) et non par une
        # règle ``!important`` figée — sinon un composant masqué au chargement
        # ne peut jamais être réaffiché sans recharger l'aperçu.
        rules.append("[data-page-editor-hidden]{display:none!important}")
        rules.append("[data-config-hidden],[data-adv-hidden]{display:none!important}")
    for component_id, component in adapter["components"].items():
        item = layout.get(component_id, {})
        if not isinstance(item, dict):
            item = defaults[component_id]
        selector = component["selector"]
        if not item.get("visible", True):
            if not preview:
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
        gallery = layout.get("gallery")
        if not preview and isinstance(gallery, dict) and gallery.get("visible") is False:
            rules.append("#div_center_divided{grid-template-columns:1fr}")
    elif page == "patient":
        rules.append("#main{display:flex;flex-flow:row wrap;align-content:flex-start;align-items:stretch}")
    elif page == "phone":
        rules.append(".container,#div_infos{display:flex;flex-flow:row wrap;align-content:flex-start;align-items:stretch}")
    return Markup("<style data-page-editor-layout>" + "".join(rules) + "</style>")


def preview_vars_style(css):
    """Bloc ``<style>`` des variables CSS de l'aperçu, généré côté serveur.

    Évite d'écrire une boucle Jinja dans un bloc ``<style>`` (illisible pour
    les linters CSS) et refiltre chaque valeur au passage.
    """
    declarations = "".join(
        f"--{key}:{value};"
        for key, value in (css or {}).items()
        if is_safe_css_value(value)
    )
    return Markup(
        f'<style id="page-editor-preview-vars">:root{{{declarations}}}</style>'
    )


def public_adapter_data(page):
    adapter = ADAPTERS.get(page)
    if adapter is None:
        return None
    components = deepcopy(adapter["components"])
    for component in components.values():
        for field in component["config"]:
            spec = get_spec(field["key"])
            letters = BALISE_LETTERS.get(spec.validator, "") if spec else ""
            if letters:
                field["markers"] = [
                    {
                        "token": f"{{{letter}}}",
                        "label": _MARKER_LABELS[letter][0],
                        "description": _MARKER_LABELS[letter][1],
                    }
                    for letter in "PNAMCDH" if letter in letters
                ]
    return {
        "page": page,
        "label": adapter["label"],
        "zones": adapter["zones"],
        "viewports": adapter["viewports"],
        "scenarios": adapter["scenarios"],
        "components": components,
        "palette": _palette_data(adapter),
        "default_layout": default_layout(page),
    }
