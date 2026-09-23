/* Apercu dynamique du ticket d'impression dans l'administration.
 *
 * Le serveur renvoie un fragment HTML produit a partir du meme flux ESC/POS
 * que l'impression physique. On envoie aussi les textareas non enregistrees :
 * l'administrateur voit donc le resultat pendant sa saisie.
 */
(function () {
    "use strict";

    var DEBOUNCE_MS = 300;
    var watchedSelectors = [
        "#ticket_header",
        "#ticket_message",
        "#ticket_footer",
        "#printer_width",
        "#ticket_display_specific_message",
        "#call_number",
        "#activity",
        "#language"
    ];
    var timer = null;
    var controller = null;

    function fieldValue(id, fallback) {
        var element = document.getElementById(id);
        return element ? element.value : fallback;
    }

    function previewParameters() {
        var specific = document.getElementById("ticket_display_specific_message");
        return new URLSearchParams({
            ticket_header: fieldValue("ticket_header", ""),
            ticket_message: fieldValue("ticket_message", ""),
            ticket_footer: fieldValue("ticket_footer", ""),
            printer_width: fieldValue("printer_width", "42"),
            display_specific_message: specific && specific.checked ? "true" : "false",
            call_number: fieldValue("call_number", "A-1"),
            activity: fieldValue("activity", ""),
            language: fieldValue("language", "fr")
        });
    }

    function showClientError(result, message) {
        result.replaceChildren();
        var alert = document.createElement("div");
        alert.className = "alert alert-danger mb-0";
        alert.setAttribute("role", "alert");
        alert.textContent = message;
        result.appendChild(alert);
    }

    async function refreshPreview() {
        var root = document.querySelector("[data-ticket-preview-root]");
        var result = document.getElementById("ticket_preview_result");
        var loading = document.getElementById("ticket_preview_loading");
        if (!root || !result) { return; }

        if (controller) { controller.abort(); }
        var requestController = new AbortController();
        controller = requestController;
        result.setAttribute("aria-busy", "true");
        if (loading) { loading.hidden = false; }

        try {
            var response = await fetch("/admin/patient/ticket_preview", {
                method: "POST",
                credentials: "same-origin",
                headers: {"Accept": "text/html"},
                body: previewParameters(),
                signal: requestController.signal
            });
            if (!response.ok) {
                throw new Error("HTTP " + response.status);
            }
            result.innerHTML = await response.text();
        } catch (error) {
            if (error.name !== "AbortError") {
                showClientError(result,
                    "Impossible de mettre à jour l'aperçu. Vérifiez la connexion puis réessayez.");
            }
        } finally {
            if (controller === requestController) {
                result.setAttribute("aria-busy", "false");
                if (loading) { loading.hidden = true; }
            }
        }
    }

    function schedulePreview(immediate) {
        window.clearTimeout(timer);
        timer = window.setTimeout(refreshPreview, immediate ? 0 : DEBOUNCE_MS);
    }

    document.addEventListener("DOMContentLoaded", function () {
        if (!document.querySelector("[data-ticket-preview-root]")) { return; }

        var selector = watchedSelectors.join(",");
        document.addEventListener("input", function (event) {
            if (event.target.matches(selector)) { schedulePreview(false); }
        });
        document.addEventListener("change", function (event) {
            if (event.target.matches(selector)) { schedulePreview(true); }
        });

        var refreshButton = document.getElementById("ticket_preview_refresh");
        if (refreshButton) {
            refreshButton.addEventListener("click", function () {
                schedulePreview(true);
            });
        }
        schedulePreview(true);
    });
})();
