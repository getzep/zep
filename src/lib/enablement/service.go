package enablement

import (
	"context"

	"github.com/google/uuid"
)

type Trait func(t map[string]any)

type BillingPlan string

func (z BillingPlan) String() string {
	return string(z)
}

type Profile struct {
	UUID               uuid.UUID
	Plan               BillingPlan
	UnderMessagesQuota bool
}

type Service interface {
	UpdateSubscription(ctx context.Context, customerId string, accountUUID uuid.UUID, newPlan BillingPlan) error
	GenerateSubscriptionURL(accountUUID uuid.UUID, plan BillingPlan) (string, error)
	GenerateCustomerPortalURL(customerId string) (string, error)
	ConfirmSubscription(accountUUID uuid.UUID, sessionId string) (string, error)
	UpdatePlan(ctx context.Context, accountUUID uuid.UUID, newPlan BillingPlan) error

	GetProfile(ctx context.Context, accountUUID uuid.UUID) Profile
	IsEnabled(ctx context.Context, accountUUID uuid.UUID, flag string) bool
	UnderProjectQuota(ctx context.Context, accountUUID uuid.UUID) bool

	CreateProfile(ctx context.Context, accountUUID uuid.UUID)
	CreateUser(
		ctx context.Context,
		accountUUID, memberUUID uuid.UUID,
		firstName, lastName, email string,
		traits ...Trait,
	)
	UpdateProjectCount(ctx context.Context, accountUUID uuid.UUID, projectCount int)

	TrackEvent(event Event, metadata EventMetadata)
}
