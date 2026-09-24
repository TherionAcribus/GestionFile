(function () {
    'use strict';

    const tokenValues = {
        '{P}': '042',
        '{D}': 'Pharmacie Démonstration',
        '{H}': '10:30',
        '{A}': 'Ordonnances',
        '{N}': '3',
        '{M}': 'Message de démonstration',
        '{C}': 'Comptoir 3'
    };
    let markdownTimer = null;
    let markdownRequest = 0;

    function demoText(value) {
        let result = String(value == null ? '' : value);
        Object.entries(tokenValues).forEach(function (entry) {
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
                element.textContent = demoText(entry[1]);
            });
        });
        if (document.body.dataset.page === 'phone') renderPhoneMarkdown(payload);
        Object.entries(payload.layout || {}).forEach(function (entry) {
            document.querySelectorAll('[data-page-editor-component="' + CSS.escape(entry[0]) + '"]').forEach(function (element) {
                const item = entry[1];
                element.style.display = item.visible ? '' : 'none';
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

    window.parent.postMessage({type: 'page-editor:ready'}, window.location.origin);
}());
