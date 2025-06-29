
package graphiti

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/google/uuid"

	"github.com/getzep/zep/lib/config"
	"github.com/getzep/zep/lib/util/httputil"
	"github.com/getzep/zep/models"
)

type GetMemoryRequest struct {
	GroupID        string           `json:"group_id"`
	MaxFacts       int              `json:"max_facts"`
	CenterNodeUUID string           `json:"center_node_uuid"`
	Messages       []models.Message `json:"messages"`
}

type Fact struct {
	UUID      uuid.UUID  `json:"uuid"`
	Name      string     `json:"name"`
	Fact      string     `json:"fact"`
	CreatedAt time.Time  `json:"created_at"`
	ExpiredAt *time.Time `json:"expired_at"`
	ValidAt   *time.Time `json:"valid_at"`
	InvalidAt *time.Time `json:"invalid_at"`
}

func (f Fact) ExtractCreatedAt() time.Time {
	if f.ValidAt != nil {
		return *f.ValidAt
	}
	return f.CreatedAt
}

type GetMemoryResponse struct {
	Facts []Fact `json:"facts"`
}

type Message struct {
	UUID string `json:"uuid"`
	// The role of the sender of the message (e.g., "user", "assistant").
	Role string `json:"role"`
	// The type of the role (e.g., "user", "system").
	RoleType models.RoleType `json:"role_type,omitempty"`
	// The content of the message.
	Content string `json:"content"`
}

type PutMemoryRequest struct {
	GroupId  string    `json:"group_id"`
	Messages []Message `json:"messages"`
}

type SearchRequest struct {
	GroupIDs []string `json:"group_ids"`
	Text     string   `json:"query"`
	MaxFacts int      `json:"max_facts,omitempty"`
}

type SearchResponse struct {
	Facts []Fact `json:"facts"`
}

type AddNodeRequest struct {
	GroupID string `json:"group_id"`
	UUID    string `json:"uuid"`
	Name    string `json:"name"`
	Summary string `json:"summary"`
}

type Service interface {
	GetMemory(ctx context.Context, payload GetMemoryRequest) (*GetMemoryResponse, error)
	PutMemory(ctx context.Context, groupID string, messages []models.Message, addGroupIDPrefix bool) error
	Search(ctx context.Context, payload SearchRequest) (*SearchResponse, error)
	AddNode(ctx context.Context, payload AddNodeRequest) error
	GetFact(ctx context.Context, factUUID uuid.UUID) (*Fact, error)
	DeleteFact(ctx context.Context, factUUID uuid.UUID) error
	DeleteGroup(ctx context.Context, groupID string) error
	DeleteMessage(ctx context.Context, messageUUID uuid.UUID) error
}

var _instance Service

func I() Service {
	return _instance
}

type service struct {
	Client  httputil.HTTPClient
	BaseUrl string
}

func Setup() {
	if _instance != nil {
		return
	}

	_instance = &service{
		Client: httputil.NewRetryableHTTPClient(
			httputil.DefaultRetryMax,
			httputil.DefaultTimeout,
			httputil.IgnoreBadRequestRetryPolicy,
			"",
		),
		BaseUrl: config.Graphiti().ServiceUrl,
	}
}

func (s *service) newRequest(ctx context.Context, method, path string, body any) (*http.Request, error) {
	buf := new(bytes.Buffer)
	if body != nil {
		err := json.NewEncoder(buf).Encode(body)
		if err != nil {
			return nil, err
		}
	}

	req, err := http.NewRequestWithContext(ctx, method, fmt.Sprintf("%s/%s", s.BaseUrl, path), buf)
	if err != nil {
		return nil, err
	}

	req.Header.Set("Content-Type", "application/json")

	return req, nil
}

func (s *service) doRequest(req *http.Request, v any) error {
	resp, err := s.Client.Do(req)
	if err != nil {
		return err
	}

	defer func(body io.ReadCloser) {
		body.Close()
	}(resp.Body)

	if resp.StatusCode > http.StatusAccepted {
		return fmt.Errorf("received status code: %d", resp.StatusCode)
	}

	if v == nil {
		return nil
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}

	if len(body) == 0 {
		return fmt.Errorf("received empty response")
	}

	return json.Unmarshal(body, v)
}

func (s *service) GetMemory(ctx context.Context, payload GetMemoryRequest) (*GetMemoryResponse, error) {
	req, err := s.newRequest(ctx, http.MethodPost, "get-memory", payload)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	var resp GetMemoryResponse

	err = s.doRequest(req, &resp)
	if err != nil {
		return nil, fmt.Errorf("failed to do request: %w", err)
	}

	return &resp, nil
}

func (s *service) PutMemory(ctx context.Context, groupID string, messages []models.Message, addGroupIDPrefix bool) error {
	var graphitiMessages []Message
	for _, m := range messages {
		episodeUUID := m.UUID.String()
		if addGroupIDPrefix {
			episodeUUID = fmt.Sprintf("%s-%s", groupID, m.UUID)
		}
		graphitiMessages = append(graphitiMessages, Message{
			UUID:     episodeUUID,
			Role:     m.Role,
			RoleType: m.RoleType,
			Content:  m.Content,
		})
	}

	req, err := s.newRequest(ctx, http.MethodPost, "messages", &PutMemoryRequest{
		GroupId:  groupID,
		Messages: graphitiMessages,
	})
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}
	err = s.doRequest(req, nil)
	if err != nil {
		return fmt.Errorf("failed to do request: %w", err)
	}

	return nil
}

func (s *service) AddNode(ctx context.Context, payload AddNodeRequest) error {
	req, err := s.newRequest(ctx, http.MethodPost, "entity-node", payload)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}

	err = s.doRequest(req, nil)
	if err != nil {
		return fmt.Errorf("failed to do request: %w", err)
	}

	return nil
}

func (s *service) Search(ctx context.Context, payload SearchRequest) (*SearchResponse, error) {
	req, err := s.newRequest(ctx, http.MethodPost, "search", payload)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	var resp SearchResponse

	err = s.doRequest(req, &resp)
	if err != nil {
		return nil, fmt.Errorf("failed to do request: %w", err)
	}

	return &resp, nil
}

func (s *service) GetFact(ctx context.Context, factUUID uuid.UUID) (*Fact, error) {
	req, err := s.newRequest(ctx, http.MethodGet, fmt.Sprintf("entity-edge/%s", factUUID), nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	var resp Fact

	err = s.doRequest(req, &resp)
	if err != nil {
		return nil, fmt.Errorf("failed to do request: %w", err)
	}

	return &resp, nil
}

func (s *service) DeleteGroup(ctx context.Context, groupID string) error {
	req, err := s.newRequest(ctx, http.MethodDelete, fmt.Sprintf("group/%s", groupID), nil)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}

	err = s.doRequest(req, nil)
	if err != nil {
		return fmt.Errorf("failed to do request: %w", err)
	}

	return nil
}

func (s *service) DeleteFact(ctx context.Context, factUUID uuid.UUID) error {
	req, err := s.newRequest(ctx, http.MethodDelete, fmt.Sprintf("entity-edge/%s", factUUID), nil)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}

	err = s.doRequest(req, nil)
	if err != nil {
		return fmt.Errorf("failed to do request: %w", err)
	}

	return nil
}

func (s *service) DeleteMessage(ctx context.Context, messageUUID uuid.UUID) error {
	req, err := s.newRequest(ctx, http.MethodDelete, fmt.Sprintf("episode/%s", messageUUID), nil)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}

	err = s.doRequest(req, nil)
	if err != nil {
		return fmt.Errorf("failed to do request: %w", err)
	}

	return nil
}
