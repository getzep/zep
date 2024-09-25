package httputil

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/hashicorp/go-retryablehttp"
)

const (
	DefaultHTTPTimeout      = 10 * time.Second
	DefaultMaxRetryAttempts = 3
)

type HTTPBaser interface {
	Request(ctx context.Context, payload any) ([]byte, error)
	healthCheck(ctx context.Context) error
}

var _ HTTPBaser = &HTTPBase{}

// HTTPBase is a MixIn for Models that have HTTP APIs and use Bearer tokens for authorization
type HTTPBase struct {
	ApiURL           string
	ApiKey           string
	HealthURL        string
	ServerName       string
	RequestTimeOut   time.Duration
	MaxRetryAttempts int
}

// request makes a POST request to the LLM's API endpoint. payload is marshalled to JSON and sent
// as the request body. The response body is returned as a []byte.
// Assumes the content type is application/json
func (h *HTTPBase) Request(ctx context.Context, payload any) ([]byte, error) {
	var requestTimeout time.Duration
	if h.RequestTimeOut != 0 {
		requestTimeout = h.RequestTimeOut
	} else {
		requestTimeout = DefaultHTTPTimeout
	}

	var maxRetryAttempts int
	if h.MaxRetryAttempts != 0 {
		maxRetryAttempts = h.MaxRetryAttempts
	} else {
		maxRetryAttempts = DefaultMaxRetryAttempts
	}

	ctx, cancel := context.WithTimeout(ctx, requestTimeout)
	defer cancel()

	httpClient := NewRetryableHTTPClient(
		maxRetryAttempts,
		requestTimeout,
		IgnoreBadRequestRetryPolicy,
		h.ServerName,
	)

	p, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}

	body := bytes.NewBuffer(p)

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, h.ApiURL, body)
	if err != nil {
		return nil, err
	}

	req.Header.Set("Authorization", "Bearer "+h.ApiKey)
	req.Header.Set("Content-Type", "application/json")

	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, err
	}

	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf(
			"error making POST request: %d - %s",
			resp.StatusCode,
			resp.Status,
		)
	}

	rb, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	return rb, nil
}

func (h *HTTPBase) healthCheck(ctx context.Context) error {
	ctx, cancel := context.WithTimeout(ctx, DefaultHTTPTimeout)
	defer cancel()

	httpClient := NewRetryableHTTPClient(
		1,
		DefaultHTTPTimeout,
		retryablehttp.DefaultRetryPolicy,
		h.ServerName,
	)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, h.HealthURL, http.NoBody)
	if err != nil {
		return err
	}

	resp, err := httpClient.Do(req)
	if err != nil {
		return err
	}

	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("health check failed with status: %d", resp.StatusCode)
	}

	return nil
}
