import re
from datetime import datetime


def validate_and_transform_text(user_input):
    """ Verif et conversion des entrées pour les annonces"""
    # Convertir les minuscules {p}, {c}, {m} en majuscules {P}, {C}, {M}
    corrected_input = re.sub(r"\{(p|c|m)\}", lambda m: "{" + m.group(1).upper() + "}", user_input)

    # Vérifier si tous les placeholders sont {P}, {C}, {M} après correction
    # Cette regex recherche toute chaîne qui ne suit pas exactement le modèle autorisé
    if re.search(r"\{[^PCM]?\}", corrected_input) or re.search(r"\{[PCM][^}]", corrected_input):
        return {"success": False, "value": "Certaines balises sont incorrectes. Vous ne pouvez utiliser que {P}, {C} ou {M}."}
    
    print("corrected_input", corrected_input)
    return {"success": True, "value": corrected_input}


def validate_and_transform_text_for_phone(user_input):
    """ Verif et conversion des entrées pour les annonces"""
    # Convertir les minuscules {p}, {a} en majuscules {P}, {A}
    corrected_input = re.sub(r"\{(p|a)\}", lambda m: "{" + m.group(1).upper() + "}", user_input)

    # Vérifier si tous les placeholders sont {P}, {C}, {M} après correction
    # Cette regex recherche toute chaîne qui ne suit pas exactement le modèle autorisé
    if re.search(r"\{[^PA]?\}", corrected_input) or re.search(r"\{[PA][^}]", corrected_input):
        return {"success": False, "value": "Certaines balises sont incorrectes. Vous ne pouvez utiliser que {P}, {C} ou {M}."}
    
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


def convert_markdown_to_escpos(markdown_text):
    # ESC/POS commands
    escpos_commands = {
        'center_on': b'\x1b\x61\x01',
        'center_off': b'\x1b\x61\x00',
        'double_size_on': b'\x1d\x21\x11',
        'double_size_off': b'\x1d\x21\x00',
        'bold_on': b'\x1b\x45\x01',
        'bold_off': b'\x1b\x45\x00',
        'underline_on': b'\x1b\x2d\x01',
        'underline_off': b'\x1b\x2d\x00',
        'separator': b'--------------------------------\n',
    }

    # Markdown patterns
    patterns = {
        'center': re.compile(r'\[center\](.*?)\[\/center\]', re.DOTALL),
        'double_size': re.compile(r'\[double\](.*?)\[\/double\]', re.DOTALL),
        'bold': re.compile(r'\*\*(.*?)\*\*', re.DOTALL),
        'underline': re.compile(r'__(.*?)__', re.DOTALL),
        'separator': re.compile(r'\[separator\]', re.DOTALL),
    }

    # Convert markdown to ESC/POS commands
    def replace_pattern(pattern, on_command, off_command, text):
        return pattern.sub(lambda m: on_command.decode() + m.group(1) + off_command.decode(), text)

    escpos_text = markdown_text
    escpos_text = replace_pattern(patterns['center'], escpos_commands['center_on'], escpos_commands['center_off'], escpos_text)
    escpos_text = replace_pattern(patterns['double_size'], escpos_commands['double_size_on'], escpos_commands['double_size_off'], escpos_text)
    escpos_text = replace_pattern(patterns['bold'], escpos_commands['bold_on'], escpos_commands['bold_off'], escpos_text)
    escpos_text = replace_pattern(patterns['underline'], escpos_commands['underline_on'], escpos_commands['underline_off'], escpos_text)
    escpos_text = patterns['separator'].sub(escpos_commands['separator'].decode(), escpos_text)

    return escpos_text

    # Convert the final text to bytes
    escpos_bytes = escpos_text.encode('utf-8')

    # Replace placeholder spaces with actual commands
    escpos_bytes = escpos_bytes.replace(b' \x1b', b'\x1b').replace(b' \x1d', b'\x1d').replace(b' \n', b'\n')
    
    return escpos_bytes
