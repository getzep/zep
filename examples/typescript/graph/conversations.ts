import { RoleType } from "../../src/api/";

export const history: {
    name: string;
    role: RoleType;
    content: string;
}[][] = [
        // Conversation 1: Japan
        [
            {
                name: "Paul",
                role: "user",
                content: "Hi, I'm planning a vacation to Japan. Can you give me some advice?",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "Of course! Japan is a fascinating destination. Are you more interested in modern cities like Tokyo, or historical sites like Kyoto?",
            },
            {
                name: "Paul",
                role: "user",
                content: "I think I'd like to experience both. Can you suggest an itinerary?",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "Certainly! You could start with 3 days in Tokyo, then take the bullet train to Kyoto for 3 days. This way, you'll experience both the modern and traditional aspects of Japan.",
            },
            {
                name: "Paul",
                role: "user",
                content: "That sounds perfect! What are some must-visit places in Tokyo?",
            },
        ],
        // Conversation 2: Italy
        [
            {
                name: "Paul",
                role: "user",
                content: "I'm thinking about visiting Italy next summer. Any recommendations?",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "Italy is a wonderful choice! Are you more interested in art and history, or would you prefer to focus on food and wine experiences?",
            },
            {
                name: "Paul",
                role: "user",
                content: "I love both, but I think I'm leaning towards the food and wine experiences.",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "Great! In that case, you might want to consider regions like Tuscany or Emilia-Romagna. Would you like more information about these areas?",
            },
            {
                name: "Paul",
                role: "user",
                content: "Yes, please tell me more about Tuscany. What are some must-try dishes and wines there?",
            },
        ],
        [
            {
                name: "Paul",
                role: "user",
                content: "Apples are my favorite fruit",
            },
            {
                name: "Paul",
                role: "user",
                content: "now bananas are my favorite fruit",
            },
            {
                name: "Paul",
                role: "user",
                content: "Eric Clapton is my favorite guitarist",
            },
        ],
        // Conversation 3: US Road Trip
        [
            {
                name: "Paul",
                role: "user",
                content: "I'm thinking about taking a road trip across the United States. Any suggestions?",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "That sounds exciting! A cross-country road trip can be an amazing experience. Do you have any specific regions or landmarks you'd like to see?",
            },
            {
                name: "Paul",
                role: "user",
                content: "I'd love to see some national parks and maybe hit a few major cities. I'm starting from New York.",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "Great! How about a route that takes you through some major cities and national parks? We could plan a route from New York to California, hitting spots like Chicago, Yellowstone, and the Grand Canyon along the way.",
            },
            {
                name: "Paul",
                role: "user",
                content: "That sounds perfect! How long do you think a trip like that would take?",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "A trip like that could take anywhere from 2-4 weeks, depending on how much time you want to spend at each location. Would you prefer a faster-paced trip or a more leisurely one?",
            },
            {
                name: "Paul",
                role: "user",
                content: "I think I'd prefer a more leisurely pace. Let's aim for about 3 weeks. Can you break down a potential itinerary?",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "Certainly! Here's a rough 3-week itinerary: Days 1-2: New York to Chicago, Days 3-4: Chicago, Days 5-7: Drive to and explore Yellowstone, Days 8-10: Drive to and explore Grand Canyon, Days 11-13: Las Vegas and Death Valley, Days 14-16: Drive to and explore Yosemite, Days 17-19: San Francisco, Days 20-21: Drive down the Pacific Coast Highway to Los Angeles.",
            },
            {
                name: "Paul",
                role: "user",
                content: "That sounds amazing! What kind of vehicle would you recommend for a trip like this?",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "For a long road trip like this, comfort and reliability are key. A mid-size SUV could be a good choice. It offers space for luggage, comfort for long drives, and can handle various road conditions you might encounter in national parks.",
            },
            {
                name: "Paul",
                role: "user",
                content: "Good idea. Are there any specific preparations I should make for the national parks?",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "Absolutely! For national parks, it's a good idea to get an America the Beautiful Pass, which covers entrance fees to all national parks for a year. Also, make reservations for campsites or lodges well in advance, especially for popular parks like Yellowstone and Yosemite.",
            },
            {
                name: "Paul",
                role: "user",
                content: "Great tip about the pass! What about food? Should I plan to cook or eat out mostly?",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "A mix of both can be good. In cities, you'll want to try local cuisines. For national parks, having some cooking equipment can save money and be convenient. Maybe plan to cook simple meals while camping and eat out in cities or when you want to treat yourself.",
            },
            {
                name: "Paul",
                role: "user",
                content: "That makes sense. Are there any must-try foods in the cities we'll be visiting?",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "Absolutely! In Chicago, try deep-dish pizza and a Chicago-style hot dog. In San Francisco, don't miss the clam chowder in a sourdough bread bowl at Fisherman's Wharf. Las Vegas has world-class restaurants from celebrity chefs. And in Los Angeles, try some authentic tacos or Korean BBQ.",
            },
            {
                name: "Paul",
                role: "user",
                content: "Yum! Now, what about budgeting? How much should I expect to spend on a trip like this?",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "Budget can vary widely, but for a 3-week trip, you might budget around $4000-$6000 for one person. This would cover gas, accommodations, food, and some activities. You can save by camping in national parks and cooking some meals, or spend more for hotels and restaurants.",
            },
            {
                name: "Paul",
                role: "user",
                content: "That's helpful, thanks. Last question: what are some essential items I shouldn't forget to pack?",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "Great question! Don't forget: a good map or GPS, first-aid kit, flashlight, multi-tool, reusable water bottles, sunscreen, insect repellent, comfortable hiking shoes, and layers of clothing for various climates. Also, a camera to capture memories, and portable chargers for your devices.",
            },
            {
                name: "Paul",
                role: "user",
                content: "Perfect, I think I'm all set to start planning in detail. Thanks for all your help!",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "You're welcome, Paul! Enjoy your epic road trip across the US. Don't hesitate to ask if you need any more advice as you plan. Safe travels!",
            },
        ],
        // Conversation 4: Alex's personal information
        [
            {
                name: "Alex",
                role: "user",
                content: "Hi there! I thought I'd share some facts about myself today.",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "That's great, Alex! I'd love to learn more about you. Please, go ahead and share whatever you're comfortable with.",
            },
            {
                name: "Alex",
                role: "user",
                content: "Well, I'm 28 years old and I work as a software engineer at a tech startup.",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "That's interesting! Software engineering is a dynamic field. How long have you been working in this role?",
            },
            {
                name: "Alex",
                role: "user",
                content: "I've been in this role for about 3 years now. Before that, I studied Computer Science at UCLA.",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "UCLA is a great school! Do you have any hobbies or interests outside of work?",
            },
            {
                name: "Alex",
                role: "user",
                content: "Yes, I'm really into rock climbing. I try to go to the climbing gym at least twice a week, and I love outdoor climbing trips when I can manage them.",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "Rock climbing sounds exciting! It's a great way to stay active and challenge yourself. Have you been on any memorable climbing trips recently?",
            },
            {
                name: "Alex",
                role: "user",
                content: "Actually, yes! I just got back from a trip to Joshua Tree National Park. The rock formations there are amazing for bouldering.",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "Joshua Tree is beautiful! It's wonderful that you can combine your love for the outdoors with your hobby. Do you have any other interests or things you're passionate about?",
            },
            {
                name: "Alex",
                role: "user",
                content: "I'm also really into cooking. I love trying out new recipes, especially dishes from different cultures. Last week, I attempted to make sushi for the first time!",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "Cooking is a great skill to have! How did your sushi-making attempt go? It can be quite challenging for beginners.",
            },
            {
                name: "Alex",
                role: "user",
                content: "It was definitely a learning experience! The rolls weren't as tight as I'd like, but the flavor was good. I'm determined to keep practicing.",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "That's a great attitude! Practice makes perfect. Is there anything else you'd like to share about yourself?",
            },
            {
                name: "Alex",
                role: "user",
                content: "Well, I'm originally from Boston, but I moved to San Francisco for work about 5 years ago. I love the tech scene here, but I do miss the changing seasons back east.",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "Moving across the country is a big change! San Francisco and Boston are quite different. Do you get to visit your hometown often?",
            },
            {
                name: "Alex",
                role: "user",
                content: "I try to go back for the holidays each year. Oh, and I should mention, I have a rescue dog named Pixel. She's a mix of who-knows-what, but she's the best companion I could ask for.",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "Pixel sounds lovely! Rescue dogs often make the most loyal companions. Does she join you on any of your outdoor adventures?",
            },
            {
                name: "Alex",
                role: "user",
                content: "She does! Pixel loves hiking, and she's even been camping with me a few times. She's not much for rock climbing though - she prefers to watch from the ground.",
            },
            {
                name: "assistant",
                role: "assistant",
                content: "It's great that you and Pixel can share outdoor experiences together. Thank you for sharing all these interesting facts about yourself, Alex. You seem to lead a rich and varied life!",
            },
        ],
    ];