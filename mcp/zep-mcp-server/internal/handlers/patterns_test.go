package handlers

import "testing"

func TestBuildDetectPatternsRequestRequiresGraphTarget(t *testing.T) {
	_, err := buildDetectPatternsRequest(DetectPatternsInput{})
	if err == nil {
		t.Fatal("expected an error when neither user_id nor graph_id is set")
	}
}

func TestBuildDetectPatternsRequestRejectsAmbiguousGraphTarget(t *testing.T) {
	_, err := buildDetectPatternsRequest(DetectPatternsInput{
		UserID:  "user-123",
		GraphID: "graph-123",
	})
	if err == nil {
		t.Fatal("expected an error when both user_id and graph_id are set")
	}
}

func TestBuildDetectPatternsRequestParsesRecencyWeight(t *testing.T) {
	req, err := buildDetectPatternsRequest(DetectPatternsInput{
		UserID:        "user-123",
		RecencyWeight: "30_days",
		Limit:         25,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if req.UserID == nil || *req.UserID != "user-123" {
		t.Fatalf("expected user_id to be set, got %#v", req.UserID)
	}
	if req.RecencyWeight == nil || string(*req.RecencyWeight) != "30_days" {
		t.Fatalf("expected recency_weight to be 30_days, got %#v", req.RecencyWeight)
	}
	if req.Limit == nil || *req.Limit != 25 {
		t.Fatalf("expected limit to be 25, got %#v", req.Limit)
	}
}

func TestBuildDetectPatternsRequestRejectsInvalidRecencyWeight(t *testing.T) {
	_, err := buildDetectPatternsRequest(DetectPatternsInput{
		GraphID:       "graph-123",
		RecencyWeight: "yesterday",
	})
	if err == nil {
		t.Fatal("expected an error for invalid recency_weight")
	}
}
