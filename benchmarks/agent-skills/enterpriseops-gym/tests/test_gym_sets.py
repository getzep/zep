from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from eog.dataset import Task
from eog.gym_sets import (
    GYM_SERVICES,
    gym_service_run_command,
    gym_set_container_names,
    gym_set_host_port,
    task_for_gym_set,
)
from scripts import start_gym_service


def _task(server: dict) -> Task:
    return Task(
        task_id="gym-set-test",
        domain="itsm",
        system_prompt="",
        user_prompt="",
        selected_tools=(),
        restricted_tools=(),
        mcp_endpoint="/mcp",
        number_of_runs=1,
        reset_database_between_runs=True,
        gym_servers_config=(server,),
        verifiers=(),
    )


def test_gym_set_zero_keeps_existing_container_names_and_ports() -> None:
    assert gym_set_container_names(0) == {
        "csm": "eog-csm",
        "teams": "eog-teams",
        "calendar": "eog-calendar",
        "email": "eog-email",
        "itsm": "eog-itsm-opened-by-patch",
        "hr": "eog-hr",
        "drive": "eog-drive",
    }
    assert {
        domain: gym_set_host_port(0, domain) for domain in ("csm", "teams", "calendar", "email", "itsm", "hr", "drive")
    } == {
        "csm": 8001,
        "teams": 8002,
        "calendar": 8003,
        "email": 8004,
        "itsm": 8006,
        "hr": 8008,
        "drive": 8009,
    }


def test_nonzero_gym_set_maps_names_ports_and_task_urls() -> None:
    assert gym_set_container_names(2)["itsm"] == "eog-set2-itsm"
    assert gym_set_container_names(2)["teams"] == "eog-set2-teams"
    assert gym_set_host_port(2, "itsm") == 8206
    assert gym_set_host_port(2, "drive") == 8209

    task = _task(
        {
            "mcp_server_name": "gym-itsm-mcp",
            "mcp_server_url": "http://localhost:8006",
            "context": {"x-database-id": "tenant"},
        }
    )
    selected = task_for_gym_set(task, 2)

    assert selected.gym_servers_config[0]["mcp_server_url"] == "http://localhost:8206"
    assert selected.gym_servers_config[0]["context"] == {"x-database-id": "tenant"}
    assert task.gym_servers_config[0]["mcp_server_url"] == "http://localhost:8006"


def test_service_launch_mapping_uses_pinned_images_ports_and_apps() -> None:
    expected = {
        "csm": (8005, "main:app", "eaa456ac9aa85728426e7d3813a0bbca0949d6a8695be30e26f03894e6e6b189", "127.0.0.1"),
        "teams": (8005, "main:app", "602655e46f6501885540c36dc9b12114cb173c75063d7f25c17ed0652695fa78", "0.0.0.0"),
        "calendar": (8003, "main:app", "994c5421a6dd065861bc7f813a177f6d408875e9df60fe8d012959bc4510da02", "127.0.0.1"),
        "email": (8005, "main:app", "69c2081fe4ab0962b86233f9fb52b307b8ad0019f6746ba64ce75851036201cd", "127.0.0.1"),
        "itsm": (8005, "main:app", "a234ae3fb7cee196ba25e6b9957969dea829919b6e8271dddae128f065aaf39f", "127.0.0.1"),
        "hr": (8005, "main:app", "1ea1c1d64d4be35e8062e56f00b8318e9e6c09289cfa56bcfd0595bfa59ac64d", "127.0.0.1"),
        "drive": (
            8005,
            "app.main:app",
            "3475962fcf6da7675e194dbf138de01fa3e96134a302ad47316e4111a5e63f32",
            "127.0.0.1",
        ),
    }

    assert set(GYM_SERVICES) == set(expected)
    for domain, (container_port, app, digest, host_bind) in expected.items():
        service = GYM_SERVICES[domain]
        command = gym_service_run_command(2, domain)
        assert service.container_port == container_port
        assert service.app == app
        assert service.image_ref.endswith(f"@sha256:{digest}")
        assert service.host_bind == host_bind
        assert command[command.index("--name") + 1] == f"eog-set2-{domain}"
        assert command[command.index("--publish") + 1] == (
            f"{host_bind}:{gym_set_host_port(2, domain)}:{container_port}"
        )
        assert command[-4:] == ["--host", "0.0.0.0", "--port", str(container_port)]
        assert "--ulimit" in command
        assert "nofile=65536:65536" in command
        assert command[command.index("--workdir") + 1] == "/app"


@pytest.mark.parametrize(
    ("domain", "host_port", "host_ip"),
    [("teams", 8002, "0.0.0.0"), ("itsm", 8006, "")],
)
def test_container_with_old_image_id_does_not_match_current_image(domain: str, host_port: int, host_ip: str) -> None:
    service = GYM_SERVICES[domain]
    info = {
        "Image": "sha256:previous-build",
        "Config": {"Image": service.image},
        "HostConfig": {
            "PortBindings": {f"{service.container_port}/tcp": [{"HostIp": host_ip, "HostPort": str(host_port)}]}
        },
    }

    assert not start_gym_service._matches_container(
        info,
        domain=domain,
        gym_set=0,
        image_id="sha256:current-build",
    )

    info["Image"] = "sha256:current-build"
    assert start_gym_service._matches_container(
        info,
        domain=domain,
        gym_set=0,
        image_id="sha256:current-build",
    )


def test_recreates_container_when_image_id_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    domain = "teams"
    service = GYM_SERVICES[domain]
    name = "eog-teams"
    old_info = {
        "Image": "sha256:previous-build",
        "Config": {"Image": service.image},
        "HostConfig": {"PortBindings": {f"{service.container_port}/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8002"}]}},
    }
    commands: list[list[str]] = []
    command = gym_service_run_command(0, domain)
    monkeypatch.setattr(start_gym_service, "_prepare_image", lambda _: "sha256:current-build")
    monkeypatch.setattr(start_gym_service, "_container_info", lambda _: old_info)
    monkeypatch.setattr(start_gym_service, "_run", lambda value: commands.append(value) or "")
    monkeypatch.setattr(start_gym_service, "_wait_for_health", lambda *_: None)
    monkeypatch.setattr(start_gym_service, "_verify_patch_marker", lambda *_: None)

    start_gym_service.start_domain(domain, 0)

    assert commands == [["docker", "rm", "--force", name], command]


def test_patch_marker_verification_matches_the_source_file(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_path = start_gym_service.PROJECT_ROOT / GYM_SERVICES["teams"].build_context / "sitecustomize.py"
    patch_hash = hashlib.sha256(patch_path.read_bytes()).hexdigest()
    result = SimpleNamespace(
        returncode=0,
        stdout=f"EOG_TEAMS_TOWNHALL_PATCH_ACTIVE:{patch_hash}\n",
        stderr="",
    )
    monkeypatch.setattr(start_gym_service.subprocess, "run", lambda *args, **kwargs: result)

    start_gym_service._verify_patch_marker("teams", "eog-teams")


def test_set_zero_returns_the_original_task() -> None:
    task = _task({"mcp_server_name": "gym-itsm-mcp", "mcp_server_url": "http://localhost:8006"})

    assert task_for_gym_set(task, 0) is task


@pytest.mark.parametrize("gym_set", [-1])
def test_negative_gym_set_is_rejected(gym_set: int) -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        gym_set_container_names(gym_set)
