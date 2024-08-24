from flask import current_app as app
from datetime import datetime, timezone, date
from models import Patient, Activity, Counter, db

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
