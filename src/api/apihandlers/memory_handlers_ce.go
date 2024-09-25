
package apihandlers

import (
	"context"
	"net/http"

	"github.com/getzep/zep/api/apidata"
	"github.com/getzep/zep/lib/graphiti"

	"github.com/getzep/zep/models"
)

func putMemory(r *http.Request, rs *models.RequestState, sessionID string, memory apidata.AddMemoryRequest) error {
	return rs.Memories.PutMemory(
		r.Context(),
		sessionID,
		&models.Memory{
			MemoryCommon: models.MemoryCommon{
				Messages: apidata.MessagesToModelMessagesTransformer(memory.Messages),
			},
		},
		false, /* skipNotify */
	)
}

func extractMemoryFilterOptions(_ *http.Request) ([]models.MemoryFilterOption, error) {
	var memoryOptions []models.MemoryFilterOption

	return memoryOptions, nil
}

func deleteMemory(ctx context.Context, sessionID string, rs *models.RequestState) error {
	mList, err := rs.Memories.GetMessageList(ctx, sessionID, 0, 1)
	if err != nil {
		return err
	}
	totalSize := mList.TotalCount
	if totalSize == 0 {
		return rs.Memories.DeleteSession(ctx, sessionID)
	}
	err = graphiti.I().DeleteGroup(ctx, sessionID)
	if err != nil {
		return err
	}

	return rs.Memories.DeleteSession(ctx, sessionID)
}
