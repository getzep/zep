package main

import (
	cmd "github.com/getzep/zep/cmd/zep"
	"github.com/getzep/zep/internal"
)

var log = internal.GetLogger()

func main() {
	log.Info("Starting zep")
	cmd.Execute()
}
