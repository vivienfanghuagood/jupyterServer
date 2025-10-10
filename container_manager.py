import time
import subprocess
import re
import socket
import random
import json
import os
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

from kubernetes import client, config, stream
from kubernetes.config.config_exception import ConfigException

# ---------- Configuration ----------------------------------------------------------
PUBLIC_IP = "129.212.190.193"
CONTAINER_PORT = 8888
DEFAULT_IMAGE = os.getenv("DEFAULT_IMAGE", "rocm/7.0-preview:rocm7.0_preview_ubuntu_22.04_vllm_0.10.1_instinct_rc1")
POD_TIMEOUT = 120
CHECK_INTERVAL = 5
MAPPING_FILE = "/tmp/jupyter_pod_mappings.json"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")  # Personal Access Token for private repos

# Global flag to track if Kubernetes config has been loaded
_k8s_config_loaded = False

@dataclass
class PodConfig:
    """Configuration for a Jupyter pod"""
    name: str
    startup_command: str
    image: str = DEFAULT_IMAGE
    container_port: int = CONTAINER_PORT
    gpu_limit: str = "1"
    gpu_request: str = "1"

# ---------- Helper Functions -------------------------------------------------------
def load_kubernetes_config():
    """
    Load Kubernetes configuration.
    Try in-cluster config first (when running inside a K8s cluster),
    then fall back to kubeconfig file (for local development).
    Only loads once per process to avoid configuration errors.
    """
    global _k8s_config_loaded

    if _k8s_config_loaded:
        return

    try:
        config.load_incluster_config()
        print("Loaded in-cluster Kubernetes configuration")
        _k8s_config_loaded = True
    except ConfigException:
        try:
            config.load_kube_config()
            print("Loaded Kubernetes configuration from kubeconfig")
            _k8s_config_loaded = True
        except ConfigException as e:
            print(f"Failed to load Kubernetes configuration: {e}")
            raise

def get_free_port(low: int = 10000, high: int = 60000, max_tries: int = 100) -> int:
    """Find a free port in the specified range"""
    for _ in range(max_tries):
        port = random.randint(low, high)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("Could not find a free port")

def get_node_gpu_counts() -> Dict[str, int]:
    """Get available GPU counts for each node"""
    load_kubernetes_config()
    v1 = client.CoreV1Api()
    counts: Dict[str, int] = {}

    for node in v1.list_node().items:
        disk_pressure = next((c for c in node.status.conditions if c.type == "DiskPressure"), None)
        if disk_pressure and disk_pressure.status == "True":
            continue
        alloc = node.status.allocatable.get("amd.com/gpu")
        counts[node.metadata.name] = int(alloc) if alloc else 0

    return counts

def find_available_gpu_node() -> Optional[str]:
    """Find a node with available GPU capacity"""
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

    for node, total in node_gpu.items():
        used = usage.get(node, 0)
        if total - used > 0:
            return node

    return None

def save_pod_mapping(pod_name: str, node_port: int, token: str, **extra_info):
    """Save pod mapping information to file"""
    mappings = {}
    if os.path.exists(MAPPING_FILE):
        with open(MAPPING_FILE, 'r') as f:
            mappings = json.load(f)

    mappings[pod_name] = {
        "node_port": node_port,
        "token": token,
        "public_ip": PUBLIC_IP,
        **extra_info
    }

    with open(MAPPING_FILE, 'w') as f:
        json.dump(mappings, f, indent=2)

# ---------- Kubernetes Operations --------------------------------------------------
def create_pod(pod_config: PodConfig) -> client.V1Pod:
    """Create a Kubernetes pod with the given configuration"""
    return client.V1Pod(
        metadata=client.V1ObjectMeta(name=pod_config.name),
        spec=client.V1PodSpec(
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
                    image=pod_config.image,
                    image_pull_policy="IfNotPresent",
                    command=["/bin/sh", "-c", pod_config.startup_command],
                    env=[
                        client.V1EnvVar(name="SHELL", value="/bin/bash"),
                        client.V1EnvVar(name="EXA_API_KEY", value="a6b74c67-4b93-4e79-b050-c0e61159c685"),
                    ],
                    ports=[client.V1ContainerPort(container_port=pod_config.container_port)],
                    volume_mounts=[
                        client.V1VolumeMount(
                            name="models-volume",
                            mount_path="/models"
                        )
                    ],
                    resources=client.V1ResourceRequirements(
                        limits={"amd.com/gpu": pod_config.gpu_limit},
                        requests={"amd.com/gpu": pod_config.gpu_request},
                    ),
                    security_context=client.V1SecurityContext(
                        capabilities=client.V1Capabilities(add=["SYS_PTRACE"]),
                        privileged=False,
                    ),
                )
            ],
        ),
    )

def create_service(pod_name: str, container_port: int) -> client.V1Service:
    """Create a NodePort service for the pod"""
    service_name = f"{pod_name}-svc"
    return client.V1Service(
        metadata=client.V1ObjectMeta(name=service_name),
        spec=client.V1ServiceSpec(
            type="NodePort",
            selector={"name": pod_name},
            ports=[
                client.V1ServicePort(
                    name="jupyter",
                    port=container_port,
                    target_port=container_port,
                    protocol="TCP",
                )
            ],
        ),
    )

def create_service_with_another_port(pod_name: str, container_port: int) -> client.V1Service:
    """Create a NodePort service for the pod"""
    service_name = f"{pod_name}-svc"
    return client.V1Service(
        metadata=client.V1ObjectMeta(name=service_name),
        spec=client.V1ServiceSpec(
            type="NodePort",
            selector={"name": pod_name},
            ports=[
                client.V1ServicePort(
                    name="jupyter",
                    port=container_port,
                    target_port=container_port,
                    protocol="TCP",
                ),
                client.V1ServicePort(
                    name="another",
                    port=8188,
                    target_port=8188,
                    protocol="TCP",
                )
            ],
        ),
    )

def wait_for_pod_ready(v1: client.CoreV1Api, pod_name: str, timeout: int = POD_TIMEOUT) -> bool:
    """Wait for a pod to be ready"""
    start = time.time()
    while time.time() - start < timeout:
        p = v1.read_namespaced_pod(pod_name, "default")
        if p.status.phase == "Running":
            return True
        if p.status.phase in ["Failed", "UnexpectedAdmissionError"]:
            print(f"Pod {pod_name} failed to start: {p.status.phase}")
            return False
        time.sleep(CHECK_INTERVAL)
    return False

def get_jupyter_token(v1: client.CoreV1Api, pod_name: str, timeout: int = POD_TIMEOUT) -> Optional[str]:
    """Extract Jupyter token from running pod"""
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
            return m.group(1)
        time.sleep(CHECK_INTERVAL)
    return None

def launch_jupyter_pod(pod_config: PodConfig, **extra_info) -> Tuple[Optional[str], Optional[str]]:
    """Generic function to launch a Jupyter pod with given configuration"""
    load_kubernetes_config()
    v1 = client.CoreV1Api()

    # Find available GPU node
    chosen_node = find_available_gpu_node()
    if not chosen_node:
        print("No node with free GPU capacity found.")
        return None, "/no_gpu"

    # Create pod
    pod = create_pod(pod_config)
    v1.create_namespaced_pod(namespace="default", body=pod)
    print(f"Pod {pod_config.name} created on {chosen_node}. Waiting for Jupyter...")

    # Wait for pod to be ready
    if not wait_for_pod_ready(v1, pod_config.name):
        return pod_config.name, "/no_gpu"

    # Label the pod for service selection
    v1.patch_namespaced_pod(
        name=pod_config.name,
        namespace="default",
        body={"metadata": {"labels": {"name": pod_config.name}}}
    )

    # Create service
    if "need_ip_port" in extra_info:
        service = create_service_with_another_port(pod_config.name, pod_config.container_port)
    else:
        service = create_service(pod_config.name, pod_config.container_port)
    service = v1.create_namespaced_service(namespace="default", body=service)
    node_port = service.spec.ports[0].node_port
    print(f"NodePort service created on port {node_port}.")

    # Get Jupyter token
    token = get_jupyter_token(v1, pod_config.name)
    if not token:
        print("Jupyter server did not come up in time.")
        return pod_config.name, None

    # Build URL based on extra_info
    if "need_ip_port" in extra_info:
        url = f"http://129.212.179.141:{node_port}/jupyter/{pod_config.name}/lab/tree/austin_ws/austin_multi-agent.ipynb?token={token}"
    elif "notebook_path" in extra_info:
        url = f"http://oneclickamd.ai/jupyter/{pod_config.name}/lab/tree/{extra_info['notebook_path']}?token={token}"
    else:
        # Default path for existing workshop
        url = f"http://oneclickamd.ai/jupyter/{pod_config.name}/lab/tree/austin_ws/austin_multi-agent.ipynb?token={token}"
    

    print("Jupyter Notebook URL:", url)

    # Save mapping
    save_pod_mapping(pod_config.name, node_port, token, **extra_info)

    return pod_config.name, url

# ---------- Public API Functions ---------------------------------------------------
def start_pod_and_get_jupyter_url() -> Tuple[Optional[str], Optional[str]]:
    """Start a pod with the default AMD GPU workshop"""
    pod_name = f"jupyter-launcher-{random.randint(1000,9999)}"
    startup_command = (
        "pip install --no-cache-dir jupyter ihighlight && "
        "git clone https://github.com/Mahdi-CV/amd-gpu-workshops && "
        "cd amd-gpu-workshops && cd notebooks && "
        f"jupyter lab --ip=0.0.0.0 --port={CONTAINER_PORT} --allow-root "
        f"--ServerApp.base_url=/jupyter/{pod_name}/ "
        f"--ServerApp.open_browser=False --ServerApp.trust_xheaders=True"
    )

    pod_config = PodConfig(
        name=pod_name,
        startup_command=startup_command
    )

    return launch_jupyter_pod(pod_config)

def start_ip_pod_and_get_jupyter_url() -> Tuple[Optional[str], Optional[str]]:
    """Start a pod with the default AMD GPU workshop"""
    pod_name = f"jupyter-launcher-{random.randint(1000,9999)}"
    startup_command = (
        "pip install --no-cache-dir jupyter ihighlight && "
        "git clone https://github.com/Mahdi-CV/amd-gpu-workshops && "
        "cd amd-gpu-workshops && cd notebooks && "
        f"jupyter lab --ip=0.0.0.0 --port={CONTAINER_PORT} --allow-root "
        f"--ServerApp.base_url=/jupyter/{pod_name}/ "
        f"--ServerApp.open_browser=False --ServerApp.trust_xheaders=True"
    )

    pod_config = PodConfig(
        name=pod_name,
        startup_command=startup_command
    )

    extra_info = {
        "need_ip_port": True
    }

    return launch_jupyter_pod(pod_config, **extra_info)

def start_pod_with_github_repo(owner: str, repo: str, branch: str, notebook_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Start a pod with a specific GitHub repository cloned and open the specified notebook"""
    pod_name = f"jupyter-launcher-{random.randint(1000,9999)}"
    github_url = f"https://github.com/{owner}/{repo}.git"

    startup_command = (
        f"pip install --no-cache-dir jupyter ihighlight && "
        f"git clone -b {branch} {github_url} /workspace/{repo} && "
        f"cd /workspace/{repo} && "
        f"jupyter lab --ip=0.0.0.0 --port={CONTAINER_PORT} --allow-root "
        f"--ServerApp.base_url=/jupyter/{pod_name}/ "
        f"--ServerApp.open_browser=False --ServerApp.trust_xheaders=True"
    )

    pod_config = PodConfig(
        name=pod_name,
        startup_command=startup_command
    )

    extra_info = {
        "repo": f"{owner}/{repo}",
        "notebook_path": notebook_path
    }

    return launch_jupyter_pod(pod_config, **extra_info)

# ---------- Main -------------------------------------------------------------------
if __name__ == "__main__":
    start_pod_and_get_jupyter_url()