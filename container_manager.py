import docker
import time
import subprocess
import re
import socket
import random
import os

GPU_RESOURCE = os.getenv("GPU_RESOURCE", "nvidia.com/gpu")

# ---------- helper ----------------------------------------------------------
def get_free_port(low: int = 10000, high: int = 60000, max_tries: int = 100) -> int:
    """
    Pick a random TCP port that is not in use on the host.
    """
    for _ in range(max_tries):
        port = random.randint(low, high)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:  # 0 means 'open / in use'
                return port
    raise RuntimeError("Could not find a free port")


# ---------- public IP -------------------------------------------------------
try:
    public_ip = subprocess.check_output(
        ["curl", "--silent", "ifconfig.me"], text=True, timeout=10
    ).strip()
except Exception as e:
    print(f"Warning: could not fetch public IP ({e}), defaulting to localhost")
    public_ip = "127.0.0.1"


# ---------- main ------------------------------------------------------------
def start_container_and_get_jupyter_url():
    client = docker.from_env()

    # 1. grab an unused host port first
    jupyter_port = get_free_port()

    # 2. embed that port in the start-up command
    startup_command = (
        "pip install --quiet jupyter && "
        f"jupyter lab --ip=0.0.0.0 --port={jupyter_port} --allow-root "
        "--NotebookApp.allow_origin='https://colab.research.google.com'"
    )

    try:
        container = client.containers.run(
            image="rocm/vllm-dev:20250112",
            command=["/bin/sh", "-c", startup_command],
            detach=True,
            cap_add=["SYS_PTRACE"],
            security_opt=["seccomp=unconfined"],
            devices=["/dev/kfd", "/dev/dri"],
            volumes={"/": {"bind": "/workspace", "mode": "rw"}},
            group_add=["video"],
            ipc_mode="host",
            network="host",          # host networking ⇒ same port is exposed outside
            shm_size="32G",
        )
        print(f"Container started! ID: {container.id}\nWaiting for Jupyter…")

        # 3. poll for the token
        token, timeout, interval = None, 60, 5
        start = time.time()

        while time.time() - start < timeout:
            exec_result = container.exec_run("jupyter notebook list", user="root")
            output = exec_result.output.decode().strip()

            m = re.search(r"\?token=([^\s&]+)", output)
            if m:
                token = m.group(1)
                break
            time.sleep(interval)

        if not token:
            print("Jupyter server did not come up in time.")
            return None

        # 4. compose the external URL
        url = f"http://{public_ip}:{jupyter_port}/?token={token}"
        print("Jupyter Notebook URL:", url)
        return url

    except Exception as e:
        print(f"Error starting container: {e}")
        return None
