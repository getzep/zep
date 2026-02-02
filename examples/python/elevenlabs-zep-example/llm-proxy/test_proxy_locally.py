"""
Test the proxy server locally without ElevenLabs.

This script simulates what ElevenLabs would send to test that:
1. The proxy starts correctly
2. Authentication works (PROXY_API_KEY)
3. Zep context is fetched and injected
4. OpenAI responds correctly
"""

import asyncio
import os
import httpx
import json
from dotenv import load_dotenv

# Load environment variables to get the proxy API key
load_dotenv()

PROXY_API_KEY = os.getenv("PROXY_API_KEY")


async def test_proxy():
    """Send a test request to the proxy server."""

    # The request format that ElevenLabs sends (OpenAI-compatible)
    test_request = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful voice assistant named Eva. Be conversational and friendly."
            },
            {
                "role": "user",
                "content": "Hi there! Do you remember anything about me?"
            }
        ],
        "stream": True,
        "temperature": 0.7,
        # This is how you'd pass the user_id and conversation_id from ElevenLabs
        "elevenlabs_extra_body": {
            "user_id": "test-user-123",  # This matches what we created in setup_test_user.py
            "conversation_id": "test-conv-123"  # Required for Zep thread tracking
        }
    }

    print("=" * 60)
    print("Testing ElevenLabs-Zep Proxy")
    print("=" * 60)
    print(f"\nSending request with user_id: {test_request['elevenlabs_extra_body']['user_id']}")
    print(f"User message: {test_request['messages'][-1]['content']}")
    print(f"Using PROXY_API_KEY: {PROXY_API_KEY[:10]}..." if PROXY_API_KEY else "WARNING: No PROXY_API_KEY found!")
    print("\n" + "-" * 60)
    print("Response from proxy (streaming):")
    print("-" * 60)

    # Include the API key in the Authorization header
    headers = {}
    if PROXY_API_KEY:
        headers["Authorization"] = f"Bearer {PROXY_API_KEY}"

    async with httpx.AsyncClient() as client:
        async with client.stream(
            "POST",
            "http://localhost:8080/v1/chat/completions",
            json=test_request,
            headers=headers,
            timeout=30.0
        ) as response:
            if response.status_code != 200:
                print(f"Error: HTTP {response.status_code}")
                print(await response.aread())
                return

            # Process the SSE stream
            full_response = ""
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data = line[6:]  # Remove "data: " prefix

                    if data == "[DONE]":
                        break

                    try:
                        chunk = json.loads(data)
                        if "choices" in chunk and chunk["choices"]:
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                print(content, end="", flush=True)
                                full_response += content
                    except json.JSONDecodeError:
                        pass

    print("\n" + "-" * 60)
    print(f"Full response length: {len(full_response)} characters")
    print("=" * 60)


async def test_health():
    """Test the health endpoint."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get("http://localhost:8080/health", timeout=5.0)
            print(f"Health check: {response.json()}")
            return True
        except httpx.ConnectError:
            print("ERROR: Cannot connect to proxy server. Make sure it's running:")
            print("  python proxy_server.py")
            return False


async def main():
    print("\nChecking if proxy server is running...")
    if await test_health():
        print("\n")
        await test_proxy()
    else:
        print("\nProxy server is not running. Start it first with:")
        print("  cd elevenlabs-zep-proxy")
        print("  python proxy_server.py")


if __name__ == "__main__":
    asyncio.run(main())
