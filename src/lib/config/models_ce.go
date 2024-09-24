
package config

import "github.com/google/uuid"

type Config struct {
	Common `yaml:",inline"`

	Telemetry telemetryConfig `yaml:"telemetry"`
	Graphiti  graphitiConfig  `yaml:"graphiti"`
	ApiSecret string          `yaml:"api_secret"`
}

type postgresConfig struct {
	postgresConfigCommon `yaml:",inline"`

	SchemaName string `yaml:"schema_name"`
}

type graphitiConfig struct {
	ServiceUrl string `yaml:"service_url"`
}

type telemetryConfig struct {
	Disabled         bool   `yaml:"disabled"`
	OrganizationName string `yaml:"organization_name"`
}

func Graphiti() graphitiConfig {
	return _loaded.Graphiti
}

func ApiSecret() string {
	return _loaded.ApiSecret
}

func ProjectUUID() uuid.UUID {
	return uuid.MustParse("399e79e0-d0ec-4ea8-a0bf-fe556d19fb9f")
}

func Telemetry() telemetryConfig {
	return _loaded.Telemetry
}
