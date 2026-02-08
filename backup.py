import zipfile
import mysql.connector
import os
from datetime import datetime
from flask import redirect, url_for, current_app, send_file
from io import BytesIO


# Les anciennes fonctions de backup individuelles (backup_staff, backup_counters, etc.)
# ont été migrées vers backup_service.py
# Seules les fonctions de backup de base de données brute restent ici.


def backup_databases(database):
    if database == "sqlite":
        return backup_sqlite()
    elif database == "mysql":
        return backup_mysql()


def backup_sqlite():
    """ Sauvegarde des bases de données SQLite via Python pour être indépendant de la machine sur lequel tourne l'App. 
    Le plus performant reste quand même de faire un dump """

    # Préparer un fichier ZIP en mémoire
    zip_buffer = BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w') as zip_file:
        # Liste des bases de données
        databases = {
            'queuedatabase.db': current_app.config['SQLALCHEMY_DATABASE_URI'].replace('sqlite:///', ''),
            'queueschedulerdatabase.db': current_app.config.get('SQLALCHEMY_DATABASE_URI_SCHEDULER', '').replace('sqlite:///', ''),
            'userdatabase.db': current_app.config['SQLALCHEMY_BINDS']['users'].replace('sqlite:///', '')
        }

        for db_name, db_path in databases.items():
            if db_path:  # Vérifier que le chemin n'est pas vide
                # Éviter la duplication de "instance" dans le chemin
                if not db_path.startswith('instance/'):
                    db_path = os.path.join(current_app.instance_path, db_path)
                
                if os.path.exists(db_path):
                    # Ajouter chaque base de données au fichier ZIP
                    zip_file.write(db_path, arcname=db_name)
                else:
                    return f"Database file {db_name} not found at {db_path}", 404

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

    zip_buffer.seek(0)
    return send_file(zip_buffer, as_attachment=True, download_name=f'backup_databases_{timestamp}.zip')

def backup_mysql():
    dbs_to_backup = ['queuedatabase', 'queueschedulerdatabase', 'userdatabase']

    connection = mysql.connector.connect(
        host='localhost',
        user=current_app.config["MYSQL_USER"],
        password=current_app.config["MYSQL_PASSWORD"],
    )
    cursor = connection.cursor()

    zip_buffer = BytesIO()

    with zipfile.ZipFile(zip_buffer, 'w') as zip_file:
        for db_name in dbs_to_backup:
            buffer = backup_database(db_name, cursor)
            buffer.seek(0)
            zip_file.writestr(f"{db_name}.sql", buffer.read())

    zip_buffer.seek(0)
    cursor.close()
    connection.close()

    return send_file(zip_buffer, as_attachment=True, download_name='backup_databases.zip')


def backup_database(db_name, cursor):
    buffer = BytesIO()
    cursor.execute(f"SHOW TABLES FROM {db_name}")
    tables = cursor.fetchall()

    for table in tables:
        table_name = table[0]
        cursor.execute(f"SHOW CREATE TABLE {db_name}.{table_name}")
        create_table_sql = cursor.fetchone()[1] + ";\n\n"
        buffer.write(create_table_sql.encode('utf-8'))

        cursor.execute(f"SELECT * FROM {db_name}.{table_name}")
        rows = cursor.fetchall()
        if rows:
            columns = [desc[0] for desc in cursor.description]
            columns_str = ', '.join(columns)
            buffer.write(f"INSERT INTO {table_name} ({columns_str}) VALUES\n".encode('utf-8'))

            for row in rows:
                values_str = ', '.join(["'{}'".format(str(value).replace("'", "\\'")) if value is not None else 'NULL' for value in row])
                buffer.write(f"({values_str}),\n".encode('utf-8'))
            buffer.seek(buffer.tell() - 2)  # Remove the last comma
            buffer.write(";\n\n".encode('utf-8'))

    return buffer