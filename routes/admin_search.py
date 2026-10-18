"""Recherche globale de l'administration : index JSON de la palette « Ctrl+K ».

Le contenu (pages, onglets, sections, réglages) vient du noyau pur
``admin_search.py`` ; cette route y ajoute les éléments vivants de la base
(activités, comptoirs, équipe, comptes) puis ne renvoie que ce que
l'utilisateur courant a le droit d'ouvrir.

Garde-fous :

- lecture seule (GET, aucune écriture) ;
- authentification imposée par la garde ``/admin`` d'app.py, revérifiée ici ;
- filtrage par permission côté serveur — le navigateur ne reçoit jamais un
  lien ni un nom qu'il ne pourrait pas consulter ;
- aucune valeur de réglage n'est renvoyée, seulement libellés et emplacements ;
- ``Cache-Control: no-store`` : la réponse dépend du compte connecté.
"""

import os

from flask import Blueprint, current_app as app, jsonify
from flask_security import current_user

import admin_search
from models import Activity, Counter, Pharmacist, User
from routes.admin_security import user_has_permission

admin_search_bp = Blueprint('admin_search', __name__)


def _can(resource):
    return user_has_permission(current_user, resource)


def _admin_templates_dir():
    return os.path.join(app.root_path, 'templates', 'admin')


def _entity_entries():
    """Éléments vivants visibles par l'utilisateur courant (best-effort)."""
    entries = []
    try:
        if _can('activity'):
            for activity in Activity.query.order_by(Activity.name).all():
                if activity.is_staff:
                    entries.append(admin_search.entity_entry(
                        activity.name, '/admin/activity?tab=staff',
                        f'edit_s{activity.id}', 'Activités › Demandes nominatives',
                        'demande nominative', 'bi-person-lines-fill'))
                else:
                    entries.append(admin_search.entity_entry(
                        activity.name, '/admin/activity?tab=activity',
                        f'edit_a{activity.id}', 'Activités',
                        f'activité lettre {activity.letter or ""}', 'bi-card-list'))
        if _can('counter'):
            for counter in Counter.query.order_by(Counter.sort_order, Counter.name).all():
                entries.append(admin_search.entity_entry(
                    counter.name, '/admin/counter', f'edit_counter_{counter.id}',
                    'Comptoirs', 'comptoir poste', 'bi-window-stack'))
        if _can('staff'):
            for member in Pharmacist.query.order_by(Pharmacist.name).all():
                entries.append(admin_search.entity_entry(
                    member.name, '/admin/staff', f'edit_staff_{member.id}',
                    'Équipe', f'membre initiales {member.initials or ""}',
                    'bi-person-badge'))
        if _can('security_view'):
            for user in User.query.order_by(User.username).all():
                entries.append(admin_search.entity_entry(
                    user.username, '/admin/security?tab=users', f'user-{user.id}',
                    'Sécurité › Utilisateurs', 'compte utilisateur', 'bi-person-circle'))
    except Exception:
        # La recherche reste utilisable sans les éléments vivants.
        app.logger.exception("Recherche admin : éléments vivants indisponibles")
    return entries


@admin_search_bp.route('/admin/search/index')
def search_index():
    if not current_user.is_authenticated:
        return jsonify({"error": "Unauthorized"}), 401

    static_entries = admin_search.static_index(_admin_templates_dir())
    entries = admin_search.visible_entries(static_entries, _can) + _entity_entries()

    response = jsonify({"entries": entries, "kinds": list(admin_search.KIND_ORDER)})
    response.headers['Cache-Control'] = 'no-store'
    return response
