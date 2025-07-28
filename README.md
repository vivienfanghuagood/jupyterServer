# Jupyter Server Launcher

This repository contains a small FastAPI application that launches a Jupyter Lab server inside a Kubernetes pod.

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

Start the application using uvicorn:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in your browser. The web interface automatically begins launching a notebook pod. Each request is assigned a session ID that is stored in a local SQLite database. The page polls the API with this ID until the Jupyter server is ready and then redirects your browser to the running instance.

## Customisation

`container_manager.py` contains the logic for starting the Kubernetes pod. You can modify the image or startup command there if required. The script selects a node that still has available GPUs and schedules the pod on it.

### Kubernetes Setup

1. Ensure your kubeconfig is accessible (e.g. via `~/.kube/config`).
2. The nodes must expose the GPU resource as `amd.com/gpu`.
3. Install the Python dependencies and then start the FastAPI server as described above.

## License

This project is released under the MIT License.
