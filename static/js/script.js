function startProcess() {
    fetch('/launch', { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            if (data.url) {
                window.location.href = data.url;
            } else {
                alert("Error: " + (data.error || "Failed to start Jupyter Notebook."));
            }
        })
        .catch(error => console.error('Error:', error));
}

// function startProcess() {
//     fetch('/launch', { method: 'POST' })
//         .then(response => response.json())
//         .then(data => {
//             console.log(data.message);
//             console.log(data.url)
//             checkForUrl();  // Start polling for the URL
//         })
//         .catch(error => console.error('Error:', error));
// }

// function checkForUrl() {
//     let interval = setInterval(() => {
//         fetch('/get_url', { method: 'GET' })
//             .then(response => response.json())
//             .then(data => {
//                 if (data.url) {
//                     clearInterval(interval);  // Stop polling
//                     window.location.href = data.url;  // Redirect
//                 }
//             })
//             .catch(error => console.error('Error:', error));
//     }, 5000);  // Poll every 5 seconds
// }
