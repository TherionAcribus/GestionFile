// Fonctions partagees par les FRAGMENTS de l'administration.
// Extraites des gabarits (Phase 8, point 2).
//
// Ces fragments sont injectes par HTMX ; leurs <script> etaient donc reexecutes
// a chaque echange, redefinissant les memes fonctions. Elles sont desormais
// chargees une fois depuis admin/base.html. Les interactions utilisent des
// ecouteurs deleges (attributs data-*) : les attributs onclick des fragments
// seraient bloques par la CSP (script-src 'self').

// --- extrait de templates/admin/announce_audio.html ---
function showTestStatus(message, isError) {
    const timeElement = document.getElementById('generation-time');
    const infoElement = document.getElementById('generation-time-info');
    if (!timeElement) { return; }
    if (infoElement) { infoElement.style.display = 'none'; }
    timeElement.style.display = 'block';
    timeElement.classList.toggle('text-danger', !!isError);
    timeElement.textContent = message;
}

function updateGenerationTime(time, scope) {
    const timeElement = document.getElementById('generation-time');
    const infoElement = document.getElementById('generation-time-info');

    if (scope === 'announce') {
        timeElement.style.display = 'none';
        infoElement.style.display = 'block';
    } else {
        // Un fichier déjà en cache se rejoue quasi instantanément : le dire
        // rassure sur le fait que Google n'est pas rappelé à chaque annonce.
        const cached = time < 0.3;
        showTestStatus('Annonce prête en ' + time.toFixed(2).replace('.', ',') + ' s'
            + (cached ? ' (fichier déjà généré, réutilisé).' : ' (nouvelle génération).'), false);
    }
}

// `language` : fourni par le bouton « Écouter un exemple » d'un panneau de
// langue (data-test-language) ; sinon la langue choisie dans « Tester ».
function testAudio(scope, language) {
    let language_code = language || document.getElementById('language_code').value;
    let call_number = document.getElementById('call_number').value;

    // Anti double-clic : la génération TTS est synchrone côté serveur et peut
    // appeler un service externe — on bloque les boutons le temps de la requête.
    const buttons = document.querySelectorAll('[data-test-audio]');
    buttons.forEach(function (btn) { btn.disabled = true; });
    showTestStatus("Génération de l'annonce…", false);

    const params = new URLSearchParams({ language_code: language_code, call_number: call_number });
    fetch(`/admin/announce/audio/test/${scope}?${params}`, { method: 'POST' })
        .then(response => response.json().then(data => ({ ok: response.ok, data: data })))
        .then(result => {
            if (!result.ok) {
                showTestStatus(result.data.error || 'Test refusé.', true);
                return;
            }
            updateGenerationTime(result.data.generation_time, scope);
        })
        .catch(error => {
            console.error('Error:', error);
            showTestStatus('Le test a échoué (voir le journal du serveur).', true);
        })
        .finally(() => {
            buttons.forEach(function (btn) { btn.disabled = false; });
        });
}

// --- extrait de templates/admin/announce_audio_gallery.html ---
function selectSound(button) {
    // D'abord, désélectionner tous les éléments

    document.querySelectorAll(".sound-item").forEach(function(item) {
        item.classList.remove("bg-secondary");
        item.classList.remove("bg-secondary-subtle");
    });

    // Ensuite, sélectionner l'élément parent de ce bouton
    const li = button.closest("li");
    li.classList.add("bg-secondary");

    // mise à jour du span pour le nom du fichier
    document.getElementById("selected_sound").innerText = button.name;
}

// --- extrait de templates/admin/patient_page_button_modal_gallery.html ---
// Sélection d'une image dans la modale galerie de boutons.
// Anciennement appelée par onclick inline (incompatible CSP script-src 'self').
// Désormais déclenchée par délégation d'événement sur les boutons
// [data-select-image] (voir admin.js). Conservée comme globale pour compat
// ascendante, mais corrigée : `let` au lieu d'une variable globale implicite,
// et sélection via querySelector au lieu d'un id fragile construit depuis le
// nom de fichier (qui peut contenir des espaces, points, etc.).
function selectImage(imageName) {
    // Mettre à jour l'URL de l'image dans le formulaire
    var field = document.getElementById('image_name_field');
    if (field) { field.innerText = imageName; }
    // Cadre bleu autour de l'image : retirer la sélection précédente
    document.querySelectorAll('.selected-image').forEach(function (el) {
        el.classList.remove('selected-image');
    });
    // Sélectionner la vignette via data-image (robuste : pas d'id construit
    // depuis le nom de fichier, qui peut contenir des caractères invalides
    // pour un id HTML).
    var target = document.querySelector('[data-image="' + imageName + '"]');
    if (target) { target.classList.add('selected-image'); }
}

// --- extrait de templates/admin/activity_htmx_table.html ---
    // Permet de récuperer les options multiples
    function getSelectedOptions(selectElementId) {
    var selectElement = document.getElementById(selectElementId);
    var selectedValues = [];
    for (var option of selectElement.options) {
        if (option.selected) {
            selectedValues.push(option.value);
        }
    }
    return selectedValues;
}

// --- Comportements délégués (remplacent les onclick inline, CSP) -----------

// Boutons « Tester l'annonce » de announce_audio.html : data-test-audio="scope".
document.addEventListener('click', function (evt) {
    var btn = evt.target.closest ? evt.target.closest('[data-test-audio]') : null;
    if (btn) { testAudio(btn.getAttribute('data-test-audio'), btn.getAttribute('data-test-language')); }
});

// Boutons « Sélectionner » de announce_audio_gallery_list.html : la liste est
// réinjectée par HTMX dans la modale, la délégation couvre les arrivées tardives.
document.addEventListener('click', function (evt) {
    var btn = evt.target.closest ? evt.target.closest('.select-sound-button') : null;
    if (btn) { selectSound(btn); }
});


// Bouton « fermer » d'un formulaire injecté par HTMX (ex. « Nouvelle règle »
// de l'algorithme) : data-clear-target="#conteneur" vide le conteneur.
document.addEventListener('click', function (evt) {
    var btn = evt.target.closest ? evt.target.closest('[data-clear-target]') : null;
    if (!btn) { return; }
    var target = document.querySelector(btn.getAttribute('data-clear-target'));
    if (target) { target.innerHTML = ''; }
});
