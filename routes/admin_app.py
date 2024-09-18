from flask import Blueprint, render_template, request, current_app as app
from routes.admin_security import send_test_email

admin_app_bp = Blueprint('admin_app', __name__)

@admin_app_bp.route('/admin/app')
def admin_app():
    return render_template('/admin/app.html',
                            network_adress = app.config["NETWORK_ADRESS"],
                            numbering_by_activity = app.config["NUMBERING_BY_ACTIVITY"], 
                            announce_sound = app.config["ANNOUNCE_SOUND"],
                            pharmacy_name = app.config["PHARMACY_NAME"],
                            mail_server = app.config["MAIL_SERVER"],
                            mail_port=app.config["MAIL_PORT"],
                            mail_username=app.config["MAIL_USERNAME"],
                            mail_password=app.config["MAIL_PASSWORD"],
                            mail_default_sender=app.config["MAIL_DEFAULT_SENDER"],
                            mail_use_tls=app.config["MAIL_USE_TLS"],
                            mail_use_ssl=app.config["MAIL_USE_SSL"],
    )

@admin_app_bp.route('/admin/app/mail/test')
def admin_app_mail_test():
    mail_adress = request.values.get('mail_adress')

    print("mail adress", mail_adress)

    if mail_adress == "":
        app.display_toast(success=False, message="Veuillez entrer une adresse email")
        
    if send_test_email(mail_adress):
        app.display_toast(success=True, message="Email envoyé")
    else:
        app.display_toast(success=False, message="Email non envoyé")    

    return "", 200