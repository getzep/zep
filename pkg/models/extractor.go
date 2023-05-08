package models

import (
	"context"
)

// Extractor is an interface that defines the methods that must be implemented by an Extractor
type Extractor interface {
	Extract(
		ctx context.Context,
		appState *AppState,
		messageEvents *MessageEvent,
	) error
	Notify(ctx context.Context, appState *AppState, messageEvents *MessageEvent) error
}
