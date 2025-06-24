# Jupyter Server Launcher

This repository contains a small Flask application that launches a Jupyter Lab server inside a Docker container.

The web interface provides a single button which starts the container and shows a progress bar until Jupyter is ready. Once the notebook server is up, the page redirects to the running instance.

## Requirements

- Python 3.10+
- [Docker](https://www.docker.com/) with access to pull and run the container image `rocm/vllm-dev:20250112`
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

Open `http://localhost:5000` in your browser and click **Start Jupyter Notebook**. The server starts a container in the background, waits for the Jupyter Lab token, and automatically redirects your browser to the running notebook once it is ready.

## Customisation

`container_manager.py` contains the logic for starting the Docker container. You can modify the image or startup command there if required.

## License

This project is released under the MIT License.
