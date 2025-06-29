package apidata

// APIError represents an error response. Used for swagger documentation.
type APIError struct {
	Message string `json:"message"`
}

type SuccessResponse struct {
	Message string `json:"message"`
}
