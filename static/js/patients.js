var eventSource = new EventSource('/events/update_page_patient');

eventSource.onmessage = function(event) {
    console.log(event.data);
    htmx.trigger('#div_buttons_parents', 'refresh_buttons', {target: "#div_buttons_parents"});
};


