import os
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, current_app as app
from flask_login import current_user
from functools import wraps
from sqlalchemy.orm import selectinload
from models import db, Role, User, DashboardCard
from permissions_registry import PERMISSIONS, permissions_by_category
from flask_mailman import EmailMessage
from flask_security import login_required, login_user
from wtforms import StringField, PasswordField, HiddenField, BooleanField
from flask_wtf import FlaskForm
from wtforms.validators import DataRequired
from urllib.parse import urlparse, urljoin
from datetime import datetime
import json

from pagination import parse_page_params, paginate_query
from login_guard import (
    get_shared_throttle,
    ip_key,
    identity_key,
    worst_retry_after,
)
from login_audit import (
    build_login_audit,
    format_login_audit,
    OUTCOME_SUCCESS,
    OUTCOME_FAILURE,
    OUTCOME_BLOCKED,
)
from password_policy import validate_password
from audit_service import record_audit
from audit_log import (
    ACTION_CREATE,
    ACTION_UPDATE,
    ACTION_DELETE,
    ACTION_RESET,
    ACTION_LOGOUT_ALL,
    OUTCOME_SUCCESS,
    OUTCOME_FAILURE,
)

admin_security_bp = Blueprint('admin_security', __name__)

# Message unique présenté à l'utilisateur quel que soit le motif d'échec
# (identifiant inconnu OU mot de passe erroné) : ne pas révéler lequel des deux
# est faux évite l'énumération des comptes.
GENERIC_LOGIN_ERROR = "Identifiants incorrects"


def _client_ip():
    """Adresse IP source de la requête de connexion.

    Utilise ``remote_addr``. On ne fait PAS confiance à ``X-Forwarded-For`` par
    défaut : derrière un proxy non maîtrisé, un client pourrait forger cet
    en-tête pour se soustraire à la limitation ou empoisonner l'audit. Si un
    reverse proxy de confiance est en place, il doit réécrire ``remote_addr``
    (ProxyFix) plutôt que de compter sur l'en-tête ici.
    """
    return request.remote_addr


def _audit_login(outcome, *, username=None, retry_after=None):
    """Émet une ligne d'audit de connexion (sans aucun secret)."""
    record = build_login_audit(
        outcome,
        username=username,
        ip=_client_ip(),
        user_agent=request.headers.get('User-Agent'),
        retry_after=retry_after,
    )
    # WARNING pour un refus/blocage (repérable dans les alertes), INFO pour un
    # succès. Le mot de passe n'est jamais passé à build_login_audit.
    line = format_login_audit(record)
    if outcome == OUTCOME_SUCCESS:
        app.logger.info(line)
    else:
        app.logger.warning(line)


def user_has_permission(user, resource):
    """Indique si ``user`` possède la permission ``admin_<resource>``.

    Source de vérité unique pour toutes les vérifications de permission (pages
    et API). Un utilisateur non authentifié ou sans rôle n'a aucune permission.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "roles", None):
        return False

    permission_field = f'admin_{resource}'
    for role in user.roles:
        if getattr(role, permission_field, False):
            return True
    return False


# ---------------------------------------------------------------------------
# Contrôle d'accès centralisé (point 1.3)
#
# Toute la logique de décision d'autorisation vit ici, dans une source unique
# (``_permission_status``), afin de ne pas la dupliquer dans chaque fonction ni
# dans chaque décorateur. Les décorateurs ci-dessous ne font que choisir la
# *forme* du refus (page HTML, JSON API, carte de tableau de bord).
# ---------------------------------------------------------------------------


def _permission_status(resource):
    """État d'autorisation de l'utilisateur courant pour ``resource``.

    Retourne ``None`` si l'accès est autorisé, sinon le code HTTP du refus :
    - **401** : utilisateur non authentifié ;
    - **403** : utilisateur authentifié mais sans la permission (rôle absent ou
      permission ``admin_<resource>`` à False).
    """
    if not current_user.is_authenticated:
        return 401
    if not user_has_permission(current_user, resource):
        return 403
    return None


def permission_error_response(resource, *, api):
    """Réponse de refus prête à renvoyer, ou ``None`` si l'accès est autorisé.

    Deux variantes, sélectionnées par ``api`` :
    - ``api=True`` (API/HTMX) : **401/403 JSON**, exploitable côté client ;
    - ``api=False`` (page HTML) : **redirection** vers la connexion pour un
      anonyme, **page d'erreur 403** pour un utilisateur connecté sans droit.

    Utilisable aussi bien par les décorateurs que directement en garde *inline*
    lorsque la ressource dépend de données de la requête (ex. variables CSS dont
    la permission dépend de la page ciblée).
    """
    status = _permission_status(resource)
    if status is None:
        return None

    if api:
        app.logger.warning(
            "Permission API '%s' refusée (%s) à %s", resource, status,
            getattr(current_user, "username", "anonyme"))
        return jsonify({"error": "Unauthorized" if status == 401 else "Forbidden"}), status

    if status == 401:
        app.logger.warning(
            "Accès non authentifié refusé (page '%s')", resource)
        return redirect(url_for('security.login'))

    app.logger.warning(
        "Permission '%s' refusée à %s", resource,
        getattr(current_user, "username", "?"))
    error_message = f"Vous n'avez pas les permissions nécessaires pour accéder à la page '{resource}'."
    return render_template('admin/permission_error.html', error_message=error_message), 403


# gère les permissions sur les pages (variante PAGE HTML)
def require_permission(resource):
    """Protège une route servant une **page/fragment HTML**.

    Anonyme → redirection connexion ; connecté sans droit → page d'erreur 403.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            refusal = permission_error_response(resource, api=False)
            return refusal if refusal is not None else f(*args, **kwargs)
        return decorated_function
    return decorator


def require_permission_api(resource):
    """Protège un endpoint **API/HTMX** : refus en **401/403 JSON**.

    À réserver aux endpoints appelés programmatiquement (fetch/AJAX) et à ceux
    qui renvoient du JSON, pour lesquels une redirection HTML n'aurait pas de
    sens côté client.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            refusal = permission_error_response(resource, api=True)
            return refusal if refusal is not None else f(*args, **kwargs)
        return decorated_function
    return decorator


def require_permission_dashboard(resource):
    """Variante PAGE pour les cartes du tableau de bord.

    Même décision d'autorisation que les autres variantes ; en cas de refus
    « connecté sans droit », rend la carte avec un message plutôt qu'une page
    d'erreur pleine, afin de rester intégrable dans le tableau de bord.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            status = _permission_status(resource)
            if status == 401:
                app.logger.warning(
                    "Accès non authentifié refusé (dashboard '%s')", resource)
                return redirect(url_for('security.login'))
            if status == 403:
                app.logger.warning(
                    "Permission '%s' refusée à %s (dashboard)", resource,
                    getattr(current_user, "username", "?"))
                dashboardcard = DashboardCard.query.filter_by(name=resource).first()
                return render_template('/admin/dashboard_base.html',
                                    dashboardcard=dashboardcard,
                                    error_message="Vous n'avez pas les permissions nécessaires pour accéder à cette partie.",
                                    title=resource), 403

            return f(*args, **kwargs)
        return decorated_function
    return decorator


# Colonnes de tri autorisées (liste blanche) — cf. pagination.parse_page_params.
USER_SORT_COLUMNS = {
    'username': User.username,
    'email': User.email,
}
ROLE_SORT_COLUMNS = {
    'name': Role.name,
    'description': Role.description,
}


@admin_security_bp.route('/admin/security/role/table')
@require_permission('security')
def display_security_role_table():
    # request.values : fonctionne que la table soit demandée en GET (nav/recherche)
    # ou re-rendue après une mutation POST (delete_role) — dans ce dernier cas les
    # paramètres sont absents et l'on retombe sur la première page par défaut.
    params = parse_page_params(
        request.values,
        allowed_sort=tuple(ROLE_SORT_COLUMNS),
        default_sort='name',
    )
    pager = paginate_query(
        Role.query,
        params,
        sort_columns=ROLE_SORT_COLUMNS,
        search_columns=[Role.name, Role.description],
    )
    return render_template('admin/security_htmx_role_table.html', roles=pager.items,
                           pager=pager, params=params,
                           permissions_by_category=permissions_by_category())

@admin_security_bp.route('/admin/security/dashboard')
@require_permission_dashboard('security')
def dashboard_security():
    dashboardcard = DashboardCard.query.filter_by(name="security").first()
    is_default = check_default_admin()
    return render_template('/admin/dashboard_security.html',
                            dashboardcard=dashboardcard,
                            is_default_admin=is_default)

@admin_security_bp.route('/admin/security')
@require_permission('security')
def admin_security():

    valid_tabs = ['general', 'users']
    tab = request.args.get('tab', 'general')
    if tab not in valid_tabs:
        tab = 'general'

    return render_template('admin/security.html',
                        security_login_admin=app.config["SECURITY_LOGIN_ADMIN"],
                        security_login_counter=app.config["SECURITY_LOGIN_COUNTER"],
                        security_login_screen=app.config["SECURITY_LOGIN_SCREEN"],
                        security_login_patient=app.config["SECURITY_LOGIN_PATIENT"],
                        security_remember_duration=app.config["SECURITY_REMEMBER_DURATION"],
                        is_default_admin = check_default_admin())

@admin_security_bp.route('/admin/security/table')
@require_permission('security')
def display_security_table():
    params = parse_page_params(
        request.values,
        allowed_sort=tuple(USER_SORT_COLUMNS),
        default_sort='username',
    )
    # Le gabarit lit user.roles pour chaque ligne (case cochée par rôle) :
    # selectinload charge tous les rôles en une requête IN groupée, au lieu d'une
    # requête par utilisateur (N+1).
    pager = paginate_query(
        User.query.options(selectinload(User.roles)),
        params,
        sort_columns=USER_SORT_COLUMNS,
        search_columns=[User.username, User.email],
    )
    roles = Role.query.all()
    return render_template('admin/security_htmx_table.html', users=pager.items,
                           pager=pager, params=params, roles=roles)

# affiche le formulaire pour ajouter un utilisateur
@admin_security_bp.route('/admin/security/add_user_form')
@require_permission('security')
def add_user_form():
    roles = Role.query.all()
    return render_template('/admin/security_add_user_form.html', roles=roles)


@admin_security_bp.route('/admin/security/add_new_user', methods=['POST'])
@require_permission('security')
def add_new_user():
    """Ajoute un nouvel utilisateur"""
    try:
        app.logger.info("=== Ajout d'un nouvel utilisateur ===")
        
        # Récupération des données du formulaire
        data = request.get_json() if request.is_json else request.form
        username = data.get('username')
        email = data.get('email')
        password1 = data.get('password1')
        password2 = data.get('password2')
        role_id = data.get('role_id')
        
        app.logger.info(f"Données reçues - username: {username}, email: {email}, role_id: {role_id}")

        # Vérification des mots de passe
        if password1 != password2:
            app.logger.error("Les mots de passe ne correspondent pas")
            app.display_toast(success=False, message="Les mots de passe ne correspondent pas")
            return display_security_table()

        # Politique minimale de mot de passe (point 3.4)
        policy_problems = validate_password(password1, username=username)
        if policy_problems:
            app.logger.warning("Mot de passe refusé par la politique à la création d'utilisateur")
            app.display_toast(success=False, message=" ".join(policy_problems))
            return display_security_table()

        # Vérification de l'unicité du nom d'utilisateur
        if User.query.filter_by(username=username).first():
            app.logger.error(f"Le nom d'utilisateur {username} existe déjà")
            app.display_toast(success=False, message="Ce nom d'utilisateur existe déjà")
            return display_security_table()

        # Récupération du rôle
        role = Role.query.get(role_id)
        if not role:
            app.logger.error(f"Le rôle {role_id} n'existe pas")
            app.display_toast(success=False, message="Le rôle sélectionné n'existe pas")
            return display_security_table()

        # Création de l'utilisateur
        user = User(
            username=username,
            email=email,
            active=True
        )
        user.set_password(password1)
        user.roles.append(role)

        db.session.add(user)
        db.session.commit()
        
        app.logger.info(f"Nouvel utilisateur créé avec succès: {username}")
        record_audit(ACTION_CREATE, "user", target_id=user.id, outcome=OUTCOME_SUCCESS,
                     details=f"username={username}")
        app.display_toast(success=True, message="Utilisateur créé avec succès")
        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_user_form"></div>"""
        return f"{display_security_table()}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Erreur lors de la création de l'utilisateur: {str(e)}")
        record_audit(ACTION_CREATE, "user", target_id=username, outcome=OUTCOME_FAILURE)
        app.display_toast(success=False, message=f"Erreur lors de la création : {str(e)}")
        return display_security_table()


@admin_security_bp.route('/admin/security/user_update/<int:user_id>', methods=['POST'])
@require_permission('security')
def security_update_user(user_id):
    try:
        user = User.query.get(user_id)
        if not user:
            app.display_toast(success=False, message="Utilisateur non trouvé")
            return display_security_table()

        # Récupérer les données du formulaire
        data = request.get_json() if request.is_json else request.form
        username = data.get('username')
        email = data.get('email')
        role_id = data.get('role_id')

        # Vérifier si le nom d'utilisateur existe déjà
        existing_user = User.query.filter_by(username=username).first()
        if existing_user and existing_user.id != user_id:
            app.display_toast(success=False, message="Ce nom d'utilisateur existe déjà")
            return display_security_table()

        # Vérifier si l'email existe déjà
        if email:
            existing_user = User.query.filter_by(email=email).first()
            if existing_user and existing_user.id != user_id:
                app.display_toast(success=False, message="Cet email est déjà utilisé")
                return display_security_table()

        # Vérifier le changement de rôle pour un admin
        if has_admin_role(user):
            new_role = Role.query.get(role_id)
            if not new_role or new_role.name != "admin":
                # Si c'est le dernier admin, on refuse le changement
                if count_admin_users() <= 1:
                    app.display_toast(success=False, message="Impossible de retirer le rôle admin du dernier administrateur")
                    return display_security_table()

        # Mettre à jour les informations de base
        user.username = username
        user.email = email

        # Mettre à jour le rôle
        role = Role.query.get(role_id)
        if not role:
            app.display_toast(success=False, message="Rôle invalide")
            return display_security_table()

        # Remplacer tous les rôles par le nouveau
        user.roles = [role]

        db.session.commit()
        record_audit(ACTION_UPDATE, "user", target_id=user_id, outcome=OUTCOME_SUCCESS,
                     details=f"username={username} role={role.name}")
        app.display_toast(success=True, message="Utilisateur mis à jour avec succès")
        return display_security_table()

    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_UPDATE, "user", target_id=user_id, outcome=OUTCOME_FAILURE)
        app.display_toast(success=False, message=f"Erreur lors de la mise à jour : {str(e)}")
        app.logger.error(f"Error in security_update_user: {str(e)}")
        return display_security_table()

def has_admin_role(user):
    """Vérifie si l'utilisateur a le rôle admin"""
    return any(role.name == "admin" for role in user.roles)

def count_admin_users():
    """Compte le nombre d'utilisateurs ayant le rôle admin"""
    admin_role = Role.query.filter_by(name="admin").first()
    if not admin_role:
        return 0
    return User.query.filter(User.roles.contains(admin_role)).count()

@admin_security_bp.route('/admin/security/delete_user/<int:user_id>', methods=['POST'])
@require_permission('security')
def delete_user2(user_id):
    try:
        user = User.query.get(user_id)
        if not user:
            app.display_toast(success=False, message="Utilisateur non trouvé")
            return display_security_table()

        # Vérifier si c'est un admin
        if has_admin_role(user):
            # Si c'est le dernier admin, on refuse la suppression
            if count_admin_users() <= 1:
                record_audit(ACTION_DELETE, "user", target_id=user_id, outcome=OUTCOME_FAILURE,
                             details="refus: dernier administrateur")
                app.display_toast(success=False, message="Impossible de supprimer le dernier administrateur")
                return display_security_table()

        deleted_username = user.username
        db.session.delete(user)
        db.session.commit()

        record_audit(ACTION_DELETE, "user", target_id=user_id, outcome=OUTCOME_SUCCESS,
                     details=f"username={deleted_username}")
        app.display_toast(success=True, message="Utilisateur supprimé avec succès")
        return display_security_table()

    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_DELETE, "user", target_id=user_id, outcome=OUTCOME_FAILURE)
        app.display_toast(success=False, message=f"Erreur lors de la suppression : {str(e)}")
        app.logger.error(f"Error in delete_user: {str(e)}")
        return display_security_table()

# affiche la modale pour confirmer la suppression d'un membre
@admin_security_bp.route('/admin/security/confirm_delete_user/<int:user_id>', methods=['GET'])
@require_permission('security')
def confirm_delete_user(user_id):
    user = User.query.get(user_id)
    return render_template('/admin/security_modal_confirm_delete_user.html', user=user)

# affiche la modale pour confirmer la suppression d'un role
@admin_security_bp.route('/admin/security/confirm_delete_role/<int:role_id>', methods=['GET'])
@require_permission('security')
def confirm_delete_role(role_id):
    role = Role.query.get(role_id)
    return render_template('/admin/security_modal_confirm_delete_role.html', role=role)


# Point 2.1 : l'ancienne route GET ``/admin/security/delete_user`` a été retirée.
# C'était un DOUBLON de ``delete_user2`` (POST, ci-dessus) qui, en plus d'être une
# mutation exposée en GET (contournable par CSRF), ne portait PAS la protection
# « dernier administrateur ». La suppression d'utilisateur passe désormais
# exclusivement par la route POST protégée ``delete_user2``.


# Fonction utilitaire (PAS une route) : l'envoi d'e-mail de test est déclenché
# par la route POST protégée ``/admin/app/mail/test`` (require_permission_api).
# L'ancienne route GET ``/send_test_email`` a été retirée : signature invalide
# comme vue (argument positionnel sans convertisseur d'URL) et déclenchement
# d'envoi d'e-mail sans authentification.
def send_test_email(mail_adress):
    app.logger.info("Envoi d'un email de test")
    print("mail_adress", mail_adress)
    msg = EmailMessage(
        subject="Test Email",
        body="This is a test email sent from Flask-Mailman.",
        to=[mail_adress],
    )
    print("message", msg)
    msg.send()
    return True


def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


@admin_security_bp.route('/login', methods=['GET', 'POST'])
def login():
    app.logger.info("=== Tentative de connexion ===")
    form = ExtendedLoginForm()
    throttle = get_shared_throttle()

    if form.validate_on_submit():
        username = form.username.data
        ip = _client_ip()

        # 1) Limitation : si l'IP OU l'identité est en délai/verrouillage, on
        #    refuse AVANT toute vérification de mot de passe. Message générique
        #    (ne pas révéler qu'un blocage est actif ni sur quelle clé).
        retry_after = worst_retry_after(throttle, ip, username)
        if retry_after > 0:
            _audit_login(OUTCOME_BLOCKED, username=username, retry_after=retry_after)
            app.display_toast(success=False, message=GENERIC_LOGIN_ERROR)
            return render_template('security/login.html', form=form)

        user = User.query.filter_by(username=username).first()

        # 2) Vérification. Un utilisateur inconnu et un mot de passe erroné
        #    produisent EXACTEMENT le même message et le même traitement
        #    (anti-énumération). Si l'utilisateur est inconnu, on effectue tout
        #    de même une vérification factice pour égaliser le temps de réponse
        #    et ne pas trahir l'existence du compte par le timing.
        password_ok = False
        if user is not None:
            password_ok = user.verify_password(form.password.data)
        else:
            _dummy_password_check(form.password.data)

        if not password_ok:
            # 3) Échec : on compte l'échec sur les deux clés (IP + identité) puis
            #    on journalise. Le délai renvoyé sert au calcul mais n'est pas
            #    divulgué à l'utilisateur.
            throttle.register_failure(ip_key(ip))
            delay = throttle.register_failure(identity_key(username))
            _audit_login(OUTCOME_FAILURE, username=username, retry_after=delay)
            app.display_toast(success=False, message=GENERIC_LOGIN_ERROR)
            return render_template('security/login.html', form=form)

        # 4) Succès : on réinitialise les compteurs des deux clés et on journalise.
        throttle.register_success(ip_key(ip))
        throttle.register_success(identity_key(username))
        remember = form.remember.data
        login_user(user, remember=remember)
        _audit_login(OUTCOME_SUCCESS, username=user.username)

        next_url = request.args.get('next') or form.next.data
        if not next_url or not is_safe_url(next_url):
            # Default post-login landing page.
            # SECURITY_POST_LOGIN_VIEW is configured as a path (ex: "/admin"), so we
            # prefer the known dashboard endpoint and fall back to the config value.
            try:
                next_url = url_for('admin_dashboard.admin')
            except Exception:
                next_url = app.config.get("SECURITY_POST_LOGIN_VIEW", "/admin")

        app.display_toast(success=True, message=f"Bienvenue {user.username} !")
        return redirect(next_url)

    if form.errors:
        # Erreurs de validation de formulaire (champ vide, CSRF…) : même message
        # générique, sans détailler le champ fautif au-delà de la validation WTForms.
        app.logger.info(f"Erreurs de validation du formulaire de connexion: {list(form.errors)}")
        app.display_toast(success=False, message=GENERIC_LOGIN_ERROR)

    return render_template('security/login.html', form=form)


# Hash bcrypt figé d'une valeur arbitraire, utilisé uniquement pour consommer un
# temps de calcul comparable à une vraie vérification lorsque l'utilisateur est
# inconnu (défense anti-timing / anti-énumération). Ce n'est pas un secret.
_DUMMY_BCRYPT_HASH = "$2b$12$Bw8tY3NUQeKVnA4/0sZ7budKVOtyCx7ULJ2uPOgUxudgzBjAU9UZO"


def _dummy_password_check(candidate):
    """Vérification factice à temps ~constant (utilisateur inexistant)."""
    try:
        import bcrypt
        bcrypt.checkpw((candidate or "").encode("utf-8"),
                       _DUMMY_BCRYPT_HASH.encode("utf-8"))
    except Exception:
        # Ne jamais faire échouer la connexion à cause de la vérif factice.
        pass


class ExtendedLoginForm(FlaskForm):
    username = StringField('Nom d\'utilisateur', [DataRequired()])
    password = PasswordField('Mot de passe', [DataRequired()])
    remember = BooleanField('Se souvenir de moi')
    next = HiddenField()

    def validate(self, extra_validators=None):
        app.logger.info("Validation du formulaire de connexion")
        if not super(ExtendedLoginForm, self).validate():
            app.logger.error("Erreur de validation du formulaire")
            return False
            
        app.logger.info(f"Validation OK pour username: {self.username.data}")
        return True
    
@admin_security_bp.route('/logout_all')
@require_permission('security')
def logout_all():
    """ Déconnexion de tous les utilisateurs 
    Cela permet de restaurer la base de données User """
    app.logger.info("Logout all users")
    # Supprimer toutes les sessions
    if os.path.exists('flask_session'):
        for filename in os.listdir('flask_session'):
            file_path = os.path.join('flask_session', filename)
            try:
                if os.path.isfile(file_path):
                    os.unlink(file_path)
            except Exception as e:
                app.logger.warning(f"Suppression de session impossible ({file_path}): {e}")

    record_audit(ACTION_LOGOUT_ALL, "session", outcome=OUTCOME_SUCCESS)
    return redirect(url_for('admin_security.login'))


@admin_security_bp.route('/logout')
def logout():
    logout_user()
    # Rediriger vers la page de login ou une autre page appropriée après la déconnexion
    return redirect(url_for('admin_security.login'))


def reset_roles_table():
    """Réinitialise la table des rôles"""
    """ A Supprimer quand toutes les BDD sont à jour"""
    try:
        # Suppression de la table roles_users
        db.session.execute(text('DROP TABLE IF EXISTS roles_users'))
        # Suppression de la table role
        db.session.execute(text('DROP TABLE IF EXISTS role'))
        db.session.commit()
        
        # Recréation des tables
        db.create_all()
        app.logger.info("Roles table reset successfully")
        return True
    except Exception as e:
        app.logger.error(f"Error resetting roles table: {str(e)}")
        return False

def create_default_role():
    """Crée le rôle admin par défaut avec toutes les permissions"""
    try:
        # Vérifier si le rôle admin existe déjà
        admin_role = Role.query.filter_by(name='admin').first()
        if admin_role:
            app.logger.info("Le rôle admin existe déjà")
            return True

        # Créer le rôle admin avec TOUTES les permissions du registre à True.
        # On dérive la liste du registre (source de vérité unique) plutôt que de
        # la répéter ici : impossible d'oublier une permission ajoutée plus tard.
        admin_role = Role(
            name='admin',
            description='Administrateur avec toutes les permissions',
        )
        for perm in PERMISSIONS:
            setattr(admin_role, perm.field, True)

        db.session.add(admin_role)
        db.session.commit()
        app.logger.info("Rôle admin créé avec succès")
        return True

    except Exception as e:
        app.logger.error(f"Erreur lors de la création du rôle admin: {str(e)}")
        return False

# Fonction utilitaire (PAS une route) : consultée en interne pour signaler la
# présence des identifiants par défaut. L'ancienne route GET
# ``/admin/check_default_admin`` a été retirée : elle renvoyait un booléen (vue
# Flask invalide) et divulguait l'existence du compte admin/admin par défaut.
def check_default_admin():
    """Check if the default admin/admin credentials still exist"""
    admin_user = User.query.filter_by(username='admin').first()

    # les valeurs de base sont trouvées
    if admin_user and admin_user.verify_password('admin'):
        return True    
    return False


def reset_admin_user():
    """Reset the admin user by deleting it if it exists"""
    try:
        # Supprimer d'abord toutes les associations roles-utilisateurs
        db.session.execute(db.text('DELETE FROM roles_users'))
        
        # Puis supprimer tous les utilisateurs
        db.session.execute(db.text('DELETE FROM app_users'))
        
        db.session.commit()
        app.logger.info("All users have been deleted")
        return True
    except Exception as e:
        app.logger.error(f"Error in reset_admin_user: {str(e)}")
        return False

@admin_security_bp.route('/admin/reset_admin', methods=['POST'])
@require_permission('security')
def reset_admin():
    """Reset the admin user and create a new one"""
    try:
        # Réinitialiser l'utilisateur admin
        if not reset_admin_user():
            app.display_toast(success=False, message="Erreur lors de la suppression des utilisateurs")
            return display_security_table()
            
        # Créer le nouvel utilisateur admin
        if not create_default_user():
            app.display_toast(success=False, message="Erreur lors de la création de l'utilisateur admin")
            return display_security_table()
            
        record_audit(ACTION_RESET, "user", target_id="admin", outcome=OUTCOME_SUCCESS)
        app.display_toast(success=True, message="Utilisateur admin réinitialisé avec succès")
        return display_security_table()
    except Exception as e:
        app.logger.error(f"Error in reset_admin: {str(e)}")
        record_audit(ACTION_RESET, "user", target_id="admin", outcome=OUTCOME_FAILURE)
        app.display_toast(success=False, message=f"Erreur lors de la réinitialisation : {str(e)}")
        return display_security_table()

@admin_security_bp.route('/admin/security/role_update/<int:role_id>', methods=['POST'])
@require_permission_api('security')
def security_update_role(role_id):
    try:
        app.logger.info(f"=== Début de la mise à jour du rôle {role_id} ===")
        app.logger.info(f"Request data: {request.data}")
        app.logger.info(f"Request form: {request.form}")
        app.logger.info(f"Request content type: {request.content_type}")
        
        # Récupérer les données du formulaire
        if request.is_json:
            data = request.get_json()
        else:
            data = request.form.to_dict()
            
        app.logger.info(f"Data after parsing: {data}")
        
        name = data.get('name')
        description = data.get('description')
        permissions_str = data.get('permissions', '{}')
        
        app.logger.info(f"Permissions string: {permissions_str}")
        
        try:
            permissions = json.loads(permissions_str)
        except json.JSONDecodeError as e:
            app.logger.error(f"Erreur de décodage JSON: {str(e)}")
            app.logger.error(f"Contenu qui a causé l'erreur: {permissions_str}")
            return jsonify({'error': 'Invalid JSON format for permissions'}), 400

        app.logger.info(f"Permissions parsed: {permissions}")

        # Récupérer le rôle
        role = Role.query.get(role_id)
        if not role:
            app.logger.error(f"Rôle {role_id} non trouvé")
            return jsonify({'error': 'Role not found'}), 404

        # Mise à jour des données de base
        role.name = name
        role.description = description

        # Mise à jour des permissions
        app.logger.info(f"Permissions avant mise à jour: {role.get_permissions()}")
        for permission_name, value in permissions.items():
            if hasattr(role, permission_name):
                app.logger.info(f"Setting {permission_name} to {value} (type: {type(value)})")
                new_value = bool(value)
                app.logger.info(f"Setting {permission_name} to {new_value} (type: {type(value)})")
                setattr(role, permission_name, new_value)

        app.logger.info(f"Permissions après mise à jour: {role.get_permissions()}")

        try:
            db.session.commit()
            app.logger.info("Rôle mis à jour avec succès")
            app.logger.info(f"Permissions finales du rôle: {role.to_dict()['permissions']}")
            record_audit(ACTION_UPDATE, "role", target_id=role_id, outcome=OUTCOME_SUCCESS,
                         details=f"name={name}")
            app.display_toast(success=True, message="Rôle mis à jour avec succès")
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Erreur lors du commit: {str(e)}")
            app.logger.error(f"Type d'erreur: {type(e)}")
            record_audit(ACTION_UPDATE, "role", target_id=role_id, outcome=OUTCOME_FAILURE)
            app.display_toast(success=False, message="Erreur lors de la mise à jour du rôle")
            return jsonify({'error': str(e)}), 500

    except Exception as e:
        app.logger.error(f"Erreur générale: {str(e)}")
        app.logger.error(f"Type d'erreur: {type(e)}")
        record_audit(ACTION_UPDATE, "role", target_id=role_id, outcome=OUTCOME_FAILURE)
        app.display_toast(success=False, message="Erreur lors de la mise à jour du rôle")
        return jsonify({'error': str(e)}), 500

@admin_security_bp.route('/admin/security/save_role', methods=['POST'])
@require_permission('security')
def save_role():
    try:
        data = request.get_json() if request.is_json else request.form
        name = data.get('name')
        description = data.get('description')
        
        if not name:
            app.logger.error("Le nom est requis")
            app.display_toast(success=False, message="Le nom est requis")
            return ""

        permissions_str = data.get('permissions', '{}')
        try:
            permissions = json.loads(permissions_str)
        except json.JSONDecodeError:
            app.logger.error("Format de permissions invalide")
            app.display_toast(success=False, message="Format de permissions invalide")
            return ""

        app.logger.info(f"Création d'un nouveau rôle - name: {name}, description: {description}")
        app.logger.info(f"Permissions: {permissions}")

        # Création du rôle : les colonnes admin_* du modèle valent False par
        # défaut, inutile de les répéter ici. On n'active que ce qui est soumis.
        role = Role(
            name=name,
            description=description,
        )

        # Attribution des permissions. On restreint aux champs réellement connus
        # du modèle (hasattr) : une clé parasite venue du formulaire est ignorée.
        for permission_name, value in permissions.items():
            if hasattr(role, permission_name):
                app.logger.info(f"Setting {permission_name} to {value} (type: {type(value)})")
                new_value = bool(value)
                app.logger.info(f"Setting {permission_name} to {new_value} (type: {type(value)})")
                setattr(role, permission_name, new_value)
                app.logger.info(f"Nouvelle valeur de {permission_name}: {getattr(role, permission_name)}")

        try:
            db.session.add(role)
            db.session.commit()
            app.logger.info("Rôle créé avec succès")
            app.logger.info(f"Permissions finales du rôle: {role.to_dict()['permissions']}")
            record_audit(ACTION_CREATE, "role", target_id=role.id, outcome=OUTCOME_SUCCESS,
                         details=f"name={name}")
            app.display_toast(success=True, message="Rôle créé avec succès")
            return ""
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Erreur lors de la création du rôle: {str(e)}")
            record_audit(ACTION_CREATE, "role", target_id=name, outcome=OUTCOME_FAILURE)
            app.display_toast(success=False, message="Erreur lors de la création du rôle")
            return ""

    except Exception as e:
        app.logger.error(f"Erreur lors de la création du rôle: {str(e)}")
        app.display_toast(success=False, message="Erreur lors de la création du rôle")
        return ""

@admin_security_bp.route('/admin/security/add_role_form')
@require_permission('security')
def add_role_form():
    """Affiche le formulaire d'ajout de rôle.

    Les cases à cocher sont générées à partir du registre de permissions (source
    de vérité unique), groupées par catégorie : plus aucune liste codée en dur
    dans le gabarit, donc plus de divergence possible avec le modèle.
    """
    return render_template('/admin/security_add_role_form.html',
                           permissions_by_category=permissions_by_category())

@admin_security_bp.route('/admin/security/delete_role/<int:role_id>', methods=['DELETE'])
@require_permission('security')
# supprime un rôle
def delete_role(role_id):
    try:
        role = Role.query.get(role_id)
        if not role:
            app.display_toast(success=False, message="Role non trouvé")
            return display_security_role_table()

        deleted_role_name = role.name
        db.session.delete(role)
        db.session.commit()

        record_audit(ACTION_DELETE, "role", target_id=role_id, outcome=OUTCOME_SUCCESS,
                     details=f"name={deleted_role_name}")
        app.display_toast(success=True, message="Role supprimé")
        return display_security_role_table()

    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_DELETE, "role", target_id=role_id, outcome=OUTCOME_FAILURE)
        app.display_toast(success=False, message="erreur : " + str(e))
        return display_security_role_table()

@admin_security_bp.route('/admin/security/change_password/<int:user_id>', methods=['GET'])
@require_permission('security')
def change_password_form(user_id):
    try:
        user = User.query.get(user_id)
        if not user:
            app.display_toast(success=False, message="Utilisateur non trouvé")
            return ""

        return render_template('admin/security_change_password.html', user_id=user_id)

    except Exception as e:
        app.display_toast(success=False, message=f"Erreur : {str(e)}")
        return ""

@admin_security_bp.route('/admin/security/update_password/<int:user_id>', methods=['POST'])
@require_permission('security')
def update_password(user_id):
    try:
        user = User.query.get(user_id)
        if not user:
            app.display_toast(success=False, message="Utilisateur non trouvé")
            return display_security_table()

        password1 = request.form.get('password1')
        password2 = request.form.get('password2')

        if not password1 or not password2:
            app.display_toast(success=False, message="Les deux champs de mot de passe sont obligatoires")
            return display_security_table()

        if password1 != password2:
            app.display_toast(success=False, message="Les mots de passe ne correspondent pas")
            return display_security_table()

        # Politique minimale de mot de passe (point 3.4)
        policy_problems = validate_password(password1, username=user.username)
        if policy_problems:
            app.logger.warning("Mot de passe refusé par la politique au changement de mot de passe")
            app.display_toast(success=False, message=" ".join(policy_problems))
            return display_security_table()

        user.set_password(password1)
        db.session.commit()

        # Le mot de passe lui-même n'est JAMAIS journalisé, seulement le fait
        # qu'il a été changé et sur quel compte.
        record_audit(ACTION_UPDATE, "password", target_id=user_id, outcome=OUTCOME_SUCCESS,
                     details=f"username={user.username}")
        app.display_toast(success=True, message="Mot de passe mis à jour avec succès")
        return display_security_table()

    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_UPDATE, "password", target_id=user_id, outcome=OUTCOME_FAILURE)
        app.display_toast(success=False, message=f"Erreur lors de la mise à jour : {str(e)}")
        app.logger.error(f"Error in update_password: {str(e)}")
        return display_security_table()

def create_default_user():
    """Crée l'utilisateur admin par défaut s'il n'existe pas"""
    try:
        if User.query.count() == 0:
            app.logger.info("Creating admin user...")            
           
            # Création de l'utilisateur admin
            admin_user = User(
                email='admin',
                username='admin',
                active=True,
                confirmed_at=datetime.now()
            )
            admin_user.set_password('admin')

            # Attribution du rôle admin
            admin_role = Role.query.filter_by(name='admin').first()
            admin_user.roles.append(admin_role)
            
            db.session.add(admin_user)
            db.session.commit()
            
            app.logger.info("Admin user created successfully with admin role")
            return True
        return True
    except Exception as e:
        app.logger.error(f"Error in create_default_user: {str(e)}")
        return False
