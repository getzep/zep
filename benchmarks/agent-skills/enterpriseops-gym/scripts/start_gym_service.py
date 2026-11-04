from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from eog.gym_sets import GYM_SERVICES, gym_service_run_command, gym_set_host_port

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GYM_SET_DOMAINS = ("csm", "teams", "calendar", "email", "itsm", "hr", "drive")


def _run(command: list[str]) -> str:
    result = subprocess.run(command, check=True, cwd=PROJECT_ROOT, capture_output=True, text=True)
    return result.stdout.strip()


def _prepare_image(domain: str) -> str:
    service = GYM_SERVICES[domain]
    if service.dockerfile is None:
        _run(["docker", "pull", service.image_ref])
    else:
        _run(
            [
                "docker",
                "build",
                "-t",
                service.image,
                "-f",
                str(PROJECT_ROOT / service.dockerfile),
                str(PROJECT_ROOT / service.build_context),
            ]
        )
    return json.loads(_run(["docker", "image", "inspect", service.image]))[0]["Id"]


def _container_info(name: str) -> dict | None:
    result = subprocess.run(
        ["docker", "inspect", name],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        if "no such object" in result.stderr.lower() or "no such container" in result.stderr.lower():
            return None
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)[0]


def _matches_container(info: dict, *, domain: str, gym_set: int, image_id: str) -> bool:
    service = GYM_SERVICES[domain]
    port = f"{service.container_port}/tcp"
    bindings = (info.get("HostConfig", {}).get("PortBindings") or {}).get(port) or []
    if info.get("Image") != image_id or len(bindings) != 1:
        return False
    binding = bindings[0]
    if binding.get("HostPort") != str(gym_set_host_port(gym_set, domain)):
        return False
    host_ip = binding.get("HostIp", "")
    if host_ip == service.host_bind:
        return True
    return gym_set == 0 and domain == "itsm" and host_ip in {"", "0.0.0.0", "::"}


def _verify_patch_marker(domain: str, name: str) -> None:
    marker_prefixes = {
        "teams": "EOG_TEAMS_TOWNHALL_PATCH_ACTIVE",
        "itsm": "EOG_ITSM_OPENED_BY_PATCH_ACTIVE",
    }
    marker_prefix = marker_prefixes.get(domain)
    if marker_prefix is None:
        return
    service = GYM_SERVICES[domain]
    patch_path = PROJECT_ROOT / service.build_context / "sitecustomize.py"
    patch_hash = hashlib.sha256(patch_path.read_bytes()).hexdigest()
    expected_marker = f"{marker_prefix}:{patch_hash}"
    result = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
    if result.returncode or expected_marker not in result.stdout + result.stderr:
        raise RuntimeError(f"Container {name} does not report the expected patch marker {expected_marker}.")


def _wait_for_health(domain: str, gym_set: int) -> None:
    port = gym_set_host_port(gym_set, domain)
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + 90
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as error:
            last_error = error
        time.sleep(1)
    raise RuntimeError(f"Gym health check did not pass for {domain} set {gym_set}: {last_error}")


def start_domain(domain: str, gym_set: int) -> None:
    image_id = _prepare_image(domain)
    command = gym_service_run_command(gym_set, domain)
    name = command[command.index("--name") + 1]
    info = _container_info(name)
    if info is not None:
        if info.get("Image") != image_id:
            _run(["docker", "rm", "--force", name])
            _run(command)
            print(f"Recreated container {name}", flush=True)
        elif not _matches_container(info, domain=domain, gym_set=gym_set, image_id=image_id):
            raise RuntimeError(f"Container {name} exists with a different image or port mapping; it was not changed.")
        else:
            if not info.get("State", {}).get("Running"):
                _run(["docker", "start", name])
            print(f"Reused container {name}", flush=True)
    else:
        _run(command)
        print(f"Started container {name}", flush=True)
    _wait_for_health(domain, gym_set)
    _verify_patch_marker(domain, name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start or reuse a static set of EnterpriseOps Gym services.")
    parser.add_argument("--gym-set", type=int, default=0)
    parser.add_argument("--domain", choices=GYM_SET_DOMAINS, action="append")
    args = parser.parse_args()
    if args.gym_set < 0:
        parser.error("--gym-set must not be negative")
    for domain in args.domain or GYM_SET_DOMAINS:
        start_domain(domain, args.gym_set)


if __name__ == "__main__":
    main()
