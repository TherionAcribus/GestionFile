"""Registre serveur des paramètres de configuration modifiables.

Point 1 (sécurisation des paramètres) — ce module est la **seule source de
vérité** décrivant les clés de configuration que le client peut modifier via
``/admin/update_switch``, ``/admin/update_input`` et ``/admin/update_select``.

Pour chaque clé autorisée, on déclare :

- ``config_name``      : nom de la clé dans ``app.config`` (majuscules) ;
- ``value_type``       : colonne de ``ConfigOption`` (``value_str`` /
                         ``value_int`` / ``value_bool``) ;
- ``permission``       : ressource de permission requise. La modification exige
                         qu'au moins un rôle de l'utilisateur porte
                         ``admin_<permission>`` à ``True`` ;
- ``kind``             : widget d'origine (``switch`` / ``input`` / ``select``),
                         indicatif ;
- ``validator``        : type de validation appliqué **côté serveur** —
                         ``bool`` / ``int`` / ``text`` / ``welcome`` /
                         ``before_call`` / ``after_call`` ;
- ``restart_required`` : ``True`` si le paramètre ne prend effet qu'après
                         redémarrage du serveur.

Règles de sécurité (cf. app.py) :

* une clé **absente** du registre est rejetée avec **HTTP 400** ;
* la **permission** associée à la clé est vérifiée avant toute écriture ;
* le type de validation provient du registre, **jamais** du paramètre
  ``check`` envoyé par le navigateur.

Le registre est volontairement indépendant de ``app.py`` (qui exige MySQL) afin
d'être importable et testable isolément.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Table canonique clé -> (nom dans app.config, colonne ConfigOption)
#
# C'est aussi la liste chargée au démarrage : ``app.load_configuration`` importe
# ``CONFIG_MAPPINGS`` (dérivé de cette table) pour repeupler ``app.config`` depuis
# la base. Garder les deux usages sur une seule table évite toute dérive entre
# « ce qui est chargé » et « ce qui est modifiable ».
# ---------------------------------------------------------------------------
_CONFIG_TYPES: dict[str, tuple[str, str]] = {
    "pharmacy_name": ("PHARMACY_NAME", "value_str"),
    "network_adress": ("NETWORK_ADRESS", "value_str"),
    "numbering_by_activity": ("NUMBERING_BY_ACTIVITY", "value_bool"),
    "start_rabbitmq": ("START_RABBITMQ", "value_bool"),
    "algo_activate": ("ALGO_IS_ACTIVATED", "value_bool"),
    "algo_overtaken_limit": ("ALGO_OVERTAKEN_LIMIT", "value_int"),
    "printer": ("PRINTER", "value_bool"),
    "printer_width": ("PRINTER_WIDTH", "value_int"),
    "add_paper": ("ADD_PAPER", "value_bool"),
    "admin_colors": ("ADMIN_COLORS", "value_str"),
    "announce_title": ("ANNOUNCE_TITLE", "value_str"),
    "announce_title_size": ("ANNOUNCE_TITLE_SIZE", "value_int"),
    "announce_subtitle": ("ANNOUNCE_SUBTITLE", "value_str"),
    "announce_text_up_patients": ("ANNOUNCE_TEXT_UP_PATIENTS", "value_str"),
    "announce_text_up_patients_display": ("ANNOUNCE_TEXT_UP_PATIENTS_DISPLAY", "value_str"),
    "announce_text_up_patients_size": ("ANNOUNCE_TEXT_UP_PATIENTS_SIZE", "value_int"),
    "announce_text_down_patients": ("ANNOUNCE_TEXT_DOWN_PATIENTS", "value_str"),
    "announce_text_down_patients_display": ("ANNOUNCE_TEXT_DOWN_PATIENTS_DISPLAY", "value_str"),
    "announce_text_down_patients_size": ("ANNOUNCE_TEXT_DOWN_PATIENTS_SIZE", "value_int"),
    "announce_sound": ("ANNOUNCE_SOUND", "value_bool"),
    "announce_alert": ("ANNOUNCE_ALERT", "value_bool"),
    "announce_alert_filename": ("ANNOUNCE_ALERT_FILENAME", "value_str"),
    "announce_style": ("ANNOUNCE_STYLE", "value_str"),
    "announce_player": ("ANNOUNCE_PLAYER", "value_str"),
    "announce_infos_display": ("ANNOUNCE_INFOS_DISPLAY", "value_bool"),
    "announce_infos_display_time": ("ANNOUNCE_INFOS_DISPLAY_TIME", "value_int"),
    "announce_infos_transition": ("ANNOUNCE_INFOS_TRANSITION", "value_str"),
    "announce_infos_gallery": ("ANNOUNCE_INFOS_GALLERY", "value_str"),
    "announce_gallery_folders": ("ANNOUNCE_GALLERY_FOLDERS", "value_str"),
    "announce_infos_mix_folders": ("ANNOUNCE_INFOS_MIX_FOLDERS", "value_bool"),
    "announce_infos_width": ("ANNOUNCE_INFOS_WIDTH", "value_int"),
    "announce_infos_height": ("ANNOUNCE_INFOS_HEIGHT", "value_int"),
    "announce_call_text": ("ANNOUNCE_CALL_TEXT", "value_str"),
    "announce_call_text_size": ("ANNOUNCE_CALL_TEXT_SIZE", "value_int"),
    "announce_call_text_transition": ("ANNOUNCE_CALL_TEXT_TRANSITION", "value_str"),
    "announce_ongoing_display": ("ANNOUNCE_ONGOING_DISPLAY", "value_bool"),
    "announce_ongoing_text": ("ANNOUNCE_ONGOING_TEXT", "value_str"),
    "announce_next_patients_display": ("ANNOUNCE_NEXT_PATIENTS_DISPLAY", "value_bool"),
    "announce_next_patients_text": ("ANNOUNCE_NEXT_PATIENTS_TEXT", "value_str"),
    "announce_next_patients_alignment": ("ANNOUNCE_NEXT_PATIENTS_ALIGNMENT", "value_str"),
    "announce_call_sound": ("ANNOUNCE_CALL_SOUND", "value_str"),
    "announce_call_translation": ("ANNOUNCE_CALL_TRANSLATION", "value_str"),
    "counter_order": ("COUNTER_ORDER", "value_str"),
    "music_volume": ("MUSIC_VOLUME", "value_int"),
    "music_announce_volume": ("MUSIC_ANNOUNCE_VOLUME", "value_int"),
    "music_announce_action": ("MUSIC_ANNOUNCE_ACTION", "value_str"),
    "music_spotify": ("MUSIC_SPOTIFY", "value_bool"),
    "music_spotify_user": ("MUSIC_SPOTIFY_USER", "value_str"),
    "music_spotify_key": ("MUSIC_SPOTIFY_KEY", "value_str"),
    "page_patient_disable_button": ("PAGE_PATIENT_DISABLE_BUTTON", "value_bool"),
    "page_patient_disable_default_message": ("PAGE_PATIENT_DISABLE_DEFAULT_MESSAGE", "value_str"),
    "page_patient_title": ("PAGE_PATIENT_TITLE", "value_str"),
    "page_patient_subtitle": ("PAGE_PATIENT_SUBTITLE", "value_str"),
    "page_patient_validation_message": ("PAGE_PATIENT_VALIDATION_MESSAGE", "value_str"),
    "page_patient_confirmation_message": ("PAGE_PATIENT_CONFIRMATION_MESSAGE", "value_str"),
    "page_patient_qrcode_display": ("PAGE_PATIENT_QRCODE_DISPLAY", "value_bool"),
    "page_patient_display_button_scan": ("PAGE_PATIENT_DISPLAY_BUTTON_SCAN", "value_bool"),
    "page_patient_display_scan_explanation": ("PAGE_PATIENT_DISPLAY_SCAN_EXPLANATION", "value_bool"),
    "page_patient_qrcode_web_page": ("PAGE_PATIENT_QRCODE_WEB_PAGE", "value_bool"),
    "page_patient_qrcode_data": ("PAGE_PATIENT_QRCODE_DATA", "value_str"),
    "page_patient_qrcode_display_specific_message": ("PAGE_PATIENT_QRCODE_DISPLAY_SPECIFIC_MESSAGE", "value_bool"),
    "page_patient_print_ticket_display": ("PAGE_PATIENT_PRINT_TICKET_DISPLAY", "value_bool"),
    "page_patient_end_timer": ("PAGE_PATIENT_END_TIMER", "value_int"),
    "page_patient_display_specific_message": ("PAGE_PATIENT_DISPLAY_SPECIFIC_MESSAGE", "value_bool"),
    "page_patient_direct_print": ("PAGE_PATIENT_DIRECT_PRINT", "value_bool"),
    "page_patient_display_translations": ("PAGE_PATIENT_DISPLAY_TRANSLATIONS", "value_bool"),
    "page_patient_interface_validate_print": ("PAGE_PATIENT_INTERFACE_VALIDATE_PRINT", "value_str"),
    "page_patient_interface_validate_scan": ("PAGE_PATIENT_INTERFACE_VALIDATE_SCAN", "value_str"),
    "page_patient_interface_scan_explanation": ("PAGE_PATIENT_INTERFACE_SCAN_EXPLANATION", "value_str"),
    "page_patient_interface_validate_cancel": ("PAGE_PATIENT_INTERFACE_VALIDATE_CANCEL", "value_str"),
    "page_patient_interface_done_print": ("PAGE_PATIENT_INTERFACE_DONE_PRINT", "value_str"),
    "page_patient_interface_done_extend": ("PAGE_PATIENT_INTERFACE_DONE_EXTEND", "value_str"),
    "page_patient_interface_done_back": ("PAGE_PATIENT_INTERFACE_DONE_BACK", "value_str"),
    "page_patient_print_after_scan": ("PAGE_PATIENT_PRINT_AFTER_SCAN", "value_bool"),
    "page_patient_print_after_print": ("PAGE_PATIENT_PRINT_AFTER_PRINT", "value_bool"),
    "page_patient_timer_activity_inactive": ("PAGE_PATIENT_TIMER_ACTIVITY_INACTIVE", "value_int"),
    "page_patient_button_print_ticket_display_picture": ("PAGE_PATIENT_BUTTON_PRINT_TICKET_DISPLAY_PICTURE", "value_bool"),
    "page_patient_button_print_ticket_picture": ("PAGE_PATIENT_BUTTON_PRINT_TICKET_PICTURE", "value_str"),
    "page_patient_button_cancel_display_picture": ("PAGE_PATIENT_BUTTON_CANCEL_DISPLAY_PICTURE", "value_bool"),
    "page_patient_button_cancel_picture": ("PAGE_PATIENT_BUTTON_CANCEL_PICTURE", "value_str"),
    "ticket_header": ("TICKET_HEADER", "value_str"),
    "ticket_header_printer": ("TICKET_HEADER_PRINTER", "value_str"),
    "ticket_message": ("TICKET_MESSAGE", "value_str"),
    "ticket_message_printer": ("TICKET_MESSAGE_PRINTER", "value_str"),
    "ticket_footer": ("TICKET_FOOTER", "value_str"),
    "ticket_footer_printer": ("TICKET_FOOTER_PRINTER", "value_str"),
    "ticket_display_specific_message": ("TICKET_DISPLAY_SPECIFIC_MESSAGE", "value_bool"),
    "mail_server": ("MAIL_SERVER", "value_str"),
    "mail_port": ("MAIL_PORT", "value_int"),
    "mail_username": ("MAIL_USERNAME", "value_str"),
    "mail_password": ("MAIL_PASSWORD", "value_str"),
    "mail_default_sender": ("MAIL_DEFAULT_SENDER", "value_str"),
    "mail_use_tls": ("MAIL_USE_TLS", "value_bool"),
    "mail_use_ssl": ("MAIL_USE_SSL", "value_bool"),
    "phone_center": ("PHONE_CENTER", "value_bool"),
    "phone_title": ("PHONE_TITLE", "value_str"),
    "phone_line1": ("PHONE_LINE1", "value_str"),
    "phone_line2": ("PHONE_LINE2", "value_str"),
    "phone_line3": ("PHONE_LINE3", "value_str"),
    "phone_line4": ("PHONE_LINE4", "value_str"),
    "phone_line5": ("PHONE_LINE5", "value_str"),
    "phone_line6": ("PHONE_LINE6", "value_str"),
    "phone_display_your_turn": ("PHONE_DISPLAY_YOUR_TURN", "value_bool"),
    "phone_your_turn_line1": ("PHONE_YOUR_TURN_LINE1", "value_str"),
    "phone_your_turn_line2": ("PHONE_YOUR_TURN_LINE2", "value_str"),
    "phone_your_turn_line3": ("PHONE_YOUR_TURN_LINE3", "value_str"),
    "phone_your_turn_line4": ("PHONE_YOUR_TURN_LINE4", "value_str"),
    "phone_your_turn_line5": ("PHONE_YOUR_TURN_LINE5", "value_str"),
    "phone_your_turn_line6": ("PHONE_YOUR_TURN_LINE6", "value_str"),
    "phone_display_specific_message": ("PHONE_DISPLAY_SPECIFIC_MESSAGE", "value_bool"),
    "cron_delete_patient_table_activated": ("CRON_DELETE_PATIENT_TABLE_ACTIVATED", "value_bool"),
    "cron_transfer_patient_to_history": ("CRON_TRANSFER_PATIENT_TO_HISTORY", "value_bool"),
    "cron_delete_patient_table_hour": ("CRON_DELETE_PATIENT_TABLE_HOUR", "value_str"),
    "cron_delete_announce_calls_activated": ("CRON_DELETE_ANNOUNCE_CALLS_ACTIVATED", "value_bool"),
    "cron_delete_announce_calls_hour": ("CRON_DELETE_ANNOUNCE_CALLS_HOUR", "value_str"),
    "security_login_admin": ("SECURITY_LOGIN_ADMIN", "value_bool"),
    "security_login_counter": ("SECURITY_LOGIN_COUNTER", "value_bool"),
    "security_login_screen": ("SECURITY_LOGIN_SCREEN", "value_bool"),
    "security_login_patient": ("SECURITY_LOGIN_PATIENT", "value_bool"),
    "security_remember_duration": ("SECURITY_REMEMBER_DURATION", "value_int"),
    "page_patient_print_fail_behavior": ("PAGE_PATIENT_PRINT_FAIL_BEHAVIOR", "value_str"),
    "page_patient_print_fail_show_retry": ("PAGE_PATIENT_PRINT_FAIL_SHOW_RETRY", "value_bool"),
    "page_patient_print_fail_show_staff": ("PAGE_PATIENT_PRINT_FAIL_SHOW_STAFF", "value_bool"),
    "page_patient_print_fail_abandon_timer": ("PAGE_PATIENT_PRINT_FAIL_ABANDON_TIMER", "value_int"),
    "page_patient_interface_printing": ("PAGE_PATIENT_INTERFACE_PRINTING", "value_str"),
    "page_patient_interface_print_failed": ("PAGE_PATIENT_INTERFACE_PRINT_FAILED", "value_str"),
    "page_patient_interface_retry": ("PAGE_PATIENT_INTERFACE_RETRY", "value_str"),
    "page_patient_interface_call_staff": ("PAGE_PATIENT_INTERFACE_CALL_STAFF", "value_str"),
    "page_patient_interface_staff_called": ("PAGE_PATIENT_INTERFACE_STAFF_CALLED", "value_str"),
    "page_patient_interface_no_ticket": ("PAGE_PATIENT_INTERFACE_NO_TICKET", "value_str"),
    "page_patient_interface_print_failed_staff": ("PAGE_PATIENT_INTERFACE_PRINT_FAILED_STAFF", "value_str"),
}


# Clés dont la valeur ne prend effet qu'après un redémarrage du serveur.
_RESTART_REQUIRED = {
    "network_adress",
    "start_rabbitmq",
}


# ---------------------------------------------------------------------------
# Clés SECRÈTES (point 5 — protection des secrets)
# ---------------------------------------------------------------------------
# Ces valeurs (mot de passe SMTP, clé d'API Spotify...) ne doivent JAMAIS :
#   * être renvoyées au navigateur (les templates n'affichent plus la valeur,
#     seulement un indicateur « défini / non défini ») ;
#   * être incluses dans une sauvegarde exportée (cf. backup_service) ;
#   * être écrites dans un journal ou un message d'erreur.
# Côté écriture (``/admin/update_input``), une valeur VIDE soumise pour une clé
# secrète signifie « conserver la valeur actuelle » (on n'efface pas un secret
# parce que le champ du formulaire est vide, cf. app.update_input).
SECRET_CONFIG_KEYS: frozenset[str] = frozenset({
    "mail_password",
    "music_spotify_key",
})


def is_secret_key(key) -> bool:
    """``True`` si ``key`` désigne une valeur secrète à ne jamais exposer."""
    return isinstance(key, str) and key in SECRET_CONFIG_KEYS


# ---------------------------------------------------------------------------
# Balises autorisées par famille de texte (repris des macros des templates).
# Détermine, côté serveur, quels marqueurs {P} {D} {H} {A} {N} {M} {C} sont
# acceptés dans un champ texte. Le client n'a plus voix au chapitre.
# ---------------------------------------------------------------------------
BALISE_LETTERS = {
    "welcome": "PDH",
    "before_call": "PDHAN",
    "after_call": "PDHANMC",
}

_WELCOME_KEYS = {
    "announce_title",
    "announce_subtitle",
    "announce_text_up_patients",
    "announce_text_down_patients",
}

_AFTER_CALL_KEYS = {
    "announce_call_sound",
    "announce_call_text",
    "announce_ongoing_text",
    "announce_next_patients_text",
}

_BEFORE_CALL_KEYS = {
    "page_patient_subtitle",
    "page_patient_validation_message",
    "page_patient_confirmation_message",
    "page_patient_qrcode_data",
    "phone_title",
    "phone_line1", "phone_line2", "phone_line3",
    "phone_line4", "phone_line5", "phone_line6",
    "phone_your_turn_line1", "phone_your_turn_line2", "phone_your_turn_line3",
    "phone_your_turn_line4", "phone_your_turn_line5", "phone_your_turn_line6",
}


# Ressources de permission connues (miroir des champs ``admin_*`` du modèle Role).
KNOWN_PERMISSIONS = {
    "security", "counter", "activity", "schedule", "algo", "translation",
    "options", "music_play", "music_options", "app", "queue", "stats",
    "staff", "phone", "announce", "patient", "gallery",
}


def _permission_for(key: str) -> str:
    """Ressource de permission requise pour modifier ``key``.

    Le rattachement suit la page d'administration où le paramètre est exposé
    (mêmes ressources que les décorateurs ``@require_permission`` existants)."""
    if key.startswith("security_"):
        return "security"
    if key.startswith("cron_"):
        return "schedule"
    if key.startswith("algo_"):
        return "algo"
    if key.startswith("announce_"):
        return "announce"
    if key.startswith("music_"):
        return "music_options"
    if key.startswith("phone_"):
        return "phone"
    if key.startswith(("page_patient_", "ticket_")):
        return "patient"
    if key in ("printer", "printer_width", "add_paper"):
        return "patient"
    if key == "counter_order":
        return "counter"
    if key == "admin_colors":
        return "options"
    # network_adress, pharmacy_name, start_rabbitmq, numbering_by_activity,
    # mail_* : page « Application ».
    return "app"


def _validator_for(key: str, value_type: str) -> str:
    """Type de validation serveur à appliquer à ``key``."""
    if value_type == "value_bool":
        return "bool"
    if value_type == "value_int":
        return "int"
    if key in _WELCOME_KEYS:
        return "welcome"
    if key in _AFTER_CALL_KEYS:
        return "after_call"
    if key in _BEFORE_CALL_KEYS:
        return "before_call"
    return "text"


def _kind_for(value_type: str) -> str:
    if value_type == "value_bool":
        return "switch"
    return "input"


@dataclass(frozen=True)
class ParamSpec:
    """Spécification d'un paramètre modifiable."""
    key: str
    config_name: str
    value_type: str          # value_str | value_int | value_bool
    permission: str
    validator: str           # bool | int | text | welcome | before_call | after_call
    kind: str                # switch | input | select
    restart_required: bool = False
    secret: bool = False     # valeur secrète (jamais exposée/exportée/journalisée)


def _build_registry() -> dict[str, ParamSpec]:
    registry: dict[str, ParamSpec] = {}
    for key, (config_name, value_type) in _CONFIG_TYPES.items():
        registry[key] = ParamSpec(
            key=key,
            config_name=config_name,
            value_type=value_type,
            permission=_permission_for(key),
            validator=_validator_for(key, value_type),
            kind=_kind_for(value_type),
            restart_required=key in _RESTART_REQUIRED,
            secret=key in SECRET_CONFIG_KEYS,
        )
    return registry


#: Registre complet clé -> ParamSpec (source de vérité pour les routes update_*).
PARAM_REGISTRY: dict[str, ParamSpec] = _build_registry()


#: Table {clé: (config_name, value_type)} pour ``app.load_configuration``.
CONFIG_MAPPINGS: dict[str, tuple[str, str]] = dict(_CONFIG_TYPES)


def get_spec(key):
    """Retourne le ``ParamSpec`` d'une clé, ou ``None`` si elle est inconnue."""
    if not isinstance(key, str):
        return None
    return PARAM_REGISTRY.get(key)


def is_known_key(key) -> bool:
    """``True`` si ``key`` est une clé de configuration autorisée."""
    return isinstance(key, str) and key in PARAM_REGISTRY
