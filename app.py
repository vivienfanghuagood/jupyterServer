from flask import Flask, render_template, jsonify
from container_manager import start_container_and_get_jupyter_url

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/launch', methods=['POST'])
def launch():
    jupyter_url = start_container_and_get_jupyter_url()
    if jupyter_url:
        # Return the URL to the frontend so it can redirect the user.
        return jsonify({"url": jupyter_url})
    else:
        return jsonify({"error": "Failed to start Jupyter Notebook."}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)


# from flask import Flask, render_template, redirect, jsonify
# from container_manager import start_container_and_print_jupyter_url
# import threading
# import time

# app = Flask(__name__)

# # Global variable to store the Jupyter URL
# jupyter_url = None

# def launch_container():
#     """Background function to start the container and set the global Jupyter URL."""
#     global jupyter_url
#     jupyter_url = start_container_and_print_jupyter_url()

# @app.route('/')
# def home():
#     return render_template('index.html')

# @app.route('/launch', methods=['POST'])
# def launch():
#     """Starts the container asynchronously and returns a response immediately."""
#     global jupyter_url
#     jupyter_url = None  # Reset the URL before launching

#     thread = threading.Thread(target=launch_container)
#     thread.start()

#     return jsonify({"message": "Container is launching, please wait..."})

# @app.route('/get_url', methods=['GET'])
# def get_url():
#     """Checks if the Jupyter URL is ready and returns it."""
#     global jupyter_url
#     if jupyter_url:
#         return jsonify({"url": jupyter_url})
#     return jsonify({"url": None})



# if __name__ == '__main__':
#     app.run(debug=True, host='0.0.0.0', port=5000)
