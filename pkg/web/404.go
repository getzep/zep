package web

import (
	"html/template"
	"net/http"
)

func NotFoundHandler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		tmpl, err := template.New("404.html").Funcs(templateFuncs()).ParseFS(
			TemplatesFS,
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
