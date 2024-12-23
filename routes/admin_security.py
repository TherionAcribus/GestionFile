import os
from flask import Blueprint, render_template, jsonify, redirect, url_for, request, current_app as app
from datetime import datetime
from models import User, db, Role
from flask_mailman import EmailMessage
from flask_login import logout_user
from flask_security import current_user, hash_password, login_required, login_user
from wtforms import StringField, PasswordField, HiddenField
from wtforms.validators import DataRequired
from flask_security.forms import LoginForm, BooleanField
from urllib.parse import urlparse, urljoin
from werkzeug.security import generate_password_hash
from communication import communikation
from functools import wraps
from flask import flash
from sqlalchemy import text
import json

admin_security_bp = Blueprint('admin_security', __name__)

def require_permission(page, level='read'):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('admin_security.login'))
            
            has_permission = False
            for role in current_user.roles:
                permission = getattr(role, f'admin_{page}', 'none')
                if (level == 'read' and permission in ['read', 'write']) or \
                    (level == 'write' and permission == 'write'):
                    has_permission = True
                    break
            
            if not has_permission:
                flash('Vous n\'avez pas les permissions nécessaires pour accéder à cette page.', 'error')
                return redirect(url_for('admin_security.home'))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator


@admin_security_bp.route('/admin/security/role/table')
def display_security_role_table():
    roles = Role.query.all()
    return render_template('admin/security_htmx_role_table.html', roles=roles)

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
    return render_template('admin/security_htmx_table.html', users=users)

# affiche le formulaire pour ajouter une regle de l'algo
@admin_security_bp.route('/admin/security/add_user_form')
def add_user_form():
    return render_template('/admin/security_add_user_form.html')


@admin_security_bp.route('/admin/security/add_new_user', methods=['POST'])
def add_new_user():
    try:
        username = request.form.get('username')
        email = request.form.get("email")
        password1 = request.form.get("password1")
        password2 = request.form.get("password2")

        # Vérifications de base
        if not username:
            app.display_toast(success=False, message="Nom obligatoire")
            return display_security_table()
        if not password1 or not password2:
            app.display_toast(success=False, message="Les deux champs de mots de passe sont obligatoires")
            return display_security_table()
        if password1 != password2:
            app.display_toast(success=False, message="Les deux champs de mots de passe doivent être similaires")
            return display_security_table()

        # Vérification si le nom d'utilisateur existe déjà
        if User.query.filter_by(username=username).first():
            app.display_toast(success=False, message="Ce nom d'utilisateur existe déjà")
            return display_security_table()

        # Vérifications supplémentaires de sécurité
        #if len(password1) < 8:
        #    app.display_toast(success=False, message="Le mot de passe doit contenir au moins 8 caractères")
        #    return display_security_table()

        # Création du nouvel utilisateur avec mot de passe haché
        hashed_password = generate_password_hash(password1, method='pbkdf2:sha256')
        new_user = User(
            username=username,
            email=None,  # On met l'email à None par défaut
            password=hashed_password
        )
        
        # Si un email est fourni et qu'il n'existe pas déjà, on l'ajoute
        if email and not User.query.filter_by(email=email).first():
            new_user.email = email

        db.session.add(new_user)
        db.session.commit()

        communikation('update_admin', data={"action": "delete_add_rule_form"})
        app.display_toast(success=True, message="Utilisateur ajouté avec succès")

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_user_form"></div>"""
        return f"{display_security_table()}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        app.display_toast(success=False, message="erreur : " + str(e))
        print(e)
        return "", 500


@admin_security_bp.route('/admin/security/user_update/<int:user_id>', methods=['POST'])
def security_update_user(user_id):
    try:
        user = User.query.get(user_id)
        if user:
            if request.form.get('username') == '':
                app.display_toast(success=False, message="Le nom est obligatoire")
                return ""
            elif request.form.get("password1") == "" or request.form.get("password2") == "":
                app.display_toast(success=False, message="Les deux mots de passe sont obligatoires")
            elif request.form.get("password1") != request.form.get("password2"):
                app.display_toast(success=False, message="Les deux mots de passe doivent être similaires")

            user.username = request.form.get('username', user.username)
            user.email = request.form.get('email', user.email)
            user.password = hash_password(request.form.get('password1', user.password))
            user.active = True
            user.confirmed_at=datetime.now()

            db.session.commit()

            app.display_toast(success=True, message="Mise à jour réussie")
            return ""
        else:
            app.display_toast(success=False, message="Règle introuvable")
            return ""

    except Exception as e:
            app.display_toast(success=False, message="erreur : " + str(e))
            app.logger.error(e)
            return jsonify(status="error", message=str(e)), 500
    

# affiche la modale pour confirmer la suppression d'un membre
@admin_security_bp.route('/admin/security/confirm_delete_user/<int:user_id>', methods=['GET'])
def confirm_delete_user(user_id):
    user = User.query.get(user_id)
    print(user)
    return render_template('/admin/security_modal_confirm_delete_user.html', user=user)

# affiche la modale pour confirmer la suppression d'un membre
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
    print("FLASK LOGIN")
    form = ExtendedLoginForm()
    # Récupérez 'next' de l'URL ou du formulaire
    next_url = request.args.get('next') or form.next.data
    
    # Assurez-vous que form.next.data est toujours défini
    form.next.data = next_url

    print("next_url", next_url)
    
    if form.validate_on_submit():
        user = form.user
        remember = form.remember.data 
        login_user(user, remember=remember)
        
        # Vérifiez si l'URL next est sûre avant de rediriger
        if not next_url or not is_safe_url(next_url):
            next_url = url_for('admin_security.home')
        
        return redirect(next_url)
    
    return render_template('security/login.html', form=form)


class ExtendedLoginForm(LoginForm):
    print("EXTENDED LOGIN FORM")
    username = StringField('Nom d\'utilisateur', [DataRequired()])
    password = PasswordField('Mot de passe', [DataRequired()])
    remember = BooleanField('Se souvenir de moi')
    email = None
    next = HiddenField()

    def validate(self, extra_validators=None):
        print("VALIDATE")
        self.user = User.query.filter_by(username=self.username.data).first()
        print(self.user)

        if not self.user:
            print("Unknown username")
            return False

        if not self.user.verify_password(self.password.data):
            print("Invalid password")
            return False
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


# POSITION TEMPORAIRE -> FAIRE UNE VRAIE PAGE D'ACCEUIL AVEC TOUTES LES PAGES + DOC
@admin_security_bp.route('/')
@login_required
def home():
    return "Bonjour la pharmacie!"

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
        db.session.rollback()
        return False

def create_default_role():
    """Crée le rôle admin par défaut s'il n'existe pas"""
    try:
        # Vérifie si la table role existe et a la bonne structure
        admin_role = Role.query.filter_by(name='admin').first()
        if not admin_role:
            app.logger.info("Creating admin role...")
            admin_role = Role(
                name='admin',
                description='Administrateur avec tous les droits'
            )
            # Attribution de toutes les permissions en écriture
            admin_pages = ['security', 'counter', 'activity', 'schedule', 'algo', 
                          'translation', 'options', 'music', 'dashboard', 'app']
            for page in admin_pages:
                setattr(admin_role, f'admin_{page}', 'write')
            
            db.session.add(admin_role)
            try:
                db.session.commit()
                app.logger.info("Admin role created successfully")
            except Exception as commit_error:
                app.logger.error(f"Error committing admin role: {str(commit_error)}")
                db.session.rollback()
                if "Unknown column" in str(commit_error):
                    app.logger.info("Resetting roles table due to missing columns...")
                    if reset_roles_table():
                        return create_default_role()  # Réessayer après la réinitialisation
                return None
        return admin_role
    except Exception as e:
        app.logger.error(f"Error in create_default_role: {str(e)}")
        db.session.rollback()
        # Si l'erreur est due à une colonne manquante, réinitialiser la table
        if "Unknown column" in str(e):
            app.logger.info("Resetting roles table due to missing columns...")
            if reset_roles_table():
                return create_default_role()  # Réessayer après la réinitialisation
        return None

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
    admin_user = User.query.filter_by(username='admin').first()
    if admin_user:
        db.session.delete(admin_user)
        db.session.commit()
        return True
    return False

@admin_security_bp.route('/admin/reset_admin', methods=['POST'])
def reset_admin():
    """Reset the admin user and create a new one"""
    try:
        reset_admin_user()
        create_default_user()
        return jsonify({
            "success": True,
            "message": "L'utilisateur admin a été réinitialisé avec succès"
        }), 200
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Erreur lors de la réinitialisation : {str(e)}"
        }), 500



@admin_security_bp.route('/admin/security/role_update/<int:role_id>', methods=['POST'])
def security_update_role(role_id):
    try:
        role = Role.query.get(role_id)
        if role:
            # Récupérer les données JSON ou form-data
            data = request.get_json() if request.is_json else request.form
            app.logger.info(f"Received data: {data}")

            if not data.get('name'):
                app.display_toast(success=False, message="Le nom est obligatoire")
                return ""

            # Mise à jour des informations de base
            role.name = data.get('name', role.name)
            role.description = data.get('description', role.description)
            app.logger.info(f"Updated name to: {role.name}, description to: {role.description}")

            # Mise à jour des permissions
            permissions_str = data.get('permissions', '{}')
            if isinstance(permissions_str, str):
                try:
                    permissions = json.loads(permissions_str)
                except json.JSONDecodeError:
                    permissions = {}

            app.logger.info(f"Permissions before update: {role.to_dict()['permissions']}")
            app.logger.info(f"Received permissions: {permissions}")
            
            if isinstance(permissions, dict):
                for page, permission in permissions.items():
                    if hasattr(role, page):
                        old_value = getattr(role, page)
                        setattr(role, page, permission)
                        app.logger.info(f"Updated {page} from {old_value} to {permission}")

            try:
                db.session.commit()
                app.logger.info("Changes committed successfully")
                app.logger.info(f"Permissions after update: {role.to_dict()['permissions']}")
                app.display_toast(success=True, message="Mise à jour réussie")
            except Exception as commit_error:
                app.logger.error(f"Error committing changes: {str(commit_error)}")
                db.session.rollback()
                app.display_toast(success=False, message=f"Erreur lors de la sauvegarde : {str(commit_error)}")
                return ""
            
            return ""

        else:
            app.display_toast(success=False, message="Role introuvable")
            return ""

    except Exception as e:
        app.display_toast(success=False, message="erreur : " + str(e))
        app.logger.error(f"Error in security_update_role: {str(e)}")
        return jsonify(status="error", message=str(e)), 500


@admin_security_bp.route('/admin/security/delete_role/<int:role_id>', methods=['GET'])
@require_permission('security', 'write')
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

def create_default_user():
    """Crée l'utilisateur admin par défaut s'il n'existe pas"""
    try:
        if User.query.count() == 0:
            app.logger.info("Creating admin user...")
            
            # Création du rôle admin
            admin_role = create_default_role()
            if not admin_role:
                app.logger.error("Failed to create admin role")
                return False
            
            # Création de l'utilisateur admin
            hashed_password = generate_password_hash('admin', method='pbkdf2:sha256')
            admin_user = User(
                email='admin',
                username='admin',
                password=hashed_password,
                active=True,
                confirmed_at=datetime.now()
            )
            
            # Attribution du rôle admin
            admin_user.roles.append(admin_role)
            
            db.session.add(admin_user)
            db.session.commit()
            
            app.logger.info("Admin user created successfully with admin role")
            return True
        return True
    except Exception as e:
        app.logger.error(f"Error in create_default_user: {str(e)}")
        return False

@admin_security_bp.route('/admin/security/add_role_form')
def add_role_form():
    admin_pages = ['security', 'counter', 'activity', 'schedule', 'algo', 
                   'translation', 'options', 'music', 'dashboard', 'app']
    return render_template('/admin/security_add_role_form.html', admin_pages=admin_pages)

@admin_security_bp.route('/admin/security/save_role', methods=['POST'])
def save_role():
    try:
        # Récupérer les données JSON
        data = request.get_json() if request.is_json else request.form
        
        if not data.get('name'):
            app.display_toast(success=False, message="Le nom est obligatoire")
            return ""

        # Créer le nouveau rôle
        role = Role(
            name=data.get('name'),
            description=data.get('description', '')
        )

        print("permissions", data.get('permissions'))
        # Définir les permissions
        permissions = data.get('permissions', {})
        if isinstance(permissions, str):
            try:
                permissions = json.loads(permissions)
            except json.JSONDecodeError:
                permissions = {}

        if isinstance(permissions, dict):
            for page, permission in permissions.items():
                if hasattr(role, page):
                    setattr(role, page, permission)

        db.session.add(role)
        db.session.commit()
        
        app.display_toast(success=True, message="Rôle créé avec succès")        

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_role_form"></div>"""
        return f"{display_security_role_table()}{clear_form_html}"

    except Exception as e:
        app.display_toast(success=False, message=f"Erreur lors de la création : {str(e)}")
        app.logger.error(f"Error in save_role: {str(e)}")
        return jsonify(status="error", message=str(e)), 500