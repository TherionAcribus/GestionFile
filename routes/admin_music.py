import spotipy
import time as tm
from spotipy.oauth2 import SpotifyOAuth, SpotifyOauthError
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, current_app as app
from models import db, ConfigOption, DashboardCard
from communication import communikation
from routes.admin_security import require_permission
from flask_login import current_user

admin_music_bp = Blueprint('admin_music', __name__)

class SpotifyFlaskCacheHandler(spotipy.CacheHandler):
    def __init__(self, session_key):
        self.session_key = session_key

    def get_cached_token(self):
        return session.get(self.session_key)

    def save_token_to_cache(self, token_info):
        session[self.session_key] = token_info

    def delete_token_from_cache(self):
        session.pop(self.session_key, None)


@admin_music_bp.route('/admin/music')
@admin_music_bp.route('/admin/music/<tab>')
@require_permission('music', 'read')
def admin_music(tab=None):
    valid_tabs = ['player', 'options']
    tab = request.args.get('tab', 'player')
    if tab not in valid_tabs:
        tab = 'player'

    # Vérifier les permissions pour chaque section
    can_write = any(role.has_permission('music', 'write') for role in current_user.roles)
    
    token_info, authorized = get_spotify_token()
    spotify_connected = authorized
    print("spotify", spotify_connected)
    if spotify_connected:
        sp = spotipy.Spotify(auth=token_info['access_token'])
        playlists = sp.current_user_playlists()

        return render_template('/admin/music.html',
                                music_spotify = app.config["MUSIC_SPOTIFY"],
                                music_spotify_user = app.config["MUSIC_SPOTIFY_USER"],
                                music_spotify_key = app.config["MUSIC_SPOTIFY_KEY"],
                                music_volume = app.config["MUSIC_VOLUME"],
                                music_announce_volume = app.config["MUSIC_ANNOUNCE_VOLUME"],
                                music_announce_action = app.config["MUSIC_ANNOUNCE_ACTION"],
                                spotify_connected=spotify_connected,
                                track_infos = get_spotify_current_track_info(),
                                playlists=playlists['items'],
                                active_tab=tab,
                                can_write=can_write)

    else:
        return render_template('/admin/music.html',
                                music_spotify = app.config["MUSIC_SPOTIFY"],
                                music_spotify_user = app.config["MUSIC_SPOTIFY_USER"],
                                music_spotify_key = app.config["MUSIC_SPOTIFY_KEY"],
                                music_volume = app.config["MUSIC_VOLUME"],
                                music_announce_volume = app.config["MUSIC_ANNOUNCE_VOLUME"],
                                music_announce_action = app.config["MUSIC_ANNOUNCE_ACTION"],
                                spotify_connected=spotify_connected,
                                playlists=[],
                                active_tab=tab,
                                can_write=can_write)
    


def get_spotify_oauth():
    cache_handler = SpotifyFlaskCacheHandler(session_key='token_info')
    return SpotifyOAuth(
        client_id="d061eca61b9b475dbffc3a15c57d6b5e",
        client_secret="401f14a3f95e4c7fad1c525dfed3c808",
        redirect_uri=url_for('admin_music.spotify_callback', _external=True),
        scope="user-library-read user-read-playback-state user-modify-playback-state streaming",
        cache_handler=cache_handler
    )

def spotify_authorized():
    print("spotify_authorized", app.config["MUSIC_SPOTIFY_USER"], app.config["MUSIC_SPOTIFY_KEY"])
    return SpotifyOAuth(client_id="d061eca61b9b475dbffc3a15c57d6b5e",
                            client_secret = "401f14a3f95e4c7fad1c525dfed3c808",
                            redirect_uri=url_for('admin_music.spotify_callback', _external=True),
                            scope='user-library-read user-read-playback-state user-modify-playback-state streaming')

@admin_music_bp.route('/spotify/login')
def spotify_login():
    # Initialiser le flux OAuth avec le cache personnalisé
    sp_oauth = get_spotify_oauth()
    auth_url = sp_oauth.get_authorize_url()
    communikation("update_screen", event="spotify_status", data=True)
    # Rediriger l'utilisateur vers l'URL d'autorisation
    return redirect(auth_url)


def clear_spotify_tokens():
    # Supprimez les informations de token de la session
    session.pop('token_info', None)
    session.modified = True

@admin_music_bp.route('/spotify/logout')
def spotify_logout():
    sp_oauth = get_spotify_oauth()
    sp_oauth.cache_handler.delete_token_from_cache()
    communikation("update_screen", event="spotify_status", data=False)
    return redirect(url_for('admin_music.admin_music'))

@admin_music_bp.route('/spotify/callback')
def spotify_callback():
    sp_oauth = get_spotify_oauth()

    # Obtenir le code de l'URL de redirection
    code = request.args.get('code')

    try:
        # Echanger le code contre un token d'accès
        token_info = sp_oauth.get_access_token(code)
        # Stocker le token dans la session via le cache handler
        session['token_info'] = token_info
        session.modified = True
    except SpotifyOauthError as e:
        print(f"Error obtaining token: {e}")
        return redirect(url_for('admin_music.error_page'))

    return redirect(url_for('admin_music.admin_music'))  # Rediriger vers votre page d'administration ou autre

@admin_music_bp.route('/show_saved_tracks')
def show_saved_tracks():
    token_info = session.get('token_info', None)
    if not token_info:
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
@spotify_exception_handler
def pause_music():
    sp = get_spotipy()
    sp.pause_playback()
    return '', 204

@admin_music_bp.route('/spotify/resume_music', methods=['GET'])
@spotify_exception_handler
def resume_music():
    sp = get_spotipy()
    sp.start_playback()
    return '', 204

@admin_music_bp.route('/spotify/next_track', methods=['GET'])
@spotify_exception_handler
def next_track():
    sp = get_spotipy()
    sp.next_track()
    return '', 204

@admin_music_bp.route('/spotify/previous_track', methods=['GET'])
@spotify_exception_handler
def previous_track():
    sp = get_spotipy()
    sp.previous_track()
    return '', 204


@admin_music_bp.route('/spotify/change_volume', methods=['POST'])
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

@admin_music_bp.route('/spotify/start_announce', methods=['GET'])
def start_announce_music():
    if app.config["MUSIC_ANNOUNCE_ACTION"] == "pause":
        return pause_music()
    elif app.config["MUSIC_ANNOUNCE_ACTION"] == "down":
        return set_volume(app.config["MUSIC_ANNOUNCE_VOLUME"])

@admin_music_bp.route('/spotify/stop_announce', methods=['GET'])
def stop_announce_music():
    if app.config["MUSIC_ANNOUNCE_ACTION"] == "pause":
        return resume_music()
    elif app.config["MUSIC_ANNOUNCE_ACTION"] == "down":
        return set_volume(app.config["MUSIC_VOLUME"])

@admin_music_bp.route('/spotify/play_playlist', methods=['POST'])
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

@admin_music_bp.route('/admin/music/save_options', methods=['POST'])
@require_permission('music', 'write')
def save_music_options():
    """Sauvegarde les options de musique"""
    try:
        # Code existant...
        pass
    except Exception as e:
        print(f"Erreur lors de la sauvegarde des options de musique : {e}")
        return '', 500

def get_spotify_token():
    token_info = session.get('token_info', None)
    if not token_info:
        return None, False

    now = int(tm.time())
    is_token_expired = token_info['expires_at'] - now < 60

    if is_token_expired:
        try:
            sp_oauth = SpotifyOAuth(
        client_id = app.config["MUSIC_SPOTIFY_USER"],
        client_secret = app.config["MUSIC_SPOTIFY_KEY"],
        redirect_uri=url_for('admin_music.spotify_callback', _external=True),
        scope='user-library-read user-read-playback-state user-modify-playback-state streaming'
    )
            token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
            session['token_info'] = token_info
        except SpotifyOauthError as e:
            print(f"Error refreshing token: {e}")
            #clear_spotify_tokens()
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
def dashboard_music():
    token_info, authorized = get_spotify_token()
    dashboardcard = DashboardCard.query.filter_by(name="player").first()
    print("spotify", authorized)
    if authorized:
        track_infos = get_spotify_current_track_info()
        return render_template('/admin/dashboard_player.html',
                                spotify_connected=authorized,
                                track_infos=track_infos,
                                dashboardcard=dashboardcard)
    else:
        return render_template('/admin/dashboard_player.html', 
                                spotify_connected=authorized,
                                dashboardcard=dashboardcard)