// Fonctions partagees par les macros de templates/admin/macros.html.
// Extraites des gabarits (Phase 8, point 2).
//
// Pourquoi c'est important ici : ces definitions vivaient DANS le corps des
// macros. `css_unit_bloc` etant appele 71 fois sur une page de configuration
// CSS, les memes fonctions etaient renvoyees 71 fois dans une seule reponse
// HTML -- non cachables, reparsees a chaque affichage. Elles sont desormais
// chargees une fois depuis admin/base.html.
//
// Elles restent des GLOBALES : les macros les appellent depuis des attributs
// onclick / onkeypress, qui se resolvent au moment du clic.

// --- extrait de macros.html, ligne 3 ---
function handleInputChangeConfig(key) {
    // Récupération des éléments avec vérification
    const input = document.getElementById(key);
    const button = document.getElementById(`${key}_button`);
    
    if (!input || !button) {
        console.error('Elements not found:', { 
            key,
            inputFound: !!input, 
            buttonFound: !!button 
        });
        return;
    }
    
    const currentValue = input.value;
    const initialValue = input.getAttribute('data-initial-value');
    
    // Active/désactive le bouton selon si la valeur a changé
    button.disabled = currentValue === initialValue;
}
function handleKeyPressConfig(event, variable) {
    // Si la touche Entrée est pressée et que le bouton est activé
    if (event.key === 'Enter' && !event.shiftKey) {  // Permet le saut de ligne avec Shift+Enter
        const button = document.getElementById(`${variable}_button`);
        if (!button.disabled) {
            event.preventDefault();
            button.click();
        }
    }
}

// Désactive le bouton pendant la requête et remet la zone de résultat à zéro.
function handleBeforeRequestConfig(variable) {
    const button = document.getElementById(`${variable}_button`);
    const result = document.getElementById(`${variable}_result`);
    if (button) {
        button.disabled = true;
        button.textContent = "Enregistrement…";
    }
    if (result) {
        result.textContent = "";
        result.className = "small mt-1";
    }
}

function handleAfterRequestConfig(event, variable) {
    const input = document.getElementById(`${variable}`);
    const button = document.getElementById(`${variable}_button`);
    const result = document.getElementById(`${variable}_result`);

    // On ne considère la sauvegarde réussie que si htmx signale un succès ET
    // que le statut HTTP est un 2xx. Le serveur renvoie 400 (+ message) en cas
    // d'échec de validation, ce qui met event.detail.successful à false.
    const detail = event && event.detail;
    const xhr = detail && detail.xhr;
    const status = xhr ? xhr.status : 0;
    const ok = !!(detail && detail.successful) && status >= 200 && status < 300;
    const serverMessage = (xhr && xhr.responseText ? xhr.responseText : "").trim();

    if (button) {
        button.textContent = "Enregistrer";
    }

    if (ok) {
        // Succès : la valeur enregistrée devient la nouvelle valeur de référence,
        // le bouton se désactive (plus rien à enregistrer) et on confirme.
        if (input) {
            input.dataset.initialValue = input.value;
        }
        if (button) {
            button.disabled = true;
            button.textContent = "Enregistré ✓";
            setTimeout(() => {
                button.textContent = "Enregistrer";
            }, 1500);
        }
        if (result) {
            result.className = "small mt-1 text-success";
            result.textContent = serverMessage || "Enregistré.";
        }
    } else {
        // Échec : NE PAS toucher à la valeur initiale, garder le bouton actif
        // pour réessayer, et afficher le message d'erreur près du champ.
        if (button) {
            button.disabled = false;
        }
        if (result) {
            result.className = "small mt-1 text-danger";
            result.textContent = serverMessage
                || "Échec de l'enregistrement. Veuillez réessayer.";
        }
    }
}

// --- extrait de macros.html, ligne 822 ---
function handleInputChange(source, variable) {
    const input = document.getElementById(`${source}_${variable}`);
    const button = document.getElementById(`${source}_${variable}_button`);
    const initialValue = input.dataset.initialValue;
    
    // Active/désactive le bouton selon si la valeur a changé
    button.disabled = input.value === initialValue;
}

function handleKeyPress(event, source, variable, unit) {
    // Si la touche Entrée est pressée et que le bouton est activé
    if (event.key === 'Enter') {
        const button = document.getElementById(`${source}_${variable}_button`);
        if (!button.disabled) {
            event.preventDefault();
            button.click();
        }
    }
}

function handleAfterRequest(source, variable) {
    const input = document.getElementById(`${source}_${variable}`);
    const button = document.getElementById(`${source}_${variable}_button`);
    
    // Met à jour la valeur initiale et désactive le bouton
    input.dataset.initialValue = input.value;
    button.disabled = true;
}

// --- extrait de macros.html, ligne 1177 ---
function handleSimpleInputChange(source, variable) {
    const input = document.getElementById(`${source}_${variable}`);
    const button = document.getElementById(`${source}_${variable}_button`);
    const initialValue = input.dataset.initialValue;
    
    // Active/désactive le bouton selon si la valeur a changé
    button.disabled = input.value === initialValue;
}

function handleSimpleKeyPress(event, source, variable) {
    // Si la touche Entrée est pressée et que le bouton est activé
    if (event.key === 'Enter') {
        const button = document.getElementById(`${source}_${variable}_button`);
        if (!button.disabled) {
            event.preventDefault();
            button.click();
        }
    }
}

function handleSimpleAfterRequest(source, variable) {
    const input = document.getElementById(`${source}_${variable}`);
    const button = document.getElementById(`${source}_${variable}_button`);
    
    // Met à jour la valeur initiale
    input.dataset.initialValue = input.value;
    
    // Désactive le bouton
    button.disabled = true;
    
    // Feedback visuel temporaire
    button.textContent = "Enregistré ✓";
    setTimeout(() => {
        button.textContent = "Enregistrer";
    }, 1000);
}

