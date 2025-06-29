
package telemetry

import (
	"bytes"
	"encoding/json"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/getzep/zep/lib/config"
)

const installIDFilePermissions = 0o644

type Request interface {
	RequestCommon
}

func Setup() {
	if _instance != nil {
		return
	}

	noop := config.Telemetry().Disabled

	var installID string

	if !noop {
		installID = getInstallID()
	}

	_instance = &service{
		noop:      noop,
		installID: installID,
		orgName:   config.Telemetry().OrganizationName,
	}

	touchInstallIDFile()
}

func Shutdown() {}

type service struct {
	noop      bool
	installID string
	orgName   string
}

func (s *service) TrackEvent(req Request, event Event, metadata ...map[string]any) {
	if s.noop {
		return
	}

	if !isCEEvent(event) {
		return
	}

	ev := CEEvent{
		Event: event,
	}

	if s.installID != "" {
		ev.InstallID = s.installID
	}

	if s.orgName != "" {
		ev.OrgName = s.orgName
	}

	if len(metadata) > 0 {
		ev.Data = metadata[0]
	}

	b, _ := json.Marshal(ev)
	request, _ := http.NewRequest("POST", apiEndpoint, bytes.NewBuffer(b))

	_, err := http.DefaultClient.Do(request)
	if err != nil {
		// if we error, make it noop so we don't continue to try and error
		s.noop = true
	}
}

const (
	installIDFile = "/tmp/_zep"
	unknownID     = "UNKNOWN"

	apiEndpoint = "https://api.getzep.com/api/v2/telemetry"
)

func touchInstallIDFile() {
	go func() {
		t := time.NewTicker(1 * time.Hour)

		for {
			<-t.C

			if _, err := os.Stat(installIDFile); os.IsNotExist(err) {
				return
			}

			os.ReadFile(installIDFile) //nolint:errcheck,revive // we don't care if this fails
		}
	}()
}

func getInstallID() string {
	if _, err := os.Stat(installIDFile); os.IsNotExist(err) {
		return createInstallID()
	}

	b, err := os.ReadFile(installIDFile)
	if err != nil {
		return unknownID
	}

	return strings.TrimSpace(string(b))
}

func createInstallID() string {
	id := uuid.New().String()

	err := os.WriteFile(installIDFile, []byte(id), installIDFilePermissions) //nolint:gosec // we want this to be readable by the user
	if err != nil {
		return unknownID
	}

	return id
}

func isCEEvent(event Event) bool {
	return event == Event_CEStart || event == Event_CEStop
}
