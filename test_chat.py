import asyncio
import os
import sys

from data_manager import dm
from ai_client import AIClient

async def test():
    bot_ai = AIClient(api_key="test_key", provider="openrouter")
    try:
        print("Running AI chat...")
        result = await bot_ai.chat(1471107222959423681, 1234567, "test prompt", "system prompt")
        print("Result:", result)
    except Exception as e:
        print(f"Error caught {type(e)}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
