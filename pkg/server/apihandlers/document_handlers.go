package apihandlers

import (
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"strings"

	"github.com/getzep/zep/pkg/server/handlertools"

	"github.com/go-playground/validator/v10"

	"github.com/google/uuid"

	"github.com/getzep/zep/pkg/models"
	"github.com/go-chi/chi/v5"
)

var validate = validator.New()

// CreateCollectionHandler godoc
//
//	@Summary		Creates a new DocumentCollection
//	@Description	If a collection with the same name already exists, an error will be returned.
//	@Tags			collection
//	@Accept			json
//	@Produce		json
//	@Param			collectionName	path		string									true	"Name of the Document Collection"
//	@Param			collection		body		models.CreateDocumentCollectionRequest	true	"Document Collection"
//	@Success		200				{object}	string									"OK"
//	@Failure		400				{object}	APIError								"Bad Request"
//	@Failure		401				{object}	APIError								"Unauthorized"
//	@Failure		404				{object}	APIError								"Not Found"
//	@Failure		500				{object}	APIError								"Internal Server Error"
//
//	@Security		Bearer
//
//	@Router			/api/v1/collection/{collectionName} [post]
func CreateCollectionHandler(appState *models.AppState) http.HandlerFunc {
	store := appState.DocumentStore
	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := strings.ToLower(chi.URLParam(r, "collectionName"))
		if collectionName == "" {
			handlertools.RenderError(
				w,
				errors.New("collectionName is required"),
				http.StatusBadRequest,
			)
			return
		}

		var collectionRequest models.CreateDocumentCollectionRequest
		err := json.NewDecoder(r.Body).Decode(&collectionRequest)
		if err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		if err := validate.Struct(collectionRequest); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		collection := documentCollectionFromCreateRequest(collectionRequest)
		err = store.CreateCollection(r.Context(), collection)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, err, http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		w.WriteHeader(http.StatusOK)
		_, err = w.Write([]byte(OKResponse))
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// UpdateCollectionHandler godoc
//
//	@Summary	Updates a DocumentCollection
//	@Tags		collection
//	@Accept		json
//	@Produce	json
//	@Param		collectionName	path		string									true	"Name of the Document Collection"
//	@Param		collection		body		models.UpdateDocumentCollectionRequest	true	"Document Collection"
//	@Success	200				{object}	string									"OK"
//	@Failure	400				{object}	APIError								"Bad Request"
//	@Failure	401				{object}	APIError								"Unauthorized"
//	@Failure	404				{object}	APIError								"Not Found"
//	@Failure	500				{object}	APIError								"Internal Server Error"
//
//	@Security	Bearer
//
//	@Router		/api/v1/collection/{collectionName} [patch]
func UpdateCollectionHandler(appState *models.AppState) http.HandlerFunc {
	store := appState.DocumentStore
	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := strings.ToLower(chi.URLParam(r, "collectionName"))
		if collectionName == "" {
			handlertools.RenderError(
				w,
				errors.New("collectionName is required"),
				http.StatusBadRequest,
			)
			return
		}
		var collectionRequest models.UpdateDocumentCollectionRequest
		if err := json.NewDecoder(r.Body).Decode(&collectionRequest); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		if err := validate.Struct(collectionRequest); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		collection := documentCollectionFromUpdateRequest(collectionName, collectionRequest)
		err := store.UpdateCollection(r.Context(), collection)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, err, http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		w.WriteHeader(http.StatusOK)
		_, err = w.Write([]byte("OK"))
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// DeleteCollectionHandler godoc
//
//	@Summary		Deletes a DocumentCollection
//	@Description	If a collection with the same name already exists, it will be overwritten.
//	@Tags			collection
//	@Accept			json
//	@Produce		json
//	@Param			collectionName	path		string		true	"Name of the Document Collection"
//	@Success		200				{object}	string		"OK"
//	@Failure		400				{object}	APIError	"Bad Request"
//	@Failure		401				{object}	APIError	"Unauthorized"
//	@Failure		404				{object}	APIError	"Not Found"
//	@Failure		500				{object}	APIError	"Internal Server Error"
//
//	@Security		Bearer
//
//	@Router			/api/v1/collection/{collectionName} [delete]
func DeleteCollectionHandler(appState *models.AppState) http.HandlerFunc {
	store := appState.DocumentStore
	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := strings.ToLower(chi.URLParam(r, "collectionName"))
		if collectionName == "" {
			handlertools.RenderError(
				w,
				errors.New("collectionName is required"),
				http.StatusBadRequest,
			)
			return
		}

		err := store.DeleteCollection(r.Context(), collectionName)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, err, http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		w.WriteHeader(http.StatusOK)
		_, err = w.Write([]byte("OK"))
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// GetCollectionListHandler godoc
//
//	@Summary		Gets a list of DocumentCollections
//	@Description	Returns a list of all DocumentCollections.
//	@Tags			collection
//	@Accept			json
//	@Produce		json
//	@Success		200	{array}		[]models.DocumentCollectionResponse	"OK"
//	@Failure		401	{object}	APIError							"Unauthorized"
//	@Failure		500	{object}	APIError							"Internal Server Error"
//
//	@Security		Bearer
//
//	@Router			/api/v1/collection [get]
func GetCollectionListHandler(appState *models.AppState) http.HandlerFunc {
	store := appState.DocumentStore
	return func(w http.ResponseWriter, r *http.Request) {
		collections, err := store.GetCollectionList(r.Context())
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, err, http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		collectionListResponse := collectionListToCollectionResponseList(collections)

		if err := handlertools.EncodeJSON(w, collectionListResponse); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// GetCollectionHandler godoc
//
//	@Summary		Gets a DocumentCollection
//	@Description	Returns a DocumentCollection if it exists.
//	@Tags			collection
//	@Accept			json
//	@Produce		json
//	@Param			collectionName	path		string								true	"Name of the Document Collection"
//	@Success		200				{object}	models.DocumentCollectionResponse	"OK"
//	@Failure		400				{object}	APIError							"Bad Request"
//	@Failure		401				{object}	APIError							"Unauthorized"
//	@Failure		404				{object}	APIError							"Not Found"
//	@Failure		500				{object}	APIError							"Internal Server Error"
//
//	@Security		Bearer
//
//	@Router			/api/v1/collection/{collectionName} [get]
func GetCollectionHandler(appState *models.AppState) http.HandlerFunc {
	store := appState.DocumentStore
	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := strings.ToLower(chi.URLParam(r, "collectionName"))
		if collectionName == "" {
			handlertools.RenderError(
				w,
				errors.New("collectionName is required"),
				http.StatusBadRequest,
			)
			return
		}

		collection, err := store.GetCollection(r.Context(), collectionName)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, err, http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		collectionResponse := collectionToCollectionResponse(collection)

		if err := handlertools.EncodeJSON(w, collectionResponse); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// CreateDocumentsHandler godoc
//
//	@Summary		Creates Multiple Documents in a DocumentCollection
//	@Description	Creates Documents in a specified DocumentCollection and returns their UUIDs.
//	@Tags			document
//	@Accept			json
//	@Produce		json
//	@Param			collectionName	path		string							true	"Name of the Document Collection"
//	@Param			documents		body		[]models.CreateDocumentRequest	true	"Array of Documents to be created"
//	@Success		200				{array}		uuid.UUID						"OK"
//	@Failure		400				{object}	APIError						"Bad Request"
//	@Failure		401				{object}	APIError						"Unauthorized"
//	@Failure		500				{object}	APIError						"Internal Server Error"
//
//	@Security		Bearer
//
//	@Router			/api/v1/collection/{collectionName}/document [post]
func CreateDocumentsHandler(appState *models.AppState) http.HandlerFunc {
	store := appState.DocumentStore
	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := strings.ToLower(chi.URLParam(r, "collectionName"))
		if collectionName == "" {
			handlertools.RenderError(
				w,
				errors.New("collectionName is required"),
				http.StatusBadRequest,
			)
			return
		}

		var documentListRequest []models.CreateDocumentRequest
		if err := json.NewDecoder(r.Body).Decode(&documentListRequest); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		documents, err := documentListFromDocumentCreateRequestList(
			documentListRequest,
		)
		if err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		uuids, err := store.CreateDocuments(r.Context(), collectionName, documents)
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		if err := handlertools.EncodeJSON(w, uuids); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// UpdateDocumentHandler godoc
//
//	@Summary	Updates a Document in a DocumentCollection by UUID
//	@Tags		document
//	@Accept		json
//	@Produce	json
//	@Param		collectionName	path		string							true	"Name of the Document Collection"
//	@Param		documentUUID	path		string							true	"UUID of the Document to be updated"
//	@Param		document		body		models.UpdateDocumentRequest	true	"Document to be updated"
//	@Success	200				{object}	string							"OK"
//	@Failure	400				{object}	APIError						"Bad Request"
//	@Failure	401				{object}	APIError						"Unauthorized"
//	@Failure	404				{object}	APIError						"Not Found"
//	@Failure	500				{object}	APIError						"Internal Server Error"
//
//	@Security	Bearer
//
//	@Router		/api/v1/collection/{collectionName}/document/uuid/{documentUUID} [patch]
func UpdateDocumentHandler(appState *models.AppState) http.HandlerFunc {
	store := appState.DocumentStore
	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := strings.ToLower(chi.URLParam(r, "collectionName"))
		documentUUID := handlertools.UUIDFromURL(r, w, "documentUUID")

		if collectionName == "" {
			handlertools.RenderError(
				w,
				errors.New("collectionName is required"),
				http.StatusBadRequest,
			)
			return
		}
		if documentUUID == uuid.Nil {
			handlertools.RenderError(
				w,
				errors.New("documentUUID is required"),
				http.StatusBadRequest,
			)
			return
		}

		var documentRequest models.UpdateDocumentRequest
		if err := json.NewDecoder(r.Body).Decode(&documentRequest); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		if err := validate.Struct(documentRequest); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		document := documentFromDocumentUpdateRequest(documentUUID, documentRequest)
		documents := []models.Document{document}
		err := store.UpdateDocuments(r.Context(), collectionName, documents)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, err, http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		w.WriteHeader(http.StatusOK)
		_, err = w.Write([]byte("OK"))
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// UpdateDocumentListHandler godoc
//
//	@Summary		Batch Updates Documents in a DocumentCollection
//	@Description	Updates Documents in a specified DocumentCollection.
//	@Tags			document
//	@Accept			json
//	@Produce		json
//	@Param			collectionName	path		string								true	"Name of the Document Collection"
//	@Param			documents		body		[]models.UpdateDocumentListRequest	true	"Array of Documents to be updated"
//	@Success		200				{object}	string								"OK"
//	@Failure		400				{object}	APIError							"Bad Request"
//	@Failure		401				{object}	APIError							"Unauthorized"
//	@Failure		500				{object}	APIError							"Internal Server Error"
//
//	@Security		Bearer
//
//	@Router			/api/v1/collection/{collectionName}/document/batchUpdate [patch]
func UpdateDocumentListHandler(appState *models.AppState) http.HandlerFunc {
	store := appState.DocumentStore
	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := strings.ToLower(chi.URLParam(r, "collectionName"))
		if collectionName == "" {
			handlertools.RenderError(
				w,
				errors.New("collectionName is required"),
				http.StatusBadRequest,
			)
			return
		}

		var documentsRequest []models.UpdateDocumentListRequest
		if err := json.NewDecoder(r.Body).Decode(&documentsRequest); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		documents, err := documentListFromDocumentBatchUpdateRequest(documentsRequest)
		if err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		err = store.UpdateDocuments(r.Context(), collectionName, documents)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, err, http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		w.WriteHeader(http.StatusOK)
		_, err = w.Write([]byte("OK"))
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// GetDocumentHandler godoc
//
//	@Summary		Gets a Document from a DocumentCollection by UUID
//	@Description	Returns specified Document from a DocumentCollection.
//	@Tags			document
//	@Accept			json
//	@Produce		json
//	@Param			collectionName	path		string					true	"Name of the Document Collection"
//	@Param			documentUUID	path		string					true	"UUID of the Document to be updated"
//	@Success		200				{object}	models.DocumentResponse	"OK"
//	@Failure		400				{object}	APIError				"Bad Request"
//	@Failure		401				{object}	APIError				"Unauthorized"
//	@Failure		500				{object}	APIError				"Internal Server Error"
//
//	@Security		Bearer
//
//	@Router			/api/v1/collection/{collectionName}/document/uuid/{documentUUID} [get]
func GetDocumentHandler(appState *models.AppState) http.HandlerFunc {
	store := appState.DocumentStore
	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := strings.ToLower(chi.URLParam(r, "collectionName"))
		documentUUID := handlertools.UUIDFromURL(r, w, "documentUUID")

		if collectionName == "" {
			handlertools.RenderError(
				w,
				errors.New("collectionName is required"),
				http.StatusBadRequest,
			)
			return
		}
		if documentUUID == uuid.Nil {
			handlertools.RenderError(
				w,
				errors.New("documentUUID is required"),
				http.StatusBadRequest,
			)
			return
		}

		uuids := []uuid.UUID{documentUUID}
		documents, err := store.GetDocuments(
			r.Context(),
			collectionName,
			uuids,
			nil,
		)

		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, err, http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		documentResponse := documentResponseFromDocument(documents[0])

		if err := handlertools.EncodeJSON(w, documentResponse); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// GetDocumentListHandler godoc
//
//	@Summary		Batch Gets Documents from a DocumentCollection
//	@Description	Returns Documents from a DocumentCollection specified by UUID or ID.
//	@Tags			document
//	@Accept			json
//	@Produce		json
//	@Param			collectionName	path		string							true	"Name of the Document Collection"
//	@Param			documentRequest	body		models.GetDocumentListRequest	true	"UUIDs and IDs of the Documents to be fetched"
//	@Success		200				{array}		[]models.DocumentResponse		"OK"
//	@Failure		400				{object}	APIError						"Bad Request"
//	@Failure		401				{object}	APIError						"Unauthorized"
//	@Failure		500				{object}	APIError						"Internal Server Error"
//
//	@Security		Bearer
//
//	@Router			/api/v1/collection/{collectionName}/document/batchGet [post]
func GetDocumentListHandler(appState *models.AppState) http.HandlerFunc {
	store := appState.DocumentStore
	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := strings.ToLower(chi.URLParam(r, "collectionName"))
		if collectionName == "" {
			handlertools.RenderError(
				w,
				errors.New("collectionName is required"),
				http.StatusBadRequest,
			)
			return
		}

		var docRequest models.GetDocumentListRequest
		if err := json.NewDecoder(r.Body).Decode(&docRequest); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		documents, err := store.GetDocuments(
			r.Context(),
			collectionName,
			docRequest.UUIDs,
			docRequest.DocumentIDs,
		)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, err, http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		documentResponses := documentBatchResponseFromDocumentList(documents)
		if err := handlertools.EncodeJSON(w, documentResponses); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// DeleteDocumentHandler godoc
//
//	@Summary		Delete Document from a DocumentCollection by UUID
//	@Description	Delete specified Document from a DocumentCollection.
//
//	@Tags			document
//
//	@Accept			json
//	@Produce		json
//	@Param			collectionName	path		string		true	"Name of the Document Collection"
//	@Param			documentUUID	path		string		true	"UUID of the Document to be deleted"
//	@Success		200				{object}	string		"OK"
//	@Failure		400				{object}	APIError	"Bad Request"
//	@Failure		401				{object}	APIError	"Unauthorized"
//	@Failure		404				{object}	APIError	"Document Not Found"
//	@Failure		500				{object}	APIError	"Internal Server Error"
//
//	@Security		Bearer
//
//	@Router			/api/v1/collection/{collectionName}/document/uuid/{documentUUID} [delete]
func DeleteDocumentHandler(appState *models.AppState) http.HandlerFunc {
	store := appState.DocumentStore
	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := strings.ToLower(chi.URLParam(r, "collectionName"))
		documentUUID := handlertools.UUIDFromURL(r, w, "documentUUID")

		if collectionName == "" {
			handlertools.RenderError(
				w,
				errors.New("collectionName is required"),
				http.StatusBadRequest,
			)
			return
		}

		if documentUUID == uuid.Nil {
			handlertools.RenderError(
				w,
				errors.New("documentUUID is required"),
				http.StatusBadRequest,
			)
			return
		}

		uuids := []uuid.UUID{documentUUID}
		err := store.DeleteDocuments(r.Context(), collectionName, uuids)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, err, http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		w.WriteHeader(http.StatusOK)
		_, err = w.Write([]byte("OK"))
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// DeleteDocumentListHandler godoc
//
//	@Summary		Batch Deletes Documents from a DocumentCollection by UUID
//	@Description	Deletes specified Documents from a DocumentCollection.
//
//	@Tags			document
//
//	@Accept			json
//	@Produce		json
//	@Param			collectionName	path		string		true	"Name of the Document Collection"
//	@Param			documentUUIDs	body		[]uuid.UUID	true	"UUIDs of the Documents to be deleted"
//	@Success		200				{object}	string		"OK"
//	@Failure		400				{object}	APIError	"Bad Request"
//	@Failure		401				{object}	APIError	"Unauthorized"
//	@Failure		500				{object}	APIError	"Internal Server Error"
//
//	@Security		Bearer
//
//	@Router			/api/v1/collection/{collectionName}/document/batchDelete [post]
func DeleteDocumentListHandler(appState *models.AppState) http.HandlerFunc {
	store := appState.DocumentStore
	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := strings.ToLower(chi.URLParam(r, "collectionName"))
		if collectionName == "" {
			handlertools.RenderError(
				w,
				errors.New("collectionName is required"),
				http.StatusBadRequest,
			)
			return
		}

		var documentUUIDs []uuid.UUID
		if err := json.NewDecoder(r.Body).Decode(&documentUUIDs); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		err := store.DeleteDocuments(r.Context(), collectionName, documentUUIDs)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, err, http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		w.WriteHeader(http.StatusOK)
		_, err = w.Write([]byte("OK"))
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// CreateCollectionIndexHandler godoc
//
//	@Summary		Creates an index for a DocumentCollection
//	@Description	Creates an index for the specified DocumentCollection to improve query performance.
//
//	@Tags			collection
//
//	@Accept			json
//	@Produce		json
//	@Param			collectionName	path		string		true	"Name of the Document Collection"
//	@Param			force			query		bool		false	"Force index creation, even if there are too few documents to index"
//
//	@Success		200				{object}	string		"OK"
//	@Failure		400				{object}	APIError	"Bad Request"
//	@Failure		401				{object}	APIError	"Unauthorized"
//	@Failure		500				{object}	APIError	"Internal Server Error"
//
//	@Security		Bearer
//
//	@Router			/api/v1/collection/{collectionName}/index/create [post]
func CreateCollectionIndexHandler(appState *models.AppState) http.HandlerFunc {
	store := appState.DocumentStore
	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := strings.ToLower(chi.URLParam(r, "collectionName"))
		if collectionName == "" {
			handlertools.RenderError(
				w,
				errors.New("collectionName is required"),
				http.StatusBadRequest,
			)
			return
		}

		forceStr := r.URL.Query().Get("force")
		force := false
		if forceStr != "" {
			var err error
			force, err = strconv.ParseBool(forceStr)
			if err != nil {
				handlertools.RenderError(w, err, http.StatusBadRequest)
				return
			}
		}

		err := store.CreateCollectionIndex(r.Context(), collectionName, force)
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		w.WriteHeader(http.StatusOK)
		_, err = w.Write([]byte("OK"))
		if err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// SearchDocumentsHandler godoc
//
//	@Summary		Searches Documents in a DocumentCollection
//	@Description	Searches Documents in a DocumentCollection based on provided search criteria.
//
//	@Tags			document
//
//	@Accept			json
//	@Produce		json
//	@Param			collectionName	path		string							true	"Name of the Document Collection"
//	@Param			limit			query		int								false	"Limit the number of returned documents"
//	@Param			searchPayload	body		models.DocumentSearchPayload	true	"Search criteria"
//	@Success		200				{object}	[]models.Document				"OK"
//	@Failure		400				{object}	APIError						"Bad Request"
//	@Failure		401				{object}	APIError						"Unauthorized"
//	@Failure		500				{object}	APIError						"Internal Server Error"
//
//	@Security		Bearer
//
//	@Router			/api/v1/collection/{collectionName}/search [post]
func SearchDocumentsHandler(appState *models.AppState) http.HandlerFunc {
	store := appState.DocumentStore
	return func(w http.ResponseWriter, r *http.Request) {
		collectionName := strings.ToLower(chi.URLParam(r, "collectionName"))
		if collectionName == "" {
			handlertools.RenderError(
				w,
				errors.New("collectionName is required"),
				http.StatusBadRequest,
			)
			return
		}

		limit, err := handlertools.IntFromQuery[int](r, "limit")
		if err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		var searchPayload models.DocumentSearchPayload
		if err := json.NewDecoder(r.Body).Decode(&searchPayload); err != nil {
			handlertools.RenderError(w, err, http.StatusBadRequest)
			return
		}

		searchPayload.CollectionName = collectionName

		results, err := store.SearchCollection(r.Context(), &searchPayload, limit, 0, 0)
		if err != nil {
			if errors.Is(err, models.ErrNotFound) {
				handlertools.RenderError(w, err, http.StatusNotFound)
				return
			}
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}

		if err := handlertools.EncodeJSON(w, results); err != nil {
			handlertools.RenderError(w, err, http.StatusInternalServerError)
			return
		}
	}
}

// documentCollectionFromCreateRequest converts a CreateDocumentCollectionRequest to a DocumentCollection.
func documentCollectionFromCreateRequest(
	collectionRequest models.CreateDocumentCollectionRequest,
) models.DocumentCollection {
	return models.DocumentCollection{
		Name:                collectionRequest.Name,
		Description:         collectionRequest.Description,
		Metadata:            collectionRequest.Metadata,
		EmbeddingDimensions: collectionRequest.EmbeddingDimensions,
		IsAutoEmbedded:      *collectionRequest.IsAutoEmbedded,
	}
}

// documentCollectionFromUpdateRequest converts a UpdateDocumentCollectionRequest to a DocumentCollection.
func documentCollectionFromUpdateRequest(
	collectionName string,
	collectionRequest models.UpdateDocumentCollectionRequest,
) models.DocumentCollection {
	return models.DocumentCollection{
		Name:        collectionName,
		Description: collectionRequest.Description,
		Metadata:    collectionRequest.Metadata,
	}
}

// collectionToCollectionResponse converts a DocumentCollection to a DocumentCollectionResponse.
func collectionToCollectionResponse(
	collection models.DocumentCollection,
) models.DocumentCollectionResponse {
	counts := &models.DocumentCollectionCounts{}
	if collection.DocumentCollectionCounts != nil {
		counts = &models.DocumentCollectionCounts{
			DocumentCount:         collection.DocumentCount,
			DocumentEmbeddedCount: collection.DocumentEmbeddedCount,
		}
	}
	return models.DocumentCollectionResponse{
		UUID:                     collection.UUID,
		CreatedAt:                collection.CreatedAt,
		UpdatedAt:                collection.UpdatedAt,
		Name:                     collection.Name,
		Description:              collection.Description,
		Metadata:                 collection.Metadata,
		EmbeddingModelName:       collection.EmbeddingModelName,
		EmbeddingDimensions:      collection.EmbeddingDimensions,
		IsAutoEmbedded:           collection.IsAutoEmbedded,
		IsNormalized:             collection.IsNormalized,
		IsIndexed:                collection.IsIndexed,
		DocumentCollectionCounts: counts,
	}
}

// collectionListToCollectionResponseList converts a list of DocumentCollections to a list of DocumentCollectionResponses.
func collectionListToCollectionResponseList(
	collections []models.DocumentCollection,
) []models.DocumentCollectionResponse {
	collectionResponses := make([]models.DocumentCollectionResponse, len(collections))
	for i, collection := range collections {
		collectionResponses[i] = collectionToCollectionResponse(collection)
	}
	return collectionResponses
}

// documentListFromDocumentCreateRequestList validates a list of CreateDocumentRequests and returns a list of Documents.
// If any of the CreateDocumentRequests are invalid, an error is returned.
func documentListFromDocumentCreateRequestList(
	documents []models.CreateDocumentRequest,
) ([]models.Document, error) {
	documentList := make([]models.Document, len(documents))
	for i := range documents {
		d := documents[i]
		if err := validate.Struct(d); err != nil {
			return nil, err
		}
		documentList[i] = documentFromDocumentCreateRequest(d)
	}
	return documentList, nil
}

// documentFromDocumentCreateRequest converts a CreateDocumentRequest to a Document.
func documentFromDocumentCreateRequest(request models.CreateDocumentRequest) models.Document {
	return models.Document{
		DocumentBase: models.DocumentBase{
			DocumentID: request.DocumentID,
			Content:    request.Content,
			Metadata:   request.Metadata,
		},
		Embedding: request.Embedding,
	}
}

// documentFromDocumentUpdateRequest converts a UpdateDocumentRequest to a Document.
func documentFromDocumentUpdateRequest(
	documentUUID uuid.UUID,
	request models.UpdateDocumentRequest,
) models.Document {
	return models.Document{
		DocumentBase: models.DocumentBase{
			UUID:       documentUUID,
			DocumentID: request.DocumentID,
			Metadata:   request.Metadata,
		},
	}
}

// documentListFromDocumentBatchUpdateRequest validates a list of UpdateDocumentBatchRequests
// and returns a list of Documents. Returns an error if any of the requests are invalid.
func documentListFromDocumentBatchUpdateRequest(
	documentUpdates []models.UpdateDocumentListRequest,
) ([]models.Document, error) {
	documentList := make([]models.Document, len(documentUpdates))
	for i := range documentUpdates {
		d := documentUpdates[i]
		if err := validate.Struct(d); err != nil {
			return nil, err
		}
		documentList[i] = documentFromDocumentUpdateRequest(d.UUID, d.UpdateDocumentRequest)
	}
	return documentList, nil
}

// documentResponseFromDocument converts a models.Document to a models.DocumentResponse
func documentResponseFromDocument(document models.Document) models.DocumentResponse {
	return models.DocumentResponse{
		UUID:       document.UUID,
		CreatedAt:  document.CreatedAt,
		UpdatedAt:  document.UpdatedAt,
		DocumentID: document.DocumentID,
		Content:    document.Content,
		Metadata:   document.Metadata,
		Embedding:  document.Embedding,
		IsEmbedded: document.IsEmbedded,
	}
}

// documentBatchResponseFromDocumentList converts a list of models.Documents to a list of models.DocumentResponses
func documentBatchResponseFromDocumentList(documents []models.Document) []models.DocumentResponse {
	documentResponses := make([]models.DocumentResponse, len(documents))
	for i, document := range documents {
		documentResponses[i] = documentResponseFromDocument(document)
	}
	return documentResponses
}
