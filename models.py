import uuid
from datetime import datetime, timezone
from flask import current_app as app
from sqlalchemy import Sequence, UniqueConstraint, CheckConstraint, event
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import relationship, Session
from flask_security import UserMixin, RoleMixin
from sqlalchemy.dialects.mysql import JSON

db = SQLAlchemy()


roles_users = db.Table(
    'roles_users',
    db.Column('user_id', db.Integer(), db.ForeignKey('user.id')),
    db.Column('role_id', db.Integer(), db.ForeignKey('role.id'))
)

class Role(db.Model, RoleMixin):
    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.String(80), unique=True)
    description = db.Column(db.String(255))

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=True)  # Rendre email nullable
    username = db.Column(db.String(255), unique=True, nullable=False)  # Le username devient obligatoire
    password = db.Column(db.String(255), nullable=False)
    last_login_at = db.Column(db.DateTime())
    current_login_at = db.Column(db.DateTime())
    last_login_ip = db.Column(db.String(100))
    current_login_ip = db.Column(db.String(100))
    login_count = db.Column(db.Integer)
    active = db.Column(db.Boolean())
    fs_uniquifier = db.Column(db.String(255), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    confirmed_at = db.Column(db.DateTime())
    roles = db.relationship('Role', secondary='roles_users', backref=db.backref('users', lazy='dynamic'))

class Patient(db.Model):
    id = db.Column(db.Integer, Sequence('patient_id_seq'), primary_key=True)
    call_number = db.Column(db.String(10), nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    timestamp_counter = db.Column(db.DateTime, default=None)
    timestamp_end = db.Column(db.DateTime, default=None)
    status = db.Column(db.String(50), nullable=False, default='standing')
    counter_id = db.Column(db.Integer, db.ForeignKey('counter.id', name='fk_patient_counter_id'), nullable=True)  # nullable=True si un patient peut ne pas être à un comptoir
    counter = db.relationship('Counter', backref=db.backref('patients', lazy=True))
    activity_id = db.Column(db.Integer, db.ForeignKey('activity.id', name='fk_patient_activity_id'), nullable=False)  # Now referencing the activity directly
    activity = db.relationship('Activity', backref=db.backref('patients', lazy=True))
    overtaken = db.Column(db.Integer, default=0)
    # ajout d'une référence à Language
    language_id = db.Column(db.Integer, db.ForeignKey('language.id', name='fk_patient_language_id'), nullable=True)
    language = db.relationship('Language', backref=db.backref('patients', lazy=True))

    def __repr__(self):
        return f'<Patient {self.call_number}> ({self.id})'
    
    def to_dict(self):
        return {
            "id": self.id,
            "call_number": self.call_number,
            "activity_id": self.activity_id,
            "activity": self.activity.name,
            "timestamp": self.timestamp.strftime('%Y-%m-%d %H:%M:%S'),  # Format datetime as string
            "status": self.status,
            "counter_id": self.counter_id,
            "language_id": self.language_id,
            "language_code": self.language.code
        }


counters_activities = db.Table('counters_activities',
    db.Column('counter_id', db.Integer, db.ForeignKey('counter.id'), primary_key=True),
    db.Column('activity_id', db.Integer, db.ForeignKey('activity.id'), primary_key=True)
)

class PatientHistory(db.Model):
    id = db.Column(db.Integer, Sequence('patient_history_id_seq'), primary_key=True)
    call_number = db.Column(db.String(10), nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    timestamp_counter = db.Column(db.DateTime, default=None)
    timestamp_end = db.Column(db.DateTime, default=None)
    day_of_week = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(50), nullable=False, default='standing')
    counter_id = db.Column(db.Integer, nullable=True)
    activity_id = db.Column(db.Integer, nullable=False)
    overtaken = db.Column(db.Integer, default=0)
    language_id = db.Column(db.Integer, nullable=True)

    def __repr__(self):
        return f'<PatientHistory {self.call_number}> ({self.id})'

    def to_dict(self):
        return {
            "id": self.id,
            "call_number": self.call_number,
            "activity_id": self.activity_id,
            "timestamp": self.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            "day_of_week": self.day_of_week,
            "status": self.status,
            "counter_id": self.counter_id,
            "language_id": self.language_id,
        }

class Counter(db.Model):
    id = db.Column(db.Integer, Sequence('counter_id_seq'), primary_key=True)
    name = db.Column(db.String(20), nullable=False)
    is_active = db.Column(db.Boolean, default=False)  # Indique si le comptoir est actuellement utilisé
    non_actions = db.Column(db.String(255))  # Liste des actes non réalisés par ce comptoir
    priority_actions = db.Column(db.String(255))  # Liste des actions prioritaires réalisées par ce comptoir
    activities = db.relationship('Activity', secondary=counters_activities, lazy='subquery',
            backref=db.backref('counters', lazy=True)) 
    # Ajouter une référence à un membre de l'équipe
    staff_id = db.Column(db.Integer, db.ForeignKey('pharmacist.id', name='fk_counter_staff_id'))
    staff = db.relationship('Pharmacist', backref=db.backref('counter', lazy=True))
    auto_calling = db.Column(db.Boolean, default=False)
    sort_order = db.Column(db.Integer)  # Champ pour l'ordre

    def __repr__(self):
        return f'<Counter {self.name}>'

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "is_active": self.is_active,
            "non_actions": self.non_actions,
            "priority_actions": self.priority_actions,
            "staff_id": self.staff_id,
            "activities": [activity.id for activity in self.activities]
        }

    def from_dict(self, data):
        for field in data:
            if field != 'activities':
                setattr(self, field, data[field])
        if 'activities' in data:
            self.activities = [Activity.query.get(id) for id in data['activities']]


pharmacists_activities = db.Table('pharmacists_activities',
    db.Column('pharmacist_id', db.Integer, db.ForeignKey('pharmacist.id', name='fk_pharmacists_activities_pharmacist_id'), primary_key=True),
    db.Column('activity_id', db.Integer, db.ForeignKey('activity.id', name='fk_pharmacists_activities_activity_id'), primary_key=True)
)


class Pharmacist(db.Model):
    id = db.Column(db.Integer, Sequence('pharmacist_id_seq'), primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    initials = db.Column(db.String(10), nullable=False)
    language = db.Column(db.String(20), default=False)
    is_active = db.Column(db.Boolean, default=False)
    activities = db.relationship('Activity', secondary=pharmacists_activities, lazy='subquery',
        backref=db.backref('pharmacists', lazy=True))   
    
    # Ajout d'une contrainte unique nominative sur 'initials'
    __table_args__ = (
        UniqueConstraint('initials', name='uq_pharmacist_initials'),
    )

    def __repr__(self):
        return f'<Pharmacist {self.name}>'

    def from_dict(self, data):
        """ Utilisé par la restauration de la base de données """
        for field in data:
            if field != 'activities':
                setattr(self, field, data[field])
        if 'activities' in data:
            self.activities = [Activity.query.get(id) for id in data['activities']]

    def to_dict(self):
        """ Convertit l'objet en dictionnaire pour faciliter la sauvegarde en JSON """
        return {
            "id": self.id,
            "name": self.name,
            "initials": self.initials,
            "language": self.language,
            "is_active": self.is_active,
            "activities": [activity.id for activity in self.activities]
        }


activity_schedule_weekday = db.Table('activity_schedule_weekday',
    db.Column('schedule_id', db.Integer, db.ForeignKey('activity_schedules.id'), primary_key=True),
    db.Column('weekday_id', db.Integer, db.ForeignKey('weekdays.id'), primary_key=True)
)


class Weekday(db.Model):
    __tablename__ = 'weekdays'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(10), nullable=False, unique=True)  
    english_name = db.Column(db.String(10), nullable=False, unique=True)  
    abbreviation = db.Column(db.String(3), unique=True) 
    schedules = relationship('ActivitySchedule', secondary=activity_schedule_weekday, back_populates='weekdays')

    def __repr__(self):
        return f'<Weekday {self.name}>'



class Activity(db.Model):
    __tablename__ = 'activity'
    id = db.Column(db.Integer, Sequence('activity_id_seq'), primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    letter = db.Column(db.String(1), nullable=False)
    inactivity_message = db.Column(db.String(255), default="")
    specific_message = db.Column(db.String(255), default="")
    notification = db.Column(db.Boolean, default=False)
    schedules = relationship('ActivitySchedule', secondary='activity_schedule_link', back_populates='activities')
    is_staff = db.Column(db.Boolean, default=False)
    staff_id = db.Column(db.Integer, db.ForeignKey('pharmacist.id', name='fk_activity_staff_id'), nullable=True)
    staff = db.relationship('Pharmacist', backref=db.backref('activity', lazy=True))

    def __repr__(self):
        return f'<Activity - {self.name}>'

    def from_dict(self, data):
        for field in data:
            if field == 'schedules':
                self.schedules = [ActivitySchedule.query.get(schedule_id) for schedule_id in data[field]]
            elif field == 'staff_id':
                self.staff_id = data[field] if data[field] else None
            else:
                setattr(self, field, data[field])


# Table d'association pour la relation many-to-many
activity_schedule_link = db.Table('activity_schedule_link',
    db.Column('activity_id', db.Integer, db.ForeignKey('activity.id'), primary_key=True),
    db.Column('schedule_id', db.Integer, db.ForeignKey('activity_schedules.id'), primary_key=True)
)

class ActivitySchedule(db.Model):
    __tablename__ = 'activity_schedules'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    start_time = db.Column(db.Time)
    end_time = db.Column(db.Time)
    weekdays = relationship('Weekday', secondary='activity_schedule_weekday', back_populates='schedules')
    activities = relationship('Activity', secondary='activity_schedule_link', back_populates='schedules')

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "start_time": self.start_time.strftime("%H:%M:%S") if self.start_time else None,
            "end_time": self.end_time.strftime("%H:%M:%S") if self.end_time else None,
            "weekdays": [weekday.id for weekday in self.weekdays],
            "activities": [activity.id for activity in self.activities]
        }

    def from_dict(self, data):
        for field in data:
            if field == 'start_time':
                self.start_time = datetime.strptime(data[field], "%H:%M:%S").time() if data[field] else None
            elif field == 'end_time':
                self.end_time = datetime.strptime(data[field], "%H:%M:%S").time() if data[field] else None
            elif field not in ['weekdays', 'activities']:
                setattr(self, field, data[field])
        if 'weekdays' in data:
            self.weekdays = [Weekday.query.get(id) for id in data['weekdays']]
        if 'activities' in data:
            self.activities = [Activity.query.get(id) for id in data['activities']]

    def __repr__(self):
        return f'<ActivitySchedule from {self.start_time} to {self.end_time}>'



# pour l'instant les jours ne sont pas utilisées... Peut être plus simple d'ajouter une table pour les jours de la semaine ????
class AlgoRule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255))
    activity_id = db.Column(db.Integer, db.ForeignKey('activity.id'), nullable=False, index=True)
    activity = db.relationship('Activity', backref=db.backref('priority_rules', lazy=True))
    
    min_patients = db.Column(db.Integer, nullable=False, default=0)
    max_patients = db.Column(db.Integer, nullable=False, default=999)
    CheckConstraint('min_patients <= max_patients', name='ck_priority_rules_min_max_patients')

    max_overtaken = db.Column(db.Integer, nullable=False, default=999)
    
    start_time = db.Column(db.Time, nullable=False)  # Heure de début de la validité de la règle
    end_time = db.Column(db.Time, nullable=False)    # Heure de fin de la validité de la règle
    CheckConstraint('start_time < end_time', name='ck_priority_rules_start_end_time')
    
    days_of_week = db.Column(db.String(30), nullable=False, default='Mon,Tue,Wed,Thu,Fri')  # Jours de la semaine, par exemple "Mon,Tue,Wed,Thu,Fri"
    
    priority_level = db.Column(db.Integer, nullable=False, default=1)  # Niveau de priorité, 1 étant le plus bas
    CheckConstraint('priority_level > 0', name='ck_priority_rules_priority_level')

    __table_args__ = (
        UniqueConstraint('activity_id', 'start_time', 'end_time', 'days_of_week', name='uq_priority_rule_combination'),
    )

    def __repr__(self):
        return (f'<PriorityRule for Activity ID {self.activity_id} with Priority {self.priority_level} '
                f'from {self.start_time.strftime("%H:%M")} to {self.end_time.strftime("%H:%M")} '
                f'on {self.days_of_week}>')


class ConfigOption(db.Model):
    id = db.Column(db.Integer, Sequence('config_option_id_seq'), primary_key=True)
    config_key = db.Column(db.String(50), unique=True, nullable=False)
    value_str = db.Column(db.String(200))  # Pour les chaînes de caractères
    value_int = db.Column(db.Integer)     # Pour les entiers
    value_bool = db.Column(db.Boolean)    # Pour les valeurs booléennes
    value_text = db.Column(db.Text)       # Pour les très longues chaînes
    value_json = db.Column(db.JSON)  # Type JSON pour MySQL

    def __repr__(self):
        return f'<ConfigOption {self.config_key}: {self.value_str or self.value_int or self.value_bool or self.value_text}>'


class ConfigVersion(db.Model):
    id = db.Column(db.Integer, Sequence('config_version_id_seq'), primary_key=True)
    config_key = db.Column(db.String(50), unique=True, nullable=False)
    version = db.Column(db.String(50), nullable=False)
    comments = db.Column(db.Text)
    date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<ConfigVersion {self.version}>'


class Button(db.Model):
    __tablename__ = 'button'

    id = db.Column(db.Integer, Sequence('button_id_seq'), primary_key=True)
    by_user = db.Column(db.Boolean, default=False)  # True si le bouton est créé par un user. Permet de savoir si bouton d'origine ou non
    code = db.Column(db.String(20), nullable=True, unique=True)  # Code unique est interne pour les boutons d'origine du logiciel. Permet de les reconnaitre même si le titre change.
    is_parent = db.Column(db.Boolean, default=False, nullable=True)
    label = db.Column(db.String(50), nullable=False)
    label_en = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)
    is_present = db.Column(db.Boolean, default=True)
    shape = db.Column(db.String(20), default='square')
    image_url = db.Column(db.String(100))
    background_color = db.Column(db.String(20))
    text_color = db.Column(db.String(20))
    sort_order = db.Column(db.Integer, nullable=False, default=1)  # Champ pour l'ordre

    # Ajouter une référence directe à Activity
    activity_id = db.Column(db.Integer, db.ForeignKey('activity.id', name='fk_button_activity_id'))
    activity = db.relationship('Activity', backref=db.backref('buttons', lazy=True))

    # relation ForeignKey entre deux boutons (un bouton est enfant d'un autre)
    parent_button_id = db.Column(db.Integer, db.ForeignKey('button.id', name='fk_button_parent_id'), nullable=True)
    parent_button = db.relationship('Button', remote_side=[id], backref='dependent_buttons')

    # Ajoutez des contraintes uniques directement dans la définition du modèle
    __table_args__ = (
        UniqueConstraint('code', name='uq_button_code'),
    )

    def __repr__(self):
        return f'<Button {self.label}>'
    
    def from_dict(self, data, session):
        for field, value in data.items():
            if field == 'activity_id':
                if value is not None:
                    self.activity = session.get(Activity, value)
                else:
                    self.activity = None
            elif field == 'parent_button_id':
                if value is not None:
                    self.parent_button = session.get(Button, value)
                else:
                    self.parent_button = None
            elif hasattr(self, field):
                setattr(self, field, value)


class Language(db.Model):
    __tablename__ = 'language'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(2), nullable=False, unique=True)
    name = db.Column(db.String(50), nullable=False)
    translation = db.Column(db.String(50), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    flag_url = db.Column(db.String(100))
    sort_order = db.Column(db.Integer)  # Champ pour l'ordre
    voice_is_active = db.Column(db.Boolean, default=True)
    voice_model = db.Column(db.String(20))
    voice_gtts_name = db.Column(db.String(100))
    voice_google_name = db.Column(db.String(100))
    voice_google_region = db.Column(db.String(20))

    __table_args__ = (
        db.UniqueConstraint('code', name='uq_language_code'),
    )

class Text(db.Model):
    __tablename__ = 'text'
    id = db.Column(db.Integer, primary_key=True)
    text_key = db.Column(db.String(100), nullable=False, unique=True)
    text_value = db.Column(db.Text, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('text_key', name='uq_text_key'),
    )

class TextTranslation(db.Model):
    __tablename__ = 'text_translation'
    id = db.Column(db.Integer, primary_key=True)
    text_id = db.Column(db.Integer, db.ForeignKey('text.id', ondelete='CASCADE'), nullable=False)
    language_id = db.Column(db.Integer, db.ForeignKey('language.id', ondelete='CASCADE'), nullable=False)
    translation = db.Column(db.Text, nullable=False)

    __table_args__ = (
        db.ForeignKeyConstraint(['text_id'], ['text.id'], name='fk_text_translation_text_id', ondelete='CASCADE'),
        db.ForeignKeyConstraint(['language_id'], ['language.id'], name='fk_text_translation_language_id', ondelete='CASCADE'),
    )

class Translation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    table_name = db.Column(db.String(50), nullable=False)  # Le nom de la table d'origine
    column_name = db.Column(db.String(50), nullable=False)  # Le nom de la colonne d'origine
    key_name = db.Column(db.String(50), nullable=True)
    row_id = db.Column(db.Integer, nullable=False)  # L'ID de la ligne d'origine
    language_code = db.Column(db.String(5), nullable=False)  # Code de la langue (ex: 'en', 'fr')
    translated_text = db.Column(db.Text, nullable=False, default="")  # Le texte traduit

    __table_args__ = (
        UniqueConstraint('table_name', 'column_name', 'row_id', 'language_code', name='uq_translation'),
    )

    def __repr__(self):
        return f"<Translation {self.language_code}: {self.translated_text[:20]}>"

class TextInterface(db.Model):
    __tablename__ = 'text_interface'
    id = db.Column(db.Integer, primary_key=True)
    text_id = db.Column(db.String(50), nullable=False)
    value = db.Column(db.Text, nullable=False)

class DashboardCard(db.Model):
    __tablename__ = 'dashboard_cards'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    visible = db.Column(db.Boolean, default=True)
    position = db.Column(db.Integer, nullable=False)  # Pour gérer l'ordre des cards
    size = db.Column(db.String(20), nullable=False, default='col-md-6')  # Taille avec classes Bootstrap
    color = db.Column(db.String(20), default='bg-white')  # Couleur de fond
    settings = db.Column(JSON)  # Champ JSON pour des réglages spécifiques à chaque card

    def __repr__(self):
        return f'<DashboardCard {self.name}>'