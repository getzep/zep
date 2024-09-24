package models

type FilterOption[T any] func(*T)

func ApplyFilterOptions[T any](opts ...FilterOption[T]) T {
	var o T
	for _, opt := range opts {
		opt(&o)
	}
	return o
}
