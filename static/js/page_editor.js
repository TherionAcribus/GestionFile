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
    const applyButton = document.getElementById('editor-apply');
    const historyButton = document.getElementById('editor-history-toggle');
    const historyPanel = document.getElementById('editor-history');
    const revisionsContainer = document.getElementById('editor-revisions');

    let adapter;
    let payload;
    let published;
    let draftVersion = 0;
    let hasDraft = false;
    let dirty = false;
    let selectedComponent = null;
    let undoStack = [];
    let redoStack = [];
    let sortableInstances = [];

    function clone(value) {
        return JSON.parse(JSON.stringify(value));
    }

    function setStatus(message, kind) {
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

    function pushPayloadToPreview() {
        if (!preview.contentWindow || !payload) return;
        preview.contentWindow.postMessage({type: 'page-editor:update', payload: payload}, window.location.origin);
        preview.contentWindow.postMessage({type: 'page-editor:select', componentId: selectedComponent}, window.location.origin);
    }

    function updateButtons() {
        undoButton.disabled = undoStack.length === 0;
        redoButton.disabled = redoStack.length === 0;
        saveButton.disabled = !dirty;
        publishButton.disabled = !dirty && !hasDraft;
        root.dataset.dirty = dirty ? 'true' : 'false';
    }

    function mutate(change, message) {
        undoStack.push(clone(payload));
        if (undoStack.length > 50) undoStack.shift();
        redoStack = [];
        change();
        dirty = true;
        renderPalette();
        renderInspector();
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
            title.textContent = zone;
            const list = document.createElement('div');
            list.className = 'page-editor-component-list';
            list.dataset.zone = zone;
            const componentIds = Object.keys(adapter.components).filter(function (componentId) {
                return payload.layout[componentId].zone === zone;
            }).sort(function (left, right) {
                return payload.layout[left].order - payload.layout[right].order;
            });
            componentIds.forEach(function (componentId) {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'page-editor-component' + (payload.layout[componentId].visible ? '' : ' is-hidden');
                button.dataset.componentId = componentId;
                button.setAttribute('aria-pressed', componentId === selectedComponent ? 'true' : 'false');
                const icon = document.createElement('i');
                icon.className = 'bi bi-grip-vertical';
                icon.setAttribute('aria-hidden', 'true');
                const label = document.createElement('span');
                label.textContent = adapter.components[componentId].label;
                button.append(icon, label);
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
                    renderPalette();
                    renderInspector();
                    pushPayloadToPreview();
                    updateButtons();
                    setStatus('Ordre des composants modifié.', 'warning');
                }
            }));
        });
    }

    function formGroup(labelText, control) {
        const wrapper = document.createElement('div');
        wrapper.className = 'mb-3';
        const label = document.createElement('label');
        label.className = 'form-label';
        label.textContent = labelText;
        if (control.id) label.htmlFor = control.id;
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
            }
            renderPalette();
            renderInspector();
            pushPayloadToPreview();
            updateButtons();
        });
    }

    function renderInspector() {
        inspector.replaceChildren();
        if (!selectedComponent || !adapter.components[selectedComponent]) {
            inspector.textContent = 'Sélectionnez un composant.';
            return;
        }
        const definition = adapter.components[selectedComponent];
        const layout = payload.layout[selectedComponent];
        const heading = document.createElement('p');
        heading.className = 'fw-semibold';
        heading.textContent = definition.label;
        inspector.appendChild(heading);

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

        if (definition.config.length) {
            const contentFields = addFieldset('Contenu');
            definition.config.forEach(function (field) {
                let control;
                if (field.type === 'bool') {
                    control = document.createElement('input');
                    control.type = 'checkbox';
                    control.className = 'form-check-input ms-2';
                    control.checked = Boolean(payload.config[field.key]);
                    bindValue(control, function (element) { payload.config[field.key] = element.checked; });
                } else {
                    control = document.createElement('textarea');
                    control.className = 'form-control';
                    control.rows = 3;
                    control.value = payload.config[field.key] == null ? '' : payload.config[field.key];
                    bindValue(control, function (element) { payload.config[field.key] = element.value; });
                }
                control.id = 'editor-config-' + field.key;
                contentFields.appendChild(formGroup(field.label, control));
            });
        }

        if (definition.css.length) {
            const appearanceFields = addFieldset('Apparence');
            definition.css.forEach(function (field) {
                const control = document.createElement('input');
                control.type = 'text';
                control.className = 'form-control';
                control.id = 'editor-css-' + field.key;
                control.value = payload.css[field.key] || '';
                control.placeholder = field.type === 'color' ? '#008B8B' : (field.type === 'number' ? '400' : '32px');
                bindValue(control, function (element) { payload.css[field.key] = element.value; });
                appearanceFields.appendChild(formGroup(field.label, control));
            });
        }
    }

    function selectComponent(componentId) {
        selectedComponent = componentId;
        renderPalette();
        renderInspector();
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
        const scale = Math.min(1, availableWidth / viewport.width);
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

    async function loadState(message) {
        root.setAttribute('aria-busy', 'true');
        try {
            const state = await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/state');
            adapter = state.adapter;
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
            setStatus(message || (hasDraft ? 'Brouillon partagé chargé.' : 'Configuration publiée chargée.'), hasDraft ? 'info' : 'light');
        } catch (error) {
            setStatus(error.message, 'danger');
        } finally {
            root.setAttribute('aria-busy', 'false');
        }
    }

    async function saveDraft() {
        const data = await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/draft', {
            method: 'PUT',
            body: JSON.stringify({draft_version: draftVersion, payload: payload})
        });
        payload = clone(data.draft);
        draftVersion = data.draft_version;
        hasDraft = true;
        dirty = false;
        updateButtons();
        setStatus('Brouillon partagé enregistré.', 'success');
        return data;
    }

    async function publish() {
        try {
            publishButton.disabled = true;
            if (dirty || !hasDraft) await saveDraft();
            const result = await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/publish', {
                method: 'POST',
                body: JSON.stringify({draft_version: draftVersion})
            });
            await loadState('Révision ' + result.revision + ' publiée. Les écrans en service ne sont pas rechargés automatiquement.');
            applyButton.disabled = false;
            setStatus('Publication terminée. Utilisez « Appliquer/recharger les écrans » au moment opportun.', 'success');
        } catch (error) {
            setStatus(error.message, error.status === 409 ? 'warning' : 'danger');
            publishButton.disabled = false;
        }
    }

    async function discard() {
        try {
            if (hasDraft) {
                await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/draft', {
                    method: 'DELETE',
                    body: JSON.stringify({draft_version: draftVersion})
                });
            }
            await loadState('Brouillon annulé.');
        } catch (error) {
            setStatus(error.message, error.status === 409 ? 'warning' : 'danger');
        }
    }

    async function restoreRevision(revision) {
        if (!window.confirm('Restaurer et publier la révision ' + revision + ' ?')) return;
        try {
            const result = await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/revisions/' + revision + '/restore', {
                method: 'POST', body: '{}'
            });
            await loadState('Révision restaurée et republiée sous le numéro ' + result.revision + '.');
            applyButton.disabled = false;
        } catch (error) {
            setStatus(error.message, 'danger');
        }
    }

    undoButton.addEventListener('click', function () {
        if (!undoStack.length) return;
        redoStack.push(clone(payload));
        payload = undoStack.pop();
        dirty = true;
        renderPalette(); renderInspector(); pushPayloadToPreview(); updateButtons();
        setStatus('Modification annulée.', 'warning');
    });
    redoButton.addEventListener('click', function () {
        if (!redoStack.length) return;
        undoStack.push(clone(payload));
        payload = redoStack.pop();
        dirty = true;
        renderPalette(); renderInspector(); pushPayloadToPreview(); updateButtons();
        setStatus('Modification rétablie.', 'warning');
    });
    saveButton.addEventListener('click', function () {
        saveDraft().catch(function (error) { setStatus(error.message, error.status === 409 ? 'warning' : 'danger'); });
    });
    publishButton.addEventListener('click', publish);
    discardButton.addEventListener('click', discard);
    applyButton.addEventListener('click', async function () {
        try {
            await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/apply', {method: 'POST', body: '{}'});
            setStatus('Ordre de rechargement envoyé aux écrans connectés.', 'success');
        } catch (error) {
            setStatus(error.message, 'danger');
        }
    });
    historyButton.addEventListener('click', function () {
        historyPanel.hidden = !historyPanel.hidden;
        historyButton.setAttribute('aria-expanded', historyPanel.hidden ? 'false' : 'true');
    });
    scenarioSelect.addEventListener('change', loadPreview);
    viewportSelect.addEventListener('change', resizePreview);
    preview.addEventListener('load', function () { resizePreview(); pushPayloadToPreview(); });
    window.addEventListener('resize', resizePreview);
    window.addEventListener('message', function (event) {
        if (event.origin !== window.location.origin || !event.data) return;
        if (event.data.type === 'page-editor:ready') pushPayloadToPreview();
        if (event.data.type === 'page-editor:selected' && adapter.components[event.data.componentId]) selectComponent(event.data.componentId);
    });
    window.addEventListener('beforeunload', function (event) {
        if (!dirty) return;
        event.preventDefault();
        event.returnValue = '';
    });

    loadState();
}());
