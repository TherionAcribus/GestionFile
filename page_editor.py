from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from copy import deepcopy

from flask import current_app
from markupsafe import Markup

from button_shapes import CHILDREN_SHAPE_KEY, HOME_SHAPE_KEY
from image_storage import ALLOWED_IMAGE_EXTENSIONS
from page_journey import public_journey
from params_registry import BALISE_LETTERS, get_spec
from utils import validate_config_text
from variables import is_safe_css_value


logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
ALIGNMENTS = frozenset({"left", "center", "right", "stretch"})
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_PAYLOAD_KEYS = frozenset({"schema_version", "page", "base_hash", "layout", "config", "css"})
_LAYOUT_KEYS = frozenset({"zone", "order", "visible", "span", "alignment"})
_PALETTE_ROLES = (
    {
        "id": "primary",
        "label": "Couleur principale",
        "description": "Bandeaux et boutons principaux",
    },
    {
        "id": "surface",
        "label": "Fond de page et surfaces",
        "description": "Arrière-plan de la page et zones claires",
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
    {
        "id": "accent",
        "label": "Couleur d’accent (appels)",
        "description": "Fond des appels en cours, distinct du bandeau",
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
               scenarios=None, managed_bool=None, hidden_when=None,
               layout_controls=None, contrast_pairs=None):
    return {
        "label": label,
        "zone": zone,
        "zones": [zone],
        "selector": selector,
        "span": span,
        "scenarios": scenarios,
        "managed_bool": managed_bool,
        "hidden_when": hidden_when,
        "layout_controls": layout_controls or ["visible", "span", "alignment", "order"],
        "config": config or [],
        "css": css or [],
        "contrast_pairs": contrast_pairs or [],
    }


_BUTTON_SHAPE_CHOICES = [["mixed", "Formes actuelles différentes — choisir une forme"],
                         ["circle", "Boutons ronds"],
                         ["square", "Boutons rectangulaires"]]


def _shape_condition(values):
    """Condition d'affichage liée à la forme des boutons du scénario : celle
    de l'accueil par défaut, celle des sous-activités dans « children »."""
    return {"key": HOME_SHAPE_KEY, "values": values,
            "key_by_scenario": {"children": CHILDREN_SHAPE_KEY}}


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
                contrast_pairs=[("title_font_color", "title_background_color")],
                config=[{"key": "announce_title", "label": "Texte", "type": "text"}],
                css=[{"key": "title_font_size", "label": "Taille du texte", "type": "size", "group": "Texte"},
                     {"key": "title_font_color", "label": "Couleur du texte", "type": "color", "group": "Texte", "role": "secondary"},
                     {"key": "title_font_border_size", "label": "Épaisseur contour texte", "type": "size", "group": "Texte"},
                     {"key": "title_font_border_color", "label": "Couleur contour texte", "type": "color", "group": "Texte", "role": "border"},
                     {"key": "title_background_color", "label": "Couleur", "type": "color", "group": "Bandeau", "role": "primary"},
                     {"key": "title_background_height", "label": "Hauteur", "type": "size", "group": "Bandeau"}]),
            # Le sous-titre partage le bandeau du titre : le contraste se mesure
            # contre title_background_color, pas contre une clé propre.
            "subtitle": _component("Sous-titre", "header", "#text_subtitle",
                contrast_pairs=[("subtitle_font_color", "title_background_color")],
                config=[{"key": "announce_subtitle", "label": "Texte", "type": "text"}],
                css=[{"key": "subtitle_font_size", "label": "Taille du texte", "type": "size", "group": "Texte"},
                     {"key": "subtitle_font_color", "label": "Couleur du texte", "type": "color", "group": "Texte", "role": "secondary"},
                     {"key": "subtitle_font_border_size", "label": "Épaisseur contour texte", "type": "size", "group": "Texte"},
                     {"key": "subtitle_font_border_color", "label": "Couleur contour texte", "type": "color", "group": "Texte", "role": "border"}]),
            "top_message": _component("Message supérieur", "main", "#div_display_text_up",
                hidden_when={"key": "announce_text_up_patients_display", "values": ["never"]},
                contrast_pairs=[("text_up_font_color", "text_up_background_color")],
                config=[{"key": "announce_text_up_patients", "label": "Texte", "type": "text"},
                        {"key": "announce_text_up_patients_display", "label": "Mode d'affichage", "type": "select",
                         "choices": [["always", "Toujours affiché"],
                                     ["empty", "Affiché si liste vide"],
                                     ["full", "Affiché si patient(s)"],
                                     ["never", "Jamais affiché"]]}],
                css=[{"key": "text_up_font_size", "label": "Taille du texte", "type": "size", "group": "Texte"},
                     {"key": "text_up_font_color", "label": "Couleur du texte", "type": "color", "group": "Texte", "role": "secondary"},
                     {"key": "text_up_font_border_size", "label": "Épaisseur contour texte", "type": "size", "group": "Texte"},
                     {"key": "text_up_font_border_color", "label": "Couleur contour texte", "type": "color", "group": "Texte", "role": "border"},
                     {"key": "text_up_background_color", "label": "Couleur de fond", "type": "color", "group": "Zone", "role": "primary"}]),
            "calls": _component("Appels en cours", "main", "#div_calling",
                scenarios=["active", "multiple", "gallery"],
                contrast_pairs=[("calling_font_color", "calling_background_color")],
                config=[{"key": "announce_call_text", "label": "Format", "type": "text"}],
                css=[{"key": "calling_font_size", "label": "Taille du texte", "type": "size", "group": "Texte"},
                     {"key": "calling_font_color", "label": "Couleur du texte", "type": "color", "group": "Texte", "role": "secondary"},
                     {"key": "calling_font_border_size", "label": "Épaisseur contour texte", "type": "size", "group": "Texte"},
                     {"key": "calling_font_border_color", "label": "Couleur contour texte", "type": "color", "group": "Texte", "role": "border"},
                     {"key": "calling_background_color", "label": "Couleur de fond", "type": "color", "group": "Zone", "role": "accent"}]),
            "empty_message": _component("Message file vide", "main", "#div_display_text_down",
                scenarios=["empty"],
                hidden_when={"key": "announce_text_down_patients_display", "values": ["never"]},
                contrast_pairs=[("text_down_font_color", "text_down_background_color")],
                config=[{"key": "announce_text_down_patients", "label": "Texte", "type": "text"},
                        {"key": "announce_text_down_patients_display", "label": "Mode d'affichage", "type": "select",
                         "choices": [["empty", "Affiché si liste vide"],
                                     ["never", "Jamais affiché"]]}],
                css=[{"key": "text_down_font_size", "label": "Taille du texte", "type": "size", "group": "Texte"},
                     {"key": "text_down_font_color", "label": "Couleur du texte", "type": "color", "group": "Texte", "role": "secondary"},
                     {"key": "text_down_font_border_size", "label": "Épaisseur contour texte", "type": "size", "group": "Texte"},
                     {"key": "text_down_font_border_color", "label": "Couleur contour texte", "type": "color", "group": "Texte", "role": "border"},
                     {"key": "text_down_background_color", "label": "Couleur de fond", "type": "color", "group": "Zone", "role": "primary"}]),
            "ongoing": _component("Patients au comptoir", "main", "#div_ongoing",
                managed_bool="announce_ongoing_display",
                contrast_pairs=[("ongoing_font_color", "ongoing_background_color")],
                config=[{"key": "announce_ongoing_display", "label": "Afficher les patients au comptoir", "type": "bool"},
                        {"key": "announce_ongoing_text", "label": "Format", "type": "text"}],
                css=[{"key": "ongoing_font_size", "label": "Taille du texte", "type": "size", "group": "Texte"},
                     {"key": "ongoing_font_color", "label": "Couleur du texte", "type": "color", "group": "Texte", "role": "secondary"},
                     {"key": "ongoing_font_border_size", "label": "Épaisseur contour texte", "type": "size", "group": "Texte"},
                     {"key": "ongoing_font_border_color", "label": "Couleur contour texte", "type": "color", "group": "Texte", "role": "border"},
                     {"key": "ongoing_background_color", "label": "Couleur de fond", "type": "color", "group": "Zone", "role": "primary"}]),
            "gallery": _component("Galerie", "aside", "#div_pub", span=6,
                scenarios=["gallery"], managed_bool="announce_infos_display",
                # Seul composant de la zone « aside » : largeur, ordre et
                # alignement n'ont aucun effet (« span » reste exigé par
                # validate_payload, d'où sa valeur conservée ci-dessus).
                layout_controls=["visible"],
                # Réglages auparavant réservés à l'onglet « Galerie » du mode
                # avancé. Le choix des images se fait dans « Galerie média ».
                config=[{"key": "announce_infos_display", "label": "Afficher", "type": "bool"},
                        {"key": "announce_infos_display_time", "label": "Durée d'affichage de chaque image",
                         "type": "int", "min": 1, "max": 3600, "unit": "s",
                         "help": "Les images se choisissent dans la page « Galerie média »."},
                        {"key": "announce_infos_transition", "label": "Transition entre les images", "type": "select",
                         "choices": [["slide", "Glissement"], ["fade", "Fondu"], ["cube", "Cube"],
                                     ["coverflow", "Carrousel 3D"], ["flip", "Retournement"], ["cards", "Cartes"]]},
                        {"key": "announce_infos_mix_folders", "label": "Mélanger les images", "type": "bool"},
                        {"key": "announce_infos_width", "label": "Largeur de la galerie", "type": "int",
                         "min": 50, "max": 10000, "unit": "px",
                         "help": "Taille et transition s'appliquent sur l'écran après publication."},
                        {"key": "announce_infos_height", "label": "Hauteur de la galerie", "type": "int",
                         "min": 50, "max": 10000, "unit": "px"}]),
            "next": _component("Prochains patients", "footer", "#div_next_patients",
                managed_bool="announce_next_patients_display",
                contrast_pairs=[("next_patients_font_color", "next_patients_background_color")],
                config=[{"key": "announce_next_patients_display", "label": "Afficher la liste des prochains patients", "type": "bool"},
                        {"key": "announce_next_patients_text", "label": "Texte", "type": "text"},
                        {"key": "announce_next_patients_alignment", "label": "Alignement du texte", "type": "select",
                         "choices": [["center", "Centré"], ["left", "Gauche"]]}],
                css=[{"key": "next_patients_font_size", "label": "Taille du texte", "type": "size", "group": "Texte"},
                     {"key": "next_patients_font_color", "label": "Couleur du texte", "type": "color", "group": "Texte", "role": "secondary"},
                     {"key": "next_patients_font_border_size", "label": "Épaisseur contour texte", "type": "size", "group": "Texte"},
                     {"key": "next_patients_font_border_color", "label": "Couleur contour texte", "type": "color", "group": "Texte", "role": "border"},
                     {"key": "next_patients_background_color", "label": "Couleur de fond", "type": "color", "group": "Zone", "role": "primary"}]),
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
            {"id": "activity_inactive", "label": "Activités indisponibles"},
            {"id": "validation", "label": "Validation QR / impression"},
            {"id": "conclusion", "label": "Conclusion"},
            {"id": "print_error", "label": "Erreur d'impression"},
            {"id": "printing", "label": "Impression en cours"},
            {"id": "print_uncertain", "label": "Impression incertaine"},
            {"id": "staff_called", "label": "Personnel appelé"},
        ],
        "components": {
            "title": _component("Titre", "header", "#div_title_area",
                layout_controls=["visible"],
                contrast_pairs=[("patient_title_font_color", "patient_title_background_color")],
                config=[{"key": "page_patient_title", "label": "Texte", "type": "text"}],
                css=[{"key": "patient_title_font_size", "label": "Taille du texte", "type": "size", "group": "Texte"},
                     {"key": "patient_title_font_color", "label": "Couleur du texte", "type": "color", "group": "Texte", "role": "secondary"},
                     {"key": "patient_title_border_size", "label": "Épaisseur contour texte", "type": "size", "group": "Texte"},
                     {"key": "patient_title_border_color", "label": "Couleur contour texte", "type": "color", "group": "Texte", "role": "border"},
                     {"key": "patient_title_background_color", "label": "Couleur", "type": "color", "group": "Bandeau", "role": "primary"},
                     {"key": "patient_title_background_height", "label": "Hauteur", "type": "size", "group": "Bandeau"}]),
            "buttons": _component("Contenu principal", "main", "#div_buttons_parents",
                layout_controls=["visible"],
                # Le texte des boutons ronds s'affiche sous l'image, sur le
                # fond de page — pas sur le bouton lui-même.
                contrast_pairs=[("square_button_text_color", "square_button_color"),
                                ("circle_button_text_color", "patient_secondary_color"),
                                ("validation_button_text_color", "validation_button_color"),
                                ("validation_text_font_color", "validation_text_background_color"),
                                ("confirmation_text_font_color", "confirmation_text_background_color"),
                                ("scan_explanation_font_color", "scan_explanation_background_color")],
                # ~24 réglages de contenu : les sous-groupes (« group ») les
                # découpent en sections titrées dans l'inspecteur. L'ordre de
                # la liste n'affecte que l'affichage, pas le payload — les
                # groupes restent contigus pour n'afficher chaque titre
                # qu'une fois.
                config=[{"key": "page_patient_button_shape", "label": "Forme des boutons de l’accueil", "type": "select",
                         "group": "Boutons d'activité",
                         "choices": _BUTTON_SHAPE_CHOICES,
                         "help": "Appliquée à tous les boutons de l’accueil lors de la publication. Les boutons de sous-activités ont leur propre réglage (scénario « Sous-activités »). Le mode avancé permet ensuite de les différencier."},
                        {"key": "page_patient_children_button_shape", "label": "Forme des boutons de sous-activités", "type": "select",
                         "group": "Boutons d'activité",
                         "choices": _BUTTON_SHAPE_CHOICES,
                         "help": "Appliquée lors de la publication aux boutons affichés après le choix d’un bouton parent. Les tailles et couleurs des boutons ronds ou rectangulaires sont communes avec l’accueil."},
                        {"key": "page_patient_circle_image_above", "label": "Image débordant au-dessus du bouton", "type": "bool",
                         "group": "Boutons d'activité",
                         "help": "Boutons ronds : l'image est agrandie et déborde au-dessus du cercle, comme sur l'ancienne présentation. Sinon elle reste contenue dans le cercle. Réglage commun à l’accueil et aux sous-activités.",
                         "visible_when": _shape_condition(["circle", "mixed"])},
                        {"key": "page_patient_qrcode_display", "label": "Afficher le QR code", "type": "bool",
                         "group": "QR code et scan",
                         "help": "Ce réglage est partagé avec l’onglet QR code du mode avancé."},
                        {"key": "page_patient_qrcode_web_page", "label": "Le QR code affiche une adresse web (sinon des données brutes)", "type": "bool",
                         "group": "QR code et scan",
                         "help": "Non disponible si le serveur tourne en local."},
                        {"key": "page_patient_qrcode_data", "label": "Données brutes du QR code", "type": "text",
                         "group": "QR code et scan"},
                        {"key": "page_patient_qrcode_display_specific_message", "label": "Inclure le message spécifique de l'activité dans le QR code", "type": "bool",
                         "group": "QR code et scan"},
                        {"key": "page_patient_display_button_scan", "label": "Afficher le bouton « Scanner et valider »", "type": "bool",
                         "group": "QR code et scan"},
                        {"key": "page_patient_display_scan_explanation", "label": "Afficher les explications pour scanner le QR-Code", "type": "bool",
                         "group": "QR code et scan"},
                        {"key": "page_patient_interface_scan_explanation", "label": "Consignes pour scanner le QR code", "type": "text",
                         "group": "QR code et scan"},
                        {"key": "page_patient_print_after_scan", "label": "Page de réimpression après « Scan »", "type": "bool",
                         "group": "QR code et scan"},
                        {"key": "page_patient_print_after_print", "label": "Page de réimpression après « Print »", "type": "bool",
                         "group": "QR code et scan"},
                        # Écran de validation (après le choix d'une activité) et
                        # message de confirmation : auparavant réservés au mode
                        # avancé. « Imprimer directement » ouvre le groupe : il
                        # décide si cet écran apparaît.
                        {"key": "page_patient_direct_print", "label": "Imprimer directement le ticket (sans écran de validation)", "type": "bool",
                         "group": "Écran de validation"},
                        {"key": "page_patient_validation_message", "label": "Écran de validation : message",
                         "type": "text", "group": "Écran de validation",
                         "help": "Affiché après le choix d'une activité, avant l'impression ou le scan."},
                        {"key": "page_patient_interface_validate_print", "label": "Écran de validation : bouton « Imprimer »", "type": "text",
                         "group": "Écran de validation"},
                        {"key": "page_patient_button_print_ticket_display_picture", "label": "Image sur le bouton « Imprimer »", "type": "bool",
                         "group": "Écran de validation"},
                        {"key": "page_patient_button_print_ticket_picture", "label": "Image du bouton « Imprimer »", "type": "image",
                         "group": "Écran de validation"},
                        {"key": "page_patient_interface_validate_scan", "label": "Écran de validation : bouton « Scanner »", "type": "text",
                         "group": "Écran de validation"},
                        {"key": "page_patient_interface_validate_cancel", "label": "Écran de validation : bouton « Annuler »", "type": "text",
                         "group": "Écran de validation"},
                        {"key": "page_patient_button_cancel_display_picture", "label": "Image sur le bouton « Annuler »", "type": "bool",
                         "group": "Écran de validation"},
                        {"key": "page_patient_button_cancel_picture", "label": "Image du bouton « Annuler »", "type": "image",
                         "group": "Écran de validation"},
                        {"key": "page_patient_display_conclusion", "label": "Afficher l'écran de conclusion", "type": "bool",
                         "group": "Conclusion et délais",
                         "help": "Décoché : retour à l'accueil dès que le ticket est imprimé ou l'inscription par QR code validée. Les messages d'incident d'impression restent affichés."},
                        {"key": "page_patient_confirmation_message", "label": "Message de confirmation (après inscription)", "type": "text",
                         "group": "Conclusion et délais"},
                        {"key": "page_patient_interface_done_print", "label": "Conclusion : bouton « Imprimer »", "type": "text",
                         "group": "Conclusion et délais"},
                        {"key": "page_patient_interface_done_extend", "label": "Conclusion : bouton « Prolonger »", "type": "text",
                         "group": "Conclusion et délais"},
                        {"key": "page_patient_interface_done_back", "label": "Conclusion : bouton de retour", "type": "text",
                         "group": "Conclusion et délais"},
                        {"key": "page_patient_end_timer", "label": "Délai avant retour à l'accueil", "type": "int", "min": 0,
                         "unit": "s", "group": "Conclusion et délais"}],
                css=[{"key": "circle_button_size", "label": "Bouton rond", "type": "size"},
                     {"key": "circle_button_image_size", "label": "Image bouton rond", "type": "size", "default": "160px"},
                     {"key": "square_button_width", "label": "Largeur bouton", "type": "size"},
                     {"key": "square_button_height", "label": "Hauteur bouton", "type": "size"},
                     {"key": "square_button_image_size", "label": "Image bouton", "type": "size", "default": "110px"},
                     {"key": "square_button_color", "label": "Couleur bouton", "type": "color", "role": "primary"},
                     {"key": "square_button_border_size", "label": "Épaisseur bordure", "type": "size"},
                     {"key": "square_button_border_color", "label": "Couleur bordure", "type": "color", "role": "border"}]),
            "back_button": _component("Bouton Retour", "main",
                "[data-page-editor-component='back_button']",
                scenarios=["children"],
                layout_controls=["visible"],
                contrast_pairs=[("square_cancel_button_text_color", "square_cancel_button_color")],
                config=[{"key": "page_patient_interface_children_back", "label": "Texte", "type": "text"}],
                css=[]),
            "subtitle": _component("Sous-titre standard", "footer", "[data-page-editor-component='subtitle']",
                layout_controls=["visible"],
                scenarios=["home", "children", "validation", "conclusion", "print_error", "printing", "print_uncertain", "staff_called"],
                contrast_pairs=[("subtitle_font_color", "subtitle_background_color")],
                config=[{"key": "page_patient_subtitle", "label": "Texte du sous-titre", "type": "text"}],
                css=[{"key": "subtitle_font_size", "label": "Taille du texte", "type": "size", "group": "Texte"},
                     {"key": "subtitle_font_color", "label": "Couleur du texte", "type": "color", "group": "Texte", "role": "secondary"},
                     {"key": "subtitle_border_size", "label": "Épaisseur contour texte", "type": "size", "group": "Texte"},
                     {"key": "subtitle_border_color", "label": "Couleur contour texte", "type": "color", "group": "Texte", "role": "border"},
                     {"key": "subtitle_background_color", "label": "Couleur", "type": "color", "group": "Bandeau", "role": "primary"},
                     {"key": "subtitle_background_height", "label": "Hauteur", "type": "size", "group": "Bandeau"}]),
            "activity_status": _component("Affichage des activités indisponibles", "footer",
                "[data-page-editor-component='activity_status']",
                scenarios=["activity_inactive"],
                layout_controls=["visible"],
                contrast_pairs=[("subtitle_no_activity_font_color", "subtitle_no_activity_background_color")],
                config=[{"key": "page_patient_disable_button", "label": "Griser les boutons indisponibles (sinon les masquer)", "type": "bool",
                         "help": "Activé : le bouton reste visible en grisé. Désactivé : le bouton disparaît et les autres conservent leur présentation normale."},
                        {"key": "page_patient_disable_default_message", "label": "Message affiché après sélection", "type": "text"},
                        {"key": "page_patient_timer_activity_inactive", "label": "Durée d'affichage du message", "type": "int", "min": 0, "unit": "s"}],
                css=[]),
            "specific_message": _component("Message spécifique d’activité", "footer",
                "[data-page-editor-component='specific_message']",
                scenarios=["validation", "conclusion"],
                managed_bool="page_patient_display_specific_message",
                layout_controls=["visible"],
                contrast_pairs=[("subtitle_specific_message_font_color", "subtitle_specific_message_background_color")],
                config=[{"key": "page_patient_display_specific_message", "label": "Afficher le message spécifique de l'activité", "type": "bool",
                         "help": "Le texte du message se configure sur chaque activité."}],
                css=[]),
            # Écran affiché quand le ticket ne peut pas être imprimé (overlay
            # rempli par patients.js selon le comportement d'échec choisi).
            "print_error": _component("Erreur d'impression", "main", "#print_status_overlay",
                scenarios=["print_error", "printing", "print_uncertain", "staff_called"],
                layout_controls=["visible"],
                contrast_pairs=[("print_error_font_color", "print_error_background_color")],
                config=[{"key": "page_patient_print_fail_behavior", "label": "Comportement en cas d'échec", "type": "select",
                         "choices": [["ask", "Demander au patient"],
                                     ["keep", "Conserver dans la file"],
                                     ["cancel", "Annuler l'inscription"]]},
                        {"key": "page_patient_print_fail_show_retry", "label": "Afficher le bouton « Réessayer » (mode Demander)", "type": "bool"},
                        {"key": "page_patient_print_fail_show_staff", "label": "Afficher le bouton « Appeler le personnel » (mode Demander)", "type": "bool"},
                        {"key": "page_patient_print_fail_abandon_timer", "label": "Retour auto à l'accueil si aucun choix (0 = jamais)", "type": "int", "min": 0, "unit": "s"},
                        {"key": "page_patient_print_uncertain_timer", "label": "Impression incertaine : délai de réponse du patient", "type": "int", "min": 5, "max": 600, "unit": "s"},
                        {"key": "page_patient_print_uncertain_timeout_action", "label": "Impression incertaine : sans réponse du patient", "type": "select",
                         "choices": [["received", "Ticket reçu : il entre dans la file"],
                                     ["staff", "Personnel : l'inscription expire"]]},
                        {"key": "page_patient_interface_printing", "label": "Message « impression en cours »", "type": "text"},
                        {"key": "page_patient_interface_print_failed", "label": "Message d'échec (mode Demander)", "type": "text"},
                        {"key": "page_patient_interface_no_ticket", "label": "Message si le patient est conservé (mode Conserver)", "type": "text"},
                        {"key": "page_patient_interface_print_failed_staff", "label": "Message si l'inscription est annulée (mode Annuler)", "type": "text"},
                        {"key": "page_patient_interface_staff_called", "label": "Message après « Appeler le personnel »", "type": "text"},
                        {"key": "page_patient_interface_print_uncertain", "label": "Message si le résultat d'impression est incertain", "type": "text"},
                        {"key": "page_patient_interface_retry", "label": "Bouton « Réessayer »", "type": "text"},
                        {"key": "page_patient_interface_ticket_received", "label": "Bouton « J'ai récupéré mon ticket »", "type": "text"},
                        {"key": "page_patient_interface_ticket_missing", "label": "Bouton « Je n'ai pas de ticket »", "type": "text"},
                        {"key": "page_patient_interface_call_staff", "label": "Bouton « Appeler le personnel »", "type": "text"}]),
            "languages": _component("Choix de langue", "overlay", ".language-selector",
                managed_bool="page_patient_display_translations",
                layout_controls=["visible"],
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
            {"id": "ended", "label": "Parcours terminé"},
            {"id": "closed", "label": "Activité fermée"},
        ],
        "components": {},
    },
}


for index in range(1, 7):
    ADAPTERS["phone"]["components"][f"line{index}"] = _component(
        f"Ligne {index}", "main", f"#phone_line{index}", scenarios=["confirmation"],
        contrast_pairs=[(f"phone_line{index}_font_color", f"phone_line{index}_background_color")],
        config=[{"key": f"phone_line{index}", "label": "Texte Markdown", "type": "text"}],
        css=[{"key": f"phone_line{index}_font_size", "label": "Taille du texte", "type": "size", "group": "Texte"},
             {"key": f"phone_line{index}_font_color", "label": "Couleur du texte", "type": "color", "group": "Texte", "role": "secondary"},
             {"key": f"phone_line{index}_font_weight", "label": "Graisse", "type": "number", "group": "Texte", "step": 100},
             {"key": f"phone_line{index}_border_size", "label": "Épaisseur contour texte", "type": "size", "group": "Texte"},
             {"key": f"phone_line{index}_border_color", "label": "Couleur contour texte", "type": "color", "group": "Texte", "role": "border"},
             {"key": f"phone_line{index}_background_color", "label": "Couleur de fond", "type": "color", "group": "Zone", "role": "surface"},
             {"key": f"phone_line{index}_background_height", "label": "Hauteur", "type": "size", "group": "Zone"}],
    )
    ADAPTERS["phone"]["components"][f"your_turn_line{index}"] = _component(
        f"Appel — ligne {index}", "main", f"#phone_your_turn_line{index}",
        scenarios=["your-turn"],
        managed_bool="phone_display_your_turn",
        contrast_pairs=[(f"phone_your_turn_line{index}_font_color",
                         f"phone_your_turn_line{index}_background_color")],
        config=(
            [{"key": "phone_display_your_turn", "label": "Afficher l'écran « Votre tour »", "type": "bool"}]
            if index == 1 else []
        ) + [{"key": f"phone_your_turn_line{index}", "label": "Texte Markdown", "type": "text"}],
        css=[{"key": f"phone_your_turn_line{index}_font_size", "label": "Taille du texte", "type": "size", "group": "Texte"},
             {"key": f"phone_your_turn_line{index}_font_color", "label": "Couleur du texte", "type": "color", "group": "Texte", "role": "secondary"},
             {"key": f"phone_your_turn_line{index}_font_weight", "label": "Graisse", "type": "number", "group": "Texte", "step": 100},
             {"key": f"phone_your_turn_line{index}_border_size", "label": "Épaisseur contour texte", "type": "size", "group": "Texte"},
             {"key": f"phone_your_turn_line{index}_border_color", "label": "Couleur contour texte", "type": "color", "group": "Texte", "role": "border"},
             {"key": f"phone_your_turn_line{index}_background_color", "label": "Couleur de fond", "type": "color", "group": "Zone", "role": "surface"},
             {"key": f"phone_your_turn_line{index}_background_height", "label": "Hauteur", "type": "size", "group": "Zone"}],
    )

ADAPTERS["phone"]["components"]["title"] = _component(
    "Titre", "header", "#div_title_area",
    # Seul composant de la zone « header » ; le centrage se règle via
    # « phone_center » ci-dessous, pas par les contrôles de disposition.
    layout_controls=["visible"],
    contrast_pairs=[("phone_title_font_color", "phone_title_background_color")],
    config=[{"key": "phone_title", "label": "Texte", "type": "text"},
            {"key": "phone_center", "label": "Centrer le contenu", "type": "bool"}],
    css=[{"key": "phone_title_font_size", "label": "Taille du texte", "type": "size", "group": "Texte"},
         {"key": "phone_title_font_color", "label": "Couleur du texte", "type": "color", "group": "Texte", "role": "secondary"},
         {"key": "phone_title_font_weight", "label": "Graisse", "type": "number", "group": "Texte", "step": 100},
         {"key": "phone_title_border_size", "label": "Épaisseur contour texte", "type": "size", "group": "Texte"},
         {"key": "phone_title_border_color", "label": "Couleur contour texte", "type": "color", "group": "Texte", "role": "border"},
         {"key": "phone_title_background_color", "label": "Couleur", "type": "color", "group": "Bandeau", "role": "primary"},
         {"key": "phone_title_background_height", "label": "Hauteur", "type": "size", "group": "Bandeau"}],
)
ADAPTERS["phone"]["components"]["specific"] = _component(
    "Message spécifique", "footer", "#specific_message",
    # Seul composant de la zone « footer » : disposition sans effet (le
    # centrage dépend de « phone_center », réglé sur le composant Titre).
    layout_controls=["visible"],
    managed_bool="phone_display_specific_message",
    contrast_pairs=[("phone_specific_message_font_color", "phone_specific_message_background_color")],
    config=[{"key": "phone_display_specific_message", "label": "Afficher le message spécifique", "type": "bool"}],
    css=[{"key": "phone_specific_message_font_size", "label": "Taille du texte", "type": "size", "group": "Texte"},
         {"key": "phone_specific_message_font_weight", "label": "Graisse", "type": "number", "group": "Texte", "step": 100},
         {"key": "phone_specific_message_font_color", "label": "Couleur du texte", "type": "color", "group": "Texte", "role": "secondary"},
         {"key": "phone_specific_message_border_size", "label": "Épaisseur contour texte", "type": "size", "group": "Texte"},
         {"key": "phone_specific_message_border_color", "label": "Couleur contour texte", "type": "color", "group": "Texte", "role": "border"},
         {"key": "phone_specific_message_background_color", "label": "Couleur de fond", "type": "color", "group": "Zone", "role": "surface"},
         {"key": "phone_specific_message_background_height", "label": "Hauteur", "type": "size", "group": "Zone"}],
)

# Écrans d'attente, de fin de parcours et d'activité fermée : texte simple
# échappé Jinja (pas de Markdown), même famille de réglages que les lignes.
for _component_id, _label, _selector, _scenario, _prefix, _config in (
    ("waiting_text", "Écran d'attente", "#phone_waiting", "waiting", "phone_waiting",
     [{"key": "phone_waiting_line1", "label": "Première ligne — {N} = numéro d'appel", "type": "text"},
      {"key": "phone_waiting_line2", "label": "Deuxième ligne", "type": "text"}]),
    ("journey_end", "Parcours terminé", "#phone_journey_end", "ended", "phone_journey_end",
     [{"key": "phone_journey_end_message", "label": "Message de fin de parcours — {N} = numéro d'appel", "type": "text"}]),
    ("closed", "Activité fermée", "#phone_closed", "closed", "phone_closed",
     [{"key": "page_patient_disable_default_message", "label": "Message par défaut si l'activité n'en définit pas", "type": "text"}]),
):
    ADAPTERS["phone"]["components"][_component_id] = _component(
        _label, "main", _selector, scenarios=[_scenario], layout_controls=["visible"],
        contrast_pairs=[(f"{_prefix}_font_color", f"{_prefix}_background_color")],
        config=_config,
        css=[{"key": f"{_prefix}_font_size", "label": "Taille du texte", "type": "size", "group": "Texte"},
             {"key": f"{_prefix}_font_color", "label": "Couleur du texte", "type": "color", "group": "Texte", "role": "secondary"},
             {"key": f"{_prefix}_font_weight", "label": "Graisse", "type": "number", "group": "Texte", "step": 100},
             {"key": f"{_prefix}_border_size", "label": "Épaisseur contour texte", "type": "size", "group": "Texte"},
             {"key": f"{_prefix}_border_color", "label": "Couleur contour texte", "type": "color", "group": "Texte", "role": "border"},
             {"key": f"{_prefix}_background_color", "label": "Couleur de fond", "type": "color", "group": "Zone", "role": "surface"},
             {"key": f"{_prefix}_background_height", "label": "Hauteur", "type": "size", "group": "Zone"}],
    )


#: Tuples ``(key, label, type, default, group, role)`` — ``group`` et ``role``
#: peuvent être ``None``. ``role`` rattache la couleur à un groupe de la
#: palette ; laisser ``None`` exclut volontairement le champ de la palette
#: (ex. fonds de texte « transparent » par défaut).
_ADDITIONAL_CSS_FIELDS = {
    "announce": {"title": [
        # Liseré en bas du bandeau titre : même mécanisme que la borne patient.
        ("title_bottom_border_size", "Épaisseur liseré bas", "size", "0px", "Liseré en bas", None),
        ("title_bottom_border_color", "Couleur liseré bas", "color", "#000000", "Liseré en bas", "border"),
        ("announce_secondary_color", "Fond de page", "color", "#B6F5F5", "Page", "surface"),
    ],
    "subtitle": [
        # Filet séparateur tracé au-dessus du sous-titre, entre le titre et le
        # sous-titre (les deux lignes partagent le même bandeau).
        ("subtitle_top_border_size", "Épaisseur liseré haut", "size", "0px", "Liseré en haut", None),
        ("subtitle_top_border_color", "Couleur liseré haut", "color", "#000000", "Liseré en haut", "border"),
    ],
    "ongoing": [
        # Liseré autour de la zone : nommé « ..._border_* » pour rejoindre le
        # rôle « Couleur des bordures » de la palette et le défaut 0px des
        # thèmes — 0px = pas de liseré affiché.
        ("ongoing_border_size", "Épaisseur liseré", "size", "0px", "Liseré", None),
        ("ongoing_border_color", "Couleur liseré", "color", "#000000", "Liseré", "border"),
    ],
    "next": [
        # Liseré autour du bandeau des prochains patients (appliqué à
        # #div_footer, qui porte le fond de la zone).
        ("next_patients_border_size", "Épaisseur liseré", "size", "0px", "Liseré", None),
        ("next_patients_border_color", "Couleur liseré", "color", "#000000", "Liseré", "border"),
    ]},
    "phone": {"title": [
        ("phone_secondary_color", "Fond de page", "color", "#B6F5F5", "Page", "surface"),
    ]},
    "patient": {
        "title": [
            # Liseré en bas du bandeau titre : nommé « ..._border_* » pour
            # rejoindre le rôle « Couleur des bordures » de la palette et le
            # défaut 0px des thèmes — 0px = pas de liseré affiché.
            ("patient_title_bottom_border_size", "Épaisseur liseré bas", "size", "0px", "Liseré en bas", None),
            ("patient_title_bottom_border_color", "Couleur liseré bas", "color", "#000000", "Liseré en bas", "border"),
        ],
        "buttons": [
            ("patient_secondary_color", "Fond de page", "color", "#B6F5F5", "Page", "surface"),
            # Contour et fond du TEXTE des boutons (mode avancé uniquement
            # jusqu'ici). Les fonds de texte restent hors palette : appliquer
            # une couleur y poserait un pavé derrière chaque libellé.
            ("circle_button_text_border_color", "Contour du texte des boutons ronds", "color", "black", None, "border"),
            ("circle_button_text_background_color", "Fond du texte des boutons ronds", "color", "transparent", None, None),
            ("square_button_text_border_color", "Contour du texte des boutons", "color", "black", None, "border"),
            ("square_button_text_background_color", "Fond du texte des boutons", "color", "transparent", None, None),
            ("validation_button_text_border_color", "Contour du texte des boutons de validation", "color", "black", None, "border"),
            ("validation_button_text_background_color", "Fond du texte des boutons de validation", "color", "transparent", None, None),
            ("square_button_text_color", "Texte des boutons", "color", "#FFFFFF", None, "secondary"),
            ("square_button_text_size", "Taille du texte des boutons", "size", "30px", None, None),
            ("square_button_text_border_size", "Contour du texte des boutons", "size", "0px", None, None),
            ("circle_button_color", "Fond des boutons ronds", "color", "#008B8B", None, "primary"),
            ("circle_button_text_color", "Texte sous les boutons ronds", "color", "#008B8B", None, "secondary"),
            ("circle_button_text_size", "Taille du texte sous les boutons ronds", "size", "30px", None, None),
            ("circle_button_text_border_size", "Contour du texte sous les boutons ronds", "size", "0px", None, None),
            ("circle_button_border_size", "Bordure des boutons ronds", "size", "0px", None, None),
            ("circle_button_border_color", "Couleur bordure des boutons ronds", "color", "#000000", None, "border"),
            ("validation_button_width", "Largeur des boutons de validation", "size", "500px", None, None),
            ("validation_button_height", "Hauteur des boutons de validation", "size", "150px", None, None),
            ("validation_button_color", "Fond des boutons de validation", "color", "#008B8B", None, "primary"),
            ("validation_button_border_size", "Bordure des boutons de validation", "size", "0px", None, None),
            ("validation_button_border_color", "Couleur bordure validation", "color", "#000000", None, "border"),
            ("validation_button_text_color", "Texte des boutons de validation", "color", "#FFFFFF", None, "secondary"),
            ("validation_button_text_size", "Taille du texte validation", "size", "40px", None, None),
            ("validation_button_text_border_size", "Contour du texte validation", "size", "0px", None, None),
            ("validation_button_picture_size", "Taille de l'image validation", "size", "200px", None, None),
            ("validation_text_font_color", "Texte du numéro", "color", "#008B8B", None, "secondary"),
            ("validation_text_font_size", "Taille du numéro", "size", "50px", None, None),
            ("validation_text_border_size", "Contour du numéro", "size", "0px", None, None),
            ("validation_text_border_color", "Couleur contour du numéro", "color", "#000000", None, "border"),
            ("validation_text_background_color", "Fond du texte de validation", "color", "#B6F5F5", None, "surface"),
            ("confirmation_text_font_color", "Texte de confirmation", "color", "#008B8B", None, "secondary"),
            ("confirmation_text_font_size", "Taille de confirmation", "size", "50px", None, None),
            ("confirmation_text_border_size", "Contour de confirmation", "size", "0px", None, None),
            ("confirmation_text_border_color", "Couleur contour confirmation", "color", "#000000", None, "border"),
            ("confirmation_text_background_color", "Fond de confirmation", "color", "#B6F5F5", None, "surface"),
            ("scan_explanation_font_color", "Texte des consignes QR", "color", "#008B8B", None, "secondary"),
            ("scan_explanation_font_size", "Taille des consignes QR", "size", "50px", None, None),
            ("scan_explanation_border_size", "Contour des consignes QR", "size", "0px", None, None),
            ("scan_explanation_border_color", "Couleur contour des consignes QR", "color", "#000000", None, "border"),
            ("scan_explanation_background_color", "Fond des consignes QR", "color", "#B6F5F5", None, "surface"),
        ],
        "subtitle": [
            # Liseré en haut du bandeau de pied de page : appliqué aux trois
            # variantes (standard, activités indisponibles, message spécifique)
            # comme subtitle_background_height, partagé entre elles.
            ("subtitle_top_border_size", "Épaisseur liseré haut", "size", "0px", "Liseré en haut", None),
            ("subtitle_top_border_color", "Couleur liseré haut", "color", "#000000", "Liseré en haut", "border"),
        ],
        "back_button": [
            ("square_cancel_button_width", "Largeur du bouton retour", "size", "500px", None, None),
            ("square_cancel_button_height", "Hauteur du bouton retour", "size", "150px", None, None),
            ("square_cancel_button_color", "Fond du bouton retour", "color", "#008B8B", None, "primary"),
            ("square_cancel_button_border_size", "Bordure du bouton retour", "size", "0px", None, None),
            ("square_cancel_button_border_color", "Couleur bordure retour", "color", "#000000", None, "border"),
            ("square_cancel_button_text_color", "Texte du bouton retour", "color", "#FFFFFF", None, "secondary"),
            ("square_cancel_button_text_size", "Taille du texte retour", "size", "30px", None, None),
            ("square_cancel_button_text_border_size", "Contour du texte retour", "size", "0px", None, None),
            ("square_cancel_button_text_border_color", "Contour du texte du bouton retour", "color", "black", None, "border"),
            ("square_cancel_button_text_background_color", "Fond du texte du bouton retour", "color", "transparent", None, None),
            ("square_cancel_button_image_size", "Taille de l'image du bouton retour", "size", "200px", None, None),
        ],
        "print_error": [
            ("print_error_font_size", "Taille du message", "size", "50px", None, None),
            ("print_error_font_color", "Couleur du message", "color", "#008B8B", None, "secondary"),
            ("print_error_border_size", "Épaisseur contour", "size", "0px", None, None),
            ("print_error_border_color", "Couleur contour", "color", "#000000", None, "border"),
            ("print_error_background_color", "Fond du message", "color", "#B6F5F5", None, "surface"),
            ("print_error_number_size", "Taille du numéro", "size", "80px", None, None),
        ],
        "activity_status": [
            ("subtitle_no_activity_font_color", "Texte sans activité", "color", "#FFFFFF", None, "secondary"),
            ("subtitle_no_activity_font_size", "Taille sans activité", "size", "40px", None, None),
            ("subtitle_no_activity_border_size", "Contour sans activité", "size", "0px", None, None),
            ("subtitle_no_activity_border_color", "Couleur contour sans activité", "color", "#000000", None, "border"),
            ("subtitle_no_activity_background_color", "Fond sans activité", "color", "#008B8B", None, "primary"),
        ],
        "specific_message": [
            ("subtitle_specific_message_font_color", "Texte du message spécifique", "color", "#FFFFFF", None, "secondary"),
            ("subtitle_specific_message_font_size", "Taille du message spécifique", "size", "40px", None, None),
            ("subtitle_specific_message_border_size", "Contour du message spécifique", "size", "0px", None, None),
            ("subtitle_specific_message_border_color", "Couleur contour message spécifique", "color", "#000000", None, "border"),
            ("subtitle_specific_message_background_color", "Fond du message spécifique", "color", "#008B8B", None, "primary"),
        ],
        "languages": [
            ("flag_border_size", "Contour des drapeaux", "size", "0px", None, None),
            ("flag_border_color", "Couleur contour des drapeaux", "color", "#FFFFFF", None, "border"),
        ],
    },
}

for _page, _components in _ADDITIONAL_CSS_FIELDS.items():
    for _component_id, _fields in _components.items():
        for key, label, field_type, default, group, role in _fields:
            field = {"key": key, "label": label, "type": field_type,
                     "default": default, "optional": True}
            if group:
                field["group"] = group
            if role:
                field["role"] = role
            ADAPTERS[_page]["components"][_component_id]["css"].append(field)


# L'ancien composant patient « Boutons » regroupait 74 contrôles sans tenir
# compte de l'étape affichée. Les métadonnées ci-dessous conservent un payload
# rétrocompatible tout en ne présentant que les réglages utiles au scénario.
_PATIENT_CONFIG_SCENARIOS = {
    "page_patient_button_shape": ["home"],
    "page_patient_children_button_shape": ["children"],
    "page_patient_circle_image_above": ["home", "children"],
    "page_patient_direct_print": ["home", "validation"],
    "page_patient_qrcode_display": ["validation", "conclusion"],
    "page_patient_qrcode_web_page": ["validation", "conclusion"],
    "page_patient_qrcode_data": ["validation", "conclusion"],
    "page_patient_qrcode_display_specific_message": ["validation", "conclusion"],
    "page_patient_display_button_scan": ["validation"],
    "page_patient_display_scan_explanation": ["validation"],
    "page_patient_print_after_scan": ["conclusion"],
    "page_patient_print_after_print": ["conclusion"],
    "page_patient_end_timer": ["conclusion"],
    "page_patient_display_conclusion": ["conclusion"],
    "page_patient_validation_message": ["validation"],
    "page_patient_interface_validate_print": ["validation"],
    "page_patient_button_print_ticket_display_picture": ["validation"],
    "page_patient_button_print_ticket_picture": ["validation"],
    "page_patient_interface_validate_scan": ["validation"],
    "page_patient_interface_scan_explanation": ["validation"],
    "page_patient_interface_validate_cancel": ["validation"],
    "page_patient_button_cancel_display_picture": ["validation"],
    "page_patient_button_cancel_picture": ["validation"],
    "page_patient_confirmation_message": ["conclusion"],
    "page_patient_interface_done_print": ["conclusion"],
    "page_patient_interface_done_extend": ["conclusion"],
    "page_patient_interface_done_back": ["conclusion"],
}
for _field in ADAPTERS["patient"]["components"]["buttons"]["config"]:
    _field["scenarios"] = _PATIENT_CONFIG_SCENARIOS[_field["key"]]

for _field in ADAPTERS["patient"]["components"]["buttons"]["css"]:
    _key = _field["key"]
    if _key == "patient_secondary_color":
        _field["scenarios"] = ["home", "validation", "conclusion", "print_error"]
    elif _key.startswith("circle_"):
        _field["scenarios"] = ["home", "children"]
        _field["visible_when"] = _shape_condition(["circle"])
    elif _key.startswith("square_button_"):
        _field["scenarios"] = ["home", "children"]
        _field["visible_when"] = _shape_condition(["square"])
    elif _key.startswith("validation_button_"):
        _field["scenarios"] = ["validation", "conclusion", "print_error"]
    elif _key.startswith("validation_text_") or _key.startswith("scan_explanation_"):
        _field["scenarios"] = ["validation"]
    elif _key.startswith("confirmation_text_"):
        _field["scenarios"] = ["conclusion"]

_PRINT_ERROR_FIELD_SCENARIOS = {
    "page_patient_print_fail_behavior": ["print_error"],
    "page_patient_print_fail_show_retry": ["print_error"],
    "page_patient_print_fail_show_staff": ["print_error"],
    "page_patient_print_fail_abandon_timer": ["print_error"],
    "page_patient_print_uncertain_timer": ["print_uncertain"],
    "page_patient_print_uncertain_timeout_action": ["print_uncertain"],
    "page_patient_interface_printing": ["printing"],
    "page_patient_interface_print_failed": ["print_error"],
    "page_patient_interface_no_ticket": ["print_error"],
    "page_patient_interface_print_failed_staff": ["print_error"],
    "page_patient_interface_staff_called": ["staff_called"],
    "page_patient_interface_print_uncertain": ["print_uncertain"],
    "page_patient_interface_retry": ["print_error"],
    "page_patient_interface_ticket_received": ["print_uncertain"],
    "page_patient_interface_ticket_missing": ["print_uncertain"],
    "page_patient_interface_call_staff": ["print_error"],
}
for _field in ADAPTERS["patient"]["components"]["print_error"]["config"]:
    _field["scenarios"] = _PRINT_ERROR_FIELD_SCENARIOS[_field["key"]]


#: Palette : fond, surface, texte, principale, douce, texte sur principale,
#: accent, texte sur accent. L'accent colore les appels en cours : une teinte
#: proche de la principale mais distincte du bandeau, avec un texte qui garde
#: un contraste d'au moins 4,5:1 (Ardoise : menthe plus soutenue, la
#: principale étant déjà très claire).
_BUILTIN_THEMES = (
    ("officine", "Officine", "Clair et rassurant — recommandé pour commencer.",
     ("#F5F8F7", "#FFFFFF", "#18332F", "#176B52", "#E5F2EC", "#FFFFFF", "#1C8465", "#FFFFFF")),
    ("lisibilite", "Lisibilité renforcée", "Textes agrandis ; galerie masquée sur l’affichage si vous appliquez la disposition.",
     ("#FFFFFF", "#FFFFFF", "#111827", "#123B63", "#FFF1B8", "#FFFFFF", "#1E62A4", "#FFFFFF")),
    ("sauge-lin", "Sauge & Lin", "Des tons naturels et chaleureux, sans sacrifier la lisibilité.",
     ("#FAF7F0", "#FFFEFA", "#29372F", "#365C46", "#E8EDE3", "#FFFFFF", "#497C5E", "#FFFFFF")),
    ("bleu-horizon", "Bleu Horizon", "Une présentation claire et structurée, dans des tons bleus.",
     ("#F3F6FA", "#FFFFFF", "#172B4D", "#245EA8", "#E7EFFA", "#FFFFFF", "#2D75D2", "#FFFFFF")),
    ("ardoise", "Ardoise", "Variante sombre — à tester sur place selon l’éclairage et les reflets.",
     ("#17212B", "#243342", "#F3F6FA", "#7EDDB5", "#243342", "#17212B", "#33C286", "#17212B")),
    ("classique", "Classique", "Reprend les réglages actuels de l’installation — grands textes blancs sur bandeaux vert canard.",
     ("#B6F5F5", "#008B8B", "#FFFFFF", "#006666", "#5FB4B4", "#FFFFFF", "#008B8B", "#FFFFFF")),
)

#: Second style d'appel proposé sur l'écran d'annonce : une couleur
#: complémentaire de la principale, plus visible de loin que la teinte
#: « discrète » de la palette (texte ≥ 4,5:1). Un thème absent d'ici (Classique,
#: reproduction exacte d'une configuration) n'offre pas le choix.
_BUILTIN_VIVID_ACCENTS = {
    "officine": ("#B9D44A", "#18332F"),  # vert anis, texte foncé du thème
    "lisibilite": ("#FFD24D", "#111827"),
    "sauge-lin": ("#A4502E", "#FFFFFF"),
    "bleu-horizon": ("#C2410C", "#FFFFFF"),
    "ardoise": ("#F2B84B", "#17212B"),
}

#: Style d'appel présélectionné par thème (« discreet » si absent).
_BUILTIN_DEFAULT_CALL_STYLE = {}


def _call_outline(foreground):
    """Contour des numéros appelés : 5px (couleur de texte foncée du thème)
    détoure un texte blanc, plus net de loin et sous les reflets ; un texte
    foncé n'en a pas besoin — le contour l'empâterait."""
    return "5px" if foreground.upper() == "#FFFFFF" else "0px"


def _call_styles(slug, accent, on_accent):
    """Choix « Appels » d'un thème d'annonce : discret (accent de la palette,
    celui de l'instantané) ou bien visible (couleur complémentaire)."""
    vivid = _BUILTIN_VIVID_ACCENTS.get(slug)
    if vivid is None:
        return None
    return [
        {"id": "discreet", "label": "Discrets",
         "css": {"calling_background_color": accent, "calling_font_color": on_accent,
                 "calling_font_border_size": _call_outline(on_accent)}},
        {"id": "vivid", "label": "Bien visibles",
         "css": {"calling_background_color": vivid[0], "calling_font_color": vivid[1],
                 "calling_font_border_size": _call_outline(vivid[1])}},
    ]


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
            "title_bottom_border_color": "#000000",
            "title_background_color": "#006666",
            "title_background_height": "200px",
            "subtitle_font_color": "#FFFFFF",
            "subtitle_font_size": "75px",
            "subtitle_font_border_size": "0px",
            "subtitle_font_border_color": "#000000",
            "subtitle_top_border_color": "#000000",
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
            "ongoing_border_color": "#000000",
            "next_patients_font_color": "#FFFFFF",
            "next_patients_font_size": "90px",
            "next_patients_font_border_size": "2px",
            "next_patients_font_border_color": "#000000",
            "next_patients_border_color": "#000000",
            "next_patients_background_color": "#006666",
        },
        "patient": {
            "patient_secondary_color": "#B6F5F5",
            "patient_title_font_size": "80px",
            "patient_title_font_color": "#FFFFFF",
            "patient_title_border_size": "2px",
            "patient_title_border_color": "#000000",
            "patient_title_bottom_border_color": "#000000",
            "patient_title_background_color": "#008B8B",
            "patient_title_background_height": "150px",
            "circle_button_size": "230px",
            "circle_button_color": "#008B8B",
            "circle_button_border_size": "0px",
            "circle_button_border_color": "#000000",
            "circle_button_image_size": "215px",
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
            "print_error_font_color": "#008B8B",
            "print_error_font_size": "50px",
            "print_error_border_size": "0px",
            "print_error_border_color": "#000000",
            "print_error_background_color": "#B6F5F5",
            "print_error_number_size": "80px",
            "flag_size": "100px",
            "flag_border_size": "0px",
            "flag_border_color": "#FFFFFF",
            "subtitle_font_size": "60px",
            "subtitle_font_color": "#FFFFFF",
            "subtitle_border_size": "2px",
            "subtitle_border_color": "#000000",
            "subtitle_top_border_color": "#000000",
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
        background, surface, text, primary, soft, on_primary, accent, on_accent = colors
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
            # Hiérarchie à distance : l'appel domine, le reste l'accompagne.
            block("title", on_primary, primary, "90px" if large else "80px")
            css.update(title_background_height="160px" if large else "140px",
                       subtitle_font_color=on_primary,
                       subtitle_font_size="56px" if large else "48px")
            block("calling", on_accent, accent, "140px" if large else "120px")
            css["calling_font_border_size"] = _call_outline(on_accent)
            block("text_up", text, surface, "64px" if large else "56px")
            block("text_down", text, surface, "96px" if large else "84px")
            block("ongoing", text, soft, "66px" if large else "56px")
            block("next_patients", text, soft, "70px" if large else "60px")
        elif page == "patient":
            block("patient_title", on_primary, primary, "72px" if large else "64px")
            block("subtitle", on_primary, primary, "37px" if large else "33px")
            css.update(patient_title_background_height="150px" if large else "140px",
                       subtitle_background_height="115px" if large else "100px",
                       circle_button_size="170px" if large else "150px", circle_button_color=primary,
                       circle_button_text_color=text,
                       circle_button_text_size="36px" if large else "32px",
                       circle_button_image_size="180px" if large else "160px",
                       square_button_width="560px" if large else "520px",
                       square_button_height="175px" if large else "160px", square_button_color=primary,
                       square_button_text_color=on_primary,
                       square_button_text_size="42px" if large else "38px",
                       # Images bornées par la hauteur du bouton : à 200px
                       # elles débordaient du bouton.
                       square_button_image_size="120px" if large else "110px",
                       # « Retour » / « Annuler » : secondaire, donc plus petit
                       # que les boutons d'activité qu'il chevauchait.
                       square_cancel_button_width="330px" if large else "300px",
                       square_cancel_button_height="100px" if large else "90px",
                       square_cancel_button_color=primary, square_cancel_button_text_color=on_primary,
                       square_cancel_button_text_size="34px" if large else "30px",
                       square_cancel_button_image_size="62px" if large else "56px",
                       validation_button_color=primary,
                       validation_button_text_color=on_primary,
                       validation_button_text_size="38px" if large else "34px",
                       validation_button_width="560px" if large else "520px",
                       validation_button_height="120px" if large else "110px",
                       validation_button_picture_size="62px" if large else "56px",
                       validation_text_font_size="50px" if large else "44px",
                       confirmation_text_font_size="50px" if large else "44px",
                       scan_explanation_font_size="36px" if large else "32px",
                       print_error_font_size="46px" if large else "40px",
                       print_error_number_size="104px" if large else "96px",
                       subtitle_no_activity_font_size="40px" if large else "36px",
                       subtitle_specific_message_font_size="40px" if large else "36px",
                       flag_size="80px" if large else "72px")
            # Le texte des boutons n'a pas de fond propre : sans cela, la règle
            # générique (« surface ») poserait un bandeau blanc derrière chaque
            # libellé de bouton en appliquant un thème.
            for prefix in ("circle_button", "square_button", "square_cancel_button", "validation_button"):
                css[f"{prefix}_text_background_color"] = "transparent"
        else:
            block("phone_title", on_primary, primary, "22px" if large else "20px")
            css.update(phone_title_font_weight="700",
                       phone_title_background_height="18px" if large else "16px")
            # Texte courant par défaut ; les lignes clés sont réglées ensuite.
            for prefix in ("phone_line", "phone_your_turn_line"):
                for index in range(1, 7):
                    block(f"{prefix}{index}", text, surface, "20px" if large else "18px")
                    css[f"{prefix}{index}_background_height"] = "6px"
                    css[f"{prefix}{index}_font_weight"] = "400"
            # Attente : libellé discret, numéro dominant, demande en gras.
            for index in (1, 3):
                css[f"phone_line{index}_font_size"] = "22px" if large else "20px"
            css.update(phone_line2_font_size="72px" if large else "64px",
                       phone_line2_font_weight="800",
                       phone_line4_font_size="28px" if large else "26px",
                       phone_line4_font_weight="700")
            # Appel : bandeau « votre tour », numéro dominant, consigne.
            block("phone_your_turn_line1", on_primary, primary, "38px" if large else "34px")
            css.update(phone_your_turn_line1_font_weight="700",
                       phone_your_turn_line1_background_height="18px",
                       phone_your_turn_line2_font_size="72px" if large else "64px",
                       phone_your_turn_line2_font_weight="800",
                       phone_your_turn_line3_font_size="26px" if large else "24px",
                       phone_your_turn_line3_font_weight="600")
            block("phone_specific_message", text, soft, "20px" if large else "18px")
            css["phone_specific_message_background_height"] = "54px" if large else "48px"
            # Texte courant : graisse normale (la règle générique des nombres
            # donnerait 600).
            css["phone_specific_message_font_weight"] = "400"
            # Attente, fin de parcours et activité fermée : texte simple sur
            # le fond de page — pas de pavé coloré par défaut.
            for prefix in ("phone_waiting", "phone_journey_end", "phone_closed"):
                block(prefix, text, "transparent", "24px" if large else "22px")
                css[f"{prefix}_font_weight"] = "400"
                css[f"{prefix}_background_height"] = "16px"
                css[f"{prefix}_border_color"] = "#000000"

        css.update(overrides.get(page, {}))

        layout = default_layout(page)
        for item in layout.values():
            item.pop("visible")
            item.update(span=12, alignment="center")
        if page == "announce" and large:
            layout["gallery"]["visible"] = False
        theme = {
            "id": f"builtin-{slug}", "page": page, "name": name, "description": description,
            "builtin": True, "recommended": slug == "officine",
            "colors": list(colors[:5]) + [colors[6]],
            "snapshot": {"css": css, "layout": layout, "config": {}},
        }
        call_styles = _call_styles(slug, accent, on_accent) if page == "announce" else None
        if call_styles:
            default_style = _BUILTIN_DEFAULT_CALL_STYLE.get(slug, "discreet")
            theme["call_styles"] = call_styles
            theme["default_call_style"] = default_style
            css.update(next(style["css"] for style in call_styles if style["id"] == default_style))
        themes.append(theme)
    return themes


def enabled_pages():
    raw = current_app.config.get("PAGE_EDITOR_ENABLED_PAGES", "announce,patient,phone")
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
    # Chaque champ couleur porte explicitement son rôle : aucune inférence par
    # le nom de clé. Les champs sans « role » (ex. les fonds de texte de
    # bouton, « transparent » par défaut) n'appartiennent à aucun rôle — les
    # inclure repeindrait un pavé coloré derrière chaque libellé.
    keys_by_role = {role["id"]: [] for role in _PALETTE_ROLES}
    for component in adapter["components"].values():
        for field in component["css"]:
            if field["type"] == "color" and field.get("role") in keys_by_role:
                keys_by_role[field["role"]].append(field["key"])
    return [
        {**role, "keys": sorted(keys_by_role[role["id"]])}
        for role in _PALETTE_ROLES
        if keys_by_role[role["id"]]
    ]


def _normalize_css_value(value, field_type):
    """Normalise une valeur d'apparence : préfixe ``#`` aux couleurs hex
    historiques saisies sans lui et passe les hexadécimaux en minuscules.

    La casse compte pour la comparaison de ``payload_diff`` : les thèmes et
    les palettes écrivent ``#008B8B`` alors que les champs couleur saisissent
    ``#008b8b`` — sans normalisation, la même couleur ressortait comme un
    changement.
    """
    if isinstance(value, str) and field_type == "color":
        if re.fullmatch(r"[0-9A-Fa-f]{3,8}", value):
            value = f"#{value}"
        if re.fullmatch(r"#[0-9A-Fa-f]{3,8}", value):
            return value.lower()
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

#: Variables « parentes » du mode avancé : aucune règle CSS ne les consomme,
#: mais elles servent d'entrées de cascade dans ``admin_colors.js`` et de
#: rôles dans la copie de palettes (``COLOR_PAGE_ROLES``). À la publication,
#: chacune est réalignée sur le représentant choisi ci-dessous — une variable
#: gérée par l'éditeur qui incarne le même rôle — pour ne pas rester périmée.
_PARENT_CSS_REPRESENTATIVES = {
    "announce": {
        "announce_main_color": "title_background_color",
        "announce_third_color": "title_font_color",
        "announce_fourth_color": "ongoing_background_color",
        "announce_border_color": "title_bottom_border_color",
    },
    "patient": {
        "patient_main_color": "patient_title_background_color",
        "patient_third_color": "patient_title_font_color",
        "patient_border_color": "patient_title_border_color",
        "patient_button_border_color": "square_button_border_color",
    },
    "phone": {
        "phone_main_color": "phone_title_background_color",
        "phone_third_color": "phone_line1_font_color",
        "phone_border_color": "phone_title_border_color",
        "phone_lines_font_size": "phone_line2_font_size",
        "phone_lines_font_weight": "phone_line2_font_weight",
        "phone_your_turn_main_color": "phone_your_turn_line2_background_color",
        "phone_your_turn_third_color": "phone_your_turn_line2_font_color",
        "phone_your_turn_border_color": "phone_your_turn_line2_border_color",
        "phone_your_turn_lines_font_size": "phone_your_turn_line2_font_size",
        "phone_your_turn_lines_font_weight": "phone_your_turn_line2_font_weight",
    },
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


#: Lecteurs de valeurs publiées propres à une page : ``page -> fonction``
#: renvoyant ``{clé: valeur}``. Ils priment sur ``app.config`` quand la
#: vérité est ailleurs — ex. la forme des boutons patient, enregistrée bouton
#: par bouton (cf. routes.admin_page_editor). Enregistrés par la couche
#: routes : ce module reste sans dépendance aux modèles.
_PUBLISHED_CONFIG_READERS = {}


def register_published_config_reader(page, reader):
    _PUBLISHED_CONFIG_READERS[page] = reader


def _published_config(page, keys):
    values = {}
    for key in keys:
        spec = get_spec(key)
        values[key] = current_app.config.get(spec.config_name) if spec else None
    reader = _PUBLISHED_CONFIG_READERS.get(page)
    if reader is not None and keys:
        values.update({
            key: value for key, value in reader(values).items()
            if key in values and value is not None
        })
    return values


def current_payload(page):
    adapter = ADAPTERS[page]
    config_values = _published_config(page, _managed_keys(adapter, "config"))
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


def _factory_json(filename):
    """Lit un JSON « valeurs d'usine » de ``static/json`` — les mêmes
    fichiers qu'``init_restore`` charge au démarrage. Fichier absent ou
    contenu invalide → ``{}`` : les valeurs d'usine deviennent simplement
    indisponibles dans l'éditeur, jamais bloquantes."""
    path = os.path.join(
        os.path.dirname(__file__), "static", "json", filename)
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        logger.warning("Fichier de valeurs d'usine illisible : %s", filename)
        return {}
    return data if isinstance(data, dict) else {}


def factory_payload(page):
    """Valeurs d'usine de la page, sous la même forme qu'un payload.

    ``layout`` reprend la disposition par défaut ; ``config`` et ``css``
    viennent des JSON d'usine (et non de la configuration publiée, qui peut
    avoir été personnalisée). Une clé de contenu absente du JSON vaut
    ``None`` — le client n'affiche alors aucun bouton de réinitialisation.
    Les couleurs sont normalisées comme à la validation (le fichier
    annonce écrit ``#008B8B`` en majuscules) pour éviter les faux diffs.
    """
    adapter = ADAPTERS[page]
    factory_config = (
        _factory_json("default_config.json").get("configurations") or {})
    factory_css = (
        _factory_json(f"default_{adapter['css_source']}_css_variables.json")
        .get("variables") or {})
    css_values = {}
    for component in adapter["components"].values():
        for field in component["css"]:
            css_values[field["key"]] = _normalize_css_value(
                factory_css.get(field["key"], field.get("default")),
                field["type"],
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "page": page,
        "layout": default_layout(page),
        "config": {
            key: factory_config.get(key)
            for key in _managed_keys(adapter, "config")
        },
        "css": css_values,
    }


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
    completed.update(_published_config(
        page, _managed_keys(ADAPTERS[page], "config") - set(completed)))
    return completed


def complete_layout(page, layout):
    """Ajoute à ``layout`` les composants apparus après l'enregistrement du
    brouillon, avec leur disposition par défaut (visibles, en fin de zone).
    Sans cela l'éditeur et ``validate_payload`` exigeraient des entrées que
    les anciens brouillons ne pouvaient pas contenir."""
    completed = dict(layout) if isinstance(layout, dict) else {}
    for component_id, item in default_layout(page).items():
        completed.setdefault(component_id, item)
    return completed


def complete_css(page, css):
    """Idem pour les variables d'apparence : les champs ajoutés après coup
    (optionnels) sont complétés avec la valeur publiée ou leur défaut."""
    adapter = ADAPTERS[page]
    manager = getattr(current_app, "css_variable_manager", None)
    completed = dict(css) if isinstance(css, dict) else {}
    for component in adapter["components"].values():
        for field in component["css"]:
            if field["key"] not in completed:
                published = (
                    manager.get_variable(adapter["css_source"], field["key"])
                    if manager else None
                )
                completed[field["key"]] = _normalize_css_value(
                    published or field.get("default"), field["type"],
                )
            else:
                # Même normalisation qu'à la validation (casse hex…) : un
                # instantané enregistré avant son introduction ne doit pas
                # ressortir comme « modifié » dans le diff publié/révision.
                completed[field["key"]] = _normalize_css_value(
                    completed[field["key"]], field["type"],
                )
    return completed


def _is_button_image_name(value):
    """Nom d'image accepté par les champs ``type: "image"`` : ``""``
    (aucune image) ou un nom de fichier simple — sans ``/``, ``\\``,
    ``..`` ni ``:`` — dont l'extension figure dans
    ``ALLOWED_IMAGE_EXTENSIONS``. Le fichier doit exister dans
    ``static/images/buttons`` ; l'éditeur ne propose que les fichiers
    réellement présents (pas d'upload ici : le dépôt reste au mode
    avancé)."""
    if not value:
        return True
    if any(sep in value for sep in ("/", "\\", "..", ":")):
        return False
    return ("." in value
            and value.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS)


def validate_payload(page, payload):
    adapter = ADAPTERS[page]
    # Libellés « Composant › Champ (clé) » pour les messages d'erreur : la clé
    # technique reste entre parenthèses (journalisation, tests, support).
    _field_labels, _field_components = _field_label_maps(adapter)

    def _label(key):
        label = _field_labels.get(key)
        if label is None:
            return key
        component = _field_components.get(key)
        human = f"{component} › {label}" if component else label
        return f"{human} ({key})"

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
    # Bornes déclarées sur les champs (« min » / « max »), croisées avec
    # spec.min_value / spec.max_value du registre : une largeur de galerie
    # nulle ou un délai négatif ne doit pas pouvoir être publié.
    config_minimums = {field["key"]: field["min"]
                       for component in adapter["components"].values()
                       for field in component["config"] if field.get("min") is not None}
    config_maximums = {field["key"]: field["max"]
                       for component in adapter["components"].values()
                       for field in component["config"] if field.get("max") is not None}
    config_types = {field["key"]: field.get("type", "text")
                    for component in adapter["components"].values()
                    for field in component["config"]}
    normalized_config = {}
    for key, value in config_values.items():
        spec = get_spec(key)
        if spec is None:
            raise ValueError("Paramètre de contenu inconnu.")
        if spec.value_type == "value_bool":
            if not isinstance(value, bool):
                raise ValueError(f"Valeur booléenne invalide pour {_label(key)}.")
        elif spec.value_type == "value_int":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"Valeur numérique invalide pour {_label(key)}.")
            minimum = max((v for v in (config_minimums.get(key), spec.min_value)
                           if v is not None), default=None)
            maximum = min((v for v in (config_maximums.get(key), spec.max_value)
                           if v is not None), default=None)
            # Seulement pour une valeur modifiée : une valeur déjà publiée
            # (même atypique) ne doit pas bloquer la publication du reste.
            published = current_app.config.get(spec.config_name)
            if minimum is not None and value < minimum and value != published:
                raise ValueError(
                    f"Valeur trop petite pour {_label(key)} (minimum {minimum}).")
            if maximum is not None and value > maximum and value != published:
                raise ValueError(
                    f"Valeur trop grande pour {_label(key)} (maximum {maximum}).")
        else:
            # Colonne cible du paramètre : value_str est limitée à 200
            # caractères, seule value_text (textes longs) monte à 6000.
            max_length = 6000 if spec.value_type == "value_text" else 200
            if not isinstance(value, str):
                raise ValueError(f"Texte invalide pour {_label(key)}.")
            if len(value) > max_length:
                raise ValueError(
                    f"Texte trop long pour {_label(key)} (maximum {max_length} caractères).")
            if "<" in value or ">" in value:
                raise ValueError(f"Le HTML n'est pas autorisé pour {_label(key)}.")
            check = validate_config_text(key, value)
            if not check["success"]:
                raise ValueError(str(check["value"]))
            value = check["value"]
            if config_types.get(key) == "image" and not _is_button_image_name(value):
                raise ValueError(
                    f"Image invalide pour {_label(key)} : nom de fichier attendu, "
                    "sans chemin ni extension non image.")
        if spec.allowed_values is not None and value not in spec.allowed_values:
            raise ValueError(f"Valeur non autorisée pour {_label(key)}.")
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
            raise ValueError(f"Valeur CSS invalide pour {_label(key)}.")
        stripped = value.strip()
        if not _is_valid_css_for_type(stripped, css_types[key]):
            raise ValueError(f"Valeur CSS inutilisable pour {_label(key)}.")
        normalized_css[key] = stripped

    raw_layout = payload.get("layout")
    if not isinstance(raw_layout, dict) or set(raw_layout) != set(adapter["components"]):
        raise ValueError("La structure des composants est incomplète.")
    normalized_layout = {}
    for component_id, raw in raw_layout.items():
        component = adapter["components"][component_id]
        component_label = f"{component['label']} ({component_id})"
        if not isinstance(raw, dict) or set(raw) != _LAYOUT_KEYS:
            raise ValueError("Structure de composant invalide.")
        zone = raw.get("zone")
        if zone not in component["zones"]:
            raise ValueError(f"Zone invalide pour {component_label}.")
        order = raw.get("order")
        span = raw.get("span")
        visible = raw.get("visible")
        alignment = raw.get("alignment")
        if not isinstance(order, int) or not 0 <= order <= 1000:
            raise ValueError(f"Ordre invalide pour {component_label}.")
        if not isinstance(span, int) or not 1 <= span <= 12:
            raise ValueError(f"Largeur invalide pour {component_label}.")
        if not isinstance(visible, bool) or alignment not in ALIGNMENTS:
            raise ValueError(f"Présentation invalide pour {component_label}.")
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


def _field_label_maps(adapter):
    """``(labels, composants)`` des champs config/css indexés par clé :
    partagés par ``payload_diff`` et le diff des changements externes."""
    field_labels = {}
    field_component = {}
    for component in adapter["components"].values():
        for section in ("config", "css"):
            for field in component[section]:
                field_labels[field["key"]] = field["label"]
                field_component[field["key"]] = component["label"]
    return field_labels, field_component


def payload_diff(page, working):
    """Différences libellées entre ``working`` et la version publiée.

    ``working`` doit être un payload déjà normalisé par ``validate_payload``.
    Le résultat sert au panneau « Comparer » et au récapitulatif affiché avant
    publication : chaque changement porte le libellé affiché dans l'inspecteur
    plutôt que la clé technique.
    """
    adapter = ADAPTERS[page]
    published = current_payload(page)
    field_labels, field_component = _field_label_maps(adapter)

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


def external_changes(page, base, current):
    """Réglages modifiés hors de l'éditeur entre ``base`` (instantané de la
    configuration au moment de la création du brouillon) et ``current``
    (configuration publiée actuelle), avec les libellés de l'inspecteur.

    ``base`` et ``current`` sont des payloads complets. Retourne ``None``
    quand l'instantané de référence est inconnu (brouillon créé avant son
    enregistrement, ou client désynchronisé au premier enregistrement).
    """
    if not isinstance(base, dict) or not isinstance(current, dict):
        return None
    adapter = ADAPTERS[page]
    field_labels, field_component = _field_label_maps(adapter)
    changes = []
    for section in ("config", "css"):
        base_values = base.get(section) or {}
        current_values = current.get(section) or {}
        for key in sorted(set(base_values) | set(current_values)):
            old = base_values.get(key)
            new = current_values.get(key)
            if old != new:
                changes.append({
                    "section": section,
                    "component": field_component.get(key),
                    "key": key,
                    "label": field_labels.get(key, key),
                    "old": old,
                    "new": new,
                })
    base_layout = base.get("layout") or {}
    current_layout = current.get("layout") or {}
    for component_id, component in adapter["components"].items():
        old_item = base_layout.get(component_id) or {}
        new_item = current_layout.get(component_id) or {}
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
    return changes


#: Décalage d'ordre entre deux zones du téléphone (l'ordre d'un composant est
#: borné à 1000). Repris à l'identique par page_editor_preview.js.
PHONE_ZONE_ORDER_STEP = 1000


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
        # La borne patient possède trois régions structurelles et un overlay.
        # Leur appliquer les largeurs/ordres du moteur générique transforme la
        # colonne plein écran en flex horizontal et agrandit le sélecteur de
        # langues sur toute la page. Seule leur visibilité est personnalisable.
        if page == "patient":
            continue
        try:
            order = max(0, min(1000, int(item.get("order", defaults[component_id]["order"]))))
            span = max(1, min(12, int(item.get("span", defaults[component_id]["span"]))))
        except (TypeError, ValueError):
            order = defaults[component_id]["order"]
            span = defaults[component_id]["span"]
        if page == "phone":
            # Les zones du téléphone partagent un seul conteneur flex : sans
            # décalage par zone, le message spécifique (pied, ordre 10)
            # s'intercalait entre la ligne 1 et la ligne 2 du contenu.
            order += PHONE_ZONE_ORDER_STEP * adapter["zones"].index(component["zone"])
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
        rules.extend([
            "#main{display:grid;grid-template-columns:minmax(0,1fr);"
            "grid-template-rows:auto minmax(0,1fr) auto;height:100%;"
            "min-height:0;align-content:stretch;align-items:stretch;overflow:hidden}",
            "#div_title_area{grid-column:1;grid-row:1;min-width:0}",
            "#div_buttons_parents{grid-column:1;grid-row:2;height:auto;"
            "min-height:0;min-width:0}",
            "#div_buttons_children{grid-column:1;grid-row:3;min-width:0}",
        ])
    elif page == "phone":
        rules.append(".container,#div_infos{display:flex;flex-flow:row wrap;align-content:flex-start;align-items:stretch}")
        # Le contenu suit toujours le titre (zone « header ») et occupe toute
        # la largeur : sans ordre propre il passait AVANT le titre.
        rules.append(f"#div_infos{{order:{PHONE_ZONE_ORDER_STEP};flex:1 1 100%;min-width:0}}")
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
            # Limite de saisie côté client, alignée sur la validation du
            # brouillon (colonne value_str = 200, value_text = 6000).
            field["max_length"] = 6000 if spec and spec.value_type == "value_text" else 200
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
        # Frise « parcours » (borne patient) : None pour les autres pages.
        "journey": public_journey(page, {
            field["key"]: field
            for component in components.values()
            for field in component["config"]
        }),
    }
