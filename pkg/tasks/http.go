package tasks

import (
	"context"
	"net/http"
	"net/http/httptrace"
	"time"

	"go.opentelemetry.io/contrib/instrumentation/net/http/httptrace/otelhttptrace"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"

	"github.com/hashicorp/go-retryablehttp"
)

// NewRetryableHTTPClient returns a new retryable HTTP client with the given retryMax and timeout.
// The retryable HTTP transport is wrapped in an OpenTelemetry transport.
func NewRetryableHTTPClient(retryMax int, timeout time.Duration) *http.Client {
	retryableHTTPClient := retryablehttp.NewClient()
	retryableHTTPClient.RetryMax = retryMax
	retryableHTTPClient.HTTPClient.Timeout = timeout
	retryableHTTPClient.Logger = log
	retryableHTTPClient.Backoff = retryablehttp.DefaultBackoff
	retryableHTTPClient.CheckRetry = retryablehttp.DefaultRetryPolicy

	httpClient := &http.Client{
		Transport: otelhttp.NewTransport(
			retryableHTTPClient.StandardClient().Transport,
			otelhttp.WithClientTrace(func(ctx context.Context) *httptrace.ClientTrace {
				return otelhttptrace.NewClientTrace(ctx)
			}),
		),
	}

	return httpClient
}
