package main

import (
	cmd "github.com/danielchalef/zep/cmd/zep"
	"github.com/danielchalef/zep/internal"
)

var log = internal.GetLogger()

func main() {
	log.Info("Starting zep")
	cmd.Execute()
}
