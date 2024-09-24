package apidata

import "github.com/getzep/zep/models"

type MemoryCommon struct {
	// A list of message objects, where each message contains a role and content. Only last_n messages will be returned
	Messages []Message `json:"messages"`

	// A dictionary containing metadata associated with the memory.
	Metadata map[string]any `json:"metadata,omitempty"`

	RelevantFacts []Fact `json:"relevant_facts"`
}

func commonMemoryTransformer(memory *models.Memory) MemoryCommon {
	return MemoryCommon{
		Messages:      MessageListTransformer(memory.Messages),
		Metadata:      memory.Metadata,
		RelevantFacts: FactListTransformer(memory.RelevantFacts),
	}
}

type AddMemoryRequestCommon struct {
	// A list of message objects, where each message contains a role and content.
	Messages []Message `json:"messages" validate:"required"`
}
