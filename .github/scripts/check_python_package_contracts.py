"""Validate the PEP 561 contract of Zep's published Python packages."""

from __future__ import annotations

import argparse
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


def _published_package_dirs() -> list[Path]:
    integrations = sorted(
        path.parent
        for path in (REPO_ROOT / "integrations").glob("*/python/pyproject.toml")
    )
    return [REPO_ROOT / "ingestion", *integrations]


def _load_package_roots(package_dir: Path) -> tuple[str, str, list[Path]]:
    pyproject_path = package_dir / "pyproject.toml"
    if not pyproject_path.is_file():
        raise ValueError(f"{package_dir}: pyproject.toml is missing")

    with pyproject_path.open("rb") as pyproject_file:
        config: dict[str, Any] = tomllib.load(pyproject_file)

    try:
        project = config["project"]
        project_name = project["name"]
        project_version = project["version"]
        package_values = config["tool"]["hatch"]["build"]["targets"]["wheel"][
            "packages"
        ]
    except (KeyError, TypeError) as error:
        raise ValueError(
            f"{pyproject_path}: expected project name/version and "
            "tool.hatch.build.targets.wheel.packages"
        ) from error

    if not isinstance(project_name, str) or not isinstance(project_version, str):
        raise TypeError(f"{pyproject_path}: project name and version must be strings")
    if not isinstance(package_values, list) or not package_values:
        raise ValueError(f"{pyproject_path}: wheel packages must be a non-empty list")

    package_roots: list[Path] = []
    resolved_package_dir = package_dir.resolve()
    for value in package_values:
        if not isinstance(value, str):
            raise TypeError(f"{pyproject_path}: wheel package paths must be strings")
        package_root = (package_dir / value).resolve()
        if not package_root.is_relative_to(resolved_package_dir):
            raise ValueError(
                f"{pyproject_path}: wheel package escapes its project: {value!r}"
            )
        package_roots.append(package_root)

    return project_name, project_version, package_roots


def _check_marker_bytes(label: str, content: bytes, errors: list[str]) -> None:
    if content.strip():
        errors.append(
            f"{label}: inline packages require an empty py.typed marker; "
            "the 'partial' marker is only for partial stub packages"
        )


def _check_source_markers(
    package_dir: Path,
    package_roots: list[Path],
    errors: list[str],
) -> None:
    for package_root in package_roots:
        marker = package_root / "py.typed"
        if not marker.is_file():
            errors.append(f"{marker}: missing PEP 561 marker")
            continue
        _check_marker_bytes(str(marker), marker.read_bytes(), errors)


def _check_wheel(
    wheel_path: Path,
    package_roots: list[Path],
    errors: list[str],
) -> None:
    try:
        with zipfile.ZipFile(wheel_path) as wheel:
            names = set(wheel.namelist())
            for package_root in package_roots:
                marker_name = f"{package_root.name}/py.typed"
                if marker_name not in names:
                    errors.append(f"{wheel_path}: missing {marker_name}")
                    continue
                _check_marker_bytes(
                    f"{wheel_path}:{marker_name}",
                    wheel.read(marker_name),
                    errors,
                )
    except (OSError, zipfile.BadZipFile) as error:
        errors.append(f"{wheel_path}: cannot read wheel: {error}")


def _check_sdist(
    sdist_path: Path,
    package_dir: Path,
    package_roots: list[Path],
    errors: list[str],
) -> None:
    try:
        with tarfile.open(sdist_path, mode="r:gz") as sdist:
            members = {member.name: member for member in sdist.getmembers()}
            for package_root in package_roots:
                relative_marker = (package_root / "py.typed").relative_to(package_dir)
                suffix = f"/{relative_marker.as_posix()}"
                matches = [
                    name
                    for name in members
                    if name == relative_marker.as_posix() or name.endswith(suffix)
                ]
                if len(matches) != 1:
                    errors.append(
                        f"{sdist_path}: expected one {relative_marker.as_posix()}, "
                        f"found {len(matches)}"
                    )
                    continue
                extracted = sdist.extractfile(members[matches[0]])
                if extracted is None:
                    errors.append(
                        f"{sdist_path}:{matches[0]}: marker is not a regular file"
                    )
                    continue
                _check_marker_bytes(
                    f"{sdist_path}:{matches[0]}",
                    extracted.read(),
                    errors,
                )
    except (OSError, tarfile.TarError) as error:
        errors.append(f"{sdist_path}: cannot read source distribution: {error}")


def _check_artifacts(
    artifacts_dir: Path,
    package_dir: Path,
    package_roots: list[Path],
    errors: list[str],
) -> None:
    wheels = sorted(artifacts_dir.glob("*.whl"))
    sdists = sorted(artifacts_dir.glob("*.tar.gz"))
    if len(wheels) != 1:
        errors.append(f"{artifacts_dir}: expected one wheel, found {len(wheels)}")
    else:
        _check_wheel(wheels[0], package_roots, errors)
    if len(sdists) != 1:
        errors.append(
            f"{artifacts_dir}: expected one source distribution, found {len(sdists)}"
        )
    else:
        _check_sdist(sdists[0], package_dir, package_roots, errors)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Check py.typed source markers for published Python packages and, "
            "optionally, verify one built wheel and source distribution."
        )
    )
    parser.add_argument(
        "packages",
        nargs="*",
        type=Path,
        help="Package directories; defaults to ingestion and every integrations/*/python",
    )
    parser.add_argument(
        "--artifacts",
        type=Path,
        help="Directory containing one wheel and one sdist (requires exactly one package)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    package_dirs = (
        [package.resolve() for package in args.packages]
        if args.packages
        else _published_package_dirs()
    )
    if args.artifacts is not None and len(package_dirs) != 1:
        print("--artifacts requires exactly one package directory", file=sys.stderr)
        return 2

    errors: list[str] = []
    checked: list[str] = []
    for package_dir in package_dirs:
        try:
            project_name, project_version, package_roots = _load_package_roots(
                package_dir
            )
        except (OSError, TypeError, ValueError, tomllib.TOMLDecodeError) as error:
            errors.append(str(error))
            continue

        _check_source_markers(package_dir, package_roots, errors)
        if args.artifacts is not None:
            _check_artifacts(
                args.artifacts.resolve(),
                package_dir,
                package_roots,
                errors,
            )
        checked.append(f"{project_name} {project_version}")

    if errors:
        for message in errors:
            print(f"error: {message}", file=sys.stderr)
        return 1

    print(f"PEP 561 package contract passed: {', '.join(checked)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
