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
    const languageSelect = document.getElementById('editor-language');
    const languageField = document.getElementById('editor-language-field');
    const viewportSelect = document.getElementById('editor-viewport');
    const statusBox = document.getElementById('editor-status');
    const undoButton = document.getElementById('editor-undo');
    const redoButton = document.getElementById('editor-redo');
    const saveButton = document.getElementById('editor-save');
    const publishButton = document.getElementById('editor-publish');
    const discardButton = document.getElementById('editor-discard');
    const diffButton = document.getElementById('editor-diff');
    const applyButton = document.getElementById('editor-apply');
    const actionsPanel = document.getElementById('editor-actions');
    const actionsToggle = document.getElementById('editor-actions-toggle');
    const workflowDraft = document.getElementById('editor-workflow-draft');
    const workflowPublish = document.getElementById('editor-workflow-publish');
    const workflowApply = document.getElementById('editor-workflow-apply');
    const workflowDraftStatus = document.getElementById('editor-workflow-draft-status');
    const workflowPublishStatus = document.getElementById('editor-workflow-publish-status');
    const workflowApplyStatus = document.getElementById('editor-workflow-apply-status');
    const themesButton = document.getElementById('editor-themes');
    const screensButton = document.getElementById('editor-screens');
    const historyButton = document.getElementById('editor-history-toggle');
    const historyPanel = document.getElementById('editor-history');
    const revisionsContainer = document.getElementById('editor-revisions');
    const zoomSelect = document.getElementById('editor-zoom');
    const topToolbar = document.querySelector('.page-editor-toolbar');
    const journeySection = document.getElementById('editor-journey');
    const journeyPath = document.getElementById('editor-journey-path');
    const journeySummary = document.getElementById('editor-journey-summary');
    const journeyBranches = document.getElementById('editor-journey-branches');
    const journeySkipped = document.getElementById('editor-journey-skipped');
    const modesNavigation = document.getElementById('editor-modes');
    const modeHelp = document.getElementById('editor-mode-help');
    const modeButtons = Array.from(document.querySelectorAll('[data-editor-mode]'));
    const componentsPanel = document.getElementById('editor-components-panel');
    const inspectorPanel = document.getElementById('editor-inspector-panel');
    const inspectorToggle = document.getElementById('editor-inspector-toggle');
    const inspectorClose = document.getElementById('editor-inspector-close');
    const inspectorBackdrop = document.getElementById('editor-inspector-backdrop');
    const scenarioField = document.getElementById('editor-scenario-field');
    const simulationPanel = document.getElementById('editor-simulation-panel');
    const simulationReset = document.getElementById('editor-simulation-reset');
    const simulationKicker = document.getElementById('editor-simulation-kicker');
    const simulationIcon = document.getElementById('editor-simulation-icon');
    const simulationStepLabel = document.getElementById('editor-simulation-step');
    const simulationRole = document.getElementById('editor-simulation-role');
    const simulationResult = document.getElementById('editor-simulation-result');
    const simulationActions = document.getElementById('editor-simulation-actions');
    const simulationTrail = document.getElementById('editor-simulation-trail');
    const compactInspectorMedia = window.matchMedia('(max-width: 1199px)');
    const compactActionsMedia = window.matchMedia('(max-width: 767px)');

    // L'aperçu est épinglé sous la barre d'outils : l'offset suit sa hauteur
    // réelle (les boutons peuvent s'étaler sur plusieurs lignes selon la
    // largeur), sinon l'aperçu passerait dessous et serait masqué.
    function syncStickyOffset() {
        if (!topToolbar) return;
        root.style.setProperty('--page-editor-toolbar-height', (topToolbar.offsetHeight + 8) + 'px');
    }

    function syncActionsPresentation() {
        if (compactActionsMedia.matches) {
            const open = root.dataset.actionsOpen === 'true';
            actionsToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            actionsPanel.setAttribute('aria-hidden', open ? 'false' : 'true');
        } else {
            actionsToggle.setAttribute('aria-expanded', 'false');
            actionsPanel.removeAttribute('aria-hidden');
        }
    }
    syncStickyOffset();
    window.addEventListener('resize', syncStickyOffset);
    if (window.ResizeObserver && topToolbar) {
        new ResizeObserver(syncStickyOffset).observe(topToolbar);
    }

    let adapter;
    let payload;
    let published;
    let defaults = null;
    let draftVersion = 0;
    let hasDraft = false;
    let dirty = false;
    let saving = false;
    let saveQueued = false;
    let autosaveTimer = null;
    let publishedRevision = 0;
    let appliedRevision = null;
    let selectedComponent = null;
    let undoStack = [];
    let redoStack = [];
    let sortableInstances = [];
    let paletteSources = [];
    let disabledComponents = {};
    let previewLanguages = [];
    // Frise « parcours » (borne patient, cf. page_journey.py) : définition
    // déclarative servie par /state, faits calculés côté serveur, et étape
    // dont l'inspecteur affiche les réglages (exclusive d'un composant).
    let journey = null;
    let journeyFacts = {};
    let selectedStep = null;
    let journeyContextStep = null;
    let editorMode = 'design';
    let inspectorReturnFocus = null;
    let simulationStepId = null;
    let simulationHistory = [];
    let simulationOutcome = null;
    root.dataset.actionsOpen = 'false';
    syncActionsPresentation();
    // État ouvert/fermé des sections de l'inspecteur, conservé entre les
    // re-rendus : à chaque modification l'inspecteur est reconstruit et
    // sans cette mémoire les sections retomberaient sur leur état
    // initial — « Apparence » se refermerait sous les doigts à chaque
    // saisie.
    const fieldsetOpen = {};

    function clone(value) {
        return JSON.parse(JSON.stringify(value));
    }

    function canonical(value) {
        return JSON.stringify({layout: value.layout, config: value.config, css: value.css});
    }

    // Thème ou copie locale enregistrés avant un retrait de réglage : seules
    // les clés encore connues de la cible sont reprises — une clé disparue
    // depuis serait refusée à la sauvegarde (« paramètre/variable inconnu »).
    function applyKnownKeys(target, source) {
        Object.keys(source || {}).forEach(function (key) {
            if (Object.prototype.hasOwnProperty.call(target, key)) {
                target[key] = clone(source[key]);
            }
        });
    }

    function colorToHex(value) {
        const raw = String(value || '').trim();
        // « transparent » / « none » n'ont pas de couleur mesurable : les
        // convertir en noir créerait un faux positif de contraste.
        if (raw === 'transparent' || raw === 'none') return null;
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
        const rgb = computed.match(/^rgba?\(\s*(\d+)\D+(\d+)\D+(\d+)(?:\D+([\d.]+))?/i);
        if (!rgb) return null;
        // Un rgba() dont l'alpha vaut 0 (couleur entièrement transparente) ne
        // représente aucune couleur affichée : pas de valeur hexadécimale.
        if (rgb[4] !== undefined && Number(rgb[4]) === 0) return null;
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

    // Les paires texte/fond à contrôler sont déclarées par le serveur
    // (component.contrast_pairs) : deviner le premier « font_color » et le
    // premier « background_color » du composant comparerait des réglages
    // sans rapport (ex. un texte avec un fond « transparent »).
    function componentContrastFindings(definition) {
        const failures = [];
        (definition.contrast_pairs || []).forEach(function (pair) {
            const font = colorToHex(payload.css[pair[0]]);
            const background = colorToHex(payload.css[pair[1]]);
            if (!font || !background) return;
            const ratio = contrastRatio(font, background);
            if (ratio < 3) {
                failures.push({font: pair[0], background: pair[1], ratio: ratio});
            }
        });
        return failures;
    }

    function cssFieldLabel(definition, key) {
        // Libellé du champ dans le composant d'abord, puis dans toute la
        // page : une paire peut croiser le champ d'un composant voisin
        // (ex. le sous-titre mesuré contre le bandeau du titre).
        const local = (definition.css || []).find(function (field) {
            return field.key === key;
        });
        if (local) return local.label;
        const componentIds = Object.keys(adapter.components);
        for (let index = 0; index < componentIds.length; index++) {
            const field = adapter.components[componentIds[index]].css.find(function (item) {
                return item.key === key;
            });
            if (field) return field.label;
        }
        return key;
    }

    function appendContrastWarning(container, definition) {
        componentContrastFindings(definition).forEach(function (failure) {
            const warning = document.createElement('div');
            warning.className = 'alert alert-warning py-2 small';
            warning.setAttribute('role', 'note');
            warning.textContent = 'Contraste faible (' + failure.ratio.toFixed(1).replace('.', ',')
                + ':1) entre « ' + cssFieldLabel(definition, failure.font)
                + ' » et « ' + cssFieldLabel(definition, failure.background)
                + ' » : illisible à distance.';
            container.appendChild(warning);
        });
    }

    const ZONE_LABELS = {
        header: 'En-tête',
        main: 'Contenu',
        aside: 'Zone latérale',
        footer: 'Pied de page',
        overlay: 'Superposition',
    };

    const ALIGNMENT_LABELS = {stretch: 'Étendre', left: 'Gauche', center: 'Centre', right: 'Droite'};

    const MODE_HELP = {
        journey: 'Configurez l’ordre, les conditions et les incidents du parcours. Sélectionnez une étape pour afficher ses règles.',
        design: 'Modifiez la disposition, les textes et l’apparence des composants de l’écran courant.',
        simulation: 'Jouez un parcours complet, choisissez ses embranchements et déclenchez les incidents à tester.',
    };

    function journeyStepForScenario() {
        if (!journey) return null;
        return journeyAllSteps().find(function (step) {
            return step.scenario === scenarioSelect.value;
        }) || journey.steps[0] || null;
    }

    function componentBelongsToStep(componentId, step) {
        const component = adapter && adapter.components[componentId];
        return Boolean(component && (!step || !component.scenarios
            || component.scenarios.includes(step.scenario)));
    }

    function syncInspectorPresentation() {
        const available = !(journey && editorMode === 'simulation');
        inspectorPanel.hidden = !available;
        if (!available) {
            root.dataset.inspectorOpen = 'false';
            inspectorToggle.hidden = true;
            inspectorBackdrop.hidden = true;
            inspectorPanel.inert = true;
            inspectorPanel.setAttribute('aria-hidden', 'true');
            return;
        }
        if (compactInspectorMedia.matches) {
            const open = root.dataset.inspectorOpen === 'true';
            inspectorToggle.hidden = false;
            inspectorToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            inspectorBackdrop.hidden = !open;
            inspectorPanel.inert = !open;
            inspectorPanel.setAttribute('aria-hidden', open ? 'false' : 'true');
        } else {
            root.dataset.inspectorOpen = 'false';
            inspectorToggle.hidden = true;
            inspectorToggle.setAttribute('aria-expanded', 'false');
            inspectorBackdrop.hidden = true;
            inspectorPanel.inert = false;
            inspectorPanel.setAttribute('aria-hidden', 'false');
        }
    }

    function openInspectorDrawer(trigger) {
        if (!compactInspectorMedia.matches || (journey && editorMode === 'simulation')) return;
        inspectorReturnFocus = trigger || document.activeElement;
        root.dataset.inspectorOpen = 'true';
        syncInspectorPresentation();
        window.requestAnimationFrame(function () { inspectorClose.focus(); });
    }

    function closeInspectorDrawer(restoreFocus) {
        const target = inspectorReturnFocus;
        root.dataset.inspectorOpen = 'false';
        inspectorReturnFocus = null;
        syncInspectorPresentation();
        if (restoreFocus && target && document.contains(target)) target.focus();
    }

    function syncEditorModeChrome() {
        const hasJourney = Boolean(journey);
        if (!hasJourney) editorMode = 'design';
        root.dataset.editorMode = editorMode;
        modesNavigation.hidden = !hasJourney;
        componentsPanel.hidden = hasJourney && editorMode !== 'design';
        scenarioField.hidden = hasJourney && editorMode !== 'design';
        simulationPanel.hidden = !hasJourney || editorMode !== 'simulation';
        modeButtons.forEach(function (button) {
            const active = button.dataset.editorMode === editorMode;
            button.setAttribute('aria-selected', active ? 'true' : 'false');
            button.classList.toggle('btn-primary', active);
            button.classList.toggle('btn-outline-primary', !active);
            button.tabIndex = active ? 0 : -1;
        });
        if (modeHelp) modeHelp.textContent = MODE_HELP[editorMode] || '';
        syncInspectorPresentation();
    }

    function setEditorMode(nextMode, options) {
        options = options || {};
        if (!journey) nextMode = 'design';
        if (!['journey', 'design', 'simulation'].includes(nextMode)) return;
        const previousScenario = scenarioSelect.value;

        if (nextMode === 'journey') {
            const context = journeyStep(journeyContextStep) || journeyStep(selectedStep) || journeyStepForScenario();
            selectedStep = context ? context.id : null;
            journeyContextStep = selectedStep;
            selectedComponent = null;
            if (context) scenarioSelect.value = context.scenario;
        } else if (nextMode === 'design') {
            const context = journeyStep(selectedStep) || journeyStep(journeyContextStep) || journeyStepForScenario();
            if (context) journeyContextStep = context.id;
            selectedStep = null;
            if (!selectedComponent || !componentBelongsToStep(selectedComponent, context)) {
                selectedComponent = context && Object.keys(adapter.components).find(function (componentId) {
                    return componentBelongsToStep(componentId, context);
                });
            }
            if (!selectedComponent) selectedComponent = Object.keys(adapter.components)[0];
        } else {
            const context = journeyStep(selectedStep) || journeyStep(journeyContextStep) || journeyStepForScenario();
            if (context) journeyContextStep = context.id;
            selectedStep = null;
            selectedComponent = null;
            ensureSimulationState();
        }

        editorMode = nextMode;
        try { localStorage.setItem('page-editor-mode-' + page, editorMode); } catch (error) {}
        closeInspectorDrawer(false);
        syncEditorModeChrome();
        rerenderAll();
        if (options.reloadPreview === true || scenarioSelect.value !== previousScenario) loadPreview();
        else pushPayloadToPreview();
        if (options.focus) {
            const activeButton = modeButtons.find(function (button) {
                return button.dataset.editorMode === editorMode;
            });
            if (activeButton) activeButton.focus();
        }
    }

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
            error.code = data.code;
            error.data = data;
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
            renderJourney();
            renderSimulation();
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
        applyButton.disabled = saving || publishedRevision < 1;
        root.dataset.dirty = dirty ? 'true' : 'false';

        workflowDraft.dataset.state = dirty ? 'ready' : (hasDraft ? 'complete' : 'idle');
        workflowDraftStatus.textContent = saving ? 'Enregistrement…'
            : (dirty ? 'Modifications à enregistrer' : (hasDraft ? 'Brouillon enregistré' : 'À jour'));
        workflowPublish.dataset.state = dirty || hasDraft ? 'ready' : 'complete';
        workflowPublishStatus.textContent = dirty ? 'Enregistrer avant publication'
            : (hasDraft ? 'Prêt à publier' : 'Version publiée');
        workflowApply.dataset.state = publishedRevision < 1 ? 'idle'
            : (appliedRevision === publishedRevision ? 'complete' : 'ready');
        workflowApplyStatus.textContent = publishedRevision < 1 ? 'Aucune publication'
            : (appliedRevision === publishedRevision ? 'Ordre envoyé' : 'Révision ' + publishedRevision + ' disponible');
    }

    function mutate(change, message) {
        undoStack.push(clone(payload));
        if (undoStack.length > 50) undoStack.shift();
        redoStack = [];
        change();
        dirty = true;
        scheduleAutosave();
        persistBackup();
        rerenderAll();
        pushPayloadToPreview();
        updateButtons();
        setStatus(message || 'Modifications non enregistrées.', 'warning');
    }

    // Sauvegarde automatique : chaque modification repousse le minuteur
    // (~3 s d'inactivité). ``saveDraft`` gère la réentrance (``saveQueued``)
    // et ne remplace le document local que s'il n'a pas bougé pendant la
    // requête ; si un enregistrement manuel est en cours, on attend son
    // terme — la reprise est assurée par ``saveDraft`` lui-même.
    function scheduleAutosave() {
        if (autosaveTimer) clearTimeout(autosaveTimer);
        autosaveTimer = setTimeout(function () {
            autosaveTimer = null;
            if (dirty && !saving) {
                saveDraft(true).catch(function (error) {
                    setStatus(error.message, error.status === 409 ? 'warning' : 'danger');
                });
            }
        }, 3000);
    }

    function destroySortables() {
        sortableInstances.forEach(function (instance) { instance.destroy(); });
        sortableInstances = [];
    }

    function renderPalette() {
        destroySortables();
        zonesContainer.replaceChildren();
        const contextStep = editorMode === 'design' ? journeyStep(journeyContextStep) : null;
        if (contextStep) {
            const context = document.createElement('div');
            context.className = 'page-editor-component-context';
            const copy = document.createElement('div');
            const label = document.createElement('strong');
            label.textContent = 'Écran : ' + contextStep.label;
            const hint = document.createElement('span');
            hint.textContent = 'Composants utilisés dans ce scénario.';
            copy.append(label, hint);
            const actions = document.createElement('div');
            actions.className = 'd-flex flex-wrap gap-1';
            const back = document.createElement('button');
            back.type = 'button';
            back.className = 'btn btn-sm btn-primary';
            back.textContent = 'Retour au parcours';
            back.addEventListener('click', function () { setEditorMode('journey'); });
            const all = document.createElement('button');
            all.type = 'button';
            all.className = 'btn btn-sm btn-outline-secondary';
            all.textContent = 'Tous les composants';
            all.addEventListener('click', function () {
                journeyContextStep = null;
                rerenderAll();
            });
            actions.append(back, all);
            context.append(copy, actions);
            zonesContainer.appendChild(context);
        }
        adapter.zones.forEach(function (zone) {
            const section = document.createElement('section');
            section.className = 'page-editor-zone';
            const title = document.createElement('h4');
            title.textContent = ZONE_LABELS[zone] || zone;
            const list = document.createElement('div');
            list.className = 'page-editor-component-list';
            list.dataset.zone = zone;
            const componentIds = Object.keys(adapter.components).filter(function (componentId) {
                const component = adapter.components[componentId];
                const inContext = !contextStep || !component.scenarios
                    || component.scenarios.includes(contextStep.scenario);
                return payload.layout[componentId].zone === zone && inContext;
            }).sort(function (left, right) {
                return payload.layout[left].order - payload.layout[right].order;
            });
            if (!componentIds.length) return;
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
                button.addEventListener('click', function () { selectComponent(componentId, button); });
                list.appendChild(button);
            });
            section.append(title, list);
            zonesContainer.appendChild(section);

            if (!contextStep) sortableInstances.push(new Sortable(list, {
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
                    scheduleAutosave();
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

    function formGroup(labelText, control, labelAction) {
        const wrapper = document.createElement('div');
        wrapper.className = 'mb-3';
        const label = document.createElement('label');
        label.className = 'form-label';
        label.textContent = labelText;
        if (control.id) label.htmlFor = control.id;
        if (control.dataset.labelFor) label.htmlFor = control.dataset.labelFor;
        if (labelAction) {
            // Bouton discret de la ligne du libellé (ex. « valeur d'usine »)
            // : le libellé et l'action partagent la même ligne.
            const row = document.createElement('div');
            row.className = 'd-flex align-items-center justify-content-between gap-2';
            label.classList.add('mb-0');
            row.append(label, labelAction);
            wrapper.append(row, control);
        } else {
            wrapper.append(label, control);
        }
        return wrapper;
    }

    // Valeur d'usine d'un réglage (``defaults`` servi par /state) : ``null``
    // signifie « absente des JSON d'usine » — pas de bouton dans ce cas.
    function factoryValue(section, key) {
        if (!defaults || !defaults[section]) return undefined;
        const value = defaults[section][key];
        return value === null || value === undefined ? undefined : value;
    }

    // Bouton « valeur d'usine » discret, visible seulement quand la valeur
    // courante diffère de l'usine. La comparaison des variables d'apparence
    // ignore la casse et les espaces : « #008B8B » et « #008b8b » sont la
    // même couleur une fois normalisée côté serveur.
    function differsFromFactory(section, key) {
        const value = factoryValue(section, key);
        if (value === undefined) return false;
        const current = payload[section][key];
        if (current === value) return false;
        if (section === 'css') {
            return String(current).trim().toLowerCase()
                !== String(value).trim().toLowerCase();
        }
        return true;
    }

    function factoryResetButton(section, key) {
        if (!differsFromFactory(section, key)) return null;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-link btn-sm page-editor-factory-reset';
        button.title = 'Reprendre la valeur d’usine : ' + defaults[section][key];
        const icon = document.createElement('i');
        icon.className = 'bi bi-arrow-counterclockwise';
        icon.setAttribute('aria-hidden', 'true');
        const hiddenLabel = document.createElement('span');
        hiddenLabel.className = 'visually-hidden';
        hiddenLabel.textContent = 'Reprendre la valeur d’usine';
        button.append(icon, hiddenLabel);
        button.addEventListener('click', function () {
            const value = clone(defaults[section][key]);
            // mutate re-rend l'inspecteur : le contrôle reflète la valeur
            // usine et le bouton disparaît (plus de différence).
            mutate(function () {
                payload[section][key] = value;
            }, 'Valeur d’usine rétablie pour ce réglage.');
        });
        return button;
    }

    function addFieldset(titleText) {
        const details = document.createElement('details');
        details.className = 'page-editor-fieldset';
        details.open = titleText in fieldsetOpen
            ? fieldsetOpen[titleText] : titleText !== 'Apparence';
        details.addEventListener('toggle', function () {
            fieldsetOpen[titleText] = details.open;
        });
        const summary = document.createElement('summary');
        summary.textContent = titleText;
        const body = document.createElement('div');
        body.className = 'page-editor-fieldset-body';
        details.append(summary, body);
        inspector.appendChild(details);
        return body;
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
            scheduleAutosave();
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

    const SIZE_UNITS = ['px', '%', 'em', 'rem', 'vh', 'vw', 'pt'];
    // Mêmes unités que la validation serveur (_CSS_SIZE_RE) : une valeur
    // qui s'y conforme est découpée en nombre + unité ; les autres
    // (calc(), var(), mots-clés…) gardent le champ texte libre.
    const SIZE_VALUE_RE = /^(-?\d+(?:\.\d+)?)(px|em|rem|%|vh|vw|vmin|vmax|pt|pc|cm|mm|in|ch|ex|lh|rlh)?$/i;

    // Bouton −/+ accolé à un champ numérique. Chaque clic applique le pas
    // (Maj+clic : ×10), borne sur min/max si le champ en déclare, puis
    // rejoue la séquence input→change attendue par bindValue : aperçu en
    // direct et une étape d'annulation par clic. L'id dérivé du champ
    // permet au focus de suivre le bouton recréé au re-rendu de
    // l'inspecteur.
    function stepperButton(input, delta, label) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn btn-outline-secondary page-editor-stepper-btn';
        button.id = input.id + (delta < 0 ? '-minus' : '-plus');
        button.setAttribute('aria-label', label);
        button.title = 'Maj+clic : ±' + Math.abs(delta) * 10;
        const icon = document.createElement('i');
        icon.className = 'bi ' + (delta < 0 ? 'bi-dash' : 'bi-plus');
        icon.setAttribute('aria-hidden', 'true');
        button.appendChild(icon);
        button.addEventListener('click', function (event) {
            const step = Math.abs(delta) * (event.shiftKey ? 10 : 1);
            const direction = delta < 0 ? -1 : 1;
            const base = parseFloat(input.value) || 0;
            // Une valeur « hors pas » (750 avec un pas de 100) s'aligne
            // d'abord sur le pas avant d'avancer : + rend 800, pas 850.
            let next = direction > 0
                ? Math.floor(base / step) * step + step
                : Math.ceil(base / step) * step - step;
            if (input.min !== '') next = Math.max(parseFloat(input.min), next);
            if (input.max !== '') next = Math.min(parseFloat(input.max), next);
            input.value = String(Math.round(next * 1000) / 1000);
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));
        });
        return button;
    }

    // Champ « taille » : la valeur stockée garde son unité (« 32px »), mais
    // l'édition la découpe — nombre au clavier, boutons −/+ et unité en
    // sélecteur. Fini de retaper « px » à chaque ajustement ou de casser
    // l'aperçu en l'effaçant. Une valeur non découpable renvoie ``null``
    // pour laisser le site appelant poser le champ texte libre.
    function createSizeControl(id, value, apply) {
        const trimmed = String(value == null ? '' : value).trim();
        const match = trimmed === '' ? ['', '', ''] : trimmed.match(SIZE_VALUE_RE);
        if (!match) return null;

        const group = document.createElement('div');
        group.className = 'input-group page-editor-stepper';

        const number = document.createElement('input');
        number.type = 'number';
        number.className = 'form-control';
        number.id = id;
        number.step = '1';
        number.value = match[1];
        group.dataset.labelFor = number.id;

        const unitValue = (match[2] || 'px').toLowerCase();
        const unit = document.createElement('select');
        unit.className = 'form-select page-editor-stepper-unit';
        unit.id = id + '-unit';
        unit.setAttribute('aria-label', 'Unité');
        const units = SIZE_UNITS.includes(unitValue)
            ? SIZE_UNITS : SIZE_UNITS.concat(unitValue);
        units.forEach(function (item) {
            const option = document.createElement('option');
            option.value = item;
            option.textContent = item;
            unit.appendChild(option);
        });
        unit.value = unitValue;

        const commit = function () {
            // parseFloat normalise les écritures exotiques acceptées par
            // les champs number (« 1e3 », « 32.50 ») avant de recoller
            // l'unité : la valeur enregistrée reste du CSS lisible.
            const parsed = parseFloat(number.value);
            apply(Number.isNaN(parsed) ? '' : String(parsed) + unit.value);
        };
        bindValue(number, commit);
        bindValue(unit, commit);

        group.append(
            stepperButton(number, -1, 'Diminuer'),
            number,
            stepperButton(number, 1, 'Augmenter'),
            unit);
        return group;
    }

    // Champ « nombre » (graisse…) : même découpage que les tailles, sans
    // l'unité ; le pas vient du champ (« step: 100 » pour la graisse).
    // Une valeur non numérique (mot-clé « bold ») renvoie ``null``.
    function createNumberControl(id, value, step, apply) {
        const trimmed = String(value == null ? '' : value).trim();
        if (trimmed !== '' && !/^-?\d+(?:\.\d+)?$/.test(trimmed)) return null;

        const group = document.createElement('div');
        group.className = 'input-group page-editor-stepper';

        const input = document.createElement('input');
        input.type = 'number';
        input.className = 'form-control';
        input.id = id;
        // Bornes de la validation serveur (_is_css_number : 1…1000). Le
        // pas du champ (``step``) ne pilote que les boutons −/+ : posé
        // sur l'input il décalerait les valeurs permises à partir de
        // ``min`` (min=1, pas=100 → 1, 101, 201… au lieu de centaines).
        input.min = '1';
        input.max = '1000';
        input.value = trimmed;
        group.dataset.labelFor = input.id;
        bindValue(input, function () {
            const parsed = parseFloat(input.value);
            apply(Number.isNaN(parsed) ? '' : String(parsed));
        });
        group.append(
            stepperButton(input, -step, 'Diminuer'),
            input,
            stepperButton(input, step, 'Augmenter'));
        return group;
    }

    const BUTTONS_IMAGE_PREFIX = '/static/images/buttons/';

    // Champ « image » : choix d'un fichier existant de
    // static/images/buttons (le dépôt se remplit en mode avancé — pas
    // d'upload dans l'éditeur). La valeur vit dans un input caché relié
    // au circuit habituel par ``bindValue`` : « input » enregistre la
    // valeur (dirty/autosave), « change » empile l'annulation et pousse
    // le brouillon vers l'aperçu.
    function createImageControl(id, key, change) {
        const wrapper = document.createElement('div');
        wrapper.className = 'page-editor-image-control';

        const input = document.createElement('input');
        input.type = 'hidden';
        input.id = id + '-value';
        input.value = payload.config[key] == null ? '' : String(payload.config[key]);
        wrapper.dataset.labelFor = input.id;
        bindValue(input, change);

        const current = document.createElement('div');
        current.className = 'page-editor-image-current';
        const thumb = document.createElement('img');
        thumb.className = 'page-editor-image-thumb';
        thumb.alt = '';
        const name = document.createElement('span');
        name.className = 'page-editor-image-name';
        current.append(thumb, name);

        const actions = document.createElement('div');
        actions.className = 'page-editor-image-actions';
        const chooseButton = document.createElement('button');
        chooseButton.type = 'button';
        chooseButton.className = 'btn btn-sm btn-outline-primary';
        chooseButton.textContent = 'Choisir…';
        const removeButton = document.createElement('button');
        removeButton.type = 'button';
        removeButton.className = 'btn btn-sm btn-outline-secondary';
        removeButton.textContent = 'Retirer';
        actions.append(chooseButton, removeButton);

        function sync() {
            const file = input.value;
            thumb.hidden = !file;
            thumb.src = file ? BUTTONS_IMAGE_PREFIX + file : '';
            name.textContent = file || 'Aucune image';
            removeButton.disabled = !file;
        }
        function commit(file) {
            if (input.value === file) return;
            input.value = file;
            sync();
            input.dispatchEvent(new Event('input', {bubbles: true}));
            input.dispatchEvent(new Event('change', {bubbles: true}));
        }
        sync();

        chooseButton.addEventListener('click', function () { openImageDialog(commit); });
        removeButton.addEventListener('click', function () { commit(''); });

        wrapper.append(current, actions, input);
        return wrapper;
    }

    async function openImageDialog(commit) {
        let images = [];
        try {
            const data = await requestJSON(
                '/admin/page-editor/' + encodeURIComponent(page) + '/images');
            images = data.images || [];
        } catch (error) {
            setStatus(error.message, 'danger');
            return;
        }
        const grid = document.createElement('div');
        grid.className = 'page-editor-image-grid';
        if (!images.length) {
            const empty = document.createElement('p');
            empty.className = 'text-muted';
            empty.textContent = 'Aucune image disponible : le dépôt se gère dans le mode avancé.';
            grid.appendChild(empty);
        }
        images.forEach(function (file) {
            const item = document.createElement('button');
            item.type = 'button';
            item.className = 'page-editor-image-choice';
            const thumb = document.createElement('img');
            thumb.className = 'page-editor-image-choice-thumb';
            thumb.src = BUTTONS_IMAGE_PREFIX + file;
            thumb.alt = '';
            const label = document.createElement('span');
            label.className = 'page-editor-image-choice-name';
            label.textContent = file;
            item.append(thumb, label);
            item.addEventListener('click', function () {
                commit(file);
                const backdrop = item.closest('.page-editor-dialog-backdrop');
                if (backdrop && backdrop.closeDialog) backdrop.closeDialog(file);
            });
            grid.appendChild(item);
        });
        await openDialog({
            title: 'Choisir une image',
            body: grid,
            actions: [{label: 'Fermer', className: 'btn btn-secondary', value: null, autofocus: true}],
        });
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
        if (selectedStep && journeyStep(selectedStep)) {
            renderJourneyInspector(journeyStep(selectedStep));
            return;
        }
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
        const layoutControls = definition.layout_controls || ['visible', 'span', 'alignment', 'order'];
        const contextStep = editorMode === 'design' ? journeyStep(journeyContextStep) : null;
        if (contextStep) {
            const context = document.createElement('div');
            context.className = 'page-editor-inspector-context';
            const text = document.createElement('span');
            text.textContent = 'Écran · ' + contextStep.label;
            const back = document.createElement('button');
            back.type = 'button';
            back.className = 'btn btn-sm btn-link';
            back.textContent = 'Retour au parcours';
            back.addEventListener('click', function () { setEditorMode('journey'); });
            context.append(text, back);
            inspector.appendChild(context);
        }
        const heading = document.createElement('h4');
        heading.className = 'h6 fw-semibold';
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
        if (layoutControls.includes('visible')) layoutFields.appendChild(visibilityWrapper);

        const span = document.createElement('input');
        span.type = 'range';
        span.className = 'form-range';
        span.id = 'editor-field-span';
        span.min = '1';
        span.max = '12';
        span.value = String(layout.span);
        bindValue(span, function (control) { layout.span = Number(control.value); });
        if (layoutControls.includes('span')) {
            const spanGroup = formGroup('Largeur (' + layout.span + '/12)', span);
            // bindValue ne re-rend pas à chaque « input » : le libellé suit
            // le curseur en direct, sinon sa valeur resterait figée.
            const spanLabel = spanGroup.querySelector('.form-label');
            span.addEventListener('input', function () {
                spanLabel.textContent = 'Largeur (' + span.value + '/12)';
            });
            layoutFields.appendChild(spanGroup);
        }

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
        if (layoutControls.includes('span')) layoutFields.appendChild(spanPresets);

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
        if (layoutControls.includes('alignment')) layoutFields.appendChild(formGroup('Alignement', alignment));

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
        if (layoutControls.includes('order')) layoutFields.appendChild(movement);

        const resetButton = document.createElement('button');
        resetButton.type = 'button';
        resetButton.className = 'btn btn-sm btn-outline-secondary w-100 mt-2';
        resetButton.textContent = 'Reprendre la version publiée';
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

        const factoryButton = document.createElement('button');
        factoryButton.type = 'button';
        factoryButton.className = 'btn btn-sm btn-outline-secondary w-100 mt-2';
        factoryButton.textContent = 'Reprendre les valeurs d’usine';
        factoryButton.title = 'Reprend les réglages livrés avec l’application pour ce composant.';
        factoryButton.addEventListener('click', function () {
            if (!defaults || !defaults.layout[selectedComponent]) return;
            mutate(function () {
                payload.layout[selectedComponent] = clone(defaults.layout[selectedComponent]);
                // Un réglage sans valeur d'usine (``null`` dans defaults)
                // conserve sa valeur : réassigner ``null`` produirait un
                // brouillon refusé à la sauvegarde.
                definition.config.forEach(function (field) {
                    const value = factoryValue('config', field.key);
                    if (value !== undefined) payload.config[field.key] = clone(value);
                });
                definition.css.forEach(function (field) {
                    const value = factoryValue('css', field.key);
                    if (value !== undefined) payload.css[field.key] = clone(value);
                });
            }, 'Composant réinitialisé sur les valeurs d’usine.');
        });
        layoutFields.appendChild(factoryButton);

        const fieldIsRelevant = function (field) {
            if (field.scenarios && !field.scenarios.includes(scenarioSelect.value)) return false;
            if (field.visible_when) {
                // ``key_by_scenario`` : la clé qui conditionne le champ peut
                // dépendre du scénario (forme des boutons de l'accueil ou des
                // sous-activités).
                const byScenario = field.visible_when.key_by_scenario || {};
                const key = byScenario[scenarioSelect.value] || field.visible_when.key;
                return (field.visible_when.values || []).includes(payload.config[key]);
            }
            return true;
        };
        const scenarioConfigFields = definition.config.filter(fieldIsRelevant);
        if (scenarioConfigFields.length) {
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
            let lastConfigGroup = null;
            scenarioConfigFields.forEach(function (field) {
                // Sous-groupes déclarés par field.group (ex. « QR code et
                // scan », « Écran de validation ») : même rendu qu'en
                // « Apparence » — un titre sépare les familles de réglages.
                if ((field.group || null) !== lastConfigGroup) {
                    lastConfigGroup = field.group || null;
                    if (lastConfigGroup) {
                        const heading = document.createElement('div');
                        heading.className = 'page-editor-fieldgroup';
                        heading.textContent = lastConfigGroup;
                        contentFields.appendChild(heading);
                    }
                }
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
                    if (field.min != null) control.min = String(field.min);
                    if (field.max != null) control.max = String(field.max);
                    control.value = payload.config[field.key] == null ? 0 : payload.config[field.key];
                    bindValue(control, function (element) {
                        let parsed = Number.parseInt(element.value, 10);
                        if (Number.isNaN(parsed)) parsed = field.min != null ? field.min : 0;
                        if (field.min != null) parsed = Math.max(field.min, parsed);
                        if (field.max != null) parsed = Math.min(field.max, parsed);
                        setConfigValue(field.key, parsed);
                    });
                    // Boutons −/+ autour du champ ; l'unité (« s », « px »)
                    // reste suffixée en input-group plutôt que répétée
                    // dans le libellé.
                    control.id = 'editor-config-' + field.key + '-value';
                    const intGroup = document.createElement('div');
                    intGroup.className = 'input-group page-editor-stepper';
                    intGroup.dataset.labelFor = control.id;
                    intGroup.append(
                        stepperButton(control, -1, 'Diminuer'),
                        control,
                        stepperButton(control, 1, 'Augmenter'));
                    if (field.unit) {
                        const suffix = document.createElement('span');
                        suffix.className = 'input-group-text';
                        suffix.textContent = field.unit;
                        intGroup.appendChild(suffix);
                    }
                    control = intGroup;
                } else if (field.type === 'image') {
                    control = createImageControl(
                        'editor-config-' + field.key,
                        field.key,
                        function (element) { setConfigValue(field.key, element.value); });
                } else {
                    control = document.createElement('textarea');
                    control.className = 'form-control';
                    control.rows = 3;
                    // Bloque la saisie au-delà de la limite serveur (colonne
                    // de la clé : 200 ou 6000 caractères).
                    if (field.max_length != null) control.maxLength = field.max_length;
                    control.value = payload.config[field.key] == null ? '' : payload.config[field.key];
                    bindValue(control, function (element) { setConfigValue(field.key, element.value); });
                }
                control.id = 'editor-config-' + field.key;
                const group = formGroup(
                    field.label, control, factoryResetButton('config', field.key));
                if (field.help) {
                    // Aide courte sous le champ (ex. où choisir les images).
                    const help = document.createElement('div');
                    help.className = 'form-text';
                    help.id = control.id + '-help';
                    help.textContent = field.help;
                    control.setAttribute('aria-describedby', help.id);
                    group.appendChild(help);
                }
                if (field.markers && field.markers.length && control instanceof HTMLTextAreaElement) {
                    group.appendChild(createMarkerToolbar(control, field.markers));
                }
                contentFields.appendChild(group);
            });
        }

        const scenarioCssFields = definition.css.filter(fieldIsRelevant);
        if (scenarioCssFields.length) {
            const appearanceFields = addFieldset('Apparence');
            let lastGroup = null;
            scenarioCssFields.forEach(function (field) {
                // Sous-groupes déclarés par field.group (ex. « Texte »,
                // « Bandeau », « Liseré ») : un titre sépare les réglages
                // qui agissent sur le même élément.
                if ((field.group || null) !== lastGroup) {
                    lastGroup = field.group || null;
                    if (lastGroup) {
                        const heading = document.createElement('div');
                        heading.className = 'page-editor-fieldgroup';
                        heading.textContent = lastGroup;
                        appearanceFields.appendChild(heading);
                    }
                }
                let control;
                const applyCss = function (value) { payload.css[field.key] = value; };
                if (field.type === 'color') {
                    control = createColorControl(
                        'editor-css-' + field.key,
                        payload.css[field.key],
                        function (element) { applyCss(element.value); }
                    );
                } else {
                    if (field.type === 'size') {
                        control = createSizeControl(
                            'editor-css-' + field.key, payload.css[field.key], applyCss);
                    } else if (field.type === 'number') {
                        control = createNumberControl(
                            'editor-css-' + field.key, payload.css[field.key],
                            field.step || 1, applyCss);
                    }
                    if (!control) {
                        // Type inconnu ou valeur non découpable (calc(),
                        // var(), mot-clé…) : le champ texte libre reste
                        // disponible, comme avant.
                        control = document.createElement('input');
                        control.type = 'text';
                        control.className = 'form-control';
                        control.id = 'editor-css-' + field.key;
                        control.value = payload.css[field.key] || '';
                        control.placeholder = field.type === 'number' ? '400' : '32px';
                        bindValue(control, function (element) { applyCss(element.value); });
                    }
                }
                appearanceFields.appendChild(formGroup(
                    field.label, control, factoryResetButton('css', field.key)));
            });
            appendContrastWarning(appearanceFields, Object.assign({}, definition, {css: scenarioCssFields}));
        }

        // La palette de la page est rendue après les propriétés du composant :
        // les réglages du bloc sélectionné restent accessibles immédiatement.
        renderPagePalette();
    }

    function selectComponent(componentId, trigger) {
        const context = journeyStepForScenario();
        if (journey && context) journeyContextStep = context.id;
        selectedComponent = componentId;
        selectedStep = null;
        if (journey && editorMode !== 'design') {
            setEditorMode('design', {reloadPreview: false});
        } else {
            rerenderAll();
            pushPayloadToPreview();
        }
        const current = zonesContainer.querySelector('[data-component-id="' + componentId + '"]');
        openInspectorDrawer(current || trigger);
    }

    // --- Parcours patient -------------------------------------------------
    // Miroir de page_journey.evaluate : même langage de conditions.
    function journeyCondition(condition) {
        if (!condition) return true;
        if (condition.all) return condition.all.every(journeyCondition);
        if (condition.any) return condition.any.some(journeyCondition);
        if (condition.not) return !journeyCondition(condition.not);
        if (condition.fact) {
            const fact = journeyFacts[condition.fact];
            return Array.isArray(fact) ? fact.length > 0 : Boolean(fact);
        }
        const expected = Object.prototype.hasOwnProperty.call(condition, 'is') ? condition.is : true;
        const value = payload.config[condition.config];
        return Array.isArray(expected) ? expected.includes(value) : value === expected;
    }

    function journeyText(text) {
        return String(text || '').replace(/\{(=?)([a-z_]+)\}/g, function (match, isConfig, name) {
            if (isConfig) {
                const configured = payload.config[name];
                return configured == null ? '' : String(configured);
            }
            const value = journeyFacts[name];
            if (Array.isArray(value)) {
                return value.map(function (item) { return '« ' + item + ' »'; }).join(', ');
            }
            return value == null ? '' : String(value);
        });
    }

    function journeyStatus(step) {
        const rule = (step.status || []).find(function (item) { return journeyCondition(item.when); });
        return rule ? {state: rule.state, text: journeyText(rule.text)} : {state: 'hidden', text: ''};
    }

    function journeyAllSteps() {
        if (!journey) return [];
        const all = [];
        journey.steps.forEach(function (step, index) {
            all.push(Object.assign({number: index + 1, parent: null}, step));
            (step.branches || []).forEach(function (branch) {
                all.push(Object.assign({number: null, parent: step.id}, branch));
            });
        });
        return all;
    }

    function journeyStep(stepId) {
        return journeyAllSteps().find(function (step) { return step.id === stepId; }) || null;
    }

    function simulationStartStep() {
        if (!journey) return null;
        return journey.steps.find(function (step) {
            return journeyStatus(step).state !== 'hidden';
        }) || journey.steps[0] || null;
    }

    function resetSimulation(options) {
        options = options || {};
        const previousScenario = scenarioSelect.value;
        const start = simulationStartStep();
        simulationStepId = start ? start.id : null;
        simulationHistory = start ? [{stepId: start.id, label: start.label, via: null}] : [];
        simulationOutcome = null;
        if (start) scenarioSelect.value = start.scenario;
        if (options.render !== false) rerenderAll();
        if (options.preview !== false) {
            if (start && previousScenario !== start.scenario) loadPreview();
            else pushPayloadToPreview();
        }
    }

    function ensureSimulationState() {
        if (!journey || !payload) return;
        const current = journeyStep(simulationStepId);
        if (!current || journeyStatus(current).state === 'hidden') {
            resetSimulation({render: false, preview: false});
        } else {
            scenarioSelect.value = current.scenario;
        }
    }

    function resolveSimulationTarget(targetId) {
        const requested = journeyStep(targetId);
        if (!requested) return {step: null, skipped: []};
        if (requested.parent || journeyStatus(requested).state !== 'hidden') {
            return {step: requested, skipped: []};
        }
        const index = journey.steps.findIndex(function (step) { return step.id === requested.id; });
        const skipped = [];
        for (let cursor = index; cursor < journey.steps.length; cursor += 1) {
            const candidate = journey.steps[cursor];
            if (journeyStatus(candidate).state !== 'hidden') return {step: candidate, skipped: skipped};
            skipped.push(candidate.label);
        }
        return {step: null, skipped: skipped};
    }

    function runSimulationAction(action) {
        const previousScenario = scenarioSelect.value;
        if (action.finish) {
            simulationOutcome = journeyText(action.finish);
        } else {
            const resolved = resolveSimulationTarget(action.target);
            if (resolved.step) {
                simulationStepId = resolved.step.id;
                scenarioSelect.value = resolved.step.scenario;
                simulationOutcome = null;
                let via = journeyText(action.label);
                if (resolved.skipped.length) {
                    via += ' · sans passer par : ' + resolved.skipped.join(', ');
                }
                simulationHistory.push({stepId: resolved.step.id, label: resolved.step.label, via: via});
            } else {
                const home = journeyStep('home') || simulationStartStep();
                simulationStepId = home ? home.id : null;
                if (home) {
                    scenarioSelect.value = home.scenario;
                    simulationHistory.push({stepId: home.id, label: 'Retour à l’accueil', via: journeyText(action.label)});
                }
                if (resolved.skipped.length === 1) {
                    simulationOutcome = 'Parcours terminé : l’écran « ' + resolved.skipped[0]
                        + ' » est masqué, retour immédiat à l’accueil.';
                } else if (resolved.skipped.length > 1) {
                    simulationOutcome = 'Parcours terminé : les écrans « '
                        + resolved.skipped.join(' », « ') + ' » sont masqués, retour immédiat à l’accueil.';
                } else {
                    simulationOutcome = 'Parcours terminé.';
                }
            }
        }
        rerenderAll();
        if (scenarioSelect.value !== previousScenario) loadPreview();
        else pushPayloadToPreview();
    }

    function simulationActionButton(action) {
        const button = document.createElement('button');
        button.type = 'button';
        const tone = action.tone || 'primary';
        button.className = 'btn ' + (tone === 'primary' ? 'btn-primary' : 'btn-outline-' + tone);
        button.dataset.simulationAction = action.id;
        if (action.icon) {
            const icon = document.createElement('i');
            icon.className = 'bi ' + action.icon;
            icon.setAttribute('aria-hidden', 'true');
            button.appendChild(icon);
        }
        button.appendChild(document.createTextNode((action.icon ? ' ' : '') + journeyText(action.label)));
        button.addEventListener('click', function () { runSimulationAction(action); });
        return button;
    }

    function renderSimulation() {
        if (!simulationPanel) return;
        simulationPanel.hidden = !journey || editorMode !== 'simulation';
        if (simulationPanel.hidden || !payload) return;
        ensureSimulationState();
        const step = journeyStep(simulationStepId);
        if (!step) return;

        const mainIndex = journey.steps.findIndex(function (candidate) { return candidate.id === step.id; });
        simulationKicker.textContent = step.parent
            ? 'Incident ou écran annexe'
            : 'Étape ' + (mainIndex + 1) + ' sur ' + journey.steps.length;
        simulationIcon.className = 'bi ' + (step.icon || 'bi-window') + ' page-editor-simulation-icon';
        simulationStepLabel.textContent = step.label;
        simulationRole.textContent = step.role || '';
        simulationResult.hidden = !simulationOutcome;
        simulationResult.textContent = simulationOutcome || '';

        simulationTrail.replaceChildren();
        simulationHistory.forEach(function (entry, index) {
            const item = document.createElement('li');
            item.className = 'page-editor-simulation-trail-item';
            if (index === simulationHistory.length - 1) item.setAttribute('aria-current', 'step');
            const marker = document.createElement('span');
            marker.className = 'page-editor-simulation-trail-marker';
            marker.textContent = String(index + 1);
            const content = document.createElement('span');
            const label = document.createElement('strong');
            label.textContent = entry.label;
            content.appendChild(label);
            if (entry.via) {
                const via = document.createElement('small');
                via.textContent = entry.via;
                content.appendChild(via);
            }
            item.append(marker, content);
            simulationTrail.appendChild(item);
        });

        simulationActions.replaceChildren();
        if (simulationOutcome) {
            const restart = document.createElement('button');
            restart.type = 'button';
            restart.className = 'btn btn-primary';
            restart.innerHTML = '<i class="bi bi-arrow-counterclockwise" aria-hidden="true"></i> Recommencer un test';
            restart.addEventListener('click', function () { resetSimulation(); });
            simulationActions.appendChild(restart);
            return;
        }
        const availableActions = (step.actions || []).filter(function (action) {
            return journeyCondition(action.when);
        });
        availableActions.forEach(function (action) {
            simulationActions.appendChild(simulationActionButton(action));
        });
        if (!availableActions.length) {
            const empty = document.createElement('p');
            empty.className = 'alert alert-warning py-2 mb-0';
            empty.textContent = 'Aucune action n’est disponible avec les réglages actuels.';
            simulationActions.appendChild(empty);
        }
    }

    const JOURNEY_STATE_LABELS = {
        shown: 'Toujours',
        conditional: 'Selon le cas',
        hidden: 'Jamais'
    };

    function journeyStateLabel(step, state) {
        // Une étape principale absente est « sautée » : le patient passe à
        // la suivante ; un écran annexe ne s'affiche simplement « jamais ».
        if (state === 'hidden' && !step.parent) return 'Sautée';
        return JOURNEY_STATE_LABELS[state] || state;
    }

    function selectJourneyStep(stepId) {
        const step = journeyStep(stepId);
        if (!step) return;
        selectedStep = stepId;
        journeyContextStep = stepId;
        selectedComponent = null;
        const changedScenario = scenarioSelect.value !== step.scenario;
        scenarioSelect.value = step.scenario;
        rerenderAll();
        if (changedScenario) loadPreview();
        else pushPayloadToPreview();
        const current = journeyPath.querySelector('[data-journey-step="' + stepId + '"]');
        if (compactInspectorMedia.matches) openInspectorDrawer(current);
        else if (current) current.focus();
    }

    const JOURNEY_STATE_ICONS = {
        shown: 'bi-check-circle-fill',
        conditional: 'bi-signpost-split',
        hidden: 'bi-slash-circle'
    };

    function journeyStateIcon(state) {
        const icon = document.createElement('i');
        icon.className = 'bi ' + (JOURNEY_STATE_ICONS[state] || 'bi-circle') + ' page-editor-journey-state-icon';
        icon.setAttribute('aria-hidden', 'true');
        return icon;
    }

    // Bouton d'étape compact : icône de l'écran, libellé, icône d'état. Le
    // libellé barré et la forme de l'icône portent l'état (pas la seule
    // couleur) ; l'explication est au survol et pour les lecteurs d'écran.
    function journeyButton(step, status) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'page-editor-journey-button is-' + status.state;
        button.dataset.journeyStep = step.id;
        button.dataset.journeyState = status.state;
        button.setAttribute('aria-pressed', selectedStep === step.id ? 'true' : 'false');
        if (scenarioSelect.value === step.scenario) button.classList.add('is-previewed');
        // Le texte commence souvent déjà par l'état (« Sautée : … ») : pas
        // de répétition dans l'infobulle ni pour les lecteurs d'écran.
        const stateLabel = journeyStateLabel(step, status.state);
        const explanation = status.text.indexOf(stateLabel) === 0
            ? status.text : stateLabel + ' : ' + status.text;
        button.title = explanation;
        const label = document.createElement('span');
        label.className = 'page-editor-journey-label';
        label.textContent = step.label;
        const detail = document.createElement('span');
        detail.className = 'visually-hidden';
        detail.textContent = ' — ' + explanation;
        if (step.number) {
            // Étape principale, façon « stepper » : pastille numérotée et
            // icône d'état sur une ligne, libellé dessous (tient dans une
            // colonne étroite sans chevaucher les flèches).
            const head = document.createElement('span');
            head.className = 'page-editor-journey-head';
            const number = document.createElement('span');
            number.className = 'page-editor-journey-number';
            number.textContent = String(step.number);
            head.append(number, journeyStateIcon(status.state));
            button.append(head, label, detail);
        } else {
            const icon = document.createElement('i');
            icon.className = 'bi ' + (step.icon || 'bi-window') + ' page-editor-journey-icon';
            icon.setAttribute('aria-hidden', 'true');
            button.append(icon, label, journeyStateIcon(status.state), detail);
        }
        button.addEventListener('click', function () { selectJourneyStep(step.id); });
        return button;
    }

    function journeyToggleChecked(toggle) {
        const value = Boolean(payload.config[toggle.key]);
        return toggle.invert ? !value : value;
    }

    function setJourneyToggle(step, checked, focusId) {
        const toggle = step.toggle;
        mutate(function () {
            payload.config[toggle.key] = toggle.invert ? !checked : checked;
        }, (checked ? 'Étape affichée : ' : 'Étape masquée : ') + step.label + '.');
        // La frise et l'inspecteur viennent d'être reconstruits : le focus
        // revient sur le même interrupteur (navigation au clavier).
        const again = document.getElementById(focusId);
        if (again) again.focus();
    }

    // Sous une étape de la frise : interrupteur « Afficher » si l'étape peut
    // être masquée, sinon la raison courte (« Obligatoire »…).
    function journeyStepVisibility(step) {
        const row = document.createElement('div');
        row.className = 'page-editor-journey-visibility';
        if (step.toggle) {
            const id = 'editor-journey-toggle-' + step.id;
            row.classList.add('form-check', 'form-switch');
            const input = document.createElement('input');
            input.type = 'checkbox';
            input.className = 'form-check-input';
            input.setAttribute('role', 'switch');
            input.id = id;
            input.checked = journeyToggleChecked(step.toggle);
            input.setAttribute('aria-label', 'Afficher l’étape « ' + step.label + ' »');
            input.title = step.toggle.help || step.toggle.label;
            input.addEventListener('change', function () { setJourneyToggle(step, input.checked, id); });
            const label = document.createElement('label');
            label.className = 'form-check-label';
            label.htmlFor = id;
            label.setAttribute('aria-hidden', 'true');
            label.textContent = 'Afficher';
            row.append(input, label);
        } else if (step.fixed) {
            const icon = document.createElement('i');
            icon.className = 'bi bi-lock';
            icon.setAttribute('aria-hidden', 'true');
            const text = document.createElement('span');
            text.textContent = step.fixed;
            row.append(icon, text);
        }
        return row;
    }

    function renderJourney() {
        if (!journeySection) return;
        if (!journey || editorMode !== 'journey') {
            journeySection.hidden = true;
            journeySkipped.hidden = true;
            return;
        }
        journeySection.hidden = false;
        document.getElementById('editor-journey-title').textContent = journey.title;
        document.getElementById('editor-journey-intro').textContent = journey.intro;
        journeyPath.replaceChildren();
        journeyBranches.replaceChildren();
        const route = [];
        journey.steps.forEach(function (step, index) {
            const status = journeyStatus(step);
            const item = document.createElement('li');
            item.className = 'page-editor-journey-step is-' + status.state;
            item.appendChild(journeyButton(Object.assign({number: index + 1, parent: null}, step), status));
            item.appendChild(journeyStepVisibility(step));
            journeyPath.appendChild(item);
            if (status.state !== 'hidden') {
                route.push(step.label + (status.state === 'conditional' ? ' (selon le cas)' : ''));
            }
            if ((step.branches || []).length) {
                const group = document.createElement('div');
                group.className = 'page-editor-journey-branch-group';
                const caption = document.createElement('span');
                caption.className = 'page-editor-journey-branch-caption';
                caption.textContent = (step.branches_label || step.label) + ' :';
                const list = document.createElement('ul');
                list.className = 'page-editor-journey-branches';
                list.setAttribute('aria-label', 'Écrans annexes — ' + (step.branches_label || step.label));
                step.branches.forEach(function (branch) {
                    const branchItem = document.createElement('li');
                    branchItem.appendChild(journeyButton(
                        Object.assign({number: null, parent: step.id}, branch), journeyStatus(branch)));
                    list.appendChild(branchItem);
                });
                group.append(caption, list);
                journeyBranches.appendChild(group);
            }
        });
        const summaryLabel = document.createElement('strong');
        summaryLabel.textContent = 'Parcours actuel : ';
        journeySummary.replaceChildren(summaryLabel, document.createTextNode(route.join(' → ')));

        // Écran prévisualisé que le patient ne verra jamais : on le dit
        // au-dessus de l'aperçu plutôt que de laisser croire qu'il s'affiche.
        const previewed = journeyAllSteps().find(function (step) { return step.scenario === scenarioSelect.value; });
        const previewedStatus = previewed ? journeyStatus(previewed) : null;
        const exitRule = previewed
            ? (previewed.exit || []).find(function (rule) { return journeyCondition(rule.when); })
            : null;
        journeySkipped.replaceChildren();
        if (previewedStatus && previewedStatus.state === 'hidden') {
            journeySkipped.hidden = false;
            journeySkipped.className = 'alert alert-secondary py-2 small mb-2';
            journeySkipped.textContent = 'Avec les réglages actuels, le patient ne voit pas cet écran. '
                + previewedStatus.text + ' L’aperçu reste disponible pour préparer ses textes.';
        } else if (exitRule) {
            // Ce que l'aperçu ne peut pas montrer : quand et comment la borne
            // quitte l'écran (délais, suite donnée), valeurs du brouillon.
            journeySkipped.hidden = false;
            journeySkipped.className = 'alert alert-info py-2 small mb-2 page-editor-journey-exit';
            const icon = document.createElement('i');
            icon.className = 'bi bi-stopwatch me-1';
            icon.setAttribute('aria-hidden', 'true');
            const label = document.createElement('strong');
            label.textContent = 'Sur la borne : ';
            journeySkipped.append(icon, label, document.createTextNode(journeyText(exitRule.text)));
        } else {
            journeySkipped.hidden = true;
        }
    }

    function journeyChoiceControl(choice, enabled) {
        const id = 'editor-journey-' + choice.key;
        const wrapper = document.createElement('div');
        wrapper.className = 'mb-3 page-editor-journey-choice' + (enabled ? '' : ' is-disabled');
        const describe = [];
        let control;
        if (choice.type === 'bool') {
            wrapper.classList.add('form-check', 'form-switch');
            control = document.createElement('input');
            control.type = 'checkbox';
            control.setAttribute('role', 'switch');
            control.className = 'form-check-input';
            control.id = id;
            const value = Boolean(payload.config[choice.key]);
            control.checked = choice.invert ? !value : value;
            control.addEventListener('change', function () {
                const checked = control.checked;
                mutate(function () {
                    payload.config[choice.key] = choice.invert ? !checked : checked;
                }, 'Parcours modifié : ' + choice.label + (checked ? ' — activé.' : ' — désactivé.'));
                // L'inspecteur vient d'être reconstruit : le focus revient
                // sur le même interrupteur pour la navigation au clavier.
                const again = document.getElementById(id);
                if (again) again.focus();
            });
            const label = document.createElement('label');
            label.className = 'form-check-label';
            label.htmlFor = id;
            label.textContent = choice.label;
            wrapper.append(control, label);
        } else {
            const label = document.createElement('label');
            label.className = 'form-label';
            label.htmlFor = id;
            label.textContent = choice.label;
            if (choice.type === 'select') {
                control = document.createElement('select');
                control.className = 'form-select form-select-sm';
                (choice.choices || []).forEach(function (option) {
                    const element = document.createElement('option');
                    element.value = option[0];
                    element.textContent = option[1];
                    control.appendChild(element);
                });
                control.value = payload.config[choice.key] == null ? '' : payload.config[choice.key];
            } else {
                control = document.createElement('input');
                control.type = 'number';
                control.step = '1';
                control.className = 'form-control form-control-sm';
                if (choice.min != null) control.min = String(choice.min);
                if (choice.max != null) control.max = String(choice.max);
                control.value = payload.config[choice.key] == null ? 0 : payload.config[choice.key];
            }
            control.id = id;
            control.addEventListener('change', function () {
                let value = control.value;
                if (choice.type === 'int') {
                    value = Number.parseInt(value, 10);
                    if (Number.isNaN(value)) value = choice.min != null ? choice.min : 0;
                    if (choice.min != null) value = Math.max(choice.min, value);
                    if (choice.max != null) value = Math.min(choice.max, value);
                }
                mutate(function () { payload.config[choice.key] = value; },
                    'Parcours modifié : ' + choice.label + '.');
                const again = document.getElementById(id);
                if (again) again.focus();
            });
            if (choice.unit) {
                const group = document.createElement('div');
                group.className = 'input-group input-group-sm';
                const suffix = document.createElement('span');
                suffix.className = 'input-group-text';
                suffix.textContent = choice.unit;
                group.append(control, suffix);
                wrapper.append(label, group);
            } else {
                wrapper.append(label, control);
            }
        }
        control.disabled = !enabled;
        if (choice.help) {
            const help = document.createElement('div');
            help.className = 'form-text';
            help.id = id + '-help';
            help.textContent = choice.help;
            describe.push(help.id);
            wrapper.appendChild(help);
        }
        if (!enabled) {
            const note = document.createElement('div');
            note.className = 'form-text fst-italic';
            note.id = id + '-disabled';
            note.textContent = 'Sans effet avec les réglages ci-dessus.';
            describe.push(note.id);
            wrapper.appendChild(note);
        }
        if (describe.length) control.setAttribute('aria-describedby', describe.join(' '));
        return wrapper;
    }

    function renderJourneyInspector(step) {
        const status = journeyStatus(step);
        const heading = document.createElement('h4');
        heading.className = 'h6 fw-semibold mb-1';
        heading.textContent = (step.number ? 'Étape ' + step.number + ' · ' : 'Écran annexe · ') + step.label;
        const role = document.createElement('p');
        role.className = 'small text-muted';
        role.textContent = step.role;
        const state = document.createElement('div');
        state.className = 'page-editor-journey-state is-' + status.state;
        state.setAttribute('role', 'status');
        const stateIcon = document.createElement('i');
        stateIcon.className = 'bi ' + ({shown: 'bi-check-circle', conditional: 'bi-signpost-split', hidden: 'bi-eye-slash'}[status.state] || 'bi-info-circle');
        stateIcon.setAttribute('aria-hidden', 'true');
        const stateText = document.createElement('span');
        stateText.textContent = status.text;
        state.append(stateIcon, stateText);
        inspector.append(heading, role, state);

        if (step.toggle) {
            const box = document.createElement('div');
            box.className = 'page-editor-journey-toggle-box';
            const control = journeyChoiceControl(step.toggle, true);
            // Même réglage que l'interrupteur de la frise : un seul chemin
            // d'écriture (setJourneyToggle) et un message d'état explicite.
            const input = control.querySelector('input');
            const replacement = input.cloneNode(true);
            replacement.checked = journeyToggleChecked(step.toggle);
            replacement.addEventListener('change', function () {
                setJourneyToggle(step, replacement.checked, replacement.id);
            });
            input.replaceWith(replacement);
            box.appendChild(control);
            inspector.appendChild(box);
        } else if (step.fixed) {
            const fixed = document.createElement('p');
            fixed.className = 'small text-muted page-editor-journey-fixed';
            const icon = document.createElement('i');
            icon.className = 'bi bi-lock me-1';
            icon.setAttribute('aria-hidden', 'true');
            fixed.append(icon, document.createTextNode(step.fixed_help || step.fixed));
            inspector.appendChild(fixed);
        }

        (step.warnings || []).filter(function (warning) { return journeyCondition(warning.when); })
            .forEach(function (warning) {
                const alert = document.createElement('div');
                alert.className = 'alert alert-warning py-2 small';
                alert.setAttribute('role', 'alert');
                alert.textContent = warning.text;
                inspector.appendChild(alert);
            });

        if ((step.choices || []).length) {
            const title = document.createElement('p');
            title.className = 'page-editor-fieldgroup mt-3';
            title.textContent = 'Ce que voit le patient';
            inspector.appendChild(title);
            step.choices.forEach(function (choice) {
                inspector.appendChild(journeyChoiceControl(choice, journeyCondition(choice.enabled_when)));
            });
        }
        if (step.note) {
            const note = document.createElement('p');
            note.className = 'small';
            note.textContent = step.note;
            inspector.appendChild(note);
        }
        if (step.link) {
            const link = document.createElement('a');
            link.className = 'btn btn-sm btn-outline-secondary mb-3';
            link.href = step.link.href;
            link.textContent = step.link.label;
            inspector.appendChild(link);
        }

        const components = (step.components || []).filter(function (componentId) {
            return adapter.components[componentId];
        });
        if (components.length) {
            const title = document.createElement('p');
            title.className = 'page-editor-fieldgroup mt-3';
            title.textContent = 'Textes et apparence de cet écran';
            const list = document.createElement('div');
            list.className = 'd-flex flex-wrap gap-2';
            components.forEach(function (componentId) {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'btn btn-sm btn-outline-primary';
                button.textContent = adapter.components[componentId].label;
                button.addEventListener('click', function () { selectComponent(componentId); });
                list.appendChild(button);
            });
            inspector.append(title, list);
        }
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
        languageSelect.replaceChildren();
        previewLanguages.forEach(function (language) {
            const option = document.createElement('option');
            option.value = language.code;
            option.textContent = language.name;
            languageSelect.appendChild(option);
        });
        // Une seule langue active (« fr ») : le choix serait figé — le
        // sélecteur n'apparaît que s'il y a réellement une alternative.
        languageField.hidden = previewLanguages.length <= 1;
    }

    function loadPreview() {
        preview.src = '/admin/page-editor/' + encodeURIComponent(page)
            + '/preview?scenario=' + encodeURIComponent(scenarioSelect.value)
            + '&lang=' + encodeURIComponent(languageSelect.value || 'fr');
    }

    function resizePreview() {
        const viewport = adapter.viewports.find(function (item) { return item.id === viewportSelect.value; }) || adapter.viewports[0];
        const availableWidth = Math.max(280, previewShell.clientWidth - 32);
        const zoom = zoomSelect ? zoomSelect.value : 'fit';
        const scale = zoom === 'fit'
            ? Math.min(1, availableWidth / viewport.width)
            : Number(zoom);
        preview.width = String(viewport.width);
        preview.height = String(viewport.height);
        preview.style.transform = 'translateX(-50%) scale(' + scale + ')';
        previewShell.style.height = Math.ceil(viewport.height * scale + 32) + 'px';
    }

    function renderRevisions(revisions, publishedRevision) {
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
            if (revision.revision === publishedRevision) {
                const badge = document.createElement('span');
                badge.className = 'badge text-bg-success ms-2';
                badge.textContent = 'actuelle';
                label.appendChild(badge);
            }
            const actions = document.createElement('div');
            actions.className = 'btn-group btn-group-sm';
            const compareButton = document.createElement('button');
            compareButton.type = 'button';
            compareButton.className = 'btn btn-outline-secondary';
            compareButton.textContent = 'Comparer';
            compareButton.addEventListener('click', function () { compareRevision(revision.revision); });
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-outline-primary';
            button.textContent = 'Restaurer';
            button.addEventListener('click', function () { restoreRevision(revision.revision); });
            actions.append(compareButton, button);
            item.append(label, actions);
            list.appendChild(item);
        });
        revisionsContainer.appendChild(list);
    }

    async function compareRevision(revision) {
        try {
            const diff = await requestJSON('/admin/page-editor/' + encodeURIComponent(page)
                + '/revisions/' + revision + '/diff');
            await openDialog({
                title: 'Révision ' + revision + ' — différences avec la version publiée',
                body: buildDiffList(diff),
                actions: [{label: 'Fermer', className: 'btn btn-secondary', value: true, autofocus: true}],
            });
        } catch (error) {
            setStatus(error.message, 'danger');
        }
    }

    // Le brouillon est partagé entre administrateurs : nommer son dernier
    // auteur rend visible un travail mené en parallèle. Le client ne
    // connaît pas son propre identifiant — l'auteur est affiché tel quel,
    // sans tentative de comparaison.
    function draftStatusText(state) {
        const details = [];
        if (state.draft_author) details.push('par ' + state.draft_author);
        if (state.draft_updated_at) {
            details.push('le ' + new Date(state.draft_updated_at).toLocaleString('fr-FR'));
        }
        return details.length
            ? 'Brouillon partagé — modifié ' + details.join(' ') + '.'
            : 'Brouillon partagé chargé.';
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
            // La copie est reconstruite sur la version publiée : les réglages
            // apparus depuis son enregistrement prennent la valeur publiée et
            // ceux retirés depuis sont éliminés — sinon la sauvegarde du
            // brouillon restauré serait refusée par la validation.
            const restored = clone(published);
            // L'empreinte reste celle de la copie : la détection d'un
            // changement en mode avancé à la publication est inchangée.
            restored.base_hash = backup.payload.base_hash;
            const backupLayout = backup.payload.layout || {};
            Object.keys(restored.layout).forEach(function (componentId) {
                if (backupLayout[componentId]) {
                    applyKnownKeys(restored.layout[componentId], backupLayout[componentId]);
                }
            });
            ['config', 'css'].forEach(function (section) {
                applyKnownKeys(restored[section], backup.payload[section]);
            });
            payload = restored;
            dirty = true;
            scheduleAutosave();
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
            setStatus(hasDraft ? draftStatusText(state) : 'Configuration publiée chargée.',
                hasDraft ? 'info' : 'light');
        });
        statusBox.append(text, restoreButton, ignoreButton);
    }

    async function loadState(message) {
        root.setAttribute('aria-busy', 'true');
        try {
            const state = await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/state');
            adapter = state.adapter;
            journey = adapter.journey || null;
            journeyFacts = state.journey_facts || {};
            if (selectedStep && !journeyStep(selectedStep)) selectedStep = null;
            if (journeyContextStep && !journeyStep(journeyContextStep)) journeyContextStep = null;
            if (root.dataset.modeReady !== 'true') {
                let storedMode = null;
                try { storedMode = localStorage.getItem('page-editor-mode-' + page); } catch (error) {}
                editorMode = journey && ['journey', 'design', 'simulation'].includes(storedMode)
                    ? storedMode : (journey ? 'journey' : 'design');
                root.dataset.modeReady = 'true';
            }
            paletteSources = state.palette_sources || [];
            disabledComponents = state.disabled_components || {};
            previewLanguages = state.languages || [];
            const nextPublishedRevision = Number(state.published_revision) || 0;
            if (publishedRevision && nextPublishedRevision !== publishedRevision) appliedRevision = null;
            publishedRevision = nextPublishedRevision;
            published = clone(state.published);
            defaults = state.defaults || null;
            hasDraft = Boolean(state.draft);
            payload = clone(state.draft || state.published);
            draftVersion = state.draft_version;
            dirty = false;
            undoStack = [];
            redoStack = [];
            populateSelectors();
            if (journey && editorMode === 'journey') {
                const context = journeyStep(selectedStep) || journeyStep(journeyContextStep) || journeyStepForScenario();
                selectedStep = context ? context.id : null;
                journeyContextStep = selectedStep;
                selectedComponent = null;
            } else if (journey && editorMode === 'simulation') {
                selectedStep = null;
                selectedComponent = null;
            } else {
                selectedStep = null;
                const context = journeyStep(journeyContextStep);
                if (!selectedComponent || !componentBelongsToStep(selectedComponent, context)) {
                    selectedComponent = Object.keys(adapter.components).find(function (componentId) {
                        return componentBelongsToStep(componentId, context);
                    }) || Object.keys(adapter.components)[0];
                }
            }
            syncEditorModeChrome();
            rerenderAll();
            renderRevisions(state.revisions || [], state.published_revision);
            loadPreview();
            updateButtons();
            if (message) {
                setStatus(message, hasDraft ? 'info' : 'light');
            } else {
                offerLocalBackup(state);
                if (!statusBox.querySelector('button')) {
                    setStatus(hasDraft ? draftStatusText(state) : 'Configuration publiée chargée.',
                        hasDraft ? 'info' : 'light');
                }
            }
        } catch (error) {
            setStatus(error.message, 'danger');
        } finally {
            root.setAttribute('aria-busy', 'false');
        }
    }

    async function saveDraft(auto) {
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
                // « auto » (autosave) n'écrit pas de ligne d'audit : sinon
                // chaque pause de ~3 s en saisie en produirait une.
                body: JSON.stringify({draft_version: draftVersion, payload: payload, auto: auto === true})
            });
            draftVersion = data.draft_version;
            hasDraft = true;
            if (canonical(payload) === sent) {
                payload = clone(data.draft);
                dirty = false;
                // Plus rien à enregistrer : un minuteur d'autosave encore
                // armé (saisie juste avant l'enregistrement) ne doit pas
                // renvoyer le même brouillon une seconde fois.
                if (autosaveTimer) {
                    clearTimeout(autosaveTimer);
                    autosaveTimer = null;
                }
                if (canonical(payload) !== sent) {
                    // Le serveur a normalisé des valeurs : les refléter dans
                    // l'inspecteur pour éviter toute divergence silencieuse.
                    rerenderAll();
                    pushPayloadToPreview();
                }
                setStatus('Brouillon partagé enregistré.', 'success');
            } else {
                dirty = true;
                // La saisie pendant la requête n'est pas dans ce brouillon :
                // l'autosave la reprendra sans attendre un nouveau geste.
                scheduleAutosave();
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
            componentContrastFindings(definition).forEach(function (failure) {
                findings.push({
                    level: 'warning',
                    message: '« ' + definition.label + ' » : contraste faible ('
                        + failure.ratio.toFixed(1).replace('.', ',') + ':1) entre « '
                        + cssFieldLabel(definition, failure.font) + ' » et « '
                        + cssFieldLabel(definition, failure.background) + ' ».',
                });
            });
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
            const clippedComponents = new Set();
            doc.querySelectorAll('[data-page-editor-component]').forEach(function (componentEl) {
                if (componentEl.offsetParent === null) return;
                const rect = componentEl.getBoundingClientRect();
                const componentId = componentEl.dataset.pageEditorComponent;
                const label = (adapter.components[componentId] || {}).label || componentId;
                if (rect.top < -4 || rect.left < -4
                    || rect.bottom > viewport.height + 4 || rect.right > viewport.width + 4
                    || componentEl.scrollHeight > componentEl.clientHeight + 4
                    || componentEl.scrollWidth > componentEl.clientWidth + 4) {
                    clippedComponents.add(label);
                }
            });
            clippedComponents.forEach(function (label) {
                findings.push({
                    level: 'warning',
                    message: '« ' + label + ' » dépasse ou coupe une partie de son contenu dans ce format.',
                });
            });
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
                // Ne ferme que le dialogue empilé au-dessus : sinon Échap
                // dans une confirmation ouverte par-dessus « Thèmes »
                // fermerait les deux d'un coup.
                if (event.key !== 'Escape') return;
                const backdrops = document.querySelectorAll('.page-editor-dialog-backdrop');
                if (backdrops[backdrops.length - 1] !== backdrop) return;
                close(null);
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

    // Remplace la boîte de confirmation native du navigateur : celle-ci
    // reprend le style de l'éditeur. ``openDialog`` ne résout ``true`` que
    // via le bouton de confirmation — fermer (Échap, croix, « Annuler »)
    // répond ``null``.
    function confirmDialog(title, message, confirmLabel, danger) {
        const body = document.createElement('p');
        body.textContent = message;
        return openDialog({
            title: title,
            body: body,
            actions: [
                {label: 'Annuler', className: 'btn btn-outline-secondary', value: null},
                {label: confirmLabel, className: 'btn ' + (danger ? 'btn-danger' : 'btn-primary'), value: true, autofocus: true},
            ],
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
        renderScreenRows(list, data);
        container.appendChild(list);
        return container;
    }

    function renderScreenRows(list, data) {
        list.replaceChildren();
        data.screens.forEach(function (screen) {
            const item = document.createElement('li');
            item.className = 'd-flex align-items-center gap-2';
            const identity = document.createElement('div');
            const name = document.createElement('span');
            const when = screen.at
                ? ' · accusé ' + new Date(screen.at * 1000).toLocaleTimeString('fr-FR')
                : '';
            // ``screen.sid`` est le sid complet : il sert à cibler le
            // rechargement ; l'affichage reste tronqué à 8 caractères.
            name.textContent = screen.username + ' (#' + String(screen.sid).slice(0, 8) + ')'
                + ' — révision ' + (screen.revision === null ? 'inconnue' : screen.revision) + when;
            const details = document.createElement('div');
            details.className = 'small text-muted';
            const agent = screen.user_agent || 'agent inconnu';
            details.textContent = (screen.ip || 'IP inconnue') + ' · '
                + (agent.length > 60 ? agent.slice(0, 57) + '…' : agent);
            details.title = agent;
            identity.append(name, details);
            const badge = document.createElement('span');
            const status = SCREEN_STATUS[screen.status] || SCREEN_STATUS.pending;
            badge.className = 'badge ms-auto ' + status.className;
            badge.textContent = status.label;
            const reload = document.createElement('button');
            reload.type = 'button';
            reload.className = 'btn btn-sm btn-outline-primary';
            reload.textContent = 'Recharger';
            reload.title = 'Recharger uniquement cet écran.';
            reload.addEventListener('click', async function () {
                reload.disabled = true;
                try {
                    await reloadScreen(screen.sid);
                    // Le badge de l'écran reflète son nouvel accusé : la
                    // liste est relue plutôt que figée.
                    renderScreenRows(list, await fetchScreens());
                } catch (error) {
                    setStatus(error.message, 'danger');
                    reload.disabled = false;
                }
            });
            item.append(identity, badge, reload);
            list.appendChild(item);
        });
    }

    async function reloadScreen(sid) {
        await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/apply', {
            method: 'POST',
            body: JSON.stringify({sid: sid}),
        });
        setStatus('Ordre de rechargement envoyé à cet écran.', 'success');
        await watchScreenAcks(sid);
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

    // Après « Appliquer » (ou « Recharger » sur un écran précis) : surveille
    // les accusés jusqu'à ce que les écrans concernés affichent la révision
    // publiée (ou 30 s max). ``targetSid`` limite l'attente à un seul écran.
    async function watchScreenAcks(targetSid) {
        const deadline = Date.now() + 30000;
        while (Date.now() < deadline) {
            const data = await fetchScreens();
            const screens = targetSid
                ? data.screens.filter(function (screen) { return screen.sid === targetSid; })
                : data.screens;
            const total = screens.length;
            const confirmed = screens.filter(function (screen) {
                return screen.status === 'current';
            }).length;
            if (!total) {
                setStatus(targetSid
                    ? 'L’écran visé n’est plus détecté : vérifiez « Écrans ».'
                    : 'Ordre de rechargement envoyé ; aucun écran connecté détecté.', 'warning');
                return;
            }
            if (confirmed === total) {
                setStatus(targetSid
                    ? 'Rechargement confirmé sur cet écran.'
                    : 'Rechargement confirmé sur ' + total + ' écran(s).', 'success');
                return;
            }
            setStatus('Ordre envoyé — ' + confirmed + '/' + total + ' écran(s) confirmé(s)…', 'info');
            await new Promise(function (resolve) { setTimeout(resolve, 2000); });
        }
        setStatus(targetSid
            ? 'Rechargement non confirmé par l’écran visé : vérifiez « Écrans ».'
            : 'Rechargement non confirmé par tous les écrans : vérifiez « Écrans ».', 'warning');
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
            let swatches = null;
            if (theme.colors) {
                swatches = document.createElement('div');
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
            // Choix « Appels » (thèmes d'annonce) : teinte discrète ou couleur
            // complémentaire. La dernière pastille montre l'accent retenu.
            let selectedCallStyle = null;
            if (theme.call_styles) {
                const showAccent = function (style) {
                    const accentSwatch = swatches && swatches.lastElementChild;
                    if (!accentSwatch) return;
                    accentSwatch.style.backgroundColor = style.css.calling_background_color;
                    accentSwatch.title = style.css.calling_background_color;
                };
                const group = document.createElement('div');
                group.className = 'page-editor-theme-call-styles';
                group.setAttribute('role', 'radiogroup');
                const legend = document.createElement('span');
                legend.id = 'theme-call-styles-' + theme.id;
                legend.textContent = 'Appels :';
                group.setAttribute('aria-labelledby', legend.id);
                group.appendChild(legend);
                theme.call_styles.forEach(function (style) {
                    const inputId = 'theme-call-style-' + theme.id + '-' + style.id;
                    const radio = document.createElement('input');
                    radio.type = 'radio';
                    radio.className = 'form-check-input';
                    radio.name = 'theme-call-style-' + theme.id;
                    radio.id = inputId;
                    radio.value = style.id;
                    radio.checked = style.id === theme.default_call_style;
                    const label = document.createElement('label');
                    label.className = 'form-check-label';
                    label.htmlFor = inputId;
                    const chip = document.createElement('span');
                    chip.className = 'page-editor-theme-call-chip';
                    chip.style.backgroundColor = style.css.calling_background_color;
                    chip.setAttribute('aria-hidden', 'true');
                    label.append(chip, style.label);
                    label.title = style.css.calling_background_color;
                    radio.addEventListener('change', function () {
                        if (!radio.checked) return;
                        selectedCallStyle = style;
                        showAccent(style);
                    });
                    const option = document.createElement('div');
                    option.className = 'form-check form-check-inline';
                    option.append(radio, label);
                    group.appendChild(option);
                    if (radio.checked) {
                        selectedCallStyle = style;
                        showAccent(style);
                    }
                });
                info.appendChild(group);
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
                const applied = selectedCallStyle
                    ? Object.assign({}, theme, {snapshot: Object.assign({}, theme.snapshot, {
                        css: Object.assign({}, theme.snapshot.css, selectedCallStyle.css),
                    })})
                    : theme;
                applyTheme(applied, {
                    appearance: sectionChecks.appearance.checked,
                    layout: sectionChecks.layout.checked,
                    content: !theme.builtin && sectionChecks.content.checked,
                });
            });
            actions.appendChild(applyButton);
            if (!theme.builtin) {
                const exportButton = document.createElement('button');
                exportButton.type = 'button';
                exportButton.className = 'btn btn-sm btn-outline-secondary';
                exportButton.textContent = 'Exporter';
                exportButton.setAttribute('aria-label', 'Exporter le thème ' + theme.name);
                exportButton.addEventListener('click', function () { exportTheme(theme); });
                actions.appendChild(exportButton);
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

        const importCard = document.createElement('div');
        importCard.className = 'page-editor-palette-copy mb-3';
        const importHeading = document.createElement('div');
        importHeading.className = 'fw-semibold mb-1';
        importHeading.textContent = 'Importer un thème';
        const importHelp = document.createElement('div');
        importHelp.className = 'small text-muted mb-2';
        importHelp.textContent = 'Fichier JSON produit par « Exporter » ou instantané brut (layout, config, css).';
        const importFile = document.createElement('input');
        importFile.type = 'file';
        importFile.accept = '.json,application/json';
        importFile.id = 'editor-theme-import-file';
        importFile.className = 'd-none';
        importFile.setAttribute('aria-label', 'Fichier de thème à importer');
        const importButton = document.createElement('button');
        importButton.type = 'button';
        importButton.className = 'btn btn-sm btn-outline-primary';
        importButton.id = 'editor-theme-import';
        importButton.textContent = 'Importer…';
        importButton.addEventListener('click', function () { importFile.click(); });
        importFile.addEventListener('change', function () {
            const file = importFile.files && importFile.files[0];
            // Vider la sélection : réimporter le même fichier redéclenche
            // « change ».
            importFile.value = '';
            if (file) importThemeFile(file);
        });
        importCard.append(importHeading, importHelp, importButton, importFile);
        container.appendChild(importCard);

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

    // POST /themes partagé par « Enregistrer comme thème » et l'import :
    // le 409 « nom déjà pris » propose l'écrasement explicite, avec un
    // message adapté à l'origine du thème.
    async function storeTheme(options) {
        try {
            await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/themes', {
                method: 'POST',
                body: JSON.stringify({
                    name: options.name,
                    description: options.description || '',
                    overwrite: Boolean(options.overwrite),
                    payload: options.payload,
                }),
            });
            setStatus(options.successMessage
                || ('Thème « ' + options.name.trim() + ' » enregistré.'), 'success');
            closeThemesDialog();
            showThemes();
        } catch (error) {
            if (error.status === 409) {
                if (await confirmDialog('Remplacer le thème',
                    options.conflictMessage
                        || 'Un thème porte déjà ce nom : le remplacer ?',
                    'Remplacer', true)) {
                    return storeTheme(Object.assign({}, options, {overwrite: true}));
                }
                return;
            }
            setStatus(error.message, 'danger');
        }
    }

    async function saveTheme(name, description, overwrite) {
        return storeTheme({
            name: name,
            description: description,
            overwrite: overwrite,
            payload: payload,
            conflictMessage: 'Un thème porte déjà ce nom : le remplacer par le brouillon courant ?',
        });
    }

    function slugifyThemeName(name) {
        const slug = String(name || '')
            .normalize('NFD').replace(/[̀-ͯ]/g, '')
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, '-')
            .replace(/^-+|-+$/g, '');
        return slug || 'theme';
    }

    function exportTheme(theme) {
        // L'instantané « complété » servi par GET /themes est exporté tel
        // quel : le fichier contient déjà les réglages apparus après
        // l'enregistrement du thème.
        const data = {
            format: 'gf-page-theme',
            page: page,
            name: theme.name,
            schema_version: (theme.snapshot || {}).schema_version || null,
            exported_at: new Date().toISOString(),
            snapshot: theme.snapshot || {},
        };
        const blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = 'theme-' + page + '-' + slugifyThemeName(theme.name) + '.json';
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
        setStatus('Thème « ' + theme.name + ' » exporté.', 'success');
    }

    // Nom du thème importé : pré-rempli par data.name ou le nom de fichier
    // via un dialogue dédié (jamais de boîte native) — redemandé tant que
    // le nom reste vide.
    async function confirmThemeImportName(suggestedName) {
        const body = document.createElement('div');
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'form-control';
        input.id = 'editor-theme-import-name';
        input.maxLength = 80;
        input.value = suggestedName;
        input.addEventListener('input', function () {
            input.classList.remove('is-invalid');
        });
        const label = document.createElement('label');
        label.className = 'form-label';
        label.htmlFor = input.id;
        label.textContent = 'Nom du thème importé';
        const hint = document.createElement('div');
        hint.className = 'form-text';
        hint.textContent = 'Le thème sera enregistré dans « Mes thèmes ».';
        body.append(label, input, hint);
        for (;;) {
            const choice = await openDialog({
                title: 'Importer un thème',
                body: body,
                actions: [
                    {label: 'Annuler', className: 'btn btn-outline-secondary', value: null},
                    {label: 'Importer', className: 'btn btn-primary', value: true, autofocus: true},
                ],
            });
            if (!choice) return null;
            const name = input.value.trim();
            if (name) return name;
            input.classList.add('is-invalid');
        }
    }

    async function importThemeFile(file) {
        let text;
        try {
            text = await file.text();
        } catch (error) {
            setStatus('Lecture du fichier impossible : import annulé.', 'danger');
            return;
        }
        let data = null;
        try {
            data = JSON.parse(text);
        } catch (error) { /* l'objet reste null : signalé juste après */ }
        if (!data || typeof data !== 'object' || Array.isArray(data)) {
            setStatus('Fichier JSON illisible : import annulé.', 'danger');
            return;
        }
        if (data.page && data.page !== page) {
            setStatus('Ce thème appartient à la page « ' + data.page
                + ' » — import refusé sur « ' + page + ' ».', 'danger');
            return;
        }
        // Deux formes acceptées : l'enveloppe d'export (« snapshot ») ou
        // l'instantané brut portant layout/config/css à la racine.
        const snapshot = (data.snapshot && typeof data.snapshot === 'object')
            ? data.snapshot
            : data;
        if (typeof snapshot.layout !== 'object' && typeof snapshot.config !== 'object'
            && typeof snapshot.css !== 'object') {
            setStatus('Ce fichier ne contient pas d’instantané de thème exploitable.', 'danger');
            return;
        }
        const suggested = (typeof data.name === 'string' ? data.name.trim() : '')
            || file.name.replace(/\.[^.]*$/, '')
            || 'Thème importé';
        const name = await confirmThemeImportName(suggested.slice(0, 80));
        if (name === null) return;
        // Un instantané brut ou ancien peut manquer de clés : l'enveloppe
        // est reconstruite ici et les sections sont complétées côté serveur
        // (_complete_payload), comme pour les thèmes anciens en base.
        await storeTheme({
            name: name,
            payload: {
                schema_version: payload.schema_version,
                page: page,
                base_hash: payload.base_hash,
                layout: snapshot.layout || {},
                config: snapshot.config || {},
                css: snapshot.css || {},
            },
            successMessage: 'Thème « ' + name + ' » importé.',
            conflictMessage: 'Un thème porte déjà ce nom : le remplacer par le thème importé ?',
        });
    }

    async function deleteTheme(theme) {
        if (!await confirmDialog('Supprimer le thème',
            'Supprimer le thème « ' + theme.name + ' » ?', 'Supprimer', true)) return;
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

    async function applyTheme(theme, sections) {
        sections = Object.assign({}, sections, {content: !theme.builtin && sections.content});
        if (!sections.appearance && !sections.layout && !sections.content) {
            setStatus('Cochez au moins une section à appliquer.', 'warning');
            return;
        }
        if ((dirty || sections.content) && !await confirmDialog(
            'Appliquer le thème',
            'Appliquer « ' + theme.name + ' » remplacera les sections sélectionnées du travail courant.'
            + (sections.content ? ' Les textes seront également remplacés.' : ' Vos textes sont conservés.')
            + ' Vous pourrez annuler cette action.',
            'Appliquer'
        )) return;
        const applied = [];
        mutate(function () {
            if (sections.appearance) {
                applyKnownKeys(payload.css, theme.snapshot.css);
                applied.push('apparence');
            }
            if (sections.layout) {
                // Jamais de remplacement en bloc : un composant absent du
                // thème (ajouté après son enregistrement) garde sa valeur
                // courante, sinon le layout sauvegardé serait incomplet.
                const snapshotLayout = theme.snapshot.layout || {};
                Object.keys(payload.layout).forEach(function (componentId) {
                    if (snapshotLayout[componentId]) {
                        applyKnownKeys(payload.layout[componentId], snapshotLayout[componentId]);
                    }
                });
                applied.push('disposition');
            }
            if (sections.content) {
                applyKnownKeys(payload.config, theme.snapshot.config);
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

    function buildExternalChangesList(changes) {
        const fragment = document.createElement('div');
        const intro = document.createElement('p');
        intro.textContent = 'Ces réglages ont été modifiés depuis la création du brouillon :';
        const list = document.createElement('ul');
        list.className = 'page-editor-diff-list';
        changes.forEach(function (change) {
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
        fragment.append(intro, list);
        return fragment;
    }

    // Conflit de publication : la configuration a été modifiée hors de
    // l'éditeur depuis la création du brouillon. Propose de rebaser le
    // brouillon sur la configuration actuelle (les valeurs externes sont
    // reprises pour les clés non modifiées) ou de publier malgré tout.
    async function showBaseConflictDialog(error) {
        const body = document.createElement('div');
        if (error.data && Array.isArray(error.data.external_changes)) {
            body.appendChild(buildExternalChangesList(error.data.external_changes));
        } else {
            // Référence du brouillon inconnue : on montre le diff
            // brouillon↔publié à la place du détail des changements externes.
            try {
                body.appendChild(buildDiffList(await fetchDiff()));
            } catch (diffError) {
                setStatus(diffError.message, 'danger');
            }
            const note = document.createElement('p');
            note.className = 'small text-muted mt-3 mb-0';
            note.textContent = 'Impossible de détailler les changements externes : la référence du brouillon est trop ancienne.';
            body.appendChild(note);
        }
        const choice = await openDialog({
            title: 'La configuration a changé hors de l’éditeur',
            body: body,
            actions: [
                {label: 'Fermer', className: 'btn btn-outline-secondary', value: null},
                {label: 'Publier quand même', className: 'btn btn-warning', value: 'force'},
                {label: 'Rebaser le brouillon', className: 'btn btn-primary', value: 'rebase', autofocus: true},
            ],
        });
        if (choice === 'rebase') {
            try {
                await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/draft/rebase', {
                    method: 'POST',
                    body: JSON.stringify({draft_version: draftVersion})
                });
                await loadState('Base réalignée : les réglages externes ont été repris, les vôtres conservés.');
            } catch (rebaseError) {
                setStatus(rebaseError.message, rebaseError.status === 409 ? 'warning' : 'danger');
            }
        } else if (choice === 'force') {
            await publish({force: true});
        }
    }

    // Après une publication qui a modifié des textes sources traduisibles :
    // rappel non bloquant que leurs traductions sont à revoir — le dialogue
    // s'affiche par-dessus le statut de publication, sans le remettre en
    // cause. « Ouvrir les traductions » mène à l'administration dédiée.
    async function showTranslationReviewDialog(review) {
        const body = document.createElement('div');
        const intro = document.createElement('p');
        intro.textContent = review.length
            + ' texte(s) traduisible(s) modifié(s) — vérifiez leurs traductions.';
        const list = document.createElement('ul');
        list.className = 'page-editor-diff-list';
        review.forEach(function (item) {
            const entry = document.createElement('li');
            entry.textContent = item.label;
            list.appendChild(entry);
        });
        body.append(intro, list);
        const choice = await openDialog({
            title: 'Traductions à revoir',
            body: body,
            actions: [
                {label: 'Fermer', className: 'btn btn-outline-secondary', value: null},
                {label: 'Ouvrir les traductions', className: 'btn btn-primary', value: 'translations', autofocus: true},
            ],
        });
        if (choice === 'translations') {
            window.location.assign('/admin/translations');
        }
    }

    async function publish(opts) {
        const options = opts || {};
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
            // La publication forcée suit le dialogue de conflit : l'utilisateur
            // vient de voir les changements, pas de second récapitulatif.
            const approved = options.force ? true : await confirmPublish();
            if (!approved) {
                publishButton.disabled = false;
                return;
            }
            const result = await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/publish', {
                method: 'POST',
                body: JSON.stringify({draft_version: draftVersion, force: Boolean(options.force)})
            });
            clearBackup();
            await loadState('Révision ' + result.revision + ' publiée. Les écrans en service ne sont pas rechargés automatiquement.');
            applyButton.disabled = false;
            setStatus('Publication terminée. Utilisez « Appliquer/recharger les écrans » au moment opportun.', 'success');
            if (result.translation_review && result.translation_review.length) {
                showTranslationReviewDialog(result.translation_review);
            }
        } catch (error) {
            publishButton.disabled = false;
            if (error.status === 409 && error.code === 'base_conflict') {
                await showBaseConflictDialog(error);
                return;
            }
            setStatus(error.message, error.status === 409 ? 'warning' : 'danger');
        }
    }

    async function discard() {
        const question = hasDraft
            ? 'Supprimer le brouillon partagé et revenir à la version publiée ?'
            : 'Abandonner les modifications non enregistrées ?';
        if (!await confirmDialog('Annuler les changements', question, 'Abandonner', true)) return;
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
        if (!await confirmDialog('Restaurer une révision', warning, 'Restaurer', hasDraft || dirty)) return;
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
        scheduleAutosave();
        persistBackup();
        rerenderAll(); pushPayloadToPreview(); updateButtons();
        setStatus('Modification annulée.', 'warning');
    });
    redoButton.addEventListener('click', function () {
        if (!redoStack.length) return;
        undoStack.push(clone(payload));
        payload = redoStack.pop();
        dirty = true;
        scheduleAutosave();
        persistBackup();
        rerenderAll(); pushPayloadToPreview(); updateButtons();
        setStatus('Modification rétablie.', 'warning');
    });
    saveButton.addEventListener('click', function () {
        saveDraft().catch(function (error) { setStatus(error.message, error.status === 409 ? 'warning' : 'danger'); });
    });
    // Appel sans argument : l'événement ne doit pas être lu comme ``opts``.
    publishButton.addEventListener('click', function () { publish(); });
    diffButton.addEventListener('click', showDiff);
    if (screensButton) screensButton.addEventListener('click', showScreens);
    if (themesButton) themesButton.addEventListener('click', showThemes);
    discardButton.addEventListener('click', discard);
    applyButton.addEventListener('click', async function () {
        if (!await confirmDialog('Appliquer/recharger les écrans',
            'Recharger maintenant les écrans connectés en service ?', 'Recharger')) return;
        try {
            await requestJSON('/admin/page-editor/' + encodeURIComponent(page) + '/apply', {method: 'POST', body: '{}'});
            appliedRevision = publishedRevision;
            updateButtons();
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
    actionsToggle.addEventListener('click', function () {
        const open = root.dataset.actionsOpen !== 'true';
        root.dataset.actionsOpen = open ? 'true' : 'false';
        syncActionsPresentation();
        window.requestAnimationFrame(syncStickyOffset);
    });
    compactActionsMedia.addEventListener('change', function () {
        syncActionsPresentation();
        window.requestAnimationFrame(syncStickyOffset);
    });
    modeButtons.forEach(function (button, index) {
        button.addEventListener('click', function () {
            setEditorMode(button.dataset.editorMode, {focus: true});
        });
        button.addEventListener('keydown', function (event) {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            let targetIndex = index;
            if (event.key === 'ArrowLeft') targetIndex = (index - 1 + modeButtons.length) % modeButtons.length;
            if (event.key === 'ArrowRight') targetIndex = (index + 1) % modeButtons.length;
            if (event.key === 'Home') targetIndex = 0;
            if (event.key === 'End') targetIndex = modeButtons.length - 1;
            setEditorMode(modeButtons[targetIndex].dataset.editorMode, {focus: true});
        });
    });
    simulationReset.addEventListener('click', function () { resetSimulation(); });
    inspectorToggle.addEventListener('click', function () { openInspectorDrawer(inspectorToggle); });
    inspectorClose.addEventListener('click', function () { closeInspectorDrawer(true); });
    inspectorBackdrop.addEventListener('click', function () { closeInspectorDrawer(true); });
    compactInspectorMedia.addEventListener('change', function () { closeInspectorDrawer(false); });
    scenarioSelect.addEventListener('change', function () {
        const match = journeyAllSteps().find(function (step) { return step.scenario === scenarioSelect.value; });
        if (journey && editorMode === 'journey') {
            selectedStep = match ? match.id : null;
            journeyContextStep = selectedStep;
        } else if (journey && match) {
            journeyContextStep = match.id;
            if (editorMode === 'design' && !componentBelongsToStep(selectedComponent, match)) {
                selectedComponent = Object.keys(adapter.components).find(function (componentId) {
                    return componentBelongsToStep(componentId, match);
                }) || Object.keys(adapter.components)[0];
            }
        }
        rerenderAll();
        loadPreview();
    });
    languageSelect.addEventListener('change', loadPreview);
    viewportSelect.addEventListener('change', resizePreview);
    if (zoomSelect) zoomSelect.addEventListener('change', resizePreview);
    preview.addEventListener('load', function () { resizePreview(); pushPayloadToPreview(); });
    window.addEventListener('resize', resizePreview);
    window.addEventListener('message', function (event) {
        if (event.origin !== window.location.origin || !event.data) return;
        if (event.data.type === 'page-editor:ready') pushPayloadToPreview();
        if (event.data.type === 'page-editor:selected' && adapter.components[event.data.componentId]) selectComponent(event.data.componentId);
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && root.dataset.inspectorOpen === 'true') {
            event.preventDefault();
            closeInspectorDrawer(true);
            return;
        }
        if (event.key === 'Tab' && root.dataset.inspectorOpen === 'true') {
            const focusable = Array.from(inspectorPanel.querySelectorAll(
                'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), '
                + 'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
            )).filter(function (element) {
                return !element.hidden && element.getAttribute('aria-hidden') !== 'true';
            });
            if (!focusable.length) {
                event.preventDefault();
                return;
            }
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        }
    });
    window.addEventListener('beforeunload', function (event) {
        if (!dirty && !saving) return;
        event.preventDefault();
        event.returnValue = '';
    });

    // Ctrl+Z / Ctrl+Maj+Z / Ctrl+Y hors champs de saisie (dans un champ, le
    // navigateur applique déjà son propre undo sur le texte). Ctrl+S est
    // traité partout, champs compris : contrairement à l'undo texte, il n'a
    // pas de comportement natif utile à préserver — le raccourci enregistre
    // le brouillon sans quitter la saisie. Sans modification, il est un
    // no-op : un envoi re-datant le brouillon brouillerait l'identité de
    // son dernier auteur (voir ``draftStatusText``).
    document.addEventListener('keydown', function (event) {
        if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
        const key = event.key.toLowerCase();
        if (key === 's') {
            event.preventDefault();
            if (dirty) {
                saveDraft().catch(function (error) {
                    setStatus(error.message, error.status === 409 ? 'warning' : 'danger');
                });
            }
            return;
        }
        const target = event.target;
        if (target && (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT'
            || target.tagName === 'SELECT' || target.isContentEditable)) return;
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
