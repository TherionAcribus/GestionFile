"""Bilan de fiabilite des donnees en fin de test de charge (noyau pur).

Le test de charge mesure si le serveur repond et a quelle vitesse. Ce module
repond a une autre question : les ecritures que le serveur a *confirmees*
sont-elles reellement en base a la fin du test ?

Pendant le test, le runner note dans un ``JourneyLedger`` chaque patient de
test dont la creation a ete acquittee (HTTP 201) et le dernier etat confirme
(HTTP 200) pour ce patient. A la fin, ``reconcile`` compare ce registre aux
lignes relues en base (prefixe ``journey_id`` du test) et classe les ecarts :

* ``missing``  : patient confirme cree mais absent de la base -> perte ;
* ``regressed``: patient en base dans un etat anterieur (ou inconnu) a celui
  que le serveur avait confirme -> perte de mise a jour ;
* ``ahead``    : patient en base dans un etat *plus avance* que le dernier
  acquittement -> le serveur a applique une action dont la reponse n'est
  jamais arrivee (delai depasse, arret du test). Ambigu, pas une perte ;
* ``unacknowledged`` : patient en base que le runner ne connait pas -> une
  creation appliquee sans reponse recue. Ambigu, pas une perte.

Un acquittement n'est envoye qu'apres le commit : un ecart ``missing`` ou
``regressed`` ne peut donc pas venir d'un simple decalage temporel.
"""
from __future__ import annotations

JOURNEY_ORDER = ('standing', 'calling', 'ongoing', 'done')
_RANK = {status: index for index, status in enumerate(JOURNEY_ORDER)}
SAMPLE_LIMIT = 10


class JourneyLedger:
    """Ce que le serveur a confirme au runner, patient par patient.

    Les utilisateurs Locust sont des greenlets d'un meme processus : les
    operations sur le dictionnaire n'ont pas besoin de verrou.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.acknowledged: dict[int, str] = {}
        self.create_failures = 0

    def created(self, patient_id, status='standing'):
        self.acknowledged[int(patient_id)] = status or 'standing'

    def acknowledge(self, patient_id, status):
        if patient_id is None or status not in _RANK:
            return
        self.acknowledged[int(patient_id)] = status

    def create_failed(self):
        self.create_failures += 1


def reconcile(acknowledged, stored, *, create_failures=0):
    """Compare les etats confirmes (``acknowledged``) a la base (``stored``).

    Les deux arguments sont des ``{patient_id: status}``. Le resultat est un
    dictionnaire JSON-serialisable, borne (echantillons de 10 ecarts maximum),
    destine au resume du test.
    """
    acknowledged = {int(k): v for k, v in (acknowledged or {}).items()}
    stored = {int(k): v for k, v in (stored or {}).items()}
    missing, regressed, ahead, consistent = [], [], [], 0
    for patient_id, expected in sorted(acknowledged.items()):
        if patient_id not in stored:
            missing.append(patient_id)
            continue
        actual = stored[patient_id]
        expected_rank = _RANK.get(expected, -1)
        actual_rank = _RANK.get(actual, -1)
        if actual_rank == expected_rank:
            consistent += 1
        elif actual_rank > expected_rank:
            ahead.append(patient_id)
        else:
            regressed.append({'id': patient_id, 'expected': expected,
                              'stored': actual})
    unacknowledged = sorted(set(stored) - set(acknowledged))
    lost = len(missing) + len(regressed)
    if lost:
        status = 'lost'
    elif ahead or unacknowledged:
        status = 'ambiguous'
    else:
        status = 'ok'
    return {
        'status': status,
        'acknowledged_patients': len(acknowledged),
        'stored_patients': len(stored),
        'consistent': consistent,
        'lost': lost,
        'missing': len(missing),
        'regressed': len(regressed),
        'ahead': len(ahead),
        'unacknowledged': len(unacknowledged),
        'create_failures': int(create_failures or 0),
        'samples': {
            'missing': missing[:SAMPLE_LIMIT],
            'regressed': regressed[:SAMPLE_LIMIT],
        },
    }


def _plural(count, singular, plural):
    return singular if count <= 1 else plural


def reconciliation_message(result):
    """Phrase directement affichable et ton Bootstrap (success/warning/danger)."""
    if not result:
        return None, None
    acked = result.get('acknowledged_patients') or 0
    if result.get('status') == 'lost':
        lost = result.get('lost') or 0
        return 'danger', (
            f"{lost} {_plural(lost, 'patient de test perdu ou revenu', 'patients de test perdus ou revenus')} "
            "à un état antérieur alors que le serveur avait confirmé l'action "
            f"({result.get('missing', 0)} absent(s) de la base, "
            f"{result.get('regressed', 0)} dans un état antérieur). "
            "Un traitement planifié (vidage de la file, validation automatique) "
            "peut aussi expliquer un écart : vérifiez qu'aucun n'a tourné pendant le test.")
    if result.get('status') == 'ambiguous':
        pending = (result.get('ahead') or 0) + (result.get('unacknowledged') or 0)
        return 'warning', (
            f"Aucune perte sur {acked} {_plural(acked, 'patient', 'patients')} de test, mais "
            f"{pending} {_plural(pending, 'action a été appliquée', 'actions ont été appliquées')} "
            "par le serveur sans que la réponse arrive au simulateur "
            "(délai dépassé ou arrêt du test).")
    if not acked:
        return 'warning', "Aucun patient de test n'a été confirmé : le bilan des données est vide."
    return 'success', (
        f"Aucune perte : {_plural(acked, 'le patient de test confirmé', f'les {acked} patients de test confirmés')} "
        f"par le serveur {_plural(acked, 'est', 'sont tous')} en base, dans l'état attendu.")
