# Jupyter Server Launcher

This repository contains a small FastAPI application that launches a Jupyter Lab server inside a Kubernetes pod.

The web interface prompts for an email address. When you submit the form it launches a Jupyter Lab server inside a Kubernetes pod and stores the resulting URL for that email. If you request again with the same email, the stored URL is returned instead of creating a new pod.

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

Open `http://localhost:8000` in your browser and enter your email address. The application stores the notebook URL in a local SQLite database and reuses it on subsequent requests.

## Customisation

`container_manager.py` contains the logic for starting the Kubernetes pod. You can modify the image or startup command there if required. The script selects a node that still has available GPUs and schedules the pod on it.

### Kubernetes Setup

1. Ensure your kubeconfig is accessible (e.g. via `~/.kube/config`).
2. The nodes must expose the GPU resource as `amd.com/gpu`.
3. Install the Python dependencies and then start the FastAPI server as described above.

## License

This project is released under the MIT License.
