import re
from datetime import datetime, date
from flask import current_app as app


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


def word_wrap(text, width):
    # Split text by spaces but keep spaces with words
    words = re.split(r'(\s+)', text)
    lines = []
    current_line = ""

    print("ENTREE", width, text)
    for word in words:
        print("MOT", word)
        if len(current_line) + len(word) <= width:
            print("LEN", len(current_line) + len(word))
            current_line += word
            print("CURRENT", current_line)
            print('LEN CURRENT', len(current_line))
        else:
            lines.append(current_line.strip())
            current_line = word

    if current_line:
        lines.append(current_line.strip())

    return "\n".join(lines)


def convert_markdown_to_escpos(markdown_text, line_width=42):
    # ESC/POS commands
    escpos_commands = {
        'center_on': '\x1b\x61\x01',
        'center_off': '\x1b\x61\x00',
        'double_size_on': '\x1d\x21\x11',
        'double_size_off': '\x1d\x21\x00',
        'bold_on': '\x1b\x45\x01',
        'bold_off': '\x1b\x45\x00',
        'underline_on': '\x1b\x2d\x01',
        'underline_off': '\x1b\x2d\x00',
        'separator': '--------------------------------\n',
    }

    # Markdown patterns
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
            width = line_width // 2 if adjust_width else line_width
            wrapped_text = word_wrap(inner_text, width)
            return f"{on_command}{wrapped_text}{off_command}"
        return pattern.sub(wrap_and_format, text)

    # Handling new lines explicitly
    escpos_text = markdown_text.replace('\\n', '\n')

    escpos_text = replace_pattern(patterns['center'], escpos_commands['center_on'], escpos_commands['center_off'], escpos_text, adjust_width=False)
    escpos_text = replace_pattern(patterns['double_size'], escpos_commands['double_size_on'], escpos_commands['double_size_off'], escpos_text)
    escpos_text = replace_pattern(patterns['bold'], escpos_commands['bold_on'], escpos_commands['bold_off'], escpos_text, adjust_width=False)
    escpos_text = replace_pattern(patterns['underline'], escpos_commands['underline_on'], escpos_commands['underline_off'], escpos_text, adjust_width=False)
    escpos_text = patterns['separator'].sub(escpos_commands['separator'], escpos_text)

    return escpos_text


def replace_balise_announces(template, patient):
    """ Remplace les balises dans les textes d'annonces (texte et son)"""
    print(template)
    print("replace_balise_announces", template, patient)
    try:
        if patient.counter.staff.name:
            return template.format(N=patient.call_number, C=patient.counter.name, M=patient.counter.staff.name)
        else:
            template = "Patient {N} : {C}"
            return template.format(N=patient.call_number, C=patient.counter.name)
    except AttributeError:
        return f"Erreur {patient.call_number}. Demandez à notre personnel"


def replace_balise_phone(template, patient):
    """ Remplace les balises dans les textes d'annonces (texte et son)"""
    print("replace_balise_announces", template, patient)
    return template.format(P=app.config["PHARMACY_NAME"],
                            N=patient.call_number, 
                            A=patient.activity.name, 
                            D=date.today().strftime("%d/%m/%y"),
                            H=datetime.now().strftime("%H:%M"))