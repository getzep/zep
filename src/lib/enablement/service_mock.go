package enablement

import (
	"context"

	"github.com/google/uuid"
)

func NewMockService() Service {
	return &mockService{}
}

type mockService struct{}

func (*mockService) UpdateSubscription(_ context.Context, _ string, _ uuid.UUID, _ BillingPlan) error {
	return nil
}

func (*mockService) GenerateSubscriptionURL(_ uuid.UUID, _ BillingPlan) (string, error) {
	return "", nil
}

func (*mockService) GenerateCustomerPortalURL(_ string) (string, error) {
	return "", nil
}

func (*mockService) ConfirmSubscription(_ uuid.UUID, _ string) (string, error) {
	return "", nil
}

func (*mockService) UpdatePlan(_ context.Context, _ uuid.UUID, _ BillingPlan) error {
	return nil
}

func (*mockService) GetProfile(_ context.Context, _ uuid.UUID) Profile {
	return Profile{}
}

func (*mockService) IsEnabled(_ context.Context, _ uuid.UUID, _ string) bool {
	return true
}

func (*mockService) UnderProjectQuota(_ context.Context, _ uuid.UUID) bool {
	return true
}

func (*mockService) CreateProfile(_ context.Context, _ uuid.UUID) {}

func (*mockService) CreateUser(
	_ context.Context,
	_, _ uuid.UUID,
	_, _, _ string,
	_ ...Trait,
) {
}

func (*mockService) UpdateProjectCount(_ context.Context, _ uuid.UUID, _ int) {
}

func (*mockService) TrackEvent(_ Event, _ EventMetadata) {}

func (*mockService) Close() {}
