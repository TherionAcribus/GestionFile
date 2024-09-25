from flask import Blueprint, render_template, jsonify, request, current_app as app
from flask_security import hash_password
from datetime import datetime
from models import User, db
from flask_mailman import EmailMessage

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

