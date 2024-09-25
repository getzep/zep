package httputil

import "context"

type MockHTTPBase struct {
	ReturnPayload []byte
}

func (m *MockHTTPBase) Request(_ context.Context, _ any) ([]byte, error) {
	return m.ReturnPayload, nil
}

func (m *MockHTTPBase) healthCheck(_ context.Context) error {
	return nil
}
