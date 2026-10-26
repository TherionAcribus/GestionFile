// Retour automatique à l'accueil de la borne après inactivité.
//
// Seul l'écran de conclusion avait un minuteur : un patient qui partait sur
// l'écran de validation (QR + « Imprimer »), sur les sous-activités ou après
// avoir choisi une autre langue laissait la borne figée. Le suivant tombait
// sur le choix du précédent — et pouvait scanner son QR, donc s'inscrire
// dans la mauvaise activité.
//
// Sans contact pendant le délai réglé (data-idle-timer du <body>, en
// secondes), la page revient à l'accueil EN FRANÇAIS : même requête que le
// drapeau français, qui réaffiche tout #main (titre, boutons, sous-titre).
// Le QR abandonné disparaît ; rien n'est inscrit en base à ce stade.
//
// Jamais pendant un flux d'impression (ses écrans ont leurs propres délais),
// sur la conclusion (son propre minuteur, réglé à part), sous l'écran
// « connexion perdue » ou pendant une requête : on réessaie peu après.

var PatientIdle = (function () {
    'use strict';

    // Requêtes dont la réponse affiche l'accueil dans #div_buttons_parents.
    // Tout autre écran central (y compris un chemin inconnu) compte comme
    // « hors accueil » : en cas de doute, mieux vaut revenir à l'accueil.
    var HOME_PATHS = ['/patient/patient_buttons', '/patient/patient_cancel',
                      '/patient/cancel_patient'];
    var HOME_LANGUAGE = 'fr';
    var RETRY_MS = 5000;
    var ACTIVITY_EVENTS = ['pointerdown', 'touchstart', 'mousedown', 'keydown'];

    var _timer = null;
    var _awayFromHome = false;

    function delayMs() {
        var body = document.body;
        var seconds = Number(body && body.getAttribute('data-idle-timer'));
        return (isFinite(seconds) && seconds > 0) ? seconds * 1000 : 0;
    }

    function requestPath(detail) {
        var path = (detail && detail.pathInfo && detail.pathInfo.requestPath)
            || (detail && detail.requestConfig && detail.requestConfig.path) || '';
        path = String(path).split('?')[0];
        return path.charAt(0) === '/' ? path : '/' + path;
    }

    function isHome() {
        var lang = (document.documentElement.lang || HOME_LANGUAGE).toLowerCase();
        return !_awayFromHome && lang === HOME_LANGUAGE;
    }

    // Écrans qui gèrent eux-mêmes leur sortie, ou moment mal choisi.
    function busy() {
        if (typeof printFlowActive === 'function' && printFlowActive()) { return true; }
        if (document.getElementById('timer_gauge')) { return true; }
        var offline = document.getElementById('kiosk_offline_overlay');
        if (offline && !offline.hidden) { return true; }
        return typeof _htmxPending === 'number' && _htmxPending > 0;
    }

    function arm(ms) {
        if (_timer !== null) {
            clearTimeout(_timer);
            _timer = null;
        }
        if (ms > 0) { _timer = setTimeout(onIdle, ms); }
    }

    function restart() { arm(delayMs()); }

    function goHome() {
        document.documentElement.lang = HOME_LANGUAGE;
        htmx.ajax('GET', '/patient/change_language/' + HOME_LANGUAGE, {
            target: '#main', select: '#main', swap: 'outerHTML'
        });
    }

    function onIdle() {
        _timer = null;
        if (isHome()) { return; }
        if (busy()) {
            arm(RETRY_MS);
            return;
        }
        goHome();
    }

    function onAfterSwap(evt) {
        var target = evt && evt.detail && evt.detail.target;
        if (target && target.id === 'div_buttons_parents') {
            _awayFromHome = HOME_PATHS.indexOf(requestPath(evt.detail)) < 0;
        }
        // Nouvel écran : le patient dispose du délai complet pour le lire.
        restart();
    }

    function init() {
        ACTIVITY_EVENTS.forEach(function (name) {
            document.addEventListener(name, restart, { capture: true, passive: true });
        });
        document.body.addEventListener('htmx:afterSwap', onAfterSwap);
        restart();
    }

    return { init: init, isHome: isHome, onIdle: onIdle };
})();

document.addEventListener('DOMContentLoaded', PatientIdle.init);
