// Onglet Application > Général : aide au réglage « Adresse du serveur sur le
// réseau » (clé de config network_adress) — boutons « Détecter » et
// « Tester avec un QR code ».
//
// Vanilla JS dans un fichier dédié (defer) : la CSP interdit les attributs
// onclick inline (script-src 'self'). Les deux endpoints sont en GET, donc
// csrf.js n'y injecte pas de jeton (réservé aux méthodes mutantes).
(function () {
    "use strict";

    var input = document.getElementById("network_adress");
    var detectBtn = document.getElementById("network-adress-detect");
    var testBtn = document.getElementById("network-adress-test");
    var result = document.getElementById("network-adress-test-result");
    if (!input || !detectBtn || !testBtn || !result) {
        return; // autre page : les ancres n'existent pas
    }

    function showMessage(text, cls) {
        result.textContent = text;
        result.className = "mt-2 small" + (cls ? " " + cls : "");
    }

    detectBtn.addEventListener("click", function () {
        showMessage("Détection en cours…");
        fetch("/admin/app/network_adress/suggest")
            .then(function (resp) {
                if (!resp.ok) {
                    throw new Error("suggest: HTTP " + resp.status);
                }
                return resp.json();
            })
            .then(function (data) {
                input.value = data.url;
                // admin_macros.js réactive le bouton « Enregistrer » du champ
                // sur input quand la valeur diffère de data-initial-value :
                // sans ce dispatch, la détection semblerait non enregistrable.
                input.dispatchEvent(new Event("input", { bubbles: true }));
                showMessage("Adresse détectée : " + data.url
                    + " — pensez à Enregistrer.", "text-success");
            })
            .catch(function () {
                showMessage("Détection impossible : saisissez l'adresse "
                    + "manuellement ou laissez vide.", "text-danger");
            });
    });

    testBtn.addEventListener("click", function () {
        var url = input.value.trim();
        if (!url) {
            showMessage("Renseignez d'abord une adresse (ou Détecter).",
                "text-danger");
            return;
        }
        result.textContent = "";
        result.className = "mt-2";
        var img = document.createElement("img");
        img.src = "/admin/app/network_adress/qrcode.png?url="
            + encodeURIComponent(url);
        img.alt = "QR code de test vers " + url;
        img.className = "d-block border rounded bg-white p-1";
        img.width = 160;
        img.height = 160;
        img.onerror = function () {
            showMessage("URL invalide.", "text-danger");
        };
        result.appendChild(img);
        var help = document.createElement("p");
        help.className = "small text-muted mt-1 mb-0";
        help.textContent = "Scannez ce code avec un téléphone connecté au même "
            + "Wi-Fi : si la page s'ouvre, l'adresse est bonne.";
        result.appendChild(help);
    });
})();
