from __future__ import annotations

import hashlib
from functools import wraps
from pathlib import Path
from typing import Any

from database.managers import virtual_event_townhall_manager

PATCH_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
PATCH_MARKER = f"EOG_TEAMS_TOWNHALL_PATCH_ACTIVE:{PATCH_SHA256}"


def _townhall_create_with_read_response(original: Any) -> Any:
    @wraps(original)
    def create_townhall(self: Any, org_id: str, creator_user_id: str, request_data: Any) -> Any:
        result = original(self, org_id, creator_user_id, request_data)
        if result is not None:
            return result

        model = virtual_event_townhall_manager.VirtualEventTownhall
        session = self.session_manager.get_session(self.database_id)
        try:
            row = (
                session.query(model)
                .filter(
                    model.org_id == org_id,
                    model.created_by_user_id == creator_user_id,
                    model.display_name == request_data.displayName,
                    model.description_content_type == request_data.description.contentType,
                    model.description_content == request_data.description.content,
                    model.start_date_time == self._parse_datetime_to_naive_utc(request_data.startDateTime.dateTime),
                    model.start_time_zone == request_data.startDateTime.timeZone,
                    model.end_date_time == self._parse_datetime_to_naive_utc(request_data.endDateTime.dateTime),
                    model.end_time_zone == request_data.endDateTime.timeZone,
                    model.audience == request_data.audience.value,
                    model.is_invite_only == bool(request_data.isInviteOnly),
                )
                .order_by(model.created_at.desc())
                .first()
            )
            townhall_id = row.id if row is not None else None
        finally:
            session.close()

        if townhall_id is None:
            return result
        return self.get_townhall(org_id, townhall_id)

    create_townhall.__eog_townhall_patch_sha256__ = PATCH_SHA256
    return create_townhall


manager_class = virtual_event_townhall_manager.VirtualEventTownhallManager
if getattr(manager_class.create_townhall, "__eog_townhall_patch_sha256__", None) != PATCH_SHA256:
    manager_class.create_townhall = _townhall_create_with_read_response(manager_class.create_townhall)

print(PATCH_MARKER, flush=True)
