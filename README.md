# Jupyter Server Launcher

This repository contains a small Flask application that launches a Jupyter Lab server inside a Kubernetes pod.

The web interface provides a single button which starts the pod and shows a progress bar until Jupyter is ready. Once the notebook server is up, the page redirects to the running instance.

## Requirements

- Python 3.10+
- Access to a Kubernetes cluster with GPU nodes
- The [Kubernetes Python client](https://github.com/kubernetes-client/python) and `kubectl` configured to access your cluster
- `pip` to install Python dependencies

Install the dependencies with:

```bash
pip install -r requirements.txt
```

## Running

Start the Flask server:

```bash
python app.py
```

Open `http://localhost:5000` in your browser and click **Start Jupyter Notebook**. The server creates a Kubernetes pod in the background, waits for the Jupyter Lab token, and then redirects your browser to the running notebook.

The tokenised URL is also stored in `jupyter_url.json` so that it can be retrieved by the web page.

## Customisation

`container_manager.py` contains the logic for starting the Kubernetes pod. You can modify the image or startup command there if required. The script selects a node that still has available GPUs and schedules the pod on it.

### Kubernetes Setup

1. Ensure your kubeconfig is accessible (e.g. via `~/.kube/config`).
2. The nodes must expose the GPU resource as `nvidia.com/gpu`.
3. Install the Python dependencies and then start the Flask server as described above.

## License

This project is released under the MIT License.
