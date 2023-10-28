package web

import (
	"bytes"

	"github.com/alecthomas/chroma/formatters/html"
	"github.com/alecthomas/chroma/lexers"
	"github.com/alecthomas/chroma/styles"
)

type CustomPreWrapper struct{}

// Start is called to write a start <pre> element.
// The code flag tells whether this block surrounds
// highlighted code. This will be false when surrounding
// line numbers.
func (p *CustomPreWrapper) Start(code bool, _ string) string {
	if code {
		return `<pre tabindex="0" style="-moz-tab-size:2;-o-tab-size:2;tab-size:2;white-space:pre-wrap;word-break:break-word;">`
	}
	return "<pre>"
}

// End is called to write the end </pre> element.
func (p *CustomPreWrapper) End(code bool) string {
	if code {
		return "</pre>"
	}
	return "</pre>"
}

// CodeHighlight takes a string of code and a lexer name and returns a highlighted
// HTML string.
func CodeHighlight(code string, lexer string) (string, error) {
	// Create a preWrapper that implements the PreWrapper interface
	preWrapper := &CustomPreWrapper{}

	var buf bytes.Buffer
	l := lexers.Get(lexer)
	formatter := html.New(
		html.WrapLongLines(true),
		html.TabWidth(2),
		html.WithPreWrapper(preWrapper),
	)

	style := styles.Get("github")
	iterator, err := l.Tokenise(nil, code)
	if err != nil {
		return "", err
	}
	err = formatter.Format(&buf, style, iterator)
	if err != nil {
		return "", err
	}

	// Convert buffer to string
	return buf.String(), nil
}
