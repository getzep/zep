
package store

import (
	"context"
	"errors"

	"github.com/getzep/zep/lib/graphiti"
	"github.com/getzep/zep/lib/telemetry"
	"github.com/getzep/zep/models"
)

const maxMessagesForFactRetrieval = 4 // 2 chat turns

func (dao *memoryDAO) _get(
	ctx context.Context,
	session *models.Session,
	messages []models.Message,
	_ models.MemoryFilterOptions,
) (*models.Memory, error) {
	mForRetrieval := messages
	if len(messages) > maxMessagesForFactRetrieval {
		mForRetrieval = messages[len(messages)-maxMessagesForFactRetrieval:]
	}
	var result models.Memory
	groupID := session.SessionID
	if session.UserID != nil {
		groupID = *session.UserID
	}
	memory, err := graphiti.I().GetMemory(
		ctx,
		graphiti.GetMemoryRequest{
			GroupID:  groupID,
			MaxFacts: 5,
			Messages: mForRetrieval,
		},
	)
	if err != nil {
		return nil, err
	}

	result.Messages = messages
	var memoryFacts []models.Fact
	for _, fact := range memory.Facts {
		createdAt := fact.CreatedAt
		if fact.ValidAt != nil {
			createdAt = *fact.ValidAt
		}
		memoryFacts = append(memoryFacts, models.Fact{
			Fact:      fact.Fact,
			UUID:      fact.UUID,
			CreatedAt: createdAt,
		})
	}
	result.RelevantFacts = memoryFacts
	return &result, nil
}

func (dao *memoryDAO) _initializeProcessingMemory(
	ctx context.Context,
	session *models.Session,
	memoryMessages *models.Memory,
) error {
	err := graphiti.I().PutMemory(ctx, session.SessionID, memoryMessages.Messages, true)
	if err != nil {
		return err
	}
	if session.UserID != nil {
		err = graphiti.I().PutMemory(ctx, *session.UserID, memoryMessages.Messages, true)
	}
	return err
}

func (dao *memoryDAO) _searchSessions(ctx context.Context, query *models.SessionSearchQuery, limit int) (*models.SessionSearchResponse, error) {
	if query == nil {
		return nil, errors.New("nil query received")
	}
	var groupIDs []string
	if query.UserID != "" {
		groupIDs = append(groupIDs, query.UserID)
	}
	if len(query.SessionIDs) > 0 {
		groupIDs = append(groupIDs, query.SessionIDs...)
	}
	result, err := graphiti.I().Search(
		ctx,
		graphiti.SearchRequest{
			GroupIDs: groupIDs,
			Text:     query.Text,
			MaxFacts: limit,
		},
	)
	if err != nil {
		return nil, err
	}

	var searchResults []models.SessionSearchResult

	for _, r := range result.Facts {
		createdAt := r.CreatedAt
		if r.ValidAt != nil {
			createdAt = *r.ValidAt
		}
		searchResults = append(searchResults, models.SessionSearchResult{
			SessionSearchResultCommon: models.SessionSearchResultCommon{
				Fact: &models.Fact{
					Fact:      r.Fact,
					UUID:      r.UUID,
					CreatedAt: createdAt,
				},
			},
		})
	}

	telemetry.I().TrackEvent(dao.requestState, telemetry.Event_SearchSessions, map[string]any{
		"result_count":   len(searchResults),
		"query_text_len": len(query.Text),
	})

	return &models.SessionSearchResponse{
		Results: searchResults,
	}, nil
}
