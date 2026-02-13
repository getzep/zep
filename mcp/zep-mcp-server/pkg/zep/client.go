package zep

import (
	"github.com/getzep/zep-go/v3/client"
	"github.com/getzep/zep-go/v3/option"
)

// Client wraps the Zep Cloud client
type Client struct {
	*client.Client
}

// NewClient creates a new Zep Cloud client
func NewClient(apiKey string) *Client {
	zepClient := client.NewClient(option.WithAPIKey(apiKey))
	return &Client{Client: zepClient}
}
