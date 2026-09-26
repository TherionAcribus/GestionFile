import re
import base64
from datetime import datetime, date
from flask import session, has_request_context, current_app as app
from models import Button, Translation, db
from communication import send_app_notification
from params_registry import BALISE_LETTERS, get_spec

def validate_and_transform_text(user_input, allowed_letters):
    """ Vérification et transformation des entrées avec des lettres autorisées spécifiques"""
    # Convertir les lettres spécifiées en minuscules en majuscules
    pattern = r"\{(" + "|".join(allowed_letters.lower()) + r")\}"
    corrected_input = re.sub(pattern, lambda m: "{" + m.group(1).upper() + "}", user_input)

    # Vérifier si tous les placeholders sont parmi les lettres autorisées après correction
    allowed_pattern = "[" + "".join(allowed_letters) + "]"
    if re.search(r"\{[^" + allowed_pattern + r"]?\}", corrected_input) or re.search(r"\{" + allowed_pattern + "[^}]", corrected_input):
        return {"success": False, "value": f"Certaines balises sont incorrectes. Vous ne pouvez utiliser que {', '.join(['{' + letter + '}' for letter in allowed_letters])}."}
    
    app.logger.debug('corrected_input %s', corrected_input)
    return {"success": True, "value": corrected_input}


#: Balisages d'impression du ticket à paire obligatoire : un ouvrant sans
#: fermant (ou un marqueur impair) serait imprimé littéralement sur le ticket.
_TICKET_PAIRED_TAGS = (("[center]", "[/center]"), ("[double]", "[/double]"))
_TICKET_PAIRED_MARKERS = ("**", "__")


def validate_ticket_text(value):
    """Valide un texte de ticket avant enregistrement.

    Deux contrôles : les balises ``{X}`` (famille « ticket » : {P} {D} {H}
    {A} {N}, comme les boutons proposés par l'interface) et l'équilibre du
    balisage d'impression — ``[center]…[/center]``, ``[double]…[/double]``,
    ``**gras**``, ``__souligné__`` (``[separator]`` est autonome). Sans cela,
    une balise inconnue ou une mise en forme non fermée était enregistrée puis
    imprimée littéralement.
    """
    check = validate_and_transform_text(value, BALISE_LETTERS["ticket"])
    if not check["success"]:
        return check
    text = check["value"]
    for open_tag, close_tag in _TICKET_PAIRED_TAGS:
        if text.count(open_tag) != text.count(close_tag):
            return {"success": False, "value":
                    f"Balisage non fermé : chaque {open_tag} doit être fermé par {close_tag}."}
    for marker in _TICKET_PAIRED_MARKERS:
        if text.count(marker) % 2:
            return {"success": False, "value":
                    f"Balisage non fermé : {marker} doit aller par paire."}
    return {"success": True, "value": text}


_HOUR_PATTERN = re.compile(r"([01]?\d|2[0-3]):([0-5]?\d)")


def validate_hour(value):
    """Valide un horaire « HH:MM » (00:00–23:59) pour une tâche planifiée.

    Renvoie ``{"success": bool, "value": ...}`` — même contrat que
    ``validate_and_transform_text``. La valeur acceptée est normalisée
    (« 9:5 » → « 09:05 ») afin que la chaîne persistée alimente
    directement un ``<input type="time">`` et le découpage heure/minute
    des jobs cron. Tout le reste (« abc », « 25:99 », « 9h30 »…)
    est rejeté AVANT écriture : une tâche créée à partir d'une chaîne
    non analysable échouait silencieusement après enregistrement.
    """
    match = _HOUR_PATTERN.fullmatch(str(value or "").strip())
    if not match:
        return {"success": False,
                "value": "Format d'heure attendu : HH:MM (ex. 09:30)."}
    return {"success": True,
            "value": "{:02d}:{:02d}".format(int(match.group(1)), int(match.group(2)))}


def validate_int(value, positive=False):
    """Valide un entier pour une clé ``value_int``.

    Ces clés passaient auparavant par le cas implicite « texte inchangé » :
    « abc » était persisté dans une colonne INT puis rejeté à la lecture
    (``int(...)`` levait ``ValueError`` dans la tâche planifiée). La chaîne
    vide signifie « non renseigné » — ``column_values_for`` la traduit en
    ``NULL`` — et reste acceptée. ``positive=True`` exige >= 1 (durées de
    conservation : 0 rendrait la purge quotidienne destructrice).
    """
    value = str(value or "").strip()
    if value == "":
        return {"success": True, "value": ""}
    try:
        number = int(value, 10)
    except ValueError:
        return {"success": False, "value": "Un nombre entier est attendu."}
    if positive and number < 1:
        return {"success": False,
                "value": "Un entier strictement positif est attendu."}
    return {"success": True, "value": str(number)}


def validate_config_text(key, value):
    """Valide ``value`` pour la clé de configuration ``key`` selon le
    validateur déclaré dans le registre.

    Renvoie ``{"success": bool, "value": ...}`` — même contrat que
    ``validate_and_transform_text`` (value = texte corrigé ou message
    d'erreur). Utilisé par ``update_input`` et par la sauvegarde des
    traductions : un texte traduit doit respecter les mêmes balises que la
    source française. Les validateurs sans règle (« text », « bool », « int »)
    renvoient la valeur inchangée.
    """
    spec = get_spec(key)
    if spec is None:
        return {"success": True, "value": value}
    if spec.validator == "ticket":
        return validate_ticket_text(value)
    if spec.validator == "hour":
        return validate_hour(value)
    if spec.validator == "int":
        return validate_int(value)
    if spec.validator == "positive_int":
        return validate_int(value, positive=True)
    if spec.validator in BALISE_LETTERS:
        return validate_and_transform_text(value, BALISE_LETTERS[spec.validator])
    return {"success": True, "value": value}


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
    # Chaine BRUTE : la version non-brute contenait `\[` et `\]`, des séquences
    # d'échappement invalides que Python signale et qui deviendront des erreurs
    # de syntaxe. Le moteur `re` interprète lui-même `\x1b`, `\x00` et `\\` :
    # les deux écritures produisent donc exactement le même découpage (vérifié).
    split_pattern = re.compile(r'(\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x1b]*\x1b\\|\x1b.|[\x00-\x1F])')

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

# ---------------------------------------------------------------------------
# Moteur centralisé de rendu des balises
# ---------------------------------------------------------------------------
# Toutes les balises annoncées par l'administration sont prises en charge —
# union de params_registry.BALISE_LETTERS : « welcome » {P}{D}{H},
# « before_call » {P}{D}{H}{A}{N} et « after_call » {P}{D}{H}{A}{N}{M}{C}.
# Le rendu ne lève JAMAIS d'exception : il intervient pour certaines annonces
# APRÈS que le patient a été marqué 'calling', où un KeyError (str.format sans
# la clé) laissait l'appel sans bannière à l'écran ni annonce audio.

_BALISE_PATTERN = re.compile(r"\{([A-Za-z])\}")

# Sentinel : distingue « balise inconnue » (laissée telle quelle) de « balise
# connue mais sans valeur » (rendue vide).
_BALISE_MISSING = object()


def render_balises(template, values):
    """Remplace chaque balise ``{X}`` par ``values[X]``, sans jamais lever
    d'exception.

    Contrairement à ``str.format`` : une balise absente de ``values`` reste
    inchangée, une valeur ``None`` est rendue vide, et les autres accolades
    (``{0}``, ``{{``, ``{`` seule…) ne font pas échouer le rendu.
    """
    if not template:
        return template

    def _sub(match):
        value = values.get(match.group(1).upper(), _BALISE_MISSING)
        if value is _BALISE_MISSING:
            return match.group(0)
        return "" if value is None else str(value)

    return _BALISE_PATTERN.sub(_sub, str(template))


def _activity_label(patient, language_code=None):
    """Libellé pour ``{A}`` : le bouton associé à l'activité du patient
    (traduit si possible), à défaut le nom de l'activité.

    Repli silencieux : un libellé introuvable ne doit jamais casser un appel.
    """
    if patient is None:
        return ""
    if language_code is None:
        # En contexte de requête : la langue choisie sur la borne. Hors requête
        # (thread audio, planificateur) : la langue du patient.
        language_code = (
            session.get('language_code') if has_request_context()
            else getattr(getattr(patient, 'language', None), 'code', None)
        )
    label = ""
    try:
        button = Button.query.filter_by(activity_id=patient.activity_id).first()
        if button is not None:
            if language_code and language_code != "fr":
                label = get_button_translations([button], language_code).get(button.id) or button.label
            else:
                label = button.label
    except Exception as e:
        app.logger.warning("Balise {A} : libellé d'activité introuvable (%s)", e)
    if not label:
        label = getattr(getattr(patient, 'activity', None), 'name', '') or ''
    return label


def balise_values(patient=None, language_code=None, with_activity=False):
    """Valeurs de TOUTES les balises annoncées.

    {P} nom de la pharmacie, {D} date du jour, {H} heure, {N} numéro d'appel,
    {C} comptoir, {M} membre d'équipe, {A} activité. Chaque donnée
    indisponible (pas de patient, pas de comptoir, pas de membre) est rendue
    vide plutôt que de faire échouer le rendu.
    """
    counter = getattr(patient, 'counter', None)
    staff = getattr(counter, 'staff', None)
    return {
        "P": app.config.get("PHARMACY_NAME", "") or "",
        "D": date.today().strftime("%d/%m/%y"),
        "H": datetime.now().strftime("%H:%M"),
        "N": getattr(patient, 'call_number', '') or '',
        "C": getattr(counter, 'name', '') or '',
        "M": getattr(staff, 'name', '') or '',
        # {A} coûte une requête (bouton + traduction) : résolu à la demande.
        "A": _activity_label(patient, language_code) if with_activity else "",
    }


def replace_balises(template, patient=None, language_code=None):
    """Point d'entrée unique : rend TOUTES les balises d'un texte, pour un
    patient donné ou hors contexte patient (``patient=None``)."""
    return render_balises(
        template,
        balise_values(patient, language_code,
                      with_activity="{a}" in str(template or "").lower()),
    )


def replace_balise_announces(template, patient):
    """ Remplace les balises dans les textes d'annonces (texte et son).

    Sans membre d'équipe sur le comptoir, on dégrade le texte en
    « Comptoir {C}: {N} » et on alerte le personnel — le patient reste appelé.
    """
    app.logger.debug('replace_balise_announces %s %s', template, patient)
    counter = getattr(patient, 'counter', None)
    staff = getattr(counter, 'staff', None)
    if patient is None or staff is None:
        app.logger.error(f"Pas de Staff on counter : {patient} {counter} {staff}")
        send_app_notification(origin="erreur", data="Erreur: Vous n'êtes pas connecté au comptoir. Le patient est bien appelé. Signaler le problème.")
        template = "Comptoir {C}: {N}"
    return replace_balises(template, patient)


def replace_balise_phone(template, patient, language_code=None):
    """ Remplace les balises dans les textes « avant appel » (page patient,
    ticket, téléphone). Même moteur que les annonces : aucune balise ne peut
    lever d'exception. Pour le nom de l'activité ({A}), on reprend le libellé
    du bouton pour plus de cohérence avec ce que le patient a choisi."""
    return replace_balises(template, patient, language_code)


def replace_balise_welcome(template):
    """ Remplace les balises des textes « d'accueil » (sans patient) : {P} nom de
    la pharmacie, {D} date du jour, {H} heure ; les balises patient ({N} {A}
    {M} {C}) sont rendues vides. Utilisé pour le titre de la page patient et
    les textes hors appel de l'écran d'annonce.

    Remplacement ciblé (pas de ``str.format``) afin de ne PAS planter si le texte
    contient d'autres accolades ou une balise non gérée. """
    return replace_balises(template)


def get_button_translations(buttons, language_code):
    """Renvoie ``{button.id: libellé traduit}``, en UNE seule requête.

    Le modèle ``Button`` n'est pas modifié : écrire la traduction dans
    ``button.label`` marquait l'instance comme modifiée et un commit ultérieur
    pouvait remplacer le libellé source français par sa traduction.
    """
    if not buttons:
        return {}

    ids = [bouton.id for bouton in buttons]
    return {
        traduction.row_id: traduction.translated_text
        for traduction in Translation.query.filter(
            Translation.table_name == 'Button',
            Translation.column_name == 'label',
            Translation.language_code == language_code,
            Translation.row_id.in_(ids),
            Translation.translated_text != '',
        ).all()
    }


def get_activity_message_translation(activity, language_code):
    translation = Translation.query.filter_by(
        table_name='Activity',
        column_name='specific_message',
        row_id=activity.id,
        language_code=language_code
    ).first()

    if translation:    
        return translation.translated_text
    else:
        return ""


def get_activity_inactivity_message_translation(activity, language_code):
    """Traduction du message d'inactivité de l'activité.

    ``column_name`` est filtré explicitement : ``inactivity_message`` et
    ``specific_message`` partagent les mêmes table_name/row_id — un
    ``.first()`` non filtré pourrait renvoyer l'autre texte."""
    translation = Translation.query.filter_by(
        table_name='Activity',
        column_name='inactivity_message',
        row_id=activity.id,
        language_code=language_code
    ).first()
    return translation.translated_text if translation else ""


def get_text_translation(key_name, language_code):
    app.logger.debug('key_name %s %s %s', key_name, "language_code", language_code)
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


def render_ticket_escpos(text_list, new_patient, line_width, language_code=None):
    """Rend un ticket en texte ESC/POS, sans l'encoder en base64.

    Ce point d'entree commun est utilise par l'impression physique et par
    l'apercu Admin. Les deux chemins partagent ainsi exactement le remplacement
    des balises, l'enveloppe et les commandes de mise en forme.
    """
    combined_text = "\n".join("" if text is None else str(text)
                              for text in text_list)
    combined_text = replace_balise_phone(
        combined_text, new_patient, language_code=language_code)
    return convert_markdown_to_escpos(combined_text, line_width=line_width)


_ESCPOS_PREVIEW_COMMANDS = {
    '\x1b\x61\x01': ("alignment", "center"),
    '\x1b\x61\x00': ("alignment", "left"),
    '\x1d\x21\x11': ("double", True),
    '\x1d\x21\x00': ("double", False),
    '\x1b\x45\x01': ("bold", True),
    '\x1b\x45\x00': ("bold", False),
    '\x1b\x2d\x01': ("underline", True),
    '\x1b\x2d\x00': ("underline", False),
}


def escpos_to_preview_lines(escpos_text):
    """Transforme le flux ESC/POS produit par ce module en lignes affichables.

    Le resultat ne contient que du texte et des booleens/classes connues. Le
    gabarit Jinja echappe ensuite le texte : aucune saisie administrateur n'est
    interpretee comme du HTML.
    """
    state = {
        "alignment": "left",
        "double": False,
        "bold": False,
        "underline": False,
    }
    lines = []
    segments = []
    buffer = []
    segment_state = None
    line_alignment = None

    def style_key():
        return (state["double"], state["bold"], state["underline"])

    def flush_segment():
        nonlocal buffer, segment_state
        if not buffer:
            return
        double, bold, underline = segment_state
        segments.append({
            "text": "".join(buffer),
            "double": double,
            "bold": bold,
            "underline": underline,
        })
        buffer = []

    def flush_line():
        nonlocal segments, line_alignment
        flush_segment()
        lines.append({
            "alignment": line_alignment or state["alignment"],
            "segments": segments,
            "double": any(segment["double"] for segment in segments),
        })
        segments = []
        line_alignment = None

    text = str(escpos_text or "")
    index = 0
    while index < len(text):
        command = next(
            (candidate for candidate in _ESCPOS_PREVIEW_COMMANDS
             if text.startswith(candidate, index)),
            None,
        )
        if command is not None:
            flush_segment()
            attribute, value = _ESCPOS_PREVIEW_COMMANDS[command]
            state[attribute] = value
            index += len(command)
            continue

        char = text[index]
        index += 1
        if char == "\n":
            flush_line()
            continue
        if char == "\r":
            continue
        if char == "\t":
            char = "    "

        current_style = style_key()
        if segment_state != current_style:
            flush_segment()
            segment_state = current_style
        if line_alignment is None:
            line_alignment = state["alignment"]
        buffer.append(char)

    if buffer or segments or not lines:
        flush_line()
    return lines


def format_ticket_text(new_patient, activity):
    app.logger.debug('ticket_text %s', new_patient)
    app.logger.debug("%s", app.config['TICKET_DISPLAY_SPECIFIC_MESSAGE'])
    language_code = session.get('language_code', 'fr') or 'fr'
    if language_code != "fr":
        text_list = [
        get_text_translation("ticket_header", language_code)["translation"],
        get_text_translation('ticket_message',language_code)["translation"],
        get_text_translation("ticket_footer",language_code)["translation"]
        ]
        if app.config["TICKET_DISPLAY_SPECIFIC_MESSAGE"]:
            text_list.append(get_activity_message_translation(activity, language_code))
    else:
        # Source Markdown brute : la conversion ESC/POS est faite plus bas, au
        # moment de l'impression, avec la largeur courante (PRINTER_WIDTH).
        # Ne PAS lire les clés TICKET_*_PRINTER : c'étaient des versions
        # préformatées à 42 caractères figées à l'enregistrement (supprimées).
        text_list = [
            app.config['TICKET_HEADER'],
            app.config['TICKET_MESSAGE'],
            app.config['TICKET_FOOTER']
        ]
        if app.config["TICKET_DISPLAY_SPECIFIC_MESSAGE"]:
            text_list.append(activity.specific_message)
            
    formatted_text = render_ticket_escpos(
        text_list,
        new_patient,
        line_width=app.config["PRINTER_WIDTH"],
        language_code=language_code,
    )
    encoded_text = base64.b64encode(formatted_text.encode('utf-8')).decode('utf-8')
    return encoded_text
