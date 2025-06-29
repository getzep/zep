
package models

type SessionSearchResult struct {
	SessionSearchResultCommon
}

type SessionSearchQuery struct {
	SessionSearchQueryCommon
}

func (s SessionSearchQuery) BreadcrumbFields() map[string]any {
	return map[string]any{}
}
