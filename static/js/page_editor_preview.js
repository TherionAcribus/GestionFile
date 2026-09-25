(function () {
    'use strict';

    const tokensNode = document.getElementById('page-editor-preview-tokens');
    const tokenValues = tokensNode ? JSON.parse(tokensNode.textContent) : {};
    const hiddenNode = document.getElementById('page-editor-initial-hidden');
    const initialHidden = hiddenNode ? JSON.parse(hiddenNode.textContent) : [];
    let markdownTimer = null;
    let markdownRequest = 0;

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    function demoText(value, element) {
        let result = String(value == null ? '' : value);
        const values = Object.assign({}, tokenValues);
        if (element?.dataset.previewNumber) values['{N}'] = element.dataset.previewNumber;
        Object.entries(values).forEach(function (entry) {
            result = result.split(entry[0]).join(entry[1]);
        });
        return result;
    }

    // Variante HTML : {N} devient un élément mis en valeur (.print_status_number),
    // comme sur la borne où patients.js l'entoure d'un span dédié. Insensible
    // à la casse, comme fillCallNumber/render_balises.
    function demoHtml(value, element) {
        let result = escapeHtml(value == null ? '' : value);
        const values = Object.assign({}, tokenValues);
        if (element?.dataset.previewNumber) values['{N}'] = element.dataset.previewNumber;
        Object.entries(values).forEach(function (entry) {
            const replacement = entry[0] === '{N}'
                ? '<span class="print_status_number">' + escapeHtml(entry[1]) + '</span>'
                : escapeHtml(entry[1]);
            const pattern = new RegExp(
                entry[0].replace(/[{}]/g, function (c) { return '\\' + c; }), 'gi');
            result = result.replace(pattern, replacement);
        });
        return result;
    }

    function applyPayload(payload) {
        if (!payload || typeof payload !== 'object') return;
        Object.entries(payload.css || {}).forEach(function (entry) {
            document.documentElement.style.setProperty('--' + entry[0], entry[1]);
        });
        Object.entries(payload.config || {}).forEach(function (entry) {
            if (document.body.dataset.page === 'phone' && (entry[0].startsWith('phone_line') || entry[0].startsWith('phone_your_turn_line'))) return;
            document.querySelectorAll('[data-config-key="' + CSS.escape(entry[0]) + '"]').forEach(function (element) {
                if (element.hasAttribute('data-number-styled')) {
                    element.innerHTML = demoHtml(entry[1], element);
                } else {
                    element.textContent = demoText(entry[1], element);
                }
            });
        });
        if (document.body.dataset.page === 'phone') {
            renderPhoneMarkdown(payload);
            const infos = document.getElementById('div_infos');
            if (infos) infos.classList.toggle('text-center', Boolean(payload.config.phone_center));
        }
        // Les règles de masquage liées à la configuration se cumulent :
        // l'élément reste masqué dès qu'une règle l'exige.
        const hiddenByConfig = new Map();
        const markHidden = function (element, hidden) {
            hiddenByConfig.set(element, Boolean(hiddenByConfig.get(element)) || hidden);
        };
        document.querySelectorAll('[data-config-bool]').forEach(function (element) {
            markHidden(element, !payload.config[element.dataset.configBool]);
        });
        document.querySelectorAll('[data-config-hide-values]').forEach(function (element) {
            let values = [];
            try { values = JSON.parse(element.dataset.configHideValues); } catch (error) { values = []; }
            markHidden(element, values.includes(payload.config[element.dataset.configDisplayKey]));
        });
        document.querySelectorAll('[data-hide-empty]').forEach(function (element) {
            const value = payload.config[element.dataset.configKey];
            markHidden(element, !String(value == null ? '' : value).trim());
        });
        // Écran « Erreur d'impression » : le numéro en grand n'est ajouté que
        // si le texte associé n'a pas de balise {N} — même règle que la borne.
        document.querySelectorAll('[data-print-error-number-for]').forEach(function (element) {
            const message = String(payload.config[element.dataset.printErrorNumberFor] || '');
            markHidden(element, /\{n\}/i.test(message));
        });
        hiddenByConfig.forEach(function (hidden, element) {
            element.toggleAttribute('data-config-hidden', hidden);
        });
        if (document.body.dataset.page === 'announce') {
            const center = document.getElementById('pe_center');
            if (center) {
                const divided = Boolean(payload.config.announce_infos_display)
                    && payload.layout?.gallery?.visible !== false && center.dataset.scenario === 'gallery';
                center.classList.toggle('pe-grid-2', divided);
                center.classList.toggle('pe-grid-1', !divided);
            }
            const nextWrapper = document.querySelector('.next_patients_wrapper');
            if (nextWrapper && payload.config.announce_next_patients_alignment) {
                nextWrapper.classList.toggle('align-center', payload.config.announce_next_patients_alignment === 'center');
                nextWrapper.classList.toggle('align-right', payload.config.announce_next_patients_alignment === 'right');
            }
        }
        Object.entries(payload.layout || {}).forEach(function (entry) {
            document.querySelectorAll('[data-page-editor-component="' + CSS.escape(entry[0]) + '"]').forEach(function (element) {
                const item = entry[1];
                element.toggleAttribute('data-page-editor-hidden', !item.visible);
                element.style.order = String(item.order);
                element.style.width = ((item.span / 12) * 100).toFixed(4) + '%';
                element.style.alignSelf = item.alignment === 'left' ? 'flex-start' : (item.alignment === 'right' ? 'flex-end' : item.alignment);
                element.style.textAlign = item.alignment === 'stretch' ? 'center' : item.alignment;
                element.style.boxSizing = 'border-box';
            });
        });
    }

    function renderPhoneMarkdown(payload) {
        window.clearTimeout(markdownTimer);
        markdownTimer = window.setTimeout(async function () {
            const requestId = ++markdownRequest;
            try {
                const response = await fetch('/admin/page-editor/phone/preview/render', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({payload: payload})
                });
                if (!response.ok) return;
                const data = await response.json();
                if (requestId !== markdownRequest) return;
                Object.entries(data.html || {}).forEach(function (entry) {
                    document.querySelectorAll('[data-config-key="' + CSS.escape(entry[0]) + '"]').forEach(function (element) {
                        element.innerHTML = entry[1];
                    });
                });
            } catch (error) {
                return;
            }
        }, 120);
    }

    window.addEventListener('message', function (event) {
        if (event.origin !== window.location.origin || !event.data) return;
        if (event.data.type === 'page-editor:update') applyPayload(event.data.payload);
        if (event.data.type === 'page-editor:select') {
            document.querySelectorAll('[data-page-editor-selected]').forEach(function (element) {
                element.removeAttribute('data-page-editor-selected');
            });
            const selected = document.querySelector('[data-page-editor-component="' + CSS.escape(event.data.componentId || '') + '"]');
            if (selected) selected.setAttribute('data-page-editor-selected', 'true');
        }
    });

    document.addEventListener('click', function (event) {
        const component = event.target.closest('[data-page-editor-component]');
        if (!component) return;
        window.parent.postMessage({
            type: 'page-editor:selected',
            componentId: component.dataset.pageEditorComponent
        }, window.location.origin);
    });

    // Applique l'état masqué du chargement avant le premier postMessage du
    // parent (la feuille générée côté serveur n'émet plus de règle
    // ``display:none`` figée en aperçu : tout passe par cet attribut).
    initialHidden.forEach(function (componentId) {
        document.querySelectorAll('[data-page-editor-component="' + CSS.escape(componentId) + '"]').forEach(function (element) {
            element.setAttribute('data-page-editor-hidden', 'true');
        });
    });

    window.parent.postMessage({type: 'page-editor:ready'}, window.location.origin);
}());
