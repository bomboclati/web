import os
import aiohttp
import random
from typing import Optional

class GIFManager:
    """
    Random GIF integration for welcome/goodbye/level-up.
    Supports Tenor, GIPHY, and Built-in Pools.
    """
    BUILTIN_GIRLS = {
        "welcome": ["https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExcjk0ODJ4eHd5NXo5eHhyeHh4eHh4eHh4eHh4eHh4eHh4eHgmZXA9djFfaW50ZXJuYWxfZ2lmX2J5X2lkJmN0PWc/ASd0UkjqhZ3P2/giphy.gif"],
        "level_up": ["https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExcjk0ODJ4eHd5NXo5eHhyeHh4eHh4eHh4eHh4eHh4eHh4eHgmZXA9djFfaW50ZXJuYWxfZ2lmX2J5X2lkJmN0PWc/12S5z7Z2y1P2sM/giphy.gif"],
        "success": ["https://media.giphy.com/media/v1.Y2lkPTc5MGI3NjExcjk0ODJ4eHd5NXo5eHhyeHh4eHh4eHh4eHh4eHh4eHh4eHgmZXA9djFfaW50ZXJuYWxfZ2lmX2J5X2lkJmN0PWc/3o7TKVUn7iM8FMEU24/giphy.gif"]
    }

    async def get_random_gif(self, search_term: str) -> str:
        tenor_key = os.getenv("TENOR_API_KEY")
        if tenor_key:
            async with aiohttp.ClientSession() as session:
                url = f"https://tenor.googleapis.com/v2/search?q={search_term}&key={tenor_key}&limit=10"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("results"):
                            return random.choice(data["results"])["media_formats"]["gif"]["url"]
        
        # Fallback to local pool
        pool = self.BUILTIN_GIRLS.get(search_term, self.BUILTIN_GIRLS["success"])
        return random.choice(pool)

# Global GIF Manager
gif_manager = GIFManager()
