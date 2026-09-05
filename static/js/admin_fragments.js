// Fonctions partagees par les FRAGMENTS de l'administration.
// Extraites des gabarits (Phase 8, point 2).
//
// Ces fragments sont injectes par HTMX ; leurs <script> etaient donc reexecutes
// a chaque echange, redefinissant les memes fonctions. Elles sont desormais
// chargees une fois depuis admin/base.html et restent des GLOBALES : les
// attributs onclick des fragments les resolvent au moment du clic, quel que
// soit le moment ou le fragment est arrive dans la page.

// --- extrait de templates/admin/announce_audio.html ---
function updateGenerationTime(time, scope) {
    const timeElement = document.getElementById('generation-time');
    const infoElement = document.getElementById('generation-time-info');
    
    if (scope === 'announce') {
        timeElement.style.display = 'none';
        infoElement.style.display = 'block';
    } else {
        timeElement.style.display = 'block';
        infoElement.style.display = 'none';
        timeElement.textContent = `Generation time: ${time.toFixed(3)} seconds`;
    }
}

function testAudio(scope) {
    let language_code = document.getElementById('language_code').value;
    let call_number = document.getElementById('call_number').value;

    fetch(`/admin/announce/audio/test/${scope}?language_code=${language_code}&call_number=${call_number}`, { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            updateGenerationTime(data.generation_time, scope);
        })
        .catch(error => {
            console.error('Error:', error);
            document.getElementById('generation-time').textContent = 'Error measuring generation time';
            document.getElementById('generation-time-info').style.display = 'none';
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

