package web

import (
	"net/http"
)

func IndexHandler(w http.ResponseWriter, r *http.Request) {
	const path = "/admin"

	page := NewPage(
		"Dashboard",
		"",
		path,
		[]string{
			"templates/pages/dashboard.html",
			"templates/components/content/*.html",
		},
		[]BreadCrumb{
			{
				Title: "Dashboard",
				Path:  path,
			},
		},
		nil,
	)

	page.Render(w, r)
}
