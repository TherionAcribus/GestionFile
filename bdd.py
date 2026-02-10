from flask import current_app as app
from flask_migrate import upgrade as _alembic_upgrade
from models import Patient, PatientHistory, db
from sqlalchemy import inspect

def init_database(database, db):
    if database == "sqlite":
        db.create_all()
    elif database == "mysql":
        # Migrations are handled by `python manage.py migrate` during container startup.
        # Running Alembic migrations again inside the app startup is error-prone (and can
        # deadlock / race in multi-worker deployments).
        tables = set(inspect(db.engine).get_table_names())
        if "patient" not in tables:
            raise RuntimeError(
                "Database schema is not initialized (missing table 'patient'). "
                "Run `python manage.py migrate` before starting the app."
            )
        app.logger.info("Database schema detected (startup migrations skipped).")

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
