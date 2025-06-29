package communication

import (
	"context"

	"github.com/google/uuid"
)

func NewMockService() Service {
	return &mockService{}
}

type mockService struct{}

func (*mockService) HandleSignup(_ context.Context, _ Recipient) error {
	return nil
}

func (*mockService) HandleMemberInvite(_ context.Context, _ Recipient) error {
	return nil
}

func (*mockService) HandleMemberDelete(_ context.Context, _ Recipient) error {
	return nil
}

func (*mockService) NotifyAccountOverage(_ uuid.UUID, _, _ string) {}

func (*mockService) NotifyAccountCreation(_ uuid.UUID, _, _, _ string, _ uuid.UUID) {
}

func (*mockService) NotifyAccountMemberAdded(_ uuid.UUID, _, _, _ string, _ uuid.UUID) {
}
