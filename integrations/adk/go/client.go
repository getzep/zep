package zepadk

import (
	"os"

	zepclient "github.com/getzep/zep-go/v3/client"
	zepoption "github.com/getzep/zep-go/v3/option"
)

// NewClient constructs a Zep client authenticated with apiKey. It is a thin
// convenience wrapper over [zepclient.NewClient]; callers who need additional
// options (a custom base URL, for example) can build the client directly.
//
// The returned client should be reused across the lifetime of the process.
func NewClient(apiKey string, opts ...zepoption.RequestOption) *zepclient.Client {
	allOpts := append([]zepoption.RequestOption{zepoption.WithAPIKey(apiKey)}, opts...)
	return zepclient.NewClient(allOpts...)
}

// NewClientFromEnv returns a Zep client built from the ZEP_API_KEY environment
// variable, or nil when the variable is unset or empty. A nil client is
// accepted everywhere in this package and makes the integration a no-op, so
// callers can wire Zep unconditionally and let it disable itself when no key
// is configured.
func NewClientFromEnv() *zepclient.Client {
	apiKey := os.Getenv("ZEP_API_KEY")
	if apiKey == "" {
		return nil
	}
	return NewClient(apiKey)
}
