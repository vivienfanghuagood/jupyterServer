from flask import Flask, render_template, jsonify
from container_manager import start_pod_and_get_jupyter_url
import threading
import json
import os
from urllib.parse import urlparse

app = Flask(__name__)

URL_FILE = "jupyter_url.json"

def save_url_to_file(url):
    with open(URL_FILE, "w") as f:
        json.dump({"url": url}, f)

def load_url_from_file():
    if os.path.exists(URL_FILE):
        with open(URL_FILE, "r") as f:
            data = json.load(f)
            return data.get("url")
    return None

def launch_container():
    jupyter_url = start_pod_and_get_jupyter_url()
    if jupyter_url:
        save_url_to_file(jupyter_url)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/launch', methods=['POST'])
def launch():
    # Remove any previous URL before launching
    if os.path.exists(URL_FILE):
        os.remove(URL_FILE)
    # Start the container in a background thread
    thread = threading.Thread(target=launch_container)
    thread.start()
    return jsonify({"message": "Pod is launching, please wait..."})

@app.route('/get_url', methods=['GET'])
def get_url():
    url = load_url_from_file()

    return jsonify({
        "url": url,
        
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
