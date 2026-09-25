(function () {
    'use strict';

    const root = document.getElementById('page-editor');
    if (!root) return;

    const page = root.dataset.page;
    const zonesContainer = document.getElementById('editor-zones');
    const inspector = document.getElementById('editor-inspector');
    const preview = document.getElementById('editor-preview');
    const previewShell = document.getElementById('editor-preview-shell');
    const scenarioSelect = document.getElementById('editor-scenario');
    const viewportSelect = document.getElementById('editor-viewport');
    const statusBox = document.getElementById('editor-status');
    const undoButton = document.getElementById('editor-undo');
    const redoButton = document.getElementById('editor-redo');
    const saveButton = document.getElementById('editor-save');
    const publishButton = document.getElementById('editor-publish');
    const discardButton = document.getElementById('editor-discard');
    const diffButton = document.getElementById('editor-diff');
    const applyButton = document.getElementById('editor-apply');
    const themesButton = document.getElementById('editor-themes');
    const screensButton = document.getElementById('editor-screens');
    const historyButton = document.getElementById('editor-history-toggle');
    const historyPanel = document.getElementById('editor-history');
    const revisionsContainer = document.getElementById('editor-revisions');
    const realSizeToggle = document.getElementById('editor-real-size');
    const topToolbar = document.querySelector('.page-editor-toolbar');

    // L'aperçu est épinglé sous la barre d'outils : l'offset suit sa hauteur
    // réelle (les boutons peuvent s'étaler sur plusieurs lignes selon la
    // largeur), sinon l'aperçu passerait dessous et serait masqué.
    function syncStickyOffset() {
        if (!topToolbar) return;
        root.style.setProperty('--page-editor-toolbar-height', (topToolbar.offsetHeight + 8) + 'px');
    }
    syncStickyOffset();
    window.addEventListener('resize', syncStickyOffset);
    if (window.ResizeObserver && topToolbar) {
        new ResizeObserver(syncStickyOffset).observe(topToolbar);
    }

    let adapter;
    let payload;
    let published;
    let draftVersion = 0;
    let hasDraft = false;
    let dirty = false;
    let saving = false;
    let saveQueued = false;
    let selectedComponent = null;
    let undoStack = [];
    let redoStack = [];
    let sortableInstances = [];
    let paletteSources = [];
    let disabledComponents = {};

    function clone(value) {
        return JSON.parse(JSON.stringify(value));
    }

    function canonical(value) {
        return JSON.stringify({layout: value.layout, config: value.config, css: value.css});
    }

    function colorToHex(value) {
        const raw = String(value || '').trim();
        const shortHex = raw.match(/^#?([0-9a-f]{3,4})$/i);
        if (shortHex) {
            return '#' + shortHex[1].slice(0, 3).split('').map(function (part) { return part + part; }).join('').toLowerCase();
        }
        const longHex = raw.match(/^#?([0-9a-f]{6})(?:[0-9a-f]{2})?$/i);
        if (longHex) return '#' + longHex[1].toLowerCase();

        const probe = document.createElement('span');
        probe.style.color = raw;
        if (!probe.style.color) return null;
        probe.hidden = true;
        document.body.appendChild(probe);
        const computed = window.getComputedStyle(probe).color;
        probe.remove();
        const rgb = computed.match(/^rgba?\(\s*(\d+)\D+(\d+)\D+(\d+)/i);
        if (!rgb) return null;
        return '#' + rgb.slice(1, 4).map(function (part) {
            return Number(part).toString(16).padStart(2, '0');
        }).join('');
    }

    function contrastRatio(foreground, background) {
        // Ratio WCAG — les écrans affichent de grands textes : le seuil
        // « grand texte » (3:1) est le repère pertinent ici.
        const parse = function (hex) {
            return [1, 3, 5].map(function (index) {
                return parseInt(hex.slice(index, index + 2), 16) / 255;
            });
        };
        const channel = function (value) {
            return value <= 0.04045 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4);
        };
        const luminance = function (hex) {
            const rgb = parse(hex).map(channel);
            return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
        };
        const light = Math.max(luminance(foreground), luminance(background));
        const dark = Math.min(luminance(foreground), luminance(background));
        return (light + 0.05) / (dark + 0.05);
    }

    function componentContrastRatio(definition) {
        const fontField = definition.css.find(function (field) {
            return field.type === 'color' && field.key.endsWith('font_color');
        });
        const backgroundField = definition.css.find(function (field) {
            return field.type === 'color' && field.key.endsWith('background_color');
        });
        if (!fontField || !backgroundField) return null;
        const font = colorToHex(payload.css[fontField.key]);
        const background = colorToHex(payload.css[backgroundField.key]);
        if (!font || !background) return null;
        return contrastRatio(font, background);
    }

    function appendContrastWarning(container, definition) {
        const ratio = componentContrastRatio(definition);
        if (ratio === null || ratio >= 3) return;
        const warning = document.createElement('div');
        warning.className = 'alert alert-warning py-2 small';
        warning.setAttribute('role', 'note');
        warning.textContent = 'Contraste faible (' + ratio.toFixed(1) + ':1) entre le texte et le fond : illisible à distance.';
        container.appendChild(warning);
    }

    const ZONE_LABELS = {
        header: 'En-tête',
        main: 'Contenu',
        aside: 'Zone latérale',
        footer: 'Pied de page',
        overlay: 'Superposition',
    };

    const ALIGNMENT_LABELS = {stretch: 'Étendre', left: 'Gauche', center: 'Centre', right: 'Droite'};

    const backupKey = 'page-editor-backup-' + page;

    function persistBackup() {
        // Filet local : si l'onglet se ferme ou la session expire, la copie
        // la plus récente est proposée à la réouverture (voir offerLocalBackup).
        try {
            localStorage.setItem(backupKey, JSON.stringify({
                payload: payload,
                saved_at: Date.now(),
            }));
        } catch (error) { /* stockage plein ou désactivé : sans conséquence */ }
    }

    function clearBackup() {
        try { localStorage.removeItem(backupKey); } catch (error) {}
    }

    function setStatus(message, kind) {
        statusBox.replaceChildren();
        statusBox.textContent = message;
        statusBox.className = 'alert py-2 alert-' + (kind || 'light');
    }

    async function requestJSON(url, options) {
        const response = await fetch(url, Object.assign({
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json'}
        }, options || {}));
        const data = await response.json().catch(function () { return {}; });
        if (!response.ok) {
            const error = new Error(data.error || 'La requête a échoué.');
            error.status = response.status;
            throw error;
        }
        return data;
    }

    // Retirer un champ encore focalisé déclenche un blur → change → re-rendu
    // pendant le re-rendu en cours : la file d'attente évite le re-render
    // récursif qui casserait ``replaceChildren``.
    let rendering = false;
    let renderQueued = false;

    function rerenderAll() {
        if (rendering) {
            renderQueued = true;
            return;
        }
        rendering = true;
        try {
            renderPalette();
            renderInspector();
        } finally {
            rendering = false;
        }
        if (renderQueued) {
            renderQueued = false;
            rerenderAll();
        }
    }

    function pushPayloadToPreview() {
        if (!preview.contentWindow || !payload) return;
        preview.contentWindow.postMessage({type: 'page-editor:update', payload: payload}, window.location.origin);
        preview.contentWindow.postMessage({type: 'page-editor:select', componentId: selectedComponent}, window.location.origin);
    }

    function updateButtons() {
        undoButton.disabled = undoStack.length === 0;
        redoButton.disabled = redoStack.length === 0;
        saveButton.disabled = !dirty || saving;
        publishButton.disabled = saving || (!dirty && !hasDraft);
        discardButton.disabled = saving;
        root.dataset.dirty = dirty ? 'true' : 'false';
    }

    function mutate(change, message) {
        undoStack.push(clone(payload));
        if (undoStack.length > 50) undoStack.shift();
        redoStack = [];
        change();
        dirty = true;
        persistBackup();
        rerenderAll();
        pushPayloadToPreview();
        updateButtons();
        setStatus(message || 'Modifications non enregistrées.', 'warning');
    }

    function destroySortables() {
        sortableInstances.forEach(function (instance) { instance.destroy(); });
        sortableInstances = [];
    }

    function renderPalette() {
        destroySortables();
        zonesContainer.replaceChildren();
        adapter.zones.forEach(function (zone) {
            const section = document.createElement('section');
            section.className = 'page-editor-zone';
            const title = document.createElement('h4');
            title.textContent = ZONE_LABELS[zone] || zone;
            const list = document.createElement('div');
            list.className = 'page-editor-component-list';
            list.dataset.zone = zone;
            const componentIds = Object.keys(adapter.components).filter(function (componentId) {
                return payload.layout[componentId].zone === zone;
            }).sort(function (left, right) {
                return payload.layout[left].order - payload.layout[right].order;
            });
            componentIds.forEach(function (componentId) {
                const component = adapter.components[componentId];
                const disabledReason = managedDisabledReason(componentId, component);
                const inScenario = !component.scenarios || component.scenarios.includes(scenarioSelect.value);
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'page-editor-component'
                    + (payload.layout[componentId].visible ? '' : ' is-hidden')
                    + (disabledReason ? ' is-disabled' : '')
                    + (inScenario ? '' : ' is-inactive');
                button.dataset.componentId = componentId;
                button.setAttribute('aria-pressed', componentId === selectedComponent ? 'true' : 'false');
                if (disabledReason) button.title = disabledReason;
                else if (!inScenario) button.title = 'Non affiché dans le scénario courant.';
                const icon = document.createElement('i');
                icon.className = 'bi bi-grip-vertical';
                icon.setAttribute('aria-hidden', 'true');
                const label = document.createElement('span');
                label.textContent = component.label;
                button.append(icon, label);
                if (disabledReason || !inScenario) {
                    const badge = document.createElement('i');
                    badge.className = 'bi ' + (disabledReason ? 'bi-slash-circle' : 'bi-eye-slash') + ' ms-auto';
                    badge.setAttribute('aria-hidden', 'true');
                    button.appendChild(badge);
                }
                button.addEventListener('click', function () { selectComponent(componentId); });
                list.appendChild(button);
            });
            section.append(title, list);
            zonesContainer.appendChild(section);

            sortableInstances.push(new Sortable(list, {
                group: 'page-editor-components',
                animation: 150,
                draggable: '.page-editor-component',
                onMove: function (event) {
                    const componentId = event.dragged.dataset.componentId;
                    const destination = event.to.dataset.zone;
                    return adapter.components[componentId].zones.includes(destination);
                },
                onEnd: function () {
                    const before = clone(payload);
                    document.querySelectorAll('.page-editor-component-list').forEach(function (zoneList) {
                        Array.from(zoneList.children).forEach(function (element, index) {
                            const componentId = element.dataset.componentId;
                            payload.layout[componentId].zone = zoneList.dataset.zone;
                            payload.layout[componentId].order = (index + 1) * 10;
                        });
                    });
                    undoStack.push(before);
                    redoStack = [];
                    dirty = true;
                    persistBackup();
                    rerenderAll();
                    pushPayloadToPreview();
                    updateButtons();
                    setStatus('Ordre des composants modifié.', 'warning');
                }
            }));
        });
    }

    // État « désactivé » piloté par le brouillon : les réglages du mode
    // avancé qui conditionnent un composant sont exposés dans l'inspecteur
    // (case « Afficher » ou mode d'affichage), donc c'est la valeur du
    // brouillon qui décide — jamais la configuration actuellement publiée.
    function managedDisabledReason(componentId, component) {
        if (component.managed_bool || component.hidden_when) {
            if (component.managed_bool && !payload.config[component.managed_bool]) {
                return 'Désactivé : la case « Afficher » de ce composant est décochée.';
            }
            const rule = component.hidden_when;
            if (rule && (rule.values || []).includes(payload.config[rule.key])) {
                return 'Désactivé : son mode d\'affichage est réglé sur « Jamais ».';
            }
            return null;
        }
        return disabledComponents[componentId] || null;
    }

    function formGroup(labelText, control) {
        const wrapper = document.createElement('div');
        wrapper.className = 'mb-3';
        const label = document.createElement('label');
        label.className = 'form-label';
        label.textContent = labelText;
        if (control.id) label.htmlFor = control.id;
        if (control.dataset.labelFor) label.htmlFor = control.dataset.labelFor;
        wrapper.append(label, control);
        return wrapper;
    }

    function addFieldset(titleText) {
        const fieldset = document.createElement('fieldset');
        fieldset.className = 'page-editor-fieldset';
        const legend = document.createElement('legend');
        legend.textContent = titleText;
        fieldset.appendChild(legend);
        inspector.appendChild(fieldset);
        return fieldset;
    }

    function bindValue(control, change) {
        let beforeEdit = null;
        control.addEventListener('focus', function () {
            beforeEdit = clone(payload);
        });
        control.addEventListener('input', function () {
            if (beforeEdit === null) beforeEdit = clone(payload);
            change(control);
            dirty = true;
            pushPayloadToPreview();
            updateButtons();
            setStatus('Modifications non enregistrées.', 'warning');
        });
        control.addEventListener('change', function () {
            if (beforeEdit !== null) {
                undoStack.push(beforeEdit);
                if (undoStack.length > 50) undoStack.shift();
                beforeEdit = null;
                redoStack = [];
                persistBackup();
            }
            const focusId = document.activeElement && document.activeElement.id;
            rerenderAll();
            pushPayloadToPreview();
            updateButtons();
            // L'inspecteur est reconstruit à chaque changement : sans restauration
            // explicite, le focus retombe sur <body> et la navigation clavier
            // repart de zéro.
            if (focusId) {
                const refocused = document.getElementById(focusId);
                if (refocused) refocused.focus();
            }
        });
    }

    function createColorControl(id, value, change) {
        const wrapper = document.createElement('div');
        wrapper.className = 'page-editor-color-control';

        const picker = document.createElement('input');
        picker.type = 'color';
        picker.className = 'form-control form-control-color page-editor-color-picker';
        picker.id = id + '-picker';
        picker.title = 'Choisir une couleur';
        picker.setAttribute('aria-label', 'Choisir une couleur');

        const text = document.createElement('input');
        text.type = 'text';
        text.className = 'form-control page-editor-color-value';
        text.id = id;
        text.value = value || '';
        text.placeholder = '#008B8B';
        text.autocomplete = 'off';
        wrapper.dataset.labelFor = text.id;

        const initialHex = colorToHex(text.value);
        picker.value = initialHex || '#000000';
        if (!initialHex && text.value) text.classList.add('is-invalid');

        bindValue(text, change);
        text.addEventListener('input', function () {
            const hex = colorToHex(text.value);
            text.classList.toggle('is-invalid', !hex);
            if (hex) picker.value = hex;
        });
        picker.addEventListener('input', function () {
            text.value = picker.value.toUpperCase();
            text.classList.remove('is-invalid');
            text.dispatchEvent(new Event('input', {bubbles: true}));
        });
        picker.addEventListener('change', function () {
            text.dispatchEvent(new Event('change', {bubbles: true}));
        });
        wrapper.append(picker, text);
        return wrapper;
    }

    function mostCommonPaletteColor(keys) {
        const counts = new Map();
        keys.forEach(function (key) {
            const hex = colorToHex(payload.css[key]);
            if (hex) counts.set(hex, (counts.get(hex) || 0) + 1);
        });
        return Array.from(counts.entries()).sort(function (left, right) {
            return right[1] - left[1];
        })[0]?.[0] || '#008B8B';
    }

    function renderPagePalette() {
        if (!adapter.palette || !adapter.palette.length) return;
        const paletteFields = addFieldset('Palette de la page');
        const help = document.createElement('p');
        help.className = 'small text-muted mb-3';
        help.textContent = 'Définissez vos couleurs puis appliquez-les en une fois aux éléments correspondants.';
        paletteFields.appendChild(help);

        if (paletteSources.length) {
            const copyCard = document.createElement('div');
            copyCard.className = 'page-editor-palette-copy';
            const copyHeading = document.createElement('div');
            copyHeading.className = 'fw-semibold mb-1';
            copyHeading.textContent = 'Réutiliser une palette';
            const copyHelp = document.createElement('div');
            copyHelp.className = 'small text-muted mb-2';
            copyHelp.textContent = 'Importe les couleurs publiées d’une autre page dans ce brouillon.';
            const copyControls = document.createElement('div');
            copyControls.className = 'page-editor-palette-copy-controls';
            const sourceSelect = document.createElement('select');
            sourceSelect.id = 'editor-palette-source';
            sourceSelect.className = 'form-select form-select-sm';
            sourceSelect.setAttribute('aria-label', 'Page dont réutiliser la palette');
            const placeholder = document.createElement('option');
            placeholder.value = '';
            placeholder.textContent = 'Choisir une page…';
            sourceSelect.appendChild(placeholder);
            paletteSources.forEach(function (source) {
                const option = document.createElement('option');
                option.value = source.page;
                option.textContent = source.label;
                sourceSelect.appendChild(option);
            });
            const copyButton = document.createElement('button');
            copyButton.id = 'editor-palette-copy';
            copyButton.type = 'button';
            copyButton.className = 'btn btn-sm btn-outline-primary';
            copyButton.textContent = 'Copier la palette';
            copyButton.disabled = true;
            sourceSelect.addEventListener('change', function () {
                copyButton.disabled = !sourceSelect.value;
            });
            copyButton.addEventListener('click', function () {
                const source = paletteSources.find(function (item) { return item.page === sourceSelect.value; });
                if (!source) return;
                const sourceRoles = new Map(source.roles.map(function (role) { return [role.id, role]; }));
                const paletteChanges = [];
                adapter.palette.forEach(function (targetRole) {
                    const sourceRole = sourceRoles.get(targetRole.id);
                    if (!sourceRole) return;
                    const counts = new Map();
                    sourceRole.values.forEach(function (raw) {
                        const hex = colorToHex(raw);
                        if (hex) counts.set(hex, (counts.get(hex) || 0) + 1);
                    });
                    const color = Array.from(counts.entries()).sort(function (left, right) {
                        return right[1] - left[1];
                    })[0]?.[0];
                    if (color) paletteChanges.push({role: targetRole, color: color.toUpperCase()});
                });
                if (!paletteChanges.length) {
                    setStatus('Aucune couleur compatible dans cette palette.', 'warning');
                    return;
                }
                mutate(function () {
                    paletteChanges.forEach(function (change) {
                        change.role.keys.forEach(function (key) { payload.css[key] = change.color; });
                    });
                }, 'Palette « ' + source.label + ' » appliquée à ' + paletteChanges.length + ' groupe(s).');
            });
            copyControls.append(sourceSelect, copyButton);
            copyCard.append(copyHeading, copyHelp, copyControls);
            paletteFields.appendChild(copyCard);
        }

        adapter.palette.forEach(function (role) {
            const colors = new Set(role.keys.map(function (key) { return colorToHex(payload.css[key]); }).filter(Boolean));
            const card = document.createElement('div');
            card.className = 'page-editor-palette-card';

            const heading = document.createElement('div');
            heading.className = 'd-flex align-items-start justify-content-between gap-2 mb-2';
            const text = document.createElement('div');
            const label = document.createElement('div');
            label.className = 'fw-semibold';
            label.textContent = role.label;
            const description = document.createElement('div');
            description.className = 'small text-muted';
            description.textContent = role.description;
            text.append(label, description);
            heading.appendChild(text);
            if (colors.size > 1) {
                const mixed = document.createElement('span');
                mixed.className = 'badge text-bg-light border';
                mixed.textContent = 'Mixte';
                heading.appendChild(mixed);
            }

            const controls = document.createElement('div');
            controls.className = 'page-editor-palette-controls';
            const picker = document.createElement('input');
            picker.type = 'color';
            picker.className = 'form-control form-control-color page-editor-color-picker';
            picker.id = 'editor-palette-' + role.id + '-picker';
            picker.value = mostCommonPaletteColor(role.keys);
            picker.setAttribute('aria-label', role.label);
            const value = document.createElement('input');
            value.type = 'text';
            value.className = 'form-control page-editor-color-value';
            value.id = 'editor-palette-' + role.id;
            value.value = picker.value.toUpperCase();
            value.autocomplete = 'off';
            value.setAttribute('aria-label', role.label + ' en notation CSS');
            const apply = document.createElement('button');
            apply.type = 'button';
            apply.className = 'btn btn-outline-primary';
            apply.textContent = 'Appliquer';

            function validatePaletteColor() {
                const hex = colorToHex(value.value);
                value.classList.toggle('is-invalid', !hex);
                apply.disabled = !hex;
                if (hex) picker.value = hex;
                return hex;
            }

            picker.addEventListener('input', function () {
                value.value = picker.value.toUpperCase();
                validatePaletteColor();
            });
            value.addEventListener('input', validatePaletteColor);
            apply.addEventListener('click', function () {
                const hex = validatePaletteColor();
                if (!hex) return;
                mutate(function () {
                    role.keys.forEach(function (key) { payload.css[key] = hex.toUpperCase(); });
                }, role.label + ' appliquée à ' + role.keys.length + ' réglage(s).');
            });

            controls.append(picker, value, apply);
            card.append(heading, controls);
            paletteFields.appendChild(card);
        });
    }

    function createMarkerToolbar(control, markers) {
        const toolbar = document.createElement('div');
        toolbar.className = 'page-editor-markers';
        const heading = document.createElement('div');
        heading.className = 'page-editor-markers-heading';
        heading.textContent = 'Balises dynamiques';
        heading.title = 'Ces balises sont remplacées automatiquement par les informations réelles à l’affichage.';
        const buttons = document.createElement('div');
        buttons.className = 'page-editor-marker-list';

        markers.forEach(function (marker) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'page-editor-marker';
            button.title = marker.description;
            button.setAttribute('aria-label', 'Insérer ' + marker.token + ' : ' + marker.description);
            const token = document.createElement('code');
            token.textContent = marker.token;
            const label = document.createElement('span');
            label.textContent = marker.label;
            button.append(token, label);
            button.addEventListener('pointerdown', function (event) {
                event.preventDefault();
            });
            button.addEventListener('click', function () {
                const start = control.selectionStart ?? control.value.length;
                const end = control.selectionEnd ?? start;
                control.focus();
                control.setRangeText(marker.token, start, end, 'end');
                control.dispatchEvent(new Event('input', {bubbles: true}));
            });
            buttons.appendChild(button);
        });
        toolbar.append(heading, buttons);
        return toolbar;
    }

    function renderInspector() {
        inspector.replaceChildren();
        if (!selectedComponent || !adapter.components[selectedComponent]) {
            renderPagePalette();
            const empty = document.createElement('p');
            empty.className = 'text-muted';
            empty.textContent = 'Sélectionnez un composant.';
            inspector.appendChild(empty);
            return;
        }
        const definition = adapter.components[selectedComponent];
        const layout = payload.layout[selectedComponent];
        const heading = document.createElement('p');
        heading.className = 'fw-semibold';
        heading.textContent = definition.label;
        inspector.appendChild(heading);

        const disabledReason = managedDisabledReason(selectedComponent, definition);
        if (disabledReason) {
            // Un composant désactivé par sa configuration est invisible dans
            // l'aperçu : sans ce repère, on ne sait pas où le réactiver.
            const hint = document.createElement('p');
            hint.className = 'alert alert-info py-2 small mb-3';
            hint.textContent = disabledReason
                + ' Le réglage est disponible dans la section « Contenu » ci-dessous.';
            inspector.appendChild(hint);
        }

        const layoutFields = addFieldset('Disposition');
        const visibility = document.createElement('input');
        visibility.type = 'checkbox';
        visibility.className = 'form-check-input';
        visibility.id = 'editor-field-visible';
        visibility.checked = layout.visible;
        bindValue(visibility, function (control) { layout.visible = control.checked; });
        const visibilityWrapper = document.createElement('div');
        visibilityWrapper.className = 'form-check mb-3';
        const visibilityLabel = document.createElement('label');
        visibilityLabel.className = 'form-check-label';
        visibilityLabel.htmlFor = visibility.id;
        visibilityLabel.textContent = 'Afficher ce composant';
        visibilityWrapper.append(visibility, visibilityLabel);
        layoutFields.appendChild(visibilityWrapper);

        const span = document.createElement('input');
        span.type = 'range';
        span.className = 'form-range';
        span.id = 'editor-field-span';
        span.min = '1';
        span.max = '12';
        span.value = String(layout.span);
        bindValue(span, function (control) { layout.span = Number(control.value); });
        layoutFields.appendChild(formGroup('Largeur (' + layout.span + '/12)', span));

        const spanPresets = document.createElement('div');
        spanPresets.className = 'btn-group btn-group-sm w-100 mb-3';
        spanPresets.setAttribute('role', 'group');
        spanPresets.setAttribute('aria-label', 'Largeurs prédéfinies');
        [['25 %', 3], ['33 %', 4], ['50 %', 6], ['66 %', 8], ['75 %', 9], ['100 %', 12]].forEach(function (preset) {
            const presetButton = document.createElement('button');
            presetButton.type = 'button';
            presetButton.className = 'btn btn-outline-secondary'
                + (layout.span === preset[1] ? ' active' : '');
            presetButton.textContent = preset[0];
            presetButton.addEventListener('click', function () {
                if (layout.span === preset[1]) return;
                mutate(function () { layout.span = preset[1]; },
                    'Largeur réglée sur ' + preset[0] + '.');
            });
            spanPresets.appendChild(presetButton);
        });
        layoutFields.appendChild(spanPresets);

        const alignment = document.createElement('select');
        alignment.className = 'form-select';
        alignment.id = 'editor-field-alignment';
        [['stretch', 'Étendre'], ['left', 'Gauche'], ['center', 'Centre'], ['right', 'Droite']].forEach(function (choice) {
            const option = document.createElement('option');
            option.value = choice[0];
            option.textContent = choice[1];
            option.selected = layout.alignment === choice[0];
            alignment.appendChild(option);
        });
        bindValue(alignment, function (control) { layout.alignment = control.value; });
        layoutFields.appendChild(formGroup('Alignement', alignment));

        const movement = document.createElement('div');
        movement.className = 'btn-group w-100';
        [['Monter', -1], ['Descendre', 1]].forEach(function (item) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-sm btn-outline-secondary';
            button.textContent = item[0];
            button.addEventListener('click', function () { moveSelected(item[1]); });
            movement.appendChild(button);
        });
        layoutFields.appendChild(movement);

        const resetButton = document.createElement('button');
        resetButton.type = 'button';
        resetButton.className = 'btn btn-sm btn-outline-secondary w-100 mt-2';
        resetButton.textContent = 'Réinitialiser ce composant';
        resetButton.title = 'Reprend les réglages de la version publiée pour ce composant.';
        resetButton.addEventListener('click', function () {
            if (!published || !published.layout[selectedComponent]) return;
            mutate(function () {
                payload.layout[selectedComponent] = clone(published.layout[selectedComponent]);
                definition.config.forEach(function (field) {
                    payload.config[field.key] = clone(published.config[field.key]);
                });
                definition.css.forEach(function (field) {
                    payload.css[field.key] = clone(published.css[field.key]);
                });
            }, 'Composant réinitialisé sur la version publiée.');
        });
        layoutFields.appendChild(resetButton);

        if (definition.config.length) {
            // Réglages qui conditionnent l'affichage d'un composant : leur
            // modification rafraîchit aussi les badges de la liste.
            const gatedKeys = new Set();
            Object.values(adapter.components).forEach(function (item) {
                if (item.managed_bool) gatedKeys.add(item.managed_bool);
                if (item.hidden_when) gatedKeys.add(item.hidden_when.key);
            });
            const setConfigValue = function (key, value) {
                payload.config[key] = value;
                if (gatedKeys.has(key)) renderPalette();
            };
            const contentFields = addFieldset('Contenu');
            definition.config.forEach(function (field) {
                let control;
                if (field.type === 'bool') {
                    control = document.createElement('input');
                    control.type = 'checkbox';
                    control.className = 'form-check-input ms-2';
                    control.checked = Boolean(payload.config[field.key]);
                    bindValue(control, function (element) { setConfigValue(field.key, element.checked); });
                } else if (field.type === 'select') {
                    control = document.createElement('select');
                    control.className = 'form-select';
                    (field.choices || []).forEach(function (choice) {
                        const option = document.createElement('option');
                        option.value = choice[0];
                        option.textContent = choice[1];
                        control.appendChild(option);
                    });
                    control.value = payload.config[field.key] == null ? '' : payload.config[field.key];
                    bindValue(control, function (element) { setConfigValue(field.key, element.value); });
                } else if (field.type === 'int') {
                    control = document.createElement('input');
                    control.type = 'number';
                    control.className = 'form-control';
                    control.step = '1';
                    control.value = payload.config[field.key] == null ? 0 : payload.config[field.key];
                    bindValue(control, function (element) {
                        setConfigValue(field.key, Number.parseInt(element.value, 10) || 0);
                    });
                } else {
                    control = document.createElement('textarea');
                    control.className = 'form-control';
                    control.rows = 3;
                    control.value = payload.config[field.key] == null ? '' : payload.config[field.key];
                    bindValue(control, function (element) { setConfigValue(field.key, element.value); });
                }
                control.id = 'editor-config-' + field.key;
                const group = formGroup(field.label, control);
                if (field.markers && field.markers.length && control instanceof HTMLTextAreaElement) {
                    group.appendChild(createMarkerToolbar(control, field.markers));
                }
                contentFields.appendChild(group);
            });
        }

        if (definition.css.length) {
            const appearanceFields = addFieldset('Apparence');
            definition.css.forEach(function (field) {
                let control;
                if (field.type === 'color') {
                    control = createColorControl(
                        'editor-css-' + field.key,
                        payload.css[field.key],
                        function (element) { payload.css[field.key] = element.value; }
                    );
                } else {
                    control = document.createElement('input');
                    control.type = 'text';
                    control.className = 'form-control';
                    control.id = 'editor-css-' + field.key;
                    control.value = payload.css[field.key] || '';
                    control.placeholder = field.type === 'number' ? '400' : '32px';
                    bindValue(control, function (element) { payload.css[field.key] = element.value; });
                }
                appearanceFields.appendChild(formGroup(field.label, control));
            });
            appendContrastWarning(appearanceFields, definition);
        }

        // La palette de la page est rendue après les propriétés du composant :
        // les réglages du bloc sélectionné restent accessibles immédiatement.
        renderPagePalette();
    }

    function selectComponent(componentId) {
        selectedComponent = componentId;
        rerenderAll();
        pushPayloadToPreview();
    }

    function moveSelected(direction) {
        const selected = payload.layout[selectedComponent];
        const siblings = Object.keys(payload.layout).filter(function (componentId) {
            return payload.layout[componentId].zone === selected.zone;
        }).sort(function (left, right) {
            return payload.layout[left].order - payload.layout[right].order;
        });
        const index = siblings.indexOf(selectedComponent);
        const targetIndex = index + direction;
        if (targetIndex < 0 || targetIndex >= siblings.length) return;
        mutate(function () {
            const target = payload.layout[siblings[targetIndex]];
            const order = selected.order;
            selected.order = target.order;
            target.order = order;
        }, 'Ordre du composant modifié.');
    }

    function populateSelectors() {
        scenarioSelect.replaceChildren();
        adapter.scenarios.forEach(function (scenario) {
            const option = document.createElement('option');
            option.value = scenario.id;
            option.textContent = scenario.label;
            scenarioSelect.appendChild(option);
        });
        viewportSelect.replaceChildren();
        adapter.viewports.forEach(function (viewport) {
            const option = document.createElement('option');
            option.value = viewport.id;
            option.textContent = viewport.label;
            viewportSelect.appendChild(option);
        });
    }

    function loadPreview() {
        preview.src = '/admin/page-editor/' + encodeURIComponent(page) + '/preview?scenario=' + encodeURIComponent(scenarioSelect.value);
    }

    function resizePreview() {
        const viewport = adapter.viewports.find(function (item) { return item.id === viewportSelect.value; }) || adapter.viewports[0];
        const availableWidth = Math.max(280, previewShell.clientWidth - 32);
        const scale = realSizeToggle && realSizeToggle.checked
            ? 1
            : Math.min(1, availableWidth / viewport.width);
        preview.width = String(viewport.width);
        preview.height = String(viewport.height);
        preview.style.transform = 'translateX(-50%) scale(' + scale + ')';
        previewShell.style.height = Math.ceil(viewport.height * scale + 32) + 'px';
    }

    function renderRevisions(revisions) {
        revisionsContainer.replaceChildren();
        if (!revisions.length) {
            revisionsContainer.textContent = 'Aucune publication enregistrée.';
            return;
        }
        const list = document.createElement('div');
        list.className = 'list-group';
        revisions.forEach(function (revision) {
            const item = document.createElement('div');
            item.className = 'list-group-item d-flex align-items-center justify-content-between gap-2';
            const label = document.createElement('span');
            const date = revision.created_at ? new Date(revision.created_at).toLocaleString('fr-FR') : '';
            label.textContent = 'Révision ' + revision.revision + ' · ' + date + (revision.author ? ' · ' + revision.author : '');
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-sm btn-outline-primary';
            button.textContent = 'Restaurer';
            button.addEventListener('click', function () { restoreRevision(revision.revision); });
            item.append(label, button);
            list.appendChild(item);
        });
        revisionsContainer.appendChild(list);
    }

    function offerLocalBackup(state) {
        let backup = null;
        try {
            backup = JSON.parse(localStorage.getItem(backupKey) || 'null');
        } catch (error) { /* JSON corrompu : ignoré */ }
        if (!backup || !backup.payload || backup.payload.page !== page) return;
        if (canonical(backup.payload) === canonical(payload)) {
            clearBackup();
            return;
        }
        const serverTime = state.draft_updated_at ? Date.parse(state.draft_updated_at) : 0;
        if (!backup.saved_at || backup.saved_at <= serverTime) {
            clearBackup();
            return;
        }
        statusBox.replaceChildren();
        statusBox.className = 'alert py-2 alert-warning';
        const text = document.createElement('span');
        text.textContent = 'Une copie locale non synchronisée ('
            + new Date(backup.saved_at).toLocaleString('fr-FR')
            + ') a été retrouvée.';
        const restoreButton = document.createElement('button');
        restoreButton.type = 'button';
        restoreButton.className = 'btn btn-sm btn-warning ms-2';
        restoreButton.textContent = 'Restaurer';
        restoreButton.addEventListener('click', function () {
            payload = clone(backup.payload);
            dirty = true;
            rerenderAll();
            pushPayloadToPreview();
            updateButtons();
            setStatus('Copie locale restaurée : enregistrez le brouillon pour la conserver.', 'warning');
        });
        const ignoreButton = document.createElement('button');
        ignoreButton.type = 'button';
        ignoreButton.className = 'btn btn-sm btn-outline-secondary ms-1';
        ignoreButton.textContent = 'Ignorer';
        ignoreButton.addEventListener('click', function () {
            clearBackup();
            setStatus(hasDraft ? 'Brouillon partagé chargé.' : 'Configuration publiée chargée.',
                hasDraft ? 'info' : 'light');
        });
        statusBox.append(text, restoreButton, ignoreButton);
    }

    async function loadState(message) {
        root.setAttribute('aria-busy', 'true');
        try {
            const state = await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/state');
            adapter = state.adapter;
            paletteSources = state.palette_sources || [];
            disabledComponents = state.disabled_components || {};
            published = clone(state.published);
            hasDraft = Boolean(state.draft);
            payload = clone(state.draft || state.published);
            draftVersion = state.draft_version;
            dirty = false;
            undoStack = [];
            redoStack = [];
            selectedComponent = selectedComponent && adapter.components[selectedComponent] ? selectedComponent : Object.keys(adapter.components)[0];
            populateSelectors();
            renderPalette();
            renderInspector();
            renderRevisions(state.revisions || []);
            loadPreview();
            applyButton.disabled = state.published_revision < 1;
            updateButtons();
            if (message) {
                setStatus(message, hasDraft ? 'info' : 'light');
            } else {
                offerLocalBackup(state);
                if (!statusBox.querySelector('button')) {
                    setStatus(hasDraft ? 'Brouillon partagé chargé.' : 'Configuration publiée chargée.',
                        hasDraft ? 'info' : 'light');
                }
            }
        } catch (error) {
            setStatus(error.message, 'danger');
        } finally {
            root.setAttribute('aria-busy', 'false');
        }
    }

    async function saveDraft() {
        // Une sauvegarde en cours ne doit pas être lancée en double ni
        // écraser la saisie faite pendant la requête : on mémorise l'état
        // envoyé et on ne remplace le document local que s'il n'a pas bougé.
        if (saving) {
            saveQueued = true;
            return null;
        }
        saving = true;
        updateButtons();
        const sent = canonical(payload);
        try {
            const data = await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/draft', {
                method: 'PUT',
                body: JSON.stringify({draft_version: draftVersion, payload: payload})
            });
            draftVersion = data.draft_version;
            hasDraft = true;
            if (canonical(payload) === sent) {
                payload = clone(data.draft);
                dirty = false;
                if (canonical(payload) !== sent) {
                    // Le serveur a normalisé des valeurs : les refléter dans
                    // l'inspecteur pour éviter toute divergence silencieuse.
                    rerenderAll();
                    pushPayloadToPreview();
                }
                setStatus('Brouillon partagé enregistré.', 'success');
            } else {
                dirty = true;
                setStatus('Brouillon enregistré ; les modifications saisies pendant l’enregistrement restent à sauvegarder.', 'warning');
            }
            return data;
        } finally {
            saving = false;
            updateButtons();
            if (saveQueued) {
                saveQueued = false;
                saveDraft().catch(function (error) {
                    setStatus(error.message, error.status === 409 ? 'warning' : 'danger');
                });
            }
        }
    }

    // --- Comparaison publié/brouillon et contrôle qualité ---

    const SECTION_LABELS = {layout: 'Disposition', config: 'Contenu', css: 'Apparence'};

    function formatDiffValue(change, value) {
        if (change.section === 'layout') {
            if (change.key === 'visible') return value ? 'affiché' : 'masqué';
            if (change.key === 'zone') return ZONE_LABELS[value] || String(value);
            if (change.key === 'span') return String(value) + '/12';
            if (change.key === 'alignment') return ALIGNMENT_LABELS[value] || String(value);
        }
        if (typeof value === 'boolean') return value ? 'Oui' : 'Non';
        if (value === null || value === undefined || value === '') return '∅';
        if (change.section === 'config') {
            const field = Object.values(adapter.components)
                .flatMap(function (component) { return component.config || []; })
                .find(function (item) { return item.key === change.key && item.choices; });
            if (field) {
                const choice = field.choices.find(function (entry) { return entry[0] === value; });
                if (choice) return choice[1];
            }
        }
        const text = String(value);
        return text.length > 60 ? text.slice(0, 57) + '…' : text;
    }

    function buildDiffList(diff) {
        const container = document.createElement('div');
        if (diff.identical) {
            const empty = document.createElement('p');
            empty.className = 'text-muted';
            empty.textContent = 'Aucune différence avec la version publiée.';
            container.appendChild(empty);
            return container;
        }
        const total = diff.changes.length;
        const summary = document.createElement('p');
        const parts = ['layout', 'config', 'css']
            .filter(function (section) { return diff.counts[section] > 0; })
            .map(function (section) { return diff.counts[section] + ' ' + SECTION_LABELS[section].toLowerCase(); });
        summary.textContent = total + ' modification(s) : ' + parts.join(' · ');
        container.appendChild(summary);
        ['layout', 'config', 'css'].forEach(function (section) {
            const sectionChanges = diff.changes.filter(function (change) { return change.section === section; });
            if (!sectionChanges.length) return;
            const heading = document.createElement('h6');
            heading.className = 'mt-3 mb-1';
            heading.textContent = SECTION_LABELS[section];
            const list = document.createElement('ul');
            list.className = 'page-editor-diff-list';
            sectionChanges.forEach(function (change) {
                const item = document.createElement('li');
                const subject = document.createElement('strong');
                subject.textContent = (change.component ? change.component + ' · ' : '') + change.label;
                const arrow = document.createElement('span');
                arrow.textContent = ' : ' + formatDiffValue(change, change.old) + ' → ';
                const value = document.createElement('span');
                value.className = 'page-editor-diff-new';
                value.textContent = formatDiffValue(change, change.new);
                item.append(subject, arrow, value);
                list.appendChild(item);
            });
            container.append(heading, list);
        });
        return container;
    }

    // Contrôles de qualité : mesures dans le DOM réel de l'aperçu (même origine)
    // plus états connus côté payload (masqués, désactivés, contrastes).
    function runQualityChecks() {
        const findings = [];
        Object.keys(payload.layout).forEach(function (componentId) {
            if (!payload.layout[componentId].visible) {
                const label = adapter.components[componentId]
                    ? adapter.components[componentId].label : componentId;
                findings.push({level: 'warning', message: '« ' + label + ' » sera masqué.'});
            }
        });
        Object.keys(adapter.components).forEach(function (componentId) {
            const definition = adapter.components[componentId];
            const reason = managedDisabledReason(componentId, definition);
            if (reason) {
                findings.push({level: 'info', message: '« ' + definition.label + ' » : ' + reason});
            }
        });
        Object.keys(adapter.components).forEach(function (componentId) {
            const definition = adapter.components[componentId];
            const ratio = componentContrastRatio(definition);
            if (ratio !== null && ratio < 3) {
                findings.push({
                    level: 'warning',
                    message: 'Contraste faible (' + ratio.toFixed(1) + ':1) sur « ' + definition.label + ' ».',
                });
            }
        });

        try {
            const doc = preview.contentDocument;
            const viewport = adapter.viewports.find(function (item) {
                return item.id === viewportSelect.value;
            }) || adapter.viewports[0];
            if (!doc || !doc.documentElement) return findings;
            if (doc.documentElement.scrollWidth > viewport.width + 4) {
                findings.push({
                    level: 'warning',
                    message: 'Du contenu dépasse la largeur du format « ' + viewport.label + ' ».',
                });
            }
            const minFont = {announce: 20, patient: 14, phone: 12}[page] || 12;
            const smallFonts = new Set();
            doc.querySelectorAll('[data-page-editor-component]').forEach(function (componentEl) {
                if (componentEl.offsetParent === null) return;
                const componentId = componentEl.dataset.pageEditorComponent;
                const label = (adapter.components[componentId] || {}).label || componentId;
                Array.from(componentEl.querySelectorAll('*')).concat([componentEl]).forEach(function (el) {
                    const text = Array.from(el.childNodes).some(function (node) {
                        return node.nodeType === Node.TEXT_NODE && node.textContent.trim();
                    });
                    if (!text || el.offsetParent === null) return;
                    const size = parseFloat(preview.contentWindow.getComputedStyle(el).fontSize);
                    if (size > 0 && size < minFont) smallFonts.add(label);
                });
            });
            smallFonts.forEach(function (label) {
                findings.push({
                    level: 'warning',
                    message: 'Texte potentiellement trop petit sur « ' + label + ' » (< ' + minFont + 'px).',
                });
            });
            if (page === 'patient') {
                let smallTargets = 0;
                doc.querySelectorAll('button, a, [role="button"], .page-editor-demo-button').forEach(function (el) {
                    if (el.offsetParent === null) return;
                    if (el.getBoundingClientRect().height < 44) smallTargets += 1;
                });
                if (smallTargets) {
                    findings.push({
                        level: 'warning',
                        message: smallTargets + ' zone(s) tactile(s) de moins de 44px de haut : difficiles à toucher sur la borne.',
                    });
                }
            }
        } catch (error) {
            findings.push({level: 'info', message: 'Aperçu non chargé : contrôles visuels ignorés.'});
        }
        return findings;
    }

    function openDialog(options) {
        return new Promise(function (resolve) {
            const backdrop = document.createElement('div');
            backdrop.className = 'page-editor-dialog-backdrop';
            const dialog = document.createElement('div');
            dialog.className = 'page-editor-dialog';
            dialog.setAttribute('role', 'dialog');
            dialog.setAttribute('aria-modal', 'true');
            dialog.setAttribute('aria-label', options.title);

            const header = document.createElement('div');
            header.className = 'page-editor-dialog-header';
            const title = document.createElement('h5');
            title.className = 'mb-0';
            title.textContent = options.title;
            const closeButton = document.createElement('button');
            closeButton.type = 'button';
            closeButton.className = 'btn-close';
            closeButton.setAttribute('aria-label', 'Fermer');
            header.append(title, closeButton);

            const body = document.createElement('div');
            body.className = 'page-editor-dialog-body';
            body.appendChild(options.body);

            const footer = document.createElement('div');
            footer.className = 'page-editor-dialog-footer';
            let focusTarget = closeButton;
            (options.actions || []).forEach(function (action) {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = action.className || 'btn btn-secondary';
                button.textContent = action.label;
                button.addEventListener('click', function () { close(action.value); });
                if (action.autofocus) focusTarget = button;
                footer.appendChild(button);
            });

            function close(result) {
                document.removeEventListener('keydown', onKey);
                backdrop.remove();
                resolve(result);
            }
            function onKey(event) {
                if (event.key === 'Escape') close(null);
            }
            closeButton.addEventListener('click', function () { close(null); });
            backdrop.addEventListener('mousedown', function (event) {
                if (event.target === backdrop) close(null);
            });
            document.addEventListener('keydown', onKey);
            dialog.append(header, body, footer);
            backdrop.appendChild(dialog);
            document.body.appendChild(backdrop);
            // Handle exposé pour les actions internes du dialogue (ex. la
            // boîte « Thèmes » qui applique/supprime puis se ferme d'elle-même).
            backdrop.closeDialog = close;
            focusTarget.focus();
        });
    }

    function buildFindingsList() {
        const findings = runQualityChecks();
        if (!findings.length) return null;
        const fragment = document.createElement('div');
        const heading = document.createElement('h6');
        heading.className = 'mt-3 mb-1';
        heading.textContent = 'Points de vigilance';
        const list = document.createElement('ul');
        list.className = 'page-editor-diff-list';
        findings.forEach(function (finding) {
            const item = document.createElement('li');
            item.className = finding.level === 'warning' ? 'text-warning-emphasis' : 'text-muted';
            item.textContent = finding.message;
            list.appendChild(item);
        });
        fragment.append(heading, list);
        return fragment;
    }

    async function fetchDiff() {
        return await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/diff', {
            method: 'POST',
            body: JSON.stringify({payload: payload}),
        });
    }

    async function showDiff() {
        try {
            const diff = await fetchDiff();
            const body = buildDiffList(diff);
            const findings = buildFindingsList();
            if (findings) body.appendChild(findings);
            await openDialog({
                title: 'Différences avec la version publiée',
                body: body,
                actions: [{label: 'Fermer', className: 'btn btn-secondary', value: true, autofocus: true}],
            });
        } catch (error) {
            setStatus(error.message, 'danger');
        }
    }

    // --- Écrans connectés et accusés de rechargement ---

    const SCREEN_STATUS = {
        current: {label: 'À jour', className: 'text-bg-success'},
        stale: {label: 'Ancienne version', className: 'text-bg-warning'},
        pending: {label: 'Sans accusé', className: 'text-bg-secondary'},
    };

    function buildScreensList(data) {
        const container = document.createElement('div');
        const intro = document.createElement('p');
        intro.className = 'small text-muted';
        intro.textContent = 'Révision publiée : ' + data.published_revision
            + '. Chaque écran déclare la révision qu’il affiche à la (re)connexion '
            + '— un écran « sans accusé » tourne probablement sous une version '
            + 'antérieure à cette fonctionnalité.';
        container.appendChild(intro);
        if (!data.screens.length) {
            const empty = document.createElement('p');
            empty.className = 'text-muted';
            empty.textContent = 'Aucun écran connecté sur cette page.';
            container.appendChild(empty);
            return container;
        }
        const list = document.createElement('ul');
        list.className = 'page-editor-diff-list';
        data.screens.forEach(function (screen) {
            const item = document.createElement('li');
            item.className = 'd-flex align-items-center gap-2';
            const name = document.createElement('span');
            const when = screen.at
                ? ' · accusé ' + new Date(screen.at * 1000).toLocaleTimeString('fr-FR')
                : '';
            name.textContent = screen.username + ' (#' + screen.sid + ')'
                + ' — révision ' + (screen.revision === null ? 'inconnue' : screen.revision) + when;
            const badge = document.createElement('span');
            const status = SCREEN_STATUS[screen.status] || SCREEN_STATUS.pending;
            badge.className = 'badge ms-auto ' + status.className;
            badge.textContent = status.label;
            item.append(name, badge);
            list.appendChild(item);
        });
        container.appendChild(list);
        return container;
    }

    async function fetchScreens() {
        return await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/screens');
    }

    async function showScreens() {
        try {
            const data = await fetchScreens();
            await openDialog({
                title: 'Écrans connectés',
                body: buildScreensList(data),
                actions: [{label: 'Fermer', className: 'btn btn-secondary', value: true, autofocus: true}],
            });
        } catch (error) {
            setStatus(error.message, 'danger');
        }
    }

    // Après « Appliquer » : surveille les accusés jusqu'à ce que tous les
    // écrans connectés affichent la révision publiée (ou 30 s max).
    async function watchScreenAcks() {
        const deadline = Date.now() + 30000;
        while (Date.now() < deadline) {
            const data = await fetchScreens();
            const total = data.screens.length;
            const confirmed = data.screens.filter(function (screen) {
                return screen.status === 'current';
            }).length;
            if (!total) {
                setStatus('Ordre de rechargement envoyé ; aucun écran connecté détecté.', 'warning');
                return;
            }
            if (confirmed === total) {
                setStatus('Rechargement confirmé sur ' + total + ' écran(s).', 'success');
                return;
            }
            setStatus('Ordre envoyé — ' + confirmed + '/' + total + ' écran(s) confirmé(s)…', 'info');
            await new Promise(function (resolve) { setTimeout(resolve, 2000); });
        }
        setStatus('Rechargement non confirmé par tous les écrans : vérifiez « Écrans ».', 'warning');
    }

    // --- Thèmes nommés (instantanés réutilisables du payload) ---

    const THEME_SECTIONS = [
        ['appearance', 'Apparence (couleurs, tailles)', true],
        ['layout', 'Disposition (zones, ordre, largeurs, visibilité)', true],
        ['content', 'Contenu (textes, messages — remplace les textes actuels)', false],
    ];

    function buildThemeList(themes, sectionChecks) {
        const list = document.createElement('ul');
        list.className = 'page-editor-theme-list';
        themes.forEach(function (theme) {
            const item = document.createElement('li');
            item.className = 'page-editor-theme-item';
            item.dataset.themeId = theme.id;
            const info = document.createElement('div');
            info.className = 'page-editor-theme-info';
            const name = document.createElement('strong');
            name.textContent = theme.name;
            const meta = document.createElement('div');
            meta.className = 'small text-muted';
            meta.textContent = [
                theme.recommended ? 'Recommandé' : '',
                theme.updated_at ? new Date(theme.updated_at).toLocaleString('fr-FR') : '',
                theme.author || '', theme.description || '',
            ].filter(Boolean).join(' · ');
            info.append(name, meta);
            if (theme.colors) {
                const swatches = document.createElement('div');
                swatches.className = 'page-editor-theme-swatches';
                swatches.setAttribute('aria-label', 'Palette : ' + theme.colors.join(', '));
                theme.colors.forEach(function (color) {
                    const swatch = document.createElement('span');
                    swatch.style.backgroundColor = color;
                    swatch.title = color;
                    swatch.setAttribute('aria-hidden', 'true');
                    swatches.appendChild(swatch);
                });
                info.appendChild(swatches);
            }
            const actions = document.createElement('div');
            actions.className = 'page-editor-theme-actions';
            const applyButton = document.createElement('button');
            applyButton.type = 'button';
            applyButton.className = 'btn btn-sm btn-primary';
            applyButton.textContent = 'Appliquer';
            applyButton.setAttribute('aria-label', 'Appliquer le thème ' + theme.name);
            const updateAvailability = function () {
                applyButton.disabled = !sectionChecks.appearance.checked && !sectionChecks.layout.checked
                    && (theme.builtin || !sectionChecks.content.checked);
            };
            Object.values(sectionChecks).forEach(function (input) {
                input.addEventListener('change', updateAvailability);
            });
            updateAvailability();
            applyButton.addEventListener('click', function () {
                applyTheme(theme, {
                    appearance: sectionChecks.appearance.checked,
                    layout: sectionChecks.layout.checked,
                    content: !theme.builtin && sectionChecks.content.checked,
                });
            });
            actions.appendChild(applyButton);
            if (!theme.builtin) {
                const deleteButton = document.createElement('button');
                deleteButton.type = 'button';
                deleteButton.className = 'btn btn-sm btn-outline-danger';
                deleteButton.textContent = 'Supprimer';
                deleteButton.setAttribute('aria-label', 'Supprimer le thème ' + theme.name);
                deleteButton.addEventListener('click', function () { deleteTheme(theme); });
                actions.appendChild(deleteButton);
            }
            item.append(info, actions);
            list.appendChild(item);
        });
        return list;
    }

    function buildThemesBody(themes, builtins) {
        const container = document.createElement('div');

        const saveCard = document.createElement('div');
        saveCard.className = 'page-editor-palette-copy mb-3';
        const saveHeading = document.createElement('div');
        saveHeading.className = 'fw-semibold mb-1';
        saveHeading.textContent = 'Enregistrer le brouillon courant comme thème';
        const nameInput = document.createElement('input');
        nameInput.type = 'text';
        nameInput.className = 'form-control form-control-sm mb-2';
        nameInput.id = 'editor-theme-name';
        nameInput.placeholder = 'Nom du thème (ex. Lisibilité renforcée)';
        nameInput.maxLength = 80;
        nameInput.setAttribute('aria-label', 'Nom du thème');
        const descInput = document.createElement('input');
        descInput.type = 'text';
        descInput.className = 'form-control form-control-sm mb-2';
        descInput.id = 'editor-theme-description';
        descInput.placeholder = 'Description (facultative)';
        descInput.maxLength = 300;
        descInput.setAttribute('aria-label', 'Description du thème (facultative)');
        const saveButton = document.createElement('button');
        saveButton.type = 'button';
        saveButton.className = 'btn btn-sm btn-outline-primary';
        saveButton.id = 'editor-theme-save';
        saveButton.textContent = 'Enregistrer comme thème';
        saveButton.addEventListener('click', function () {
            saveTheme(nameInput.value, descInput.value);
        });
        saveCard.append(saveHeading, nameInput, descInput, saveButton);
        container.appendChild(saveCard);

        const sectionHeading = document.createElement('div');
        sectionHeading.className = 'fw-semibold mb-1';
        sectionHeading.textContent = 'Sections appliquées par « Appliquer »';
        container.appendChild(sectionHeading);
        const sectionChecks = {};
        THEME_SECTIONS.forEach(function (entry) {
            const wrapper = document.createElement('div');
            wrapper.className = 'form-check mb-1';
            const input = document.createElement('input');
            input.type = 'checkbox';
            input.className = 'form-check-input';
            input.id = 'editor-theme-section-' + entry[0];
            input.checked = entry[2];
            const label = document.createElement('label');
            label.className = 'form-check-label';
            label.htmlFor = input.id;
            label.textContent = entry[1];
            wrapper.append(input, label);
            container.appendChild(wrapper);
            sectionChecks[entry[0]] = input;
        });

        const builtinHeading = document.createElement('h3');
        builtinHeading.className = 'h6 mt-3 mb-1';
        builtinHeading.textContent = 'Thèmes intégrés';
        const note = document.createElement('p');
        note.className = 'small text-muted';
        note.textContent = 'Vos textes et options métier sont conservés, même si « Contenu » est coché. Ces modèles ne sont pas supprimables. Après application, vous pouvez enregistrer votre propre variante.';
        container.append(builtinHeading, note, buildThemeList(builtins, sectionChecks));
        const listHeading = document.createElement('h3');
        listHeading.className = 'h6 mt-3 mb-1';
        listHeading.textContent = 'Mes thèmes';
        container.appendChild(listHeading);
        if (!themes.length) {
            const empty = document.createElement('p');
            empty.className = 'text-muted';
            empty.textContent = 'Aucun thème personnel enregistré pour cette page.';
            container.appendChild(empty);
        }
        container.appendChild(buildThemeList(themes, sectionChecks));
        return container;
    }

    function closeThemesDialog() {
        const backdrop = document.querySelector('.page-editor-dialog-backdrop');
        if (backdrop && backdrop.closeDialog) backdrop.closeDialog(null);
    }

    async function showThemes() {
        try {
            const data = await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/themes');
            openDialog({
                title: 'Thèmes de la page',
                body: buildThemesBody(data.themes, data.builtins || []),
                actions: [{label: 'Fermer', className: 'btn btn-secondary', value: true, autofocus: true}],
            });
        } catch (error) {
            setStatus(error.message, 'danger');
        }
    }

    async function saveTheme(name, description, overwrite) {
        try {
            await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/themes', {
                method: 'POST',
                body: JSON.stringify({
                    name: name,
                    description: description,
                    overwrite: Boolean(overwrite),
                    payload: payload,
                }),
            });
            setStatus('Thème « ' + name.trim() + ' » enregistré.', 'success');
            closeThemesDialog();
            showThemes();
        } catch (error) {
            if (error.status === 409) {
                if (window.confirm('Un thème porte déjà ce nom : le remplacer par le brouillon courant ?')) {
                    return saveTheme(name, description, true);
                }
                return;
            }
            setStatus(error.message, 'danger');
        }
    }

    async function deleteTheme(theme) {
        if (!window.confirm('Supprimer le thème « ' + theme.name + ' » ?')) return;
        try {
            await requestJSON(
                '/admin/page-editor/' + encodeURIComponent(page) + '/themes/' + theme.id,
                {method: 'DELETE'}
            );
            setStatus('Thème « ' + theme.name + ' » supprimé.', 'success');
            closeThemesDialog();
            showThemes();
        } catch (error) {
            setStatus(error.message, 'danger');
        }
    }

    function applyTheme(theme, sections) {
        sections = Object.assign({}, sections, {content: !theme.builtin && sections.content});
        if (!sections.appearance && !sections.layout && !sections.content) {
            setStatus('Cochez au moins une section à appliquer.', 'warning');
            return;
        }
        if ((dirty || sections.content) && !window.confirm(
            'Appliquer « ' + theme.name + ' » remplacera les sections sélectionnées du travail courant.'
            + (sections.content ? ' Les textes seront également remplacés.' : ' Vos textes sont conservés.')
            + ' Vous pourrez annuler cette action.'
        )) return;
        const applied = [];
        mutate(function () {
            if (sections.appearance) {
                Object.assign(payload.css, clone(theme.snapshot.css));
                applied.push('apparence');
            }
            if (sections.layout) {
                if (theme.builtin) {
                    Object.entries(theme.snapshot.layout).forEach(function (entry) {
                        Object.assign(payload.layout[entry[0]], clone(entry[1]));
                    });
                } else {
                    payload.layout = clone(theme.snapshot.layout);
                }
                applied.push('disposition');
            }
            if (sections.content) {
                Object.assign(payload.config, clone(theme.snapshot.config));
                applied.push('contenu');
            }
        });
        setStatus('Thème « ' + theme.name + ' » appliqué (' + applied.join(', ') + ') — enregistrez puis publiez pour l’appliquer aux écrans.', 'info');
        closeThemesDialog();
    }

    async function confirmPublish() {
        const diff = await fetchDiff();
        const body = buildDiffList(diff);
        const findings = buildFindingsList();
        if (findings) body.appendChild(findings);
        const note = document.createElement('p');
        note.className = 'small text-muted mt-3 mb-0';
        note.textContent = 'Les écrans en service ne seront pas rechargés automatiquement : utilisez « Appliquer/recharger les écrans » au moment opportun.';
        body.appendChild(note);
        return await openDialog({
            title: 'Publier les modifications ?',
            body: body,
            actions: [
                {label: 'Annuler', className: 'btn btn-outline-secondary', value: false},
                {label: 'Publier', className: 'btn btn-success', value: true, autofocus: true},
            ],
        });
    }

    async function publish() {
        try {
            publishButton.disabled = true;
            if (dirty || !hasDraft) await saveDraft();
            if (dirty) {
                // Une saisie pendant l'enregistrement n'est pas dans le
                // brouillon : publier maintenant diffuserait une version
                // incomplète.
                setStatus('Des modifications récentes ne sont pas enregistrées : cliquez à nouveau sur « Publier ».', 'warning');
                publishButton.disabled = false;
                return;
            }
            const approved = await confirmPublish();
            if (!approved) {
                publishButton.disabled = false;
                return;
            }
            const result = await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/publish', {
                method: 'POST',
                body: JSON.stringify({draft_version: draftVersion})
            });
            clearBackup();
            await loadState('Révision ' + result.revision + ' publiée. Les écrans en service ne sont pas rechargés automatiquement.');
            applyButton.disabled = false;
            setStatus('Publication terminée. Utilisez « Appliquer/recharger les écrans » au moment opportun.', 'success');
        } catch (error) {
            setStatus(error.message, error.status === 409 ? 'warning' : 'danger');
            publishButton.disabled = false;
        }
    }

    async function discard() {
        const question = hasDraft
            ? 'Supprimer le brouillon partagé et revenir à la version publiée ?'
            : 'Abandonner les modifications non enregistrées ?';
        if (!window.confirm(question)) return;
        try {
            if (hasDraft) {
                await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/draft', {
                    method: 'DELETE',
                    body: JSON.stringify({draft_version: draftVersion})
                });
            }
            clearBackup();
            await loadState('Brouillon annulé.');
        } catch (error) {
            setStatus(error.message, error.status === 409 ? 'warning' : 'danger');
        }
    }

    async function restoreRevision(revision) {
        const warning = hasDraft || dirty
            ? 'Le brouillon actuel sera remplacé par la révision ' + revision + '. Continuer ?'
            : 'Charger la révision ' + revision + ' dans le brouillon ?';
        if (!window.confirm(warning)) return;
        try {
            const result = await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/revisions/' + revision + '/restore', {
                method: 'POST',
                body: JSON.stringify({draft_version: draftVersion})
            });
            await loadState('Révision ' + result.revision + ' chargée dans le brouillon : prévisualisez puis publiez pour l’appliquer.');
        } catch (error) {
            setStatus(error.message, error.status === 409 ? 'warning' : 'danger');
        }
    }

    undoButton.addEventListener('click', function () {
        if (!undoStack.length) return;
        redoStack.push(clone(payload));
        payload = undoStack.pop();
        dirty = true;
        persistBackup();
        rerenderAll(); pushPayloadToPreview(); updateButtons();
        setStatus('Modification annulée.', 'warning');
    });
    redoButton.addEventListener('click', function () {
        if (!redoStack.length) return;
        undoStack.push(clone(payload));
        payload = redoStack.pop();
        dirty = true;
        persistBackup();
        rerenderAll(); pushPayloadToPreview(); updateButtons();
        setStatus('Modification rétablie.', 'warning');
    });
    saveButton.addEventListener('click', function () {
        saveDraft().catch(function (error) { setStatus(error.message, error.status === 409 ? 'warning' : 'danger'); });
    });
    publishButton.addEventListener('click', publish);
    diffButton.addEventListener('click', showDiff);
    if (screensButton) screensButton.addEventListener('click', showScreens);
    if (themesButton) themesButton.addEventListener('click', showThemes);
    discardButton.addEventListener('click', discard);
    applyButton.addEventListener('click', async function () {
        if (!window.confirm('Recharger maintenant les écrans connectés en service ?')) return;
        try {
            await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/apply', {method: 'POST', body: '{}'});
            setStatus('Ordre de rechargement envoyé aux écrans connectés.', 'success');
            await watchScreenAcks();
        } catch (error) {
            setStatus(error.message, 'danger');
        }
    });
    historyButton.addEventListener('click', function () {
        historyPanel.hidden = !historyPanel.hidden;
        historyButton.setAttribute('aria-expanded', historyPanel.hidden ? 'false' : 'true');
    });
    scenarioSelect.addEventListener('change', function () {
        renderPalette();
        loadPreview();
    });
    viewportSelect.addEventListener('change', resizePreview);
    if (realSizeToggle) realSizeToggle.addEventListener('change', resizePreview);
    preview.addEventListener('load', function () { resizePreview(); pushPayloadToPreview(); });
    window.addEventListener('resize', resizePreview);
    window.addEventListener('message', function (event) {
        if (event.origin !== window.location.origin || !event.data) return;
        if (event.data.type === 'page-editor:ready') pushPayloadToPreview();
        if (event.data.type === 'page-editor:selected' && adapter.components[event.data.componentId]) selectComponent(event.data.componentId);
    });
    window.addEventListener('beforeunload', function (event) {
        if (!dirty && !saving) return;
        event.preventDefault();
        event.returnValue = '';
    });

    // Ctrl+Z / Ctrl+Maj+Z / Ctrl+Y hors champs de saisie (dans un champ, le
    // navigateur applique déjà son propre undo sur le texte).
    document.addEventListener('keydown', function (event) {
        const target = event.target;
        if (target && (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT'
            || target.tagName === 'SELECT' || target.isContentEditable)) return;
        if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
        const key = event.key.toLowerCase();
        if (key === 'z' && !event.shiftKey && !undoButton.disabled) {
            event.preventDefault();
            undoButton.click();
        } else if ((key === 'y' || (key === 'z' && event.shiftKey)) && !redoButton.disabled) {
            event.preventDefault();
            redoButton.click();
        }
    });

    loadState();
}());
