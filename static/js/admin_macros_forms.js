// Formulaires des macros d'administration : restauration et copie de couleurs.
// Extraits de templates/admin/macros.html (Phase 8, point 2).
//
// Ces deux blocs interpolaient une valeur Jinja DANS le JavaScript
// (`{{ table }}`, `{{ current_page_key }}`), ce qui les rendait inseparables du
// gabarit. La valeur passe desormais par un attribut `data-*` du HTML : le
// JavaScript est generique et vit dans ce fichier.

function initRestoreForm(table, isDatabase) {
    var fileInput = document.getElementById('fileInput_' + table);
    var restoreButton = document.getElementById('restoreButton_' + table);
    var fileContentDiv = document.getElementById('fileContent_' + table);
    var json_tag = document.getElementById('json_tag_' + table).textContent;

    fileInput.addEventListener('change', function(event) {
        var file = event.target.files[0];
        if (file) {
            if (isDatabase && file.name.endsWith('.zip')) {
                // Just indicate that a ZIP file was selected and enable the restore button
                fileContentDiv.innerHTML = `<div><strong>Fichier ZIP sélectionné :</strong> ${file.name}</div>`;
                restoreButton.disabled = false; // Enable the button
            } else if (!isDatabase && file.name.endsWith('.json')) {
                var reader = new FileReader();
                reader.onload = function(e) {
                    var content = e.target.result;
                    try {
                        var jsonContent = JSON.parse(content);
                        var metadata = {
                            "name": jsonContent.name,
                            "type": jsonContent.type,
                            "version": jsonContent.version,
                            "timestamp": jsonContent.timestamp,
                            "comments": jsonContent.comments
                        };
                        var metadataContent = `
                            <div><strong>Name:</strong> ${metadata.name}<br>
                            <strong>Type:</strong> ${metadata.type}<br>
                            <strong>Version:</strong> ${metadata.version}<br>
                            <strong>Timestamp:</strong> ${metadata.timestamp}<br>
                            <strong>Comments:</strong> ${metadata.comments}</div>
                        `;
                        if (json_tag != jsonContent.name) {
                            var metadataContentBadContent = `
                            <div><strong>Erreur de fichier !</strong><br>
                            Vous utilisez un fichier de type <strong>${jsonContent.name}</strong>
                            mais vous devez utiliser un fichier de type <strong>${json_tag}</strong><br>
                        `;
                            fileContentDiv.innerHTML = metadataContentBadContent;
                        } else {
                            fileContentDiv.innerHTML = metadataContent;
                            restoreButton.disabled = false; // Enable the button
                        }

                    } catch (error) {
                        fileContentDiv.innerText = 'Invalid JSON file or missing metadata';
                        restoreButton.disabled = true; // Disable the button in case of error
                    }
                };
                reader.readAsText(file);
            } else {
                fileContentDiv.innerText = 'Fichier invalide sélectionné. Veuillez choisir un fichier valide.';
                restoreButton.disabled = true; // Disable the button if invalid file selected
            }
        } else {
            fileContentDiv.innerText = '';
            restoreButton.disabled = true; // Disable the button if no file is selected
        }
    });
}


// --- Initialisation, pilotee par les attributs data-* ----------------------

// Chaque bloc « Sauvegarde / Restauration » porte data-restore-table.
function initFormulairesRestauration() {
    document.querySelectorAll('[data-restore-table]').forEach(function (bloc) {
        if (bloc.dataset.restoreInit === '1') { return; }   // deja cable
        bloc.dataset.restoreInit = '1';
        var table = bloc.dataset.restoreTable;
        initRestoreForm(table, table === 'databases');
    });
}

// Le bloc « Copier les couleurs depuis » porte data-copy-colors-page.
function initCopieCouleurs() {
    document.querySelectorAll('[data-copy-colors-page]').forEach(function (bloc) {
        if (bloc.dataset.copyColorsInit === '1') { return; }
        bloc.dataset.copyColorsInit = '1';
        var currentPage = bloc.dataset.copyColorsPage;
        const select = document.getElementById('copy_colors_select_' + currentPage);
        if (select && typeof pageColorRoles !== 'undefined') {
            for (const key in pageColorRoles) {
                if (key !== currentPage) {
                    const opt = document.createElement('option');
                    opt.value = key;
                    opt.textContent = pageColorRoles[key].label;
                    select.appendChild(opt);
                }
            }
        }

        // Déplacer la modale au niveau du body pour éviter les problèmes de positionnement
        const modal = document.getElementById('modal_copy_colors');
        if (modal) {
            document.body.appendChild(modal);
        }
    });
}

// Balayage depuis `document` a chaque fois : la garde `dataset.*Init` ci-dessus
// empeche la double initialisation, et on n'a pas a se demander si l'element
// cherche EST la cible de l'echange ou se trouve a l'interieur.
function initMacrosFormulaires() {
    initFormulairesRestauration();
    initCopieCouleurs();
}

document.addEventListener('DOMContentLoaded', initMacrosFormulaires);
document.addEventListener('htmx:afterSettle', initMacrosFormulaires);
