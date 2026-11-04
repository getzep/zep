from __future__ import annotations

import hashlib
import importlib
import inspect
from functools import wraps
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import create_model
from sqlalchemy import Column, Text, text

PATCH_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
PATCH_MARKER = f"EOG_ITSM_OPENED_BY_PATCH_ACTIVE:{PATCH_SHA256}"
OPENED_BY_DESCRIPTION = (
    "ID of the user who submits the incident. If you omit it, the server sets it to the signed-in user."
)


def _extend_schema(schema_module: Any, schema_name: str) -> None:
    schema_class = getattr(schema_module, schema_name)
    if "opened_by" in schema_class.model_fields:
        return
    extended_class = create_model(
        schema_name,
        __base__=schema_class,
        __module__=schema_class.__module__,
        opened_by=(str | None, None),
    )
    setattr(schema_module, schema_name, extended_class)


def _patch_incident_model(model_module: Any) -> None:
    incident_model = model_module.Incident
    if "opened_by" not in incident_model.__table__.columns:
        incident_model.__table__.append_column(Column("opened_by", Text, nullable=True))
        incident_model.__mapper__.add_property("opened_by", incident_model.__table__.c.opened_by)

    original_to_dict = incident_model.to_dict
    if getattr(original_to_dict, "__eog_opened_by_patch_sha256__", None) == PATCH_SHA256:
        return

    @wraps(original_to_dict)
    def to_dict_with_opened_by(self: Any) -> dict[str, Any]:
        result = original_to_dict(self)
        result["opened_by"] = self.opened_by
        return result

    to_dict_with_opened_by.__eog_opened_by_patch_sha256__ = PATCH_SHA256
    incident_model.to_dict = to_dict_with_opened_by


def _patch_session_manager(session_module: Any) -> None:
    manager_class = session_module.ITSMSessionManager
    original_get_session = manager_class.get_session
    if getattr(original_get_session, "__eog_opened_by_patch_sha256__", None) == PATCH_SHA256:
        return
    column_lock = Lock()

    @wraps(original_get_session)
    def get_session_with_opened_by(self: Any, database_id: str) -> Any:
        session = original_get_session(self, database_id)
        try:
            with column_lock:
                columns = session.execute(text("PRAGMA table_info(incident)")).fetchall()
                column_names = {row[1] for row in columns}
                if columns and "opened_by" not in column_names:
                    session.execute(text("ALTER TABLE incident ADD COLUMN opened_by TEXT"))
                    session.commit()
        except Exception:
            session.close()
            raise
        return session

    get_session_with_opened_by.__eog_opened_by_patch_sha256__ = PATCH_SHA256
    manager_class.get_session = get_session_with_opened_by


def _patch_incident_validation(manager_module: Any) -> None:
    manager_class = manager_module.IncidentManager
    original_validate = manager_class._validate_referenced_entities
    if getattr(original_validate, "__eog_opened_by_patch_sha256__", None) == PATCH_SHA256:
        return

    @wraps(original_validate)
    def validate_opened_by(self: Any, incident_data: dict[str, Any], org_id: str) -> list[str]:
        errors = original_validate(self, incident_data, org_id)
        opened_by = incident_data.get("opened_by")
        if opened_by is not None:
            for error in original_validate(self, {"caller_id": opened_by}, org_id):
                if error == "caller_id is required":
                    errors.append("opened_by is required")
                else:
                    errors.append(error.replace("caller_id:", "opened_by:", 1))
        return errors

    validate_opened_by.__eog_opened_by_patch_sha256__ = PATCH_SHA256
    manager_class._validate_referenced_entities = validate_opened_by


def _patch_incident_creation(manager_module: Any) -> None:
    manager_class = manager_module.IncidentManager
    original_create = manager_class.create_incident
    if getattr(original_create, "__eog_opened_by_patch_sha256__", None) == PATCH_SHA256:
        return

    @wraps(original_create)
    def create_with_opened_by(self: Any, incident_data: dict[str, Any], org_id: str | None = None) -> Any:
        opened_by = incident_data.get("opened_by")
        if isinstance(opened_by, str):
            incident_data["opened_by"] = opened_by.strip()
        return original_create(self, incident_data, org_id)

    create_with_opened_by.__eog_opened_by_patch_sha256__ = PATCH_SHA256
    manager_class.create_incident = create_with_opened_by


def _default_opened_by(original: Any) -> Any:
    if getattr(original, "__eog_opened_by_patch_sha256__", None) == PATCH_SHA256:
        return original
    signature = inspect.signature(original)

    def set_authenticated_user(args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        bound = signature.bind_partial(*args, **kwargs)
        incident_data = bound.arguments["incident_data"]
        user_context = bound.arguments["user_context"]
        if incident_data.opened_by is None:
            incident_data.opened_by = user_context[1].user_id

    if inspect.iscoroutinefunction(original):

        @wraps(original)
        async def create_incident_with_opened_by(*args: Any, **kwargs: Any) -> Any:
            set_authenticated_user(args, kwargs)
            return await original(*args, **kwargs)

    else:

        @wraps(original)
        def create_incident_with_opened_by(*args: Any, **kwargs: Any) -> Any:
            set_authenticated_user(args, kwargs)
            return original(*args, **kwargs)

    create_incident_with_opened_by.__eog_opened_by_patch_sha256__ = PATCH_SHA256
    return create_incident_with_opened_by


def _patch_create_incident_route(router_module: Any) -> None:
    original = router_module.create_incident
    patched = _default_opened_by(original)
    if patched is original:
        return
    router_module.create_incident = patched
    for route in router_module.router.routes:
        if getattr(route, "endpoint", None) is original:
            route.endpoint = patched
            route.dependant.call = patched


def _patch_tool(tool: Any) -> Any:
    schema = dict(tool.inputSchema)
    properties = dict(schema.get("properties", {}))
    properties["opened_by"] = {
        "type": ["string", "null"],
        "description": OPENED_BY_DESCRIPTION,
    }
    schema["properties"] = properties
    description = tool.description
    optional_parameters = "Optional Parameters (all can be null/omitted):"
    if optional_parameters in description:
        description = description.replace(
            optional_parameters,
            f"{optional_parameters}\n          - opened_by: {OPENED_BY_DESCRIPTION}",
            1,
        )
    return tool.model_copy(update={"description": description, "inputSchema": schema})


def _patch_create_incident_tools(tools_module: Any, handlers_module: Any) -> None:
    def patch_tools(tools: list[Any]) -> list[Any]:
        return [_patch_tool(tool) if getattr(tool, "name", None) == "create_incident" else tool for tool in tools]

    tools_module.INCIDENTS_TOOLS[:] = patch_tools(tools_module.INCIDENTS_TOOLS)
    handlers_module.MCP_TOOLS_LIST = patch_tools(handlers_module.MCP_TOOLS_LIST)


schema_module = importlib.import_module("schemas.incident")
_extend_schema(schema_module, "IncidentCreate")
_extend_schema(schema_module, "IncidentResponse")

model_module = importlib.import_module("database.models.incident")
_patch_incident_model(model_module)

session_module = importlib.import_module("database.session_manager")
_patch_session_manager(session_module)

manager_module = importlib.import_module("database.managers.incident_manager")
_patch_incident_validation(manager_module)
_patch_incident_creation(manager_module)

router_module = importlib.import_module("apis.incidents.router")
_patch_create_incident_route(router_module)

tools_module = importlib.import_module("tools.incidents")
handlers_module = importlib.import_module("handlers.tool_handlers")
_patch_create_incident_tools(tools_module, handlers_module)

print(PATCH_MARKER, flush=True)
