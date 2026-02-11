import os
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, current_app as app
from flask_login import current_user
from functools import wraps
from models import db, Role, User, DashboardCard
from flask_mailman import EmailMessage
from flask_security import login_required, login_user
from wtforms import StringField, PasswordField, HiddenField, BooleanField
from flask_wtf import FlaskForm
from wtforms.validators import DataRequired
from urllib.parse import urlparse, urljoin
from datetime import datetime
import json

admin_security_bp = Blueprint('admin_security', __name__)

# gère les permissions sur les pages
def require_permission(resource):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                app.logger.warning("Utilisateur non authentifié tentant d'accéder à une ressource protégée")
                return redirect(url_for('security.login'))

            # Vérifier si l'utilisateur a un rôle
            if not current_user.roles:
                error_message = f"Vous n'avez pas les permissions nécessaires pour pour accéder à la page '{resource}'."
                return render_template('admin/permission_error.html', error_message=error_message)

            # Vérifier si au moins un des rôles de l'utilisateur a la permission
            has_permission = False
            for role in current_user.roles:
                app.logger.debug(f"Vérification du rôle {role.name} pour la ressource {resource}")
                app.logger.debug(f"Permissions du rôle: {role.get_permissions()}")
                
                permission_field = f'admin_{resource}'
                if hasattr(role, permission_field):
                    permission_value = getattr(role, permission_field)
                    app.logger.debug(f"Valeur de la permission {permission_field}: {permission_value}")
                    if permission_value:
                        has_permission = True
                        break

            if not has_permission:
                error_message = f"Vous n'avez pas les permissions nécessaires pour accéder à la page '{resource}'."
                return render_template('admin/permission_error.html', error_message=error_message)

            return f(*args, **kwargs)
        return decorated_function
    return decorator


def require_permission_dashboard(resource):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                app.logger.warning("Utilisateur non authentifié tentant d'accéder à une ressource protégée")
                return redirect(url_for('security.login'))

            # Vérifier si au moins un des rôles de l'utilisateur a la permission
            has_permission = False
            for role in current_user.roles:
                app.logger.debug(f"Vérification du rôle {role.name} pour la ressource {resource}")
                app.logger.debug(f"Permissions du rôle: {role.get_permissions()}")
                
                permission_field = f'admin_{resource}'
                if hasattr(role, permission_field):
                    permission_value = getattr(role, permission_field)
                    app.logger.debug(f"Valeur de la permission {permission_field}: {permission_value}")
                    if permission_value:
                        has_permission = True
                        break

            if not has_permission:
                app.logger.warning(f"Utilisateur {current_user.username} n'a pas la permission {resource}")
                # Récupérer le dashboardcard depuis les arguments de la fonction
                # On suppose que la fonction originale utilise DashboardCard.query.filter_by(name=...)
                dashboardcard = DashboardCard.query.filter_by(name=resource).first()
                
                # Renvoyer uniquement le template avec le message d'erreur
                return render_template('/admin/dashboard_base.html',
                                    dashboardcard=dashboardcard,
                                    error_message="Vous n'avez pas les permissions nécessaires pour accéder à cette partie.",
                                    title=resource)

            # Si l'utilisateur a la permission, exécuter la fonction normalement
            return f(*args, **kwargs)
        return decorated_function
    return decorator


@admin_security_bp.route('/admin/security/role/table')
def display_security_role_table():
    roles = Role.query.all()
    return render_template('admin/security_htmx_role_table.html', roles=roles)

@admin_security_bp.route('/admin/security/dashboard')
@require_permission_dashboard('security')
def dashboard_security():
    dashboardcard = DashboardCard.query.filter_by(name="security").first()
    is_default = check_default_admin()
    return render_template('/admin/dashboard_security.html',
                            dashboardcard=dashboardcard,
                            is_default_admin=is_default)

@admin_security_bp.route('/admin/security')
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
def display_security_table():
    users = User.query.all()
    roles = Role.query.all()
    return render_template('admin/security_htmx_table.html', users=users, roles=roles)

# affiche le formulaire pour ajouter une regle de l'algo
@admin_security_bp.route('/admin/security/add_user_form')
def add_user_form():
    roles = Role.query.all()
    return render_template('/admin/security_add_user_form.html', roles=roles)


@admin_security_bp.route('/admin/security/add_new_user', methods=['POST'])
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
        
        app.logger.info(f"Hash du mot de passe pour le nouvel utilisateur: {user.password}")

        db.session.add(user)
        db.session.commit()
        
        app.logger.info(f"Nouvel utilisateur créé avec succès: {username}")
        app.display_toast(success=True, message="Utilisateur créé avec succès")
        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_user_form"></div>"""
        return f"{display_security_table()}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Erreur lors de la création de l'utilisateur: {str(e)}")
        app.display_toast(success=False, message=f"Erreur lors de la création : {str(e)}")
        return display_security_table()


@admin_security_bp.route('/admin/security/user_update/<int:user_id>', methods=['POST'])
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
        app.display_toast(success=True, message="Utilisateur mis à jour avec succès")
        return display_security_table()

    except Exception as e:
        db.session.rollback()
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
                app.display_toast(success=False, message="Impossible de supprimer le dernier administrateur")
                return display_security_table()

        db.session.delete(user)
        db.session.commit()

        app.display_toast(success=True, message="Utilisateur supprimé avec succès")
        return display_security_table()

    except Exception as e:
        db.session.rollback()
        app.display_toast(success=False, message=f"Erreur lors de la suppression : {str(e)}")
        app.logger.error(f"Error in delete_user: {str(e)}")
        return display_security_table()

# affiche la modale pour confirmer la suppression d'un membre
@admin_security_bp.route('/admin/security/confirm_delete_user/<int:user_id>', methods=['GET'])
def confirm_delete_user(user_id):
    user = User.query.get(user_id)
    print(user)
    return render_template('/admin/security_modal_confirm_delete_user.html', user=user)

# affiche la modale pour confirmer la suppression d'un role
@admin_security_bp.route('/admin/security/confirm_delete_role/<int:role_id>', methods=['GET'])
def confirm_delete_role(role_id):
    role = Role.query.get(role_id)
    return render_template('/admin/security_modal_confirm_delete_role.html', role=role)


# supprime un utilisateur
@admin_security_bp.route('/admin/security/delete_user/<int:user_id>', methods=['GET'])
def delete_user(user_id):
    try:
        user = User.query.get(user_id)
        if not user:
            app.display_toast(success=False, message="Utilisateur non trouvé")
            return display_security_table()

        db.session.delete(user)
        db.session.commit()

        app.display_toast(success=True, message="Utilisateur supprimé")
        return display_security_table()

    except Exception as e:
        app.display_toast(success=False, message="erreur : " + str(e))
        return display_security_table()  
    

@admin_security_bp.route('/send_test_email')
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
    
    if form.validate_on_submit():
        app.logger.info(f"Formulaire soumis avec username: {form.username.data}")
        user = User.query.filter_by(username=form.username.data).first()
        
        if user is None:
            app.logger.error("Utilisateur non trouvé")
            app.display_toast(success=False, message="Nom d'utilisateur inconnu")
            return render_template('security/login.html', form=form)
            
        app.logger.info(f"Hash stocké pour {user.username}: {user.password}")
        app.logger.info(f"Tentative de connexion avec mot de passe: {form.password.data}")
        
        if not user.verify_password(form.password.data):
            app.logger.error("Mot de passe incorrect")
            app.display_toast(success=False, message="Mot de passe incorrect")
            return render_template('security/login.html', form=form)
            
        remember = form.remember.data 
        login_user(user, remember=remember)
        app.logger.info(f"Connexion réussie pour {user.username}")
        
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
        app.logger.error(f"Erreurs de validation: {form.errors}")
        app.display_toast(success=False, message="Identifiants incorrects")
    
    return render_template('security/login.html', form=form)


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
                print(e)    
    
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

        # Créer le rôle admin avec toutes les permissions à True par défaut
        admin_role = Role(
            name='admin',
            description='Administrateur avec toutes les permissions',
            admin_security=True,
            admin_counter=True,
            admin_activity=True,
            admin_schedule=True,
            admin_algo=True,
            admin_translation=True,
            admin_options=True,
            admin_music_play=True,
            admin_music_options=True,
            admin_app=True,
            admin_queue=True,
            admin_stats=True,
            admin_staff=True,
            admin_phone=True,
            admin_announce=True,
            admin_patient=True,
            admin_gallery=True
        )

        db.session.add(admin_role)
        db.session.commit()
        app.logger.info("Rôle admin créé avec succès")
        return True

    except Exception as e:
        app.logger.error(f"Erreur lors de la création du rôle admin: {str(e)}")
        return False

@admin_security_bp.route('/admin/check_default_admin', methods=['GET'])
def check_default_admin():
    """Check if the default admin/admin credentials still exist"""
    admin_user = User.query.filter_by(username='admin').first()
    
    print("admin_user", admin_user)

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
            
        app.display_toast(success=True, message="Utilisateur admin réinitialisé avec succès")
        return display_security_table()
    except Exception as e:
        app.logger.error(f"Error in reset_admin: {str(e)}")
        app.display_toast(success=False, message=f"Erreur lors de la réinitialisation : {str(e)}")
        return display_security_table()

@admin_security_bp.route('/admin/security/role_update/<int:role_id>', methods=['POST'])
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
            app.display_toast(success=True, message="Rôle mis à jour avec succès")
            return jsonify({'success': True})
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Erreur lors du commit: {str(e)}")
            app.logger.error(f"Type d'erreur: {type(e)}")
            app.display_toast(success=False, message="Erreur lors de la mise à jour du rôle")
            return jsonify({'error': str(e)}), 500

    except Exception as e:
        app.logger.error(f"Erreur générale: {str(e)}")
        app.logger.error(f"Type d'erreur: {type(e)}")
        app.display_toast(success=False, message="Erreur lors de la mise à jour du rôle")
        return jsonify({'error': str(e)}), 500

@admin_security_bp.route('/admin/security/save_role', methods=['POST'])
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

        # Création du rôle avec toutes les permissions à False par défaut
        role = Role(
            name=name,
            description=description,
            admin_security=False,
            admin_counter=False,
            admin_activity=False,
            admin_schedule=False,
            admin_algo=False,
            admin_translation=False,
            admin_options=False,
            admin_music_play=False,
            admin_music_options=False,
            admin_app=False,
            admin_queue=False,
            admin_stats=False,
            admin_staff=False,
            admin_phone=False,
            admin_announce=False,
            admin_patient=False,
            admin_gallery=False
        )

        # Attribution des permissions
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
            app.display_toast(success=True, message="Rôle créé avec succès")
            return ""
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Erreur lors de la création du rôle: {str(e)}")
            app.display_toast(success=False, message="Erreur lors de la création du rôle")
            return ""

    except Exception as e:
        app.logger.error(f"Erreur lors de la création du rôle: {str(e)}")
        app.display_toast(success=False, message="Erreur lors de la création du rôle")
        return ""

@admin_security_bp.route('/admin/security/add_role_form')
def add_role_form():
    """Affiche le formulaire d'ajout de rôle"""
    # Récupérer la liste des pages à partir des attributs du modèle Role
    admin_pages = [attr.replace('admin_', '') for attr in vars(Role) if attr.startswith('admin_')]
    return render_template('/admin/security_add_role_form.html', admin_pages=admin_pages)

@admin_security_bp.route('/admin/security/delete_role/<int:role_id>', methods=['GET'])
@require_permission('security')
# supprime un utilisateur
def delete_role(role_id):
    try:
        role = Role.query.get(role_id)
        print("role", role)
        if not role:
            app.display_toast(success=False, message="Role non trouvé")
            return display_security_role_table()

        db.session.delete(role)
        db.session.commit()

        app.display_toast(success=True, message="Role supprimé")
        return display_security_role_table()

    except Exception as e:
        app.display_toast(success=False, message="erreur : " + str(e))
        return display_security_role_table()  

@admin_security_bp.route('/admin/security/change_password/<int:user_id>', methods=['GET'])
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

        user.set_password(password1)
        db.session.commit()

        app.display_toast(success=True, message="Mot de passe mis à jour avec succès")
        return display_security_table()

    except Exception as e:
        db.session.rollback()
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
            print(f"Hash du mot de passe admin: {admin_user.password}")
            
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
