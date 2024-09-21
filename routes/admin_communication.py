from flask import Blueprint, render_template, request, jsonify, url_for, redirect, send_from_directory, current_app as app
from flask_socketio import rooms as socketio_rooms
from models import DashboardCard

admin_communication_bp = Blueprint('admin_communication', __name__)

def list_connections():
    return {namespace: list(clients) for namespace, clients in app.active_connections.items()}

@admin_communication_bp.route('/admin/communication/dashboard')
def dashboard_communication():
    print("dashboard KOMM")

    connected = list_connections()
    print("connected", connected)

    dashboardcard = DashboardCard.query.filter_by(name="staff").first()
    return render_template('/admin/dashboard_connexion.html', 
                            connected=connected,
                            dashboardcard=dashboardcard)
