
package enablement

func I() Service {
	return NewMockService()
}

type EventMetadata any
