
package handlertools

import (
	"net/http"

	"github.com/getzep/zep/lib/config"
	"github.com/getzep/zep/models"
	"github.com/getzep/zep/store"
)

func NewRequestState(r *http.Request, as *models.AppState, opts ...RequestStateOption) (*models.RequestState, error) {
	options := &requestStateOptions{}
	for _, opt := range opts {
		opt.apply(options)
	}

	rs := &models.RequestState{}

	rs.SchemaName = config.Postgres().SchemaName
	rs.ProjectUUID = config.ProjectUUID()
	rs.Memories = store.NewMemoryStore(as, rs)
	rs.Sessions = store.NewSessionDAO(as, rs)
	rs.Users = store.NewUserStore(as, rs)

	return rs, nil
}
