package main

import (
	"fmt"
	"os"
)

const usage = `Usage:
  go run .                 Run the user-graph example
  go run . entity-types    Set custom entity/edge types on a user graph

Notes:
  entity-types requires ZEP_ENTITY_TYPES_USER_UUID and replaces that graph's ontology.
`

func main() {
	args := os.Args[1:]

	var run func() error
	switch {
	case len(args) == 0:
		run = runUserGraph
	case len(args) == 1 && args[0] == "entity-types":
		fmt.Println("WARNING: Setting entity types replaces the selected user graph's ontology.")
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
