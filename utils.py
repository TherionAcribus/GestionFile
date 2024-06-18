import re
from datetime import datetime


def validate_and_transform_text(user_input):
    """ Verif et conversion des entrées pour les annonces"""
    # Convertir les minuscules {n}, {c}, {m} en majuscules {N}, {C}, {M}
    corrected_input = re.sub(r"\{(n|c|m)\}", lambda m: "{" + m.group(1).upper() + "}", user_input)

    # Vérifier si tous les placeholders sont {P}, {C}, {M} après correction
    # Cette regex recherche toute chaîne qui ne suit pas exactement le modèle autorisé
    if re.search(r"\{[^NCM]?\}", corrected_input) or re.search(r"\{[NCM][^}]", corrected_input):
        return {"success": False, "value": "Certaines balises sont incorrectes. Vous ne pouvez utiliser que {P}, {C} ou {M}."}
    
    print("corrected_input", corrected_input)
    return {"success": True, "value": corrected_input}


def validate_and_transform_text_for_phone(user_input):
    """ Verif et conversion des entrées pour les annonces"""
    # Convertir les minuscules {p}, {a} en majuscules {P}, {A}, {H}, {D}, {N}
    corrected_input = re.sub(r"\{(p|a|h|d|n)\}", lambda m: "{" + m.group(1).upper() + "}", user_input)

    # Vérifier si tous les placeholders sont {P}, {A}, {H}, {D}, {N} après correction
    # Cette regex recherche toute chaîne qui ne suit pas exactement le modèle autorisé
    if re.search(r"\{[^PAHDN]?\}", corrected_input) or re.search(r"\{[PAHDN][^}]", corrected_input):
        return {"success": False, "value": "Certaines balises sont incorrectes. Vous ne pouvez utiliser que {P}, {A}, {H}, {D} ou {N}."}
    
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

    def replace_pattern(pattern, on_command, off_command, text):
        def wrap_and_format(match):
            inner_text = match.group(1)
            wrapped_text = word_wrap(inner_text, line_width // 2)  # Adjust width for double size
            return f"{on_command}{wrapped_text}{off_command}"
        return pattern.sub(wrap_and_format, text)

    escpos_text = markdown_text
    escpos_text = replace_pattern(patterns['center'], escpos_commands['center_on'], escpos_commands['center_off'], escpos_text)
    escpos_text = replace_pattern(patterns['double_size'], escpos_commands['double_size_on'], escpos_commands['double_size_off'], escpos_text)
    escpos_text = replace_pattern(patterns['bold'], escpos_commands['bold_on'], escpos_commands['bold_off'], escpos_text)
    escpos_text = replace_pattern(patterns['underline'], escpos_commands['underline_on'], escpos_commands['underline_off'], escpos_text)
    escpos_text = patterns['separator'].sub(escpos_commands['separator'], escpos_text)

    return escpos_text

