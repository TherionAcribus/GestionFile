import os
from flask import request, render_template, redirect, jsonify, current_app as app
from werkzeug.utils import secure_filename
from models import Button, Activity, Language, db

def admin_patient():
    buttons = Button.query.all()

    return render_template('/admin/patient_page.html', buttons=buttons,
                            page_patient_structure = app.config['PAGE_PATIENT_STRUCTURE'],
                            page_patient_disable_button = app.config['PAGE_PATIENT_DISABLE_BUTTON'],
                            page_patient_disable_default_message = app.config['PAGE_PATIENT_DISABLE_DEFAULT_MESSAGE'],
                            page_patient_title = app.config['PAGE_PATIENT_TITLE'],
                            page_patient_subtitle = app.config['PAGE_PATIENT_SUBTITLE'],
                            page_patient_display_qrcode = app.config['PAGE_PATIENT_QRCODE_DISPLAY'],
                            page_patient_qrcode_web_page = app.config['PAGE_PATIENT_QRCODE_WEB_PAGE'],
                            page_patient_qrcode_data = app.config['PAGE_PATIENT_QRCODE_DATA'],
                            page_patient_qrcode_display_specific_message = app.config['PAGE_PATIENT_QRCODE_DISPLAY_SPECIFIC_MESSAGE'],
                            page_patient_print_ticket_display = app.config['PAGE_PATIENT_PRINT_TICKET_DISPLAY'],
                            page_patient_end_timer = app.config['PAGE_PATIENT_END_TIMER'],
                            page_patient_display_specific_message = app.config['PAGE_PATIENT_DISPLAY_SPECIFIC_MESSAGE'],
                            page_patient_display_translations=app.config['PAGE_PATIENT_DISPLAY_TRANSLATIONS'],
                            ticket_header = app.config['TICKET_HEADER'],
                            ticket_message = app.config['TICKET_MESSAGE'],
                            ticket_footer = app.config['TICKET_FOOTER'],
                            printer_width = app.config['PRINTER_WIDTH'],
                            ticket_display_specific_message = app.config['TICKET_DISPLAY_SPECIFIC_MESSAGE'],
                            )


# affiche le tableau des boutons 
def display_button_table():
    buttons = Button.query.order_by(Button.sort_order).all()
    activities = Activity.query.all()
    return render_template('admin/patient_page_htmx_buttons_table.html', buttons=buttons, activities = activities)


def order_button_table():
    buttons = Button.query.order_by(Button.sort_order).all()
    return render_template('admin/patient_page_order_buttons.html', buttons=buttons)


# affiche la liste des boutons pour le 
def display_children_buttons(button_id):
    buttons = Button.query.order_by(Button.sort_order).filter_by(is_parent=True).all()
    button = Button.query.get(button_id)
    return render_template('admin/patient_page_button_display_children.html', buttons=buttons, button=button)


# mise à jour des informations d'un bouton
def update_button(button_id):
    try:
        button = Button.query.order_by(Button.sort_order).get(button_id)
        if button:
            # Récupérer l'ID de l'activité depuis le formulaire
            activity_id = request.form.get('activity')
            # GEstion du cas ou le bouton est un bouton parent
            if activity_id == "parent_button":
                button.is_parent = True
                button.activity = None
            else:
                # Récupérer l'instance de l'activité correspondante
                if activity_id:
                    activity = Activity.query.get(activity_id)
                    if activity:
                        button.activity = activity
                        button.is_parent = False
                    else:
                        print("Activité non trouvée")
                        return "Activité non trouvée", 404
                else:
                    # Si aucun ID d'activité n'est fourni, on peut décider de mettre l'attribut à None
                    button.activity = None
            
            parent_btn_id = request.form.get('parent_btn')
            if parent_btn_id:
                parent_button = Button.query.get(parent_btn_id)
                if parent_button:
                    button.parent_button = parent_button

            is_present = True if request.form.get('is_present') == "true" else False
            button.is_present = is_present

            button.label = request.form.get('label', button.label)

            button.shape = request.form.get('shape', button.shape)            

            db.session.commit()
            app.display_toast(success=True, message="Mise à jour effectuée")
            return ""
        else:
            app.display_toast(success=False, message="Membre de l'équipe introuvable")
            return ""

    except Exception as e:
            app.display_toast(success=False, message="erreur : " + str(e))
            app.logger.error(e)
            return jsonify(status="error", message=str(e)), 500


def update_button_order():
    try:
        order_data = request.form.getlist('order[]')
        for index, button_id in enumerate(order_data):
            print(button_id, index)
            button = Button.query.order_by(Button.sort_order).get(button_id)
            print(button)
            button.sort_order = index
        db.session.commit()
        app.display_toast(success=True, message="Ordre mis à jour")
        return '', 200  # Réponse sans contenu
    except Exception as e:
        app.display_toast(success=False, message=f"Erreur: {e}")


# affiche le formulaire pour ajouter un membre
def add_button_form():
    activities = Activity.query.all()
    parent_buttons = Button.query.filter_by(is_parent=True).all()
    return render_template('/admin/patient_button_add_form.html', 
                            activities=activities,
                            parent_buttons=parent_buttons)

def add_new_button():
    try:
        activity_id = request.form.get('activity')
        
        # GEstion du cas ou le bouton est un bouton parent
        if activity_id == "parent_button":
            is_parent = True
            activity = None
        else:
            is_parent = False
            if activity_id:
                activity = Activity.query.get(activity_id)
                if activity:
                    activity = activity
                else:
                    print("Activité non trouvée")
                    return "Activité non trouvée", 404
            else:
                # Si aucun ID d'activité n'est fourni, on peut décider de mettre l'attribut à None
                activity = None
                
        parent_btn_id = request.form.get('parent_btn')
        if parent_btn_id:
            parent_button = Button.query.get(parent_btn_id)
        else:
            parent_button = None
                
        is_present = True if request.form.get('is_present') == "true" else False
        
        label = request.form.get('label')

        shape = request.form.get('shape')
        
        # Trouve l'ordre le plus élevé et ajoute 1, sinon commence à 0 si aucun bouton n'existe
        max_order_button = Button.query.order_by(Button.sort_order.desc()).first()
        sort_order = max_order_button.sort_order + 1 if max_order_button else 0
        

        new_button = Button(
            is_parent=is_parent,
            activity=activity,
            label=label,
            shape=shape,
            parent_button=parent_button,
            is_present=is_present,
            sort_order=sort_order
        )
        
        if not label:  # Vérifiez que les champs obligatoires sont remplis
            app.display_toast(success=False, message="Le nom est obligatoire")
            return display_button_table()        

        db.session.add(new_button)
        db.session.commit()

        app.display_toast(success=True, message="Bouton ajouté")
        app.communikation("admin", event="refresh_button_order")

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_button_form"></div>"""

        return f"{display_button_table()}{clear_form_html}"


    except Exception as e:
        db.session.rollback()
        app.display_toast(success=False, message="erreur : " + str(e))
        app.logger.error(e)
        return display_button_table()


# affiche la modale pour confirmer la suppression d'un patient
def confirm_delete_button(button_id):
    button = Button.query.get(button_id)
    return render_template('/admin/patient_page_button_modal_confirm_delete.html', button=button)


# supprime un bouton
def delete_button(button_id):
    try:
        button = Button.query.order_by(Button.sort_order).get(button_id)
        if not button:
            app.display_toast(success=False, message="Bouton non trouvé")
            return display_button_table()

        db.session.delete(button)
        db.session.commit()
        app.display_toast(success=True, message="Bouton supprimé")

        app.communikation("admin", event="refresh_button_order")

        return display_button_table()

    except Exception as e:
        db.session.rollback()
        app.display_toast(success=False, message="erreur : " + str(e))
        app.logger.error(e)
        return display_button_table()


def upload_image(button_id):
    """ Pas réussi à faire sans rechargement de page, car problème pour passer image sans formulaire """
    button = Button.query.order_by(Button.sort_order).get(button_id)
    if 'file' not in request.files:
        return "No file part", 400
    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400
    if file and allowed_image_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.static_folder, 'images/buttons',  filename)
        file.save(file_path)
        button.image_url = filename
        db.session.commit()
        # Retour à la page admin/patient
        return redirect("/admin/patient", code=302)
    return "Invalid file", 400

# Permet de définir le type de fichiers autorisés pour l'ajout d'images
def allowed_image_file(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]

def gallery_button_images(button_id):
    directory = os.path.join(app.static_folder, 'images/buttons')
    images = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
    button = Button.query.order_by(Button.sort_order).get(button_id)
    print(images)
    return render_template('/admin/patient_page_button_modal_gallery.html', images=images, button=button)


def update_button_image_from_gallery():
    button_id = request.form.get('button_id')
    image_url = request.form.get('image')
    button = Button.query.order_by(Button.sort_order).get(button_id)
    print(request.form)
    button.image_url = image_url
    db.session.commit()
    return """<img src="{{ url_for('static', filename='images/buttons/' ~ button.image_url) }}" alt="Button Image" style="width: 100px;">"""


def delete_button_image(button_id):
    button = Button.query.order_by(Button.sort_order).get(button_id)
    button.image_url = None
    db.session.commit()
    return "<div>Pas d'image</div>"


def print_ticket_test():
    text = "12345678901234567890123456789012345678901234567890"
    print(text)
    app.communikation(stream="app_patient", data=text, flag="print")
    return "", 204
