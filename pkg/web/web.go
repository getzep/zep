package web

import "embed"

//go:embed static/assets/*
var StaticFS embed.FS

//go:embed static/templates/*
var TemplatesFS embed.FS
