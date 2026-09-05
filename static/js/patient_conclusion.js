// Page de conclusion du parcours patient.
// Extraite du gabarit templates/patient/conclusion_page.html (Phase 8, point 2).
//
// Les libelles d'impression restent transmis par l'ilot de donnees
// <script id="print_ui_labels" type="application/json"> du gabarit : ce n'est
// pas du code, et c'est le bon moyen de passer des donnees serveur au client.

var timerGauge = document.getElementById('timer_gauge');
var prolongBtn = document.getElementById('prolong_btn');
var printBtn = document.getElementById('print_btn');
var cancelBtn = document.getElementById('cancel_btn');
// Duree lue sur la jauge (attribut data-duration) : elle etait auparavant
// interpolee par Jinja directement dans ce script, ce qui l'empechait de
// vivre dans un fichier.
var timerDuration = Number((timerGauge && timerGauge.dataset.duration) || 0); // secondes
var startTime;
var remainingTime = timerDuration * 1000; // Convert to milliseconds
var animationFrame;

function updateTimerGauge() {
    var currentTime = Date.now();
    var elapsedTime = currentTime - startTime;
    var newRemainingTime = remainingTime - elapsedTime;

    if (newRemainingTime <= 0) {
        timerGauge.style.width = '0%';
        cancelAnimationFrame(animationFrame);
        goToCancelPatient();
    } else {
        var widthPercent = (newRemainingTime / (timerDuration * 1000)) * 100;
        timerGauge.style.width = widthPercent + '%';
        animationFrame = requestAnimationFrame(updateTimerGauge);
    }
}

function startTimer() {
    startTime = Date.now();
    animationFrame = requestAnimationFrame(updateTimerGauge);
}

function resetTimer() {
    remainingTime = timerDuration * 1000; // Reset remaining time
    startTime = Date.now();
    cancelAnimationFrame(animationFrame);
    updateTimerGauge();
}

function goToCancelPatient() {
    // Effectue la requête HTMX même sans rechargement
    htmx.ajax('GET', '/patient/cancel_patient', {
        target: '#div_buttons_parents'
    });
}

function stopTimer() {
    cancelAnimationFrame(animationFrame);
}

prolongBtn.addEventListener('click', function() {
    resetTimer();
});

printBtn.addEventListener('click', function() {
    resetTimer();
    handlePrintButtonClick()
});

cancelBtn.addEventListener('click', function() {
    stopTimer();
});

// Contrôles du minuteur exposés à patients.js (flux d'impression). Le retour
// à l'accueil ne doit se produire qu'après succès ou décision du patient.
window.__conclusionTimer = {
    start: function() { resetTimer(); },
    stop: stopTimer,
    goHome: goToCancelPatient
};

// Démarrage du minuteur : SAUF si une impression est en attente de
// confirmation. Dans ce cas, patients.js (runPrintFlow) le (re)démarrera
// seulement après un succès ou une décision explicite.
(function() {
    var pd = document.getElementById('print_data');
    var printPending = pd
        && pd.getAttribute('print-ticket') !== 'False'
        && pd.getAttribute('data-print-job-id');
    if (!printPending) {
        startTimer();
    }
})();


function handlePrintButtonClick() {
    // Récupérer les données d'impression
    var printDataElement = document.getElementById('print_data');
    if (!printDataElement) {
        console.error("L'élément print_data est introuvable.");
        return;
    }
    var printData = printDataElement.getAttribute('data-print');

    // sendPrintTicket est défini dans patients.js et constitue le point
    // d'entrée UNIQUE partagé avec la première impression. On l'utilise ici
    // pour la réimpression (l'ancien appel window.pywebview.api.print_ticket
    // était erroné : l'API est exposée sous ...api.printer.print_ticket).
    if (typeof sendPrintTicket !== 'function') {
        console.error("La fonction d'impression centralisée est indisponible.");
        return;
    }

    // Protection contre les clics répétés : on neutralise visuellement le
    // bouton le temps de l'impression pour éviter les doubles tickets.
    if (printBtn.dataset.printing === 'true') {
        return;
    }
    printBtn.dataset.printing = 'true';
    printBtn.style.pointerEvents = 'none';
    printBtn.style.opacity = '0.6';

    sendPrintTicket(printData).finally(function() {
        printBtn.dataset.printing = 'false';
        printBtn.style.pointerEvents = '';
        printBtn.style.opacity = '';
    });
}
