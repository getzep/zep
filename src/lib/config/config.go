package config

import (
	"fmt"
	"net/url"
)

// this is a pointer so that if someone attempts to use it before loading it will
// panic and force them to load it first.
// it is also private so that it cannot be modified after loading.
var _loaded *Config

func LoadDefault() {
	config := defaultConfig

	_loaded = &config
}

// set sane defaults for all of the config options. when loading the config from
// the file, any options that are not set will be set to these defaults.
var defaultConfig = Config{
	Common: Common{
		Log: logConfig{
			Level:  "warn",
			Format: "json",
		},
		Http: httpConfig{
			Port:           9000,
			MaxRequestSize: 5242880,
		},
		Carbon: carbonConfig{
			Locale: "en",
		},
	},
}

type Common struct {
	Log      logConfig      `yaml:"log"`
	Http     httpConfig     `yaml:"http"`
	Postgres postgresConfig `yaml:"postgres"`
	Carbon   carbonConfig   `yaml:"carbon"`
}

type logConfig struct {
	Level  string `yaml:"level"`
	Format string `yaml:"format"`
}

type httpConfig struct {
	Host           string `yaml:"host"`
	Port           int    `yaml:"port"`
	MaxRequestSize int64  `yaml:"max_request_size"`
}

type postgresConfigCommon struct {
	User               string `yaml:"user"`
	Password           string `yaml:"password"`
	Host               string `yaml:"host"`
	Port               int    `yaml:"port"`
	Database           string `yaml:"database"`
	ReadTimeout        int    `yaml:"read_timeout"`
	WriteTimeout       int    `yaml:"write_timeout"`
	MaxOpenConnections int    `yaml:"max_open_connections"`
}

func (c postgresConfigCommon) DSN() string {
	return fmt.Sprintf(
		"postgres://%s:%s@%s:%d/%s?sslmode=disable",
        	url.QueryEscape(c.User),
        	url.QueryEscape(c.Password),
        	c.Host,
        	c.Port,
        	url.QueryEscape(c.Database),
    	)
}

type carbonConfig struct {
	// should be the name of one of the language files in carbon
	// https://github.com/golang-module/carbon/tree/master/lang
	Locale string `yaml:"locale"`
}

// there should be a getter for each top level field in the config struct.
// these getters will panic if the config has not been loaded.

func Logger() logConfig {
	return _loaded.Log
}

func Http() httpConfig {
	return _loaded.Http
}

func Postgres() postgresConfig {
	return _loaded.Postgres
}

func Carbon() carbonConfig {
	return _loaded.Carbon
}
