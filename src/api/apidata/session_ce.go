
package apidata

import "github.com/getzep/zep/models"

func transformSession(in *models.Session, out *Session) {}

func SessionSearchResultTransformer(result models.SessionSearchResult) SessionSearchResult {
	return SessionSearchResult{
		SessionSearchResultCommon: SessionSearchResultCommon{
			Fact: FactTransformerPtr(result.Fact),
		},
	}
}

type Session struct {
	SessionCommon
}

type SessionSearchResult struct {
	SessionSearchResultCommon
}
