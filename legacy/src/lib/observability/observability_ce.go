
package observability

func I() Service {
	return NewMockService()
}

func Setup() {}

func Shutdown() {}
