"""Journal d'audit des actions sensibles (point 7 — Phase 8).

Module **pur** (aucune dépendance Flask ni base de données) : il construit une
ligne d'audit normalisée pour une action sensible, à partir des champs demandés
par le cahier des charges :

    utilisateur · action · ressource · identifiant ciblé · date · résultat

La *date* n'est pas portée ici : c'est l'horodatage de persistance (colonne
``timestamp`` du modèle ``AuditLog``, remplie côté base). Ce module se concentre
sur la **normalisation** et la **redaction** des champs libres, exactement comme
son module frère :pymod:`login_audit` le fait pour les connexions.

Garanties de ce module :

* aucun secret n'est jamais accepté (pas de champ mot de passe / jeton / cookie ;
  la fonction n'expose tout simplement pas de tel paramètre) ;
* les valeurs libres sont aplaties en une seule ligne (retours chariot retirés)
  pour empêcher l'injection de fausses lignes de journal ;
* chaque champ est borné en longueur pour qu'un client ne puisse pas gonfler le
  journal.

Le câblage (résolution de l'utilisateur courant, de l'IP, écriture en base et
émission dans le logger) vit dans :pymod:`audit_service`, qui s'appuie sur ce
noyau pur.
"""

from __future__ import annotations

# --- Résultats normalisés ---------------------------------------------------
OUTCOME_SUCCESS = "success"   # l'action a abouti
OUTCOME_FAILURE = "failure"   # échec : erreur technique ou refus par une règle métier
OUTCOME_DENIED = "denied"     # refus d'autorisation (permission manquante)

_VALID_OUTCOMES = {OUTCOME_SUCCESS, OUTCOME_FAILURE, OUTCOME_DENIED}

# --- Vocabulaire d'actions --------------------------------------------------
# Verbes courts, stables et non traduits : ils servent de clé de recherche dans
# le journal et ne doivent pas varier avec la langue de l'IHM.
ACTION_CREATE = "create"
ACTION_UPDATE = "update"
ACTION_DELETE = "delete"
ACTION_CLEAR = "clear"          # vidage massif (ex. toute la file)
ACTION_RESTORE = "restore"      # restauration d'une sauvegarde
ACTION_IMPORT = "import"        # import de données
ACTION_ACTIVATE = "activate"
ACTION_DEACTIVATE = "deactivate"
ACTION_RESET = "reset"          # réinitialisation (ex. compte admin)
ACTION_LOGOUT_ALL = "logout_all"
ACTION_CONNECT = "connect"      # connexion d'un service tiers (ex. Spotify)
ACTION_DISCONNECT = "disconnect"

# --- Bornes de longueur -----------------------------------------------------
_MAX_USERNAME_LEN = 64
_MAX_ACTION_LEN = 40
_MAX_RESOURCE_LEN = 40
_MAX_TARGET_LEN = 64
_MAX_DETAILS_LEN = 200
_MAX_IP_LEN = 64


def _sanitize(value, max_len: int) -> str:
    """Aplati une valeur libre en une chaîne courte et mono-ligne.

    Retire les retours à la ligne (empêche l'injection de fausses lignes de
    journal) et borne la longueur pour ne pas laisser un client gonfler le
    journal. ``None`` et le vide deviennent ``"-"``.
    """
    if value is None:
        return "-"
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return "-"
    if len(text) > max_len:
        text = text[:max_len] + "…"
    return text


def build_audit_record(
    action: str,
    resource: str,
    *,
    user=None,
    target_id=None,
    outcome: str = OUTCOME_SUCCESS,
    details=None,
    ip=None,
) -> dict:
    """Construit l'enregistrement d'audit (dict de champs non sensibles).

    - ``action`` / ``resource`` : nature de l'opération et domaine visé.
    - ``user`` : nom d'utilisateur *revendiqué* (jamais un secret). ``None`` pour
      une action système / non authentifiée.
    - ``target_id`` : identifiant ciblé (entier ou chaîne) ; ``None`` pour une
      action sans cible précise (ex. vidage de toute la file).
    - ``outcome`` : ``OUTCOME_*`` ; toute valeur inconnue retombe sur *failure*.
    - ``details`` : complément court et non sensible (optionnel). Omis s'il est
      vide.
    """
    if outcome not in _VALID_OUTCOMES:
        outcome = OUTCOME_FAILURE
    record = {
        "event": "audit",
        "outcome": outcome,
        "user": _sanitize(user, _MAX_USERNAME_LEN),
        "action": _sanitize(action, _MAX_ACTION_LEN),
        "resource": _sanitize(resource, _MAX_RESOURCE_LEN),
        "target": _sanitize(target_id, _MAX_TARGET_LEN),
        "ip": _sanitize(ip, _MAX_IP_LEN),
    }
    if details is not None:
        clean = _sanitize(details, _MAX_DETAILS_LEN)
        if clean != "-":
            record["details"] = clean
    return record


def format_audit_record(record: dict) -> str:
    """Rend l'enregistrement en une ligne ``clé=valeur`` stable et lisible."""
    parts = [f"{k}={record[k]}" for k in record]
    return "audit " + " ".join(parts)
