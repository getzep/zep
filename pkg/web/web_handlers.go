package web

import (
	"embed"
	"html/template"
	"net/http"
	"strings"

	"github.com/getzep/zep/internal"
)

//go:embed static/output.css
//go:embed static/preline/*
//go:embed static/js/*
var StaticFS embed.FS

//go:embed templates/*
var TemplatesFS embed.FS

var log = internal.GetLogger()

type PageData struct {
	Title     string
	MenuItems []MenuItem
}

func toLower() template.FuncMap {
	return template.FuncMap{
		"ToLower": strings.ToLower,
	}
}

func IndexHandler(w http.ResponseWriter, r *http.Request) {

	data := PageData{
		Title:     "My Page",
		MenuItems: menuItems, // assuming menuItems is defined
	}

	tmpl, err := template.New("Index").Funcs(toLower()).ParseFS(
		TemplatesFS,
		"templates/pages/index.html",
		"templates/components/layout/*.html",
	)
	if err != nil {
		log.Error("Failed to parse template: %s", err)
		http.Error(w, "Failed to parse template", http.StatusInternalServerError)
		return
	}

	err = tmpl.ExecuteTemplate(w, "Layout", data)
	if err != nil {
		log.Error("Failed to parse template: %s", err)
		http.Error(w, "Failed to execute template", http.StatusInternalServerError)
		return
	}
}
