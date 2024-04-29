# TODO : on load comptoir verfier si on est en train de servir ou appeler qq (pb rechargement de page)
# POSSIBLE : laisser vide si comptoires vides et affichage uniquement si tous les comptoirs occupés
# TODO : Affichage d'un message en etranger si patient etranger "on going"
# TODO : Si choix langue en etranger -> Diriger vers comptoir en etranger

# deux lignes a appeler avant tout le reste (pour server Render)
import eventlet
eventlet.monkey_patch()
from flask import Flask, render_template, request, redirect, url_for, session, current_app
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy import create_engine
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


app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*")
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///queuedatabase.db'
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
    id = db.Column(db.Integer, primary_key=True)
    call_number = db.Column(db.Integer, nullable=False)
    visit_reason = db.Column(db.String(120), nullable=False)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    status = db.Column(db.String(50), nullable=False, default='standing') 
    counter_number = db.Column(db.Integer, nullable=True)

    def __repr__(self):
        return f'<Patient {self.call_number}>'
    

class Counter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), nullable=False)
    is_active = db.Column(db.Boolean, default=False)  # Indique si le comptoir est actuellement utilisé
    non_actions = db.Column(db.String(255))  # Liste des actes non réalisés par ce comptoir
    priority_actions = db.Column(db.String(255))  # Liste des actions prioritaires réalisées par ce comptoir



    def __repr__(self):
        return f'<Counter {self.name}>'


with app.app_context():
    print("Creating database tables...")
    db.create_all()

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


@app.route('/admin')
def admin():
    all_counters = Counter.query.all()  # Récupère tous les comptoirs de la base de données
    return render_template('admin.html', counters=all_counters)


@app.route('/patients')
def patients():
    return render_template('patients.html')


@app.route('/counter/<int:counter_number>')
def counter(counter_number):

    # Récupérez le paramètre 'next_patient' si présent
    next_patient_id = request.args.get('next_patient_id')
    if next_patient_id:
    # Vous pourriez vouloir récupérer des détails sur ce patient
        next_patient = Patient.query.get(int(next_patient_id))
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


@app.route('/call_next/<int:counter_number>')
def call_next(counter_number):
    # Récupère le premier patient en attente 
    # TODO PERMETTRE DE FIXER DES REGLES ou CHOIX ARBITRAIRE

    next_patient = Patient.query.filter_by(status='standing').order_by(Patient.call_number).first()


    if next_patient:
        socketio.emit('trigger_htmx_update', {})  # ????

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





@app.route('/validate_and_call_next/<int:counter_number>')
def validate_and_call_next(counter_number):

    # si patient actuel 
    print(Patient.query.filter_by(counter_number=counter_number))
    patients_at_counter = Patient.query.filter_by(counter_number=counter_number).all()
    if patients_at_counter :
        print("patient dans le comptoir")
        # Mise à jour du statut pour tous les patients au comptoir
        Patient.query.filter_by(counter_number=counter_number).update({'status': 'done'})
        db.session.commit()

    else:
        print("pas de patient")

    # TODO Prevoir que ne renvoie rien
    next_patient = call_next(counter_number)  
    socketio.emit('trigger_new_patient', {})

    # Appelle automatiquement le prochain patient
    return redirect(url_for('counter', counter_number=counter_number, next_patient_id=next_patient.id if next_patient else None)) 



@app.route('/validate_patient/<int:counter_number>/<int:patient_number>')
def validate_patient(counter_number, patient_number):
    # Valide le patient actuel au comptoir sans appeler le prochain
    current_patient = Patient.query.get(patient_number)
    if current_patient:
        current_patient.status = 'ongoing'
        db.session.commit()

    socketio.emit('trigger_patient_calling', {})

    return redirect(url_for('counter', counter_number=counter_number, current_patient_id=current_patient.id))


@app.route('/pause_patient/<int:counter_number>/<int:current_patient_id>')
def pause_patient(counter_number, current_patient_id):
    # Valide le patient actuel au comptoir sans appeler le prochain
    print("pause_patient")
    print("p", current_patient_id, "c",counter_number)
    current_patient = Patient.query.get(current_patient_id)
    if current_patient:
        current_patient.status = 'done'
        db.session.commit()

    socketio.emit('trigger_patient_calling', {})

    return redirect(url_for('counter', counter_number=counter_number))


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
    # Récupération des données du formulaire
    reason = request.form.get('reason')
    name = request.form.get('name')  # Assurez-vous que ce champ est présent dans votre formulaire HTML

    call_number = get_next_call_number()

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

    # Création du QR Code
    image_qr = create_qr_code(call_number, reason)

    # rafraichissement des pages display et counter
    socketio.emit('trigger_new_patient', {})
    
    print(f"Name: {name}, Reason: {reason}, Time: {datetime.now(timezone.utc)}")
    return render_template('patients.html', image_qr=image_qr)  # Redirection vers la page du formulaire ou autre page de confirmation


def create_qr_code(call_number, reason):
    print(reason)
    print(call_number)
    patient_info = {
            "patient_number": call_number,
            "date_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason
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

def get_next_call_number():
    # Obtenir le dernier patient enregistré aujourd'hui
    last_patient_today = Patient.query.filter(db.func.date(Patient.timestamp) == date.today()).order_by(Patient.id.desc()).first()
    if last_patient_today:
        return last_patient_today.call_number + 1
    return 1  # Réinitialiser le compteur si aucun patient n'a été enregistré aujourd'hui


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


@app.route('/trigger_update')
def trigger_update():
    #socketio.emit('refresh', {'message': 'Update available'}, broadcast=True)
    return 'Update Triggered'


@app.route('/patients_queue')
def patients_queue():
    patients = Patient.query.filter_by(status='standing').order_by(Patient.call_number).all()
    return render_template('htmx/patients_queue.html', patients=patients)


@app.route('/patients_queue_for_counter')
def patients_queue_for_counter():
    patients = Patient.query.filter_by(status='standing').order_by(Patient.call_number).all()
    return render_template('htmx/patients_queue_for_counter.html', patients=patients)


@app.route('/patients_calling')
def patients_calling():
    patients = Patient.query.filter_by(status='calling').order_by(Patient.call_number).all()
    return render_template('htmx/patients_calling.html', patients=patients)


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


# Définir un filtre pour Jinja2
@app.template_filter('format_time')
def format_time(value):
    return value.strftime('%H:%M') if value else ''

if __name__ == "__main__":
    # Utilisez la variable d'environnement PORT si disponible, sinon défaut à 5000
    port = int(os.environ.get("PORT", 5000))
    # Activez le mode debug basé sur une variable d'environnement (définissez-la à True en développement)
    debug = os.environ.get("DEBUG", "False") == "True"

    socketio.run(app, host='0.0.0.0', port=port, debug=debug)