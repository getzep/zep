package webhandlers

import (
	"context"
	"net/http"

	"github.com/getzep/zep/pkg/web"

	"github.com/getzep/zep/pkg/models"
	"github.com/go-chi/chi/v5"
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

func (c *CollectionList) Get(ctx context.Context, _ *models.AppState) error {
	collections, err := c.DocumentStore.GetCollectionList(ctx)
	if err != nil {
		return err
	}
	c.Collections = collections

	return nil
}

type CollectionDetails struct {
	*models.DocumentCollection
}

func GetCollectionListHandler(appState *models.AppState) http.HandlerFunc {
	const path = "/admin/collections"
	return func(w http.ResponseWriter, r *http.Request) {
		collectionList := NewCollectionList(appState.DocumentStore, 0, 0)

		err := collectionList.Get(r.Context(), appState)
		if err != nil {
			handleError(w, err, "failed to get collection list")
			return
		}

		page := web.NewPage(
			"Collections",
			"Manage document collections in the vector store",
			path,
			[]string{
				"templates/pages/collections.html",
				"templates/components/content/*.html",
				"templates/components/collections_table.html",
			},
			[]web.BreadCrumb{
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
	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := chi.URLParam(r, "collectionName")

		collection, err := appState.DocumentStore.GetCollection(r.Context(), collectionName)
		if err != nil {
			handleError(w, err, "failed to get collection")
			return
		}

		const path = "/admin/collections"
		page := web.NewPage(
			collection.Name,
			collection.Description,
			path+"/"+collection.Name,
			[]string{
				"templates/pages/collection_details.html",
				"templates/components/content/*.html",
			},
			[]web.BreadCrumb{
				{
					Title: "Collections",
					Path:  path,
				},
				{
					Title: collection.Name,
					Path:  path + "/" + collection.Name,
				},
			},
			CollectionDetails{
				DocumentCollection: &collection,
			},
		)

		page.Render(w, r)
	}
}

func IndexCollectionHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := chi.URLParam(r, "collectionName")
		if collectionName == "" {
			http.Error(w, "missing collection name", http.StatusBadRequest)
			return
		}

		// Force index creation
		err := appState.DocumentStore.CreateCollectionIndex(r.Context(), collectionName, true)
		if err != nil {
			handleError(w, err, "failed to index collection")
			return
		}

		ViewCollectionHandler(appState)(w, r)
	}
}

func DeleteCollectionHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := chi.URLParam(r, "collectionName")
		if collectionName == "" {
			http.Error(w, "missing collection name", http.StatusBadRequest)
			return
		}

		err := appState.DocumentStore.DeleteCollection(r.Context(), collectionName)
		if err != nil {
			handleError(w, err, "failed to delete collection")
			return
		}

		GetCollectionListHandler(appState)(w, r)
	}
}
