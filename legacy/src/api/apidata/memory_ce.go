
package apidata

import "github.com/getzep/zep/models"

func MemoryTransformer(memory *models.Memory) Memory {
	return Memory{
		MemoryCommon: commonMemoryTransformer(memory),
	}
}

type Memory struct {
	MemoryCommon
}

type AddMemoryRequest struct {
	AddMemoryRequestCommon
}
