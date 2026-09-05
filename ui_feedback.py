"""Retour visuel des actions d'administration (« toasts ») et petits utilitaires.

Extrait d'``app.py`` (point 9.5d). Ces fonctions étaient greffées sur l'objet
application (``app.display_toast = display_toast``) pour être atteignables depuis
les blueprints via ``current_app`` — un contournement de la dépendance circulaire
que la fabrique d'application rend inutile. Elles s'importent désormais
normalement.

L'``after_request`` qui transforme le retour mémorisé en en-tête ``HX-Trigger``
reste dans ``app.py`` : il est enregistré sur l'application, pas sur un blueprint.
"""

from flask import current_app, g, has_request_context

from communication import communikation


def display_toast(success=True, message=None):
    """Affiche le toast dans la page d'administration.

    Pour une validation réussie, on peut simplement appeler la fonction sans
    argument. Renvoie un ``("", 204)`` directement utilisable comme réponse de vue.
    """
    if message is None:
        message = "Enregistrement effectué"

    data = {"toast": True, 'success': success, 'message': message}
    communikation("admin", data)

    # Point 7.5 : en plus de la diffusion WebSocket (qui informe TOUS les
    # administrateurs), on mémorise le résultat pour l'auteur de la requête.
    # `_attach_admin_feedback` (after_request, dans app.py) le renvoie dans
    # l'en-tête HX-Trigger, ce qui permet au client de confirmer la sauvegarde à
    # partir de la réponse HTTP — sans dépendre uniquement du WebSocket.
    if has_request_context():
        g._admin_feedback = {'success': bool(success), 'message': message}

    return "", 204


def allowed_image_file(filename):
    """Vérifie si le fichier a une extension autorisée.

    Conservée pour compatibilité ; les nouveaux uploads devraient utiliser
    ``image_storage.accept_image_upload`` (validation complète : extension +
    contenu + taille + nom unique).
    """
    from image_storage import ALLOWED_IMAGE_EXTENSIONS
    return ('.' in filename
            and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS)
