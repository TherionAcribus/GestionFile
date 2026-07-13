/*
 * Injection du jeton CSRF (Flask-WTF) dans toutes les requêtes mutatrices
 * du navigateur, quel que soit le mécanisme :
 *   - HTMX  (événement htmx:configRequest)
 *   - fetch (wrapper global window.fetch)
 *   - jQuery ($.ajaxSetup)
 *
 * Le jeton est lu depuis <meta name="csrf-token" content="...">, présent dans
 * les pages navigateur protégées (admin, comptoir). Les requêtes vers une autre
 * origine (ex. api.spotify.com) ne reçoivent JAMAIS le jeton.
 *
 * Côté serveur, seules les requêtes navigateur sont contrôlées : les clients
 * machine (App_Comptoir, borne) s'authentifient par jeton applicatif et sont
 * exemptés (cf. app.py : csrf_protect_browser_requests / _csrf_is_exempt).
 */
(function () {
    "use strict";

    var meta = document.querySelector('meta[name="csrf-token"]');
    var token = meta ? meta.getAttribute("content") : null;
    if (!token) {
        return; // page sans jeton (ex. page publique) : rien à injecter
    }

    var UNSAFE = /^(POST|PUT|PATCH|DELETE)$/i;

    function isSameOrigin(url) {
        if (!url) {
            return true; // requête sans URL explicite -> même page
        }
        if (/^https?:\/\//i.test(url) || /^\/\//.test(url)) {
            // URL absolue : même origine uniquement.
            return url.indexOf(window.location.origin) === 0;
        }
        return true; // URL relative -> même origine
    }

    // --- HTMX ---
    document.addEventListener("htmx:configRequest", function (evt) {
        evt.detail.headers["X-CSRFToken"] = token;
    });

    // --- fetch ---
    if (window.fetch) {
        var originalFetch = window.fetch;
        window.fetch = function (input, init) {
            init = init || {};
            var method = (init.method
                || (input && typeof input !== "string" && input.method)
                || "GET").toUpperCase();
            var url = (typeof input === "string") ? input : (input && input.url);
            if (UNSAFE.test(method) && isSameOrigin(url)) {
                var headers = new Headers(
                    init.headers
                    || (input && typeof input !== "string" && input.headers)
                    || {}
                );
                if (!headers.has("X-CSRFToken")) {
                    headers.set("X-CSRFToken", token);
                }
                init.headers = headers;
            }
            return originalFetch.call(this, input, init);
        };
    }

    // --- jQuery ---
    if (window.jQuery) {
        window.jQuery.ajaxSetup({
            beforeSend: function (xhr, settings) {
                if (UNSAFE.test(settings.type || "GET") && !settings.crossDomain) {
                    xhr.setRequestHeader("X-CSRFToken", token);
                }
            }
        });
    }
})();
