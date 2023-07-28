package config

import "fmt"

var (
	Version       = "dev"
	CommitHash    = "n/a"
	BuildTime     = "n/a"
	VersionString = fmt.Sprintf("%s-%s (%s)", Version, CommitHash, BuildTime)
)
