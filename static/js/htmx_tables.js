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
        getCheckboxGroup: (id) => document.querySelectorAll(`.activities-${id}`),
        getCheckboxGroupState: (checkboxes) => {
            if (!checkboxes) return null;
            return Array.from(checkboxes).map(cb => ({
                value: cb.value,
                checked: cb.checked
            }));
        },
        checkboxGroupStatesDiffer: (state1, state2) => {
            if (!state1 || !state2 || state1.length !== state2.length) return true;
            return state1.some((s1, index) => {
                const s2 = state2[index];
                return s1.value !== s2.value || s1.checked !== s2.checked;
            });
        }
    },    
    'schedule_table': {
        buttonClass: 'btnSaveLine',
        getFields: (id) => [
            document.getElementById(`name_schedule-${id}`),
            document.getElementById(`start_time-${id}`),
            document.getElementById(`end_time-${id}`)
        ],
        getSelect2: (id) => document.getElementById(`weekdays-${id}`),
        // Fonction pour obtenir la valeur du select2
        getSelect2Value: (select) => {
            if (!select) return null;
            return Array.from(select.selectedOptions).map(option => option.value).sort().join(',');
        }
    },
    'activity_table': {
        buttonClass: 'btn-primary',
        getFields: (id) => [
            document.getElementById(`staff-${id}`),
            document.getElementById(`name-${id}`),
            document.getElementById(`letter-${id}`),
            document.getElementById(`inactivity_message-${id}`),
            document.getElementById(`specific_message-${id}`)
        ],
        getCheckbox: (id) => document.getElementById(`notification-${id}`),
        getSelect2: (id) => document.getElementById(`schedules-${id}`),
        // Fonction pour obtenir la valeur du select2
        getSelect2Value: (select) => {
            if (!select) return null;
            return Array.from(select.selectedOptions).map(option => option.value).sort().join(',');
        }
    },
    'staff_activity_table': {
        buttonClass: 'btn-primary',
        getFields: (id) => [
            document.getElementById(`staff-${id}`),
            document.getElementById(`name-${id}`),
            document.getElementById(`letter-${id}`),
            document.getElementById(`inactivity_message-${id}`),
            document.getElementById(`specific_message-${id}`)
        ],
        getCheckbox: (id) => document.getElementById(`notification-${id}`),
        getSelect2: (id) => document.getElementById(`schedules-${id}`),
        // Fonction pour obtenir la valeur du select2
        getSelect2Value: (select) => {
            if (!select) return null;
            return Array.from(select.selectedOptions).map(option => option.value).sort().join(',');
        }
    },
    'counter_table': {
        buttonClass: 'btnSaveLine',
        getFields: (id) => [
            document.getElementById(`name-${id}`)
        ],
        getCheckboxGroup: (id) => document.querySelectorAll(`.activities-${id}`),
        getCheckboxGroupState: (checkboxes) => {
            if (!checkboxes) return null;
            return Array.from(checkboxes).map(cb => ({
                value: cb.value,
                checked: cb.checked
            }));
        },
        checkboxGroupStatesDiffer: (state1, state2) => {
            if (!state1 || !state2 || state1.length !== state2.length) return true;
            return state1.some((s1, index) => {
                const s2 = state2[index];
                return s1.value !== s2.value || s1.checked !== s2.checked;
            });
        }
    },
    'translation_table': {
        buttonClass: 'btnSaveLine',
        getFields: (id) => [
            document.getElementById(`code-${id}`),
            document.getElementById(`name-${id}`),
            document.getElementById(`translation-${id}`)
        ],
        getCheckboxArray: (id) => [
            document.getElementById(`is_active-${id}`),
            document.getElementById(`voice_is_active-${id}`)
        ]        
    },
};



// Fonction pour obtenir l'état complet d'une ligne
function getRowState(config, id) {
    const state = {
        fields: config.getFields(id).map(field => field?.value || ''),
    };

    // Checkbox unique
    if (config.getCheckbox) {
        const checkbox = config.getCheckbox(id);
        state.checkbox = checkbox?.checked;
    }

    // Array de checkboxes
    if (config.getCheckboxArray) {
        const checkboxes = config.getCheckboxArray(id);
        state.checkboxArray = checkboxes.map(cb => cb?.checked);
    }

    // Groupe de checkboxes (pour les activités)
    if (config.getCheckboxGroup) {
        const checkboxes = config.getCheckboxGroup(id);
        state.checkboxGroup = config.getCheckboxGroupState(checkboxes);
    }

    if (config.getSelect2) {
        const select = config.getSelect2(id);
        state.select2 = config.getSelect2Value(select);
    }

    return state;
}

// Fonction pour comparer deux états
function statesAreDifferent(config, state1, state2) {
    // Comparer les champs simples
    const fieldsChanged = state1.fields.some((value, index) => value !== state2.fields[index]);
    
    // Comparer la checkbox unique si présente
    const checkboxChanged = 
        'checkbox' in state1 && 
        'checkbox' in state2 && 
        state1.checkbox !== state2.checkbox;

    // Comparer l'array de checkboxes si présent
    const checkboxArrayChanged = 
        state1.checkboxArray && 
        state2.checkboxArray && 
        state1.checkboxArray.some((checked, index) => checked !== state2.checkboxArray[index]);

    // Comparer les select2 si présents
    const select2Changed = 
        'select2' in state1 && 
        'select2' in state2 && 
        state1.select2 !== state2.select2;

    // Comparer les groupes de checkboxes si présents
    const checkboxGroupChanged = 
        'checkboxGroup' in state1 && 
        'checkboxGroup' in state2 && 
        config.checkboxGroupStatesDiffer?.(state1.checkboxGroup, state2.checkboxGroup);

    return fieldsChanged || checkboxChanged || checkboxArrayChanged || select2Changed || checkboxGroupChanged;
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
        const firstInput = row.querySelector('input, select');
        const entityId = firstInput.id.split('-')[1];
        const saveButton = row.querySelector(`.${config.buttonClass}`);
        const fields = config.getFields(entityId);
        
        // Stocker l'état initial complet
        const initialState = getRowState(config, entityId);

        // Fonction pour vérifier les changements
        const hasChanges = () => {
            const currentState = getRowState(config, entityId);
            return statesAreDifferent(config, initialState, currentState);
        };

        // Ajouter les écouteurs pour chaque champ
        fields.forEach(field => {
            if (!field) return;

            ['change', 'input'].forEach(eventType => {
                field.addEventListener(eventType, () => {
                    saveButton.disabled = !hasChanges();
                });
            });

            // Gestion de la touche Entrée pour les champs texte
            if (field.tagName === 'INPUT' && field.type === 'text') {
                field.addEventListener('keypress', (event) => {
                    if (event.key === 'Enter' && !saveButton.disabled) {
                        event.preventDefault();
                        saveButton.click();
                    }
                });
            }
        });

        // Écouteur pour la checkbox unique si elle existe
        if (config.getCheckbox) {
            const checkbox = config.getCheckbox(entityId);
            if (checkbox) {
                checkbox.addEventListener('change', () => {
                    saveButton.disabled = !hasChanges();
                });
            }
        }

        // Écouteur pour l'array de checkboxes si il existe
        if (config.getCheckboxArray) {
            const checkboxes = config.getCheckboxArray(entityId);
            checkboxes.forEach(checkbox => {
                if (checkbox) {
                    checkbox.addEventListener('change', () => {
                        saveButton.disabled = !hasChanges();
                    });
                }
            });
        }

    // Écouteur pour l'array de checkboxes si il existe
    if (config.getCheckboxArray) {
        const checkboxes = config.getCheckboxArray(entityId);
        checkboxes.forEach(checkbox => {
            if (checkbox) {
                checkbox.addEventListener('change', () => {
                    saveButton.disabled = !hasChanges();
                });
            }
        });
    }

    // Écouteur pour le groupe de checkboxes si il existe
    if (config.getCheckboxGroup) {
        const checkboxes = config.getCheckboxGroup(entityId);
        checkboxes.forEach(checkbox => {
            checkbox.addEventListener('change', () => {
                saveButton.disabled = !hasChanges();
            });
        });
    }

    // Écouteur pour le select2 si il existe
    if (config.getSelect2) {
        const select2 = config.getSelect2(entityId);
        if (select2) {
            $(select2).on('change', () => {
                saveButton.disabled = !hasChanges();
            });
        }
    }

        // Écouteur pour la sauvegarde
        saveButton.addEventListener('htmx:afterRequest', () => {
            Object.assign(initialState, getRowState(config, entityId));
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