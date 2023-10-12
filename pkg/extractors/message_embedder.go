package extractors

import (
	"context"
	"database/sql"
	"encoding/json"
	"time"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/ThreeDotsLabs/watermill/message/router/middleware"
	"github.com/ThreeDotsLabs/watermill/message/router/plugin"
	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
	wla "github.com/ma-hartma/watermill-logrus-adapter"
	"github.com/sirupsen/logrus"
	"github.com/sony/gobreaker"
)

const MessageEmbeddingTopic = "message_embedding"

type EmbeddingQueueRouter struct {
	*message.Router
	Publisher message.Publisher
	Topic     string
}

func NewEmbeddingQueueRouter(appState *models.AppState, db *sql.DB) (*EmbeddingQueueRouter, error) {
	var wlog = wla.NewLogrusLogger(log)

	// Create a new router
	cfg := message.RouterConfig{}
	router, err := message.NewRouter(cfg, wlog)
	if err != nil {
		return nil, err
	}

	// SignalsHandler will gracefully shutdown Router when SIGTERM is received.
	// You can also close the router by just calling `r.Close()`.
	router.AddPlugin(plugin.SignalsHandler)

	// Router level middleware are executed for every message sent to the router
	router.AddMiddleware(
		// CorrelationID will copy the correlation id from the incoming message's metadata to the produced messages
		middleware.CorrelationID,

		// The handler function is retried if it returns an error.
		// After MaxRetries, the message is Nacked and it's up to the PubSub to resend it.
		middleware.Retry{
			MaxRetries:      3,
			InitialInterval: time.Millisecond * 100,
			Logger:          wlog,
		}.Middleware,

		middleware.NewCircuitBreaker(gobreaker.Settings{
			Name:        "message_embedding_circuit_breaker",
			MaxRequests: 1,
			Interval:    time.Second * 5,
			Timeout:     time.Second * 20,
		}).Middleware,

		middleware.NewThrottle(5, time.Second).Middleware,

		// Recoverer handles panics from handlers.
		// In this case, it passes them as errors to the Retry middleware.
		middleware.Recoverer,
	)

	publisher, err := NewSQLQueuePublisher(db, wlog)
	if err != nil {
		return nil, err
	}

	subscriber, err := NewSQLQueueSubscriber(db, wlog)
	if err != nil {
		return nil, err
	}

	embeddingHandler := NewEmbeddingProcessHandler(log)

	router.AddHandler(
		MessageEmbeddingTopic,
		MessageEmbeddingTopic,
		subscriber,
		"",
		publisher,
		embeddingHandler.Handler,
	)

	return &EmbeddingQueueRouter{
		Router:    router,
		Publisher: publisher,
		Topic:     MessageEmbeddingTopic,
	}, nil
}

func NewEmbeddingProcessHandler(appState *models.AppState, logger *logrus.Logger) *EmbeddingProcessHandler {
	return &EmbeddingProcessHandler{appState: appState, log: logger}
}

type EmbeddingProcessHandler struct {
	appState *models.AppState
	log      *logrus.Logger
}

func (e *EmbeddingProcessHandler) Handler(msg *message.Message) ([]*message.Message, error) {
	ctx, done := context.WithTimeout(context.Background(), time.Second*10)
	defer done()

	var msgs []models.Message
	err := json.Unmarshal(msg.Payload, msgs)
	if err != nil {
		return nil, err
	}

	err = e.Process(ctx, msgs)
	if err != nil {
		return nil, err
	}

	msg.Ack()

	// Return an empty slice of messages since we don't want to publish anything
	return []*message.Message{}, nil
}

func (e *EmbeddingProcessHandler) Process(
	ctx context.Context,
	msgs []models.Message,
) error {
	messageType := "message"
	texts := messageToStringSlice(msgs, false)

	model, err := llms.GetEmbeddingModel(e.appState, messageType)
	if err != nil {
		return NewExtractorError("EmbeddingExtractor get message embedding model failed", err)
	}

	embeddings, err := llms.EmbedTexts(ctx, e.appState, model, messageType, texts)
	if err != nil {
		return NewExtractorError("EmbeddingExtractor embed messages failed", err)
	}

	embeddingRecords := make([]models.MessageEmbedding, len(msgs))
	for i, r := range msgs {
		embeddingRecords[i] = models.MessageEmbedding{
			TextUUID:  r.UUID,
			Embedding: embeddings[i],
		}
	}
	err = e.appState.MemoryStore.PutMessageVectors(
		ctx,
		appState,
		messageEvent.SessionID,
		embeddingRecords,
	)
	if err != nil {
		return NewExtractorError("EmbeddingExtractor put message vectors failed", err)
	}
	return nil
}
