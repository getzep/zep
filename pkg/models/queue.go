package models

import (
	wsql "github.com/ThreeDotsLabs/watermill-sql/v2/pkg/sql"
)

type Queue struct {
	Name         string
	Subscriber   *wsql.Subscriber
	ConsumeTopic string
	Publisher    *wsql.Publisher
	PublishTopic string
}
