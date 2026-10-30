import io
import os
import socket
import time
from urllib.parse import urlparse

from flask import Blueprint, jsonify, render_template, request, send_file, current_app as app
from flask_security import current_user
from routes.admin_security import require_permission, require_permission_dashboard, require_permission_api
from models import ConfigOption, DashboardCard, User, db
from ui_feedback import display_toast
from extensions import socketio, realtime_relay
from sockets import active_connections
from sockets import connected_clients_info
from audit_log import ACTION_CONNECT, ACTION_UPDATE, OUTCOME_SUCCESS, OUTCOME_FAILURE
from audit_service import record_audit
from python.engine import qr_png_bytes
import app_notification_texts
import quick_replies
import config_sync
from account_recovery import normalize_base_url
from mail_settings import (
    BACKEND_BREVO,
    BREVO_ENV_VARIABLE,
    backend_from_config,
    is_configured as mail_is_configured,
    is_valid_email,
    provider_for_host,
    providers_payload,
    security_mode,
    validate_mail_form,
)
from params_registry import PARAM_REGISTRY, column_values_for
from services.mail_service import env_brevo_api_key, send_email

admin_app_bp = Blueprint('admin_app', __name__)

# Point 4 (audit Admin) — Throttling des e-mails de test.
# Limite simple par utilisateur, pour éviter le spam ou l'usage comme relais
# SMTP. Courte : on enchaîne souvent plusieurs essais en réglant la
# messagerie. État en mémoire par process (comme login_guard).
_email_test_last_sent: dict = {}  # {username: timestamp}
_EMAIL_TEST_COOLDOWN = 15.0  # secondes

# Libellés lisibles des canaux temps réel (sockets.NAMESPACES) pour l'onglet
# « Connexions en direct » : l'administrateur voit « Bornes patient », pas
# « /socket_patient ».
NAMESPACE_LABELS = {
    "/socket_admin": "Administration",
    "/socket_patient": "Bornes patient",
    "/socket_update_screen": "Écrans d'annonce",
    "/socket_app_screen": "Écrans (application)",
    "/socket_app_counter": "Application comptoir",
    "/socket_counter": "Comptoirs (version web)",
    "/socket_update_patient": "Suivi de la file (pages admin et comptoirs)",
    "/socket_phone": "Téléphones des patients",
}

def list_admin_themes():
    """Noms des palettes Bootswatch disponibles (static/css/themes/*.css)."""
    themes_path = os.path.join(app.static_folder, 'css/themes')
    return sorted(os.path.splitext(f)[0] for f in os.listdir(themes_path)
                  if f.endswith('.css'))


@admin_app_bp.route('/admin/app')
@admin_app_bp.route('/admin/app/<tab>')
@require_permission('app')
def admin_app(tab=None):
    valid_tabs = ['general', 'notifications', 'backups', 'mail', 'connexion']
    # /admin/app/<tab> était déclaré mais ignoré (seul ?tab= était lu).
    tab = tab or request.args.get('tab', 'general')
    if tab not in valid_tabs:
        tab = 'general'
        
    return render_template('/admin/app.html',
                            active_tab=tab,
                            # Relais inter-processus (RabbitMQ) : plus de
                            # switch applicatif — auto-détecté depuis
                            # RABBITMQ_URL (extensions.init_socketio). On
                            # affiche l'état RÉEL du processus, pas la config.
                            rabbitmq_relay=realtime_relay(),
                            rabbitmq_url_set=bool(
                                app.config.get("RABBITMQ_URL")),
                            app_messaging_enabled=app.config.get("APP_MESSAGING_ENABLED", False),
                            app_quick_replies="\n".join(
                                quick_replies.team_phrases(app.config)),
                            quick_replies_max=quick_replies.MAX_TEAM,
                            quick_replies_max_length=quick_replies.MAX_LENGTH,
                            call_auto_validation_enabled=app.config.get(
                                "CALL_AUTO_VALIDATION_ENABLED", False),
                            call_auto_validation_warning_seconds=app.config.get(
                                "CALL_AUTO_VALIDATION_WARNING_SECONDS", 60),
                            call_auto_validation_grace_seconds=app.config.get(
                                "CALL_AUTO_VALIDATION_GRACE_SECONDS", 30),
                            call_auto_validation_extension_seconds=app.config.get(
                                "CALL_AUTO_VALIDATION_EXTENSION_SECONDS", 60),
                            app_notification_groups=app_notification_texts.admin_groups(app.config),
                            stress_test_enabled=app.config.get("STRESS_TEST_ENABLED", False),
                            stress_test_mode=app.config.get("STRESS_TEST_MODE", "disabled"),
                            stress_reference_patients=app.config.get(
                                "STRESS_REFERENCE_PATIENTS", 10),
                            network_adress = app.config["NETWORK_ADRESS"],
                            numbering_by_activity = app.config["NUMBERING_BY_ACTIVITY"], 
                            announce_sound = app.config["ANNOUNCE_SOUND"],
                            pharmacy_name = app.config["PHARMACY_NAME"],
                            admin_colors=app.config.get("ADMIN_COLORS", "lumen"),
                            themes=list_admin_themes(),
                            **_mail_template_context(),
                            namespaces=[(ns, NAMESPACE_LABELS.get(ns, ns))
                                        for ns in active_connections.keys()]
    )


def _split_host_port(host):
    """(nom d'hôte, port) depuis ``request.host`` — gère ``[::1]:5000``."""
    host = host or ""
    if host.startswith("[") and "]" in host:
        name, _, rest = host.partition("]")
        return name + "]", rest.lstrip(":")
    name, sep, port = host.partition(":")
    return name, port if sep else ""


def _is_loopback_host(hostname):
    h = (hostname or "").strip().lower()
    return (not h) or h.startswith("127.") or h in ("localhost", "::1", "[::1]")


def _detect_lan_ipv4():
    """IPv4 locale de l'interface de sortie, ou ``None``.

    Astuce standard : ``connect()`` UDP vers TEST-NET-1 (192.0.2.1) n'émet
    aucun trafic mais force le noyau à choisir l'interface de sortie, dont
    ``getsockname()`` donne alors l'adresse.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 80))
        ip = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    if not ip or _is_loopback_host(ip):
        return None
    return ip


@admin_app_bp.route('/admin/app/network_adress/suggest')
@require_permission_api('app')
def admin_app_network_adress_suggest():
    """Propose une valeur pour le réglage ``network_adress`` (onglet Général)."""
    # L'administrateur accède déjà au serveur par une adresse joignable depuis
    # le réseau : c'est la meilleure réponse possible.
    hostname, host_port = _split_host_port(request.host)
    if not _is_loopback_host(hostname):
        return jsonify(url=request.host_url, source="adresse actuelle")

    ip = _detect_lan_ipv4()
    if not ip:
        return jsonify(error="Adresse réseau introuvable"), 404

    port = host_port or app.config.get("PORT", 5000)
    scheme = "https" if request.is_secure else "http"
    return jsonify(url=f"{scheme}://{ip}:{port}", source="détectée")


@admin_app_bp.route('/admin/app/network_adress/qrcode.png')
@require_permission_api('app')
def admin_app_network_adress_qrcode():
    """QR de TEST encodant l'URL fournie : jamais contactée (pas de SSRF),
    la validation évite seulement de générer un code inutilisable."""
    url = request.args.get("url", "")
    parsed = urlparse(url) if 0 < len(url) <= 500 else None
    if parsed is None or parsed.scheme not in ("http", "https") or not parsed.netloc:
        return jsonify(error="URL invalide"), 400
    return send_file(io.BytesIO(qr_png_bytes(url)), mimetype="image/png")


# ---------------------------------------------------------------------------
# Onglet « E-mails » : un seul formulaire (mail_settings.py) — préréglage du
# fournisseur, test AVANT enregistrement, enregistrement groupé.
# ---------------------------------------------------------------------------

def _accounts_without_email():
    """Comptes actifs sans adresse valide (ne pourront pas utiliser le lien)."""
    users = User.query.filter(User.active.is_(True)).all()
    return sorted(u.username for u in users if not is_valid_email(u.email or ""))


def _mail_template_context():
    config = app.config
    host = config.get("MAIL_SERVER") or ""
    provider = provider_for_host(host)
    env_key = env_brevo_api_key()
    return {
        "mail_backend": backend_from_config(config),
        # Secrets : seulement « défini / non défini », jamais la valeur.
        "mail_brevo_key_set": bool((config.get("MAIL_BREVO_API_KEY") or "").strip()),
        "mail_brevo_key_from_env": bool(env_key),
        "mail_brevo_env_variable": BREVO_ENV_VARIABLE,
        "mail_server": host,
        "mail_port": config.get("MAIL_PORT") or "",
        "mail_username": config.get("MAIL_USERNAME") or "",
        # Secret : on ne transmet JAMAIS la valeur au gabarit, seulement
        # l'information « défini / non défini ».
        "mail_password_set": bool(config.get("MAIL_PASSWORD")),
        "mail_default_sender": config.get("MAIL_DEFAULT_SENDER") or "",
        "mail_security": security_mode(config.get("MAIL_USE_TLS"), config.get("MAIL_USE_SSL")),
        "mail_provider": provider.id if provider else ("other" if host else ""),
        "mail_providers": providers_payload(),
        "mail_configured": mail_is_configured(config, env_key),
        "mail_password_reset": bool(config.get("MAIL_PASSWORD_RESET", True)),
        # Préremplie avec l'adresse par laquelle l'administrateur consulte la
        # page : c'est en général la bonne adresse publique.
        "mail_link_base_url": (normalize_base_url(config.get("MAIL_LINK_BASE_URL"))
                               or normalize_base_url(request.host_url) or ""),
        "mail_accounts_without_email": _accounts_without_email(),
    }


def _mail_result(success, message):
    """Fragment de résultat affiché sous le formulaire (zone aria-live)."""
    return render_template("admin/app_mail_result.html", success=success, message=message)


def _validated_mail_form():
    stored_key = (app.config.get("MAIL_BREVO_API_KEY") or "").strip() or env_brevo_api_key()
    return validate_mail_form(request.form,
                              stored_password=app.config.get("MAIL_PASSWORD") or "",
                              stored_api_key=stored_key)


@admin_app_bp.route('/admin/app/mail/test', methods=['POST'])
@require_permission_api('app')
def admin_app_mail_test():
    """Envoie un e-mail de test avec les valeurs DU FORMULAIRE (non enregistrées).

    Mot de passe ou clé laissés vides = ceux qui sont enregistrés.
    """
    mail_adress = (request.values.get('mail_adress') or '').strip()
    if not mail_adress:
        return _mail_result(False, "Indiquez l'adresse qui doit recevoir l'e-mail de test.")
    if not is_valid_email(mail_adress):
        return _mail_result(False, "L'adresse de destination n'est pas valide.")

    settings, errors = _validated_mail_form()
    if errors:
        return _mail_result(False, " ".join(errors))

    # Point 4 — Throttling par utilisateur.
    username = getattr(current_user, "username", "unknown")
    now = time.monotonic()
    remaining = _EMAIL_TEST_COOLDOWN - (now - _email_test_last_sent.get(username, 0.0))
    if remaining > 0:
        app.logger.warning("Test email throttled pour %s (%.0fs restantes)", username, remaining)
        return _mail_result(False, f"Patientez {int(remaining) + 1} s avant un nouveau test.")
    _email_test_last_sent[username] = now

    pharmacy = app.config.get("PHARMACY_NAME") or "Flowelis"
    via = ("Brevo (API)" if settings.backend == BACKEND_BREVO
           else f"{settings.host}:{settings.port}")
    success, error_msg = send_email(
        mail_adress,
        f"Test d'envoi — {pharmacy}",
        "Cet e-mail confirme que l'envoi depuis la file d'attente fonctionne.\n\n"
        f"Envoi : {via}\nExpéditeur : {settings.sender}\n",
        settings=settings,
    )
    record_audit(ACTION_CONNECT, "email", target_id=mail_adress,
                 outcome=OUTCOME_SUCCESS if success else OUTCOME_FAILURE,
                 details=f"via={via}")
    if success:
        return _mail_result(True, f"E-mail envoyé à {mail_adress}. Vérifiez sa réception "
                                  "(pensez au dossier « indésirables »), puis enregistrez.")
    return _mail_result(False, error_msg)


@admin_app_bp.route('/admin/app/mail/save', methods=['POST'])
@require_permission_api('app')
def admin_app_mail_save():
    """Enregistre tout le formulaire en une transaction (registre des paramètres)."""
    settings, errors = _validated_mail_form()
    base_url_raw = (request.form.get("link_base_url") or "").strip()
    base_url = normalize_base_url(base_url_raw)
    if base_url_raw and base_url is None:
        errors = [*errors, "L'adresse des liens doit commencer par http:// ou https://."]
    if errors:
        return _mail_result(False, " ".join(errors))

    # Seuls les réglages du mode choisi sont écrits : ceux de l'autre mode
    # restent en place (revenir en arrière ne fait rien perdre).
    values = {
        "mail_backend": settings.backend,
        "mail_default_sender": settings.sender,
        "mail_link_base_url": base_url or "",
    }
    if settings.backend == BACKEND_BREVO:
        # Secret : réécrit seulement si une nouvelle clé a été collée.
        if (request.form.get("api_key") or "").strip():
            values["mail_brevo_api_key"] = settings.api_key
    else:
        values.update({
            "mail_server": settings.host,
            "mail_port": settings.port,
            "mail_username": settings.username,
            "mail_use_tls": settings.use_tls,
            "mail_use_ssl": settings.use_ssl,
        })
        # Secret : réécrit seulement si un nouveau mot de passe a été saisi.
        if (request.form.get("password") or "").strip():
            values["mail_password"] = settings.password

    try:
        for key, value in values.items():
            option = ConfigOption.query.filter_by(config_key=key).first()
            if option is None:
                option = ConfigOption(config_key=key)
                db.session.add(option)
            for column, column_value in column_values_for(key, value).items():
                setattr(option, column, column_value)
        # Les autres processus (répliques web, scheduler) rechargent la config.
        config_sync.bump_generation()
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        app.logger.error("Échec d'enregistrement des réglages e-mail : %s", type(exc).__name__)
        record_audit(ACTION_UPDATE, "config", target_id="mail", outcome=OUTCOME_FAILURE)
        return _mail_result(False, "L'enregistrement a échoué.")

    for key, value in values.items():
        app.config[PARAM_REGISTRY[key].config_name] = value
    if settings.backend == BACKEND_BREVO:
        details = "backend=brevo" + (" (clé modifiée)" if "mail_brevo_api_key" in values else "")
    else:
        details = (f"backend=smtp host={settings.host} port={settings.port} "
                   f"security={settings.security}"
                   + (" (mot de passe modifié)" if "mail_password" in values else ""))
    record_audit(ACTION_UPDATE, "config", target_id="mail", outcome=OUTCOME_SUCCESS,
                 details=details)
    return _mail_result(True, "Réglages enregistrés.")


@admin_app_bp.route('/admin/communication/dashboard')
@require_permission_dashboard('app')
def dashboard_communication():
    # Résumé compact : nombre de connexions par type d'appareil. Le détail
    # (qui, filtres) est dans l'onglet Application › Connexions en direct.
    # Auparavant la carte incluait ce même onglet, dont la liste attend
    # l'ouverture d'un onglet absent du tableau de bord : elle ne se chargeait
    # jamais.
    dashboardcard = DashboardCard.query.filter_by(name="connection").first()
    rows = [{"label": NAMESPACE_LABELS.get(ns, ns), "count": len(get_connected_clients(ns))}
            for ns in active_connections.keys()]
    return render_template('/admin/dashboard_connection.html',
                            dashboardcard=dashboardcard,
                            rows=rows,
                            total=sum(r["count"] for r in rows))


@admin_app_bp.route('/admin/app/get_connections', methods=['POST'])
@require_permission_api('app')
def get_connections():
    # on récupere les namespaces selectionnes, si aucun : on les affiche tous
    selected_namespaces = request.form.getlist('namespaces[]')
    if len(selected_namespaces) == 0:
        selected_namespaces = list(active_connections.keys())

    # Seuls les canaux connus sont interrogés (valeurs issues du formulaire).
    selected_namespaces = [ns for ns in selected_namespaces if ns in active_connections]

    connections = []
    for namespace in selected_namespaces:
        connections.append({
            "namespace": namespace,
            "label": NAMESPACE_LABELS.get(namespace, namespace),
            "clients": get_connected_clients(namespace),
        })

    return render_template('admin/app_connexion_list.html', connections=connections,
                           total=sum(len(c["clients"]) for c in connections))


def get_connected_clients(namespace):
    sids = socketio.server.manager.rooms.get(namespace, {}).get(None, set())
    connected = []
    for sid in sids:
        username = connected_clients_info.get(sid, {}).get('username', 'Unknown')
        connected.append({'sid': sid, 'username': username})
    return connected
