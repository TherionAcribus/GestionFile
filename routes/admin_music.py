import os
import json
import spotipy
from spotipy.oauth2 import SpotifyOAuth, SpotifyOauthError
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, current_app as app, flash
from models import db, ConfigOption, DashboardCard, SpotifyToken
from communication import communikation
from routes.admin_security import require_permission, require_permission_dashboard
from flask_login import current_user
from spotify_support import (
    SPOTIFY_SCOPE,
    DuckController,
    resolve_spotify_credentials,
    resolve_redirect_uri,
    run_duck_cycle,
)

admin_music_bp = Blueprint('admin_music', __name__)

# Durée par défaut de la mise en sourdine pendant une annonce (secondes). Sert de
# borne de sécurité : la musique reprend au plus tard après ce délai même si un
# évènement de fin d'annonce était manqué. Les annonces vocales sont courtes.
DUCK_RESUME_SECONDS = 12

# Contrôleur global de sourdine pendant les annonces (voir spotify_support).
_duck_controller = DuckController()


class SpotifyDBCacheHandler(spotipy.CacheHandler):
    """Cache de jeton spotipy adossé à la base de données (côté serveur).

    Remplace l'ancien stockage en session Flask (cookie signé côté client), qui
    exposait les jetons d'accès et de rafraîchissement au navigateur. Le jeton
    est conservé dans l'unique ligne ``SpotifyToken`` (``id == 1``).
    """

    def get_cached_token(self):
        row = SpotifyToken.query.get(1)
        if not row or not row.token_info:
            return None
        try:
            return json.loads(row.token_info)
        except (ValueError, TypeError):
            return None

    def save_token_to_cache(self, token_info):
        try:
            row = SpotifyToken.query.get(1)
            if row is None:
                row = SpotifyToken(id=1)
                db.session.add(row)
            row.token_info = json.dumps(token_info)
            db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Échec d'enregistrement du jeton Spotify")

    def delete_token_from_cache(self):
        try:
            row = SpotifyToken.query.get(1)
            if row is not None:
                row.token_info = None
                db.session.commit()
        except Exception:
            db.session.rollback()
            app.logger.exception("Échec de suppression du jeton Spotify")


def _spotify_redirect_uri():
    """URL de redirection OAuth, tolérante à l'absence de contexte de requête."""
    external = None
    try:
        external = url_for('admin_music.spotify_callback', _external=True)
    except RuntimeError:
        # Hors contexte de requête (tâche de fond de rafraîchissement) : on
        # reconstruit l'URL à partir de l'adresse réseau configurée.
        external = None
    return resolve_redirect_uri(external, app.config.get("NETWORK_ADRESS"))


def build_spotify_oauth():
    """Construit le client OAuth Spotify, ou ``None`` si non configuré.

    L'identifiant et le secret client proviennent, dans l'ordre, des variables
    d'environnement ``SPOTIFY_CLIENT_ID`` / ``SPOTIFY_CLIENT_SECRET`` (gestion
    des secrets), puis de la configuration de l'officine. **Aucune valeur n'est
    codée en dur.** Le jeton obtenu est mis en cache côté serveur.
    """
    creds = resolve_spotify_credentials(
        os.environ.get("SPOTIFY_CLIENT_ID"),
        os.environ.get("SPOTIFY_CLIENT_SECRET"),
        app.config.get("MUSIC_SPOTIFY_USER"),
        app.config.get("MUSIC_SPOTIFY_KEY"),
    )
    if creds is None:
        return None
    client_id, client_secret = creds
    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=_spotify_redirect_uri(),
        scope=SPOTIFY_SCOPE,
        cache_handler=SpotifyDBCacheHandler(),
    )


@admin_music_bp.route('/admin/music')
@admin_music_bp.route('/admin/music/<tab>')
def admin_music(tab=None):
    # Vérifier les permissions pour chaque onglet
    can_access_player = any(role.has_permission('music_play') for role in current_user.roles)
    can_access_options = any(role.has_permission('music_options') for role in current_user.roles)

    if not (can_access_player or can_access_options):
        error_message = f"Vous n'avez pas les permissions nécessaires pour pour accéder à la partie 'musique'."
        return render_template('admin/permission_error.html', error_message=error_message)

    # Déterminer l'onglet actif en fonction des permissions
    valid_tabs = []
    if can_access_player:
        valid_tabs.append('player')
    if can_access_options:
        valid_tabs.append('options')

    # Si aucun onglet n'est spécifié ou si l'onglet spécifié n'est pas valide,
    # utiliser le premier onglet accessible
    if not tab or tab not in valid_tabs:
        tab = valid_tabs[0] if valid_tabs else None

    # Si l'utilisateur essaie d'accéder à un onglet pour lequel il n'a pas la permission
    if tab == 'player' and not can_access_player:
        error_message = f"Vous n'avez pas les permissions nécessaires pour pour accéder au lecteur audio'."
        return render_template('admin/permission_error.html', error_message=error_message)
    elif tab == 'options' and not can_access_options:
        error_message = f"Vous n'avez pas les permissions nécessaires pour pour accéder aux options de la musique'."
        return render_template('admin/permission_error.html', error_message=error_message)

    token_info, authorized = get_spotify_token()
    spotify_connected = authorized
    if spotify_connected:
        sp = spotipy.Spotify(auth=token_info['access_token'])
        playlists = sp.current_user_playlists()

        return render_template('/admin/music.html',
                                music_spotify = app.config["MUSIC_SPOTIFY"],
                                music_spotify_user = app.config["MUSIC_SPOTIFY_USER"],
                                # Secret : jamais transmis au gabarit, seulement
                                # l'indicateur « défini / non défini ».
                                music_spotify_key_set = bool(app.config.get("MUSIC_SPOTIFY_KEY")),
                                music_volume = app.config["MUSIC_VOLUME"],
                                music_announce_volume = app.config["MUSIC_ANNOUNCE_VOLUME"],
                                music_announce_action = app.config["MUSIC_ANNOUNCE_ACTION"],
                                spotify_connected=spotify_connected,
                                track_infos = get_spotify_current_track_info(),
                                playlists=playlists['items'],
                                active_tab=tab,
                                can_access_player=can_access_player,
                                can_access_options=can_access_options)

    else:
        return render_template('/admin/music.html',
                                music_spotify = app.config["MUSIC_SPOTIFY"],
                                music_spotify_user = app.config["MUSIC_SPOTIFY_USER"],
                                # Secret : jamais transmis au gabarit, seulement
                                # l'indicateur « défini / non défini ».
                                music_spotify_key_set = bool(app.config.get("MUSIC_SPOTIFY_KEY")),
                                music_volume = app.config["MUSIC_VOLUME"],
                                music_announce_volume = app.config["MUSIC_ANNOUNCE_VOLUME"],
                                music_announce_action = app.config["MUSIC_ANNOUNCE_ACTION"],
                                spotify_connected=spotify_connected,
                                playlists=[],
                                active_tab=tab,
                                can_access_player=can_access_player,
                                can_access_options=can_access_options)


@admin_music_bp.route('/admin/music/player/<action>', methods=['POST'])
@require_permission('music_play')
def player_action(action):
    if action == 'shuffle':
        return shuffle_playlist()
    elif action == 'pause':
        return pause_music()
    elif action == 'resume':
        return resume_music()
    elif action == 'next':
        return next_track()
    elif action == 'previous':
        return previous_track()
    elif action == 'change_volume':
        return change_volume()
    elif action == 'start_announce':
        return start_announce_music()
    elif action == 'stop_announce':
        return stop_announce_music()
    elif action == 'play_playlist':
        return play_playlist()

@admin_music_bp.route('/admin/music/save_options', methods=['POST'])
@require_permission('music_options')
def save_music_options():
    """Sauvegarde les options de musique"""
    try:
        # Code existant...
        pass
    except Exception:
        app.logger.exception("Erreur lors de la sauvegarde des options de musique")
        return '', 500


@admin_music_bp.route('/spotify/login')
@require_permission('music_options')
def spotify_login():
    # Initialiser le flux OAuth (identifiants lus depuis la config/env, jamais
    # codés en dur). Si Spotify n'est pas configuré, on renvoie une erreur claire.
    sp_oauth = build_spotify_oauth()
    if sp_oauth is None:
        flash("Spotify n'est pas configuré (identifiant/secret client manquant).")
        return redirect(url_for('admin_music.admin_music', tab='options'))
    auth_url = sp_oauth.get_authorize_url()
    communikation("update_screen", event="spotify_status", data=True)
    # Rediriger l'utilisateur vers l'URL d'autorisation
    return redirect(auth_url)


def clear_spotify_tokens():
    """Supprime le jeton Spotify du stockage serveur."""
    SpotifyDBCacheHandler().delete_token_from_cache()
    _duck_controller.reset()

@admin_music_bp.route('/spotify/logout')
@require_permission('music_options')
def spotify_logout():
    clear_spotify_tokens()
    communikation("update_screen", event="spotify_status", data=False)
    return redirect(url_for('admin_music.admin_music'))

@admin_music_bp.route('/spotify/callback')
@require_permission('music_options')
def spotify_callback():
    sp_oauth = build_spotify_oauth()
    if sp_oauth is None:
        return redirect(url_for('admin_music.error_page'))

    # Obtenir le code de l'URL de redirection
    code = request.args.get('code')

    try:
        # Échanger le code contre un jeton d'accès. Le cache_handler enregistre
        # automatiquement le jeton côté serveur (table SpotifyToken).
        sp_oauth.get_access_token(code, check_cache=False)
    except SpotifyOauthError:
        app.logger.warning("Échec de l'obtention du jeton Spotify (OAuth)")
        return redirect(url_for('admin_music.error_page'))

    return redirect(url_for('admin_music.admin_music'))  # Rediriger vers votre page d'administration ou autre

@admin_music_bp.route('/show_saved_tracks')
@require_permission('music_play')
def show_saved_tracks():
    token_info, authorized = get_spotify_token()
    if not authorized:
        # Rediriger vers l'authentification si le token n'est pas présent
        return redirect(url_for('admin_music.spotify_login'))

    # Utiliser spotipy.Spotify pour créer un objet client
    sp = spotipy.Spotify(auth=token_info['access_token'])
    results = sp.current_user_saved_tracks()
    tracks = []
    for idx, item in enumerate(results['items']):
        track = item['track']
        tracks.append(f"{idx}: {track['artists'][0]['name']} – {track['name']}")

    # Retourne les pistes en HTML
    return "<br>".join(tracks)


@admin_music_bp.route('/error')
def error_page():
    return "Une erreur s'est produite avec votre authentification Spotify. Veuillez essayer de vous reconnecter.", 400

def get_spotipy():
    token_info, authorized = get_spotify_token()
    if not authorized:
        return redirect(url_for('admin_music.spotify_login'))
    
    return spotipy.Spotify(auth=token_info['access_token'])

def is_spotipy_connected():
    token_info, authorized = get_spotify_token()
    if not authorized:
        return False    
    return True

def spotify_exception_handler(func):
    """ Décoration qui permet de gérer les erreurs de Spotify"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except spotipy.exceptions.SpotifyException as e:
            app.logger.error(f"Failed during Spotify operation in function {func.__name__}: {e}")
            app.display_toast(success=False, message="Error :" + str(e))
            # Retourner une réponse d'erreur standardisée
            return '', 500  # Code 500 pour une erreur serveur
    return wrapper

@admin_music_bp.route('/spotify/shuffle', methods=['GET'])
@require_permission('music_play')
@spotify_exception_handler
def shuffle_playlist():
    #TODO Ne semble plus fonctionner
    sp = get_spotipy()
    
    # Récupérer les appareils disponibles
    devices = sp.devices()

    # Sélectionner le premier appareil actif
    active_device = None
    for device in devices['devices']:
        if device['is_active']:
            active_device = device['id']
            break

    if active_device:
        # Activer le shuffle sur l'appareil actif
        sp.shuffle(state=True, device_id=active_device)
        return '', 204
    else:
        return 'Aucun appareil actif trouvé pour activer le shuffle.', 400

@admin_music_bp.route('/spotify/pause_music', methods=['GET'])
@require_permission('music_play')
@spotify_exception_handler
def pause_music():
    sp = get_spotipy()
    sp.pause_playback()
    return '', 204

@admin_music_bp.route('/spotify/resume_music', methods=['GET'])
@require_permission('music_play')
@spotify_exception_handler
def resume_music():
    sp = get_spotipy()
    sp.start_playback()
    return '', 204

@admin_music_bp.route('/spotify/next_track', methods=['GET'])
@require_permission('music_play')
@spotify_exception_handler
def next_track():
    sp = get_spotipy()
    sp.next_track()
    return '', 204

@admin_music_bp.route('/spotify/previous_track', methods=['GET'])
@require_permission('music_play')
@spotify_exception_handler
def previous_track():
    sp = get_spotipy()
    sp.previous_track()
    return '', 204


@admin_music_bp.route('/spotify/change_volume', methods=['POST'])
@require_permission('music_play')
def change_volume():
    """ Fonction appelée lorsque l'on change la valeur du slider de volume dans le lecteur 
    Enregistre la nouvelle valeur dans la BDD et change le volume du lecteur """
    volume = int(request.values.get('volume'))
    
    # change le volume dans la BDD
    config_option = ConfigOption.query.filter_by(config_key="music_volume").first()
    if config_option:
        config_option.value_int = volume
        db.session.commit()

    # change le volume tout de suite
    set_volume(volume)

    return '', 204

@spotify_exception_handler
def set_volume(volume):
    sp = get_spotipy()
    sp.volume(volume)
    return '', 204

# NB : plus de routes HTTP publiques pour le ducking. Ces actions ne sont plus
# déclenchées par l'écran d'annonce (navigateur public) mais pilotées côté
# serveur (cf. duck_for_announcement), ce qui permet de protéger l'ensemble des
# routes Spotify par une permission admin.
def start_announce_music():
    if app.config["MUSIC_ANNOUNCE_ACTION"] == "pause":
        return pause_music()
    elif app.config["MUSIC_ANNOUNCE_ACTION"] == "down":
        return set_volume(app.config["MUSIC_ANNOUNCE_VOLUME"])

def stop_announce_music():
    if app.config["MUSIC_ANNOUNCE_ACTION"] == "pause":
        return resume_music()
    elif app.config["MUSIC_ANNOUNCE_ACTION"] == "down":
        return set_volume(app.config["MUSIC_VOLUME"])


def duck_for_announcement(duration=None):
    """Met la musique en sourdine pendant une annonce, **côté serveur**.

    Lancé dans une tâche de fond (hors du chemin de l'appel patient) : un
    éventuel incident réseau Spotify n'affecte ni la latence ni la fiabilité de
    l'appel. La reprise est automatique après ``duration`` secondes ; les
    annonces qui se chevauchent prolongent la sourdine (cf. DuckController).
    """
    try:
        app_obj = app._get_current_object()
    except Exception:
        return
    socketio = getattr(app_obj, "socketio", None)
    if socketio is None:
        return
    secs = DUCK_RESUME_SECONDS if duration is None else duration

    def worker():
        with app_obj.app_context():
            try:
                if not app_obj.config.get("MUSIC_SPOTIFY"):
                    return
                if not is_spotipy_connected():
                    return
                run_duck_cycle(
                    _duck_controller,
                    start_announce_music,
                    stop_announce_music,
                    socketio.sleep,
                    secs,
                )
            except Exception:
                app_obj.logger.exception("Échec du ducking Spotify pendant l'annonce")

    socketio.start_background_task(worker)

@admin_music_bp.route('/spotify/play_playlist', methods=['POST'])
@require_permission('music_play')
@spotify_exception_handler
def play_playlist():
    sp = get_spotipy()
    playlist_uri = request.form['playlist_uri']

    sp.start_playback(context_uri=playlist_uri)

    app.config["IS_PLAYING_SPOTIFY"] = True

    # Envoie la commande à la page "announce" via WebSocket ou un autre mécanisme
    """communikation("update_audio", 
                    event="spotify", 
                    data={
                        'playlist_uri': playlist_uri, 
                        'access_token': token_info['access_token'],
                        'shuffle': shuffle  # Ajoute l'option shuffle dans les données
                    })"""

    #socketio.emit('play_playlist', {'playlist_uri': playlist_uri}, namespace='/announce')

    return redirect(url_for('admin_music.admin_music'))

def get_spotify_token():
    """Retourne ``(token_info, authorized)`` en lisant le jeton côté serveur.

    Le jeton est stocké en base (``SpotifyToken``) et non plus dans la session
    Flask. spotipy gère automatiquement le rafraîchissement via le cache handler
    lorsque le jeton est expiré ; le nouveau jeton est ré-enregistré en base.
    """
    sp_oauth = build_spotify_oauth()
    if sp_oauth is None:
        return None, False

    try:
        cached = sp_oauth.cache_handler.get_cached_token()
        token_info = sp_oauth.validate_token(cached)
    except SpotifyOauthError:
        app.logger.warning("Échec du rafraîchissement du jeton Spotify")
        return None, False
    except Exception:
        # Incident réseau/HTTP côté Spotify : on dégrade en « non connecté »
        # plutôt que de faire échouer le rendu de page ou la tâche de ducking.
        app.logger.warning("Erreur lors de la validation du jeton Spotify", exc_info=True)
        return None, False

    if not token_info:
        return None, False

    return token_info, True


def get_spotify_volume():
    sp = get_spotipy()
    
    # Récupère l'état du lecteur
    playback_info = sp.current_playback()

    if playback_info and 'device' in playback_info:
        return playback_info['device']['volume_percent']  # Retourne le volume
    else:
        return None  # Retourne None si on ne peut pas obtenir le volume
    

def get_spotify_current_track_info():
    sp = get_spotipy()
    
    # Récupère l'état actuel de la lecture
    playback_info = sp.current_playback()

    if playback_info and 'item' in playback_info:
        track_infos = {
            'track_name': playback_info['item']['name'],
            'artist_name': ', '.join([artist['name'] for artist in playback_info['item']['artists']]),
            'album_name': playback_info['item']['album']['name'],
            'volume_percent': playback_info['device']['volume_percent'],
            'shuffle_state': playback_info['shuffle_state'],  # True si shuffle activé
            'device_name': playback_info['device']['name'],  # Nom du lecteur
            'device_type': playback_info['device']['type'], 
        }
        return track_infos
    else:
        # Si aucun morceau n'est en cours de lecture
        return {
            'track_name': '',
            'artist_name': '',
            'album_name': '',
            'volume_percent': 50
        }  


@admin_music_bp.route('/admin/music/dashboard')
@require_permission_dashboard('music_play')
def dashboard_music():
    token_info, authorized = get_spotify_token()
    spotify_connected = authorized
    dashboardcard = DashboardCard.query.filter_by(name="player").first()
    
    if spotify_connected:
        track_infos = get_spotify_current_track_info()
        return render_template('/admin/dashboard_player.html',
                             spotify_connected=spotify_connected,
                             track_infos=track_infos,
                             dashboardcard=dashboardcard)
    else:
        return render_template('/admin/dashboard_player.html',
                             spotify_connected=spotify_connected,
                             dashboardcard=dashboardcard)