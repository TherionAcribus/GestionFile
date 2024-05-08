# TODO : on load comptoir verfier si on est en train de servir ou appeler qq (pb rechargement de page)
# POSSIBLE : laisser vide si comptoires vides et affichage uniquement si tous les comptoirs occupés
# TODO : Affichage d'un message en etranger si patient etranger "on going"
# TODO : Si choix langue en etranger -> Diriger vers comptoir en etranger
# TODO : Bouton Help ?

# deux lignes a appeler avant tout le reste (pour server Render)
#import eventlet
#eventlet.monkey_patch()
from flask import Flask, render_template, request, redirect, url_for, session, current_app, jsonify
import duckdb
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy import create_engine, ForeignKeyConstraint, UniqueConstraint, Sequence
from flask_migrate import Migrate
from flask_socketio import SocketIO
from datetime import datetime, timezone, date
from flask_wtf import FlaskForm  # A mettre en place : Pour sécurisation
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired
from flask_babel import Babel
from gtts import gTTS
import qrcode
import json
import os

from bdd import init_update_default_buttons_db_from_json


app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*")
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///queuedatabase.db'  # base de données pour les comptoirs, équipes et patients
#app.config['SQLALCHEMY_DATABASE_URI'] = 'duckdb:///database.duckdb'
app.config['AUDIO_FOLDER'] = '/static/audio'
app.config['BABEL_DEFAULT_LOCALE'] = 'fr'  # Définit la langue par défaut

# Configuration de la base de données avec session scoped
engine = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
db_session = scoped_session(sessionmaker(autocommit=False,
                                        autoflush=False,
                                        bind=engine))


@app.teardown_appcontext
def remove_session(ex=None):
    db_session.remove()

db = SQLAlchemy(app)
migrate = Migrate(app, db)  # Initialisation de Flask-Migrate
babel = Babel(app)


class Patient(db.Model):
    id = db.Column(db.Integer, Sequence('patient_id_seq'), primary_key=True)
    call_number = db.Column(db.Integer, nullable=False)
    visit_reason = db.Column(db.String(120), nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    status = db.Column(db.String(50), nullable=False, default='standing') 

    def __repr__(self):
        return f'<Patient {self.call_number}> ({self.id})'
    
    def to_dict(self):
        return {
            "id": self.id,
            "call_number": self.call_number,
            "visit_reason": self.visit_reason,
            "timestamp": self.timestamp.strftime('%Y-%m-%d %H:%M:%S'),  # Format datetime as string
            "status": self.status
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
    version = db.Column(db.String(50), nullable=False, unique=True)
    comments = db.Column(db.Text)
    date = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<ConfigVersion {self.version}>'


class Button(db.Model):
    __tablename__ = 'button'

    id = db.Column(db.Integer, Sequence('button_id_seq'), primary_key=True)
    by_user = db.Column(db.Boolean, default=False)  # True si le bouton est créé par un user. Permet de savoir si bouton d'origine ou non
    code = db.Column(db.String(20), nullable=True, unique=True)  # Code unique est interne pour les boutons d'origine du logiciel. Permet de les reconnaitre même si le titre change.
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
            print("ERREURRRR", e)
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
    activities =Activity.query.all()
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


# mise à jour des informations d'un membre
@app.route('/admin/patient/button_update/<int:button_id>', methods=['POST'])
def update_button(button_id):
    try:
        button = Button.query.get(button_id)
        if button:
            # Récupérer l'ID de l'activité depuis le formulaire
            activity_id = request.form.get('activity')

            # Récupérer l'instance de l'activité correspondante
            if activity_id:
                activity = Activity.query.get(activity_id)
                if activity:
                    button.activity = activity
                else:
                    return "Activité non trouvée", 404
            else:
                # Si aucun ID d'activité n'est fourni, on peut décider de mettre l'attribut à None
                button.activity = None
            
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



# -------- fin de ADMIN -> Page patient  ---------


@app.route('/patients_old')
def patients_old():
    return render_template('patients.html')


@app.route('/patient')
def patients_front_page():
    return render_template('patient/patient_front_page.html')

@app.route('/patient_right_page_default')
def patient_right_page_default():
    print("default")
    return render_template('htmx/patient_right_page_default.html')


# affiche les boutons de gauche
@app.route('/patient/patient_buttons_left')
def patient_right_page():
    buttons = Button.query.filter_by(is_present = True).all()
    print("BUTTONS", buttons)
    return render_template('patient/patient_buttons_left.html', buttons=buttons)


@app.route('/counter_old/<int:counter_number>')
def counter_old(counter_number):

    # Récupérez le paramètre 'next_patient' si présent
    next_patient_id = request.args.get('next_patient_id')
    print("COUNTER", next_patient_id)
    if next_patient_id:
    # Vous pourriez vouloir récupérer des détails sur ce patient
        next_patient = Patient.query.get(int(next_patient_id))
        print("NEXT PATIENT", next_patient)
    else :
        next_patient = None

    current_patient_id = request.args.get("current_patient_id")
    if current_patient_id:
        current_patient = Patient.query.get(int(current_patient_id))
    else:
        current_patient = None

    # Récupère le premier patient en attente
    #next_patient = Patient.query.filter_by(status='standing').order_by(Patient.call_number).first()
    return render_template('counter.html', 
                            counter_number=counter_number, 
                            next_patient=next_patient, 
                            current_patient=current_patient)


@app.route('/counter/<int:counter_number>')
def counter(counter_number):
    return render_template('counter.html', 
                            counter_number=counter_number)


@app.route('/call_next/<int:counter_number>')
def call_next(counter_number):
    # Récupère le premier patient en attente 
    # TODO PERMETTRE DE FIXER DES REGLES ou CHOIX ARBITRAIRE

    next_patient = Patient.query.filter_by(status='standing').order_by(Patient.timestamp).first()

    if next_patient:
        #socketio.emit('trigger_htmx_update', {})  # ????
        # Met à jour le statut du patient
        next_patient.status = 'calling'
        next_patient.counter_number = counter_number
        db.session.commit()
        socketio.emit('trigger_patient_calling', {'last_patient_number': next_patient.call_number})
        # Optionnel: Ajoutez ici u système pour annoncer le patient au système audio ou un écran d'affichage

        generate_audio_calling(counter_number, next_patient)

    else:
        next_patient = None

    return next_patient

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
    socketio.emit('trigger_audio_calling', {'audio_url': audio_url})


def validate_current_patient(counter_number):
    # si patient actuel 
    patients_at_counter = Patient.query.filter_by(counter_number=counter_number).all()
    if patients_at_counter :
        print("patient dans le comptoir")
        # Mise à jour du statut pour tous les patients au comptoir
        Patient.query.filter_by(counter_number=counter_number).update({'status': 'done'})
        db.session.commit()    
    else:
        print("pas de patient")


@app.route('/validate_and_call_next/<int:counter_number>', methods=['POST', 'GET'])
def validate_and_call_next(counter_number):
    print('validate_and_call_next', counter_number)

    validate_current_patient(counter_number)

    # TODO Prevoir que ne renvoie rien
    next_patient = call_next(counter_number)  
    socketio.emit('trigger_new_patient', {"patient_standing": list_patients_standing()})
    socketio.emit('trigger_patient_ongoing', {})  
    return '', 204  # No content to send back


@app.route('/call_specific_patient/<int:counter_number>/<int:patient_id>')
def call_specific_patient(counter_number, patient_id):
    print("specifique", patient_id)

    validate_current_patient(counter_number)

    # Récupération du patient spécifique
    next_patient = Patient.query.get(patient_id)
    socketio.emit('trigger_new_patient', {"patient_standing": list_patients_standing()})
    socketio.emit('trigger_patient_ongoing', {})  
    
    if next_patient:
        print("Appel du patient :", patient_id, "au comptoir", counter_number)
        # Mise à jour du statut du patient
        next_patient.status = 'calling'
        next_patient.counter_number = counter_number
        db.session.commit()
       
        # Notifier tous les clients via SocketIO
        socketio.emit('trigger_patient_calling', {'last_patient_number': next_patient.call_number})
        socketio.emit('trigger_patient_ongoing', {})

        generate_audio_calling(counter_number, next_patient)
    else:
        print("Aucun patient trouvé avec l'ID :", patient_id)
    
    print("next_patient", type(next_patient.id))

    # Redirection vers la page du comptoir ou une autre page appropriée
    return '', 204



@app.route('/validate_patient/<int:counter_number>/<int:patient_id>', methods=['POST', 'GET'])
def validate_patient(counter_number, patient_id):
    # Valide le patient actuel au comptoir sans appeler le prochain
    current_patient = Patient.query.get(patient_id)
    if current_patient:
        current_patient.status = 'ongoing'
        db.session.commit()

    socketio.emit('trigger_patient_calling', {})
    socketio.emit('trigger_patient_ongoing', {})  

    #return redirect(url_for('counter', counter_number=counter_number, current_patient_id=current_patient.id))
    return '', 204  # No content to send back


@app.route('/pause_patient/<int:counter_number>/<int:patient_id>', methods=['POST', 'GET'])
def pause_patient(counter_number, patient_id):
    # Valide le patient actuel au comptoir sans appeler le prochain
    print("pause_patient")
    print("p", patient_id, "c",counter_number)
    current_patient = Patient.query.get(patient_id)
    if current_patient:
        current_patient.status = 'done'
        db.session.commit()

    socketio.emit('trigger_patient_calling', {})
    socketio.emit('trigger_patient_ongoing', {})  

    return '', 204  # No content to send back


@app.route('/queue')
def queue():
    # Récupère tous les patients en attente ou en train d'être servis
    patients = Patient.query.all()
    return render_template('queue.html', patients=patients)


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


@app.route('/display')
def display():
    current_patients = Patient.query.filter_by(status='au comptoir').order_by(Patient.call_number).all()
    return render_template('display.html', current_patients=current_patients)


@app.route('/patients_submit', methods=['POST'])
def patients_submit():
    print("patients_submit")
    # Récupération des données du formulaire
    reason = request.form.get('reason')

    if reason == 'rdv':
        return render_template('htmx/patient_right_page_test.html')

    if reason in ['vaccination', 'ordonnance', 'retrait', 'covid', 'angine', 'cystite']:
        call_number = get_next_category_number(reason)
        print("call_number", call_number)
        new_patient = add_patient(call_number, reason)
        # Création du QR Code
        image_qr = create_qr_code(new_patient)
        text = f"{call_number}"
        # rafraichissement des pages display et counter
        # envoye de data pour être récupéré sous forme de liste par PySide
        socketio.emit('trigger_new_patient', {"patient_standing": list_patients_standing()})
        return render_template('htmx/patient_qr_right_page.html', image_url=image_qr, text=text)
    
    else:
        print("Merde, la raison n'a pas été prévue....")


def list_patients_standing():
    patients_standing = Patient.query.filter_by(status='standing').all()
    patients_data = [patient.to_dict() for patient in patients_standing]
    return patients_data


def add_patient(call_number, reason):

    # Création d'un nouvel objet Patient
    new_patient = Patient(
        call_number= call_number,  # Vous devez définir cette fonction pour générer le numéro d'appel
        visit_reason=reason,
        timestamp=datetime.now(timezone.utc),
        status='standing'
    )
    
    # Ajout à la base de données
    db.session.add(new_patient)
    db.session.commit()  # Enregistrement des changements dans la base de données

    return new_patient

def create_qr_code(patient):
    patient_info = {
            "id": patient.id,
            "patient_number": patient.call_number,
            "date_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reason": patient.visit_reason
    }
        
    # Convertir les données en chaîne JSON
    data = json.dumps(patient_info)

    # Générer le QR Code
    img = qrcode.make(data)
    
    # Utiliser app.static_folder pour obtenir le chemin absolu vers le dossier static
    directory = os.path.join(current_app.static_folder, 'qr_patients')
    filename = 'qr_patient.png'
    img_path = os.path.join(directory, filename)

    # Assurer que le répertoire existe
    if not os.path.exists(directory):
        os.makedirs(directory)  # Créer le dossier s'il n'existe pas

    # Enregistrement de l'image dans le dossier static
    img.save(img_path)

    return img_path

def get_next_call_number_simple():
    # Obtenir le dernier patient enregistré aujourd'hui
    last_patient_today = Patient.query.filter(db.func.date(Patient.timestamp) == date.today()).order_by(Patient.id.desc()).first()
    if last_patient_today:
        return last_patient_today.call_number + 1
    return 1  # Réinitialiser le compteur si aucun patient n'a été enregistré aujourd'hui


def get_next_category_number(reason):
    category_to_letter = {
        'vaccination': 'V',
        'angine': 'T',
        'covid': 'T',
        'cystite': 'T',
        'rdv': 'R',
        'retrait': 'D',
        'ordonnance': 'O',
    }
    
    letter_prefix = category_to_letter.get(reason, 'X')  # 'X' sera utilisé si la catégorie n'est pas trouvée
    today = date.today()

    # Compter combien de patients sont déjà enregistrés aujourd'hui avec le même préfixe de lettre
    today_count = Patient.query.filter(
        db.func.date(Patient.timestamp) == today,
        db.func.substr(Patient.call_number, 1, 1) == letter_prefix
    ).count()

    # Le prochain numéro sera le nombre actuel + 1
    next_number = today_count + 1

    return f"{letter_prefix}-{next_number}"


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


@app.route('/patients_queue_for_counter')
def patients_queue_for_counter():
    patients = Patient.query.filter_by(status='standing').order_by(Patient.timestamp).all()
    return render_template('htmx/patients_queue_for_counter.html', patients=patients)


@app.route('/current_patient_for_counter/<int:counter_number>')
def current_patient_for_counter(counter_number):
    patient = Patient.query.filter(
    Patient.counter_number == counter_number, 
    Patient.status != "done"
    ).first()
    return render_template('htmx/current_patient_for_counter.html', patient=patient)


@app.route('/counter_refresh_buttons/<int:counter_number>/')
def counter_refresh_buttons(counter_number):
    patient = Patient.query.filter(
    Patient.counter_number == counter_number, 
    Patient.status != "done"
    ).first()
    if not patient:
        patient_id = None
        patient_status = None
    else :
        patient_id = patient.id
        patient_status = patient.status

    return render_template('htmx/counter/display_buttons.html', counter_number = counter_number, patient_id=patient_id, status=patient_status)


@app.route('/patients_calling')
def patients_calling():
    patients = Patient.query.filter_by(status='calling').order_by(Patient.call_number).all()
    return render_template('htmx/patients_calling.html', patients=patients)


@app.route('/patients_ongoing')
def patients_ongoing():
    patients = Patient.query.filter_by(status='ongoing').order_by(Patient.counter_number).all()
    print("Patients")
    return render_template('htmx/patients_ongoing.html', patients=patients)


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


@app.route('/clear_all_patients_from_db')
def clear_all_patients_from_db():
    try:
        num_rows_deleted = db.session.query(Patient).delete()
        db.session.commit()
        message = f'Successfully deleted {num_rows_deleted} patients.'
    except Exception as e:
        db.session.rollback()
        message = f'Error occurred: {str(e)}'
    return render_template('admin.html', message=message)

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


def init_default_options_db_from_json(json_file='static/json/default_config.json'):
    with app.app_context():        
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            current_version = ConfigVersion.query.first()

            if not current_version or current_version.version != data['version']:
                if current_version:

                    current_version.version = data['version']
                else:
                    db.session.add(ConfigVersion(version=data['version']))

                for key, value in data['configurations'].items():
                    config_option = ConfigOption.query.filter_by(key=key).first()
                    if config_option:
                        # Update the existing config option
                        if isinstance(value, str) and len(value) < 200:
                            config_option.value_str = value
                        elif isinstance(value, int):
                            config_option.value_int = value
                        elif isinstance(value, bool):
                            config_option.value_bool = value
                        elif isinstance(value, str):
                            config_option.value_text = value
                    else:
                        # Add new config option
                        new_option = ConfigOption(
                            key=key,
                            value_str=value if isinstance(value, str) and len(value) < 200 else None,
                            value_int=value if isinstance(value, int) else None,
                            value_bool=value if isinstance(value, bool) else None,
                            value_text=value if isinstance(value, str) and len(value) >= 200 else None
                        )
                        db.session.add(new_option)
                db.session.commit()

# creation BDD si besoin et initialise certaines tables (Activités)
with app.app_context():
    print("Creating database tables...")
    #if not os.path.exists("database.duckdb"):
    db.create_all()  # permet de recréer les Bdd si n'existent pas. None = default + config + buttons
    init_activity_data_from_json()  # Initialiser les données d'activité si nécessaire
    #init_default_options_db_from_json()  # Initialiser les données d'activité si nécessaire
    init_update_default_buttons_db_from_json(ConfigVersion, Button, db)  # Init ou Maj des boutons partients


if __name__ == "__main__":
    print("Starting Flask app...")  

    # Utilisez la variable d'environnement PORT si disponible, sinon défaut à 5000
    port = int(os.environ.get("PORT", 5000))
    # Activez le mode debug basé sur une variable d'environnement (définissez-la à True en développement)
    debug = os.environ.get("DEBUG", "False") == "True"
    socketio.run(app, host='0.0.0.0', port=port, debug=debug)




    


