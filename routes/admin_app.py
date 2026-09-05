import re
import time

from flask import Blueprint, render_template, request, current_app as app
from flask_security import current_user
from routes.admin_security import send_test_email, require_permission, require_permission_dashboard, require_permission_api
from models import DashboardCard
from ui_feedback import display_toast
from extensions import socketio
from sockets import active_connections
from sockets import connected_clients_info
from audit_log import ACTION_CONNECT, OUTCOME_SUCCESS, OUTCOME_FAILURE
from audit_service import record_audit

admin_app_bp = Blueprint('admin_app', __name__)

# Point 4 (audit Admin) — Throttling des e-mails de test.
# Limite simple par utilisateur : 1 envoi/min, pour éviter le spam ou l'usage
# comme relais SMTP. État en mémoire par process (comme login_guard).
_email_test_last_sent: dict = {}  # {username: timestamp}
_EMAIL_TEST_COOLDOWN = 60.0  # secondes

# Regex simple d'email — suffisante pour filtrer les erreurs évidentes sans
# rejeter les adresses valides exotiques. On refuse les espaces et les
# caractères de contrôle.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

@admin_app_bp.route('/admin/app')
@admin_app_bp.route('/admin/app/<tab>')
@require_permission('app')
def admin_app(tab=None):
    valid_tabs = ['general', 'backups', 'mail', 'connexion']
    tab = request.args.get('tab', 'general')
    if tab not in valid_tabs:
        tab = 'general'
        
    return render_template('/admin/app.html',
                            active_tab=tab,
                            start_rabbitmq= app.config["START_RABBITMQ"],
                            network_adress = app.config["NETWORK_ADRESS"],
                            numbering_by_activity = app.config["NUMBERING_BY_ACTIVITY"], 
                            announce_sound = app.config["ANNOUNCE_SOUND"],
                            pharmacy_name = app.config["PHARMACY_NAME"],
                            mail_server = app.config["MAIL_SERVER"],
                            mail_port=app.config["MAIL_PORT"],
                            mail_username=app.config["MAIL_USERNAME"],
                            # Secret : on ne transmet JAMAIS la valeur au gabarit,
                            # seulement l'information « défini / non défini ».
                            mail_password_set=bool(app.config.get("MAIL_PASSWORD")),
                            mail_default_sender=app.config["MAIL_DEFAULT_SENDER"],
                            mail_use_tls=app.config["MAIL_USE_TLS"],
                            mail_use_ssl=app.config["MAIL_USE_SSL"],
                            namespaces = list(active_connections.keys())
    )

@admin_app_bp.route('/admin/app/mail/test', methods=['POST'])
@require_permission_api('app')
def admin_app_mail_test():
    # Envoi d'un e-mail = action : POST uniquement, permission 'app' requise.
    mail_adress = request.values.get('mail_adress')

    # Point 4 (audit Admin) — Validation de l'adresse e-mail.
    if not mail_adress or not mail_adress.strip():
        return display_toast(success=False, message="Veuillez entrer une adresse e-mail.")
    mail_adress = mail_adress.strip()
    if not _EMAIL_RE.match(mail_adress):
        return display_toast(success=False, message="L'adresse e-mail n'est pas valide.")

    # Point 4 — Throttling : 1 test/min par utilisateur.
    username = getattr(current_user, "username", "unknown")
    now = time.monotonic()
    last = _email_test_last_sent.get(username, 0.0)
    remaining = _EMAIL_TEST_COOLDOWN - (now - last)
    if remaining > 0:
        app.logger.warning(
            "Test email throttled pour %s (%.0fs restantes)", username, remaining
        )
        return display_toast(
            success=False,
            message="Veuillez patienter avant d'envoyer un nouveau test.",
        )
    _email_test_last_sent[username] = now

    # Point 4 — Envoi + audit. send_test_email retourne désormais (bool, msg).
    success, error_msg = send_test_email(mail_adress)
    if success:
        record_audit(
            ACTION_CONNECT,
            "email",
            target_id=mail_adress,
            outcome=OUTCOME_SUCCESS,
        )
        display_toast(success=True, message="E-mail envoyé.")
    else:
        record_audit(
            ACTION_CONNECT,
            "email",
            target_id=mail_adress,
            outcome=OUTCOME_FAILURE,
        )
        display_toast(success=False, message=error_msg or "E-mail non envoyé.")

    return "", 200


@admin_app_bp.route('/admin/communication/dashboard')
@require_permission_dashboard('app')
def dashboard_communication():
    dashboardcard = DashboardCard.query.filter_by(name="connection").first()
    return render_template('/admin/dashboard_connection.html',
                            dashboardcard=dashboardcard,
                            namespaces = list(active_connections.keys()))


@admin_app_bp.route('/admin/app/get_connections', methods=['POST'])
@require_permission_api('app')
def get_connections():
    # on récupere les namespaces selectionnes, si aucun : on les affiche tous
    selected_namespaces = request.form.getlist('namespaces[]')
    if len(selected_namespaces) == 0:
        selected_namespaces = list(active_connections.keys())

    connections = {}
    for namespace in selected_namespaces:
        # Obtenez la liste des clients connectés pour chaque namespace sélectionné
        connected_clients = get_connected_clients(namespace)
        connections[namespace] = connected_clients

    # Renvoyer le template avec les connexions mises à jour
    return render_template('admin/app_connexion_list.html', connections=connections)


def get_connected_clients(namespace):
    sids = socketio.server.manager.rooms.get(namespace, {}).get(None, set())
    connected = []
    for sid in sids:
        username = connected_clients_info.get(sid, {}).get('username', 'Unknown')
        connected.append({'sid': sid, 'username': username})
    return connected