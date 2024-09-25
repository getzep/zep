package communication

import (
	"context"

	"github.com/google/uuid"
)

type Recipient struct {
	Email     string
	FirstName string
	LastName  string
}

type AlertRecipientType string

const (
	EmailRecipientType AlertRecipientType = "email"
)

type AlertTopic string

const (
	AccountOverageTopic AlertTopic = "account_overage"
)

type Service interface {
	HandleSignup(ctx context.Context, recip Recipient) error
	HandleMemberInvite(ctx context.Context, recip Recipient) error
	HandleMemberDelete(ctx context.Context, recip Recipient) error

	NotifyAccountOverage(accountUUID uuid.UUID, email, plan string)
	NotifyAccountCreation(
		accountUUID uuid.UUID,
		ownerEmail, ownerFirstName, ownerLastName string,
		ownerUUID uuid.UUID,
	)
	NotifyAccountMemberAdded(
		accountUUID uuid.UUID,
		memberEmail, memberFirstName, memberLastName string,
		memberUUID uuid.UUID,
	)
}
