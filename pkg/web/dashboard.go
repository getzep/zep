package web

import (
	"net/http"
)

func DashboardHandler(w http.ResponseWriter, r *http.Request) {
	page := NewPage(
		"Dashboard",
		"Dashboard subtitle",
		"/admin",
		[]string{
			"templates/pages/dashboard.html",
			"templates/components/content/*.html",
		},
		nil,
	)

	page.Render(w, r)
}
