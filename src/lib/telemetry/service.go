package telemetry

import "github.com/google/uuid"

type CEEvent struct {
	Event     Event          `json:"event"`
	InstallID string         `json:"install_id"`
	OrgName   string         `json:"org_name"`
	Data      map[string]any `json:"data,omitempty"`
}

type Service interface {
	TrackEvent(req Request, event Event, metadata ...map[string]any)
}

// this interface is used to avoid needing to have a dependency on the models package.
type RequestCommon interface {
	GetProjectUUID() uuid.UUID
	GetRequestTokenType() string
}

var _instance Service

func I() Service {
	return _instance
}
