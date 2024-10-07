import os
from flask import Blueprint, render_template, jsonify, redirect, url_for, request, current_app as app
from flask_security import hash_password
from datetime import datetime
from models import User, db
from flask_mailman import EmailMessage
from flask_login import logout_user
from flask_security import Security, current_user, auth_required, hash_password, \
    SQLAlchemySessionUserDatastore, permissions_accepted, UserMixin, RoleMixin, AsaList, SQLAlchemyUserDatastore, login_required, lookup_identity, uia_username_mapper, verify_and_update_password, login_user
from wtforms import StringField, PasswordField, HiddenField
from wtforms.validators import DataRequired
from flask_security.forms import LoginForm, BooleanField
from urllib.parse import urlparse, urljoin

admin_security_bp = Blueprint('admin_security', __name__)

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
                        security_remember_duration=app.config["SECURITY_REMEMBER_DURATION"])

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

        if not username:  # Vérifiez que les champs obligatoires sont remplis
            app.display_toast(success=False, message="Nom obligatoire")
            return display_security_table()
        if not password1 or not password2:
            app.display_toast(success=False, message="Les deux champs de mots de passe sont obligatoires")
            return display_security_table()
        if password1 != password2:
            app.display_toast(success=False, message="Les deux champs de mots de passe doivent être similaires")
            return display_security_table()

        new_user = User(
            username = username,
            email = email,
            password = hash_password(password1)
        )
        db.session.add(new_user)
        db.session.commit()

        app.communication('update_admin', data={"action": "delete_add_rule_form"})
        app.display_toast(success=True, message="Utilisateur ajouté avec succès")

        return display_security_table()

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
    return render_template('/admin/security_modal_confirm_delete_user.html', user=user)


# supprime une regle de l'algo
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

        self.user = User.query.filter_by(username=self.username.data).first()
        if not self.user:
            print("Unknown username")
            return False

        if not verify_and_update_password(self.password.data, self.user):
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
