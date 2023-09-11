package web

import (
	"embed"
	"html/template"
	"net/http"
	"path/filepath"

	"github.com/getzep/zep/internal"
)

var log = internal.GetLogger()

var LayoutTemplates = []string{
	"templates/pages/index.html",
	"templates/components/layout/*.html",
	"templates/components/content/*.html",
}

//go:embed static/output.css
//go:embed static/preline/*
//go:embed static/js/*
var StaticFS embed.FS

//go:embed templates/*
var TemplatesFS embed.FS

func NewPage(
	title, subTitle, path string,
	templates []string,
	data interface{},
	menuItems []MenuItem,
) *Page {
	//templates = append(LayoutTemplates, templates...)
	return &Page{
		Title:     title,
		SubTitle:  subTitle,
		MenuItems: menuItems,
		Templates: templates,
		Path:      path,
		Data:      data,
	}
}

type Page struct {
	Title     string
	SubTitle  string
	MenuItems []MenuItem
	Templates []string
	Path      string
	Data      interface{}
}

func (p *Page) Render(w http.ResponseWriter) {
	tmpl, err := template.New(p.Title).Funcs(templateFuncs()).ParseFS(
		TemplatesFS,
		p.Templates...,
	)
	if err != nil {
		log.Errorf("Failed to parse template: %s", err)
		http.Error(w, "Failed to parse template", http.StatusInternalServerError)
		return
	}

	if p.Path != "" {
		w.Header().Set("HX-Push", p.Path)
	}
	targetTemplate := filepath.Base(p.Templates[0])
	log.Debugf("TargetTemplate: %s", targetTemplate)

	err = tmpl.ExecuteTemplate(w, targetTemplate, p)
	if err != nil {
		log.Errorf("Failed to parse template: %s", err)
		http.Error(w, "Failed to execute template", http.StatusInternalServerError)
		return
	}
}
