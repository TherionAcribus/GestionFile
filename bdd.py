from flask import current_app as app
from models import Patient, PatientHistory, db

def init_database(database, db):
    if database == "sqlite":
        db.create_all()
    elif database == "mysql":
        app.logger.info("Skipping database creation for MySQL, using migrations instead.")

def transfer_patients_to_history():
    try:
        patients = Patient.query.all()
        if not patients:
            app.logger.info("No patients to transfer")
            app.display_toast(success=False, message="Aucun patient à copier dans l'historique")
            return True

        for patient in patients:
            # Obtenir le jour de la semaine
            day_of_week = patient.timestamp.strftime('%A')  # Renvoie 'Monday', 'Tuesday', etc.

            # Créer un nouvel enregistrement PatientHistory
            patient_history = PatientHistory(
                call_number=patient.call_number,
                timestamp=patient.timestamp,
                timestamp_counter=patient.timestamp_counter,
                timestamp_end=patient.timestamp_end,
                day_of_week=day_of_week,
                status=patient.status,
                counter_id=patient.counter_id,
                activity_id=patient.activity_id,
                overtaken=patient.overtaken,
                language_id=patient.language_id,
                # Ajoutez d'autres champs si nécessaire
            )

            # Ajouter à la session
            db.session.add(patient_history)

        # Valider la session pour écrire dans PatientHistory
        db.session.commit()

        app.logger.info(f"{len(patients)} Patients transférés dans l'historique avec succès")
        app.display_toast(success=True, message=f"{len(patients)} patients transférés dans l'historique avec succès")

        return True
    except Exception as e:
        # En cas d'erreur, annuler toutes les transactions
        db.session.rollback()
        app.logger.info(f"Erreur lors de la copie dans patients dans l'historique : {e}")
        app.display_toast(success=False, message=f"Erreur lors de la copie dans patients dans l'historique : {e}")
        return False
