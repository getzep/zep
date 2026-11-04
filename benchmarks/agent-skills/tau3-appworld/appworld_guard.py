from __future__ import annotations

import ast
import json
import re
import string

GUARD_PREFIX = "Execution failed. Traceback:\n"

EXPECTED_ALLOWED_MODULES = frozenset(
    {
        "appworld",
        "array",
        "bisect",
        "builtins",
        "calendar",
        "collections",
        "collections.abc",
        "contextlib",
        "copy",
        "csv",
        "datetime",
        "difflib",
        "enum",
        "fractions",
        "functools",
        "heapq",
        "itertools",
        "json",
        "math",
        "numbers",
        "operator",
        "os",
        "pathlib",
        "pendulum",
        "pprint",
        "random",
        "re",
        "string",
        "textwrap",
        "time",
        "typing",
        "unittest",
        "uuid",
        "yaml",
    }
)
HARNESS_ALLOWED_IMPORTS = frozenset(
    {
        "array",
        "bisect",
        "calendar",
        "collections",
        "collections.abc",
        "csv",
        "datetime",
        "difflib",
        "enum",
        "fractions",
        "functools",
        "heapq",
        "itertools",
        "json",
        "math",
        "numbers",
        "pendulum",
        "pprint",
        "random",
        "re",
        "string",
        "textwrap",
        "time",
        "typing",
        "uuid",
    }
    | ({"decimal", "statistics"} & EXPECTED_ALLOWED_MODULES)
)
EXPECTED_DISALLOWED_FUNCTION_PATHS = frozenset(
    {
        "builtins.breakpoint",
        "builtins.exit",
        "builtins.open",
        "builtins.quit",
        "bz2.open",
        "codecs.open",
        "csv.DictReader",
        "csv.DictWriter",
        "csv.reader",
        "csv.writer",
        "dbm.close",
        "dbm.open",
        "fileinput.close",
        "fileinput.filename",
        "fileinput.input",
        "fileinput.lineno",
        "fileinput.nextfile",
        "glob.glob",
        "glob.iglob",
        "gzip.open",
        "io.open",
        "io.write",
        "json.dump",
        "json.load",
        "os._exit",
        "os.chdir",
        "os.chmod",
        "os.chown",
        "os.chroot",
        "os.close",
        "os.fchdir",
        "os.fchmod",
        "os.fchown",
        "os.fork",
        "os.forkpty",
        "os.kill",
        "os.killpg",
        "os.lchflags",
        "os.lchmod",
        "os.lchown",
        "os.listdir",
        "os.open",
        "os.putenv",
        "os.read",
        "os.remove",
        "os.removedirs",
        "os.rename",
        "os.renames",
        "os.replace",
        "os.rmdir",
        "os.setuid",
        "os.system",
        "os.truncate",
        "os.unlink",
        "os.walk",
        "os.write",
        "pathlib.Path.chmod",
        "pathlib.Path.chown",
        "pathlib.Path.expanduser",
        "pathlib.Path.lchmod",
        "pathlib.Path.lchown",
        "pathlib.Path.link_to",
        "pathlib.Path.mkdir",
        "pathlib.Path.open",
        "pathlib.Path.read_bytes",
        "pathlib.Path.rename",
        "pathlib.Path.replace",
        "pathlib.Path.rmdir",
        "pathlib.Path.symlink_to",
        "pathlib.Path.touch",
        "pathlib.Path.unlink",
        "pathlib.Path.write_bytes",
        "pathlib.Path.write_text",
        "pdb.set_trace",
        "pickle.dump",
        "pickle.load",
        "shelve.open",
        "shutil.chown",
        "shutil.copy",
        "shutil.copy2",
        "shutil.copyfile",
        "shutil.copytree",
        "shutil.get_archive_formats",
        "shutil.make_archive",
        "shutil.move",
        "shutil.rmtree",
        "sqlite3.connect",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.getoutput",
        "subprocess.getstatusoutput",
        "subprocess.run",
        "sys.exit",
        "tarfile.open",
        "tempfile.NamedTemporaryFile",
        "tempfile.TemporaryDirectory",
        "tempfile.mkdtemp",
        "tempfile.mkstemp",
        "tempfile.mktemp",
        "time.sleep",
        "zipfile.ZipFile",
    }
)

_OS_FUNCTIONS = {"environ", "getenv", "putenv"}
_BUILTIN_FUNCTIONS = {
    "breakpoint",
    "compile",
    "delattr",
    "eval",
    "exec",
    "getattr",
    "globals",
    "locals",
    "open",
    "setattr",
    "vars",
    "__import__",
}
_GUARD_SINK_NAMES = _BUILTIN_FUNCTIONS | {"attrgetter", "methodcaller", "Formatter"}
_GUARD_SINK_ATTRIBUTES = _GUARD_SINK_NAMES | {"format_map"}
SHELL_ESCAPE_NAMES = frozenset(
    {
        "get_ipython",
        "ex",
        "ev",
        "run_cell",
        "run_line_magic",
        "run_cell_magic",
        "system",
        "getoutput",
        "user_ns",
        "user_global_ns",
        "user_module",
        "builtin_trap",
        "magics_manager",
        "builtins",
        "requester",
        "Requester",
        "ApiCollection",
        "open",
        "breakpoint",
        "input",
        "help",
        "memoryview",
    }
)
RUNTIME_GUARD_MARKERS = (
    "ground_truth",
    "__globals__",
    "serve.environment",
    "__subclasses__",
    "get_original",
    "sys.modules",
    "unittest",
    "importlib",
    "get_ipython",
    "run_cell",
    "user_ns",
    ".ex(",
)
BANNED_CODE_NAMES = frozenset(_GUARD_SINK_NAMES | _GUARD_SINK_ATTRIBUTES | SHELL_ESCAPE_NAMES)


def runtime_guard_matches(code: str) -> list[str]:
    lowered = code.casefold()
    matches = []
    for marker in RUNTIME_GUARD_MARKERS:
        normalized = marker.casefold()
        if normalized.startswith(".") or "." in normalized:
            found = normalized in lowered
        else:
            found = re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", lowered) is not None
        if found:
            matches.append(marker)
    return matches


def _unsafe_format_field(template: str) -> bool:
    try:
        parsed = string.Formatter().parse(template)
        for _, field_name, format_spec, _ in parsed:
            if field_name is not None and "." in field_name:
                return True
            if format_spec and _unsafe_format_field(format_spec):
                return True
    except ValueError:
        return False
    return False


def _position(node: ast.AST) -> tuple[int, int]:
    return (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))


def _ordered_nodes[Node: ast.AST](
    tree: ast.AST, node_type: type[Node] | tuple[type[Node], ...]
) -> list[Node]:
    return sorted(
        (node for node in ast.walk(tree) if isinstance(node, node_type)),
        key=_position,
    )


def _guard_message(kind: str, identifier: str) -> str:
    return f"{GUARD_PREFIX}Usage of the following {kind} is not allowed: {identifier}."


def _is_os_function(name: str) -> bool:
    return name in _OS_FUNCTIONS or name.startswith(("spawn", "exec", "fork"))


def check_agent_code(code: str) -> str | None:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    for node in _ordered_nodes(tree, (ast.Import, ast.ImportFrom)):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        else:
            if node.level != 0 or not node.module:
                return _guard_message("module", node.module or "." * node.level)
            modules = [node.module]
        for module in modules:
            if module not in HARNESS_ALLOWED_IMPORTS:
                return _guard_message("module", module)

    for node in _ordered_nodes(tree, ast.ImportFrom):
        if node.module == "os" and node.level == 0:
            for alias in node.names:
                if _is_os_function(alias.name):
                    return _guard_message("function", f"os.{alias.name}")
                if alias.name == "*" and any(
                    isinstance(item, ast.Name) and item.id in _OS_FUNCTIONS
                    for item in ast.walk(tree)
                ):
                    return _guard_message("function", "os.*")

    for node in _ordered_nodes(tree, ast.Constant):
        value = node.value
        if isinstance(value, str) and "__" in value or isinstance(value, bytes) and b"__" in value:
            return _guard_message("string literal", "double underscore")

    for node in _ordered_nodes(tree, (ast.Name, ast.Attribute)):
        identifier = node.id if isinstance(node, ast.Name) else node.attr
        if (
            isinstance(node, ast.Attribute)
            and len(identifier) > 4
            and identifier.startswith("__")
            and identifier.endswith("__")
        ):
            return _guard_message("name", identifier)
        is_re_compile = (
            isinstance(node, ast.Attribute)
            and node.attr == "compile"
            and isinstance(node.value, ast.Name)
            and node.value.id == "re"
        )
        if identifier in _GUARD_SINK_NAMES and not is_re_compile:
            return _guard_message("function", identifier)
        if identifier in SHELL_ESCAPE_NAMES and not (
            isinstance(node, ast.Name) and identifier in {"ex", "ev"}
        ):
            return _guard_message("name", identifier)
        if (
            isinstance(node, ast.Attribute)
            and node.attr in _GUARD_SINK_ATTRIBUTES
            and not is_re_compile
        ):
            return _guard_message("attribute", node.attr)

    for node in _ordered_nodes(tree, (ast.Import, ast.ImportFrom)):
        aliases = node.names
        for alias in aliases:
            if alias.name in _GUARD_SINK_NAMES or alias.name == "Formatter":
                return _guard_message("function", alias.name)

    for node in sorted(
        (item for item in ast.walk(tree) if isinstance(item, (ast.Name, ast.Attribute))),
        key=_position,
    ):
        identifier = node.id if isinstance(node, ast.Name) else node.attr
        if len(identifier) > 4 and identifier.startswith("__") and identifier.endswith("__"):
            return _guard_message("name", identifier)

    for node in _ordered_nodes(tree, ast.Name):
        if isinstance(node.ctx, ast.Load) and node.id in _BUILTIN_FUNCTIONS:
            return _guard_message("function", node.id)

    for node in _ordered_nodes(tree, ast.Attribute):
        if node.attr == "format" and isinstance(node.value, ast.Name) and node.value.id == "str":
            return _guard_message("attribute", "str.format")
        if node.attr == "format" and isinstance(node.ctx, ast.Load):
            if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                return _guard_message("attribute", "format")
            parent_calls = [
                item for item in ast.walk(tree) if isinstance(item, ast.Call) and item.func is node
            ]
            if parent_calls and _unsafe_format_field(node.value.value):
                return _guard_message("attribute", "format")

    module_aliases: set[str] = set()
    for node in _ordered_nodes(tree, ast.Import):
        for alias in node.names:
            if alias.asname:
                if alias.name in {"os", "posix", "nt"}:
                    module_aliases.add(alias.asname)
            else:
                module_name = alias.name.split(".", 1)[0]
                if module_name in {"os", "posix", "nt"}:
                    module_aliases.add(module_name)
    for node in _ordered_nodes(tree, ast.Attribute):
        if (
            isinstance(node.value, ast.Name)
            and node.value.id in module_aliases
            and _is_os_function(node.attr)
        ):
            return _guard_message("function", f"{node.value.id}.{node.attr}")

    direct_call_names = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    for node in _ordered_nodes(tree, ast.Name):
        if node.id == "open" and id(node) not in direct_call_names:
            return _guard_message("function", "open")

    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute)):
            if (
                node.id if isinstance(node, ast.Name) else node.attr
            ) == "safety_guard" and isinstance(node.ctx, ast.Store | ast.Del):
                return _guard_message("name", "safety_guard")
        elif (
            isinstance(node, (ast.Global, ast.Nonlocal))
            and "safety_guard" in node.names
            or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == "safety_guard"
        ):
            return _guard_message("name", "safety_guard")

    return None


def guard_state_code() -> str:
    return """import json
from pathlib import Path
from appworld.common import code_parsing, safety_guard

def fnv1a(text):
    value = 2166136261
    for byte in text.encode():
        value = ((value ^ byte) * 16777619) & 0xffffffff
    return value

guard_text = Path(safety_guard.__file__).read_text()
parsing_text = Path(code_parsing.__file__).read_text()
state = {
    "allowed": sorted(safety_guard.ALLOWED_MODULE_NAMES),
    "disallowed": sorted(safety_guard.DISALLOWED_FUNCTION_PATHS),
    "guard_fnv": fnv1a(guard_text),
    "guard_len": len(guard_text),
    "parsing_fnv": fnv1a(parsing_text),
    "parsing_len": len(parsing_text),
}
print("APPWORLD_GUARD_STATE " + json.dumps(state, sort_keys=True))
"""


def parse_guard_state(output: str) -> str:
    prefix = "APPWORLD_GUARD_STATE "
    line = next((line for line in output.splitlines() if line.startswith(prefix)), None)
    if line is None:
        raise ValueError("AppWorld guard-state probe did not return a state line")
    state = json.loads(line[len(prefix) :])
    return json.dumps(state, sort_keys=True, separators=(",", ":"))


def env_probe_code() -> str:
    return """import json
import os

markers = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH")
names = sorted(name for name in os.environ if any(marker in name.upper() for marker in markers))
print("APPWORLD_ENV_NAMES " + json.dumps(names))
"""


def parse_secret_env_names(output: str) -> list[str]:
    prefix = "APPWORLD_ENV_NAMES "
    line = next((line for line in output.splitlines() if line.startswith(prefix)), None)
    if line is None:
        raise ValueError("AppWorld environment probe did not return a names line")
    names = json.loads(line[len(prefix) :])
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError("AppWorld environment probe returned invalid names")
    return names
