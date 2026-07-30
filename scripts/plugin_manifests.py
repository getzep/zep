#!/usr/bin/env python3
"""Keep the building-with-zep plugin's duplicated manifest values in agreement.

One plugin directory is published into three ecosystems, which forces two values
to exist in more than one file:

- **The version**, in three files — one plugin manifest per ecosystem. Claude
  Code uses it for update detection; all three ecosystems expose the same release
  identity to users and maintainers.
- **The zep-docs MCP URL**, in two — `.mcp.json` (Claude, Codex) and `mcp.json`
  (Cursor) declare the same server in two different shapes, so moving the
  endpoint means editing both.

No ecosystem's validator catches drift in either value, hence this script.

    python3 scripts/plugin_manifests.py --check     # assert agreement (CI)
    python3 scripts/plugin_manifests.py set 0.3.0   # bump the version everywhere
    python3 scripts/plugin_manifests.py version     # print the current version
    python3 scripts/plugin_manifests.py require-newer 0.3.0 0.2.0

It lives outside plugins/ on purpose: a plugin directory is copied wholesale
into every user's plugin cache, and maintainer tooling has no business there.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_NAME = "building-with-zep"

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
# Every managed file holds exactly one "version" key, which lets the rewrite be a
# scoped text substitution that preserves the file's existing formatting.
VERSION_KEY_RE = re.compile(r'("version"\s*:\s*")([^"]*)(")')

# (path, kind, label). kind "manifest" reads the top-level version; kind "entry"
# reads the version of the PLUGIN_NAME entry in a marketplace's plugins array.
VERSION_SITES: list[tuple[str, str, str]] = [
    ("plugins/building-with-zep/.claude-plugin/plugin.json", "manifest", "Claude plugin manifest"),
    ("plugins/building-with-zep/.codex-plugin/plugin.json", "manifest", "Codex plugin manifest"),
    ("plugins/building-with-zep/.cursor-plugin/plugin.json", "manifest", "Cursor plugin manifest"),
]

# Places a version must never appear. Each ecosystem resolves the release version
# from its plugin.json; a second marketplace value is redundant and can drift.
FORBIDDEN_SITES: list[tuple[str, str, str]] = [
    (
        ".claude-plugin/marketplace.json",
        "entry",
        "Claude Code always prefers plugin.json's version, so a value here is dead weight "
        "that can mask a real bump",
    ),
    (
        ".claude-plugin/marketplace.json",
        "metadata",
        "the marketplace manifest's own version is a legacy field nothing consumes, and it "
        "reads as the plugin's version",
    ),
    (
        ".agents/plugins/marketplace.json",
        "entry",
        "Codex resolves the version from .codex-plugin/plugin.json, so a second value "
        "can only drift",
    ),
    (
        ".cursor-plugin/marketplace.json",
        "entry",
        "Cursor resolves the version from .cursor-plugin/plugin.json, so a second value "
        "can only drift",
    ),
]

# The zep-docs endpoint, declared once per MCP config shape. See the README's
# "MCP config: two files, one server" for why the shapes differ.
MCP_SITES: list[tuple[str, str]] = [
    ("plugins/building-with-zep/.mcp.json", "Claude + Codex (.mcp.json)"),
    ("plugins/building-with-zep/mcp.json", "Cursor (mcp.json)"),
]
MCP_SERVER_NAME = "zep-docs"


class SiteError(Exception):
    """A manifest is missing, unparseable, or not shaped the way this script expects."""


def parse_semver(version: str) -> tuple[tuple[int, int, int], tuple[str, ...] | None]:
    """Parse a strict SemVer string into the parts that determine precedence."""
    match = SEMVER_RE.fullmatch(version)
    if match is None:
        raise ValueError(f"'{version}' is not a semantic version (MAJOR.MINOR.PATCH)")
    core = tuple(int(match.group(index)) for index in (1, 2, 3))
    prerelease = match.group(4)
    return core, tuple(prerelease.split(".")) if prerelease is not None else None


def compare_semver(left: str, right: str) -> int:
    """Return -1, 0, or 1 according to SemVer precedence (build metadata ignored)."""
    left_core, left_pre = parse_semver(left)
    right_core, right_pre = parse_semver(right)
    if left_core != right_core:
        return 1 if left_core > right_core else -1
    if left_pre is None or right_pre is None:
        if left_pre is right_pre:
            return 0
        return 1 if left_pre is None else -1

    for left_part, right_part in zip(left_pre, right_pre):
        if left_part == right_part:
            continue
        left_numeric = left_part.isdigit()
        right_numeric = right_part.isdigit()
        if left_numeric and right_numeric:
            return 1 if int(left_part) > int(right_part) else -1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return 1 if left_part > right_part else -1
    if len(left_pre) == len(right_pre):
        return 0
    return 1 if len(left_pre) > len(right_pre) else -1


def _load(rel_path: str) -> tuple[Path, str, object]:
    path = REPO_ROOT / rel_path
    try:
        text = path.read_text()
    except OSError as exc:
        raise SiteError(f"{rel_path}: cannot read ({exc})") from exc
    try:
        return path, text, json.loads(text)
    except json.JSONDecodeError as exc:
        raise SiteError(f"{rel_path}: invalid JSON ({exc})") from exc


def _entry(rel_path: str, data: object) -> dict:
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, list):
        raise SiteError(f"{rel_path}: expected a top-level 'plugins' array")
    for entry in plugins:
        if isinstance(entry, dict) and entry.get("name") == PLUGIN_NAME:
            return entry
    raise SiteError(f"{rel_path}: no plugins entry named '{PLUGIN_NAME}'")


def read_version(rel_path: str, kind: str) -> str:
    """Return the version declared at a site, or raise if it declares none."""
    _, _, data = _load(rel_path)
    holder = data if kind == "manifest" else _entry(rel_path, data)
    version = holder.get("version") if isinstance(holder, dict) else None
    if not isinstance(version, str) or not version:
        raise SiteError(f"{rel_path}: no version declared ({kind})")
    return version


def find_version(rel_path: str, kind: str) -> str | None:
    """Return the version declared at a site, or None. Used for forbidden sites."""
    _, _, data = _load(rel_path)
    if kind == "metadata":
        holder = data.get("metadata") if isinstance(data, dict) else None
    elif kind == "manifest":
        holder = data
    else:
        try:
            holder = _entry(rel_path, data)
        except SiteError:
            return None
    if not isinstance(holder, dict):
        return None
    version = holder.get("version")
    return version if isinstance(version, str) else None


def read_mcp_url(rel_path: str) -> str:
    """Return the MCP_SERVER_NAME url declared in an MCP config file."""
    _, _, data = _load(rel_path)
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise SiteError(f"{rel_path}: expected a top-level 'mcpServers' object")
    server = servers.get(MCP_SERVER_NAME)
    if not isinstance(server, dict):
        raise SiteError(f"{rel_path}: no '{MCP_SERVER_NAME}' server declared")
    url = server.get("url")
    if not isinstance(url, str) or not url:
        raise SiteError(f"{rel_path}: '{MCP_SERVER_NAME}' declares no url")
    return url


def check() -> int:
    """Report the managed values; fail on drift, a missing site, or a stray version."""
    problems: list[str] = []
    versions: dict[str, str] = {}
    urls: dict[str, str] = {}

    for rel_path, kind, label in VERSION_SITES:
        try:
            versions[label] = read_version(rel_path, kind)
        except SiteError as exc:
            problems.append(str(exc))
    for rel_path, label in MCP_SITES:
        try:
            urls[label] = read_mcp_url(rel_path)
        except SiteError as exc:
            problems.append(str(exc))

    print("version")
    for label, version in versions.items():
        print(f"  {version:<12} {label}")
    print(f"{MCP_SERVER_NAME} url")
    for label, url in urls.items():
        print(f"  {url}  {label}")

    distinct = set(versions.values())
    if len(distinct) > 1:
        problems.append(
            "version drift across manifests: "
            + ", ".join(f"{label}={v}" for label, v in sorted(versions.items()))
            + " — choose the intended release and run: "
            "python3 scripts/plugin_manifests.py set <version>"
        )
    for version in distinct:
        try:
            parse_semver(version)
        except ValueError as exc:
            problems.append(str(exc))

    if len(set(urls.values())) > 1:
        problems.append(
            f"'{MCP_SERVER_NAME}' points at different endpoints: "
            + ", ".join(f"{label}={u}" for label, u in sorted(urls.items()))
            + " — both MCP configs must name the same server"
        )

    for rel_path, kind, why in FORBIDDEN_SITES:
        try:
            stray = find_version(rel_path, kind)
        except SiteError as exc:
            problems.append(str(exc))
            continue
        if stray is not None:
            problems.append(f"{rel_path}: remove the {kind} 'version' ({stray}) — {why}")

    if problems:
        sys.stdout.flush()
        print("\nFAIL", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(
        f"\nOK — {len(versions)} manifests agree on {distinct.pop()}, "
        f"{len(urls)} MCP configs agree on one endpoint"
    )
    return 0


def set_version(new_version: str) -> int:
    """Rewrite every managed site to new_version, preserving each file's formatting."""
    try:
        parse_semver(new_version)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    pending: list[tuple[Path, str, str, str]] = []
    problems: list[str] = []

    # Resolve every edit before writing anything, so a bad manifest can't leave
    # the tree half-bumped.
    for rel_path, kind, label in VERSION_SITES:
        try:
            path, text, data = _load(rel_path)
            holder = data if kind == "manifest" else _entry(rel_path, data)
            old = holder.get("version") if isinstance(holder, dict) else None
            if not isinstance(old, str) or not old:
                raise SiteError(f"{rel_path}: no version declared ({kind})")
            try:
                direction = compare_semver(new_version, old)
            except ValueError as exc:
                raise SiteError(f"{rel_path}: {exc}") from exc
            if direction < 0:
                raise SiteError(
                    f"{rel_path}: refusing to decrease the version from {old} to {new_version}"
                )

            matches = VERSION_KEY_RE.findall(text)
            if len(matches) != 1:
                raise SiteError(
                    f"{rel_path}: found {len(matches)} 'version' keys, expected exactly 1 — "
                    "this script rewrites the only one in the file; generalize it before "
                    "adding another"
                )

            new_text = VERSION_KEY_RE.sub(
                lambda m: f"{m.group(1)}{new_version}{m.group(3)}", text, count=1
            )
            written = json.loads(new_text)
            landed = (
                written if kind == "manifest" else _entry(rel_path, written)
            ).get("version")
            if landed != new_version:
                raise SiteError(f"{rel_path}: rewrite landed on {landed!r}, not {new_version!r}")

            pending.append((path, label, old, new_text))
        except SiteError as exc:
            problems.append(str(exc))

    if problems:
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("\nnothing written", file=sys.stderr)
        return 1

    for path, label, old, new_text in pending:
        if old == new_version:
            print(f"  unchanged  {new_version:<12} {label}")
            continue
        path.write_text(new_text)
        print(f"  {old} -> {new_version:<8} {label}")

    print(f"\nBumped {PLUGIN_NAME} to {new_version}. Next:")
    print("  1. add a CHANGELOG.md entry describing what users get")
    print("  2. claude plugin validate plugins/building-with-zep --strict")
    print("  3. open the PR — merging it is the release; plugins are not tagged")
    return 0


def require_newer(new_version: str, old_version: str) -> int:
    """Fail unless new_version has strictly greater SemVer precedence."""
    try:
        direction = compare_semver(new_version, old_version)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if direction <= 0:
        print(
            f"{new_version} is not newer than {old_version}; "
            "plugin releases must increase the semantic version",
            file=sys.stderr,
        )
        return 1
    print(f"{old_version} -> {new_version}")
    return 0


def main(argv: list[str]) -> int:
    if argv[1:] == ["--check"]:
        return check()
    if len(argv) == 3 and argv[1] == "set":
        return set_version(argv[2])
    if argv[1:] == ["version"]:
        # Bare version on stdout, for scripting. VERSION_SITES[0] is canonical;
        # --check is what proves the others match it.
        rel_path, kind, _ = VERSION_SITES[0]
        try:
            print(read_version(rel_path, kind))
        except SiteError as exc:
            print(exc, file=sys.stderr)
            return 1
        return 0
    if len(argv) == 4 and argv[1] == "require-newer":
        return require_newer(argv[2], argv[3])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
