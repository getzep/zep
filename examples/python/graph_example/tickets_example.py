"""
Example of using Zep Graph API to create a concert ticket purchasing scenario.
This playground demonstrates user interactions with a ticket sales system,
mixing chat messages and purchase events to build a knowledge graph.
"""

import asyncio
import os
import uuid
import json
from dotenv import find_dotenv, load_dotenv

from zep_cloud.client import AsyncZep
from zep_cloud.types import Message

load_dotenv(dotenv_path=find_dotenv())

API_KEY = os.environ.get("ZEP_API_KEY") or "YOUR_API_KEY"

async def create_ticket_playground() -> None:
    client = AsyncZep(api_key=API_KEY)
    
    # Create a user for the playground
    user_id = uuid.uuid4().hex
    await client.user.add(user_id=user_id, first_name="Sarah", last_name="Smith", email="sarah.smith@example.com")
    print(f"Created playground user: {user_id}")


    # Sample user interactions and system events
    episodes = [
        {
            "type": "message",
            "data": "Sarah (user): Hi, I'm looking for Taylor Swift concert tickets in New York, I am a huge fan!"
        },
        {
            "type": "json",
            "data": {
                "event_type": "search_performed",
                "user_id": user_id,
                "artist": "Taylor Swift",
                "location": "New York",
                "date_range": "2024-07",
                "timestamp": "2024-01-15T10:30:00Z"
            }
        },
        {
            "type": "message",
            "data": "TicketSalesBot (assistant): Hi Sarah, welcome to the TicketSales.  I found 2 Taylor Swift concerts at Madison Square Garden on July 15 and 16, 2024. Tickets start at $199."
        },
        {
            "type": "message",
            "data": "Sarah (user): Great! I'd like 2 tickets for July 15th please."
        },
        {
            "type": "json",
            "data": {
                "event_type": "ticket_purchase",
                "user_id": user_id,
                "email": "sarah.smith@example.com",
                "concert_id": "TS-MSG-0715",
                "artist": "Taylor Swift",
                "venue": "Madison Square Garden",
                "date": "2024-07-15",
                "quantity": 2,
                "seat_type": "Floor",
                "price_per_ticket": 199,
                "total_amount": 398,
                "transaction_id": "TRX-12345",
                "purchase_timestamp": "2024-01-15T10:35:00Z"
            }
        },
        {
            "type": "message",
            "data": "Sarah (user): Are there any upcoming Arctic Monkeys concerts?"
        },
        {
            "type": "json",
            "data": {
                "event_type": "search_performed",
                "user_id": user_id,
                "artist": "Arctic Monkeys",
                "timestamp": "2024-01-15T10:40:00Z"
            }
        },
        {
            "type": "message",
            "data": "TicketSalesBot (assistant): Yes! Arctic Monkeys are playing at Barclays Center on August 5th, 2024."
        },
        {
            "type": "message",
            "data": "Sarah (user): Can you add me to the waitlist for that concert?"
        },
        {
            "type": "json",
            "data": {
                "event_type": "waitlist_addition",
                "user_id": user_id,
                "concert_id": "AM-BC-0805",
                "artist": "Arctic Monkeys",
                "venue": "Barclays Center",
                "date": "2024-08-05",
                "timestamp": "2024-01-15T10:42:00Z"
            }
        },
        {
            "type": "message",
            "data": "System Notification - Arctic Monkeys tickets are now available for waitlist members!"
        },
        {
            "type": "json",
            "data": {
                "event_type": "ticket_purchase",
                "user_id": user_id,
                "concert_id": "AM-BC-0805",
                "artist": "Arctic Monkeys",
                "venue": "Barclays Center",
                "date": "2024-08-05",
                "quantity": 1,
                "seat_type": "General Admission",
                "price_per_ticket": 150,
                "total_amount": 150,
                "transaction_id": "TRX-12346",
                "purchase_timestamp": "2024-01-15T14:20:00Z"
            }
        }
    ]

    # Add all episodes to the graph
    for episode in episodes:
        if episode["type"] == "json":
            await client.graph.add(
                user_id=user_id,
                type="json",
                data=json.dumps(episode["data"]),
            )
        else:  # message type
            await client.graph.add(
                user_id=user_id,
                type="message",
                data=episode["data"],
            )
    
    print("Added all ticket purchase episodes to the graph")
    print("Waiting for graph processing...")
    await asyncio.sleep(30)

    episodes = await client.graph.episode.get_by_user_id(user_id=user_id)
    print(episodes)
    
    
    return user_id

if __name__ == "__main__":
    user_id = asyncio.run(create_ticket_playground())
    print(f"\nPlayground ready! User ID: {user_id}")
    print("You can now explore the ticket purchase graph and add new episodes!") 