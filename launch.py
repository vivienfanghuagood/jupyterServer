import docker
import time
import re

def start_container_and_print_jupyter_url():
    client = docker.from_env()
    try:
        startup_command = (
            "pip install jupyter && "
            "jupyter lab --ip=0.0.0.0 --port=8080 --allow-root --NotebookApp.allow_origin='https://colab.research.google.com'"
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


        time.sleep(10)


        exec_result = container.exec_run("jupyter notebook list", user="root")
        output_str = exec_result.output.decode("utf-8").strip()


        token = None
        for line in output_str.splitlines():
            if "http://" in line:

                m = re.search(r'\?token=([^\s&]+)', line)
                if m:
                    token = m.group(1)
                    print("\n Copy and paste the below provide URL in browser")
                    print("\nhttp://127.0.0.1:8080/?token=", token)
                    break

        if not token:
            print("Jupyter server failed to start.")
            return None
        
        return f"http://64.139.222.239:5002/?token={token}"

    except docker.errors.APIError as e:
        print("Failed to start container or execute command:", e)

if __name__ == '__main__':
    start_container_and_print_jupyter_url()