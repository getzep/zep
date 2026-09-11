package main

import (
	"fmt"
	"os"
)

const usage = `Usage:
  go run .                 Run the user-graph example
  go run . entity-types    Register custom entity/edge types (project-wide ontology)

Notes:
  entity-types replaces the project-level ontology — use a disposable Zep project key.
`

func main() {
	args := os.Args[1:]

	var run func() error
	switch {
	case len(args) == 0:
		run = runUserGraph
	case len(args) == 1 && args[0] == "entity-types":
		fmt.Println("WARNING: Setting entity types replaces the project-wide ontology.")
		fmt.Println("Use a disposable Zep project/API key for this example.")
		fmt.Println()
		run = runEntityTypes
	default:
		fmt.Fprint(os.Stderr, usage)
		os.Exit(2)
	}

	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
