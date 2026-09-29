package main

import zep "github.com/getzep/zep-go/v4"

var history = [][]*zep.AddMessage{
	// Conversation 1: Japan
	{
		{
			Name:    zep.String("Paul"),
			Role:    zep.RoleTypeUser.Ptr(),
			Content: zep.String("Hi, I'm planning a vacation to Japan. Can you give me some advice?"),
		},
		{
			Name:    zep.String("assistant"),
			Role:    zep.RoleTypeAssistant.Ptr(),
			Content: zep.String("Of course! Japan is a fascinating destination. Are you more interested in modern cities like Tokyo, or historical sites like Kyoto?"),
		},
		{
			Name:    zep.String("Paul"),
			Role:    zep.RoleTypeUser.Ptr(),
			Content: zep.String("I think I'd like to experience both. Can you suggest an itinerary?"),
		},
		{
			Name:    zep.String("assistant"),
			Role:    zep.RoleTypeAssistant.Ptr(),
			Content: zep.String("Certainly! You could start with 3 days in Tokyo, then take the bullet train to Kyoto for 3 days. This way, you'll experience both the modern and traditional aspects of Japan."),
		},
		{
			Name:    zep.String("Paul"),
			Role:    zep.RoleTypeUser.Ptr(),
			Content: zep.String("That sounds perfect! I booked a flight on Nov 17th! it departs at 5 pm (flight number GC1234). It cost me $700."),
		},
	},
	// Conversation 2: Italy
	{
		{
			Name:    zep.String("Paul"),
			Role:    zep.RoleTypeUser.Ptr(),
			Content: zep.String("I'm thinking about visiting Italy next summer. Any recommendations?"),
		},
		{
			Name:    zep.String("assistant"),
			Role:    zep.RoleTypeAssistant.Ptr(),
			Content: zep.String("Italy is a wonderful choice! Are you more interested in art and history, or would you prefer to focus on food and wine experiences?"),
		},
		{
			Name:    zep.String("Paul"),
			Role:    zep.RoleTypeUser.Ptr(),
			Content: zep.String("I love both, but I think I'm leaning towards the food and wine experiences."),
		},
		{
			Name:    zep.String("assistant"),
			Role:    zep.RoleTypeAssistant.Ptr(),
			Content: zep.String("Great! In that case, you might want to consider regions like Tuscany or Emilia-Romagna. Would you like more information about these areas?"),
		},
		{
			Name:    zep.String("Paul"),
			Role:    zep.RoleTypeUser.Ptr(),
			Content: zep.String("Yes, please tell me more about Tuscany. What are some must-try dishes and wines there?"),
		},
	},
	{
		{
			Name:    zep.String("Paul"),
			Role:    zep.RoleTypeUser.Ptr(),
			Content: zep.String("Apples are my favorite fruit"),
		},
		{
			Name:    zep.String("Paul"),
			Role:    zep.RoleTypeUser.Ptr(),
			Content: zep.String("now bananas are my favorite fruit"),
		},
		{
			Name:    zep.String("Paul"),
			Role:    zep.RoleTypeUser.Ptr(),
			Content: zep.String("Eric Clapton is my favorite guitarist"),
		},
	},
	// Conversation 3: US Road Trip
}
