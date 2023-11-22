package postgres

import (
	"context"
	"fmt"

	"github.com/getzep/zep/pkg/models"
	"github.com/google/uuid"
	"github.com/uptrace/bun"
)

type MessageDAO struct {
	db *bun.DB
}

func NewMessageDAO(db *bun.DB) *MessageDAO {
	return &MessageDAO{db: db}
}

func (dao *MessageDAO) Create(
	ctx context.Context,
	sessionID string,
	message *models.Message,
) error {
	// Create a new MessageStoreSchema from the provided message
	pgMessage := MessageStoreSchema{
		UUID:       message.UUID,
		SessionID:  sessionID,
		Role:       message.Role,
		Content:    message.Content,
		TokenCount: message.TokenCount,
		Metadata:   message.Metadata,
	}

	// Insert the new message into the database
	_, err := dao.db.NewInsert().
		Model(&pgMessage).
		Exec(ctx)

	if err != nil {
		return fmt.Errorf("failed to create message: %w", err)
	}

	return nil
}

func (dao *MessageDAO) Retrieve(ctx context.Context, uuid uuid.UUID) (*models.Message, error) {
	// Implement the retrieval of a message by its UUID
	return nil, nil
}

func (dao *MessageDAO) Update(ctx context.Context, message *models.Message) error {
	// Implement the update of a message
	return nil
}

func (dao *MessageDAO) Delete(ctx context.Context, uuid uuid.UUID) error {
	// Implement the deletion of a message by its UUID
	return nil
}

func (dao *MessageDAO) List(
	ctx context.Context,
	sessionID string,
	currentPage int,
	pageSize int,
) ([]*models.Message, error) {
	// Implement the retrieval of all messages for a session with pagination
	return nil, nil
}
