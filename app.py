from flask import Flask, render_template, jsonify
from container_manager import start_pod_and_get_jupyter_url
import threading


app = Flask(__name__)

# Holds the most recently launched Jupyter URL
jupyter_url = None

def launch_container():
    global jupyter_url
    jupyter_url = start_pod_and_get_jupyter_url()


@app.route('/no_gpu')
def no_gpu():
    """Inform the user that no GPUs are currently available."""
    return render_template('gpu_unavailable.html')

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/launch', methods=['POST'])
def launch():
    global jupyter_url
    # Reset any previous URL before launching
    jupyter_url = None
    # Start the container in a background thread
    thread = threading.Thread(target=launch_container)
    thread.start()
    return jsonify({"message": "Pod is launching, please wait..."})

@app.route('/get_url', methods=['GET'])
def get_url():
    return jsonify({
        "url": jupyter_url,

    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
