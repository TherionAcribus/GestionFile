// Onglet Application > E-mails : préréglages des messageries (mail_settings.py).
//
// - l'adresse d'envoi saisie devine la messagerie (domaine connu) ;
// - choisir une messagerie remplit serveur, port et sécurité, et affiche son
//   conseil (ex. mot de passe d'application Gmail) ;
// - l'identifiant vide reprend l'adresse d'envoi ;
// - changer la sécurité ajuste le port s'il était un port standard.
//
// Fichier dédié (defer) : la CSP interdit le JavaScript inline.
(function () {
    "use strict";

    var form = document.getElementById("mail-settings-form");
    if (!form) {
        return; // autre onglet / autre page
    }
    var providers = [];
    try {
        providers = JSON.parse(form.getAttribute("data-providers") || "[]");
    } catch (e) {
        providers = [];
    }

    var sender = document.getElementById("mail_default_sender");
    var select = document.getElementById("mail_provider");
    var hint = document.getElementById("mail_provider_hint");
    var username = document.getElementById("mail_username");
    var host = document.getElementById("mail_server");
    var port = document.getElementById("mail_port");
    var security = document.getElementById("mail_security");
    var details = document.getElementById("mail_server_details");
    var DEFAULT_PORTS = { starttls: "587", ssl: "465", none: "25" };

    function byId(id) {
        for (var i = 0; i < providers.length; i++) {
            if (providers[i].id === id) {
                return providers[i];
            }
        }
        return null;
    }

    function byDomain(address) {
        var at = (address || "").lastIndexOf("@");
        if (at < 0) {
            return null;
        }
        var domain = address.slice(at + 1).trim().toLowerCase();
        for (var i = 0; i < providers.length; i++) {
            if (providers[i].domains.indexOf(domain) >= 0) {
                return providers[i];
            }
        }
        return null;
    }

    function showHint(provider) {
        hint.textContent = provider && provider.hint ? provider.hint : "";
    }

    function applyProvider(provider) {
        if (!provider) {
            return;
        }
        host.value = provider.host;
        port.value = String(provider.port);
        security.value = provider.security;
        showHint(provider);
    }

    select.addEventListener("change", function () {
        if (select.value === "other") {
            showHint(null);
            details.open = true;
            host.focus();
            return;
        }
        applyProvider(byId(select.value));
    });

    sender.addEventListener("change", function () {
        var guessed = byDomain(sender.value);
        // « Autre » = réglages saisis à la main : on ne les écrase pas.
        if (guessed && select.value !== "other" && select.value !== guessed.id) {
            select.value = guessed.id;
            applyProvider(guessed);
        }
        if (!username.value.trim()) {
            username.value = sender.value.trim();
        }
    });

    security.addEventListener("change", function () {
        var current = port.value.trim();
        var isStandard = current === "" || current === "587" || current === "465" || current === "25";
        if (isStandard) {
            port.value = DEFAULT_PORTS[security.value] || "";
        }
    });

    showHint(byId(select.value));
})();
