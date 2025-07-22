// Wait for the DOM to be fully parsed, then start the process
document.addEventListener('DOMContentLoaded', () => {
    startProcess();
  });
  
  function startProcess() {
      // Disable the start button to prevent multiple clicks
      const startBtn = document.getElementById("start-btn");
      if (startBtn) startBtn.disabled = true;
      
      // Show the progress bar container
      const progressContainer = document.getElementById('progress-container');
      const progressBar = document.getElementById('progress-bar');
      progressContainer.style.display = 'block';
      progressBar.style.width = '0%';
      progressBar.innerText = '0%';
  
      // Start the container launch process
      let sessionId = null;
      fetch('/launch', { method: 'POST' })
          .then(response => response.json())
          .then(data => {
              console.log(data.message);
              sessionId = data.session_id;
              // Start polling for the Jupyter URL and simulate progress
              pollForUrl();
              simulateProgress();
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
          if (!sessionId) return;
          console.log('Polling for Jupyter URL...');
          fetch(`/get_url?session_id=${sessionId}`, { method: 'GET' })
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

