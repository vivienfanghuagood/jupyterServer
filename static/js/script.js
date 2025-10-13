// Wait for the DOM to be fully parsed, then set up the form handler
document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('request-btn');
    if (btn) btn.addEventListener('click', startProcess);
});

function startProcess() {
    const emailInput = document.getElementById('email-input');
    if (!emailInput || !emailInput.value) return;
    const email = emailInput.value;

    // Disable the request button to prevent multiple clicks
    const requestBtn = document.getElementById('request-btn');
    if (requestBtn) requestBtn.disabled = true;

    // Show the progress bar container
    const progressContainer = document.getElementById('progress-container');
    const progressBar = document.getElementById('progress-bar');
    progressContainer.style.display = 'block';
    progressBar.style.width = '0%';
    progressBar.innerText = '0%';

    // Start the container launch process
    let userEmail = email;
    fetch('/launch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email })
    })
        .then(response => response.json())
        .then(data => {
            if (data.url) {
                window.location.href = data.url;
            } else {
                console.log(data.message);
                // Start polling for the Jupyter URL and simulate progress
                pollForUrl();
                simulateProgress();
            }
        })
        .catch(error => console.error('Error:', error));

    // Simulated progress (increases up to 90%)
    let progress = 0;
    let progressInterval;
    function simulateProgress() {
        progressInterval = setInterval(() => {
            if (progress < 90) {
                progress += 2; // Increment progress by 2%
                progressBar.style.width = progress + '%';
                progressBar.innerText = progress + '%';
            }
        }, 200);
    }

    // Poll for the Jupyter URL every 2000ms
    function pollForUrl() {
        if (!userEmail) return;
        console.log('Polling for Jupyter URL...');
        fetch(`/get_url?email=${encodeURIComponent(userEmail)}`, { method: 'GET' })
            .then(response => response.json())
            .then(data => {
                if (data.url) {
                    clearInterval(progressInterval);
                    progressBar.style.width = '100%';
                    progressBar.innerText = '100%';
                    // Wait briefly so the user sees the full progress bar before redirecting
                    setTimeout(() => {
                        window.location.href = data.url;
                    }, 500);
                } else {
                    setTimeout(pollForUrl, 2000);
                }
            })
            .catch(error => console.error('Error:', error));
    }
}
