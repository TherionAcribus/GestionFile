var socket = io.connect();


socket.on('trigger_valide_activity', function(data) {
    console.log(data.activity);
});


function printDiv(divId) {
    var content = document.getElementById(divId).innerHTML;
    var originalContent = document.body.innerHTML;
    document.body.innerHTML = content;
    window.print();
    document.body.innerHTML = originalContent;
}


