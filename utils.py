import re
import pytz
import base64
from datetime import datetime, date
from flask import session, current_app as app
from models import Button, Translation, db
from communication import send_app_notification

def validate_and_transform_text(user_input, allowed_letters):
    """ Vérification et transformation des entrées avec des lettres autorisées spécifiques"""
    # Convertir les lettres spécifiées en minuscules en majuscules
    pattern = r"\{(" + "|".join(allowed_letters.lower()) + r")\}"
    corrected_input = re.sub(pattern, lambda m: "{" + m.group(1).upper() + "}", user_input)

    # Vérifier si tous les placeholders sont parmi les lettres autorisées après correction
    allowed_pattern = "[" + "".join(allowed_letters) + "]"
    if re.search(r"\{[^" + allowed_pattern + "]?\}", corrected_input) or re.search(r"\{" + allowed_pattern + "[^}]", corrected_input):
        return {"success": False, "value": f"Certaines balises sont incorrectes. Vous ne pouvez utiliser que {', '.join(['{' + letter + '}' for letter in allowed_letters])}."}
    
    print("corrected_input", corrected_input)
    return {"success": True, "value": corrected_input}


def parse_time(time_str):
    """ Transforme une heure au format 'HH:MM' ou 'HH:MM:SS' en objet 'time' Python. """
    if time_str:
        if len(time_str.split(':')) == 2:  # Format HH:MM
            return datetime.strptime(time_str, '%H:%M').time()
        elif len(time_str.split(':')) == 3:  # Format HH:MM:SS
            return datetime.strptime(time_str, '%H:%M:%S').time()
    return None

def word_wrap(text, line_width):
    """
    Enveloppe le texte à la largeur de ligne spécifiée, sans couper les mots.
    """
    lines = []
    paragraphs = text.split('\n')  # Diviser le texte en paragraphes

    for paragraph in paragraphs:
        words = paragraph.split(' ')
        current_line = ''
        for word in words:
            # Vérifier si le mot dépasse la largeur de ligne
            if len(current_line + ' ' + word) > line_width:
                lines.append(current_line)
                current_line = word
            else:
                if current_line:
                    current_line += ' ' + word
                else:
                    current_line = word
        if current_line:
            lines.append(current_line)
    return '\n'.join(lines)

def convert_markdown_to_escpos(markdown_text, line_width=42):
    # Commandes ESC/POS
    escpos_commands = {
        'center_on': '\x1b\x61\x01',
        'center_off': '\x1b\x61\x00',
        'double_size_on': '\x1d\x21\x11',
        'double_size_off': '\x1d\x21\x00',
        'bold_on': '\x1b\x45\x01',
        'bold_off': '\x1b\x45\x00',
        'underline_on': '\x1b\x2d\x01',
        'underline_off': '\x1b\x2d\x00',
        'separator': '-' * line_width + '\n',
    }

    # Motifs Markdown
    patterns = {
        'center': re.compile(r'\[center\](.*?)\[\/center\]', re.DOTALL),
        'double_size': re.compile(r'\[double\](.*?)\[\/double\]', re.DOTALL),
        'bold': re.compile(r'\*\*(.*?)\*\*', re.DOTALL),
        'underline': re.compile(r'__(.*?)__', re.DOTALL),
        'separator': re.compile(r'\[separator\]', re.DOTALL),
    }

    def replace_pattern(pattern, on_command, off_command, text, adjust_width=True):
        def wrap_and_format(match):
            inner_text = match.group(1)
            # Ajuster la largeur si en double taille
            width = line_width // 2 if adjust_width else line_width
            wrapped_text = word_wrap(inner_text, width)
            return f"{on_command}{wrapped_text}{off_command}"
        return pattern.sub(wrap_and_format, text)

    # Gérer les sauts de ligne explicitement
    escpos_text = markdown_text.replace('\\n', '\n')

    # Appliquer les transformations basées sur les motifs Markdown
    escpos_text = replace_pattern(patterns['center'], escpos_commands['center_on'], escpos_commands['center_off'], escpos_text, adjust_width=False)
    escpos_text = replace_pattern(patterns['double_size'], escpos_commands['double_size_on'], escpos_commands['double_size_off'], escpos_text)
    escpos_text = replace_pattern(patterns['bold'], escpos_commands['bold_on'], escpos_commands['bold_off'], escpos_text, adjust_width=False)
    escpos_text = replace_pattern(patterns['underline'], escpos_commands['underline_on'], escpos_commands['underline_off'], escpos_text, adjust_width=False)
    escpos_text = patterns['separator'].sub(escpos_commands['separator'], escpos_text)

    # Appliquer le retour à la ligne au texte brut restant
    # Nous devons faire attention à ne pas altérer les commandes ESC/POS insérées
    # Nous pouvons diviser le texte en parties, en conservant les commandes ESC/POS intactes

    # Expression régulière pour séparer le texte en gardant les commandes ESC/POS
    split_pattern = re.compile('(\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x1b]*\x1b\\\\|\x1b.|[\x00-\x1F])')

    parts = split_pattern.split(escpos_text)
    wrapped_parts = []

    for part in parts:
        # Si la partie est une commande ESC/POS, on la laisse telle quelle
        if re.match(split_pattern, part):
            wrapped_parts.append(part)
        else:
            # Appliquer le retour à la ligne
            wrapped_text = word_wrap(part, line_width)
            wrapped_parts.append(wrapped_text)

    escpos_text = ''.join(wrapped_parts)

    return escpos_text

def replace_balise_announces(template, patient):
    """ Remplace les balises dans les textes d'annonces (texte et son)"""
    print(template)
    print("replace_balise_announces", template, patient)
    app.logger.info(patient.counter)
    app.logger.info(patient.counter.staff)
    try:
        if patient.counter.staff:
            return template.format(N=patient.call_number, C=patient.counter.name, M=patient.counter.staff.name)
        else:
            app.logger.error(f"Pas de Staff on counter : {patient} {patient.counter} {patient.counter.staff}")
            template = "Comptoir {C}: {N}"
            send_app_notification(origin="erreur", data="Erreur: Vous n'êtes pas rattaché au comptoir. Le patient est bien appelé. Signaler le problème.")
            return template.format(N=patient.call_number, C=patient.counter.name)
    except AttributeError as e:
        app.logger.error(f"Failed to replace balise announces: {e}")
        return "Erreur"


def replace_balise_phone(template, patient):
    """ Remplace les balises dans les textes d'annonces (texte et son)
    Pour le nom de l'activité, on reprend le nom du bouton pour plus de o"""    
    print("LANGUES8REPLACE", session.get('language_code'))
    print('template')
    button_label = ""
    if "{A}" in template:
        button = Button.query.filter_by(activity_id=patient.activity_id).first()
        if session.get('language_code') != "fr":        
            button_label = get_buttons_translation([button], session.get('language_code'))[0].label
            print("button_label", button_label)
        else:
            button_label = button.label
    return template.format(P=app.config["PHARMACY_NAME"],
                            N=patient.call_number, 
                            A=button_label, 
                            D=date.today().strftime("%d/%m/%y"),
                            H=datetime.now().strftime("%H:%M"))


def get_buttons_translation(buttons, language_code):
    for button in buttons:
            # Récupérer la traduction du label du bouton
            translation = Translation.query.filter_by(
                table_name='Button',
                row_id=button.id,
                language_code=language_code
            ).first()
            
            print("button", button)
            print("translation", translation)
            # Si une traduction existe, mettre à jour le label du bouton
            if translation:
                button.label = translation.translated_text
    print("buttons", buttons)
    return buttons


def get_activity_message_translation(activity, language_code):
    translation = Translation.query.filter_by(
        table_name='Activity',
        row_id=activity.id,
        language_code=language_code
    ).first()

    if translation:    
        return translation.translated_text
    else:
        return ""


def get_text_translation(key_name, language_code):
    print("key_name", key_name, "language_code", language_code)
    try:
        translation = db.session.query(Translation).filter_by(language_code=language_code, key_name=key_name).first().translated_text
        if translation == "":
            return {"success": False, "translation": app.config[key_name.upper()], "error": "Translation empty"}
        return {"success": True, "translation": translation, "error": None}
    except AttributeError:
        app.logger.error(f"Translation not found for key: {key_name}, language: {language_code}")
        return {"success": False, "translation": app.config[key_name.upper()], "error": "Translation not found"}

def choose_text_translation(key):
    language_code = session.get('language_code', 'fr')
    if language_code == "fr":
        text = app.config[key.upper()]
    else:
        text = get_text_translation(key, language_code)["translation"]
    return text


def format_ticket_text(new_patient, activity):
    print("ticket_text", new_patient)
    print(app.config['TICKET_DISPLAY_SPECIFIC_MESSAGE'])
    if session.get('language_code') != "fr":
        language_code = session.get('language_code')
        text_list = [
        get_text_translation("ticket_header", language_code)["translation"],
        get_text_translation('ticket_message',language_code)["translation"],
        get_text_translation("ticket_footer",language_code)["translation"]
        ]
        if app.config["TICKET_DISPLAY_SPECIFIC_MESSAGE"]:
            text_list.append(get_activity_message_translation(activity, language_code))
    else:
        text_list = [
            app.config['TICKET_HEADER_PRINTER'],
            app.config['TICKET_MESSAGE_PRINTER'],
            app.config['TICKET_FOOTER_PRINTER']
        ]
        if app.config["TICKET_DISPLAY_SPECIFIC_MESSAGE"]:
            text_list.append(activity.specific_message)
    print("text_list", text_list)
    combined_text = "\n".join(text_list)
    print("text_join", combined_text)
    combined_text = replace_balise_phone(combined_text, new_patient)
    formatted_text = convert_markdown_to_escpos(combined_text, line_width=app.config["PRINTER_WIDTH"])
    encoded_text = base64.b64encode(formatted_text.encode('utf-8')).decode('utf-8')
    print("encoded", encoded_text)
    return encoded_text


