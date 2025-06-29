package observability

import "github.com/google/uuid"

func NewMockService() *mockService {
	return &mockService{}
}

type mockService struct{}

func (*mockService) CaptureError(_ string, _ error, _ ...any) {}

func (*mockService) CaptureBreadcrumb(_ Category, _ string, _ ...map[string]any) {
}

func (*mockService) LogError(_ string, _ ...any) {}

func (*mockService) SetRequestScope(_, _ uuid.UUID) {}
