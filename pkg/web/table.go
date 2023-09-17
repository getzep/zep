package web

import (
	"fmt"
	"math"
	"net/http"

	"github.com/getzep/zep/pkg/server/handlertools"
)

const DefaultPageSize = 10
const DefaultSortKey = "created_at"
const DefaultSortAsc = false

func NewTable(id string, columns []Column) *Table {
	return &Table{
		TableID: id,
		Columns: columns,
	}
}

type Column struct {
	Name       string
	Sortable   bool
	OrderByKey string
}

type Table struct {
	TableID     string
	Columns     []Column
	Rows        interface{}
	TotalCount  int
	RowCount    int
	Offset      int
	CurrentPage int
	PageSize    int
	PageCount   int
	OrderBy     string
	Asc         bool
}

func (t *Table) getOrderByMap() map[string]bool {
	m := make(map[string]bool)
	for _, c := range t.Columns {
		m[c.OrderByKey] = c.Sortable
	}
	return m
}

func (t *Table) GetOffset() int {
	return (t.CurrentPage - 1) * t.PageSize
}

func (t *Table) GetOrderBy() string {
	if t.OrderBy == "" {
		return DefaultSortKey
	}
	if _, ok := t.getOrderByMap()[t.OrderBy]; !ok {
		return DefaultSortKey
	}
	return t.OrderBy
}

func (t *Table) GetAsc() bool {
	return t.Asc
}

func (t *Table) GetPageSize() int {
	if t.PageSize == 0 {
		return DefaultPageSize
	}
	return t.PageSize
}

func (t *Table) GetPageCount() int {
	totalCount := float64(t.TotalCount)
	pageSize := float64(t.GetPageSize())
	return int(math.Ceil(totalCount / pageSize))
}

func (t *Table) ParseQueryParams(r *http.Request) {
	t.CurrentPage = 1
	t.PageSize = DefaultPageSize
	t.OrderBy = DefaultSortKey
	t.Asc = DefaultSortAsc

	if page, err := handlertools.IntFromQuery[int](r, "page"); err == nil {
		if page == 0 {
			page = 1
		}
		t.CurrentPage = page
	}

	if orderBy := r.URL.Query().Get("order"); len(orderBy) > 0 {
		t.OrderBy = orderBy
	}

	if asc, err := handlertools.BoolFromQuery(r, "asc"); err == nil {
		t.Asc = asc
	}
}

func (t *Table) GetTablePath(basePath string) string {
	return fmt.Sprintf("%s?order=%s&asc=%t", basePath, t.GetOrderBy(), t.GetAsc())
}
