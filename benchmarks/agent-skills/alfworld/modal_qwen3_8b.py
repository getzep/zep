"""Run an optional Qwen3-8B endpoint for the ALFWorld benchmark.

Deploy this script in a Modal workspace that you control.
Create the `alfworld-model` secret with the `ALFWORLD_BEARER_TOKEN` value.
Set `ALFWORLD_MODEL_BASE_URL` to the deployed endpoint URL.
"""

import os
import subprocess
import time
import urllib.error
import urllib.request

import modal

MODEL = "Qwen/Qwen3-8B"
REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
SERVED_MODEL = "qwen3-8b"
STARTUP_TIMEOUT = 1500

app = modal.App("alfworld-qwen3-8b")
cache = modal.Volume.from_name("alfworld-hf-cache", create_if_missing=True)
image = modal.Image.from_registry("vllm/vllm-openai:v0.29.0", add_python="3.12").entrypoint([])


def command() -> list[str]:
    return [
        "vllm",
        "serve",
        MODEL,
        "--revision=" + REVISION,
        "--served-model-name=" + SERVED_MODEL,
        "--api-key=" + os.environ["ALFWORLD_BEARER_TOKEN"],
        "--host=0.0.0.0",
        "--port=8000",
        "--max-model-len=32768",
        "--max-num-seqs=128",
        "--gpu-memory-utilization=0.90",
        "--enable-prefix-caching",
        "--reasoning-parser=qwen3",
        "--generation-config=vllm",
        '--default-chat-template-kwargs={"enable_thinking":false}',
        "--enable-prompt-tokens-details",
    ]


@app.server(
    image=image,
    secrets=[modal.Secret.from_name("alfworld-model")],
    gpu="H100",
    cpu=8,
    memory=32768,
    volumes={"/cache": cache},
    min_containers=0,
    max_containers=4,
    target_concurrency=64,
    scaledown_window=600,
    startup_timeout=STARTUP_TIMEOUT,
    exit_grace_period=60,
    unauthenticated=False,
    port=8000,
    routing_region="us-west",
    compute_region="us",
    env={"HF_HOME": "/cache/hf", "VLLM_CACHE_ROOT": "/cache/vllm"},
)
class Server:
    @modal.enter()
    def start(self):
        self.proc = subprocess.Popen(command())
        deadline = time.monotonic() + STARTUP_TIMEOUT - 30
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("vLLM exited during startup")
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5) as r:
                    if r.status == 200:
                        cache.commit()
                        return
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                pass
            time.sleep(2)
        self.proc.terminate()
        raise TimeoutError("vLLM startup health check timed out")

    @modal.exit()
    def stop(self):
        if hasattr(self, "proc") and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
