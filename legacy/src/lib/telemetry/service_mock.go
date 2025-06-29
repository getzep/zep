package telemetry

func NewMockService() Service {
	return &mockService{}
}

type mockService struct{}

func (*mockService) TrackEvent(_ Request, _ Event, _ ...map[string]any) {
}

func (*mockService) Close() {
}
