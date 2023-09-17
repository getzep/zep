package web

import (
	"embed"
	"html/template"
	"net/http"
	"regexp"
	"strings"

	"github.com/getzep/zep/internal"
)

var log = internal.GetLogger()

var LayoutTemplates = []string{
	"templates/pages/index.html",
	"templates/components/layout/*.html",
	"templates/components/content/*.html",
}

//go:embed static/*
//go:embed static/preline/*
//go:embed static/js/*
var StaticFS embed.FS

//go:embed templates/*
var TemplatesFS embed.FS

func NewPage(
	title, subTitle, path string,
	templates []string,
	breadCrumbs []BreadCrumb,
	data interface{},
) *Page {
	return &Page{
		Title:       title,
		SubTitle:    subTitle,
		MenuItems:   menuItems,
		Templates:   templates,
		Path:        path,
		Slug:        slugify(title),
		BreadCrumbs: breadCrumbs,
		Data:        data,
	}
}

type BreadCrumb struct {
	Title string
	Path  string
}

type Page struct {
	Title       string
	SubTitle    string
	MenuItems   []MenuItem
	Templates   []string
	Path        string
	Slug        string
	BreadCrumbs []BreadCrumb
	Data        interface{}
}

func (p *Page) Render(w http.ResponseWriter, r *http.Request) {
	// If HX-Request header is set, render content template only
	// If the page was loaded directly, render full layout
	if r.Header.Get("HX-Request") == "true" {
		p.renderPartial(w)
	} else {
		p.renderFull(w)
	}
}

func (p *Page) renderPartial(w http.ResponseWriter) {
	tmpl, err := template.New(p.Title).Funcs(TemplateFuncs()).ParseFS(
		TemplatesFS,
		p.Templates...,
	)
	if err != nil {
		log.Errorf("Failed to parse template: %s", err)
		http.Error(w, "Failed to parse template", http.StatusInternalServerError)
		return
	}

	// Render template content only
	err = tmpl.ExecuteTemplate(w, "Content", p)
	if err != nil {
		log.Errorf("Failed to parse template: %s", err)
		http.Error(w, "Failed to execute template", http.StatusInternalServerError)
		return
	}
}

func (p *Page) renderFull(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "text/html")

	templates := append(LayoutTemplates, p.Templates...) //nolint:gocritic

	tmpl, err := template.New(p.Title).Funcs(TemplateFuncs()).ParseFS(
		TemplatesFS,
		templates...,
	)
	if err != nil {
		log.Errorf("Failed to parse template: %s", err)
		http.Error(w, "Failed to parse template", http.StatusInternalServerError)
		return
	}

	// Render full layout
	err = tmpl.ExecuteTemplate(w, "Layout", p)
	if err != nil {
		log.Errorf("Failed to parse template: %s", err)
		http.Error(w, "Failed to execute template", http.StatusInternalServerError)
		return
	}
}

// slugify converts a string to an alpha-only lowercase string
func slugify(s string) string {
	reg := regexp.MustCompile("[^a-zA-Z]+")
	processedString := reg.ReplaceAllString(s, "")
	return strings.ToLower(processedString)
}

type ExternalPage struct {
	Title string
	URL   string
}

var ExternalPages = map[string]ExternalPage{
	"website": {
		Title: "Website",
		URL:   "https://getzep.com",
	},
	"docs": {
		Title: "Documentation",
		URL:   "https://docs.getzep.com",
	},
	"github": {
		Title: "GitHub",
		URL:   "https://github.com/getzep/zep",
	},
}
