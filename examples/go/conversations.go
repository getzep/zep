package main

import "github.com/getzep/zep-go/v3"

var history = [][]zep.Message{
	// Conversation 1: Japan
	{
		{
			Name:    zep.String("Paul"),
			Role:    "user",
			Content: "Hi, I'm planning a vacation to Japan. Can you give me some advice?",
		},
		{
			Name:    zep.String("assistant"),
			Role:    "assistant",
			Content: "Of course! Japan is a fascinating destination. Are you more interested in modern cities like Tokyo, or historical sites like Kyoto?",
		},
		{
			Name:    zep.String("Paul"),
			Role:    "user",
			Content: "I think I'd like to experience both. Can you suggest an itinerary?",
		},
		{
			Name:    zep.String("assistant"),
			Role:    "assistant",
			Content: "Certainly! You could start with 3 days in Tokyo, then take the bullet train to Kyoto for 3 days. This way, you'll experience both the modern and traditional aspects of Japan.",
		},
		{
			Name:    zep.String("Paul"),
			Role:    "user",
			Content: "That sounds perfect! I booked a flight on Nov 17th! it departs at 5 pm (flight number GC1234). It cost me $700.",
		},
	},
	// Conversation 2: Italy
	{
		{
			Name:    zep.String("Paul"),
			Role:    "user",
			Content: "I'm thinking about visiting Italy next summer. Any recommendations?",
		},
		{
			Name:    zep.String("assistant"),
			Role:    "assistant",
			Content: "Italy is a wonderful choice! Are you more interested in art and history, or would you prefer to focus on food and wine experiences?",
		},
		{
			Name:    zep.String("Paul"),
			Role:    "user",
			Content: "I love both, but I think I'm leaning towards the food and wine experiences.",
		},
		{
			Name:    zep.String("assistant"),
			Role:    "assistant",
			Content: "Great! In that case, you might want to consider regions like Tuscany or Emilia-Romagna. Would you like more information about these areas?",
		},
		{
			Name:    zep.String("Paul"),
			Role:    "user",
			Content: "Yes, please tell me more about Tuscany. What are some must-try dishes and wines there?",
		},
	},
	{
		{
			Name:    zep.String("Paul"),
			Role:    "user",
			Content: "Apples are my favorite fruit",
		},
		{
			Name:    zep.String("Paul"),
			Role:    "user",
			Content: "now bananas are my favorite fruit",
		},
		{
			Name:    zep.String("Paul"),
			Role:    "user",
			Content: "Eric Clapton is my favorite guitarist",
		},
	},
	// Conversation 3: US Road Trip
}
