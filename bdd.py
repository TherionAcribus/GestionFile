import pymysql

def init_database(database, db):
    if database == "mysql":
        # Créer les bases de données si elles n'existent pas
        create_database_if_not_exists('mysql+pymysql://admin:1Licornecornue@localhost/', 'queueschedulerdatabase')
        create_database_if_not_exists('mysql+pymysql://admin:1Licornecornue@localhost/', 'queuedatabase')
        create_database_if_not_exists('mysql+pymysql://admin:1Licornecornue@localhost/', 'userdatabase')
        
        db.create_all()

    elif database == "sqlite":
        db.create_all()    

def create_database_if_not_exists(engine_url, database_name):
    """ Création des BDD MYSQL si elles n'existent pas"""
    print("create_database_if_not_exists", database_name)
    connection = pymysql.connect(host='localhost', user='admin', password='1Licornecornue')
    cursor = connection.cursor()
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS {database_name}")
    connection.close()