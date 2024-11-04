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
    console.log("Insert placeholder", textareaId, text);
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
    console.log("toqt ?", event.data);
    console.log(typeof(event.data));
    data = JSON.parse(event.data);
    console.log("toqt ?", data);
    console.log("ACTION", data.action);
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


// ---------------- COLORS PICKERS ----------------  

const colorMappings = {
    'patient_main_color': {
        // Liste des variables qui dépendent de la couleur principale
        targets: [
            'patient_title_background_color',
            'patient_title_font_color',
            // ... autres variables
        ]
    },
    'patient_secondary_color': {
        targets: [
            'subtitle_background_color',
            'subtitle_font_color',
            // ... autres variables
        ]
    },
    'patient_third_color': {
        targets: [
            'subtitle_no_activity_background_color',
            'subtitle_no_activity_font_color',
            // ... autres variables
        ]
    }
};

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
    
    // Met à jour la valeur initiale du parent
    input.dataset.initialValue = newValue;
    button.disabled = true;
    
    // Feedback visuel
    button.textContent = "Enregistré ✓";
    setTimeout(() => {
        button.textContent = "Enregistrer";
    }, 1000);
    
    // Si c'est une couleur parent, met à jour les dépendances
    if (colorMappings[variable]) {
        updateDependentColors(source, variable, newValue);
    }
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

function updateDependentColors(source, parentVariable, newValue) {
    // Met à jour les colorpickers dépendants
    const dependencies = colorMappings[parentVariable]?.targets || [];
    dependencies.forEach(targetVar => {
        // Met à jour le colorpicker
        const picker = document.getElementById(`${source}_${targetVar}_picker`);
        if (picker) {
            picker.value = newValue;
        }

        // Met à jour le select2
        const select = $(`#${source}_${targetVar}`);
        if (select.length) {
            const colorName = Object.entries(cssNamedColors).find(([_, hex]) => 
                hex.toUpperCase() === newValue.toUpperCase())?.[0] || newValue;
            
            if (!select.find(`option[value="${newValue}"]`).length) {
                select.append(new Option(`${colorName} ${newValue}`, newValue, false, false));
            }
            select.val(newValue).trigger('change');
        }

        // Met à jour la valeur initiale
        const input = document.getElementById(`${source}_${targetVar}`);
        if (input) {
            input.dataset.initialValue = newValue;
        }
    });
}
