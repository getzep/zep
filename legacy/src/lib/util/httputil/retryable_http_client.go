package httputil

import (
	"context"
	"crypto/tls"
	"net/http"
	"net/http/httptrace"
	"sync"
	"time"

	"github.com/hashicorp/go-retryablehttp"
	"go.opentelemetry.io/contrib/instrumentation/net/http/httptrace/otelhttptrace"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"

	"github.com/getzep/zep/lib/logger"
)

var httpClients sync.Map

const (
	DefaultRetryMax     = 3
	DefaultTimeout      = 5 * time.Second
	MaxIdleConns        = 100
	MaxIdleConnsPerHost = 20
	IdleConnTimeout     = 30 * time.Second
)

type HTTPClient interface {
	Do(req *http.Request) (*http.Response, error)
}

// NewRetryableHTTPClient returns a new retryable HTTP client with the given retryMax and timeout.
// The retryable HTTP transport is wrapped in an OpenTelemetry transport.
func NewRetryableHTTPClient(
	retryMax int,
	timeout time.Duration,
	retryPolicy retryablehttp.CheckRetry,
	serverName string,
) *http.Client {
	client, ok := httpClients.Load(serverName)
	if ok {
		if httpClient, ok := client.(*http.Client); ok {
			return httpClient
		}
	}

	tlsConfig := &tls.Config{
		MinVersion: tls.VersionTLS12,
	}
	if serverName != "" {
		tlsConfig.ServerName = serverName
	}

	httpClient := retryablehttp.Client{
		HTTPClient: &http.Client{
			Timeout: timeout,
			Transport: otelhttp.NewTransport(&http.Transport{
				TLSClientConfig:       tlsConfig,
				MaxIdleConns:          MaxIdleConns,
				MaxIdleConnsPerHost:   MaxIdleConnsPerHost,
				IdleConnTimeout:       IdleConnTimeout,
				ResponseHeaderTimeout: timeout,
				DisableKeepAlives:     false,
			}, otelhttp.WithClientTrace(
				func(ctx context.Context) *httptrace.ClientTrace {
					return otelhttptrace.NewClientTrace(ctx)
				}),
			),
		},
		Logger:     logger.GetLogger(),
		RetryMax:   retryMax,
		Backoff:    retryablehttp.DefaultBackoff,
		CheckRetry: retryPolicy,
	}

	httpClients.Store(serverName, &httpClient)

	return httpClient.HTTPClient
}

func IgnoreBadRequestRetryPolicy(ctx context.Context, resp *http.Response, err error) (bool, error) {
	if resp != nil && resp.StatusCode != http.StatusOK {
		logger.Warn("Retry policy invoked with response", "status", resp.Status, "error", err)
	}

	// do not retry on context.Canceled or context.DeadlineExceeded
	if ctx.Err() != nil {
		return false, ctx.Err()
	}

	// Do not retry 400 errors as they're used by OpenAI to indicate maximum
	// context length exceeded
	if resp != nil && resp.StatusCode == http.StatusBadRequest {
		return false, err
	}

	shouldRetry, _ := retryablehttp.DefaultRetryPolicy(ctx, resp, err)
	return shouldRetry, nil
}
