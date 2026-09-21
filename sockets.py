"""Gestionnaires Socket.IO : connexion/déconnexion des 8 namespaces.

Extrait d'``app.py`` (point 9.5c). Les décorateurs s'appliquent sur l'objet
``socketio`` partagé d'``extensions.py``, créé sans application : importer ce
module suffit à enregistrer les gestionnaires, quel que soit le moment où
``init_app`` est appelé.

Découpage des rôles : ce module ne fait que **l'accueil** des clients temps réel
(authentification de la connexion, mémorisation du nom d'utilisateur, salles du
téléphone patient). L'émission des messages, elle, reste dans
``communication.communikation``.
"""

import logging
import re

from flask import current_app, request
from flask_socketio import join_room, leave_room, rooms

from auth_utils import is_authenticated_request, is_admin_session, is_kiosk_patient_session, is_socket_connection_authorized, check_patient_phone_token
from extensions import socketio

logger = logging.getLogger(__name__)

# Les 8 namespaces exposés. Cette liste sert aussi aux pages d'administration
# (« connexions » du tableau de bord, envoi de notifications ciblées) pour
# proposer les destinations possibles.
NAMESPACES = (
    "/socket_update_patient",
    "/socket_update_screen",
    "/socket_admin",
    "/socket_patient",
    "/socket_app_counter",
    "/socket_app_screen",
    "/socket_counter",
    "/socket_phone",
)

# NOTE: historiquement `app.active_connections`, un dict {namespace: set()} dont
# les ensembles n'ont **jamais** été alimentés — seules ses clés étaient lues.
# Conservé sous forme de dict pour ne rien casser côté appelants, mais c'est en
# pratique la liste des namespaces ci-dessus.
active_connections = {namespace: set() for namespace in NAMESPACES}

# {sid: {"username": ...}} — alimenté à la connexion, purgé à la déconnexion.
connected_clients_info = {}


def register_client(req):
    """Mémorise le nom d'utilisateur associé à la connexion.

    Les noms d'utilisateur arrivent par un en-tête côté PySide et par la
    querystring côté JavaScript, pour des raisons de simplicité côté client.
    """
    username = req.headers.get("username")
    if not username:
        username = req.args.get("username", "Unknown")

    connected_clients_info[req.sid] = {"username": username}
    return username


def forget_client(req):
    connected_clients_info.pop(req.sid, None)


def _socket_require(flag_name, namespace):
    """Autorise la connexion selon le drapeau de sécurité correspondant."""
    allowed = is_socket_connection_authorized(current_app.config.get(flag_name, False))
    if not allowed:
        current_app.logger.warning(
            "Connexion Socket.IO refusee sur %s (login/jeton manquant).", namespace
        )
    return allowed


def _handlers_simples(namespace, flag_name=None, libelle=None):
    """Déclare le couple connect/disconnect standard d'un namespace.

    Les huit namespaces partageaient le même corps recopié seize fois ; seuls le
    drapeau de sécurité et le libellé de journalisation changeaient.
    """
    libelle = libelle or namespace

    @socketio.on("connect", namespace=namespace)
    def _connect():
        if flag_name is not None and not _socket_require(flag_name, namespace):
            return False
        register_client(request)
        logger.info("Client connecte au namespace %s", libelle)

    @socketio.on("disconnect", namespace=namespace)
    def _disconnect():
        forget_client(request)
        logger.info("Client deconnecte du namespace %s", libelle)

    return _connect, _disconnect


# ATTENTION : /socket_update_patient n'a AUCUNE garde d'authentification. C'est
# le comportement historique (la file d'attente est affichée publiquement) ;
# documenté ici pour que ce soit un choix visible et non un oubli.
_handlers_simples("/socket_update_patient", None, "file patients")
_handlers_simples("/socket_update_screen", "SECURITY_LOGIN_SCREEN", "ecran d'affichage")
_handlers_simples("/socket_app_counter", "SECURITY_LOGIN_COUNTER", "App comptoir")
_handlers_simples("/socket_app_screen", "SECURITY_LOGIN_SCREEN", "App ecran")
_handlers_simples("/socket_counter", "SECURITY_LOGIN_COUNTER", "comptoir")


def _socket_patient_authorized(flag_active):
    """Autorisation du namespace /socket_patient (page borne).

    Même périmètre que la zone HTTP /patient : quand SECURITY_LOGIN_PATIENT est
    actif, la connexion exige une session utilisateur, un jeton applicatif —
    OU une session borne (``patient_kiosk``), émise via /patient/kiosk_login
    sans compte technique. Sans ce troisième cas, la borne connecterait sa page
    mais son Socket.IO serait refusé."""
    if not flag_active:
        return True
    return is_authenticated_request() or is_kiosk_patient_session()


@socketio.on("connect", namespace="/socket_patient")
def connect_patient():
    if not _socket_patient_authorized(
            current_app.config.get("SECURITY_LOGIN_PATIENT", False)):
        current_app.logger.warning(
            "Connexion Socket.IO refusee sur /socket_patient "
            "(session/jeton/session-borne manquant)."
        )
        return False
    register_client(request)
    logger.info("Client connecte au namespace page patient")


@socketio.on("disconnect", namespace="/socket_patient")
def disconnect_patient():
    forget_client(request)
    logger.info("Client deconnecte du namespace page patient")


# --- Parcours QR de la borne -------------------------------------------------
# Chaque QR affiché par une borne correspond à un « parcours » identifié par un
# UUID (généré dans left_page_validate_patient et encodé dans l'URL du QR). La
# borne rejoint la salle scan_<uuid> quand le fragment QR apparaît ; le ping du
# téléphone n'émet update_scan_phone que dans cette salle. Avec plusieurs
# bornes ou parcours simultanés, la confirmation ne peut plus arriver sur le
# mauvais écran.

SCAN_JOURNEY_PREFIX = "scan_"
_SCAN_JOURNEY_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def scan_journey_room(journey_id):
    """Nom de la salle /socket_patient dédiée à un parcours QR."""
    return f"{SCAN_JOURNEY_PREFIX}{journey_id}"


def _leave_scan_journeys():
    """Quitte toutes les salles scan_* du client courant.

    Une borne n'affiche qu'un parcours QR à la fois : on purge les salles
    précédentes avant d'en rejoindre une nouvelle ou de quitter la page QR."""
    for room in rooms():
        if room.startswith(SCAN_JOURNEY_PREFIX):
            leave_room(room)


@socketio.on("join_scan_journey", namespace="/socket_patient")
def join_scan_journey(data):
    journey = (data or {}).get("journey")
    if not journey or not _SCAN_JOURNEY_ID.match(str(journey)):
        return
    _leave_scan_journeys()
    join_room(scan_journey_room(journey))
    logger.debug("Borne dans la salle parcours %s", journey)


@socketio.on("leave_scan_journey", namespace="/socket_patient")
def leave_scan_journey(_data=None):
    _leave_scan_journeys()


@socketio.on("connect", namespace="/socket_admin")
def connect_admin():
    # Admin : authentification TOUJOURS requise (point 1.2). SECURITY_LOGIN_ADMIN
    # est déprécié et n'est plus consulté pour le namespace d'administration :
    # il ne peut plus rendre l'admin anonyme.
    #
    # Point 2 (audit Admin) : on exige une **session admin authentifiée**, pas
    # un simple jeton applicatif (X-App-Token). Un jeton machine générique ne
    # prouve pas que le client est une interface d'administration : il pourrait
    # être utilisé par une application comptoir/écran pour se connecter au
    # namespace admin et recevoir des évènements (toasts, rafraîchissements)
    # qui ne la concernent pas.
    if not is_admin_session():
        current_app.logger.warning(
            "Connexion Socket.IO refusee sur /socket_admin "
            "(session admin manquante — jeton applicatif non accepté)."
        )
        return False

    # Vérification de permission : l'utilisateur doit avoir au moins une
    # permission admin. Un utilisateur authentifié sans aucun rôle admin
    # (ex. compte de service) ne doit pas rejoindre ce namespace.
    from routes.admin_security import user_has_permission
    from permissions_registry import PERMISSION_RESOURCES
    from flask_login import current_user as _current_user

    has_any_admin_perm = any(
        user_has_permission(_current_user, resource)
        for resource in PERMISSION_RESOURCES
    )
    if not has_any_admin_perm:
        current_app.logger.warning(
            "Connexion Socket.IO refusee sur /socket_admin "
            "(utilisateur sans permission admin)."
        )
        return False

    username = register_client(request)
    logger.info("Client connecte au namespace admin (SID %s, username %s)", request.sid, username)


@socketio.on("disconnect", namespace="/socket_admin")
def disconnect_admin():
    forget_client(request)
    logger.info("Client deconnecte du namespace admin")


@socketio.on("connect", namespace="/socket_phone")
def connect_phone():
    # Pas de session : le téléphone du patient rejoint la salle de son numéro
    # d'appel. Les cookies patient_id / patient_call_number sont lisibles et
    # falsifiables côté client — ils ne suffisent donc pas : il faut le cookie
    # patient_token, signé par le serveur dans /patient/phone/ping. Sans lui,
    # poser patient_call_number=X permettait d'écouter la salle call_X d'un
    # autre patient.
    register_client(request)
    logger.info("Client connecte au namespace telephone")

    patient_id = request.cookies.get("patient_id")
    call_number = request.cookies.get("patient_call_number")
    token = request.cookies.get("patient_token")
    if check_patient_phone_token(token, patient_id, call_number):
        join_room(f"call_{call_number}")
        current_app.logger.debug(
            "Patient %s (numero d'appel %s) a rejoint sa salle", patient_id, call_number
        )
    elif patient_id or call_number:
        logger.warning(
            "Telephone sans jeton patient valide (cookies non signes) : salle non rejointe")


@socketio.on("disconnect", namespace="/socket_phone")
def disconnect_phone():
    forget_client(request)
    logger.info("Client deconnecte du namespace telephone")

    call_number = request.cookies.get("patient_call_number")
    if check_patient_phone_token(
            request.cookies.get("patient_token"),
            request.cookies.get("patient_id"),
            call_number):
        leave_room(f"call_{call_number}")
