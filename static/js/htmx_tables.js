// Configuration pour chaque type de table
const TABLE_CONFIGS = {
    'algo_table': {
        buttonClass: 'btn-save-rule',
        getFields: (id) => [
            document.getElementById(`name-${id}`),
            document.getElementById(`activity-${id}`),
            document.getElementById(`priority_level-${id}`),
            document.getElementById(`min_patients-${id}`),
            document.getElementById(`max_patients-${id}`),
            document.getElementById(`max_overtaken-${id}`),
            document.getElementById(`start_time-${id}`),
            document.getElementById(`end_time-${id}`)
        ]
    },
    'queue_table': {
        buttonClass: 'btnSaveLine',
        getFields: (id) => [
            document.getElementById(`call_number-${id}`),
            document.getElementById(`activity-${id}`),
            document.getElementById(`status-${id}`),
            document.getElementById(`counter-${id}`)
        ]
    },
    'staff_table': {
        buttonClass: 'btnSaveLine',
        getFields: (id) => [
            document.getElementById(`name-${id}`),
            document.getElementById(`initials-${id}`),
            document.getElementById(`language-${id}`)
        ],
        // Nouvelle fonction spécifique pour les checkboxes
        getCheckboxes: (id) => {
            return document.querySelectorAll(`.activities-${id}`);
        }
    }
};

// Fonction pour obtenir l'état des checkboxes
function getCheckboxesState(checkboxes) {
    if (!checkboxes) return null;
    return Array.from(checkboxes).map(cb => cb.checked);
}

// Fonction pour comparer deux tableaux d'états de checkboxes
function checkboxStatesAreDifferent(state1, state2) {
    if (!state1 || !state2 || state1.length !== state2.length) return false;
    return state1.some((checked, index) => checked !== state2[index]);
}

// Fonction générique d'initialisation des comportements
function initializeTableBehaviors(tableId) {
    const config = TABLE_CONFIGS[tableId];
    const table = document.getElementById(tableId);
    if (!table) return;

    // Désactiver tous les boutons Enregistrer
    const saveButtons = table.querySelectorAll(`.${config.buttonClass}`);
    saveButtons.forEach(button => button.disabled = true);

    // Pour chaque ligne du tableau
    table.querySelectorAll('tbody tr').forEach(row => {
        // Extraire l'ID depuis le premier input
        const firstInput = row.querySelector('input');
        const entityId = firstInput.id.split('-')[1];
        const saveButton = row.querySelector(`.${config.buttonClass}`);
        const fields = config.getFields(entityId);
        
        // Récupérer les checkboxes si elles existent pour cette configuration
        const checkboxes = config.getCheckboxes ? config.getCheckboxes(entityId) : null;

        // Stocker les valeurs initiales
        const initialValues = fields.map(field => field.value);
        const initialCheckboxesState = getCheckboxesState(checkboxes);

        // Fonction pour vérifier les changements
        const hasChanges = () => {
            const fieldsChanged = fields.some((field, index) => field.value !== initialValues[index]);
            const checkboxesChanged = checkboxes ? 
                checkboxStatesAreDifferent(initialCheckboxesState, getCheckboxesState(checkboxes)) : 
                false;
            return fieldsChanged || checkboxesChanged;
        };

        // Ajouter les écouteurs pour chaque champ
        fields.forEach(field => {
            // Surveillance des changements
            ['change', 'input'].forEach(eventType => {
                field.addEventListener(eventType, () => {
                    saveButton.disabled = !hasChanges();
                });
            });

            // Gestion de la touche Entrée
            field.addEventListener('keypress', (event) => {
                if (event.key === 'Enter' && !saveButton.disabled) {
                    event.preventDefault();
                    saveButton.click();
                    fields.forEach((f, index) => {
                        initialValues[index] = f.value;
                    });
                    if (checkboxes) {
                        const newCheckboxesState = getCheckboxesState(checkboxes);
                        initialCheckboxesState.splice(0, initialCheckboxesState.length, ...newCheckboxesState);
                    }
                    saveButton.disabled = true;
                }
            });
        });

        // Ajouter les écouteurs pour les checkboxes si elles existent
        if (checkboxes) {
            checkboxes.forEach(checkbox => {
                checkbox.addEventListener('change', () => {
                    saveButton.disabled = !hasChanges();
                });
            });
        }

        // Écouteur pour la sauvegarde
        saveButton.addEventListener('htmx:afterRequest', () => {
            fields.forEach((field, index) => {
                initialValues[index] = field.value;
            });
            if (checkboxes) {
                const newCheckboxesState = getCheckboxesState(checkboxes);
                initialCheckboxesState.splice(0, initialCheckboxesState.length, ...newCheckboxesState);
            }
            saveButton.disabled = true;
        });
    });
}

// Fonction d'initialisation globale
function initializeAllTables() {
    Object.keys(TABLE_CONFIGS).forEach(tableId => {
        initializeTableBehaviors(tableId);
    });
}

// Initialisation au chargement
initializeAllTables();

// Réinitialisation après les mises à jour HTMX
document.body.addEventListener('htmx:afterSettle', function(event) {
    Object.keys(TABLE_CONFIGS).forEach(tableId => {
        if (event.detail.target.querySelector(`#${tableId}`)) {
            initializeTableBehaviors(tableId);
        }
    });
});