import json
import os
import base64
import shutil
import zipfile
from abc import ABC, abstractmethod
from datetime import datetime
from io import BytesIO
from flask import current_app
from models import (
    db, Pharmacist, Counter, Activity, ActivitySchedule, Weekday,
    AlgoRule, Button, ConfigOption, ConfigVersion,
    PatientCssVariable, AnnounceCssVariable, PhoneCssVariable,
    Language, Text, TextTranslation, TextInterface, Translation,
    DashboardCard
)

FORMAT_VERSION = "2.0"
APP_NAME = "GestionFile"


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class BackupSection(ABC):
    """Base class for a backup section."""

    key: str = ""
    label: str = ""

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
        for o in options:
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
        db.session.commit()
        try:
            current_app.load_configuration()
        except Exception:
            pass


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
# Images — Buttons
# ---------------------------------------------------------------------------

class ImagesButtonsSection(BackupSection):
    key = "images_buttons"
    label = "Images boutons"

    def _get_dir(self):
        return os.path.join(current_app.static_folder, "images", "buttons")

    def export_data(self):
        base_dir = self._get_dir()
        files = {}
        if os.path.exists(base_dir):
            for root, dirs, filenames in os.walk(base_dir):
                for fname in filenames:
                    full_path = os.path.join(root, fname)
                    rel_path = os.path.relpath(full_path, base_dir)
                    with open(full_path, "rb") as f:
                        files[rel_path] = base64.b64encode(f.read()).decode("ascii")
        return files

    def restore_data(self, data):
        base_dir = self._get_dir()
        os.makedirs(base_dir, exist_ok=True)
        for rel_path, b64content in data.items():
            full_path = os.path.join(base_dir, rel_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(base64.b64decode(b64content))


# ---------------------------------------------------------------------------
# Images — Gallery (annonces + galleries)
# ---------------------------------------------------------------------------

class ImagesGallerySection(BackupSection):
    key = "images_gallery"
    label = "Galerie images"

    def _get_dirs(self):
        return [
            ("galleries", os.path.join(current_app.static_folder, "galleries")),
            ("images/annonces", os.path.join(current_app.static_folder, "images", "annonces")),
        ]

    def export_data(self):
        files = {}
        for prefix, base_dir in self._get_dirs():
            if os.path.exists(base_dir):
                for root, dirs, filenames in os.walk(base_dir):
                    for fname in filenames:
                        full_path = os.path.join(root, fname)
                        rel_path = os.path.join(prefix, os.path.relpath(full_path, base_dir))
                        with open(full_path, "rb") as f:
                            files[rel_path] = base64.b64encode(f.read()).decode("ascii")
        return files

    def restore_data(self, data):
        for rel_path, b64content in data.items():
            full_path = os.path.join(current_app.static_folder, rel_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(base64.b64decode(b64content))


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
    "Visuel": ["css_patient", "css_announce", "css_phone"],
    "Textes & Traductions": ["languages", "texts", "text_interface", "translations"],
    "Images": ["images_buttons", "images_gallery"],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def export_sections(section_keys: list[str]) -> dict:
    """Export the requested sections into a backup dict."""
    data = {}
    for key in section_keys:
        if key in BACKUP_SECTIONS:
            section = BACKUP_SECTIONS[key]()
            try:
                data[key] = section.export_data()
            except Exception as e:
                current_app.logger.error(f"Backup export error for section '{key}': {e}", exc_info=True)
                data[key] = None

    return {
        "app": APP_NAME,
        "format_version": FORMAT_VERSION,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sections": [k for k in section_keys if k in data],
        "data": data,
    }


def restore_sections(backup_data: dict, section_keys: list[str] | None = None) -> dict:
    """Restore sections from a backup dict. Returns a report."""
    if backup_data.get("app") != APP_NAME:
        return {"success": False, "error": "Fichier de sauvegarde invalide (app inconnue)."}

    available = backup_data.get("sections", [])
    data = backup_data.get("data", {})

    if section_keys is None:
        section_keys = available

    report = {"success": True, "restored": [], "errors": []}

    for key in section_keys:
        if key not in available or key not in data:
            report["errors"].append(f"Section '{key}' absente du fichier.")
            continue
        if key not in BACKUP_SECTIONS:
            report["errors"].append(f"Section '{key}' inconnue.")
            continue

        section = BACKUP_SECTIONS[key]()
        try:
            section.restore_data(data[key])
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
    sections_info = []
    for key in backup_data.get("sections", []):
        cls = BACKUP_SECTIONS.get(key)
        label = cls.label if cls else key
        data = backup_data.get("data", {}).get(key)
        count = len(data) if isinstance(data, (list, dict)) else 0
        sections_info.append({"key": key, "label": label, "count": count})

    return {
        "app": backup_data.get("app"),
        "format_version": backup_data.get("format_version"),
        "timestamp": backup_data.get("timestamp"),
        "sections": sections_info,
    }
