from __future__ import annotations

from dataclasses import dataclass, replace
from urllib.parse import urlsplit, urlunsplit

from .dataset import Task

SERVER_NAMES = {
    "csm": "sn-csm-server",
    "teams": "gym-teams-mcp",
    "calendar": "gym-calendar",
    "email": "gym-email-mcp",
    "itsm": "gym-itsm-mcp",
    "hr": "sn-hr-internal",
    "drive": "gym-google-drive-mcp",
}

DOMAIN_INDICES = {
    "csm": 1,
    "teams": 2,
    "calendar": 3,
    "email": 4,
    "itsm": 6,
    "hr": 8,
    "drive": 9,
}

PUBLIC_IMAGE_PREFIX = "docker.io/shivakrishnareddyma225/enterpriseops-gym-mcp"


@dataclass(frozen=True, slots=True)
class GymService:
    container_port: int
    app: str
    image_ref: str
    runtime_image: str | None = None
    dockerfile: str | None = None
    build_context: str | None = None
    host_bind: str = "127.0.0.1"

    @property
    def image(self) -> str:
        return self.runtime_image or self.image_ref


GYM_SERVICES = {
    "csm": GymService(
        8005,
        "main:app",
        f"{PUBLIC_IMAGE_PREFIX}-csm@sha256:eaa456ac9aa85728426e7d3813a0bbca0949d6a8695be30e26f03894e6e6b189",
    ),
    "teams": GymService(
        8005,
        "main:app",
        f"{PUBLIC_IMAGE_PREFIX}-teams@sha256:602655e46f6501885540c36dc9b12114cb173c75063d7f25c17ed0652695fa78",
        runtime_image="eog-teams-townhall-patched",
        dockerfile="containers/teams-townhall-patch/Dockerfile",
        build_context="containers/teams-townhall-patch",
        host_bind="0.0.0.0",
    ),
    "calendar": GymService(
        8003,
        "main:app",
        f"{PUBLIC_IMAGE_PREFIX}-calendar@sha256:994c5421a6dd065861bc7f813a177f6d408875e9df60fe8d012959bc4510da02",
    ),
    "email": GymService(
        8005,
        "main:app",
        f"{PUBLIC_IMAGE_PREFIX}-email@sha256:69c2081fe4ab0962b86233f9fb52b307b8ad0019f6746ba64ce75851036201cd",
    ),
    "itsm": GymService(
        8005,
        "main:app",
        f"{PUBLIC_IMAGE_PREFIX}-itsm@sha256:a234ae3fb7cee196ba25e6b9957969dea829919b6e8271dddae128f065aaf39f",
        runtime_image="eog-itsm-opened-by:local",
        dockerfile="containers/itsm-opened-by-patch/Dockerfile",
        build_context="containers/itsm-opened-by-patch",
    ),
    "hr": GymService(
        8005,
        "main:app",
        f"{PUBLIC_IMAGE_PREFIX}-hr@sha256:1ea1c1d64d4be35e8062e56f00b8318e9e6c09289cfa56bcfd0595bfa59ac64d",
    ),
    "drive": GymService(
        8005,
        "app.main:app",
        f"{PUBLIC_IMAGE_PREFIX}-drive@sha256:3475962fcf6da7675e194dbf138de01fa3e96134a302ad47316e4111a5e63f32",
    ),
}

SET_ZERO_CONTAINERS = {name: "eog-itsm-opened-by-patch" if name == "itsm" else f"eog-{name}" for name in SERVER_NAMES}


def gym_set_host_port(gym_set: int, domain: str) -> int:
    if gym_set < 0:
        raise ValueError("Gym set index must not be negative")
    try:
        domain_index = DOMAIN_INDICES[domain]
    except KeyError as error:
        raise ValueError(f"Unknown Gym domain: {domain}") from error
    return 8000 + 100 * gym_set + domain_index


def gym_set_container_names(gym_set: int) -> dict[str, str]:
    if gym_set < 0:
        raise ValueError("Gym set index must not be negative")
    if gym_set == 0:
        return dict(SET_ZERO_CONTAINERS)
    return {domain: f"eog-set{gym_set}-{domain}" for domain in SERVER_NAMES}


def gym_service_run_command(gym_set: int, domain: str) -> list[str]:
    try:
        service = GYM_SERVICES[domain]
    except KeyError as error:
        raise ValueError(f"Unknown Gym domain: {domain}") from error
    name = gym_set_container_names(gym_set)[domain]
    host_port = gym_set_host_port(gym_set, domain)
    published_port = f"{service.host_bind}:{host_port}:{service.container_port}"
    return [
        "docker",
        "run",
        "-d",
        "--ulimit",
        "nofile=65536:65536",
        "--name",
        name,
        "--workdir",
        "/app",
        "--publish",
        published_port,
        service.image,
        "python",
        "-m",
        "uvicorn",
        service.app,
        "--host",
        "0.0.0.0",
        "--port",
        str(service.container_port),
    ]


def task_for_gym_set(task: Task, gym_set: int) -> Task:
    if gym_set < 0:
        raise ValueError("Gym set index must not be negative")
    if gym_set == 0:
        return task

    domain_by_server = {server_name: domain for domain, server_name in SERVER_NAMES.items()}
    servers = []
    for server in task.gym_servers_config:
        server_name = server["mcp_server_name"]
        try:
            domain = domain_by_server[server_name]
        except KeyError as error:
            raise ValueError(f"No Gym set mapping for server: {server_name}") from error
        parsed = urlsplit(server["mcp_server_url"])
        if not parsed.scheme or not parsed.hostname:
            raise ValueError(f"Invalid Gym server URL: {server['mcp_server_url']}")
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        userinfo = f"{parsed.netloc.rsplit('@', 1)[0]}@" if "@" in parsed.netloc else ""
        netloc = f"{userinfo}{host}:{gym_set_host_port(gym_set, domain)}"
        servers.append({**server, "mcp_server_url": urlunsplit(parsed._replace(netloc=netloc))})
    return replace(task, gym_servers_config=tuple(servers))
