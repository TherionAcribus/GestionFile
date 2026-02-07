import pymysql
from flask import current_app as app
from models import Patient, PatientHistory, db
import os
import logging

def init_database(database, db):
    if database == "mysql":
        # Créer les bases de données si elles n'existent pas
        create_database_if_not_exists(app.config["SQLALCHEMY_DATABASE_URI"], 'queueschedulerdatabase')
        create_database_if_not_exists(app.config["SQLALCHEMY_DATABASE_URI"], 'queuedatabase')
        #create_database_if_not_exists(app.config["SQLALCHEMY_DATABASE_URI"], 'userdatabase')
        
        db.create_all()

    elif database == "sqlite":
        db.create_all()    

def create_database_if_not_exists(engine_url, database_name):
    """ Création des BDD MYSQL si elles n'existent pas"""
    app.config["MYSQL_USER"] = os.getenv('MYSQL_USER')
    app.config["MYSQL_PASSWORD"] = os.getenv('MYSQL_PASSWORD')
    app.config["HOST"] = os.getenv('MYSQL_HOST')

    try:
        connection = pymysql.connect(
            host=app.config["HOST"],
            user=app.config["MYSQL_USER"],
            password=app.config["MYSQL_PASSWORD"],
        )
        cursor = connection.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {database_name}")
        connection.close()
    except pymysql.err.OperationalError as e:
        logging.error(
            "Can't connect to MySQL (%s) to init database '%s': %s",
            app.config.get("HOST"),
            database_name,
            e,
        )
        raise


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
