// Onglet « Google Voice » de la page Annonce : depot du fichier de cle par
// glisser-deposer. Le depot remplit le champ fichier puis soumet le
// formulaire — htmx intercepte l'evenement submit emis par requestSubmit()
// et envoie le POST comme une soumission classique.
//
// La zone, le champ et le formulaire sont rendus par le gabarit et ne sont
// jamais reinjectes par HTMX (hx-target vise #upload-result, enfant du
// formulaire) : les ecouteurs directs poses une fois suffisent.

(function () {
    var dropzone = document.getElementById('google-key-dropzone');
    var input = document.getElementById('googleKeyFile');
    var form = document.getElementById('google-key-form');
    var result = document.getElementById('upload-result');
    if (!dropzone || !input || !form || !result) return;

    function showError(message) {
        result.innerHTML = '<div class="alert alert-danger">' + message + '</div>';
    }

    ['dragenter', 'dragover'].forEach(function (name) {
        dropzone.addEventListener(name, function (evt) {
            evt.preventDefault();
            dropzone.classList.add('dragover');
        });
    });

    dropzone.addEventListener('dragleave', function (evt) {
        // dragleave se declenche aussi en survolant un enfant de la zone :
        // on ne retire le survol que si le curseur sort vraiment.
        if (dropzone.contains(evt.relatedTarget)) return;
        dropzone.classList.remove('dragover');
    });

    dropzone.addEventListener('drop', function (evt) {
        evt.preventDefault();
        dropzone.classList.remove('dragover');
        var file = evt.dataTransfer && evt.dataTransfer.files && evt.dataTransfer.files[0];
        if (!file) return;
        if (!/\.json$/i.test(file.name)) {
            showError('Seul un fichier .json est accepté.');
            return;
        }
        input.files = evt.dataTransfer.files;
        form.requestSubmit();
    });

    // Sans interception, un fichier depose hors de la zone s'ouvrirait dans
    // le navigateur a la place de la page d'administration.
    ['dragover', 'drop'].forEach(function (name) {
        document.addEventListener(name, function (evt) {
            evt.preventDefault();
        });
    });
})();
