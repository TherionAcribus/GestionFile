# TODO : on load comptoir verfier si on est en train de servir ou appeler qq (pb rechargement de page)
# POSSIBLE : laisser vide si comptoires vides et affichage uniquement si tous les comptoirs occupés
# TODO : Affichage d'un message en etranger si patient etranger "on going"
# TODO : Si choix langue en etranger -> Diriger vers comptoir en etranger
# TODO : Bouton Help ?

# deux lignes a appeler avant tout le reste (pour server Render)
import eventlet
eventlet.monkey_patch()
from flask import Flask, render_template, request, redirect, url_for, session, current_app, jsonify, send_from_directory, Response
import duckdb
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy import create_engine, ForeignKeyConstraint, UniqueConstraint, Sequence, func, CheckConstraint, and_
from flask_migrate import Migrate
from flask_socketio import SocketIO
from datetime import datetime, timezone, date
from flask_babel import Babel
from gtts import gTTS
from werkzeug.utils import secure_filename
from flask_apscheduler import APScheduler
import qrcode
import json
import os
from queue import Queue
import logging

from bdd import init_update_default_buttons_db_from_json, init_default_options_db_from_json, init_default_languages_db_from_json, init_or_update_default_texts_db_from_json, init_update_default_translations_db_from_json, init_default_algo_rules_db_from_json

class Config:
    SCHEDULER_API_ENABLED = True
    SECRET_KEY = 'your_secret_key'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///queuedatabase.db'
    #SQLALCHEMY_DATABASE_URI = 'duckdb:///database.duckdb'
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    AUDIO_FOLDER = '/static/audio'
    BABEL_DEFAULT_LOCALE = 'fr'  # Définit la langue par défaut


app = Flask(__name__)
app.config.from_object(Config())
socketio = SocketIO(app, cors_allowed_origins="*", ping_timeout=60000, ping_interval=30000)


# Configuration de la base de données avec session scoped
engine = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
db_session = scoped_session(sessionmaker(autocommit=False,
                                        autoflush=False,
                                        bind=engine))

# Gestion du scheduler / CRON
scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

# Pour le logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s %(name)s %(threadName)s : %(message)s')


@app.teardown_appcontext
def remove_session(ex=None):
    db_session.remove()

db = SQLAlchemy(app)
migrate = Migrate(app, db)  # Initialisation de Flask-Migrate
babel = Babel(app)


class Patient(db.Model):
    id = db.Column(db.Integer, Sequence('patient_id_seq'), primary_key=True)
    call_number = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    status = db.Column(db.String(50), nullable=False, default='standing')
    counter_id = db.Column(db.Integer, db.ForeignKey('counter.id', name='fk_patient_counter_id'), nullable=True)  # nullable=True si un patient peut ne pas être à un comptoir
    counter = db.relationship('Counter', backref=db.backref('patients', lazy=True))
    activity_id = db.Column(db.Integer, db.ForeignKey('activity.id', name='fk_patient_activity_id'), nullable=False)  # Now referencing the activity directly
    activity = db.relationship('Activity', backref=db.backref('patients', lazy=True))
    overtaken = db.Column(db.Integer, default=0)


    def __repr__(self):
        return f'<Patient {self.call_number}> ({self.id})'
    
    def to_dict(self):
        return {
            "id": self.id,
            "call_number": self.call_number,
            "activity_id": self.activity_id,
            "timestamp": self.timestamp.strftime('%Y-%m-%d %H:%M:%S'),  # Format datetime as string
            "status": self.status,
            "counter_id": self.counter_id
        }
    

counters_activities = db.Table('counters_activities',
    db.Column('counter_id', db.Integer, db.ForeignKey('counter.id'), primary_key=True),
    db.Column('activity_id', db.Integer, db.ForeignKey('activity.id'), primary_key=True)
)

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

    def __repr__(self):
        return f'<Counter {self.name}>'


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


class Activity(db.Model):
    __tablename__ = 'activity'
    id = db.Column(db.Integer, Sequence('activity_id_seq'), primary_key=True)
    code = db.Column(db.String(20), nullable=False, unique=True)
    name = db.Column(db.String(100), nullable=False)
    letter = db.Column(db.String(1), nullable=False)

    def __repr__(self):
        return f'<Activity {self.code} - {self.name}>'
    

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
    key = db.Column(db.String(50), unique=True, nullable=False)
    value_str = db.Column(db.String(200))  # Pour les chaînes de caractères
    value_int = db.Column(db.Integer)     # Pour les entiers
    value_bool = db.Column(db.Boolean)    # Pour les valeurs booléennes
    value_text = db.Column(db.Text)       # Pour les très longues chaînes

    def __repr__(self):
        return f'<ConfigOption {self.key}: {self.value_str or self.value_int or self.value_bool or self.value_text}>'



class ConfigVersion(db.Model):
    id = db.Column(db.Integer, Sequence('config_version_id_seq'), primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
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
    

class Language(db.Model):
    __tablename__ = 'language'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(2), nullable=False, unique=True)
    name = db.Column(db.String(50), nullable=False)
    traduction = db.Column(db.String(50), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('code', name='uq_language_code'),
    )

class Text(db.Model):
    __tablename__ = 'text'
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), nullable=False, unique=True)

    __table_args__ = (
        db.UniqueConstraint('key', name='uq_text_key'),
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



# A mettre dans la BDD ?
status_list = ['ongoing', 'standing', 'done', 'calling']

# Permet de définir le type de fichiers autorisés pour l'ajout d'images
def allowed_image_file(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]


def get_locale():
    return session.get('lang', request.accept_languages.best_match(['en', 'fr']))
babel.init_app(app, locale_selector=get_locale)


@app.before_request
def set_locale():
    from flask import request
    user_language = request.cookies.get('lang', 'fr')  # Exemple: lire la langue depuis un cookie
    request.babel_locale = user_language


@app.route('/')
def home():
    return "Bonjour la pharmacie!"


@app.route('/admin_old')
def admin_old():
    all_counters = Counter.query.all()  # Récupère tous les comptoirs de la base de données
    return render_template('admin.html', counters=all_counters)

# --------   ADMIN   ---------
@app.route('/admin')
def admin():
    return render_template('/admin/admin.html')


# --------  ADMIN -> Queue ---------

@app.route('/admin/queue')
def queue():
    activities = Activity.query.all()
    return render_template('admin/queue.html', activities=activities)


# affiche le tableau des patients
@app.route('/admin/queue/table', methods=['POST'])
def display_queue_table():
    print(request.form)
    # Récupération des statuts en une liste pour tri ultérieur
    filters = [status for status, value in request.form.items() if value == 'true']
    print("Filters received:", filters)

    # Filtrage des patients en fonction des statuts sélectionnés
    if filters:
        patients = Patient.query.filter(Patient.status.in_(filters)).all()
    else:
        patients = Patient.query.all()

    return render_template('admin/queue_htmx_table.html', 
                            patients=patients, 
                            activities=Activity.query.all(), 
                            status_list=status_list,
                            counters=Counter.query.all())


# affiche la modale pour confirmer la suppression de toute la table patient
@app.route('/admin/database/confirm_delete_patient_table', methods=['GET'])
def confirm_delete_patient_table():
    return render_template('/admin/queue_modal_confirm_delete.html')


@app.route('/admin/database/clear_all_patients')
def clear_all_patients_from_db():
    print("Suppression de la table Patient")
    with app.app_context():  # Nécessaire pour pouvoir effacer la table via le CRON
        try:
            db.session.query(Patient).delete()
            db.session.commit()
            app.logger.info("La table Patient a été vidée")
            notification("update_patients")
            return display_toast(message="La table Patient a été vidée")
        except Exception as e:
            db.session.rollback()
            app.logger.error(str(e))
            return display_toast(success = False, message=str(e))


# mise à jour des informations d'un patient
@app.route('/admin/queue/patient_update/<int:patient_id>', methods=['POST'])
def update_patient(patient_id):
    try:
        patient = Patient.query.get(patient_id)
        if patient:
            print(request.form)
            if request.form.get('call_number') == '':
                socketio.emit('display_toast', {'success': False, 'message': "Un numéro d'appel est obligatoire"})
                return ""
            patient.call_number = request.form.get('call_number', patient.call_number)
            patient.status = request.form.get('status', patient.status)
            activity_id = request.form.get('activity_id', patient.activity)
            patient.activity = Activity.query.get(activity_id)
            counter_id = request.form.get('counter_id', patient.counter)
            patient.counter = Counter.query.get(counter_id)

            db.session.commit()

            return display_toast(message="Mise à jour effectuée")
            return ""
        else:
            socketio.emit('display_toast', {'success': False, 'message': "Membre de l'équipe introuvable"})
            return ""

    except Exception as e:
            socketio.emit('display_toast', {'success': False, 'message': "erreur : " + str(e)})
            print(e)
            return jsonify(status="error", message=str(e)), 500


# affiche la modale pour confirmer la suppression d'un patient particulier
@app.route('/admin/queue/confirm_delete_patient/<int:patient_id>', methods=['GET'])
def confirm_delete_patient(patient_id):
    print("Confirmation de la suppression d'un patient")
    patient = Patient.query.get(patient_id)
    return render_template('/admin/queue_modal_confirm_delete_patient.html', patient=patient)


# supprime un patient
@app.route('/admin/queue/delete_patient/<int:patient_id>', methods=['GET'])
def delete_patient(patient_id):
    try:
        patient = Patient.query.get(patient_id)
        if not patient:
            return display_toast(success=False, message="Patient introuvable")

        db.session.delete(patient)
        db.session.commit()
        notification("update_patients")
        return display_toast()

    except Exception as e:
        app.logger(e)
        return display_toast(success=False, message=str(e))


# TODO PREVOIR "NONE" pour Activité ou équivalent 
@app.route('/admin/queue/create_new_patient_auto', methods=['POST'])
def create_new_patient_auto():
    activity = Activity.query.get(request.form.get('activity_id'))
    print("activity_id", request.form)
    print("activity", activity)
    call_number = get_next_call_number(activity)
    new_patient = add_patient(call_number, activity)
    notification("update_patients")

    return "", 204



# --------  FIn de ADMIN -> Queue ---------


@app.route('/admin/update_switch', methods=['POST'])
def update_switch():
    """ Mise à jour des switches d'options de l'application """
    key = request.values.get('key')
    value = request.values.get('value')
    print("update_switch", key, value)
    try:
        # MAJ BDD
        config_option = ConfigOption.query.filter_by(key=key).first()
        # MAJ Config
        app.config[key.upper()] = value
        if config_option:
            config_option.value_bool = True if value == "true" else False
            db.session.commit()
            call_function_with_switch(key, value)
            return display_toast()
        else:
            return display_toast(success='False', message="Option non trouvée.")
    except Exception as e:
            print(e)
            return display_toast(success='False', message=str(e))


def call_function_with_switch(key, value):
    """ Permet d'effectuer une action lors de l'activation d'un switch en plus de la sauvegarde"""
    if key == "cron_delete_patient_table_activated":
        if value == "true":
            scheduler_clear_all_patients()
        else:
            remove_scheduler_clear_all_patients()


# --------  ADMIN -> App  ---------

@app.route('/admin/app')
def admin_app():
    numbering_by_activity = ConfigOption.query.filter_by(key="numbering_by_activity").first().value_bool
    announce_sound = ConfigOption.query.filter_by(key="announce_sound").first().value_bool
    announce_staff_name = ConfigOption.query.filter_by(key="announce_staff_name").first().value_bool
    return render_template('/admin/app.html', 
                            numbering_by_activity = numbering_by_activity, 
                            announce_sound = announce_sound,
                            announce_staff_name = announce_staff_name)


@app.route('/admin/app/update_numbering_by_activity', methods=['POST'])
def update_numbering_by_activity():
    new_value = request.values.get('numbering_by_activity')
    print("new_value", new_value)
    try:
        # Récupérer la valeur du checkbox à partir de la requête
        new_value = request.values.get('numbering_by_activity')
        config_option = ConfigOption.query.filter_by(key="numbering_by_activity").first()
        if config_option:
            config_option.value_bool = True if new_value == "true" else False
            db.session.commit()
            socketio.emit('display_toast', {'success': True, 'message': "Mise à jour réussie. Redémarrer le serveur pour que les nouvelles configurations s'appliquent."})
            return ""
        else:
            socketio.emit('display_toast', {'success': False, 'message': "Configuration option not found"})
            return ""
    except Exception as e:
            socketio.emit('display_toast', {'success': False, 'message': "erreur : " + str(e)})
            print(e)
            return jsonify(status="error", message=str(e)), 500
    

# --------  FIn ADMIN -> Main   ---------


# --------  ADMIN -> DataBase  ---------

@app.route('/admin/database')
def admin_database():
    cron_delete_patient_table_activated = app.config["CRON_DELETE_PATIENT_TABLE_ACTIVATED"]
    return render_template('/admin/database.html',
                        cron_delete_patient_table_activated = cron_delete_patient_table_activated)


@app.route("/admin/database/schedule_tasks_list")
def display_schedule_tasks_list():
    jobs = scheduler.get_jobs()
    jobs_info = [{
        'id': job.id,
        'next_run_time': str(job.next_run_time),
        'function_name': job.func.__name__
    } for job in jobs]
    return render_template('/admin/database_schedule_tasks_list.html',
                        jobs_info=jobs_info)


def scheduler_clear_all_patients():
    # vide la table patient à minuit
    try:
        scheduler.add_job(id='Clear Patient Table', func=clear_all_patients_from_db, trigger='cron', hour=00, minute=00)
        app.logger.info("Job 'Clear Patient Table' successfully added.")
        notification("update_admin", data="schedule_tasks_list")
        return True
    except Exception as e:
        app.logger.error(f"Failed to add job 'Clear Patient Table': {e}")
        return False


def remove_scheduler_clear_all_patients():
    try:
        # Supprime le job à l'aide de son id
        scheduler.remove_job('Clear Patient Table')
        app.logger.info("Job 'Clear Patient Table' successfully removed.")
        notification("update_admin", data="schedule_tasks_list")
        return True
    except Exception as e:
        app.logger.error(f"Failed to remove job 'Clear Patient Table': {e}")
        return False


def clear_old_patients_table():
    # Vérifie si la fonctionnalité est activée dans la configuration
    if app.config.get("CRON_DELETE_PATIENT_TABLE_ACTIVATED", False):
        # Obtenez la date actuelle en UTC
        today = datetime.now(timezone.utc).date()
        
        # Construisez la requête pour trouver tous les patients dont la date est antérieure à aujourd'hui
        old_patients = Patient.query.filter(Patient.timestamp < today)
        
        # Supprimez ces patients
        old_patients.delete(synchronize_session='fetch')
        db.session.commit()
        notification("update_patients")
        app.logger.info(f"Deleted old patients not from today ({today}).")
    else:
        app.logger.info("Deletion of old patients is disabled.")


# --------  FIn ADMIN -> DataBase  ---------


# --------  ADMIN -> Staff   ---------

# base
@app.route('/admin/staff')
def staff():
    return render_template('/admin/staff.html')

# affiche la table de l'équipe
@app.route('/admin/staff/table')
def display_staff_table():
    staff = Pharmacist.query.all()
    activities = Activity.query.all()
    print("ALL", staff)
    return render_template('admin/staff_htmx_table.html', staff=staff, activities=activities)


# mise à jour des informations d'un membre
@app.route('/admin/staff/member_update/<int:member_id>', methods=['POST'])
def update_member(member_id):
    try:
        member = Pharmacist.query.get(member_id)
        if member:
            if request.form.get('name') == '':
                socketio.emit('display_toast', {'success': False, 'message': "Le nom est obligatoire"})
                return ""
            if request.form.get('initials') == '':
                socketio.emit('display_toast', {'success': False, 'message': "Les initiales sont obligatoires"})
                return ""
            member.name = request.form.get('name', member.name)
            member.initials = request.form.get('initials', member.initials)
            member.language = request.form.get('language', member.language)
            activities_ids = request.form.getlist('activities')

            # Suppression des activités ajoutées pour éviter les erreur de duplication
            activities_ids = request.form.getlist('activities')
            new_activities = Activity.query.filter(Activity.id.in_(activities_ids)).all()

            # Clear existing activities and add the new ones
            member.activities = new_activities

            db.session.commit()
            socketio.emit('display_toast', {'success': True, 'message': 'Mise à jour réussie'})
            return ""
        else:
            socketio.emit('display_toast', {'success': False, 'message': "Membre de l'équipe introuvable"})
            return ""

    except Exception as e:
            socketio.emit('display_toast', {'success': False, 'message': "erreur : " + str(e)})
            return jsonify(status="error", message=str(e)), 500


# affiche la modale pour confirmer la suppression d'un membre
@app.route('/admin/staff/confirm_delete/<int:member_id>', methods=['GET'])
def confirm_delete(member_id):
    staff = Pharmacist.query.get(member_id)
    return render_template('/admin/staff_modal_confirm_delete.html', staff=staff)


# supprime un membre de l'equipe
@app.route('/admin/staff/delete/<int:member_id>', methods=['GET'])
def delete_staff(member_id):
    try:
        member = Pharmacist.query.get(member_id)
        if not member:
            socketio.emit('display_toast', {'success': False, 'message': "Membre non trouvé"})
            return display_staff_table()

        db.session.delete(member)
        db.session.commit()
        socketio.emit('display_toast', {'success': True, 'message': 'Suppression réussie'})
        return display_staff_table()

    except Exception as e:
        socketio.emit('display_toast', {'success': False, 'message': "erreur : " + str(e)})
        return display_staff_table()
    

# affiche le formulaire pour ajouter un membre
@app.route('/admin/staff/add_form')
def add_staff_form():
    activities = Activity.query.all()
    return render_template('/admin/staff_add_form.html', activities=activities)


# enregistre le membre dans la Bdd
@app.route('/admin/staff/add_new_staff', methods=['POST'])
def add_new_staff():
    try:
        name = request.form.get('name')
        initials = request.form.get('initials')
        language = request.form.get('language')
        activities_ids = request.form.getlist('activities')

        if not name:  # Vérifiez que les champs obligatoires sont remplis
            socketio.emit('display_toast', {'success': False, 'message': "Nom obligatoire"})
            return display_staff_table()
        if not initials:  # Vérifiez que les champs obligatoires sont remplis
            socketio.emit('display_toast', {'success': False, 'message': "Initiales obligatoires"})
            return display_staff_table()

        new_staff = Pharmacist(
            name=name,
            initials=initials,
            language=language,
        )
        db.session.add(new_staff)
        db.session.commit()

        # Associer les activités sélectionnées avec le nouveau pharmacien
        print("ACTIVITIES IDS", activities_ids)
        for activity_id in activities_ids:
            print("ACTIVITY ID", activity_id)
            activity = Activity.query.get(int(activity_id))
            if activity:
                new_staff.activities.append(activity)
        db.session.commit()

        socketio.emit('delete_add_staff_form')
        socketio.emit('display_toast', {'success': True, 'message': "Membre ajouté avec succès"})

        return display_staff_table()

    except Exception as e:
        db.session.rollback()
        socketio.emit('display_toast', {'success': False, 'message': "erreur : " + str(e)})
        return display_staff_table()

# --------  FIN  de ADMIN -> Staff   ---------

# --------  ADMIN -> Activity  ---------

# page de base
@app.route('/admin/activity')
def activity():
    return render_template('/admin/activity.html')

# affiche le tableau des activités 
@app.route('/admin/activity/table')
def display_activity_table():
    activities = Activity.query.all()
    print("ALL", staff)
    return render_template('admin/activity_htmx_table.html', activities=activities)


# mise à jour des informations d'une activité 
@app.route('/admin/activity/activity_update/<int:activity_id>', methods=['POST'])
def update_activity(activity_id):
    try:
        activity = Activity.query.get(activity_id)
        if activity:
            if request.form.get('name') == '':
                socketio.emit('display_toast', {'success': False, 'message': "Le nom est obligatoire"})
                return ""
            if request.form.get('code') == '':
                socketio.emit('display_toast', {'success': False, 'message': "Le code est obligatoire"})
                return ""
            if request.form.get('letter') == '':
                socketio.emit('display_toast', {'success': False, 'message': "La lettre est obligatoire"})
                return ""
            activity.name = request.form.get('name', activity.name)
            activity.code = request.form.get('code', activity.code)
            activity.letter = request.form.get('letter', activity.letter)
            db.session.commit()
            socketio.emit('display_toast', {'success': True, 'message': 'Mise à jour réussie'})
            return ""
        else:
            socketio.emit('display_toast', {'success': False, 'message': "Activité introuvable"})
            return ""

    except Exception as e:
            print(e)
            socketio.emit('display_toast', {'success': False, 'message': "erreur : " + str(e)})
            return ""


# affiche la modale pour confirmer la suppression d'une activité
@app.route('/admin/activity/confirm_delete/<int:activity_id>', methods=['GET'])
def confirm_delete_activity(activity_id):
    activity = Activity.query.get(activity_id)
    return render_template('/admin/activity_modal_confirm_delete.html', activity=activity)


# supprime un membre de l'equipe
@app.route('/admin/activity/delete/<int:activity_id>', methods=['GET'])
def delete_activity(activity_id):
    try:
        activity = Activity.query.get(activity_id)
        if not activity:
            socketio.emit('display_toast', {'success': False, 'message': "Activité non trouvée"})
            return display_activity_table()

        db.session.delete(activity)
        db.session.commit()
        socketio.emit('display_toast', {'success': True, 'message': 'Suppression réussie'})
        return display_activity_table()

    except Exception as e:
        socketio.emit('display_toast', {'success': False, 'message': "erreur : " + str(e)})
        return display_activity_table()
    

# affiche le formulaire pour ajouter un activité
@app.route('/admin/activity/add_form')
def add_activity_form():
    return render_template('/admin/activity_add_form.html')


# enregistre le membre dans la Bdd
@app.route('/admin/activity/add_new_activity', methods=['POST'])
def add_new_activity():
    try:
        name = request.form.get('name')
        code = request.form.get('code')
        letter = request.form.get('letter')

        if not name:  # Vérifiez que les champs obligatoires sont remplis
            socketio.emit('display_toast', {'success': False, 'message': "Nom obligatoire"})
            return display_activity_table()
        if not code:  # Vérifiez que les champs obligatoires sont remplis
            socketio.emit('display_toast', {'success': False, 'message': "Code obligatoire"})
            return display_activity_table()

        new_activity = Activity(
            name=name,
            code=code,
            letter=letter,
        )
        db.session.add(new_activity)
        db.session.commit()
        socketio.emit('delete_add_activity_form')
        socketio.emit('display_toast', {'success': True, 'message': "Activité ajoutée avec succès"})

        return display_activity_table()

    except Exception as e:
        db.session.rollback()
        socketio.emit('display_toast', {'success': False, 'message': "erreur : " + str(e)})
        return display_activity_table()


# -------- Fin de ADMIN -> Activity  ---------


# --------  ADMIN -> Algo  ---------

# page de base
@app.route('/admin/algo')
def admin_algo():
    algo_overtaken_limit = app.config['ALGO_OVERTAKEN_LIMIT']
    return render_template('/admin/algo.html',
                            algo_overtaken_limit=algo_overtaken_limit)

@app.route('/admin/algo/table')
def display_algo_table():
    rules = AlgoRule.query.all()
    activities = Activity.query.all()
    return render_template('admin/algo_htmx_table.html', rules=rules, activities=activities)

# affiche le formulaire activer ou desactiver l'algorithme
@app.route('/admin/button_des_activate_algo')
def button_des_activate_algo():
    return render_template("admin/algo_des_activate_buttons.html",
                            algo_activated= app.config['ALGO_IS_ACTIVATED'])

# active ou desactive l'algorithme, enregistre l'info, retourne les boutons
@app.route('/admin/algo/toggle_activation')
def toggle_activation():
    action = request.args.get('action', 'activate')
    is_activated = action == 'activate'
    
    app.config['ALGO_IS_ACTIVATED'] = is_activated
    algo_activated = ConfigOption.query.filter_by(key="algo_activate").first()
    algo_activated.value_bool = is_activated
    db.session.commit()

    return render_template("admin/algo_des_activate_buttons.html",
                            algo_activated=app.config['ALGO_IS_ACTIVATED'])


@app.route('/admin/algo/change_overtaken_limit', methods=['POST'])
def change_overtaken_limit():
    overtaken_limit = request.form.get('overtaken_limit')

    app.config['ALGO_OVERTAKEN_LIMIT'] = overtaken_limit
    try:
        algo_overtaken_limit = ConfigOption.query.filter_by(key="algo_overtaken_limit").first()
        algo_overtaken_limit.value_int = overtaken_limit
        db.session.commit()
        return display_toast()
    except Exception as e:
        print(e)
        return display_toast(success='False', message=str(e))


# affiche le formulaire pour ajouter une regle de l'algo
@app.route('/admin/algo/add_rule_form')
def add_rule_form():
    activities = Activity.query.all()
    return render_template('/admin/algo_add_rule_form.html', activities=activities)


# enregistre la regledans la Bdd
@app.route('/admin/algo/add_new_rule', methods=['POST'])
def add_new_rule():
    print("add_new_rule", request.form)
    try:
        name = request.form.get('name')
        activity = Activity.query.get(request.form.get('activity_id'))
        print("A", activity)
        priority_level = request.form.get('priority_level')
        min_patients = request.form.get('min_patients')
        max_patients = request.form.get('max_patients')
        max_overtaken = request.form.get('max_overtaken')
        start_time_str = request.form.get('start_time')            
        start_time = datetime.strptime(start_time_str, "%H:%M").time()
        end_time_str = request.form.get('end_time')
        end_time = datetime.strptime(end_time_str, "%H:%M").time()

        if not name:  # Vérifiez que les champs obligatoires sont remplis
            socketio.emit('display_toast', {'success': False, 'message': "Nom obligatoire"})
            return display_algo_table()

        new_rule = AlgoRule(
            name=name,
            activity = activity,
            priority_level = priority_level,
            min_patients = min_patients,
            max_patients = max_patients,
            max_overtaken = max_overtaken,
            start_time = start_time,
            end_time = end_time
        )
        db.session.add(new_rule)
        db.session.commit()

        socketio.emit('delete_add_rule_form')
        socketio.emit('display_toast', {'success': True, 'message': "Règle ajoutée avec succès"})

        return display_algo_table()

    except Exception as e:
        db.session.rollback()
        socketio.emit('display_toast', {'success': False, 'message': "erreur : " + str(e)})
        print(e)
        return display_algo_table()


# affiche la modale pour confirmer la suppression d'un membre
@app.route('/admin/algo/confirm_delete_rule/<int:rule_id>', methods=['GET'])
def confirm_delete_rule(rule_id):
    rule = AlgoRule.query.get(rule_id)
    return render_template('/admin/algo_modal_confirm_delete_rule.html', rule=rule)


# supprime une regle de l'algo
@app.route('/admin/algo/delete_rule/<int:algo_id>', methods=['GET'])
def delete_algo(algo_id):
    try:
        rule = AlgoRule.query.get(algo_id)
        if not rule:
            socketio.emit('display_toast', {'success': False, 'message': "Règle non trouvée"})
            return display_algo_table()

        db.session.delete(rule)
        db.session.commit()
        socketio.emit('display_toast', {'success': True, 'message': 'Suppression réussie'})
        return display_algo_table()

    except Exception as e:
        socketio.emit('display_toast', {'success': False, 'message': "erreur : " + str(e)})
        return display_algo_table()


@app.route('/admin/algo/rule_update/<int:rule_id>', methods=['POST'])
def update_algo_rule(rule_id):
    try:
        rule = AlgoRule.query.get(rule_id)
        if rule:
            if request.form.get('name') == '':
                socketio.emit('display_toast', {'success': False, 'message': "Le nom est obligatoire"})
                return ""

            rule.name = request.form.get('name', rule.name)
            activity = Activity.query.get(request.form.get('activity_id', rule.activity_id))
            rule.activity = activity
            rule.priority_level = request.form.get('priority_level', rule.priority_level)
            rule.min_patients = request.form.get('min_patients', rule.min_patients)
            rule.max_patients = request.form.get('max_patients', rule.max_patients)
            rule.max_overtaken = request.form.get('max_overtaken', rule.max_overtaken)
            start_time_str = request.form.get('start_time', rule.start_time.strftime("%H:%M"))            
            rule.start_time = datetime.strptime(start_time_str, "%H:%M").time()
            end_time_str = request.form.get('end_time', rule.end_time.strftime("%H:%M"))
            rule.end_time = datetime.strptime(end_time_str, "%H:%M").time()

            db.session.commit()

            socketio.emit('display_toast', {'success': True, 'message': 'Mise à jour réussie'})
            return ""
        else:
            socketio.emit('display_toast', {'success': False, 'message': "Règle introuvable"})
            return ""

    except Exception as e:
            socketio.emit('display_toast', {'success': False, 'message': "erreur : " + str(e)})
            print(e)
            return jsonify(status="error", message=str(e)), 500


# -------- Fin de ADMIN -> Algo  ---------


# --------  ADMIN -> Counter  ---------

# page de base
@app.route('/admin/counter')
def admin_counter():
    return render_template('/admin/counter.html')


# affiche le tableau des counters 
@app.route('/admin/counter/table')
def display_counter_table():
    counters = Counter.query.all()
    activities = Activity.query.all()
    return render_template('admin/counter_htmx_table.html', counters=counters, activities = activities)


# mise à jour des informations d'un counter
@app.route('/admin/counter/counter_update/<int:counter_id>', methods=['POST'])
def update_counter(counter_id):
    try:
        counter = Counter.query.get(counter_id)
        if counter:
            if request.form.get('name') == '':
                socketio.emit('display_toast', {'success': False, 'message': "Le nom est obligatoire"})
                return ""
            counter.name = request.form.get('name', counter.name)
            activities_ids = request.form.getlist('activities')

            # Suppression des activités ajoutées pour éviter les erreur de duplication
            activities_ids = request.form.getlist('activities')
            new_activities = Activity.query.filter(Activity.id.in_(activities_ids)).all()

            # Clear existing activities and add the new ones
            counter.activities = new_activities

            db.session.commit()
            socketio.emit('display_toast', {'success': True, 'message': 'Mise à jour réussie'})
            return ""
        else:
            socketio.emit('display_toast', {'success': False, 'message': "Comptoir introuvable"})
            return ""

    except Exception as e:
            socketio.emit('display_toast', {'success': False, 'message': "erreur : " + str(e)})
            print("ERREURRRR", e)
            return jsonify(status="error", message=str(e)), 500


# affiche la modale pour confirmer la suppression d'un comptoir
@app.route('/admin/counter/confirm_delete/<int:counter_id>', methods=['GET'])
def confirm_delete_counter(counter_id):
    counter = Counter.query.get(counter_id)
    return render_template('/admin/counter_modal_confirm_delete.html', counter=counter)


# supprime un comptoir
@app.route('/admin/counter/delete/<int:counter_id>', methods=['GET'])
def delete_counter(counter_id):
    try:
        counter = Pharmacist.query.get(counter_id)
        if not counter:
            socketio.emit('display_toast', {'success': False, 'message': "Comptoir non trouvé"})
            return display_counter_table()

        db.session.delete(counter)
        db.session.commit()
        socketio.emit('display_toast', {'success': True, 'message': 'Suppression réussie'})
        return display_counter_table()

    except Exception as e:
        socketio.emit('display_toast', {'success': False, 'message': "erreur : " + str(e)})
        return display_counter_table()


# affiche le formulaire pour ajouter un counter
@app.route('/admin/counter/add_form')
def add_counter_form():
    activities = Activity.query.all()
    return render_template('/admin/counter_add_form.html', activities=activities)


# enregistre le comptoir dans la Bdd
@app.route('/admin/counter/add_new_counter', methods=['POST'])
def add_new_counter():
    try:
        name = request.form.get('name')
        activities_ids = request.form.getlist('activities')

        if not name:  # Vérifiez que les champs obligatoires sont remplis
            socketio.emit('display_toast', {'success': False, 'message': "Nom obligatoire"})
            return display_activity_table()

        new_counter = Counter(
            name=name,
        )
        db.session.add(new_counter)
        db.session.commit()


        # Associer les activités sélectionnées avec le nouveau pharmacien
        for activity_id in activities_ids:
            activity = Activity.query.get(int(activity_id))
            if activity:
                new_counter.activities.append(activity)
        db.session.commit()

        socketio.emit('delete_add_counter_form')
        socketio.emit('display_toast', {'success': True, 'message': "Comptoir ajouté avec succès"})

        return display_counter_table()

    except Exception as e:
        db.session.rollback()
        socketio.emit('display_toast', {'success': False, 'message': "erreur : " + str(e)})
        return display_activity_table()


# -------- fin de ADMIN -> Counter  ---------

# --------  ADMIN -> Page patient  ---------
@app.route('/admin/patient')
def admin_patient():
    buttons = Button.query.all()
    print("BUTTONS", buttons)
    return render_template('/admin/patient_page.html', buttons=buttons)


# affiche le tableau des boutons 
@app.route('/admin/patient/button_table')
def display_button_table():
    buttons = Button.query.all()
    activities = Activity.query.all()
    return render_template('admin/patient_page_htmx_buttons_table.html', buttons=buttons, activities = activities)


# affiche la liste des boutons pour le 
@app.route('/admin/patient/display_parent_buttons/<int:button_id>', methods=['GET'])
def display_children_buttons(button_id):
    buttons = Button.query.filter_by(is_parent=True).all()
    button = Button.query.get(button_id)
    return render_template('admin/patient_page_button_display_children.html', buttons=buttons, button=button)


# mise à jour des informations d'un bouton
@app.route('/admin/patient/button_update/<int:button_id>', methods=['POST'])
def update_button(button_id):
    try:
        button = Button.query.get(button_id)
        if button:
            # Récupérer l'ID de l'activité depuis le formulaire
            activity_id = request.form.get('activity')
            # GEstion du cas ou le bouton est un bouton parent
            if activity_id == "parent_button":
                button.is_parent = True
                button.activity = None
            else:
                # Récupérer l'instance de l'activité correspondante
                if activity_id:
                    activity = Activity.query.get(activity_id)
                    if activity:
                        button.activity = activity
                        button.is_parent = False
                    else:
                        print("Activité non trouvée")
                        return "Activité non trouvée", 404
                else:
                    # Si aucun ID d'activité n'est fourni, on peut décider de mettre l'attribut à None
                    button.activity = None
            
            parent_btn_id = request.form.get('parent_btn')
            if parent_btn_id:
                parent_button = Button.query.get(parent_btn_id)
                if parent_button:
                    button.parent_button = parent_button

            is_present = True if request.form.get('is_present') == "true" else False
            button.is_present = is_present

            button.label = request.form.get('label', button.label)
            button.label_en = request.form.get('label_en', button.label_en)
            #button.is_present = request.form.get('is_present', button.is_present)
            

            db.session.commit()
            socketio.emit('display_toast', {'success': True, 'message': 'Mise à jour réussie'})
            return ""
        else:
            socketio.emit('display_toast', {'success': False, 'message': "Membre de l'équipe introuvable"})
            return ""

    except Exception as e:
            socketio.emit('display_toast', {'success': False, 'message': "erreur : " + str(e)})
            print("ERREURRRR", e)
            return jsonify(status="error", message=str(e)), 500



# affiche la modale pour confirmer la suppression d'un patient
@app.route('/admin/patient/confirm_delete_button/<int:button_id>', methods=['GET'])
def confirm_delete_button(button_id):
    button = Button.query.get(button_id)
    return render_template('/admin/patient_page_button_modal_confirm_delete.html', button=button)


# supprime un bouton
@app.route('/admin/patient/delete_button/<int:button_id>', methods=['GET'])
def delete_button(button_id):
    try:
        button = Button.query.get(button_id)
        if not button:
            socketio.emit('display_toast', {'success': False, 'message': "Bouton non trouvé"})
            return display_button_table()

        db.session.delete(button)
        db.session.commit()
        socketio.emit('display_toast', {'success': True, 'message': 'Suppression réussie'})
        return display_button_table()

    except Exception as e:
        socketio.emit('display_toast', {'success': False, 'message': "erreur : " + str(e)})
        print(e)
        return display_button_table()


@app.route('/upload_image/<int:button_id>', methods=['POST'])
def upload_image(button_id):
    """ Pas réussi à faire sans rechargement de page, car problème pour passer image sans formulaire """
    button = Button.query.get(button_id)
    if 'file' not in request.files:
        return "No file part", 400
    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400
    if file and allowed_image_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.static_folder, 'images/buttons',  filename)
        file.save(file_path)
        button.image_url = filename
        db.session.commit()
        # Retour à la page admin/patient
        return redirect("/admin/patient", code=302)
    return "Invalid file", 400


@app.route('/admin/patient/gallery_button_images/<int:button_id>', methods=['GET'])
def gallery_button_images(button_id):
    directory = os.path.join(current_app.static_folder, 'images/buttons')
    images = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
    button = Button.query.get(button_id)
    print(images)
    return render_template('/admin/patient_page_button_modal_gallery.html', images=images, button=button)


@app.route('/admin/patient/update_button_image_from_gallery', methods=['POST'])
def update_button_image_from_gallery():
    button_id = request.form.get('button_id')
    image_url = request.form.get('image')
    button = Button.query.get(button_id)
    print(request.form)
    button.image_url = image_url
    db.session.commit()
    return """<img src="{{ url_for('static', filename='images/buttons/' ~ button.image_url) }}" alt="Button Image" style="width: 100px;">"""


@app.route("/admin/patient/delete_button_image/<int:button_id>", methods=['GET'])
def delete_button_image(button_id):
    button = Button.query.get(button_id)
    button.image_url = None
    db.session.commit()
    return "<div>Pas d'image</div>"

# -------- fin de ADMIN -> Page patient  ---------


# -------- ADMIN -> Page Announce  ---------

@app.route('/admin/announce')
def announce_page():
    announce_sound = app.config['ANNOUNCE_SOUND']
    print("langue", announce_sound)
    announce_staff_name = app.config['ANNOUNCE_STAFF_NAME']
    return render_template('/admin/announce.html', 
                            announce_sound = announce_sound,
                            announce_staff_name = announce_staff_name)

# -------- fin de ADMIN -> Page Announce  ---------


# -------- ADMIN -> Page INfos ---------

@app.route('/admin/info')
def admin_info():
    announce_infos_display = app.config['ANNOUNCE_INFOS_DISPLAY']
    return render_template('/admin/info.html', announce_infos_display=announce_infos_display)


# --------  Fin ADMIN -> Page INfos ---------



@app.route('/patient_right_page_default')
def patient_right_page_default():
    print("default")
    return render_template('htmx/patient_right_page_default.html')



def generate_audio_calling(counter_number, next_patient):
    # Texte pour la synthèse vocale
    text = f"Nous invitons le patient {next_patient.call_number} à se rendre au comptoir {counter_number}."
    tts = gTTS(text, lang='fr', tld='ca')  # Utilisation de gTTS avec langue française

    # Chemin de sauvegarde du fichier audio
    audiofile = f'patient_{next_patient.call_number}.mp3'
    audio_path = os.path.join(app.static_folder, 'audio/annonces', audiofile)  # Enregistrement dans le dossier 'static/audio'

    # Assurer que le répertoire existe
    if not os.path.exists(os.path.dirname(audio_path)):
        os.makedirs(os.path.dirname(audio_path))

    # Sauvegarde du fichier audio
    tts.save(audio_path)

    # Envoi du chemin relatif via Socket.IO
    audio_url = url_for('static', filename=f'audio/annonces/{audiofile}', _external=True)
    notification("update_audio", audio_source=audio_url)


@app.route('/call_specific_patient/<int:counter_id>/<int:patient_id>')
def call_specific_patient(counter_id, patient_id):
    print("specifique", patient_id)

    validate_current_patient(counter_id)

    # Récupération du patient spécifique
    next_patient = Patient.query.get(patient_id)
    
    if next_patient:
        print("Appel du patient :", patient_id, "au comptoir", counter_id)
        # Mise à jour du statut du patient
        next_patient.status = 'calling'
        next_patient.counter_id = counter_id
        db.session.commit()

        # Notifier tous les clients et mettre à jour le comptoir
        notification("update_patients")
        notification("update_counter", counter_id)

        generate_audio_calling(counter_id, next_patient)
    else:
        print("Aucun patient trouvé avec l'ID :", patient_id)
    
    print("next_patient", type(next_patient.id))

    # Redirection vers la page du comptoir ou une autre page appropriée
    return '', 204



@app.route('/validate_patient/<int:counter_id>/<int:patient_id>', methods=['POST', 'GET'])
def validate_patient(counter_id, patient_id):
    # Valide le patient actuel au comptoir sans appeler le prochain
    current_patient = Patient.query.get(patient_id)
    if current_patient:
        current_patient.status = 'ongoing'
        db.session.commit()

    notification("update_patients")
    notification("update_counter", counter_id)    

    #return redirect(url_for('counter', counter_number=counter_number, current_patient_id=current_patient.id))
    return '', 204  # No content to send back


@app.route('/update_patient_status')
def update_patient_status():
    # Mettez à jour la base de données comme nécessaire
    #socketio.emit('htmx:load', {'data': 'update'})
    return 'Status Updated'


# Route pour la page patients qui accepte une langue via l'URL
@app.route('/patients/<lang>')
def patients_langue(lang):
    session['lang'] = lang
    print(session['lang'])
    return render_template('patients.html', cache=False)



# ---------------- PAGE PATIENT FRONT ----------------

@app.route('/patient')
def patients_front_page():
    return render_template('patient/patient_front_page.html')


@app.route('/tests')
def test():
    return render_template('patient/test.html')


# affiche les boutons
@app.route('/patient/patient_buttons')
def patient_right_page():
    buttons = Button.query.filter_by(is_present = True, parent_button_id = None).all()
    print("BUTTONS", buttons)
    return render_template('patient/patient_buttons_left.html', buttons=buttons)


@app.route('/patients_submit', methods=['POST'])
def patients_submit():
    print("patients_submit")
    # Récupération des données du formulaire
    print('SUBMIT', request.form)
    if request.form.get('is_parent')  == 'True':
        return display_children_buttons_for_right_page(request)
    else:
        return display_validation_after_choice(request)


# affiche les boutons "enfants" de droite
def display_children_buttons_for_right_page(request):
    children_buttons = Button.query.filter_by(is_present = True, parent_button_id = request.form.get('button_id')).all()
    print("children_buttons", children_buttons)
    return render_template('patient/patient_buttons_left.html', buttons=children_buttons)


# affiche la page de validation pour page gauche et droite
def display_validation_after_choice(request):
    activity_id = request.form.get('activity_id')
    print("reason", activity_id)

    # Si le bouton contient bien une activité
    if activity_id != "":
        activity = Activity.query.get(activity_id)
        print("activity", activity.id)
        socketio.emit('trigger_valide_activity', {'activity': activity.id})
        return left_page_validate_patient(activity)
    

# page de validation (QR Code, Impression, Validation, Annulation)
def left_page_validate_patient(activity):
    call_number = get_next_call_number(activity)
    new_patient = add_patient(call_number, activity)
    image_name_qr = create_qr_code(new_patient)
    text = f"{call_number}"
    # rafraichissement des pages display et counter
    # envoye de data pour être récupéré sous forme de liste par PySide
    socketio.emit('trigger_update_patient', {})
    notification("update_patients")
    #socketio.emit('trigger_new_patient', {"patient_standing": list_patients_standing()})
    return render_template('patient/patient_qr_right_page.html', image_name_qr=image_name_qr, text=text)


def add_patient(call_number, activity):
    """ CRéation d'un nouveau patient et ajout à la BDD"""
    # Création d'un nouvel objet Patient
    print('    call_number 2', call_number)
    new_patient = Patient(
        call_number= call_number,  # Vous devez définir cette fonction pour générer le numéro d'appel
        activity = activity,
        timestamp=datetime.now(timezone.utc),
        status='standing'
    )    
    # Ajout à la base de données
    db.session.add(new_patient)
    db.session.commit()  # Enregistrement des changements dans la base de données

    return new_patient


# liste des patients en attente : Nécessaire pour être transmis à Pyside
def list_patients_standing():
    patients_standing = Patient.query.filter_by(status='standing').all()
    patients_data = [patient.to_dict() for patient in patients_standing]
    return patients_data


def get_next_call_number(activity):
    """ Récupérer le numéro d'appel en fonction de la méthode choisie"""
    numbering_by_activity = app.config.get('NUMBERING_BY_ACTIVITY', False)
    if numbering_by_activity:
        call_number = get_next_category_number(activity)
    else:
        call_number = get_next_call_number_simple()
    print("call_number", call_number)
    return call_number


def get_next_call_number_simple():
    # Obtenir le dernier patient enregistré aujourd'hui
    # BUG Connu : Si on passe de simple à par activité puis retour à simple -> repart à 1
    last_patient_today = Patient.query.filter(db.func.date(Patient.timestamp) == date.today()).order_by(Patient.id.desc()).first()
    if last_patient_today:
        if str(last_patient_today.call_number).isdigit():
            print(last_patient_today.call_number, type(last_patient_today.call_number))
            return last_patient_today.call_number + 1
        return 1
    return 1  # Réinitialiser le compteur si aucun patient n'a été enregistré aujourd'hui


# Générer le numéro d'appel en fonction de l'activité
def get_next_category_number(activity):
    # on utilise le code prévu de l'activité. Plusieurs activités peuvent avoir la même lettre
    letter_prefix = activity.letter
    today = date.today()

    # Compter combien de patients sont déjà enregistrés aujourd'hui avec le même préfixe de lettre
    today_count = Patient.query.filter(
        db.func.date(Patient.timestamp) == today,
        db.func.substr(Patient.call_number, 1, 1) == letter_prefix
    ).count()

    # Le prochain numéro sera le nombre actuel + 1
    next_number = today_count + 1

    return f"{letter_prefix}-{next_number}"


def create_qr_code(patient):
    patient_info = {
            "id": patient.id,
            "patient_number": patient.call_number,
            "date_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reason": patient.activity.code
    }
        
    # Convertir les données en chaîne JSON
    data = json.dumps(patient_info)

    # Générer le QR Code
    img = qrcode.make(data)
    
    # Utiliser app.static_folder pour obtenir le chemin absolu vers le dossier static
    directory = os.path.join(current_app.static_folder, 'qr_patients')
    filename = f'qr_patient-{patient.id}.png'
    img_path = os.path.join(directory, filename)

    # Assurer que le répertoire existe
    if not os.path.exists(directory):
        os.makedirs(directory)  # Créer le dossier s'il n'existe pas

    # Enregistrement de l'image dans le dossier static
    img.save(img_path)

    return filename


# ---------------- FIN  PAGE PATIENTS FRONT ----------------


# ---------------- PAGE COUNTER FRONT ----------------

#  On utilise l'ID dans l'URL pour éviter les erreurs (espace dans le nom...)
@app.route('/counter/<int:counter_id>')
def counter(counter_id):
    print("counter_number", counter_id)
    counter = Counter.query.get(counter_id)
    activities = Activity.query.all()
    # si l'id du comptoir n'existe pas -> page avec liste des comptoirs
    if not counter:
        return wrong_counter(counter_id)
    return render_template('counter/counter.html', 
                            counter=counter,
                            activities=activities)


# si le comptoir n'existe pas -> page avec liste des comptoirs
def wrong_counter(counter_id):
    return render_template('counter/wrong_counter.html', 
                    counters=Counter.query.all(), 
                    counter_id=counter_id)


@app.route('/current_patient_for_counter/<int:counter_id>')
def current_patient_for_counter(counter_id):
    """ Affiche le patient en cours de traitement pour un comptoir """
    print('counter_number', counter_id)
    patient = Patient.query.filter(
        Patient.counter_id == counter_id,
        Patient.status != 'done'
    ).first()
    print("CURRENT", patient)
    return render_template('counter/current_patient_for_counter.html', patient=patient)


@app.route('/counter/buttons/<int:counter_id>/')
def counter_refresh_buttons(counter_id):
    patient = Patient.query.filter(
        Patient.counter_id == counter_id, 
        Patient.status != "done"
    ).first()
    print("Patient", patient)
    if not patient:
        patient_id = None
        patient_status = None
    else :
        patient_id = patient.id
        patient_status = patient.status

    return render_template('/counter/display_buttons.html', counter_id=counter_id, patient_id=patient_id, status=patient_status)



@app.route('/validate_and_call_next/<int:counter_id>', methods=['POST', 'GET'])
def validate_and_call_next(counter_id):
    print('validate_and_call_next', counter_id)

    validate_current_patient(counter_id)

    # TODO Prevoir que ne renvoie rien
    next_patient = call_next(counter_id)

    notification("update_patients")
    notification("update_counter", counter_id)

    socketio.emit('trigger_new_patient', {"patient_standing": list_patients_standing()})
    socketio.emit('trigger_patient_ongoing', {})  
    return '', 204  # No content to send back


def validate_current_patient(counter_id):
    # si patient actuel 
    patients_at_counter = Patient.query.filter_by(counter_id=counter_id).all()
    if patients_at_counter :
        print("patient dans le comptoir")
        # Mise à jour du statut pour tous les patients au comptoir
        Patient.query.filter_by(counter_id=counter_id).update({'status': 'done'})
        db.session.commit()    
    else:
        print("pas de patient")


@app.route('/call_next/<int:counter_id>')
def call_next(counter_id):

    # CHoix du patient suivant pour un comptoir appelant

    next_patient = algo_choice_next_patient(counter_id)


    if next_patient:
        #socketio.emit('trigger_htmx_update', {})  # ????
        # Met à jour le statut du patient
        next_patient.status = 'calling'
        next_patient.counter_id = counter_id
        db.session.commit()
        socketio.emit('trigger_patient_calling', {'last_patient_number': next_patient.call_number})
        # Optionnel: Ajoutez ici u système pour annoncer le patient au système audio ou un écran d'affichage

        generate_audio_calling(counter_id, next_patient)

    else:
        next_patient = None

    return next_patient


def algo_choice_next_patient(counter_id):

    counter = Counter.query.get(counter_id)

    #priority = Activity.query.get(5)
    #priority = None

    # activités possible par ce pharmacien
    staff_activities = set(activity.id for activity in counter.staff.activities)

    # choix parmi les patients qui attendent 
    next_possible_patient = Patient.query.filter_by(status='standing')

    # choix parmi les patient qui correspondent aux activités du pharmacien
    next_possible_patient = next_possible_patient.filter(
        Patient.activity_id.in_(staff_activities)
    )

    # permet de voir si un patient s'est fait doubler plus que le nombre prévu
    # Si oui on bloque l'algo le temps de rétablir l'équilibre
    is_patient_waiting_too_long = Patient.query.filter(
        and_(Patient.status == 'standing',
                Patient.overtaken >= app.config["ALGO_OVERTAKEN_LIMIT"])).first()
    print("is_patient_waiting_too_long", is_patient_waiting_too_long)

    applicable_rules = None
    if app.config['ALGO_IS_ACTIVATED'] and not is_patient_waiting_too_long:
    # priorité à un type d'activité si un patient répond aux critère
    # Récupération des règles applicables
        current_day = datetime.now().weekday()
        current_time = datetime.now().time()
        print('current_time', current_time)
        print('current_day', current_day)
        number_of_patients = Patient.query.filter_by(status='standing').count()
        print('number_of_patients', number_of_patients)

        # cherche des regles applicables
        applicable_rules = AlgoRule.query.filter(
            AlgoRule.start_time <= current_time,  # L'heure actuelle doit être après l'heure de début
            AlgoRule.end_time >= current_time,    # et avant l'heure de fin
            AlgoRule.min_patients <= number_of_patients,  # Le nombre de patients doit être dans l'intervalle
            AlgoRule.max_patients >= number_of_patients,
            #AlgoRule.days_of_week.contains(current_day)  # Le jour actuel doit être inclus dans les jours valides
        )
        print("applicable_rules", applicable_rules)

        # S'il y a des regles applicables, regarde niveau par niveau les activités correspondantes
        if applicable_rules:
            for level in range(1, 6):
                rules_at_level = applicable_rules.filter(AlgoRule.priority_level == level)
                if rules_at_level:
                    print("level", level)
                    activity_ids_from_rules  = [rule.activity_id for rule in rules_at_level]
                    next_possible_patient_via_rules = next_possible_patient.filter(
                        Patient.activity_id.in_(activity_ids_from_rules)
                    )

                    print("next_possible_patient", next_possible_patient_via_rules.all())
                    # pour les patients qui rentrent dans les priorité on va regarder si on ne dépasse pas le nombre de patients à dépasser de la régle
                    if next_possible_patient_via_rules.all():
                        for patient in next_possible_patient_via_rules.all():
                            print("patient", patient)
                            patients_ahead_count = next_possible_patient.filter(
                                                                                        Patient.timestamp < patient.timestamp
                                                                                    ).count()
                            max_overtaken = min(rule.max_overtaken for rule in patient.activity.priority_rules)
                            print("max_overtaken", max_overtaken, patients_ahead_count)
                            if patients_ahead_count < max_overtaken:
                                next_possible_patient = next_possible_patient_via_rules
                                break

    print('next_possible_patient', next_possible_patient)
    
    # tri par date 
    next_patient = next_possible_patient.order_by(Patient.timestamp).first()

    if applicable_rules:
        patient_overtaken(next_patient)

    print('next_patient', next_patient)

    return next_patient


def patient_overtaken(next_patient):
    """ Met a jour le nombre de fois que le patient a été doublé"""
    patients_overtaken = Patient.query.filter(
        and_(
            Patient.status == 'standing',
            Patient.timestamp < next_patient.timestamp 
        )
    ).all() 

    for patient in patients_overtaken:
        patient.overtaken = patient.overtaken + 1
        print("patient overtaken", patient)
        db.session.commit()



@app.route('/pause_patient/<int:counter_id>/<int:patient_id>', methods=['POST', 'GET'])
def pause_patient(counter_id, patient_id):
    # Valide le patient actuel au comptoir sans appeler le prochain
    print("pause_patient")
    print("p", patient_id, "c",counter_id)
    current_patient = Patient.query.get(patient_id)
    if current_patient:
        current_patient.status = 'done'
        db.session.commit()

    socketio.emit('trigger_patient_calling', {})
    socketio.emit('trigger_patient_ongoing', {})  

    return '', 204  # No content to send back


@app.route('/counter/patients_queue_for_counter/<int:counter_id>', methods=['GET'])
def patients_queue_for_counter(counter_id):
    patients = Patient.query.filter_by(status='standing').order_by(Patient.timestamp).all()
    return render_template('/counter/patients_queue_for_counter.html', patients=patients, counter_id=counter_id)


@app.route('/counter/is_staff_on_counter/<int:counter_id>', methods=['GET'])
def is_staff_on_counter(counter_id):
    counter = Counter.query.get(counter_id)
    # emet un signal pour provoquer le réaffichage de la liste des activités
    #socketio.emit("trigger_connect_staff", {})
    print("EMITTTT")
    return render_template('counter/staff_on_counter.html', staff=counter.staff)


@app.route('/counter/remove_staff', methods=['POST'])
def remove_counter_staff():
    counter = Counter.query.get(request.form.get('counter_id')) 
    counter.staff = None
    db.session.commit()
    return is_staff_on_counter(request.form.get('counter_id'))


@app.route('/counter/update_staff', methods=['POST'])
def update_counter_staff():
    print(request.form)
    counter = Counter.query.get(request.form.get('counter_id'))  
    initials = request.form.get('initials')
    staff = Pharmacist.query.filter(func.lower(Pharmacist.initials) == func.lower(initials)).first()
    if staff:
        # si demande de déconnexion
        if request.form.get('deconnect') == "true":
            # deconnexion de tous les postes
            deconnect_staff_from_all_counters(staff)
        # Ajout du membre de l'équipe au comptoir        
        counter.staff = staff
        db.session.commit()

        # On rappelle la base de données pour être sûr que bonne personne au bon comptoir
        return is_staff_on_counter(request.form.get('counter_id'))

    # Si les initiales ne correspondent à rien
    # on déconnecte l'utilisateur précedemement connecté
    counter.staff = None
    db.session.commit()
    # on affiche une erreur à la place du nom
    return render_template('counter/staff_on_counter.html', staff=False)


def deconnect_staff_from_all_counters(staff):
    """ Déconnecte le membre de l'équipe de tous les comptoirs """
    for counter in Counter.query.all():
        if counter.staff == staff:
            counter.staff = None
            db.session.commit()
            socketio.emit("trigger_disconnect_staff", {})


@app.route('/counter/list_of_activities', methods=['POST'])
def list_of_activities():
    activities = Activity.query.all()
    staff_id = request.form.get('staff_id')
    if staff_id == "0":
        # TODO Créer un user "Anonyme" ????
        # si personne au comptoir, on affiche toutes les activités
        staff_activities_ids = [activity.id for activity in activities]

    else:     
        staff = Pharmacist.query.get(staff_id)
        # on renvoie les activités du membre de l'équipe pour les cocher dans la liste
        staff_activities_ids = [activity.id for activity in staff.activities]

    return render_template('counter/counter_list_of_activities.html', activities=activities, staff_activities_ids=staff_activities_ids)


@app.route("/counter/select_patient/<int:counter_id>/<int:patient_id>", methods=['GET'])
def counter_select_patient(counter_id, patient_id):
    """ Appeler lors du choix d'un patient spécifique au comptoir """
    print("counter_select_patient", counter_id, patient_id)
    call_specific_patient(counter_id, patient_id)

    notification("update_patients")
    notification("update_counter", counter_id)    

    return '', 204

# ---------------- FIN  PAGE COUNTER FRONT ----------------


# ---------------- PAGE AnnoNces FRONT ----------------


@app.route('/display')
def display():
    return render_template('/announce/announce.html', current_patients=current_patients)


@app.route('/announce/patients_calling')
def patients_calling():
    patients = Patient.query.filter_by(status='calling').order_by(Patient.call_number).all()
    return render_template('announce/patients_calling.html', patients=patients)


@app.route('/announce/patients_ongoing')
def patients_ongoing():
    patients = Patient.query.filter_by(status='ongoing').order_by(Patient.counter_id).all()
    print("Patients")
    return render_template('announce/patients_ongoing.html', patients=patients)


# ---------------- FIN  PAGE AnnoNces FRONT ----------------



# ---------------- FONCTIONS Généralistes / COmmunication ---------------- 

# liste des flux SSE
update_patients = []
update_admin = []
play_sound_streams = []
counter_streams = {}


def notify_clients(clients):
    for client in clients:
        try:
            client.put(True, timeout=1)
        except:
            pass


def event_stream(clients):
    while True:
        client = Queue()
        clients.append(client)
        try:
            while True:
                message = client.get()
                print("message", message)
                yield f'data: {message}\n\n'
        except GeneratorExit:
            clients.remove(client)


def event_stream_dict(client_id):
    while True:
        client = Queue()
        counter_streams[client_id] = client
        try:
            while True:
                message = client.get()
                yield f'data: {message}\n\n'
        except GeneratorExit:
            # Assurez-vous de retirer le client de la liste en cas de fermeture
            counter_streams.pop(client_id, None)
        finally:
            counter_streams.pop(client_id, None)


@app.route('/events/update_patients')
def events_update_patients():
    return Response(event_stream(update_patients), content_type='text/event-stream')


@app.route('/events/sound_calling')
def events_update_sound_calling():
    return Response(event_stream(play_sound_streams), content_type='text/event-stream')


@app.route('/events/update_counter/<int:client_id>')
def events_update_counter(client_id):
    print("counter id", client_id)
    return Response(event_stream_dict(client_id), content_type='text/event-stream')


@app.route('/events/update_admin')
def events_update_admin():
    return Response(event_stream(update_admin), content_type='text/event-stream')


def notification(stream, data=None, client_id = None, audio_source=None):
    """ Effectue la communication avec les clients """
    message = {"type": stream, "data": ""}
    print("notification", stream, client_id, audio_source, message)
    
    # SSE
    if stream == "update_patients":
        for client in update_patients:
            client.put(message)
    elif stream == "update_admin":
        for client in update_admin:
            client.put(data)
    elif stream == "update_counter":
        if client_id in counter_streams:
            counter_streams[client_id].put(message)
    elif stream == "update_audio":
        message["data"] = {"audio_url": audio_source}
        for client in play_sound_streams:
            client.put(json.dumps(message))

    # Websocket
    #socketio.emit('trigger_patient_ongoing', {})  

def display_toast(success="True", message=None):
    """ Affiche le toast dans la page Admin.
    Pour validation réussie, on peut simplement appeler la fonction sans argument """
    if message is None:
        message = "Enregistrement effectué"
        
    data = {'success': success, 'message': message}
    return f'<script>display_toast({data})</script>'


# ---------------- FIN FONCTIONS Généralistes ---------------- 


@app.route('/current_patients')
def current_patients():
    # Supposons que vous vouliez afficher les patients dont le statut est "au comptoir"
    patients = Patient.query.filter_by(status='au comptoir').all()
    print(patients)
    return render_template('htmx/update_patients.html', patients=patients)


@app.route('/add_counter', methods=['POST'])
def add_counter():
    if request.method == 'POST':
        name = request.form['name']
        new_counter = Counter(name=name)
        db.session.add(new_counter)
        db.session.commit()
        return redirect('/admin')
    return "Erreur dans la soumission du formulaire"


@app.route('/patients_queue')
def patients_queue():
    patients = Patient.query.filter_by(status='standing').order_by(Patient.timestamp).all()
    return render_template('htmx/patients_queue.html', patients=patients)


@app.route('/pharmacists')
def pharmacists():
    all_pharmacists = Pharmacist.query.all()
    print("ALL", all_pharmacists)
    return render_template('pharmacists.html', pharmacists=all_pharmacists)


@app.route('/update_pharmacist/<int:pharmacist_id>', methods=['POST'])
def update_pharmacist(pharmacist_id):
    pharmacist = Pharmacist.query.get(pharmacist_id)
    if pharmacist:
        pharmacist.name = request.form.get('name', pharmacist.name)
        pharmacist.initials = request.form.get('initials', pharmacist.initials)
        pharmacist.language = request.form.get('language', pharmacist.language)
        pharmacist.is_active = 'is_active' in request.form
        pharmacist.activity = request.form.get('activity', pharmacist.activity)
        db.session.commit()
    return redirect(url_for('pharmacists'))


@app.route('/add_pharmacist', methods=['POST'])
def add_pharmacist():
    name = request.form.get('name')
    initials = request.form.get('initials')
    language = request.form.get('language')
    is_active = request.form.get('is_active') == 'on'
    activity = request.form.get('activity')
    new_pharmacist = Pharmacist(name=name, initials=initials, language=language, is_active=is_active, activity=activity)
    db.session.add(new_pharmacist)
    db.session.commit()
    return render_template('htmx/menu_admin_pharmacist_row.html', pharmacist=new_pharmacist)

@app.route('/new_pharmacist_form')
def new_pharmacist_form():
    print("new_pharmacist_form")
    return render_template('htmx/menu_admin_new_pharmacist_form.html')




@socketio.on('connect')
def test_connect():
    print('Client connected')

@socketio.on('disconnect')
def test_disconnect():
    print('Client disconnected')


# Liste des images dans le répertoire statique
image_dir = os.path.join(app.static_folder, "images/annonces")
images = [os.path.join("/static/images/annonces", image) for image in os.listdir(image_dir) if image.endswith((".png", ".jpg", ".jpeg"))]

@app.route('/next_image/<int:index>')
def next_image(index):
    # Sélectionner l'image suivante en boucle
    image_url = images[index % len(images)]
    return render_template('htmx/display_image_announcement.html', image_url=image_url)


# Définir un filtre pour Jinja2
@app.template_filter('format_time')
def format_time(value):
    return value.strftime('%H:%M') if value else ''


def init_activity_data_from_json(json_file='static/json/activities.json'):
    print("Initialisation de la base de données...")
    # Vérifier si la table est vide
    if Activity.query.first() is None:
        # Charger les activités depuis le fichier JSON
        if os.path.exists(json_file):
            with open(json_file, 'r') as f:
                activities = json.load(f)

            # Ajouter chaque activité à la base de données
            for activity in activities:
                new_activity = Activity(
                    code=activity['code'],
                    name=activity['name'],
                    letter=activity['letter']
                )
                db.session.add(new_activity)

            # Valider les changements
            db.session.commit()
            print("Base de données initialisée avec des activités prédéfinies.")

        else:
            print(f"Fichier {json_file} introuvable.")
    else:
        print("La base de données contient déjà des données.")



# Charge des valeurs qui ne sont pas amener à changer avant redémarrage APP
def load_configuration(app, ConfigOption):
    app.logger.info("Loading configuration from database")
    # Supposons que cette fonction charge la configuration depuis la base de données
    numbering_by_activity = ConfigOption.query.filter_by(key="numbering_by_activity").first()
    if numbering_by_activity:
        app.config['NUMBERING_BY_ACTIVITY'] = numbering_by_activity.value_bool
    algo_activated = ConfigOption.query.filter_by(key="algo_activate").first()
    if algo_activated:
        app.config['ALGO_IS_ACTIVATED'] = algo_activated.value_bool
    algo_overtaken_limit = ConfigOption.query.filter_by(key="algo_overtaken_limit").first()
    if algo_overtaken_limit:
        app.config['ALGO_OVERTAKEN_LIMIT'] = algo_overtaken_limit.value_int
    printer = ConfigOption.query.filter_by(key="printer").first()
    if printer:
        app.config['PRINTER'] = printer.value_bool
    announce_sound = ConfigOption.query.filter_by(key="announce_sound").first()
    if announce_sound:
        app.config['ANNOUNCE_SOUND'] = announce_sound.value_bool
    announce_staff_name = ConfigOption.query.filter_by(key="announce_staff_name").first()
    if announce_staff_name:
        app.config['ANNOUNCE_STAFF_NAME'] = announce_staff_name.value_bool
    announce_infos_display = ConfigOption.query.filter_by(key="announce_infos_display").first()
    if announce_infos_display:
        app.config['ANNOUNCE_INFOS_DISPLAY'] = announce_infos_display.value_bool
    cron_delete_patient_table_activated = ConfigOption.query.filter_by(key="cron_delete_patient_table_activated").first()
    if cron_delete_patient_table_activated:
        app.config['CRON_DELETE_PATIENT_TABLE_ACTIVATED'] = cron_delete_patient_table_activated.value_bool
        # si au lancement on veut une planif de l'effacement de la table on s'assure que ce soit fait
        if app.config['CRON_DELETE_PATIENT_TABLE_ACTIVATED']:
            scheduler_clear_all_patients()


# creation BDD si besoin et initialise certaines tables (Activités)
with app.app_context():
    print("Creating database tables...")
    #if not os.path.exists("database.duckdb"):
    db.create_all()  # permet de recréer les Bdd si n'existent pas. None = default + config + buttons
    init_activity_data_from_json()  # Initialiser les données d'activité si nécessaire
    init_default_options_db_from_json(app, db, ConfigVersion, ConfigOption)  # Initialiser les données d'activité si nécessaire
    init_update_default_buttons_db_from_json(ConfigVersion, Button, db)  # Init ou Maj des boutons partients
    init_default_languages_db_from_json(Language, db)
    init_or_update_default_texts_db_from_json(ConfigVersion, Text, db)
    init_update_default_translations_db_from_json(ConfigVersion, TextTranslation, Text, Language, db)
    init_default_algo_rules_db_from_json(ConfigVersion, AlgoRule, db)
    load_configuration(app, ConfigOption)
    clear_old_patients_table()
    
    
if __name__ == "__main__":
    app.logger.info("Starting Flask app...")

    # Utilisez la variable d'environnement PORT si disponible, sinon défaut à 5000
    port = int(os.environ.get("PORT", 5000))
    # Activez le mode debug basé sur une variable d'environnement (définissez-la à True en développement)
    debug = os.environ.get("DEBUG", "False") == "True"
    socketio.run(app, host='0.0.0.0', port=port, debug=debug)




    


