
package models

type Session struct {
	SessionCommon
}

type SessionStore interface {
	SessionStoreCommon
}

type CreateSessionRequest struct {
	CreateSessionRequestCommon
}

type UpdateSessionRequest struct {
	UpdateSessionRequestCommon
}
