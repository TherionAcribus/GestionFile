document.addEventListener('DOMContentLoaded', (event) => {
    // Configuration du socket - on le déclare en dehors pour y avoir accès partout
    let phoneSocket = null;

    // Fonction pour initialiser le socket avec le call_number
    function initSocket(callNumber) {
        if (phoneSocket) {
            console.log('Socket already initialized');
            return;
        }

        var protocol = window.location.protocol;
        // Socket.IO expects an http(s) URL. Use same-origin host/port for reverse proxies (Coolify).
        var socketProtocol = protocol === 'https:' ? 'https://' : 'http://';
        var domain = window.location.host;
        var baseUrl = socketProtocol + domain;

        phoneSocket = io.connect(baseUrl + '/socket_phone', { 
            query: {
                username: 'phone',
                call_number: callNumber
            }
        });

        phoneSocket.on('connect', function() {
            console.log('Phone WebSocket connected');
            console.log('Call number:', callNumber);
            console.log('Socket URI:', phoneSocket.io.uri);
        });


        phoneSocket.on('your_turn', function(msg) {
            console.log('YOUR TURN received:', msg);
            
            if (msg.call_number === callNumber) {
                console.log('This is our turn!');
                
                if ('vibrate' in navigator) {
                    navigator.vibrate([200, 100, 200]);
                }

                // Récupérer la div HTMX et ses données
                const infosDiv = document.getElementById('div_infos');
                
                // Créer un FormData pour envoyer les données
                const formData = new FormData();
                formData.append('activity_id', infosDiv.getAttribute('data-activity-id'));
                formData.append('language_code', infosDiv.getAttribute('data-language-code'));
                console.log('Activity ID:', infosDiv.getAttribute('data-activity-id'));

                // Utiliser fetch au lieu de htmx.ajax pour débugger
                fetch('/patient/phone/your_turn', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.text())
                .then(html => {
                    infosDiv.outerHTML = html;
                })
                .catch(error => console.error('Error:', error));
            }
        });


    }

    // Écouter l'événement après le swap HTMX
    htmx.on('htmx:afterSwap', function(evt) {
        // Récupérer le call_number depuis les cookies après le swap HTMX
        const callNumber = document.cookie
            .split('; ')
            .find(row => row.startsWith('patient_call_number='))
            ?.split('=')[1];

        if (callNumber) {
            console.log('Call number received:', callNumber);
            initSocket(callNumber);
        }
    });
});

// Style pour la notification
const style = document.createElement('style');
style.textContent = `
    .notification {
        position: fixed;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        z-index: 1000;
        animation: fadeIn 0.5s;
    }

    @keyframes fadeIn {
        from { opacity: 0; }
        to { opacity: 1; }
    }
`;
document.head.appendChild(style);



window.onbeforeunload = function(event) {
    // Message displayed to user to prevent refresh/navigation
    event.preventDefault();
    return 'Are you sure you want to leave? Changes you made may not be saved.';
};

// Optional: Additional handling for navigation within the app
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('a').forEach(function(link) {
        link.addEventListener('click', function(event) {
            // Custom logic to handle in-app navigation if needed
            // For example: Check if the link should be allowed
            // event.preventDefault(); // Uncomment to prevent default action
        });
    });
});
