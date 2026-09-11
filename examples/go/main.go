package main

import (
	"errors"
	"fmt"
	"os"
	"strings"
)

// Root command names accepted by the examples entrypoint.
const (
	RootCommandUserGraph   = "user-graph"
	RootCommandEntityTypes = "entity-types"
)

// modulePathValue must match the final path segment in go.mod so `go install .`
// does not produce a binary named "go".
const modulePathValue = "github.com/getzep/zep/examples/zep-go-examples"

// ErrRootUsage indicates invalid CLI usage for the root examples entrypoint.
var ErrRootUsage = errors.New("usage")

func modulePath() string {
	return modulePathValue
}

func rootUsage() string {
	return strings.TrimSpace(`Usage:
  go run .                 Run the user-graph example
  go run . entity-types    Register custom entity/edge types (project-wide ontology)

Notes:
  entity-types replaces the project-level ontology — use a disposable Zep project key.
  go install . installs a binary named zep-go-examples (not "go").`) + "\n"
}

// ParseRootCommand parses the root examples CLI.
// No args runs the user-graph example; "entity-types" runs the ontology example.
func ParseRootCommand(args []string) (string, error) {
	if len(args) == 0 {
		return RootCommandUserGraph, nil
	}
	switch args[0] {
	case RootCommandEntityTypes:
		if len(args) > 1 {
			return "", fmt.Errorf("%w: unexpected arguments after %s\n\n%s", ErrRootUsage, RootCommandEntityTypes, rootUsage())
		}
		return RootCommandEntityTypes, nil
	case "-h", "--help", "help":
		return "", fmt.Errorf("%w:\n%s", ErrRootUsage, rootUsage())
	default:
		return "", fmt.Errorf("%w: unknown command %q\n\n%s", ErrRootUsage, args[0], rootUsage())
	}
}

func main() {
	cmd, err := ParseRootCommand(os.Args[1:])
	if err != nil {
		fmt.Fprint(os.Stderr, err.Error())
		if !strings.HasSuffix(err.Error(), "\n") {
			fmt.Fprintln(os.Stderr)
		}
		os.Exit(2)
	}

	var runErr error
	switch cmd {
	case RootCommandEntityTypes:
		fmt.Println("WARNING: Setting entity types replaces the project-wide ontology.")
		fmt.Println("Use a disposable Zep project/API key for this example.")
		fmt.Println()
		runErr = runEntityTypes()
	default:
		runErr = runUserGraph()
	}
	if runErr != nil {
		fmt.Fprintf(os.Stderr, "%v\n", runErr)
		os.Exit(1)
	}
}
