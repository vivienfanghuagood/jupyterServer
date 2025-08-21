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
# Use hardcoded IP instead of dynamic detection
public_ip = "129.212.190.193"

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

def start_pod_and_get_jupyter_url() -> tuple[str | None, str | None]:
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
        return None, "/no_gpu"

    pod_name = f"jupyter-launcher-{random.randint(1000,9999)}"
    container_port = 8888
    startup_command = (
        "pip install jupyter && "
        "pip install ihighlight && "
        "git clone https://github.com/hubertlu-tw/sglang_amd_sf_meetup_workshop.git && "
        "cd sglang_amd_sf_meetup_workshop && "
        f"jupyter lab --ip=0.0.0.0 --port={container_port} --allow-root "
        f"--ServerApp.base_url=/jupyter/{pod_name}/ "
        f"--ServerApp.open_browser=False --ServerApp.trust_xheaders=True"
    )

    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(name=pod_name),
        spec=client.V1PodSpec(
            #node_name=chosen,
             tolerations=[
                client.V1Toleration(
                    key="amd.com/gpu",
                    operator="Exists",
                    effect="NoSchedule",
                )
            ],
            restart_policy="Never",
            volumes=[
                client.V1Volume(
                    name="models-volume",
                    host_path=client.V1HostPathVolumeSource(path="/mnt/models")
                )
            ],
            containers=[
                client.V1Container(
                    name="jupyter",
                    image="henryx/haisgl:sglang-v0.5.0rc2-rocm630-mi30x-workshop",
                    image_pull_policy="IfNotPresent",
                    command=["/bin/sh", "-c", startup_command],
                    env=[
                    client.V1EnvVar(name="SHELL", value="/bin/bash")
                ],
                    ports=[client.V1ContainerPort(container_port=container_port)],
                    volume_mounts=[
                        client.V1VolumeMount(
                            name="models-volume",
                            mount_path="/models"
                        )
                    ],
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
            return pod_name, "/no_gpu"
        if p.status.phase == "UnexpectedAdmissionError":
            print("Pod admission error, likely due to insufficient resources.")
            return pod_name, "/no_gpu"
        time.sleep(interval)

    # Label the pod so the service can select it
    v1.patch_namespaced_pod(
        name=pod_name,
        namespace="default",
        body={"metadata": {"labels": {"name": pod_name}}}
    )

    service_name = f"{pod_name}-svc"
    #node_port = random.randint(30000, 32767)

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
                    #node_port=node_port,
                    protocol="TCP",
                )
            ],
        ),
    )
    service = v1.create_namespaced_service(namespace="default", body=service)
    node_port = service.spec.ports[0].node_port
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
        return pod_name, None

    # Generate URL without port for reverse proxy
    # The nginx proxy will route /jupyter/{pod_name}/ to the actual NodePort
    url = f"http://amddevcloud.com/jupyter/{pod_name}/lab/tree/workshop_agentic_ai_sglang.ipynb?token={token}"
    print("Jupyter Notebook URL:", url)
    
    # Store the mapping for nginx configuration
    import json
    import os
    
    mapping_file = "/tmp/jupyter_pod_mappings.json"
    mappings = {}
    if os.path.exists(mapping_file):
        with open(mapping_file, 'r') as f:
            mappings = json.load(f)
    
    mappings[pod_name] = {
        "node_port": node_port,
        "token": token,
        "public_ip": public_ip
    }
    
    with open(mapping_file, 'w') as f:
        json.dump(mappings, f, indent=2)
    
    return pod_name, url


# ---------------- Run ----------------------
if __name__ == "__main__":
    start_pod_and_get_jupyter_url()
