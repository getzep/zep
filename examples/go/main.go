package main

import (
	"fmt"
	"os"
)

func main() {
	if len(os.Args) > 1 && os.Args[1] == "entity-types" {
		fmt.Println("WARNING: Setting entity types replaces the project-wide ontology.")
		fmt.Println("Use a disposable Zep project/API key for this example.")
		fmt.Println()
		runEntityTypes()
		return
	}
	runUserGraph()
}
