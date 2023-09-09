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
	SubTitle  string
	MenuItems []MenuItem
}

func toLower() template.FuncMap {
	return template.FuncMap{
		"ToLower": strings.ToLower,
	}
}

func IndexHandler(w http.ResponseWriter, r *http.Request) {

	data := PageData{
		Title:     "Dashboard",
		SubTitle:  "Dashboard subtitle",
		MenuItems: menuItems,
	}

	tmpl, err := template.New("Layout").Funcs(toLower()).ParseFS(
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

func DashboardHandler(w http.ResponseWriter, r *http.Request) {
	data := PageData{
		Title:     "Dashboard",
		SubTitle:  "Dashboard subtitle",
		MenuItems: menuItems,
	}

	tmpl, err := template.New("dashboard.html").Funcs(toLower()).ParseFS(
		TemplatesFS,
		"templates/pages/dashboard.html",
		"templates/components/content/*.html",
	)
	if err != nil {
		log.Errorf("Failed to parse template: %s", err)
		http.Error(w, "Failed to parse template", http.StatusInternalServerError)
		return
	}

	err = tmpl.ExecuteTemplate(w, "dashboard.html", data)
	if err != nil {
		log.Errorf("Failed to parse template: %s", err)
		http.Error(w, "Failed to execute template", http.StatusInternalServerError)
		return
	}
}

func UserListHandler(w http.ResponseWriter, r *http.Request) {
	data := PageData{
		Title:     "Users",
		SubTitle:  "Users subtitle",
		MenuItems: menuItems,
	}

	tmpl, err := template.New("users").Funcs(toLower()).ParseFS(
		TemplatesFS,
		"templates/pages/users.html",
		"templates/components/content/*.html",
	)
	if err != nil {
		log.Errorf("Failed to parse template: %s", err)
		http.Error(w, "Failed to parse template", http.StatusInternalServerError)
		return
	}

	err = tmpl.ExecuteTemplate(w, "users.html", data)
	if err != nil {
		log.Errorf("Failed to parse template: %s", err)
		http.Error(w, "Failed to execute template", http.StatusInternalServerError)
		return
	}
}
