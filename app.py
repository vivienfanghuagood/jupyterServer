from flask import Flask, render_template, jsonify
from container_manager import start_container_and_get_jupyter_url
import threading

app = Flask(__name__)

# Store the URL in memory so we don't need to write a temporary file
jupyter_url = None
url_lock = threading.Lock()

def launch_container():
    global jupyter_url
    url = start_container_and_get_jupyter_url()
    if url:
        with url_lock:
            jupyter_url = url

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/launch', methods=['POST'])
def launch():
    global jupyter_url
    # Clear any previous URL before launching
    with url_lock:
        jupyter_url = None
    # Start the container in a background thread
    thread = threading.Thread(target=launch_container)
    thread.start()
    return jsonify({"message": "Container is launching, please wait..."})

@app.route('/get_url', methods=['GET'])
def get_url():
    with url_lock:
        url = jupyter_url
    return jsonify({"url": url})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
