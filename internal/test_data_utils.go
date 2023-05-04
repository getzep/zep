package internal

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"github.com/danielchalef/zep/pkg/models"
	"strings"

	"github.com/joho/godotenv"
	"github.com/spf13/viper"
)

var MessagesSummary = models.MessagesAndSummary{
	Messages: []models.Message{
		{
			Role:    "user",
			Content: "Hello",
		},
		{
			Role:    "assistant",
			Content: "Hi there!",
		},
		{
			Role:    "user",
			Content: "I'm looking to plan a trip to Iceland. Can you help me?",
		},
		{
			Role:    "assistant",
			Content: "Of course! I'd be happy to help you plan your trip.",
		},
		{
			Role:    "user",
			Content: "What's the best time of year to go?",
		},
		{
			Role:    "assistant",
			Content: "The best time to visit Iceland is from June to August. The weather is milder, and you'll have more daylight for sightseeing.",
		},
		{
			Role:    "user",
			Content: "Do I need a visa?",
		},
		{
			Role:    "assistant",
			Content: "Visa requirements depend on your nationality. Citizens of the Schengen Area, the US, Canada, and several other countries can visit Iceland for up to 90 days without a visa.",
		},
		{
			Role:    "user",
			Content: "What are some must-see attractions?",
		},
		{
			Role:    "assistant",
			Content: "Some popular attractions include the Blue Lagoon, Golden Circle, Reynisfjara Black Sand Beach, Gulfoss waterfall, and the Jökulsárlón Glacier Lagoon.",
		},
		{
			Role:    "user",
			Content: "What should I pack?",
		},
		{
			Role:    "assistant",
			Content: "Pack warm and waterproof clothing, layers for temperature changes, comfortable walking shoes, a swimsuit for hot springs, and a camera to capture the beautiful scenery.",
		},
		{
			Role:    "user",
			Content: "Should I rent a car?",
		},
		{
			Role:    "assistant",
			Content: "Renting a car is a great idea if you plan on exploring areas outside of Reykjavik. It gives you more freedom to travel at your own pace and visit remote locations.",
		},
		{
			Role:    "user",
			Content: "How much does a trip to Iceland typically cost?",
		},
		{
			Role:    "assistant",
			Content: "Iceland can be expensive. Costs depend on factors like accommodations, activities, and dining preferences. However, you can expect to spend around $200-$300 per day, not including flights.",
		},
		{
			Role:    "user",
			Content: "Is it easy to find vegetarian or vegan food in Iceland?",
		},
		{
			Role:    "assistant",
			Content: "Yes, Reykjavik has several vegetarian and vegan-friendly restaurants. In smaller towns, you may find fewer options, but most places will have some vegetarian dishes available.",
		},
		{
			Role:    "user",
			Content: "Thank you for all this information! I'm excited to start planning my trip.",
		},
		{
			Role:    "assistant",
			Content: "You're welcome! Have a great time planning and enjoy your trip to Iceland!",
		},
	},
	Summary: models.Summary{
		Content: "A conversation where the user and assistant discuss planning a trip to Iceland. They cover topics like the best time to visit, visa requirements, attractions, packing, transportation, costs, and food options.",
	},
}

func SetDefaultsAndEnv() {
	viper.SetDefault("embeddings.enable", true)
	viper.SetDefault("embeddings.dimensions", 1536)
	viper.SetDefault("embeddings.model", "AdaEmbeddingV2")
	viper.SetDefault("llm_model", "gpt-3.5-turbo")
	viper.SetDefault("messages.max_session_length", 20)
	viper.SetDefault("memory.message_window", 12)
	viper.SetDefault("memory.token_window", 500)
	viper.SetDefault("memory.summarize.enable", true)
	viper.SetDefault("memory.search.metric", "COSINE")
	viper.SetDefault("memory_store.type", "redis")
	viper.SetDefault("memory_store.url", "localhost:6379")
	viper.SetDefault("server.port", 8000)

	err := godotenv.Load()
	if err != nil {
		fmt.Println(".env file not found or unable to load")
	}

	viper.SetEnvPrefix(
		"ZEP",
	)
	viper.SetEnvKeyReplacer(strings.NewReplacer(`.`, `_`)) // replaced nested . with _
	viper.AutomaticEnv()
}

func GenerateRandomSessionID(length int) (string, error) {
	bytes := make([]byte, (length+1)/2)
	_, err := rand.Read(bytes)
	if err != nil {
		return "", fmt.Errorf("failed to generate random session ID: %w", err)
	}
	return hex.EncodeToString(bytes)[:length], nil
}
