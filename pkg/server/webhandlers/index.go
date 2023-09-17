package webhandlers

import (
	"net/http"

	"github.com/getzep/zep/pkg/web"
)

func IndexHandler(w http.ResponseWriter, r *http.Request) {
	const path = "/admin"

	page := web.NewPage(
		"Dashboard",
		"",
		path,
		[]string{
			"templates/pages/dashboard.html",
			"templates/components/content/*.html",
		},
		[]web.BreadCrumb{
			{
				Title: "Dashboard",
				Path:  path,
			},
		},
		nil,
	)

	page.Render(w, r)
}
