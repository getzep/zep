package llms

import (
	"log"

	openai "github.com/sashabaranov/go-openai"
	"github.com/spf13/viper"
)

func CreateOpenAIClient() *openai.Client {
	openAIKey := viper.GetString("OPENAI_API_KEY")
	if openAIKey == "" {
		log.Fatal("$OPENAI_API_KEY is not set")
	}
	return openai.NewClient(openAIKey)
}
