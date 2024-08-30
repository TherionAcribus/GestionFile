import pymysql
from flask import current_app as app

def init_database(database, db):
    if database == "mysql":
        # Créer les bases de données si elles n'existent pas
        create_database_if_not_exists(app.config["SQLALCHEMY_DATABASE_URI"], 'queueschedulerdatabase')
        create_database_if_not_exists(app.config["SQLALCHEMY_DATABASE_URI"], 'queuedatabase')
        create_database_if_not_exists(app.config["SQLALCHEMY_DATABASE_URI"], 'userdatabase')
        
        db.create_all()

    elif database == "sqlite":
        db.create_all()    

def create_database_if_not_exists(engine_url, database_name):
    """ Création des BDD MYSQL si elles n'existent pas"""
    print("create_database_if_not_exists", database_name)
    connection = pymysql.connect(host=app.config["HOST"], user=app.config["MYSQL_USER"], password=app.config["MYSQL_PASSWORD"])
    cursor = connection.cursor()
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {database_name}")
    connection.close()