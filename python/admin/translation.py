from flask import render_template, request, jsonify, current_app as app
from models import Text, TextTranslation, Language, db

def admin_translation():    
    return render_template('/admin/translations.html',
                            )

def display_languages_table():
    languages = Language.query.all()
    print("Llanguages", languages)
    return render_template('admin/translations_languages_htmx_table.html', languages=languages)

# affiche la table de l'équipe
def display_text_translations():
    texts = Text.query.all()
    translations = TextTranslation.query.all()
    return render_template('admin/staff_htmx_table.html', texts=texts, translations=translations)

def update_language(language_id):
    try:
        language = Language.query.get(language_id)
        if language:
            code =  request.form.get('code', language.code)
            name = request.form.get('name', language.name)
            translation = request.form.get('translation', language.translation)
            if code == '':
                app.display_toast(success=False, message="Le code est obligatoire")
                return "", 204
            if name == '':
                app.display_toast(success=False, message="Le nom est obligatoire")
                return "", 204
            if translation == '':
                app.display_toast(success=False, message="La traduction est obligatoire")
                return "", 204

            # Vérifie que le code ne sont pas déjà enregistrées par une autre langue
            existing_language = db.session.query(Language).filter(
                Language.code == code,
                Language.id != language_id  # Exclure le membre actuel
            ).first()

            if existing_language:
                app.display_toast(success=False, message="Le code est déjà utilisé par une autre langue")
                return "", 204

            language.code = code
            language.name = name
            language.translation = translation

            db.session.commit()
            app.display_toast(success=True, message="Mise à jour réussie")
            return ""
        else:
            app.display_toast(success=False, message="Langue introuvable")
            return ""

    except Exception as e:
        app.display_toast(success=False, message="Erreur : " + str(e))
        return jsonify(status="error", message=str(e)), 500


def confirm_delete_language(language_id):
    language = Language.query.get(language_id)
    return render_template('/admin/translations_languages_modal_confirm_delete copy.html', language=language)


# supprime un membre de l'equipe
def delete_language(language_id):
    try:
        language = Language.query.get(language_id)
        if not language:
            app.display_toast(success=False, message="Langue non trouvée")
            return display_languages_table()

        db.session.delete(language)
        db.session.commit()
        app.display_toast(success=True, message="Suppression réussie")
        return display_languages_table()

    except Exception as e:
        app.display_toast(success=False, message="Erreur : " + str(e))
        return display_languages_table()
    

# affiche le formulaire pour ajouter un membre
def add_language_form():
    return render_template('/admin/translations_language_add_form.html')

# enregistre le membre dans la Bdd
def add_new_language():
    try:
        code = request.form.get('code')
        name = request.form.get('name')
        translation = request.form.get('translation')

        if not code:  # Vérifiez que les champs obligatoires sont remplis
            app.display_toast(success=False, message="Code obligatoire")
            return display_languages_table()
        if code in [code[0] for code in db.session.query(Language.code).all()]:
            app.display_toast(success=False, message="Le code est déjà utilisées")
            return "", 204
        if not name:  # Vérifiez que les champs obligatoires sont remplis
            app.display_toast(success=False, message="Nom obligatoires")
            return display_languages_table()
        if not translation:  # Vérifiez que les champs obligatoires sont remplis
            app.display_toast(success=False, message="Traduction obligatoire")
            return display_languages_table()

        new_language = Language(
            code=code,
            translation=translation,
            name=name,
        )
        db.session.add(new_language)
        db.session.commit()

        app.display_toast(success=True, message="Langue ajoutée avec succès")

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_language_form"></div>"""

        return f"{display_languages_table()}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        app.display_toast(success=False, message= "Erreur : " + str(e))
        return display_languages_table()