(function () {
    'use strict';

    const tokensNode = document.getElementById('page-editor-preview-tokens');
    const tokenValues = tokensNode ? JSON.parse(tokensNode.textContent) : {};
    const hiddenNode = document.getElementById('page-editor-initial-hidden');
    const initialHidden = hiddenNode ? JSON.parse(hiddenNode.textContent) : [];
    let markdownTimer = null;
    let markdownRequest = 0;

    function demoText(value, element) {
        let result = String(value == null ? '' : value);
        const values = Object.assign({}, tokenValues);
        if (element?.dataset.previewNumber) values['{N}'] = element.dataset.previewNumber;
        Object.entries(values).forEach(function (entry) {
            result = result.split(entry[0]).join(entry[1]);
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
                element.textContent = demoText(entry[1], element);
            });
        });
        if (document.body.dataset.page === 'phone') renderPhoneMarkdown(payload);
        document.querySelectorAll('[data-config-bool]').forEach(function (element) {
            element.toggleAttribute('data-config-hidden', !payload.config[element.dataset.configBool]);
        });
        document.querySelectorAll('[data-hide-empty]').forEach(function (element) {
            const value = payload.config[element.dataset.configKey];
            element.toggleAttribute('data-config-hidden', !String(value == null ? '' : value).trim());
        });
        if (document.body.dataset.page === 'announce') {
            const center = document.getElementById('pe_center');
            if (center) {
                const divided = Boolean(payload.config.announce_infos_display) && center.dataset.scenario === 'gallery';
                center.classList.toggle('pe-grid-2', divided);
                center.classList.toggle('pe-grid-1', !divided);
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
