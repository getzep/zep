package web

import (
	"html/template"
	"net/http"
)

func IndexHandler(w http.ResponseWriter, r *http.Request) {
	data := Page{
		Title:     "Home",
		SubTitle:  "Home subtitle",
		MenuItems: menuItems,
	}

	tmpl, err := template.New("Layout").Funcs(templateFuncs()).ParseFS(
		TemplatesFS,
		"templates/pages/index.html",
		"templates/components/layout/*.html",
		"templates/components/content/*.html",
	)
	if err != nil {
		log.Errorf("Failed to parse template: %s", err)
		http.Error(w, "Failed to parse template", http.StatusInternalServerError)
		return
	}

	err = tmpl.ExecuteTemplate(w, "Layout", data)
	if err != nil {
		log.Errorf("Failed to parse template: %s", err)
		http.Error(w, "Failed to execute template", http.StatusInternalServerError)
		return
	}
}
