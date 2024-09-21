from flask import Blueprint, render_template, request, current_app as app
from routes.admin_security import send_test_email
from models import DashboardCard

admin_app_bp = Blueprint('admin_app', __name__)

@admin_app_bp.route('/admin/app')
def admin_app():
    return render_template('/admin/app.html',
                            network_adress = app.config["NETWORK_ADRESS"],
                            numbering_by_activity = app.config["NUMBERING_BY_ACTIVITY"], 
                            announce_sound = app.config["ANNOUNCE_SOUND"],
                            pharmacy_name = app.config["PHARMACY_NAME"],
                            mail_server = app.config["MAIL_SERVER"],
                            mail_port=app.config["MAIL_PORT"],
                            mail_username=app.config["MAIL_USERNAME"],
                            mail_password=app.config["MAIL_PASSWORD"],
                            mail_default_sender=app.config["MAIL_DEFAULT_SENDER"],
                            mail_use_tls=app.config["MAIL_USE_TLS"],
                            mail_use_ssl=app.config["MAIL_USE_SSL"],
                            namespaces = list(app.active_connections.keys())
    )

@admin_app_bp.route('/admin/app/mail/test')
def admin_app_mail_test():
    mail_adress = request.values.get('mail_adress')

    print("mail adress", mail_adress)

    if mail_adress == "":
        app.display_toast(success=False, message="Veuillez entrer une adresse email")
        
    if send_test_email(mail_adress):
        app.display_toast(success=True, message="Email envoyé")
    else:
        app.display_toast(success=False, message="Email non envoyé")    

    return "", 200


@admin_app_bp.route('/admin/communication/dashboard')
def dashboard_communication():
    dashboardcard = DashboardCard.query.filter_by(name="connection").first()
    return render_template('/admin/dashboard_connection.html',
                            dashboardcard=dashboardcard,
                            namespaces = list(app.active_connections.keys()))


@admin_app_bp.route('/admin/app/get_connections', methods=['POST'])
def get_connections():
    # on récupere les namespaces selectionnes, si aucun : on les affiche tous
    selected_namespaces = request.form.getlist('namespaces[]')
    if len(selected_namespaces) == 0:
        selected_namespaces = list(app.active_connections.keys())

    connections = {}
    for namespace in selected_namespaces:
        # Obtenez la liste des clients connectés pour chaque namespace sélectionné
        connected_clients = get_connected_clients(namespace)
        connections[namespace] = connected_clients

    # Renvoyer le template avec les connexions mises à jour
    return render_template('admin/app_connexion_list.html', connections=connections)


def get_connected_clients(namespace):
    sids = app.socketio.server.manager.rooms.get(namespace, {}).get(None, set())
    connected = []
    for sid in sids:
        username = app.connected_clients_info.get(sid, {}).get('username', 'Unknown')
        connected.append({'sid': sid, 'username': username})
    return connected