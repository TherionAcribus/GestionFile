from flask import Blueprint, redirect
from routes.admin_security import require_permission

admin_options_bp = Blueprint('admin_options', __name__)


# Le choix de la palette de l'administration a rejoint la page « Application »
# (onglet Général). L'ancienne adresse reste valable pour les favoris.
@admin_options_bp.route('/admin/admin_options')
@require_permission('app')
def admin_admin():
    return redirect('/admin/app')
