"""Calcul des numéros d'appel — cœur pur (aucune dépendance Flask/SQLAlchemy).

Rappel du contexte : ``Patient.call_number`` est une colonne **texte**
(``db.String(10)``), parce que la numérotation « par activité » produit des
numéros du type ``"A-3"``. En numérotation « simple » (``NUMBERING_BY_ACTIVITY``
désactivé) les numéros sont purement numériques, mais ils restent **stockés et
relus sous forme de chaîne**.

D'où la règle portée par ce module : on ne fait jamais d'arithmétique
directement sur un ``call_number`` relu en base ; on le convertit d'abord, et
on renvoie toujours une **chaîne**, comme la colonne.
"""


def next_simple_call_number(last_call_number):
    """Numéro d'appel suivant, en numérotation simple.

    ``last_call_number`` est le numéro du dernier patient enregistré
    aujourd'hui, tel qu'il sort de la base (une chaîne), ou ``None`` s'il n'y a
    aucun patient aujourd'hui.

    Le compteur repart à ``"1"`` si le dernier numéro n'est pas purement
    numérique : c'est le cas des numéros par activité (``"A-3"``), donc après
    un passage de la numérotation « par activité » à « simple » dans la même
    journée (limitation connue, cf. appelant).

    Retourne toujours une chaîne (type de ``Patient.call_number``).
    """
    if last_call_number is None:
        return "1"

    text = str(last_call_number).strip()
    # ``isascii`` en plus de ``isdigit`` : ce dernier accepte aussi les chiffres
    # exotiques ("²", chiffres arabes-indiens…) que ``int`` refuserait.
    if not (text.isascii() and text.isdigit()):
        return "1"

    return str(int(text) + 1)


def next_category_call_number(letter, todays_call_numbers):
    """Numéro suivant en numérotation par activité (``"A-4"``).

    ``todays_call_numbers`` : numéros d'appel déjà attribués aujourd'hui (tous
    préfixes confondus, chaînes). On prend le PLUS GRAND numéro de la lettre,
    et non leur nombre : l'ancien calcul (``count() + 1``) redonnait un numéro
    déjà distribué dès qu'un patient était retiré de la file (A-1, A-2, A-3,
    retrait de A-2 → nouveau patient « A-3 », en double).

    Les activités qui partagent une lettre partagent la même série.
    """
    prefix = f"{letter}-"
    highest = 0
    for number in todays_call_numbers or ():
        text = str(number or "").strip()
        if not text.startswith(prefix):
            continue
        tail = text[len(prefix):]
        if tail.isascii() and tail.isdigit():
            highest = max(highest, int(tail))
    return f"{letter}-{highest + 1}"
