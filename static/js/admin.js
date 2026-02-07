document.addEventListener('DOMContentLoaded', (event) => {
    var protocol = window.location.protocol;
    var socketProtocol = protocol === 'https:' ? 'wss://' : 'ws://';
    var domain = document.domain;
    var port = protocol === 'https:' ? '443' : '5000';
    
    // Connexion au namespace général
    var generalSocket = io.connect(socketProtocol + domain + ':' + port + '/socket_update_patient', 
                                    { query: "username=admin_interface" });
    console.log("adresse")
    console.log(socketProtocol + domain + ':' + port + '/socket_update_patient')

    generalSocket.on('connect', function() {
        console.log('General WebSocket connected');
    });

    generalSocket.on('disconnect', function() {
        console.log('General WebSocket disconnected');
    });

    generalSocket.on('update', function(msg) {
        console.log("Received general message:", msg.flag);
        refresh_queue();
    });

    generalSocket.on('connect_error', function(err) {
        console.error('General WebSocket connection error:', err);
    });

    generalSocket.on('reconnect', function(attempt) {
        console.log('General WebSocket reconnected after', attempt, 'attempts');
    });

    generalSocket.on('reconnect_attempt', function(attempt) {
        console.log('General WebSocket reconnect attempt', attempt);
    });

    generalSocket.onAny((event, ...args) => {
        console.log(`General WebSocket Event: ${event}`, args);
    });

    // Connexion au namespace écran
    var adminSocket = io.connect(socketProtocol + domain + ':' + port + '/socket_admin',
                    { query: "username=admin_interface" }
    );

    adminSocket.on('connect', function() {
        console.log('Admin WebSocket connected');
    });

    adminSocket.on('disconnect', function() {
        console.log('Admin WebSocket disconnected');
    });

    adminSocket.on('update', function(msg) {
        console.log("Received Admin message:", msg);
        display_toast(msg);
    });

    adminSocket.on("refresh_activity_table", function(msg) {
        console.log("refresh_activity_table:", msg);
        refresh_activity_table();
        refresh_activity_staff_table();
    })

    adminSocket.on("refresh_button_order", function(msg) {
        console.log("refresh_button_order:", msg);
        refresh_button_order();
    })

    adminSocket.on("refresh_counter_order", function(msg) {
        console.log("refresh_counter_order:", msg);
        refresh_counter_order();
    })

    adminSocket.on("refresh_languages_order", function(msg) {
        console.log("refresh_languages_order:", msg);
        refresh_languages_order();
    })

    adminSocket.on("refresh_sound", function(msg) {
        console.log("refresh_sound:", msg);
        refresh_sound();
    })

    adminSocket.on("refresh_colors", function(msg) {
        console.log("refresh_colors:", msg);
        refresh_page();
    })

    adminSocket.on("refresh_dashboard_select", function(msg) {
        console.log("refresh_dashboard_select:", msg);
        refresh_dashboard_select();
    })

    adminSocket.on("display_new_gallery", function(msg) {
        console.log("display_new_gallery:", msg);
        document.getElementById("name").value = ""
        display_new_gallery(msg);
    })

    adminSocket.on("refresh_gallery_list", function(msg) {
        console.log("refresh_gallery_list:", msg);
        refresh_gallery_list(msg);
    })

    adminSocket.on("refresh_schedule_tasks_list", function(msg) {
        console.log("refresh_schedule_tasks_list:", msg);
        refresh_schedule_tasks_list(msg);
    })

    adminSocket.on("refresh_printer_dashboard", function(msg) {
        console.log("refresh_printer_dashboard:", msg);
        refresh_printer_dashboard(msg);
    })

    adminSocket.on("refresh_counter_dashboard", function(msg) {
        console.log("refresh_counter_dashboard:", msg);
        refresh_counter_dashboard(msg);
    })

    adminSocket.on("audio_test", function(msg) {
        console.log("audio_test:", msg);
        playAudio(msg);
    })


    adminSocket.on('connect_error', function(err) {
        console.error('Admin WebSocket connection error:', err);
    });

    adminSocket.on('reconnect', function(attempt) {
        console.log('Admin WebSocket reconnected after', attempt, 'attempts');
    });

    adminSocket.on('reconnect_attempt', function(attempt) {
        console.log('Admin WebSocket reconnect attempt', attempt);
    });

    adminSocket.onAny((event, ...args) => {
        console.log(`Admin WebSocket Event: ${event}`, args);
    });
});


// -------------- TABS BOOTSTRAP  --------------

document.addEventListener('DOMContentLoaded', function() {
    // Fonction pour activer un onglet
    function activateTab(tabId) {
        var tabElement = document.querySelector('#' + tabId + '-tab');
        if (tabElement) {
            var tab = new bootstrap.Tab(tabElement);
            tab.show();
            return true;
        }
        return false;
    }

    // Fonction pour obtenir le paramètre 'tab' de l'URL
    function getTabFromUrl() {
        var urlParams = new URLSearchParams(window.location.search);
        return urlParams.get('tab') || getDefaultTab();
    }

    // Fonction pour obtenir l'ID du premier onglet disponible (onglet par défaut)
    function getDefaultTab() {
        var firstTab = document.querySelector('button[data-bs-toggle="tab"]');
        return firstTab ? firstTab.id.replace('-tab', '') : null;
    }

    // Fonction pour mettre à jour l'URL
    function updateUrl(tabId) {
        var url = new URL(window.location);
        url.searchParams.set('tab', tabId);
        history.pushState({tabId: tabId}, '', url);
    }

    // Activer l'onglet initial ou le premier onglet disponible
    var initialTab = getTabFromUrl();
    if (!activateTab(initialTab)) {
        initialTab = getDefaultTab();
        if (initialTab) {
            activateTab(initialTab);
        }
    }

    // Ajouter des écouteurs d'événements pour les clics sur les onglets
    document.querySelectorAll('button[data-bs-toggle="tab"]').forEach(function(tabEl) {
        tabEl.addEventListener('shown.bs.tab', function(event) {
            var id = event.target.id.replace('-tab', '');
            updateUrl(id);
        });
    });

    // Gérer les événements de navigation (boutons précédent/suivant du navigateur)
    window.addEventListener('popstate', function(event) {
        var tabId = getTabFromUrl();
        if (!activateTab(tabId)) {
            var defaultTab = getDefaultTab();
            if (defaultTab) {
                activateTab(defaultTab);
            }
        }
    });
});


function display_toast(data) {
    console.log('toast', data);

    // Déterminez la classe à utiliser pour le toast (success ou error)
    let toastClass = data.data.success === true ? 'bg-success text-white' : 'bg-danger text-white';

    // Créez le contenu HTML pour le toast
    let toastHTML = `
        <div class="toast align-items-center ${toastClass} border-0" role="alert" aria-live="assertive" aria-atomic="true">
            <div class="d-flex">
                <div class="toast-body">
                    ${data.data.message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
            </div>
        </div>
    `;

    // Ajoutez le toast au conteneur des toasts
    let toastContainer = document.getElementById('toastContainer');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toastContainer';
        toastContainer.classList.add('toast-container', 'position-fixed', 'top-0', 'end-0', 'p-3');
        document.body.appendChild(toastContainer);
    }
    toastContainer.innerHTML = toastHTML;

    // Initialisez le toast
    let toastElement = toastContainer.querySelector('.toast');
    let toast = new bootstrap.Toast(toastElement);
    toast.show();
}

// -------------- QUEUE  --------------

var eventSource = new EventSource('/events/update_patients');
eventSource.onmessage = function(event) {
    htmx.trigger('#div_queue_table', 'refresh_queue_patient', {target: "#div_queue_table"});
};


function refresh_queue(){
    var queueTable = document.querySelector('#div_queue_table');
    var card_queue = document.querySelector('#card-queue');
    console.log("card_queue", card_queue)

    // Vérifie si div_queue_table existe
    if (queueTable) {
        htmx.trigger(queueTable, 'refresh_queue_patient', {target: "#div_queue_table"});
    }

    // Vérifie si queue_dashboard existe
    if (card_queue) {
        htmx.trigger(card_queue, 'refresh_queue_patient', {target: "#card_queue"});
    }
}

$(document).ready(function() {
    $('#select_patient_filter').select2({
    placeholder: "Patients à afficher",
    allowClear: true
        });
    });
    window.addEventListener("DOMContentLoaded", (e) => {
        $('select').on('select2:select select2:unselect', function (e) {
            // Déclencher manuellement l'événement change pour HTMX
            $(this).closest('select').get(0).dispatchEvent(new Event('change', { bubbles: true }));
        });
    });

// -------------- GALERIES --------------

function display_new_gallery(data) {
    console.log(data);
    
    // Construire l'URL de la galerie
    let url = "/admin/gallery/__NAME__".replace('__NAME__', data.data);
    console.log("URL", url);

    // Utiliser HTMX pour envoyer une requête GET
    htmx.ajax('GET', url, { target: '#content' });
}


function refresh_gallery_list(data) {
    console.log(data);
    htmx.trigger('#galleries_list', 'refresh_gallery_list', {target: "#galleries_list"});
}

// -------------- DASHBOARD --------------

function refresh_dashboard_select(){
    htmx.trigger('#div_select_dashboard', 'refresh_dashboard_select', {target: "#div_select_dashboard"});
}

// -------------- ACTIVITY --------------

function refresh_activity_table(){
    htmx.trigger('#div_activity_table', 'refresh_activity_table', {target: "#div_activity_table"});
}

function refresh_activity_staff_table(){
    htmx.trigger('#div_activity_staff_table', 'refresh_activity_staff_table', {target: "#div_activity_staff_table"});
}


// ---------------- DASHBOARD ----------------

function refresh_printer_dashboard(){
    htmx.trigger('#card-printer', 'refresh_printer_dashboard', {target: "#card-printer"});
}

function refresh_counter_dashboard(){
    htmx.trigger('#card-counter', 'refresh_counter_dashboard', {target: "#card-counter"});
}


// ---------------- BOUTONS ----------------

function submitFile(buttonId) {
    var input = document.getElementById('file-input-' + buttonId);
    var file = input.files[0];
    if (file) {
        var formData = new FormData();
        formData.append('file', file);

        // Pour le débogage, loggez le contenu de FormData
        for (var pair of formData.entries()) {
            console.log(pair[0]+ ', ' + pair[1]); 
        }

        htmx.ajax('POST', '/upload_image/' + buttonId, {
            body: formData,
            headers: {
                'HX-Request': 'true',
                'Content-Type': 'multipart/form-data' // Assurez-vous de ne pas définir explicitement Content-Type
            },
            target: '#button-image-' + buttonId
        });
    } else {
        console.log('No file selected.');
    }
}

function refresh_button_order(){
    htmx.trigger('#order_buttons', 'refresh_buttons_order', {target: "#order_buttons"});
}


function sortable(){
    var el = document.getElementById('list_order_buttons');
    var sortable = Sortable.create(el, {
        animation: 150, // ms, animation speed moving items when sorting, `0` — without animation
        onEnd: function (/**Event*/evt) {
            var itemEl = evt.item;  // dragged HTMLElement
            console.log('New index: ' + evt.newIndex); // index of the new position
            // Vous pouvez ici ajouter une requête pour sauvegarder l'ordre
        }
    });
}

// ---------------- COUNTERS ----------------

function refresh_counter_order(){
    htmx.trigger('#order_counters', 'refresh_counter_order', {target: "#order_counters"});
}

// ---------------- TRANSLATIONS ----------------

function refresh_languages_order(){
    htmx.trigger('#order_languages', 'refresh_languages_order', {target: "#order_languages"});
}


// ---------------- ANNOUNCES ----------------

function refresh_page(){
    location.reload();
}

function refresh_sound(){
    htmx.trigger('#announce_current_signal', 'refresh_sound', {target: "#announce_current_signal"});
}


// ---------------- ANNOUNCES ----------------

function insertPlaceholder(textareaId, text) {
    console.log("Insert placeholder", textareaId, "_", text);
    var textarea = document.getElementById(textareaId);
    var cursorPos = textarea.selectionStart;
    var v = textarea.value;
    var textBefore = v.substring(0, cursorPos);
    var textAfter = v.substring(cursorPos, v.length);

    textarea.value = textBefore + text + textAfter;
    textarea.selectionStart = cursorPos + text.length;
    textarea.selectionEnd = cursorPos + text.length;
    textarea.focus();
}

// permet de recharger la partie 'lecteur' si l'on modifie le fichier dans "Librairie sonore"
htmx.on('htmx:afterSwap', function(evt) {
    // Vérifiez que l'échange concerne bien le contenu de la modale
    if (evt.detail.target.id === 'modal_display_gallery') {
        var closeModalButton = document.getElementById('closeModalButton');
        console.log("Close modal button", closeModalButton);

        if (closeModalButton) {
            closeModalButton.addEventListener('click', function() {
                // Déclencher l'événement personnalisé pour HTMX
                var event = new Event('closeModalEvent');
                console.log("Close modal event dispatched");
                document.getElementById('announce_current_signal').dispatchEvent(event);
            });
        }
    }
    
});


// Créez un élément audio global
let audioPlayer = new Audio();

// Fonction pour jouer l'audio
function playAudio(audioUrl) {
    console.log("Playing audio:", audioUrl);
    audioPlayer.src = audioUrl.data;
    audioPlayer.play().catch(error => {
        console.error("Erreur lors de la lecture audio:", error);
    });
}



// ---------------- TASKS ----------------

function refresh_schedule_tasks_list(data) {
    console.log(data);
    htmx.trigger('#div_schedule_tasks_list', 'refresh_schedule_tasks_list', {target: "#div_schedule_tasks_list"});
}


// ---------------- GENERAL ----------------

// utiliser pour les communications spécifiques du serveur vers l'admin
var eventSource = new EventSource('/events/update_admin_old');
eventSource.onmessage = function(event) {
    data = JSON.parse(event.data);
    if (data.toast){
        //display_toast(data);
    }
    else if (event.data === "schedule_tasks_list"){
        htmx.trigger('#div_schedule_tasks_list', 'refresh_schedule_tasks_list', {target: "#div_schedule_tasks_list"});
    }
    else if (data.action === "delete_add_activity_form"){
        document.getElementById('div_add_activity_form').innerHTML = "";
    }
    else if (data.action === "delete_add_schedule_form"){
        document.getElementById('div_add_schedule_form').innerHTML = "";
    }
    else if (data.action=== "delete_add_staff_form"){
        document.getElementById('div_add_staff_form').innerHTML = "";
    }
    else if (data.action == "delete_add_rule_form"){
        document.getElementById('div_add_rule_form').innerHTML = "";
    }
    else if (data.action == "delete_add_counter_form"){
        document.getElementById('div_add_counter_form').innerHTML = "";
    }
    else if (data.action == "delete_add_button_form"){
        console.log("delete_add_button_form");
        document.getElementById('div_add_button_form').innerHTML = "";
    }
    else if (data.action === "delete_add_activity_form_staff"){
        console.log("delete_add_activity_form_staff");
        document.getElementById('div_add_activity_form_staff').innerHTML = "";
    }
};

// ---------------- COPY COLORS BETWEEN PAGES ----------------

// Mapping des rôles de couleurs entre les pages
// Chaque "page" a un label, une source CSS, et ses variables parentes par rôle
const pageColorRoles = {
    'patient': {
        label: 'Page Patient',
        source: 'patient',
        roles: {
            'main': 'patient_main_color',
            'secondary': 'patient_secondary_color',
            'text': 'patient_third_color',
            'border': 'patient_border_color',
        }
    },
    'announce': {
        label: 'Page Annonce',
        source: 'announce',
        roles: {
            'main': 'announce_main_color',
            'secondary': 'announce_secondary_color',
            'text': 'announce_third_color',
            'border': 'announce_border_color',
        }
    },
    'phone': {
        label: 'Téléphone - Page principale',
        source: 'phone',
        roles: {
            'main': 'phone_main_color',
            'secondary': 'phone_secondary_color',
            'text': 'phone_third_color',
            'border': 'phone_border_color',
        }
    },
    'phone_your_turn': {
        label: 'Téléphone - Votre tour',
        source: 'phone',
        roles: {
            'main': 'phone_your_turn_main_color',
            'text': 'phone_your_turn_third_color',
            'border': 'phone_your_turn_border_color',
        }
    }
};

function showCopyColorsModal(title, message, onConfirm) {
    const modalEl = document.getElementById('modal_copy_colors');
    const modalTitle = document.getElementById('modal_copy_colors_title');
    const modalBody = document.getElementById('modal_copy_colors_body');
    const confirmBtn = document.getElementById('modal_copy_colors_confirm');
    const cancelBtn = document.getElementById('modal_copy_colors_cancel');

    modalTitle.textContent = title;
    modalBody.innerHTML = message;

    if (onConfirm) {
        confirmBtn.style.display = '';
        cancelBtn.textContent = 'Annuler';
        confirmBtn.onclick = function() {
            bootstrap.Modal.getInstance(modalEl).hide();
            onConfirm();
        };
    } else {
        confirmBtn.style.display = 'none';
        cancelBtn.textContent = 'Fermer';
    }

    new bootstrap.Modal(modalEl).show();
}

function copyColorsFromPage(targetPageKey) {
    const selectEl = document.getElementById(`copy_colors_select_${targetPageKey}`);
    if (!selectEl) return;

    const sourcePageKey = selectEl.value;
    if (!sourcePageKey) {
        showCopyColorsModal('Attention', 'Veuillez sélectionner une page source.', null);
        return;
    }

    const sourcePage = pageColorRoles[sourcePageKey];
    const targetPage = pageColorRoles[targetPageKey];
    if (!sourcePage || !targetPage) return;

    // Construire les mappings pour les rôles communs
    const mappings = [];
    for (const role in targetPage.roles) {
        if (sourcePage.roles[role]) {
            const sourceVar = sourcePage.roles[role];
            const targetVar = targetPage.roles[role];
            
            // Récupérer les dépendances depuis colorMappings
            const deps = colorMappings[targetVar] ? colorMappings[targetVar].targets : [];

            mappings.push({
                source_var: sourceVar,
                target_var: targetVar,
                source_source: sourcePage.source,
                target_source: targetPage.source,
                dependencies: deps
            });
        }
    }

    if (mappings.length === 0) {
        showCopyColorsModal('Attention', 'Aucune couleur commune à copier.', null);
        return;
    }

    // Confirmation via modale
    showCopyColorsModal(
        'Confirmation',
        `Copier les couleurs de <strong>"${sourcePage.label}"</strong> vers <strong>"${targetPage.label}"</strong> ?<br><small class="text-muted">Cela écrasera les couleurs actuelles.</small>`,
        function() {
            // Appel au backend
            fetch('/admin/copy_colors', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    source_page: sourcePageKey,
                    target_page: targetPageKey,
                    mappings: mappings
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.status === 'success') {
                    showCopyColorsModal('Succès', 'Couleurs copiées avec succès ! La page va se recharger.', null);
                    setTimeout(() => location.reload(), 1500);
                } else {
                    showCopyColorsModal('Erreur', 'Erreur : ' + (data.message || 'Erreur inconnue'), null);
                }
            })
            .catch(error => {
                console.error('Erreur:', error);
                showCopyColorsModal('Erreur', 'Erreur lors de la copie des couleurs.', null);
            });
        }
    );
}

// ---------------- COLORS PICKERS ----------------  

const colorMappings = {
    'patient_main_color': {
        // Liste des variables qui dépendent de la couleur principale
        targets: [
            'patient_title_background_color',
            'circle_button_color',
            'square_button_color',
            'square_cancel_button_color',
            'validation_button_color',
            'subtitle_background_color',
            'subtitle_specific_message_background_color',
            'subtitle_no_activity_background_color',
            'scan_explanation_background_color',
            'validation_text_background_color',
            'confirmation_text_background_color',
            'circle_button_text_background_color',
            'square_button_text_background_color',
            'square_cancel_button_text_background_color',
            'validation_button_text_background_color',
            // ... autres variables
        ]
    },
    'patient_secondary_color': {
        targets: [
            // ... autres variables
        ]
    },
    'patient_third_color': {
        targets: [
            'patient_title_font_color',
            'subtitle_font_color',
            'subtitle_no_activity_font_color',
            'subtitle_specific_message_font_color',
            'scan_explanation_font_color',
            'validation_text_font_color',
            'confirmation_text_font_color',
            'validation_button_text_color',
            'square_cancel_button_text_color',
            'square_button_text_color',
            'circle_button_text_color',
            // ... autres variables
        ]
    },
    'patient_border_color': {
        targets: [
            'patient_title_border_color',
            'subtitle_border_color',
            'subtitle_no_activity_border_color',
            'subtitle_specific_message_border_color',
            'scan_explanation_border_color',
            'validation_text_border_color',
            'confirmation_text_border_color',
            'circle_button_text_border_color',
            'square_button_text_border_color',
            'square_cancel_button_text_border_color',
            'validation_button_text_border_color',
            // ... autres variables
        ]
    },
    'patient_button_border_color': {
        targets: [
            'circle_button_border_color',
            'square_button_border_color',
            'square_cancel_button_border_color',
            'validation_button_border_color',
            // ... autres variables
        ]
    },
    'announce_main_color':{
        targets : [
            'title_background_color',
            'text_up_background_color',
            'next_patients_background_color',        
        ]
    },
    'announce_third_color': {
        targets: [
            'title_font_color',
            'subtitle_font_color',
            'text_up_font_color',
            'calling_font_color',
            'text_down_font_color',
            'ongoing_font_color',
            'next_patients_font_color',
            // ... autres variables
        ]
    },
    'announce_fourth_color': {
        targets: [
            'text_down_background_color',
            'ongoing_background_color',
            'calling_background_color',
            // ... autres variables
        ]
    },
    'announce_border_color': {
        targets: [
            'title_font_border_color',
            'subtitle_font_border_color',
            'text_up_font_border_color',
            'calling_font_border_color',
            'text_down_font_border_color',
            'ongoing_font_border_color',
            'next_patients_font_border_color',
            // ... autres variables
        ]
    },
    'phone_main_color': {
        targets: [
            'phone_title_background_color',
        ]
    },
    'phone_secondary_color': {
        targets: [
            'phone_line1_background_color',
            'phone_line2_background_color',
            'phone_line3_background_color',
            'phone_line4_background_color',
            'phone_line5_background_color',
            'phone_line6_background_color',
            'phone_specific_message_background_color'
        ]
    },
    'phone_third_color': {
        targets: [
            'phone_line1_font_color',
            'phone_line2_font_color',
            'phone_line3_font_color',
            'phone_line4_font_color',
            'phone_line5_font_color',
            'phone_line6_font_color',
            'phone_specific_message_font_color'
        ]
    },
    'phone_border_color': {
        targets: [
            'phone_title_border_color',
            'phone_line1_border_color',
            'phone_line2_border_color',
            'phone_line3_border_color',
            'phone_line4_border_color',
            'phone_line5_border_color',
            'phone_line6_border_color',
            'phone_specific_message_border_color'
        ]
    },
    'phone_your_turn_main_color': {
        targets: [
            'phone_your_turn_line1_background_color',
            'phone_your_turn_line2_background_color',
            'phone_your_turn_line3_background_color',
            'phone_your_turn_line4_background_color',
            'phone_your_turn_line5_background_color',
            'phone_your_turn_line6_background_color'
        ]
    },
    'phone_your_turn_third_color': {
        targets: [
            'phone_your_turn_line1_font_color',
            'phone_your_turn_line2_font_color',
            'phone_your_turn_line3_font_color',
            'phone_your_turn_line4_font_color',
            'phone_your_turn_line5_font_color',
            'phone_your_turn_line6_font_color'
        ]
    },
    'phone_your_turn_border_color': {
        targets: [
            'phone_your_turn_line1_border_color',
            'phone_your_turn_line2_border_color',
            'phone_your_turn_line3_border_color',
            'phone_your_turn_line4_border_color',
            'phone_your_turn_line5_border_color',
            'phone_your_turn_line6_border_color'
        ]
    },
};

const variableDescriptions = {
    'patient_title_background_color': 'Couleur de fond du titre',
    'circle_button_color': 'Couleur des boutons circulaires',
    'circle_button_text_color': 'Texte bouton circulaire',
    'square_button_color': 'Couleur des boutons carrés',
    'square_cancel_button_color': 'Couleur des boutons d\'annulation',
    'next_patients_background_color': 'Fond des prochains patients',
    'next_patients_font_color': 'Texte des prochains patients',
    'next_patients_font_border_color': 'Contour du texte des prochains patients',
    // patient_third_color (Couleur des textes)
    'patient_title_font_color': 'Texte du titre',
    'subtitle_font_color': 'Texte en bas de la page',
    'subtitle_no_activity_font_color': 'Texte activité inactive',
    'subtitle_specific_message_font_color': 'Texte spécifique activité',
    'scan_explanation_font_color': 'Texte explication Scan QR',
    'validation_text_font_color': 'Texte page de validation',
    'confirmation_text_font_color': 'Texte de confirmation',
    'validation_button_text_color': 'Texte boutons de validation',
    'square_cancel_button_text_color': 'Texte bouton annuler',
    'square_button_text_color': 'Texte bouton rectangulaire',
    // patient_border_color (Couleur des bordures)
    'patient_title_border_color': 'Contour du titre',
    'subtitle_border_color': 'Contour texte bas de page',
    'subtitle_no_activity_border_color': 'Contour texte activité inactive',
    'subtitle_specific_message_border_color': 'Contour texte spécifique activité',
    'scan_explanation_border_color': 'Contour texte explication Scan QR',
    'validation_text_border_color': 'Contour texte page de validation',
    'confirmation_text_border_color': 'Contour texte de confirmation',
    // patient_main_color (Couleur principale) - fonds
    'subtitle_background_color': 'Fond texte bas de page',
    'subtitle_no_activity_background_color': 'Fond texte activité inactive',
    'subtitle_specific_message_background_color': 'Fond texte spécifique activité',
    'scan_explanation_background_color': 'Fond texte explication Scan QR',
    'validation_text_background_color': 'Fond texte page de validation',
    'confirmation_text_background_color': 'Fond texte de confirmation',
    'validation_button_color': 'Couleur bouton de validation',
    // Fond du texte des boutons
    'circle_button_text_background_color': 'Fond texte bouton circulaire',
    'square_button_text_background_color': 'Fond texte bouton rectangulaire',
    'square_cancel_button_text_background_color': 'Fond texte bouton annuler',
    'validation_button_text_background_color': 'Fond texte bouton validation',
    // Contour texte des boutons
    'circle_button_text_border_color': 'Contour texte bouton circulaire',
    'square_button_text_border_color': 'Contour texte bouton rectangulaire',
    'square_cancel_button_text_border_color': 'Contour texte bouton annuler',
    'validation_button_text_border_color': 'Contour texte bouton validation',
    // patient_button_border_color (Bordures des boutons)
    'circle_button_border_color': 'Bordure bouton circulaire',
    'square_button_border_color': 'Bordure bouton rectangulaire',
    'square_cancel_button_border_color': 'Bordure bouton annuler',
    'validation_button_border_color': 'Bordure bouton validation',
    // phone_border_color (Contours du texte)
    'phone_title_border_color': 'Contour titre téléphone',
    'phone_line1_border_color': 'Contour ligne 1',
    'phone_line2_border_color': 'Contour ligne 2',
    'phone_line3_border_color': 'Contour ligne 3',
    'phone_line4_border_color': 'Contour ligne 4',
    'phone_line5_border_color': 'Contour ligne 5',
    'phone_line6_border_color': 'Contour ligne 6',
    'phone_specific_message_border_color': 'Contour message spécifique',
    // phone_main_color
    'phone_title_background_color': 'Fond titre téléphone',
    // phone_secondary_color
    'phone_line1_background_color': 'Fond ligne 1',
    'phone_line2_background_color': 'Fond ligne 2',
    'phone_line3_background_color': 'Fond ligne 3',
    'phone_line4_background_color': 'Fond ligne 4',
    'phone_line5_background_color': 'Fond ligne 5',
    'phone_line6_background_color': 'Fond ligne 6',
    'phone_specific_message_background_color': 'Fond message spécifique',
    // phone_third_color
    'phone_line1_font_color': 'Texte ligne 1',
    'phone_line2_font_color': 'Texte ligne 2',
    'phone_line3_font_color': 'Texte ligne 3',
    'phone_line4_font_color': 'Texte ligne 4',
    'phone_line5_font_color': 'Texte ligne 5',
    'phone_line6_font_color': 'Texte ligne 6',
    'phone_specific_message_font_color': 'Texte message spécifique',
    // phone_your_turn_main_color
    'phone_your_turn_line1_background_color': 'Fond ligne 1 (votre tour)',
    'phone_your_turn_line2_background_color': 'Fond ligne 2 (votre tour)',
    'phone_your_turn_line3_background_color': 'Fond ligne 3 (votre tour)',
    'phone_your_turn_line4_background_color': 'Fond ligne 4 (votre tour)',
    'phone_your_turn_line5_background_color': 'Fond ligne 5 (votre tour)',
    'phone_your_turn_line6_background_color': 'Fond ligne 6 (votre tour)',
    // phone_your_turn_third_color
    'phone_your_turn_line1_font_color': 'Texte ligne 1 (votre tour)',
    'phone_your_turn_line2_font_color': 'Texte ligne 2 (votre tour)',
    'phone_your_turn_line3_font_color': 'Texte ligne 3 (votre tour)',
    'phone_your_turn_line4_font_color': 'Texte ligne 4 (votre tour)',
    'phone_your_turn_line5_font_color': 'Texte ligne 5 (votre tour)',
    'phone_your_turn_line6_font_color': 'Texte ligne 6 (votre tour)',
    // phone_your_turn_border_color
    'phone_your_turn_line1_border_color': 'Contour ligne 1 (votre tour)',
    'phone_your_turn_line2_border_color': 'Contour ligne 2 (votre tour)',
    'phone_your_turn_line3_border_color': 'Contour ligne 3 (votre tour)',
    'phone_your_turn_line4_border_color': 'Contour ligne 4 (votre tour)',
    'phone_your_turn_line5_border_color': 'Contour ligne 5 (votre tour)',
    'phone_your_turn_line6_border_color': 'Contour ligne 6 (votre tour)',
};

// Fonction pour générer les checkboxes pour une couleur spécifique
function generateCheckboxes(parentColor) {
    const container = document.getElementById(`variableCheckboxes-${parentColor}`);
    if (!container || !colorMappings[parentColor]) return;
    
    container.innerHTML = ''; // Nettoie le conteneur
    
    const variables = colorMappings[parentColor].targets;
    variables.forEach(variable => {
        const description = variableDescriptions[variable] || variable;
        const checkboxDiv = document.createElement('div');
        checkboxDiv.className = 'form-check';
        checkboxDiv.innerHTML = `
            <input class="form-check-input variable-checkbox-${parentColor}" 
                   type="checkbox"
                   checked
                   value="${variable}" 
                   id="checkbox-${variable}">
            <label class="form-check-label" for="checkbox-${variable}">
                ${description}
            </label>
        `;
        container.appendChild(checkboxDiv);
    });
}

// Fonction pour sélectionner toutes les variables d'une couleur
function selectAllVariables(parentColor) {
    document.querySelectorAll(`.variable-checkbox-${parentColor}`).forEach(checkbox => {
        checkbox.checked = true;
    });
}

// Fonction pour désélectionner toutes les variables d'une couleur
function deselectAllVariables(parentColor) {
    document.querySelectorAll(`.variable-checkbox-${parentColor}`).forEach(checkbox => {
        checkbox.checked = false;
    });
}

// Fonction pour inverser la sélection des variables d'une couleur
function invertSelection(parentColor) {
    document.querySelectorAll(`.variable-checkbox-${parentColor}`).forEach(checkbox => {
        checkbox.checked = !checkbox.checked;
    });
}

// Fonction pour obtenir les variables sélectionnées pour une couleur
function getSelectedVariables(parentColor) {
    const selectedVariables = [];
    document.querySelectorAll(`.variable-checkbox-${parentColor}:checked`).forEach(checkbox => {
        selectedVariables.push(checkbox.value);
    });
    return selectedVariables;
}

// Initialisation des checkboxes pour chaque couleur
document.addEventListener('DOMContentLoaded', function() {
    Object.keys(colorMappings).forEach(color => {
        generateCheckboxes(color);
    });
});



// Configuration des couleurs et fonctions
const cssNamedColors = {
    // Rouges
    'indianred': '#CD5C5C',
    'lightcoral': '#F08080',
    'salmon': '#FA8072',
    'darksalmon': '#E9967A',
    'lightsalmon': '#FFA07A',
    'crimson': '#DC143C',
    'red': '#FF0000',
    'firebrick': '#B22222',
    'darkred': '#8B0000',
    
    // Roses
    'pink': '#FFC0CB',
    'lightpink': '#FFB6C1',
    'hotpink': '#FF69B4',
    'deeppink': '#FF1493',
    'mediumvioletred': '#C71585',
    'palevioletred': '#DB7093',
    
    // Oranges
    'coral': '#FF7F50',
    'tomato': '#FF6347',
    'orangered': '#FF4500',
    'darkorange': '#FF8C00',
    'orange': '#FFA500',
    
    // Jaunes
    'gold': '#FFD700',
    'yellow': '#FFFF00',
    'lightyellow': '#FFFFE0',
    'lemonchiffon': '#FFFACD',
    'lightgoldenrodyellow': '#FAFAD2',
    'papayawhip': '#FFEFD5',
    'moccasin': '#FFE4B5',
    'peachpuff': '#FFDAB9',
    'palegoldenrod': '#EEE8AA',
    'khaki': '#F0E68C',
    'darkkhaki': '#BDB76B',
    
    // Violets
    'lavender': '#E6E6FA',
    'thistle': '#D8BFD8',
    'plum': '#DDA0DD',
    'violet': '#EE82EE',
    'orchid': '#DA70D6',
    'fuchsia': '#FF00FF',
    'magenta': '#FF00FF',
    'mediumorchid': '#BA55D3',
    'mediumpurple': '#9370DB',
    'rebeccapurple': '#663399',
    'blueviolet': '#8A2BE2',
    'darkviolet': '#9400D3',
    'darkorchid': '#9932CC',
    'darkmagenta': '#8B008B',
    'purple': '#800080',
    'indigo': '#4B0082',
    'slateblue': '#6A5ACD',
    'darkslateblue': '#483D8B',
    
    // Verts
    'greenyellow': '#ADFF2F',
    'chartreuse': '#7FFF00',
    'lawngreen': '#7CFC00',
    'lime': '#00FF00',
    'limegreen': '#32CD32',
    'palegreen': '#98FB98',
    'lightgreen': '#90EE90',
    'mediumspringgreen': '#00FA9A',
    'springgreen': '#00FF7F',
    'mediumseagreen': '#3CB371',
    'seagreen': '#2E8B57',
    'forestgreen': '#228B22',
    'green': '#008000',
    'darkgreen': '#006400',
    'yellowgreen': '#9ACD32',
    'olivedrab': '#6B8E23',
    'olive': '#808000',
    'darkolivegreen': '#556B2F',
    'mediumaquamarine': '#66CDAA',
    'darkseagreen': '#8FBC8F',
    'lightseagreen': '#20B2AA',
    'darkcyan': '#008B8B',
    'teal': '#008080',
    
    // Bleus
    'aqua': '#00FFFF',
    'cyan': '#00FFFF',
    'lightcyan': '#E0FFFF',
    'paleturquoise': '#AFEEEE',
    'aquamarine': '#7FFFD4',
    'turquoise': '#40E0D0',
    'mediumturquoise': '#48D1CC',
    'darkturquoise': '#00CED1',
    'cadetblue': '#5F9EA0',
    'steelblue': '#4682B4',
    'lightsteelblue': '#B0C4DE',
    'powderblue': '#B0E0E6',
    'lightblue': '#ADD8E6',
    'skyblue': '#87CEEB',
    'lightskyblue': '#87CEFA',
    'deepskyblue': '#00BFFF',
    'dodgerblue': '#1E90FF',
    'cornflowerblue': '#6495ED',
    'mediumslateblue': '#7B68EE',
    'royalblue': '#4169E1',
    'blue': '#0000FF',
    'mediumblue': '#0000CD',
    'darkblue': '#00008B',
    'navy': '#000080',
    'midnightblue': '#191970',
    
    // Bruns
    'cornsilk': '#FFF8DC',
    'blanchedalmond': '#FFEBCD',
    'bisque': '#FFE4C4',
    'navajowhite': '#FFDEAD',
    'wheat': '#F5DEB3',
    'burlywood': '#DEB887',
    'tan': '#D2B48C',
    'rosybrown': '#BC8F8F',
    'sandybrown': '#F4A460',
    'goldenrod': '#DAA520',
    'darkgoldenrod': '#B8860B',
    'peru': '#CD853F',
    'chocolate': '#D2691E',
    'saddlebrown': '#8B4513',
    'sienna': '#A0522D',
    'brown': '#A52A2A',
    'maroon': '#800000',
    
    // Blancs
    'white': '#FFFFFF',
    'snow': '#FFFAFA',
    'honeydew': '#F0FFF0',
    'mintcream': '#F5FFFA',
    'azure': '#F0FFFF',
    'aliceblue': '#F0F8FF',
    'ghostwhite': '#F8F8FF',
    'whitesmoke': '#F5F5F5',
    'seashell': '#FFF5EE',
    'beige': '#F5F5DC',
    'oldlace': '#FDF5E6',
    'floralwhite': '#FFFAF0',
    'ivory': '#FFFFF0',
    'antiquewhite': '#FAEBD7',
    'linen': '#FAF0E6',
    'lavenderblush': '#FFF0F5',
    'mistyrose': '#FFE4E1',
    
    // Gris
    'gainsboro': '#DCDCDC',
    'lightgray': '#D3D3D3',
    'silver': '#C0C0C0',
    'darkgray': '#A9A9A9',
    'gray': '#808080',
    'dimgray': '#696969',
    'lightslategray': '#778899',
    'slategray': '#708090',
    'darkslategray': '#2F4F4F',
    'black': '#000000'
};


function getColorData() {
    // Définition des couleurs appartenant à chaque groupe
    const colorFamilies = {
        'Rouges': [
            'red', 'indianred', 'lightcoral', 'salmon', 'darksalmon', 
            'lightsalmon', 'crimson', 'firebrick', 'darkred', 'maroon'
        ],
        'Roses': [
            'pink', 'lightpink', 'hotpink', 'deeppink', 'palevioletred', 
            'mediumvioletred'
        ],
        'Oranges': [
            'coral', 'tomato', 'orangered', 'darkorange', 'orange'
        ],
        'Jaunes': [
            'gold', 'yellow', 'lightyellow', 'lemonchiffon', 'lightgoldenrodyellow',
            'papayawhip', 'moccasin', 'peachpuff', 'palegoldenrod', 'khaki',
            'darkkhaki', 'goldenrod', 'darkgoldenrod'
        ],
        'Violets': [
            'lavender', 'thistle', 'plum', 'violet', 'orchid', 'fuchsia',
            'magenta', 'mediumorchid', 'mediumpurple', 'rebeccapurple',
            'blueviolet', 'darkviolet', 'darkorchid', 'darkmagenta',
            'purple', 'indigo'
        ],
        'Bleus': [
            'blue', 'mediumblue', 'darkblue', 'navy', 'midnightblue',
            'royalblue', 'cornflowerblue', 'lightsteelblue', 'lightblue',
            'powderblue', 'deepskyblue', 'skyblue', 'lightskyblue',
            'steelblue', 'aliceblue', 'dodgerblue', 'slateblue',
            'darkslateblue', 'mediumslateblue'
        ],
        'Cyans': [
            'aqua', 'cyan', 'lightcyan', 'paleturquoise', 'aquamarine',
            'turquoise', 'mediumturquoise', 'darkturquoise', 'cadetblue',
            'darkcyan', 'teal'
        ],
        'Verts': [
            'green', 'darkgreen', 'lightgreen', 'palegreen', 'lime',
            'limegreen', 'forestgreen', 'seagreen', 'mediumseagreen',
            'springgreen', 'mediumspringgreen', 'greenyellow',
            'chartreuse', 'lawngreen', 'yellowgreen', 'olivedrab',
            'olive', 'darkolivegreen', 'mediumaquamarine', 'darkseagreen',
            'lightseagreen'
        ],
        'Bruns': [
            'cornsilk', 'blanchedalmond', 'bisque', 'navajowhite', 'wheat',
            'burlywood', 'tan', 'rosybrown', 'sandybrown', 'peru',
            'chocolate', 'saddlebrown', 'sienna', 'brown'
        ],
        'Blancs': [
            'white', 'snow', 'honeydew', 'mintcream', 'azure',
            'ghostwhite', 'whitesmoke', 'seashell', 'beige', 'oldlace',
            'floralwhite', 'ivory', 'antiquewhite', 'linen',
            'lavenderblush', 'mistyrose'
        ],
        'Gris': [
            'gainsboro', 'lightgray', 'silver', 'darkgray', 'gray',
            'dimgray', 'lightslategray', 'slategray', 'darkslategray',
            'black'
        ]
    };

    // Création des groupes de couleurs
    return Object.entries(colorFamilies).map(([group, colorNames]) => ({
        text: group,
        children: colorNames
            .filter(name => cssNamedColors[name]) // Vérifie que la couleur existe
            .map(name => ({
                id: cssNamedColors[name],
                text: `${name} ${cssNamedColors[name]}`,
                color: cssNamedColors[name]
            }))
    }));
}

function formatColorOption(color) {
    if (!color.id || !color.color) return color.text;
    return $(`<span>
        <span style="display:inline-block; width:20px; height:20px; margin-right:10px; background-color:${color.color}; vertical-align:middle;"></span>
        ${color.text}
    </span>`);
}

function initColorPickers() {
    $('.color-select2').each(function() {
        const $select = $(this);
        if (!$select.data('select2')) {  // Vérifie si Select2 n'est pas déjà initialisé
            const selectId = this.id;
            
            // Récupère la première partie comme source et le reste comme variable
            const firstUnderscore = selectId.indexOf('_');
            const source = selectId.substring(0, firstUnderscore);
            const variable = selectId.substring(firstUnderscore + 1);
            const colorPickerId = `${source}_${variable}_picker`;

            console.log('Initializing picker:', {
                selectId,
                source,
                variable,
                colorPickerId
            });

            $select.select2({
                data: getColorData(),
                templateResult: formatColorOption,
                templateSelection: formatColorOption
            });

            // Synchronisation Select2 -> ColorPicker
            $select.on('select2:select', function(e) {
                const colorPicker = document.getElementById(colorPickerId);
                if (colorPicker) {
                    colorPicker.value = e.params.data.id;
                    handleColorChange(source, variable);
                }
            });

            // Synchronisation ColorPicker -> Select2
            const colorPicker = document.getElementById(colorPickerId);
            if (colorPicker) {
                colorPicker.addEventListener('input', function(e) {
                    const color = e.target.value.toUpperCase();
                    const colorName = Object.entries(cssNamedColors).find(([_, hex]) => 
                        hex.toUpperCase() === color)?.[0] || color;
                    
                    // Mise à jour ou création de l'option dans Select2
                    if (!$select.find(`option[value="${color}"]`).length) {
                        $select.append(new Option(`${colorName} ${color}`, color, false, false));
                    }
                    $select.val(color).trigger('change');
                    handleColorChange(source, variable);
                });
            }
        }
    });
}

function handleColorChange(source, variable) {
    // On ne gère que l'activation du bouton
    const input = document.getElementById(`${source}_${variable}`);
    const button = document.getElementById(`${source}_${variable}_button`);
    const initialValue = input.dataset.initialValue;
    
    button.disabled = input.value === initialValue;
}


function handleColorAfterRequest(source, variable) {
    const input = document.getElementById(`${source}_${variable}`);
    const button = document.getElementById(`${source}_${variable}_button`);
    const newValue = input.value;
    
    // Si c'est une couleur parent, met à jour les dépendances sélectionnées
    if (colorMappings[variable]) {
        const selectedVariables = getSelectedVariables(variable);
        updateDependentColors(source, variable, newValue, selectedVariables);
    } else {
        // Si ce n'est pas une couleur parent, mise à jour simple
        const formData = new FormData();
        formData.append('source', source);
        formData.append('variable', variable);
        formData.append('value', newValue);
        formData.append('dependencies', '[]');
        sendUpdateToServer(formData);
    }

    // Met à jour la valeur initiale et le bouton après l'envoi
    input.dataset.initialValue = newValue;
    button.disabled = true;
    
    // Feedback visuel
    button.textContent = "Enregistré ✓";
    setTimeout(() => {
        button.textContent = "Enregistrer";
    }, 1000);
}


// Initialisation
document.addEventListener('DOMContentLoaded', initColorPickers);
document.addEventListener('htmx:afterSettle', initColorPickers);


document.addEventListener('DOMContentLoaded', function() {

    // initialisation du modal
    var modal = new bootstrap.Modal(document.getElementById('modal_delete'), {
        keyboard: false
    });
});

function updateDependentColors(source, parentVariable, newValue, selectedVariables) {
    // Mise à jour de l'interface pour les variables sélectionnées
    selectedVariables.forEach(targetVar => {
        const picker = document.getElementById(`${source}_${targetVar}_picker`);
        if (picker) {
            picker.value = newValue;
        }

        const select = $(`#${source}_${targetVar}`);
        if (select.length) {
            const colorName = Object.entries(cssNamedColors).find(([_, hex]) => 
                hex.toUpperCase() === newValue.toUpperCase())?.[0] || newValue;
            
            if (!select.find(`option[value="${newValue}"]`).length) {
                select.append(new Option(`${colorName} ${newValue}`, newValue, false, false));
            }
            select.val(newValue).trigger('change.select2');  // Utiliser change.select2 pour éviter la propagation
        }

        const input = document.getElementById(`${source}_${targetVar}`);
        if (input) {
            input.dataset.initialValue = newValue;
        }
    });

    // Un seul envoi avec toutes les informations
    const formData = new FormData();
    formData.append('source', source);
    formData.append('variable', parentVariable);
    formData.append('value', newValue);
    formData.append('dependencies', JSON.stringify(selectedVariables));

    sendUpdateToServer(formData);
}

function sendUpdateToServer(formData) {
    console.log(formData);
    fetch('/admin/update_css_variable', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            console.log('Variables mises à jour avec succès');
        } else {
            console.error('Erreur lors de la mise à jour des variables');
        }
    })
    .catch(error => {
        console.error('Erreur:', error);
    });
}


// Fonction utilitaire pour récupérer les variables sélectionnées
function getSelectedVariables(parentColor) {
    const selectedVariables = [];
    document.querySelectorAll(`.variable-checkbox-${parentColor}:checked`).forEach(checkbox => {
        selectedVariables.push(checkbox.value);
    });
    return selectedVariables;
}


// ---------------- INPUT POUR ENTIER PERMETTANT D'EN CHANGER PLUSIEURS ----------------

// Configuration des mappings pour les variables numériques
const numberMappings = {
    'phone_lines_font_size': {
        targets: [
            'phone_line1_font_size',
            'phone_line2_font_size',
            'phone_line3_font_size',
            'phone_line4_font_size',
            'phone_line5_font_size',
            'phone_line6_font_size',
            'phone_your_turn_line1_font_size',
            'phone_your_turn_line2_font_size',
            'phone_your_turn_line3_font_size',
            'phone_your_turn_line4_font_size',
            'phone_your_turn_line5_font_size',
            'phone_your_turn_line6_font_size',
            // Ajoutez d'autres variables liées
        ]
    },
    'phone_lines_font_weight': {
        targets: [
            'phone_line1_font_weight',
            'phone_line2_font_weight',
            'phone_line3_font_weight',
            'phone_line4_font_weight',
            'phone_line5_font_weight',
            'phone_line6_font_weight',
            'phone_your_turn_line1_font_weight',
            'phone_your_turn_line2_font_weight',
            'phone_your_turn_line3_font_weight',
            'phone_your_turn_line4_font_weight',
            'phone_your_turn_line5_font_weight',
            'phone_your_turn_line6_font_weight',
            // Ajoutez d'autres variables liées
        ]
    }
    // Ajoutez d'autres groupes de variables
};

// Fonction pour gérer les touches spéciales pour l'input parent
function handleParentKeyPress2(event, source, variable, unit) {
    if (event.key === 'Enter') {
        event.preventDefault();
        // D'abord appliquer les changements à tous les champs
        applyNumberToAll(source, variable);
        // Puis simuler le clic sur le bouton d'enregistrement
        document.getElementById(`${source}_${variable}_button`).click();
    } else if (event.key === 'Escape') {
        event.preventDefault();
        resetNumberInput(source, variable);
    }
}

function handleParentKeyPress(event, source, variable, unit) {
    if (event.key === 'Enter') {
        event.preventDefault();
        // Créer et envoyer la requête HTMX pour le champ parent, sans traiter la réponse
        htmx.ajax('POST', '/admin/update_css_variable', {
            values: {
                source: source,
                variable: variable,
                value: document.getElementById(`${source}_${variable}`).value + unit
            },
            swap: 'none'  // Ignorer la réponse
        });
        // Puis appliquer les changements aux autres champs
        applyNumberToAll(source, variable, unit);
    } else if (event.key === 'Escape') {
        event.preventDefault();
        resetNumberInput(source, variable);
    }
}

// Fonction pour appliquer la valeur à toutes les variables sélectionnées
function applyNumberToAll(source, variable) {
    const sourceInput = document.getElementById(`${source}_${variable}`);
    const value = sourceInput.value;
    
    const checkboxes = document.querySelectorAll(`#numberVariableCheckboxes-${variable} input[type="checkbox"]:checked`);
    
    // Appliquer les changements à tous les champs sélectionnés
    checkboxes.forEach(checkbox => {
        const targetVariable = checkbox.value;
        const targetInput = document.getElementById(`${source}_${targetVariable}`);
        if (targetInput) {
            targetInput.value = value;
            // Déclencher l'événement input
            targetInput.dispatchEvent(new Event('input', {
                bubbles: true,
                cancelable: true,
            }));
            
            // Cliquer sur le bouton d'enregistrement correspondant
            const saveButton = document.getElementById(`${source}_${targetVariable}_button`);
            if (saveButton) {
                saveButton.click();
            }
        }
    });
}

// Fonctions pour gérer les sélections
function selectAllNumberVariables(variable) {
    const checkboxes = document.querySelectorAll(`#numberVariableCheckboxes-${variable} input[type="checkbox"]`);
    checkboxes.forEach(checkbox => checkbox.checked = true);
}

function deselectAllNumberVariables(variable) {
    const checkboxes = document.querySelectorAll(`#numberVariableCheckboxes-${variable} input[type="checkbox"]`);
    checkboxes.forEach(checkbox => checkbox.checked = false);
}

function invertNumberSelection(variable) {
    const checkboxes = document.querySelectorAll(`#numberVariableCheckboxes-${variable} input[type="checkbox"]`);
    checkboxes.forEach(checkbox => checkbox.checked = !checkbox.checked);
}

// Fonction pour initialiser les checkboxes
function initNumberVariableCheckboxes(variable) {
    if (!numberMappings[variable]) return;
    
    const container = document.getElementById(`numberVariableCheckboxes-${variable}`);
    if (!container) return;

    const targets = numberMappings[variable].targets;
    targets.forEach(target => {
        const div = document.createElement('div');
        div.className = 'form-check';
        
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'form-check-input';
        checkbox.id = `checkbox-${target}`;
        checkbox.value = target;
        checkbox.checked = true;  // Coché par défaut
        
        const label = document.createElement('label');
        label.className = 'form-check-label';
        label.htmlFor = `checkbox-${target}`;
        label.textContent = target.replace(/_/g, ' ');
        
        div.appendChild(checkbox);
        div.appendChild(label);
        container.appendChild(div);
    });
}

// Initialisation au chargement de la page
document.addEventListener('DOMContentLoaded', () => {
    // Initialiser les checkboxes pour chaque variable dans numberMappings
    Object.keys(numberMappings).forEach(variable => {
        initNumberVariableCheckboxes(variable);
    });
});