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

from flask import current_app, request
from flask_socketio import join_room, leave_room

from auth_utils import is_authenticated_request, is_socket_connection_authorized
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
_handlers_simples("/socket_patient", "SECURITY_LOGIN_PATIENT", "page patient")
_handlers_simples("/socket_app_counter", "SECURITY_LOGIN_COUNTER", "App comptoir")
_handlers_simples("/socket_app_screen", "SECURITY_LOGIN_SCREEN", "App ecran")
_handlers_simples("/socket_counter", "SECURITY_LOGIN_COUNTER", "comptoir")


@socketio.on("connect", namespace="/socket_admin")
def connect_admin():
    # Admin : authentification TOUJOURS requise (point 1.2). SECURITY_LOGIN_ADMIN
    # est déprécié et n'est plus consulté pour le namespace d'administration :
    # il ne peut plus rendre l'admin anonyme.
    if not is_authenticated_request():
        current_app.logger.warning(
            "Connexion Socket.IO refusee sur /socket_admin (login manquant)."
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
    # Pas de garde d'authentification : le téléphone du patient n'a pas de
    # session. Il rejoint une salle dérivée de son numéro d'appel, lu dans ses
    # propres cookies — comportement historique, conservé tel quel.
    register_client(request)
    logger.info("Client connecte au namespace telephone")

    patient_id = request.cookies.get("patient_id")
    call_number = request.cookies.get("patient_call_number")
    if patient_id and call_number:
        join_room(f"call_{call_number}")
        current_app.logger.debug(
            "Patient %s (numero d'appel %s) a rejoint sa salle", patient_id, call_number
        )


@socketio.on("disconnect", namespace="/socket_phone")
def disconnect_phone():
    forget_client(request)
    logger.info("Client deconnecte du namespace telephone")

    call_number = request.cookies.get("patient_call_number")
    if request.cookies.get("patient_id") and call_number:
        leave_room(f"call_{call_number}")
