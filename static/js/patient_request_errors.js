// Échec d'une requête de la page patient : panne réseau ou erreur du serveur ?
//
// Toute réponse en erreur affichait l'écran « connexion perdue » : une
// erreur 500 (bug, bouton mal configuré) faisait croire au patient que la
// borne avait perdu le réseau, et l'écran restait là sans issue tant que le
// socket était ouvert. Désormais :
//
// - 'network' : rien n'est revenu (envoi échoué, délai dépassé) ou le proxy
//   signale le serveur absent (502/503/504) -> écran « connexion perdue »
//   habituel, qui disparaît seul au retour du réseau ;
// - 'server'  : le serveur a répondu une erreur -> message « Un problème est
//   survenu » avec « Retour à l'accueil », et retour automatique après
//   KIOSK_SERVER_ERROR_HOME_MS ;
// - 'ignore'  : rafraîchissement du titre, déclenché par le serveur et non
//   par le patient — son échec ne doit pas interrompre un parcours.
//
// Utilise setKioskOffline / kioskOfflineLabels / printFlowActive
// (patients.js) et PatientIdle.goHome (patient_idle.js), résolus à l'appel.

var KIOSK_NETWORK_STATUSES = [0, 502, 503, 504];
var KIOSK_SERVER_ERROR_HOME_MS = 10000;
var _kioskServerErrorTimer = null;

function kioskFailureKind(evt) {
    if (!evt || evt.type !== 'htmx:responseError') { return 'network'; }
    var detail = evt.detail || {};
    if (detail.elt && detail.elt.id === 'div_title_area') { return 'ignore'; }
    var status = detail.xhr ? detail.xhr.status : 0;
    return KIOSK_NETWORK_STATUSES.indexOf(status) >= 0 ? 'network' : 'server';
}

function clearKioskServerError() {
    if (_kioskServerErrorTimer !== null) {
        clearTimeout(_kioskServerErrorTimer);
        _kioskServerErrorTimer = null;
    }
}

function _leaveKioskServerError() {
    clearKioskServerError();
    setKioskOffline(false);
    // Un écran d'impression a ses propres décisions en cours : on ne le
    // détruit pas, seul le message d'erreur disparaît.
    if (typeof printFlowActive === 'function' && printFlowActive()) { return; }
    if (typeof PatientIdle !== 'undefined' && PatientIdle.goHome) {
        PatientIdle.goHome();
    }
}

function showKioskServerError(evt) {
    var detail = (evt && evt.detail) || {};
    console.error('Erreur du serveur sur la borne :',
                  detail.xhr ? detail.xhr.status : '?',
                  detail.pathInfo ? detail.pathInfo.requestPath : '');
    clearKioskServerError();
    var L = kioskOfflineLabels();
    setKioskOffline(true, {
        title: L.server_title,
        message: L.server_message,
        buttons: [{ label: L.home, onClick: _leaveKioskServerError }]
    });
    _kioskServerErrorTimer = setTimeout(_leaveKioskServerError, KIOSK_SERVER_ERROR_HOME_MS);
}
