"""Recherche globale de l'administration : index JSON de la palette « Ctrl+K ».

Le contenu (pages, onglets, sections, réglages) vient du noyau pur
``admin_search.py`` ; cette route y ajoute les éléments vivants de la base
(activités, comptoirs, équipe, règles de priorité, langues, rôles, comptes)
puis ne renvoie que ce que l'utilisateur courant a le droit d'ouvrir.

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
from models import Activity, AlgoRule, Counter, Language, Pharmacist, Role, User
from routes.admin_security import user_has_permission

admin_search_bp = Blueprint('admin_search', __name__)


def _can(resource):
    return user_has_permission(current_user, resource)


def _admin_templates_dir():
    return os.path.join(app.root_path, 'templates', 'admin')


def _activities():
    for activity in Activity.query.order_by(Activity.name).all():
        if activity.is_staff:
            yield admin_search.entity_entry(
                activity.name, '/admin/activity?tab=staff', f'edit_s{activity.id}',
                'Activités › Demandes nominatives', 'demande nominative',
                'bi-person-lines-fill')
        else:
            yield admin_search.entity_entry(
                activity.name, '/admin/activity?tab=activity', f'edit_a{activity.id}',
                'Activités', f'activité lettre {activity.letter or ""}', 'bi-card-list')


def _counters():
    for counter in Counter.query.order_by(Counter.sort_order, Counter.name).all():
        yield admin_search.entity_entry(
            counter.name, '/admin/counter', f'edit_counter_{counter.id}',
            'Comptoirs', 'comptoir poste', 'bi-window-stack')


def _staff():
    for member in Pharmacist.query.order_by(Pharmacist.name).all():
        yield admin_search.entity_entry(
            member.name, '/admin/staff', f'edit_staff_{member.id}',
            'Équipe', f'membre initiales {member.initials or ""}', 'bi-person-badge')


def _algo_rules():
    for rule in AlgoRule.query.order_by(AlgoRule.priority_level, AlgoRule.name).all():
        yield admin_search.entity_entry(
            rule.name, '/admin/algo', f'rule_edit_{rule.id}',
            'Priorités › Règles', f'règle priorité {rule.description or ""}', 'bi-list-ol')


def _languages():
    for language in Language.query.order_by(Language.sort_order, Language.name).all():
        yield admin_search.entity_entry(
            language.name, '/admin/translations', f'name-{language.id}',
            'Traductions › Langues',
            f'langue {language.code or ""} {language.translation or ""}', 'bi-flag')


def _roles():
    for role in Role.query.order_by(Role.name).all():
        yield admin_search.entity_entry(
            role.name, '/admin/security?tab=roles', f'role-item-{role.id}',
            'Sécurité › Rôles', f'rôle permissions {role.description or ""}',
            'bi-person-gear')


def _users():
    for user in User.query.order_by(User.username).all():
        yield admin_search.entity_entry(
            user.username, '/admin/security?tab=users', f'user-{user.id}',
            'Sécurité › Utilisateurs', 'compte utilisateur', 'bi-person-circle')


#: (permission, source) — chaque source n'est interrogée que si l'utilisateur
#: a la permission de la page où l'élément s'affiche.
_ENTITY_SOURCES = (
    ('activity', _activities),
    ('counter', _counters),
    ('staff', _staff),
    ('algo', _algo_rules),
    ('translation', _languages),
    ('security_view', _roles),
    ('security_view', _users),
)


def _entity_entries():
    """Éléments vivants visibles par l'utilisateur courant (best-effort).

    Une source en erreur est ignorée seule : la recherche garde les pages,
    réglages et autres éléments.
    """
    entries = []
    for permission, source in _ENTITY_SOURCES:
        if not _can(permission):
            continue
        try:
            entries.extend(source())
        except Exception:
            app.logger.exception("Recherche admin : source %s indisponible", source.__name__)
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
