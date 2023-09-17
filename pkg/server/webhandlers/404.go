package webhandlers

import (
	"html/template"
	"net/http"

	"github.com/getzep/zep/pkg/web"
)

func NotFoundHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		tmpl, err := template.New("404.html").Funcs(web.TemplateFuncs()).ParseFS(
			web.TemplatesFS,
			"templates/pages/404.html",
		)
		if err != nil {
			log.Errorf("Failed to parse template: %s", err)
			http.Error(w, "Failed to parse template", http.StatusInternalServerError)
			return
		}

		w.WriteHeader(http.StatusNotFound)

		err = tmpl.ExecuteTemplate(w, "404.html", nil)
		if err != nil {
			log.Errorf("Failed to parse template: %s", err)
			http.Error(w, "Failed to execute template", http.StatusInternalServerError)
			return
		}
	}
}
