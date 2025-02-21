import docker
import time
import re

def start_container_and_get_jupyter_url():
    client = docker.from_env()
    try:
        startup_command = (
            "pip install jupyter && "
            "jupyter lab --ip=0.0.0.0 --port=5002 --allow-root "
            "--NotebookApp.allow_origin='https://colab.research.google.com'"
        )

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
            network="host",
            shm_size="32G"
        )
        print(f"Container started successfully! \nContainer ID: {container.id}")

        # Wait (poll) for the Jupyter Notebook to be ready.
        timeout = 30  # maximum seconds to wait
        interval = 5   # poll every 5 seconds
        start_time = time.time()
        token = None

        while time.time() - start_time < timeout:
            exec_result = container.exec_run("jupyter notebook list", user="root")
            output_str = exec_result.output.decode("utf-8").strip()
            print("Polling output:\n", output_str)
            for line in output_str.splitlines():
                if "http://" in line:
                    m = re.search(r'\?token=([^\s&]+)', line)
                    if m:
                        token = m.group(1)
                        break
            if token:
                break
            time.sleep(interval)

        if not token:
            print("Jupyter server failed to start within the timeout period.")
            return None

        # Return the notebook URL (adjust as needed for your network)
        jupyter_url = f"http://64.139.222.239:5002/?token={token}"
        print("Jupyter Notebook URL:", jupyter_url)
        return jupyter_url

    except Exception as e:
        print(f"Error starting container: {e}")
        return None





# import docker
# import time
# import re

# def start_container_and_print_jupyter_url():
#     client = docker.from_env()
#     try:
#         startup_command = (
#             "pip install jupyter && "
#             "jupyter lab --ip=0.0.0.0 --port=5002 --allow-root --NotebookApp.allow_origin='https://colab.research.google.com'"
#         )

#         container = client.containers.run(
#             image="rocm/vllm-dev:20250112",
#             command=["/bin/sh", "-c", startup_command],
#             detach=True,
#             cap_add=["SYS_PTRACE"],
#             security_opt=["seccomp=unconfined"],
#             devices=["/dev/kfd", "/dev/dri"],
#             volumes={"/": {"bind": "/workspace", "mode": "rw"}},
#             group_add=["video"],
#             ipc_mode="host",
#             network="host",
#             shm_size="32G"
#         )
#         print(f"Container started successfully! \nContainer ID: {container.id}")

#         time.sleep(20)

#         exec_result = container.exec_run("jupyter notebook list", user="root")
#         output_str = exec_result.output.decode("utf-8").strip()

#         token = None
#         for line in output_str.splitlines():
#             if "http://" in line:
#                 m = re.search(r'\?token=([^\s&]+)', line)
#                 if m:
#                     token = m.group(1)
#                     print("\n Copy and paste the below provided URL in browser")
#                     print("\nhttp://64.139.222.239:5002/?token=", token)
#                     break

#         if not token:
#             print("Jupyter server failed to start.")
#             return None

#         return f"http://64.139.222.239:5002/?token={token}"

#     except Exception as e:
#         print(f"Error starting container: {e}")
#         return None
