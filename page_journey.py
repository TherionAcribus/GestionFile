"""Parcours patient de la borne, écran par écran, pour l'éditeur visuel.

La borne enchaîne des écrans dont l'apparition dépend de réglages déjà
existants (impression directe, QR code, bouton « Scanner », comportement en
cas d'échec d'impression…). Ce module les décrit de façon déclarative pour
que l'éditeur présente une frise « Accueil → … → Conclusion » : chaque étape
indique si elle est affichée, sautée ou affichée selon le cas, pourquoi, et
quels réglages la pilotent.

Une seule source de vérité : la définition est servie telle quelle au
navigateur (``public_journey``), qui l'évalue en direct sur le brouillon avec
le même petit langage de conditions que ``evaluate`` ci-dessous (miroir
testé côté Python). Module pur : aucune dépendance Flask/SQLAlchemy.

Textes : ``{fait}`` insère un fait serveur, ``{=clé}`` la valeur du
brouillon (ex. ``{=page_patient_end_timer}``). ``exit`` décrit, par règles
comme ``status``, quand et comment la borne quitte l'écran (affiché dans
l'éditeur au-dessus de l'aperçu, jamais sur la borne).

Conditions
    ``None``                    toujours vraie
    ``{"config": k, "is": v}``  valeur du brouillon égale à ``v`` (ou dans
                                ``v`` si c'est une liste)
    ``{"fact": nom}``           fait calculé côté serveur (ex. existence d'un
                                regroupement de boutons), vrai s'il est non vide
    ``{"all": [...]}`` / ``{"any": [...]}`` / ``{"not": cond}``

États d'une étape : ``shown`` (toujours affichée sur ce chemin),
``conditional`` (selon le choix du patient ou un incident), ``hidden``
(jamais avec ces réglages).
"""

from __future__ import annotations

import re
from copy import deepcopy

SHOWN = "shown"
CONDITIONAL = "conditional"
HIDDEN = "hidden"
STATES = (SHOWN, CONDITIONAL, HIDDEN)


def _is(key, value=True):
    return {"config": key, "is": value}


def _not(condition):
    return {"not": condition}


_DIRECT_PRINT = _is("page_patient_direct_print")
_QR = _is("page_patient_qrcode_display")
#: Le patient peut s'inscrire sans ticket : soit le scan du QR (adresse web)
#: l'inscrit depuis son téléphone, soit il touche « Scanner » sur la borne.
#: Les deux supposent l'écran de validation.
_SCAN_ROUTE = {"all": [
    _not(_DIRECT_PRINT),
    {"any": [_is("page_patient_display_button_scan"),
             {"all": [_QR, _is("page_patient_qrcode_web_page")]}]},
]}
_ASK = _is("page_patient_print_fail_behavior", "ask")
_CONCLUSION = _is("page_patient_display_conclusion")
_RETRY = _is("page_patient_print_fail_show_retry")
_STAFF = _is("page_patient_print_fail_show_staff")


#: Parcours de la borne patient. ``scenario`` renvoie aux scénarios d'aperçu
#: de l'adaptateur ; ``choices`` reprend des clés de contenu existantes (le
#: type et les bornes viennent du champ du composant) avec un libellé et une
#: aide propres au parcours ; ``invert`` présente une case cochée quand la
#: valeur vaut ``False`` (« Afficher cet écran » = pas d'impression directe).
#: ``toggle`` est l'interrupteur « Afficher » de l'étape elle-même (frise et
#: tête de l'inspecteur) ; ``fixed`` explique pourquoi une étape sans
#: interrupteur ne peut pas être masquée.
PATIENT_JOURNEY = {
    "title": "Parcours du patient",
    "intro": "Les écrans que voit le patient, dans l’ordre. Masquez une étape avec son interrupteur « Afficher », ou cliquez dessus pour la prévisualiser et régler son contenu.",
    "steps": [
        {
            "id": "home", "scenario": "home", "label": "Accueil", "icon": "bi-house-door",
            "role": "Premier écran : le patient touche le motif de sa visite.",
            "status": [{"state": SHOWN, "text": "Toujours affiché : c’est le point de départ."}],
            "fixed": "Obligatoire",
            "fixed_help": "Cet écran ne peut pas être masqué : c’est le point de départ du parcours.",
            "choices": [
                {"key": "page_patient_display_translations", "label": "Proposer le choix de la langue",
                 "help": "Des drapeaux permettent au patient de passer la borne dans sa langue."},
            ],
            "actions": [
                {"id": "choose_activity", "label": "Choisir une activité", "target": "validation",
                 "when": _not({"fact": "children_parents"})},
                {"id": "choose_direct", "label": "Choisir une activité directe", "target": "validation",
                 "when": {"fact": "children_parents"}},
                {"id": "choose_group", "label": "Ouvrir le groupe {children_parents}", "target": "children",
                 "when": {"fact": "children_parents"}},
                {"id": "inactive", "label": "Tester une activité indisponible", "target": "activity_inactive",
                 "when": _is("page_patient_disable_button"), "tone": "secondary"},
            ],
            "components": ["title", "buttons", "subtitle", "languages"],
            "branches_label": "Depuis l’accueil",
            "branches": [
                {
                    "id": "activity_inactive", "scenario": "activity_inactive",
                    "label": "Activité indisponible", "icon": "bi-slash-circle",
                    "role": "Message affiché quand le patient touche une activité momentanément fermée.",
                    "status": [
                        {"when": _not(_is("page_patient_disable_button")), "state": HIDDEN,
                         "text": "Jamais : les activités fermées sont retirées de l’accueil, le patient ne peut pas les toucher."},
                        {"state": CONDITIONAL,
                         "text": "Quand le patient touche une activité fermée, affichée grisée."},
                    ],
                    "toggle": {"key": "page_patient_disable_button",
                               "label": "Afficher les activités fermées (grisées) et ce message",
                               "help": "Touchées, elles expliquent pourquoi elles sont fermées. Décoché : elles disparaissent de l’accueil jusqu’à leur réouverture."},
                    "choices": [
                        {"key": "page_patient_timer_activity_inactive",
                         "label": "Durée d’affichage du message",
                         "enabled_when": _is("page_patient_disable_button")},
                    ],
                    "exit": [{"text": "Le message reste affiché {=page_patient_timer_activity_inactive} s, puis l’accueil revient."}],
                    "actions": [
                        {"id": "inactive_back", "label": "Revenir à l’accueil", "target": "home"},
                    ],
                    "components": ["activity_status"],
                },
            ],
        },
        {
            "id": "children", "scenario": "children", "label": "Sous-activités", "icon": "bi-diagram-3",
            "role": "Après un bouton qui regroupe plusieurs activités : le patient précise son choix.",
            "status": [
                {"when": {"fact": "children_parents"}, "state": CONDITIONAL,
                 "text": "Seulement après un bouton de regroupement : {children_parents}."},
                {"state": HIDDEN,
                 "text": "Jamais : aucun bouton ne regroupe de sous-activités."},
            ],
            "fixed": "Selon vos boutons",
            "fixed_help": "Cet écran apparaît seulement si un bouton regroupe des sous-activités : il se gère avec vos boutons, pas avec un interrupteur.",
            "note": "Les regroupements se créent dans le mode avancé : un bouton « parent » et ses boutons enfants.",
            "link": {"href": "/admin/patient/buttons", "label": "Gérer les boutons (mode avancé)"},
            "actions": [
                {"id": "choose_child", "label": "Choisir une sous-activité", "target": "validation"},
                {"id": "children_back", "label": "Revenir à l’accueil", "target": "home", "tone": "secondary"},
            ],
            "components": ["buttons", "back_button"],
        },
        {
            "id": "validation", "scenario": "validation", "label": "Validation", "icon": "bi-check2-square",
            "role": "Le patient confirme son choix, puis imprime son ticket, scanne le QR code ou annule.",
            "status": [
                {"when": _DIRECT_PRINT, "state": HIDDEN,
                 "text": "Sautée : le ticket s’imprime dès que le patient touche une activité."},
                {"state": SHOWN, "text": "Toujours affichée après le choix d’une activité."},
            ],
            "toggle": {"key": "page_patient_direct_print", "invert": True,
                       "label": "Afficher l’écran de validation",
                       "help": "Décoché : le ticket s’imprime dès que le patient touche une activité. Il ne peut alors ni annuler ni s’inscrire par QR code."},
            "choices": [
                {"key": "page_patient_qrcode_display",
                 "label": "Afficher un QR code à scanner avec le téléphone",
                 "help": "Le patient suit son tour sur son téléphone. Le QR code figure aussi sur l’écran de conclusion.",
                 "enabled_when": _not(_DIRECT_PRINT)},
                {"key": "page_patient_qrcode_web_page",
                 "label": "Le scan inscrit le patient sans ticket",
                 "help": "Le QR code ouvre une page de suivi qui valide l’inscription. Décoché : il contient seulement du texte (numéro, activité), sans inscription automatique.",
                 "enabled_when": {"all": [_not(_DIRECT_PRINT), _QR]}},
                {"key": "page_patient_display_button_scan",
                 "label": "Proposer le bouton « Scanner » (inscription sans ticket)",
                 "help": "Pour valider à la borne sans imprimer, une fois le QR code scanné.",
                 "enabled_when": _not(_DIRECT_PRINT)},
                {"key": "page_patient_display_scan_explanation",
                 "label": "Afficher les consignes de scan",
                 "enabled_when": {"all": [_not(_DIRECT_PRINT), _QR]}},
            ],
            "warnings": [
                {"when": {"all": [_not(_DIRECT_PRINT), _is("page_patient_display_button_scan"), _not(_QR)]},
                 "text": "Le bouton « Scanner » est proposé sans QR code : le patient n’a rien à scanner."},
            ],
            "actions": [
                {"id": "print", "label": "Imprimer le ticket", "target": "printing", "icon": "bi-printer"},
                {"id": "scan", "label": "S’inscrire sans ticket", "target": "conclusion",
                 "when": _SCAN_ROUTE, "icon": "bi-qr-code-scan"},
                {"id": "cancel", "label": "Annuler", "finish": "Parcours annulé par le patient.",
                 "tone": "secondary"},
            ],
            "components": ["buttons", "specific_message"],
        },
        {
            "id": "printing", "scenario": "printing", "label": "Impression", "icon": "bi-printer",
            "role": "Le ticket s’imprime ; la borne attend la confirmation de l’imprimante.",
            "status": [
                {"when": _SCAN_ROUTE, "state": CONDITIONAL,
                 "text": "Quand le patient choisit « Imprimer » ; s’il s’inscrit par le QR code, il ne passe pas par cet écran."},
                {"state": SHOWN, "text": "Toujours : chaque inscription imprime un ticket."},
            ],
            "fixed": "Obligatoire",
            "fixed_help": "Cet écran ne peut pas être masqué : il s’affiche pendant l’impression du ticket, le temps que l’imprimante confirme.",
            "exit": [{"text": "Affiché le temps que l’imprimante confirme, puis conclusion — ou écran d’incident si l’impression échoue."}],
            "actions": [
                {"id": "print_ok", "label": "Ticket imprimé", "target": "conclusion",
                 "icon": "bi-check-circle"},
                {"id": "print_failed", "label": "Déclencher un échec", "target": "print_error",
                 "tone": "danger", "icon": "bi-exclamation-triangle"},
                {"id": "print_uncertain", "label": "Aucune confirmation", "target": "print_uncertain",
                 "tone": "warning", "icon": "bi-question-circle"},
            ],
            "components": ["print_error"],
            "branches_label": "Pendant l’impression",
            "branches": [
                {
                    "id": "print_error", "scenario": "print_error",
                    "label": "Erreur d’impression", "icon": "bi-exclamation-triangle",
                    "role": "L’imprimante a signalé un échec (papier, câble…).",
                    "status": [{"state": CONDITIONAL, "text": "Seulement si l’impression échoue."}],
                    "fixed": "Toujours prévu en cas d’échec : le patient doit savoir s’il est inscrit.",
                    "choices": [
                        {"key": "page_patient_print_fail_behavior",
                         "help": "Demander : le patient choisit (réessayer, appeler le personnel). Conserver : il reste inscrit sans ticket et note son numéro. Annuler : l’inscription est annulée."},
                        {"key": "page_patient_print_message_timer",
                         "label": "Durée d’affichage du message",
                         "help": "Modes Conserver et Annuler : le temps de lire le message et de noter son numéro, avec un décompte affiché. Indépendant de l’écran de conclusion.",
                         "enabled_when": _not(_ASK)},
                        {"key": "page_patient_print_fail_show_retry", "label": "Proposer « Réessayer »",
                         "enabled_when": _ASK},
                        {"key": "page_patient_print_fail_show_staff",
                         "label": "Proposer « Appeler le personnel »",
                         "help": "Utile dans une grande officine : le comptoir reçoit une alerte. Dans une petite, l’équipe voit directement le patient. Réglage commun aux écrans « Erreur d’impression » (mode Demander) et « Impression incertaine »."},
                        {"key": "page_patient_print_fail_abandon_timer",
                         "label": "Retour à l’accueil si le patient ne choisit rien (0 = jamais)",
                         "help": "Gardez-le court (15 s conseillées) : le patient suivant ne doit pas toucher « Réessayer » et imprimer le ticket du précédent.",
                         "enabled_when": _ASK},
                        {"key": "page_patient_print_fail_timeout_action",
                         "label": "Si le patient ne choisit rien",
                         "help": "Délai écoulé ou bouton « Terminer ». « Conserver » : il a vu son numéro, il entre dans la file sans ticket et le comptoir est prévenu. « Annuler » : l’inscription en attente est annulée.",
                         "enabled_when": _ASK},
                    ],
                    "exit": [
                        {"when": {"all": [_ASK, _is("page_patient_print_fail_abandon_timer", 0)]},
                         "text": "Pas de retour automatique : l’écran attend un choix du patient."},
                        {"when": {"all": [_ASK, _is("page_patient_print_fail_timeout_action", "cancel")]},
                         "text": "Sans choix du patient, retour à l’accueil après {=page_patient_print_fail_abandon_timer} s ; l’inscription est annulée."},
                        {"when": _ASK,
                         "text": "Sans choix du patient, retour à l’accueil après {=page_patient_print_fail_abandon_timer} s ; il reste inscrit sans ticket et le comptoir est prévenu."},
                        {"when": _is("page_patient_print_fail_behavior", "keep"),
                         "text": "Retour à l’accueil après {=page_patient_print_message_timer} s (décompte affiché) ; le patient reste inscrit et sera appelé."},
                        {"text": "Retour à l’accueil après {=page_patient_print_message_timer} s (décompte affiché) ; l’inscription est annulée."},
                    ],
                    "warnings": [
                        # Seul « Retour » reste : le patient lit son numéro
                        # mais l'inscription en attente n'est jamais activée.
                        {"when": {"all": [_ASK, _not(_RETRY), _not(_STAFF),
                                          _is("page_patient_print_fail_timeout_action", "cancel")]},
                         "text": "Sans « Réessayer » ni « Appeler le personnel », le patient n’a qu’un bouton « Terminer » : il voit son numéro mais son inscription est annulée. Choisissez plutôt « Conserver » quand il ne choisit rien, ou le comportement « Conserver dans la file »."},
                        {"when": {"all": [_ASK, _is("page_patient_print_fail_abandon_timer", 0)]},
                         "text": "Sans délai, l’écran attend indéfiniment un choix : le patient suivant risque de toucher « Réessayer » et d’imprimer le ticket du précédent."},
                    ],
                    "actions": [
                        {"id": "retry", "label": "Réessayer", "target": "printing",
                         "when": {"all": [_ASK, _RETRY]}, "icon": "bi-arrow-repeat"},
                        {"id": "call_staff", "label": "Appeler le personnel", "target": "staff_called",
                         "when": {"all": [_ASK, _STAFF]}, "icon": "bi-person-raised-hand"},
                        {"id": "ask_back", "label": "Retour à l’accueil",
                         "finish": "Inscription abandonnée après l’échec d’impression.",
                         "when": {"all": [_ASK, _not(_RETRY), _not(_STAFF)]}, "tone": "secondary"},
                        {"id": "kept", "label": "Fin du message",
                         "finish": "Patient conservé dans la file sans ticket.",
                         "when": _is("page_patient_print_fail_behavior", "keep")},
                        {"id": "cancelled", "label": "Fin du message",
                         "finish": "Inscription annulée après l’échec d’impression.",
                         "when": _is("page_patient_print_fail_behavior", "cancel"), "tone": "secondary"},
                    ],
                    "components": ["print_error"],
                },
                {
                    "id": "print_uncertain", "scenario": "print_uncertain",
                    "label": "Impression incertaine", "icon": "bi-question-circle",
                    "role": "L’imprimante n’a pas confirmé : le patient indique s’il a reçu son ticket.",
                    "status": [{"state": CONDITIONAL, "text": "Seulement si l’imprimante ne peut pas confirmer l’impression."}],
                    "fixed": "Toujours prévu : sans réponse de l’imprimante, seul le patient sait si le ticket est sorti.",
                    "exit": [
                        {"when": _is("page_patient_print_uncertain_timeout_action", "staff"),
                         "text": "Sans réponse, retour à l’accueil après {=page_patient_print_uncertain_timer} s ; l’inscription reste en attente, le comptoir vérifie."},
                        {"text": "Sans réponse, retour à l’accueil après {=page_patient_print_uncertain_timer} s ; ticket considéré comme reçu, le patient entre dans la file."},
                    ],
                    "choices": [
                        {"key": "page_patient_print_uncertain_timer",
                         "label": "Délai laissé au patient pour répondre",
                         "help": "Beaucoup de patients repartent avec leur ticket sans toucher l’écran : passé ce délai, la borne revient à l’accueil. Gardez-le court (15 s conseillées) pour que le patient suivant ne trouve pas ces boutons et ne relance pas l’impression d’un ticket en double."},
                        {"key": "page_patient_print_uncertain_timeout_action",
                         "label": "Si le patient ne répond pas",
                         "help": "« Ticket reçu » : il entre dans la file (cas le plus probable) et le comptoir est prévenu. « Personnel » : l’inscription reste en attente puis expire ; le comptoir, alerté, vérifie."},
                        {"key": "page_patient_print_fail_show_staff",
                         "label": "Proposer « Appeler le personnel »",
                         "help": "Utile dans une grande officine : le comptoir reçoit une alerte. Dans une petite, l’équipe voit directement le patient. Réglage commun aux écrans « Erreur d’impression » (mode Demander) et « Impression incertaine »."},
                    ],
                    "actions": [
                        {"id": "ticket_received", "label": "J’ai récupéré mon ticket", "target": "conclusion",
                         "icon": "bi-check-circle"},
                        {"id": "ticket_missing", "label": "Je n’ai pas de ticket", "target": "print_error",
                         "tone": "danger", "icon": "bi-x-circle"},
                        {"id": "uncertain_staff", "label": "Appeler le personnel", "target": "staff_called",
                         "when": _STAFF, "icon": "bi-person-raised-hand"},
                        {"id": "timeout_received", "label": "Simuler l’expiration du délai",
                         "finish": "Délai expiré : ticket présumé reçu, patient conservé dans la file.",
                         "when": _is("page_patient_print_uncertain_timeout_action", "received"), "tone": "secondary"},
                        {"id": "timeout_staff", "label": "Simuler l’expiration du délai",
                         "finish": "Délai expiré : vérification laissée au personnel.",
                         "when": _is("page_patient_print_uncertain_timeout_action", "staff"), "tone": "secondary"},
                    ],
                    "components": ["print_error"],
                },
                {
                    "id": "staff_called", "scenario": "staff_called",
                    "label": "Personnel appelé", "icon": "bi-person-raised-hand",
                    "role": "Confirme au patient que l’équipe a été prévenue.",
                    "status": [
                        {"when": _not(_is("page_patient_print_fail_show_staff")), "state": HIDDEN,
                         "text": "Jamais : le bouton « Appeler le personnel » n’est pas proposé."},
                        {"state": CONDITIONAL,
                         "text": "Après « Appeler le personnel », proposé en cas d’impression en échec ou incertaine."},
                    ],
                    "exit": [{"text": "Retour à l’accueil après {=page_patient_print_message_timer} s (décompte affiché) ; le patient est dans la file."}],
                    "choices": [
                        {"key": "page_patient_print_message_timer",
                         "label": "Durée d’affichage du message",
                         "help": "Le temps de noter son numéro, avec un décompte affiché. Commune aux messages « Conserver » et « Annuler » de l’écran d’erreur."},
                    ],
                    "toggle": {"key": "page_patient_print_fail_show_staff",
                               "label": "Proposer « Appeler le personnel » en cas de problème d’impression",
                               "help": "Le personnel reçoit une alerte sur le comptoir, et le patient voit cet écran de confirmation."},
                    "actions": [
                        {"id": "staff_done", "label": "Terminer le parcours",
                         "finish": "Personnel prévenu ; le patient revient ensuite à l’accueil."},
                    ],
                    "components": ["print_error"],
                },
            ],
        },
        {
            "id": "conclusion", "scenario": "conclusion", "label": "Conclusion", "icon": "bi-flag",
            "role": "Confirme l’inscription et le numéro, puis revient à l’accueil.",
            "status": [
                {"when": _not(_CONCLUSION), "state": HIDDEN,
                 "text": "Sautée : la borne revient à l’accueil dès que le ticket est imprimé ou l’inscription par QR code validée."},
                {"state": SHOWN, "text": "Toujours affichée en fin de parcours."},
            ],
            "exit": [{"text": "Retour à l’accueil après {=page_patient_end_timer} s, ou dès que le patient touche le bouton de retour."}],
            "toggle": {"key": "page_patient_display_conclusion",
                       "label": "Afficher l’écran de conclusion",
                       "help": "Décoché : retour immédiat à l’accueil après une inscription réussie. Les messages d’incident d’impression (échec, ticket incertain, personnel prévenu) restent affichés."},
            "choices": [
                {"key": "page_patient_print_after_print",
                 "label": "Après un ticket imprimé : proposer de le réimprimer",
                 "enabled_when": _CONCLUSION},
                {"key": "page_patient_print_after_scan",
                 "label": "Après une inscription par QR code : proposer d’imprimer un ticket",
                 "enabled_when": {"all": [_CONCLUSION, _SCAN_ROUTE]}},
                {"key": "page_patient_qrcode_display", "label": "Afficher le QR code de suivi",
                 "help": "Même réglage que sur l’écran de validation.",
                 "enabled_when": _CONCLUSION},
                {"key": "page_patient_end_timer", "label": "Retour automatique à l’accueil après",
                 "enabled_when": _CONCLUSION},
            ],
            "actions": [
                {"id": "reprint", "label": "Tester une réimpression", "target": "printing",
                 "when": {"any": [_is("page_patient_print_after_print"), _is("page_patient_print_after_scan")]},
                 "tone": "secondary", "icon": "bi-printer"},
                {"id": "finish", "label": "Retour automatique à l’accueil",
                 "finish": "Inscription terminée avec succès.", "icon": "bi-flag"},
            ],
            "components": ["buttons", "specific_message"],
        },
    ],
}

JOURNEYS = {"patient": PATIENT_JOURNEY}

_PLACEHOLDER_RE = re.compile(r"\{(=?)([a-z_]+)\}")


def evaluate(condition, config, facts=None):
    """Évalue une condition du parcours (miroir de ``page_editor.js``)."""
    if condition is None:
        return True
    facts = facts or {}
    if "all" in condition:
        return all(evaluate(item, config, facts) for item in condition["all"])
    if "any" in condition:
        return any(evaluate(item, config, facts) for item in condition["any"])
    if "not" in condition:
        return not evaluate(condition["not"], config, facts)
    if "fact" in condition:
        return bool(facts.get(condition["fact"]))
    expected = condition.get("is", True)
    value = config.get(condition["config"])
    if isinstance(expected, list):
        return value in expected
    return value == expected


def _fill(text, facts, config=None):
    def replace(match):
        if match.group(1):
            value = (config or {}).get(match.group(2))
            return "" if value is None else str(value)
        value = facts.get(match.group(2))
        if isinstance(value, (list, tuple)):
            return ", ".join(f"« {item} »" for item in value)
        return "" if value is None else str(value)
    return _PLACEHOLDER_RE.sub(replace, text)


def step_status(step, config, facts=None):
    """``(état, explication)`` de la première règle vérifiée."""
    facts = facts or {}
    for rule in step["status"]:
        if evaluate(rule.get("when"), config, facts):
            return rule["state"], _fill(rule["text"], facts, config)
    return HIDDEN, ""


def step_exit(step, config, facts=None):
    """Comment la borne quitte l'écran (première règle ``exit`` vérifiée),
    ou ``None`` si l'étape n'en décrit pas."""
    for rule in step.get("exit", []):
        if evaluate(rule.get("when"), config, facts):
            return _fill(rule["text"], facts or {}, config)
    return None


def journey_overview(page, config, facts=None):
    """États de toutes les étapes et branches : ``{id: (état, texte)}``."""
    journey = JOURNEYS.get(page)
    if journey is None:
        return {}
    overview = {}
    for step in journey["steps"]:
        overview[step["id"]] = step_status(step, config, facts)
        for branch in step.get("branches", []):
            overview[branch["id"]] = step_status(branch, config, facts)
    return overview


def iter_steps(journey):
    for step in journey["steps"]:
        yield step
        yield from step.get("branches", [])


def public_journey(page, fields_by_key):
    """Définition servie au navigateur, choix complétés par les champs des
    composants (type, liste de valeurs, bornes, unité, libellé par défaut).
    ``None`` pour une page sans parcours."""
    journey = JOURNEYS.get(page)
    if journey is None:
        return None
    data = deepcopy(journey)
    for step in iter_steps(data):
        toggle = [step["toggle"]] if "toggle" in step else []
        for choice in step.get("choices", []) + toggle:
            field = fields_by_key[choice["key"]]
            for attribute in ("type", "choices", "min", "max", "unit"):
                if attribute in field:
                    choice.setdefault(attribute, deepcopy(field[attribute]))
            choice.setdefault("label", field["label"])
            if "help" not in choice and field.get("help"):
                choice["help"] = field["help"]
    return data
