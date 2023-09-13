package web

import (
	"context"
	"net/http"

	"github.com/getzep/zep/pkg/models"
	"github.com/uptrace/bun"
)

func NewCollectionList(
	documentStore models.DocumentStore[*bun.DB],
	cursor int64,
	limit int64,
) *CollectionList {
	return &CollectionList{
		DocumentStore: documentStore,
		Cursor:        cursor,
		Limit:         limit,
	}
}

type CollectionList struct {
	DocumentStore models.DocumentStore[*bun.DB]
	Collections   []models.DocumentCollection
	TotalCount    int
	Cursor        int64
	Limit         int64
}

func (c *CollectionList) Get(ctx context.Context, appState *models.AppState) error {
	collections, err := c.DocumentStore.GetCollectionList(ctx)
	if err != nil {
		return err
	}
	c.Collections = collections

	return nil
}

func GetCollectionistHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		collectionList := NewCollectionList(appState.DocumentStore, 0, 0)

		err := collectionList.Get(r.Context(), appState)
		if err != nil {
			log.Errorf("Failed to get collection list: %s", err)
			http.Error(w, "Failed to get user collection", http.StatusInternalServerError)
			return
		}
		log.Debugf("CollectionList: %+v", collectionList)

		page := NewPage(
			"Collections",
			"Collections subtitle",
			"/admin/collections",
			[]string{
				"templates/pages/collections.html",
				"templates/components/content/*.html",
				"templates/components/collections_table.html",
			},
			collectionList,
		)

		page.Render(w, r)
	}
}
