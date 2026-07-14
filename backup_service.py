import json
import os
import base64
import shutil
import zipfile
from abc import ABC, abstractmethod
from datetime import datetime
from io import BytesIO
from flask import current_app
from path_security import UnsafePathError, safe_relative_path
from params_registry import SECRET_CONFIG_KEYS, is_secret_key
import config_sync
from models import (
    db, Pharmacist, Counter, Activity, ActivitySchedule, Weekday,
    AlgoRule, Button, ConfigOption, ConfigVersion,
    PatientCssVariable, AnnounceCssVariable, PhoneCssVariable,
    Language, Text, TextTranslation, TextInterface, Translation,
    DashboardCard,
    counters_activities, pharmacists_activities,
    activity_schedule_link, activity_schedule_weekday,
)

FORMAT_VERSION = "2.0"
# Format des sauvegardes ZIP (point 13) : manifeste JSON + images en fichiers
# binaires. Les sauvegardes ne contenant aucune image restent au format JSON
# « plat » historique (2.0) ; dès qu'une section d'images est incluse, l'export
# produit une archive 3.0 pour ne pas charger les images encodées base64 dans un
# énorme JSON.
ARCHIVE_FORMAT_VERSION = "3.0"
APP_NAME = "GestionFile"

# Versions de format acceptées à l'import (rejet strict des autres).
SUPPORTED_FORMAT_VERSIONS = {"2.0", "3.0"}

# Nom du manifeste et préfixe des fichiers binaires dans une archive 3.0.
MANIFEST_NAME = "manifest.json"
FILES_ARC_PREFIX = "files"

# Seules ces extensions d'images peuvent être écrites lors d'une restauration.
# Toute autre extension (scripts, exécutables, .py, etc.) est refusée.
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico"}

# ---------------------------------------------------------------------------
# Limites de sécurité pour les fichiers de sauvegarde importés
# ---------------------------------------------------------------------------
# Taille maximale du fichier JSON téléversé (avant décodage). Les images y sont
# encodées en base64, d'où une marge confortable, mais bornée.
MAX_BACKUP_FILE_BYTES = 64 * 1024 * 1024          # 64 Mo
# Nombre maximal de sections déclarées dans un fichier.
MAX_SECTIONS = 100
# Nombre maximal de fichiers image dans une section d'images.
MAX_IMAGE_FILES = 5000
# Taille maximale (après décodage base64) d'un fichier image individuel.
MAX_DECODED_FILE_BYTES = 16 * 1024 * 1024         # 16 Mo
# Taille totale maximale (après décodage) restaurée par section d'images.
MAX_TOTAL_DECODED_BYTES = 128 * 1024 * 1024       # 128 Mo

# ---------------------------------------------------------------------------
# Limites propres aux archives ZIP (format 3.0, point 13)
# ---------------------------------------------------------------------------
# Nombre maximal d'entrées dans l'archive (manifeste + fichiers binaires).
MAX_ARCHIVE_ENTRIES = 2 * MAX_IMAGE_FILES + 50
# Taille maximale du manifeste JSON contenu dans l'archive.
MAX_MANIFEST_BYTES = MAX_BACKUP_FILE_BYTES
# Taille cumulée maximale (non compressée) autorisée dans une archive : garde
# anti-bombe de décompression, vérifiée à l'ouverture avant toute extraction.
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2 * MAX_TOTAL_DECODED_BYTES
# Taille maximale du fichier ZIP téléversé (les images y sont brutes, donc plus
# compactes que le base64 : borne alignée sur le volume décompressé autorisé).
MAX_ARCHIVE_FILE_BYTES = MAX_ARCHIVE_UNCOMPRESSED_BYTES

# Au-delà de ce volume d'images, l'interface avertit que l'export sera lourd et
# ne coche pas les images par défaut (point 13).
EXPORT_IMAGE_WARNING_BYTES = 15 * 1024 * 1024      # 15 Mo


class BackupValidationError(Exception):
    """Erreur de validation d'un fichier de sauvegarde importé.

    Le message est **sûr pour l'affichage** : il ne contient jamais de détail
    technique interne (chemin, trace, exception d'origine)."""


def _safe_b64decode(b64content, *, max_bytes: int) -> bytes:
    """Décode du base64 en bornant la taille résultante.

    Rejette (ValueError) le contenu non-str, invalide, ou dont la taille décodée
    dépasse ``max_bytes`` — la borne est vérifiée sur la longueur encodée
    **avant** d'allouer, pour éviter une bombe de décompression."""
    if not isinstance(b64content, str):
        raise ValueError("contenu base64 non textuel")
    # 4 caractères base64 -> 3 octets ; on majore avant de décoder.
    if len(b64content) > (max_bytes // 3 + 1) * 4 + 4:
        raise ValueError("contenu image trop volumineux")
    decoded = base64.b64decode(b64content, validate=True)
    if len(decoded) > max_bytes:
        raise ValueError("contenu image trop volumineux")
    return decoded


def _validate_backup_structure(data) -> dict:
    """Valide **structurellement** un dict de sauvegarde (JSON plat ou manifeste
    d'archive). Commun aux formats 2.0 (JSON) et 3.0 (ZIP).

    Lève :class:`BackupValidationError` avec un message sûr pour l'utilisateur."""
    if not isinstance(data, dict):
        raise BackupValidationError("Structure de sauvegarde invalide.")

    if data.get("app") != APP_NAME:
        raise BackupValidationError("Ce fichier n'est pas une sauvegarde GestionFile.")

    if data.get("format_version") not in SUPPORTED_FORMAT_VERSIONS:
        raise BackupValidationError("Version de format de sauvegarde non prise en charge.")

    sections = data.get("sections")
    if not isinstance(sections, list) or not all(isinstance(s, str) for s in sections):
        raise BackupValidationError("Structure de sauvegarde invalide (sections).")
    if len(sections) > MAX_SECTIONS:
        raise BackupValidationError("Sauvegarde invalide : trop de sections.")

    payload = data.get("data")
    if not isinstance(payload, dict):
        raise BackupValidationError("Structure de sauvegarde invalide (données).")
    if len(payload) > MAX_SECTIONS:
        raise BackupValidationError("Sauvegarde invalide : trop de sections de données.")

    # Pour une archive 3.0, les sections d'images ne figurent pas dans ``data``
    # (leurs octets sont des fichiers binaires) ; ``binary_sections`` déclare
    # lesquelles. Champ optionnel, mais typé strictement s'il est présent.
    binary_sections = data.get("binary_sections")
    if binary_sections is not None:
        if not isinstance(binary_sections, list) or not all(
            isinstance(s, str) for s in binary_sections
        ):
            raise BackupValidationError("Structure de sauvegarde invalide (sections binaires).")
        if len(binary_sections) > MAX_SECTIONS:
            raise BackupValidationError("Sauvegarde invalide : trop de sections binaires.")

    return data


def load_and_validate_backup(raw) -> dict:
    """Parse et valide **structurellement** une charge utile de sauvegarde JSON.

    ``raw`` est ``bytes`` ou ``str`` (dont la taille a déjà été bornée par
    l'appelant). Renvoie le dict de sauvegarde validé. Lève
    :class:`BackupValidationError` avec un message sûr pour l'utilisateur (aucun
    détail interne) en cas de problème."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise BackupValidationError("Fichier illisible (encodage non UTF-8).")
    if not isinstance(raw, str):
        raise BackupValidationError("Fichier de sauvegarde invalide.")

    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        raise BackupValidationError("Fichier JSON illisible ou mal formé.")

    return _validate_backup_structure(data)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class BackupSection(ABC):
    """Base class for a backup section."""

    key: str = ""
    label: str = ""
    # Sections dont le contenu est constitué de fichiers binaires (images).
    # Dans une archive 3.0 (point 13), leurs octets sont stockés sous
    # ``files/<key>/…`` plutôt qu'encodés base64 dans le manifeste.
    is_binary: bool = False

    def __init__(self):
        # Cles secretes ecartees lors du dernier export_data (point 5). Les
        # sections de configuration l'alimentent ; ``export_sections`` l'agrege
        # pour prevenir l'utilisateur.
        self.excluded_secrets: list[str] = []

    @abstractmethod
    def export_data(self) -> dict | list:
        ...

    @abstractmethod
    def restore_data(self, data) -> None:
        ...


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------

class StaffSection(BackupSection):
    key = "staff"
    label = "Équipe"

    def export_data(self):
        pharmacists = Pharmacist.query.all()
        return [
            {
                "id": p.id,
                "name": p.name,
                "initials": p.initials,
                "language": p.language,
                "is_active": p.is_active,
                "activities": [a.id for a in p.activities],
            }
            for p in pharmacists
        ]

    def restore_data(self, data):
        db.session.execute(pharmacists_activities.delete())
        db.session.query(Pharmacist).delete()
        db.session.flush()
        for item in data:
            activities_ids = item.pop("activities", [])
            p = Pharmacist(**item)
            db.session.add(p)
            db.session.flush()
            for aid in activities_ids:
                a = db.session.get(Activity, aid)
                if a and a not in p.activities:
                    p.activities.append(a)
        db.session.commit()


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

class CounterSection(BackupSection):
    key = "counters"
    label = "Comptoirs"

    def export_data(self):
        counters = Counter.query.all()
        return [
            {
                "id": c.id,
                "name": c.name,
                "is_active": c.is_active,
                "non_actions": c.non_actions,
                "priority_actions": c.priority_actions,
                "staff_id": c.staff_id,
                "activities": [a.id for a in c.activities],
                "sort_order": c.sort_order,
                "auto_calling": c.auto_calling,
            }
            for c in counters
        ]

    def restore_data(self, data):
        db.session.execute(counters_activities.delete())
        db.session.query(Counter).delete()
        db.session.flush()
        with db.session.no_autoflush:
            for item in data:
                activities_ids = item.pop("activities", [])
                c = Counter(**item)
                db.session.add(c)
                db.session.flush()
                for aid in activities_ids:
                    a = db.session.get(Activity, aid)
                    if a and a not in c.activities:
                        c.activities.append(a)
        db.session.commit()


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------

class ActivitySection(BackupSection):
    key = "activities"
    label = "Activités"

    def export_data(self):
        activities = Activity.query.all()
        return [
            {
                "id": a.id,
                "name": a.name,
                "letter": a.letter,
                "inactivity_message": a.inactivity_message,
                "specific_message": a.specific_message,
                "notification": a.notification,
                "is_staff": a.is_staff,
                "staff_id": a.staff_id,
                "schedules": [s.id for s in a.schedules],
            }
            for a in activities
        ]

    def restore_data(self, data):
        db.session.execute(counters_activities.delete())
        db.session.execute(pharmacists_activities.delete())
        db.session.execute(activity_schedule_link.delete())
        db.session.query(Activity).delete()
        db.session.flush()
        with db.session.no_autoflush:
            for item in data:
                schedules_ids = item.pop("schedules", [])
                a = Activity()
                a.from_dict(item)
                db.session.add(a)
                db.session.flush()
                for sid in schedules_ids:
                    s = db.session.get(ActivitySchedule, sid)
                    if s and s not in a.schedules:
                        a.schedules.append(s)
        db.session.commit()


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------

class ScheduleSection(BackupSection):
    key = "schedules"
    label = "Plages horaires"

    def export_data(self):
        schedules = ActivitySchedule.query.all()
        return [s.to_dict() for s in schedules]

    def restore_data(self, data):
        db.session.execute(activity_schedule_weekday.delete())
        db.session.execute(activity_schedule_link.delete())
        db.session.query(ActivitySchedule).delete()
        db.session.flush()
        for item in data:
            weekdays_ids = item.pop("weekdays", [])
            activities_ids = item.pop("activities", [])
            s = ActivitySchedule()
            s.from_dict(item)
            db.session.add(s)
            db.session.flush()
            for wid in weekdays_ids:
                w = db.session.get(Weekday, wid)
                if w:
                    s.weekdays.append(w)
            for aid in activities_ids:
                a = db.session.get(Activity, aid)
                if a:
                    s.activities.append(a)
        db.session.commit()


# ---------------------------------------------------------------------------
# AlgoRules
# ---------------------------------------------------------------------------

class AlgoRuleSection(BackupSection):
    key = "algorules"
    label = "Règles algorithme"

    def export_data(self):
        rules = AlgoRule.query.all()
        return [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "activity_id": r.activity_id,
                "min_patients": r.min_patients,
                "max_patients": r.max_patients,
                "max_overtaken": r.max_overtaken,
                "start_time": r.start_time.strftime("%H:%M:%S"),
                "end_time": r.end_time.strftime("%H:%M:%S"),
                "days_of_week": r.days_of_week,
                "priority_level": r.priority_level,
            }
            for r in rules
        ]

    def restore_data(self, data):
        db.session.query(AlgoRule).delete()
        db.session.flush()
        with db.session.no_autoflush:
            for item in data:
                start_time = datetime.strptime(item.pop("start_time"), "%H:%M:%S").time()
                end_time = datetime.strptime(item.pop("end_time"), "%H:%M:%S").time()
                item["start_time"] = start_time
                item["end_time"] = end_time
                r = AlgoRule(**item)
                db.session.add(r)
        db.session.commit()


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------

class ButtonSection(BackupSection):
    key = "buttons"
    label = "Boutons patient"

    def export_data(self):
        buttons = Button.query.all()
        return [
            {
                "id": b.id,
                "by_user": b.by_user,
                "code": b.code,
                "is_parent": b.is_parent,
                "label": b.label,
                "label_en": b.label_en,
                "is_active": b.is_active,
                "is_present": b.is_present,
                "shape": b.shape,
                "image_url": b.image_url,
                "background_color": b.background_color,
                "text_color": b.text_color,
                "sort_order": b.sort_order,
                "activity_id": b.activity_id,
                "parent_button_id": b.parent_button_id,
            }
            for b in buttons
        ]

    def restore_data(self, data):
        # Supprimer enfants puis parents
        db.session.query(Button).filter(Button.parent_button_id.isnot(None)).delete(synchronize_session=False)
        db.session.query(Button).filter(Button.parent_button_id.is_(None)).delete(synchronize_session=False)
        db.session.flush()

        # Restaurer parents d'abord
        for item in data:
            if item.get("parent_button_id") is None:
                b = Button()
                b.from_dict(item, db.session)
                db.session.add(b)
        db.session.flush()

        # Puis enfants
        for item in data:
            if item.get("parent_button_id") is not None:
                b = Button()
                b.from_dict(item, db.session)
                db.session.add(b)
        db.session.commit()


# ---------------------------------------------------------------------------
# Config (ConfigOption)
# ---------------------------------------------------------------------------

class ConfigSection(BackupSection):
    key = "config"
    label = "Configuration générale"

    def export_data(self):
        options = ConfigOption.query.all()
        result = {}
        self.excluded_secrets = []
        for o in options:
            # Clés techniques internes (ex. compteur de génération de config,
            # point 11) : jamais exportées ni restaurées.
            if config_sync.is_reserved_key(o.config_key):
                continue
            # Les secrets (mot de passe SMTP, clé Spotify...) ne sont JAMAIS
            # exportés (point 5). On mémorise ceux qui étaient définis pour
            # prévenir l'utilisateur (avertissement d'exclusion).
            if is_secret_key(o.config_key):
                self.excluded_secrets.append(o.config_key)
                continue
            if o.value_bool is not None:
                result[o.config_key] = o.value_bool
            elif o.value_int is not None:
                result[o.config_key] = o.value_int
            elif o.value_text is not None:
                result[o.config_key] = o.value_text
            elif o.value_str is not None:
                result[o.config_key] = o.value_str
            elif o.value_json is not None:
                result[o.config_key] = o.value_json
            else:
                result[o.config_key] = None
        return result

    def restore_data(self, data):
        for key, value in data.items():
            # Clé technique interne (point 11) : jamais restaurée (une sauvegarde
            # ne doit pas réinjecter un compteur de génération arbitraire).
            if config_sync.is_reserved_key(key):
                continue
            # Un secret ne doit jamais être restauré depuis une sauvegarde (il
            # n'y figure pas ; refuser tout de même une injection éventuelle).
            if is_secret_key(key):
                continue
            option = ConfigOption.query.filter_by(config_key=key).first()
            if option:
                option.value_str = value if isinstance(value, str) and len(value) < 200 else None
                option.value_int = value if isinstance(value, int) and not isinstance(value, bool) else None
                option.value_bool = value if isinstance(value, bool) else None
                option.value_text = value if isinstance(value, str) and len(value) >= 200 else None
                option.value_json = value if isinstance(value, (dict, list)) else None
            else:
                option = ConfigOption(
                    config_key=key,
                    value_str=value if isinstance(value, str) and len(value) < 200 else None,
                    value_int=value if isinstance(value, int) and not isinstance(value, bool) else None,
                    value_bool=value if isinstance(value, bool) else None,
                    value_text=value if isinstance(value, str) and len(value) >= 200 else None,
                    value_json=value if isinstance(value, (dict, list)) else None,
                )
                db.session.add(option)
        # Point 11 : une restauration modifie massivement la configuration ;
        # incrémenter la génération (même transaction) pour que les autres
        # processus rechargent app.config.
        config_sync.bump_generation()
        db.session.commit()
        try:
            current_app.load_configuration(current_app)
            config_sync.mark_current_generation(current_app)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Per-page Config (ConfigOption filtered by prefix)
# ---------------------------------------------------------------------------

class _PageConfigSection(BackupSection):
    """Base for page-specific config sections (filters ConfigOption by prefixes)."""
    prefixes = []

    def _match(self, key):
        return any(key.startswith(p) for p in self.prefixes)

    def export_data(self):
        options = ConfigOption.query.all()
        result = {}
        self.excluded_secrets = []
        for o in options:
            if not self._match(o.config_key):
                continue
            if is_secret_key(o.config_key):
                self.excluded_secrets.append(o.config_key)
                continue
            if o.value_bool is not None:
                result[o.config_key] = o.value_bool
            elif o.value_int is not None:
                result[o.config_key] = o.value_int
            elif o.value_text is not None:
                result[o.config_key] = o.value_text
            elif o.value_str is not None:
                result[o.config_key] = o.value_str
            elif o.value_json is not None:
                result[o.config_key] = o.value_json
            else:
                result[o.config_key] = None
        return result

    def restore_data(self, data):
        for key, value in data.items():
            if not self._match(key):
                continue
            if is_secret_key(key):
                continue
            option = ConfigOption.query.filter_by(config_key=key).first()
            if option:
                option.value_str = value if isinstance(value, str) and len(value) < 200 else None
                option.value_int = value if isinstance(value, int) and not isinstance(value, bool) else None
                option.value_bool = value if isinstance(value, bool) else None
                option.value_text = value if isinstance(value, str) and len(value) >= 200 else None
                option.value_json = value if isinstance(value, (dict, list)) else None
            else:
                option = ConfigOption(
                    config_key=key,
                    value_str=value if isinstance(value, str) and len(value) < 200 else None,
                    value_int=value if isinstance(value, int) and not isinstance(value, bool) else None,
                    value_bool=value if isinstance(value, bool) else None,
                    value_text=value if isinstance(value, str) and len(value) >= 200 else None,
                    value_json=value if isinstance(value, (dict, list)) else None,
                )
                db.session.add(option)
        # Point 11 : propager le changement aux autres processus.
        config_sync.bump_generation()
        db.session.commit()
        try:
            current_app.load_configuration(current_app)
            config_sync.mark_current_generation(current_app)
        except Exception:
            pass


class ConfigPatientSection(_PageConfigSection):
    key = "config_patient"
    label = "Textes & options page Patient"
    prefixes = ["page_patient_"]


class ConfigAnnounceSection(_PageConfigSection):
    key = "config_announce"
    label = "Textes & options page Annonce"
    prefixes = ["announce_"]


class ConfigPhoneSection(_PageConfigSection):
    key = "config_phone"
    label = "Textes & options page Téléphone"
    prefixes = ["phone_"]


# ---------------------------------------------------------------------------
# CSS Variables (Patient / Announce / Phone)
# ---------------------------------------------------------------------------

class _CssVariableSection(BackupSection):
    """Base for CSS variable sections."""
    model = None
    css_mode = None  # 'patient', 'announce', or 'phone'

    def export_data(self):
        variables = self.model.query.all()
        return {v.variable: v.value for v in variables}

    def restore_data(self, data):
        for key, value in data.items():
            var = self.model.query.filter_by(variable=key).first()
            if var:
                var.value = value
            else:
                var = self.model(variable=key, value=value)
                db.session.add(var)
        db.session.commit()
        # Recharger le cache et régénérer les fichiers CSS après restauration
        if self.css_mode:
            try:
                current_app.css_variable_manager.reload_source(self.css_mode)
                variables = current_app.css_variable_manager.get_all_variables(self.css_mode)
                current_app.css_manager.generate_css(variables, mode=self.css_mode)
            except Exception as e:
                current_app.logger.error(f"Erreur régénération CSS ({self.css_mode}): {e}", exc_info=True)


class CssPatientSection(_CssVariableSection):
    key = "css_patient"
    label = "CSS Page Patient"
    model = PatientCssVariable
    css_mode = "patient"


class CssAnnounceSection(_CssVariableSection):
    key = "css_announce"
    label = "CSS Page Annonce"
    model = AnnounceCssVariable
    css_mode = "announce"


class CssPhoneSection(_CssVariableSection):
    key = "css_phone"
    label = "CSS Page Téléphone"
    model = PhoneCssVariable
    css_mode = "phone"


# ---------------------------------------------------------------------------
# Languages
# ---------------------------------------------------------------------------

class LanguageSection(BackupSection):
    key = "languages"
    label = "Langues"

    def export_data(self):
        langs = Language.query.all()
        return [
            {
                "id": l.id,
                "code": l.code,
                "name": l.name,
                "translation": l.translation,
                "is_active": l.is_active,
                "flag_url": l.flag_url,
                "sort_order": l.sort_order,
                "voice_is_active": l.voice_is_active,
                "voice_model": l.voice_model,
                "voice_gtts_name": l.voice_gtts_name,
                "voice_google_name": l.voice_google_name,
                "voice_google_region": l.voice_google_region,
            }
            for l in langs
        ]

    def restore_data(self, data):
        for item in data:
            lang = Language.query.filter_by(code=item["code"]).first()
            if lang:
                for k, v in item.items():
                    if k != "id":
                        setattr(lang, k, v)
            else:
                lang = Language(**item)
                db.session.add(lang)
        db.session.commit()


# ---------------------------------------------------------------------------
# Texts + TextTranslation
# ---------------------------------------------------------------------------

class TextSection(BackupSection):
    key = "texts"
    label = "Textes"

    def export_data(self):
        texts = Text.query.all()
        result = []
        for t in texts:
            translations = TextTranslation.query.filter_by(text_id=t.id).all()
            result.append({
                "id": t.id,
                "text_key": t.text_key,
                "text_value": t.text_value,
                "translations": [
                    {
                        "language_id": tr.language_id,
                        "translation": tr.translation,
                    }
                    for tr in translations
                ],
            })
        return result

    def restore_data(self, data):
        for item in data:
            text = Text.query.filter_by(text_key=item["text_key"]).first()
            if text:
                text.text_value = item["text_value"]
            else:
                text = Text(text_key=item["text_key"], text_value=item["text_value"])
                db.session.add(text)
                db.session.flush()

            for tr_data in item.get("translations", []):
                tr = TextTranslation.query.filter_by(
                    text_id=text.id, language_id=tr_data["language_id"]
                ).first()
                if tr:
                    tr.translation = tr_data["translation"]
                else:
                    tr = TextTranslation(
                        text_id=text.id,
                        language_id=tr_data["language_id"],
                        translation=tr_data["translation"],
                    )
                    db.session.add(tr)
        db.session.commit()


# ---------------------------------------------------------------------------
# TextInterface
# ---------------------------------------------------------------------------

class TextInterfaceSection(BackupSection):
    key = "text_interface"
    label = "Textes interface"

    def export_data(self):
        items = TextInterface.query.all()
        return [{"id": t.id, "text_id": t.text_id, "value": t.value} for t in items]

    def restore_data(self, data):
        for item in data:
            ti = TextInterface.query.filter_by(text_id=item["text_id"]).first()
            if ti:
                ti.value = item["value"]
            else:
                ti = TextInterface(text_id=item["text_id"], value=item["value"])
                db.session.add(ti)
        db.session.commit()


# ---------------------------------------------------------------------------
# Translations (table Translation — dynamic translations)
# ---------------------------------------------------------------------------

class TranslationSection(BackupSection):
    key = "translations"
    label = "Traductions"

    def export_data(self):
        items = Translation.query.all()
        return [
            {
                "id": t.id,
                "table_name": t.table_name,
                "column_name": t.column_name,
                "key_name": t.key_name,
                "row_id": t.row_id,
                "language_code": t.language_code,
                "translated_text": t.translated_text,
            }
            for t in items
        ]

    def restore_data(self, data):
        for item in data:
            t = Translation.query.filter_by(
                table_name=item["table_name"],
                column_name=item["column_name"],
                row_id=item["row_id"],
                language_code=item["language_code"],
            ).first()
            if t:
                t.translated_text = item["translated_text"]
                t.key_name = item.get("key_name")
            else:
                t = Translation(
                    table_name=item["table_name"],
                    column_name=item["column_name"],
                    key_name=item.get("key_name"),
                    row_id=item["row_id"],
                    language_code=item["language_code"],
                    translated_text=item["translated_text"],
                )
                db.session.add(t)
        db.session.commit()


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class DashboardSection(BackupSection):
    key = "dashboard"
    label = "Dashboard"

    def export_data(self):
        cards = DashboardCard.query.all()
        return [
            {
                "id": c.id,
                "name": c.name,
                "visible": c.visible,
                "position": c.position,
                "size": c.size,
                "color": c.color,
                "settings": c.settings,
            }
            for c in cards
        ]

    def restore_data(self, data):
        for item in data:
            card = DashboardCard.query.filter_by(name=item["name"]).first()
            if card:
                card.visible = item["visible"]
                card.position = item["position"]
                card.size = item["size"]
                card.color = item["color"]
                card.settings = item.get("settings")
            else:
                card = DashboardCard(
                    name=item["name"],
                    visible=item["visible"],
                    position=item["position"],
                    size=item["size"],
                    color=item["color"],
                    settings=item.get("settings"),
                )
                db.session.add(card)
        db.session.commit()


# ---------------------------------------------------------------------------
# Images — base commune (sections binaires)
# ---------------------------------------------------------------------------

class _ImageSection(BackupSection):
    """Base des sections d'images (fichiers binaires).

    Format 3.0 (point 13) : les octets sont exportés en fichiers binaires dans
    l'archive (``iter_binary_files``) et restaurés depuis l'archive
    (``restore_zip``). Format 2.0 : rétrocompatibilité base64 (``export_data`` /
    ``restore_data``). Toute la logique d'écriture (limites, sûreté des chemins,
    volume total) est mutualisée dans ``restore_files``."""

    is_binary = True

    # --- Description des répertoires source -------------------------------
    def _source_dirs(self):
        """Renvoie ``[(arc_prefix, base_dir), …]``.

        ``arc_prefix`` (éventuellement vide) est préfixé au chemin relatif dans
        l'archive/le manifeste, pour distinguer plusieurs répertoires source."""
        raise NotImplementedError

    def _safe_target(self, rel_path):
        """Résout ``rel_path`` en un Path absolu **sûr** ou lève UnsafePathError.

        Redéfini par les sous-classes (extension autorisée, confinement)."""
        raise NotImplementedError

    # --- Export -----------------------------------------------------------
    def iter_binary_files(self):
        """Itère ``(rel_path, full_path)`` pour chaque fichier à archiver.

        ``rel_path`` utilise des séparateurs ``/`` et intègre l'``arc_prefix``
        éventuel, afin que la restauration retrouve la cible sans ambiguïté."""
        for prefix, base_dir in self._source_dirs():
            if not os.path.exists(base_dir):
                continue
            for root, _dirs, filenames in os.walk(base_dir):
                for fname in filenames:
                    full_path = os.path.join(root, fname)
                    rel = os.path.relpath(full_path, base_dir)
                    arc_rel = os.path.join(prefix, rel) if prefix else rel
                    yield arc_rel.replace(os.sep, "/"), full_path

    def total_size_bytes(self):
        """Somme des tailles sur disque des fichiers de la section (estimation)."""
        total = 0
        for _rel, full_path in self.iter_binary_files():
            try:
                total += os.path.getsize(full_path)
            except OSError:
                pass
        return total

    def export_data(self):
        # Format 2.0 (JSON plat) : images encodées base64. Conservé pour la
        # rétrocompatibilité ; l'export 3.0 ne passe jamais par ici.
        files = {}
        for rel_path, full_path in self.iter_binary_files():
            with open(full_path, "rb") as f:
                files[rel_path] = base64.b64encode(f.read()).decode("ascii")
        return files

    # --- Restauration -----------------------------------------------------
    def restore_files(self, files):
        """Écrit les images depuis un itérable ``(rel_path, content_bytes)``.

        ``content_bytes`` est déjà décodé et borné individuellement par
        l'appelant ; ``None`` signale un fichier déjà refusé et journalisé.
        Applique les limites de nombre de fichiers, de sûreté de chemin et de
        volume total ; ignore les entrées non sûres (journalisées) et lève
        :class:`BackupValidationError` sur les limites dures."""
        skipped = 0
        total = 0
        count = 0
        for rel_path, content in files:
            count += 1
            if count > MAX_IMAGE_FILES:
                raise BackupValidationError(f"Section '{self.label}' : trop de fichiers.")
            if content is None:
                skipped += 1
                continue
            try:
                full_path = self._safe_target(rel_path)
            except UnsafePathError as e:
                skipped += 1
                current_app.logger.warning(
                    f"Restore {self.key}: chemin refusé {rel_path!r}: {e}"
                )
                continue
            total += len(content)
            if total > MAX_TOTAL_DECODED_BYTES:
                raise BackupValidationError(f"Section '{self.label}' : volume total trop important.")
            full_path.parent.mkdir(parents=True, exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(content)
        if skipped:
            current_app.logger.warning(
                f"Restore {self.key}: {skipped} fichier(s) refusé(s) (traversée/extension/périmètre/taille)."
            )

    def restore_data(self, data):
        # Format 2.0 : dict {rel_path: base64}. Décode chaque fichier (borné)
        # puis délègue à restore_files ; None = fichier refusé (déjà journalisé).
        if not isinstance(data, dict):
            raise BackupValidationError(f"Section '{self.label}' invalide.")
        if len(data) > MAX_IMAGE_FILES:
            raise BackupValidationError(f"Section '{self.label}' : trop de fichiers.")

        def _decoded():
            for rel_path, b64content in data.items():
                try:
                    content = _safe_b64decode(b64content, max_bytes=MAX_DECODED_FILE_BYTES)
                except ValueError as e:
                    current_app.logger.warning(
                        f"Restore {self.key}: contenu refusé {rel_path!r}: {e}"
                    )
                    yield rel_path, None
                    continue
                yield rel_path, content

        self.restore_files(_decoded())

    def restore_zip(self, archive, section_key):
        # Format 3.0 : lire les fichiers binaires de l'archive sous
        # files/<section_key>/… (taille par fichier bornée par l'archive).
        self.restore_files(archive.iter_binary_files(section_key))


# ---------------------------------------------------------------------------
# Images — Buttons
# ---------------------------------------------------------------------------

class ImagesButtonsSection(_ImageSection):
    key = "images_buttons"
    label = "Images boutons"

    def _get_dir(self):
        return os.path.join(current_app.static_folder, "images", "buttons")

    def _source_dirs(self):
        return [("", self._get_dir())]

    def _safe_target(self, rel_path):
        return safe_relative_path(
            self._get_dir(),
            rel_path,
            allowed_extensions=ALLOWED_IMAGE_EXTENSIONS,
            what="image path",
        )


# ---------------------------------------------------------------------------
# Images — Gallery (annonces + galleries)
# ---------------------------------------------------------------------------

class ImagesGallerySection(_ImageSection):
    key = "images_gallery"
    label = "Galerie images"

    def _get_dirs(self):
        return [
            ("galleries", os.path.join(current_app.static_folder, "galleries")),
            ("images/annonces", os.path.join(current_app.static_folder, "images", "annonces")),
        ]

    def _source_dirs(self):
        return self._get_dirs()

    def _safe_target(self, rel_path):
        static_folder = current_app.static_folder
        full_path = safe_relative_path(
            static_folder,
            rel_path,
            allowed_extensions=ALLOWED_IMAGE_EXTENSIONS,
            what="image path",
        )
        # En plus de rester sous static/, confiner aux seuls sous-dossiers
        # galleries/ et images/annonces/ (pas d'écriture ailleurs sous static).
        resolved = os.path.realpath(str(full_path))
        allowed_roots = [
            os.path.realpath(base_dir) for _prefix, base_dir in self._get_dirs()
        ]
        if not any(
            resolved == root or resolved.startswith(root + os.sep)
            for root in allowed_roots
        ):
            raise UnsafePathError("resolved path outside allowed gallery roots")
        return full_path


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SECTION_CLASSES = [
    StaffSection,
    CounterSection,
    ActivitySection,
    ScheduleSection,
    AlgoRuleSection,
    ButtonSection,
    ConfigSection,
    ConfigPatientSection,
    ConfigAnnounceSection,
    ConfigPhoneSection,
    CssPatientSection,
    CssAnnounceSection,
    CssPhoneSection,
    LanguageSection,
    TextSection,
    TextInterfaceSection,
    TranslationSection,
    DashboardSection,
    ImagesButtonsSection,
    ImagesGallerySection,
]

BACKUP_SECTIONS = {cls.key: cls for cls in SECTION_CLASSES}

# Groupes pour l'UI
SECTION_GROUPS = {
    "Structure": ["staff", "counters", "activities", "schedules", "algorules", "buttons"],
    "Configuration": ["config", "dashboard"],
    "Pages": ["config_patient", "css_patient", "config_announce", "css_announce", "config_phone", "css_phone"],
    "Textes & Traductions": ["languages", "texts", "text_interface", "translations"],
    "Images": ["images_buttons", "images_gallery"],
}


# Clés des sections binaires (images) : déterminent le choix du format d'export
# (ZIP 3.0 vs JSON 2.0) et le traitement à la restauration.
BINARY_SECTION_KEYS = {
    key for key, cls in BACKUP_SECTIONS.items() if getattr(cls, "is_binary", False)
}


# ---------------------------------------------------------------------------
# Archive ZIP (format 3.0) — lecture
# ---------------------------------------------------------------------------

class BackupArchive:
    """Accès en lecture à une sauvegarde au format 3.0 (ZIP).

    Détient la ``ZipFile`` ouverte et le manifeste validé. Fournit l'itération
    des fichiers binaires d'une section avec garde de taille par fichier (les
    octets ne sont jamais chargés tous ensemble)."""

    def __init__(self, zip_ref, manifest: dict):
        self._zip = zip_ref
        self.manifest = manifest

    def iter_binary_files(self, section_key: str):
        """Itère ``(rel_path, content_bytes | None)`` pour une section binaire.

        ``None`` signale un fichier refusé (trop volumineux / illisible) déjà
        journalisé, que la restauration ignorera."""
        prefix = f"{FILES_ARC_PREFIX}/{section_key}/"
        for info in self._zip.infolist():
            name = info.filename
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            rel_path = name[len(prefix):]
            if not rel_path:
                continue
            # Garde anti-bombe : refuser sans lire un fichier dont la taille
            # décompressée annoncée dépasse la limite par fichier.
            if info.file_size > MAX_DECODED_FILE_BYTES:
                current_app.logger.warning(
                    f"Restore {section_key}: fichier {rel_path!r} trop volumineux (ignoré)."
                )
                yield rel_path, None
                continue
            try:
                content = self._zip.read(info)
            except Exception as e:
                current_app.logger.warning(
                    f"Restore {section_key}: lecture impossible {rel_path!r}: {e}"
                )
                yield rel_path, None
                continue
            yield rel_path, content

    def close(self):
        try:
            self._zip.close()
        except Exception:
            pass


def load_and_validate_archive(zip_ref) -> "BackupArchive":
    """Valide une archive 3.0 ouverte (``zipfile.ZipFile``) et renvoie un
    :class:`BackupArchive`.

    Applique des gardes anti-bombe globales (nombre d'entrées, volume
    décompressé cumulé, taille du manifeste) **avant** toute extraction, puis
    valide structurellement le manifeste. Lève :class:`BackupValidationError`
    avec un message sûr pour l'utilisateur."""
    infos = zip_ref.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise BackupValidationError("Archive invalide : trop d'entrées.")
    total = 0
    for info in infos:
        total += info.file_size
        if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise BackupValidationError("Archive invalide : volume décompressé trop important.")

    try:
        manifest_info = zip_ref.getinfo(MANIFEST_NAME)
    except KeyError:
        raise BackupValidationError("Archive invalide : manifeste absent.")
    if manifest_info.file_size > MAX_MANIFEST_BYTES:
        raise BackupValidationError("Archive invalide : manifeste trop volumineux.")

    try:
        raw = zip_ref.read(MANIFEST_NAME)
    except Exception:
        raise BackupValidationError("Archive illisible.")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError):
        raise BackupValidationError("Manifeste illisible ou mal formé.")

    manifest = _validate_backup_structure(manifest)
    return BackupArchive(zip_ref, manifest)


# ---------------------------------------------------------------------------
# Archive ZIP (format 3.0) — écriture
# ---------------------------------------------------------------------------

def selection_has_binary(section_keys) -> bool:
    """Vrai si la sélection contient au moins une section d'images.

    L'export produit alors une archive ZIP (3.0) plutôt qu'un JSON plat (2.0)."""
    return any(k in BINARY_SECTION_KEYS for k in section_keys)


def write_backup_archive(section_keys, fileobj) -> dict:
    """Écrit une sauvegarde 3.0 (ZIP) dans ``fileobj`` (binaire, en écriture).

    Le manifeste JSON contient les métadonnées et les données des sections
    **non binaires** ; les images sont écrites en fichiers binaires sous
    ``files/<section>/…`` **directement depuis le disque** (jamais de base64 ni
    de gros JSON en mémoire). Renvoie le manifeste écrit (utile aux tests)."""
    data = {}
    excluded_secrets: set[str] = set()
    binary_sections: list[str] = []
    binary_counts: dict[str, int] = {}
    included: list[str] = []

    with zipfile.ZipFile(fileobj, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for key in section_keys:
            cls = BACKUP_SECTIONS.get(key)
            if cls is None:
                continue
            section = cls()
            if section.is_binary:
                count = 0
                try:
                    for rel_path, full_path in section.iter_binary_files():
                        arcname = f"{FILES_ARC_PREFIX}/{key}/{rel_path}"
                        # Images déjà compressées (png/jpg/webp…) : ZIP_STORED
                        # évite un recompressage inutile — copie en flux du disque.
                        zf.write(full_path, arcname, zipfile.ZIP_STORED)
                        count += 1
                except Exception as e:
                    current_app.logger.error(
                        f"Backup export error for section '{key}': {e}", exc_info=True
                    )
                binary_sections.append(key)
                binary_counts[key] = count
                included.append(key)
            else:
                try:
                    data[key] = section.export_data()
                    excluded_secrets.update(section.excluded_secrets)
                except Exception as e:
                    current_app.logger.error(
                        f"Backup export error for section '{key}': {e}", exc_info=True
                    )
                    data[key] = None
                included.append(key)

        manifest = {
            "app": APP_NAME,
            "format_version": ARCHIVE_FORMAT_VERSION,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sections": included,
            # Secrets volontairement exclus (nom seulement) : avertissement.
            "excluded_secrets": sorted(excluded_secrets),
            "binary_sections": binary_sections,
            "binary_counts": binary_counts,
            "data": data,
        }
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))

    return manifest


# ---------------------------------------------------------------------------
# Estimation de taille (point 13 : avertir avant un export lourd)
# ---------------------------------------------------------------------------

def human_size(num_bytes: int) -> str:
    """Formatte une taille en octets de façon lisible (o / Ko / Mo / Go / To)."""
    size = float(num_bytes)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if size < 1024 or unit == "To":
            if unit == "o":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{int(num_bytes)} o"


def estimate_export_size(section_keys) -> dict:
    """Estime le volume des sections **d'images** sélectionnées (sur disque).

    Sert à l'interface pour afficher une estimation et avertir lorsque l'export
    serait lourd. Les sections non binaires (JSON) sont considérées légères et
    ignorées de l'estimation."""
    sections = []
    images_bytes = 0
    for key in section_keys:
        cls = BACKUP_SECTIONS.get(key)
        if cls is None or not getattr(cls, "is_binary", False):
            continue
        section = cls()
        try:
            size = section.total_size_bytes()
        except Exception:
            size = 0
        images_bytes += size
        sections.append(
            {"key": key, "label": cls.label, "bytes": size, "human": human_size(size)}
        )
    return {
        "images_bytes": images_bytes,
        "images_human": human_size(images_bytes),
        "heavy": images_bytes > EXPORT_IMAGE_WARNING_BYTES,
        "warning_bytes": EXPORT_IMAGE_WARNING_BYTES,
        "warning_human": human_size(EXPORT_IMAGE_WARNING_BYTES),
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def export_sections(section_keys: list[str]) -> dict:
    """Export the requested sections into a backup dict.

    Les valeurs secrètes (cf. ``SECRET_CONFIG_KEYS``) ne sont jamais incluses.
    La liste de celles qui ont été écartées est jointe sous ``excluded_secrets``
    pour permettre un avertissement explicite à l'utilisateur (point 5)."""
    data = {}
    excluded_secrets: set[str] = set()
    for key in section_keys:
        if key in BACKUP_SECTIONS:
            section = BACKUP_SECTIONS[key]()
            try:
                data[key] = section.export_data()
                excluded_secrets.update(section.excluded_secrets)
            except Exception as e:
                current_app.logger.error(f"Backup export error for section '{key}': {e}", exc_info=True)
                data[key] = None

    return {
        "app": APP_NAME,
        "format_version": FORMAT_VERSION,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sections": [k for k in section_keys if k in data],
        # Secrets volontairement exclus de cette sauvegarde (jamais leur valeur,
        # seulement leur nom) : sert d'avertissement à la restauration.
        "excluded_secrets": sorted(excluded_secrets),
        "data": data,
    }


def restore_sections(
    backup_data: dict,
    section_keys: list[str] | None = None,
    archive: "BackupArchive | None" = None,
) -> dict:
    """Restore sections from a backup dict. Returns a report.

    ``archive`` (facultatif) est fourni pour une sauvegarde 3.0 (ZIP) : les
    sections binaires (images) y sont restaurées depuis les fichiers de
    l'archive plutôt que depuis ``data`` (base64, format 2.0)."""
    if not isinstance(backup_data, dict) or backup_data.get("app") != APP_NAME:
        return {"success": False, "error": "Fichier de sauvegarde invalide (app inconnue)."}

    if backup_data.get("format_version") not in SUPPORTED_FORMAT_VERSIONS:
        return {"success": False, "error": "Version de format de sauvegarde non prise en charge."}

    available = backup_data.get("sections", [])
    data = backup_data.get("data", {})

    if section_keys is None:
        section_keys = available

    report = {"success": True, "restored": [], "errors": []}

    for key in section_keys:
        if key not in available:
            report["errors"].append(f"Section '{key}' absente du fichier.")
            continue
        if key not in BACKUP_SECTIONS:
            report["errors"].append(f"Section '{key}' inconnue.")
            continue

        section = BACKUP_SECTIONS[key]()
        try:
            if section.is_binary and archive is not None:
                # Format 3.0 : lire les fichiers binaires depuis l'archive.
                section.restore_zip(archive, key)
            elif key in data:
                # Format 2.0 (ou section non binaire) : données du manifeste.
                section.restore_data(data[key])
            else:
                report["errors"].append(f"Section '{key}' absente du fichier.")
                continue
            report["restored"].append(key)
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Backup restore error for section '{key}': {e}", exc_info=True)
            report["errors"].append(f"Section '{key}': {str(e)}")

    if report["errors"]:
        report["success"] = len(report["restored"]) > 0

    return report


def preview_backup(backup_data: dict) -> dict:
    """Return metadata about a backup file without restoring."""
    binary_counts = backup_data.get("binary_counts") or {}
    sections_info = []
    for key in backup_data.get("sections", []):
        cls = BACKUP_SECTIONS.get(key)
        label = cls.label if cls else key
        data = backup_data.get("data", {}).get(key)
        if isinstance(data, (list, dict)):
            # Format 2.0 : données présentes dans le manifeste (base64 pour les
            # images) → le nombre d'éléments donne le compte.
            count = len(data)
        elif isinstance(binary_counts.get(key), int):
            # Format 3.0 : section binaire, compte fourni par le manifeste.
            count = binary_counts[key]
        else:
            count = 0
        sections_info.append({"key": key, "label": label, "count": count})

    return {
        "app": backup_data.get("app"),
        "format_version": backup_data.get("format_version"),
        "timestamp": backup_data.get("timestamp"),
        "sections": sections_info,
        # Secrets absents de la sauvegarde (avertissement à la restauration).
        "excluded_secrets": backup_data.get("excluded_secrets", []),
    }
