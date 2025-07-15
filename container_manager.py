import time
import subprocess
import re
import socket
import random
from typing import Dict

from kubernetes import client, config, stream

# ---------- helper ----------------------------------------------------------
def get_free_port(low: int = 10000, high: int = 60000, max_tries: int = 100) -> int:
    for _ in range(max_tries):
        port = random.randint(low, high)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
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
def get_node_gpu_counts() -> Dict[str, int]:
    config.load_kube_config()
    v1 = client.CoreV1Api()
    counts: Dict[str, int] = {}

    for node in v1.list_node().items:
        disk_pressure = next((c for c in node.status.conditions if c.type == "DiskPressure"), None)
        if disk_pressure and disk_pressure.status == "True":
            continue
        alloc = node.status.allocatable.get("amd.com/gpu")
        counts[node.metadata.name] = int(alloc) if alloc else 0

    return counts

def start_pod_and_get_jupyter_url() -> str | None:
    config.load_kube_config()
    v1 = client.CoreV1Api()

    node_gpu = get_node_gpu_counts()
    pods = v1.list_pod_for_all_namespaces().items

    usage = {n: 0 for n in node_gpu}
    for pod in pods:
        if not pod.spec.node_name:
            continue
        node = pod.spec.node_name
        if node in usage:
            for c in pod.spec.containers:
                req = c.resources.requests or {}
                gpu = req.get("amd.com/gpu")
                if gpu:
                    usage[node] += int(gpu)

    chosen = None
    for node, total in node_gpu.items():
        used = usage.get(node, 0)
        if total - used > 0:
            chosen = node
            break

    if not chosen:
        print("No node with free GPU capacity found.")
        return None

    pod_name = f"jupyter-launcher-{random.randint(1000,9999)}"
    container_port = 8888
    startup_command = (
        "pip install --quiet jupyter && "
        f"jupyter lab --ip=0.0.0.0 --port={container_port} --allow-root "
        "--NotebookApp.allow_origin='https://colab.research.google.com'"
    )

    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(name=pod_name),
        spec=client.V1PodSpec(
            node_name=chosen,
            restart_policy="Never",
            containers=[
                client.V1Container(
                    name="jupyter",
                    image="rocm/vllm-dev:20250112",
                    image_pull_policy="IfNotPresent",
                    command=["/bin/sh", "-c", startup_command],
                    ports=[client.V1ContainerPort(container_port=container_port)],
                    resources=client.V1ResourceRequirements(
                        limits={"amd.com/gpu": "1"},
                        requests={"amd.com/gpu": "1"},
                    ),
                    security_context=client.V1SecurityContext(
                        capabilities=client.V1Capabilities(add=["SYS_PTRACE"]),
                        privileged=False,
                    ),
                )
            ],
        ),
    )

    v1.create_namespaced_pod(namespace="default", body=pod)
    print(f"Pod {pod_name} created on {chosen}. Waiting for Jupyter…")

    timeout, interval = 120, 5
    start = time.time()
    while time.time() - start < timeout:
        p = v1.read_namespaced_pod(pod_name, "default")
        if p.status.phase == "Running":
            break
        if p.status.phase == "Failed":
            print("Pod failed to start.")
            return None
        time.sleep(interval)

    # Label the pod so the service can select it
    v1.patch_namespaced_pod(
        name=pod_name,
        namespace="default",
        body={"metadata": {"labels": {"name": pod_name}}}
    )

    service_name = f"{pod_name}-svc"
    node_port = random.randint(30000, 32767)

    service = client.V1Service(
        metadata=client.V1ObjectMeta(name=service_name),
        spec=client.V1ServiceSpec(
            type="NodePort",
            selector={"name": pod_name},
            ports=[
                client.V1ServicePort(
                    name="jupyter",
                    port=container_port,
                    target_port=container_port,
                    node_port=node_port,
                    protocol="TCP",
                )
            ],
        ),
    )
    v1.create_namespaced_service(namespace="default", body=service)
    print(f"NodePort service {service_name} created on port {node_port}.")

    token = None
    start = time.time()
    while time.time() - start < timeout:
        exec_out = stream.stream(
            v1.connect_get_namespaced_pod_exec,
            pod_name,
            "default",
            command=["jupyter", "notebook", "list"],
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            container="jupyter",
        )
        m = re.search(r"\?token=([^\s&]+)", exec_out)
        if m:
            token = m.group(1)
            break
        time.sleep(interval)

    if not token:
        print("Jupyter server did not come up in time.")
        return None

    url = f"http://{public_ip}:{node_port}/?token={token}"
    print("Jupyter Notebook URL via NodePort:", url)
    return url


# ---------------- Run ----------------------
if __name__ == "__main__":
    start_pod_and_get_jupyter_url()
