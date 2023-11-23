//go:build testutils

package testutils

import "github.com/getzep/zep/pkg/models"

var TestMessages = []models.Message{
	{
		Role:     "user",
		Content:  "Hello",
		Metadata: nil,
	},
	{
		Role:    "assistant",
		Content: "Hi there!",
		Metadata: map[string]interface{}{
			"foo": "bar",
		},
	},
	{
		Role:    "user",
		Content: "I'm looking to plan a trip to Iceland. Can you help me?",
		Metadata: map[string]interface{}{
			"bar": "foo",
		},
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
}
