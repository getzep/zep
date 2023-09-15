package web

import (
	"context"
	"net/http"

	"github.com/getzep/zep/pkg/models"
	"github.com/go-chi/chi/v5"
	"github.com/uptrace/bun"
)

func NewCollectionList(
	documentStore models.DocumentStore[*bun.DB],
	cursor int64,
	limit int64,
	path string,
) *CollectionList {
	return &CollectionList{
		DocumentStore: documentStore,
		Cursor:        cursor,
		Limit:         limit,
		Path:          path,
	}
}

type CollectionList struct {
	DocumentStore models.DocumentStore[*bun.DB]
	Collections   []models.DocumentCollection
	TotalCount    int
	Cursor        int64
	Limit         int64
	Path          string
}

func (c *CollectionList) Get(ctx context.Context, appState *models.AppState) error {
	collections, err := c.DocumentStore.GetCollectionList(ctx)
	if err != nil {
		return err
	}
	c.Collections = collections

	return nil
}

type CollectionDetails struct {
	*models.DocumentCollection
	Path string
}

func GetCollectionListHandler(appState *models.AppState) http.HandlerFunc {
	const path = "/admin/collections"

	return func(w http.ResponseWriter, r *http.Request) {
		collectionList := NewCollectionList(appState.DocumentStore, 0, 0, path)

		err := collectionList.Get(r.Context(), appState)
		if err != nil {
			handleError(w, err, "failed to get collection list")
			return
		}
		log.Debugf("CollectionList: %+v", collectionList)

		page := NewPage(
			"Collections",
			"Collections subtitle",
			path,
			[]string{
				"templates/pages/collections.html",
				"templates/components/content/*.html",
				"templates/components/collections_table.html",
			},
			[]BreadCrumb{
				{
					Title: "Collections",
					Path:  path,
				},
			},
			collectionList,
		)

		page.Render(w, r)
	}
}

func ViewCollectionHandler(appState *models.AppState) http.HandlerFunc {
	const path = "/admin/collections"

	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := chi.URLParam(r, "collectionName")

		collection, err := appState.DocumentStore.GetCollection(r.Context(), collectionName)
		if err != nil {
			handleError(w, err, "failed to get collection")
			return
		}

		page := NewPage(
			collectionName,
			collection.Description,
			path,
			[]string{
				"templates/pages/collection_details.html",
				"templates/components/content/*.html",
			},
			[]BreadCrumb{
				{
					Title: "Collections",
					Path:  path,
				},
				{
					Title: collectionName,
					Path:  path + "/" + collectionName,
				},
			},
			CollectionDetails{
				DocumentCollection: &collection,
				Path:               path + "/" + collectionName,
			},
		)

		page.Render(w, r)
	}
}
