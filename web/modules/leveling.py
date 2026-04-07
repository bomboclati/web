import discord
from data_manager import dm
import math
import random

class Leveling:
    """
    XP per message, Leveling Up, and Gems.
    Gems = floor(XP / 10). Spending gems doesn't reduce XP.
    """
    def __init__(self, bot):
        self.bot = bot

    def get_xp(self, guild_id: int, user_id: int) -> int:
        xp_data = dm.get_guild_data(guild_id, "leveling_xp", {})
        return xp_data.get(str(user_id), 0)

    def add_xp(self, guild_id: int, user_id: int, amount: int):
        xp_data = dm.get_guild_data(guild_id, "leveling_xp", {})
        current = xp_data.get(str(user_id), 0)
        new_xp = current + amount
        xp_data[str(user_id)] = new_xp
        dm.update_guild_data(guild_id, "leveling_xp", xp_data)
        
        # Check for level up
        old_level = self.get_level_from_xp(current)
        new_level = self.get_level_from_xp(new_xp)
        
        if new_level > old_level:
            return new_level
        return None

    def get_level_from_xp(self, xp: int) -> int:
        # Level = sqrt(XP / 100)
        return int(math.sqrt(xp / 100)) if xp > 0 else 0

    def get_gems(self, guild_id: int, user_id: int) -> int:
        xp = self.get_xp(guild_id, user_id)
        # Gem calculation logic: floor(xp / gem_ratio)
        ratio = dm.get_guild_data(guild_id, "gem_ratio", 10)
        spent_gems = dm.get_guild_data(guild_id, "spent_gems", {}).get(str(user_id), 0)
        return (xp // ratio) - spent_gems

    def spend_gems(self, guild_id: int, user_id: int, amount: int) -> bool:
        current_gems = self.get_gems(guild_id, user_id)
        if current_gems < amount:
            return False
            
        spent_gems_data = dm.get_guild_data(guild_id, "spent_gems", {})
        current_spent = spent_gems_data.get(str(user_id), 0)
        spent_gems_data[str(user_id)] = current_spent + amount
        dm.update_guild_data(guild_id, "spent_gems", spent_gems_data)
        return True

    async def handle_message(self, message: discord.Message):
        """Passive XP gain per message."""
        if message.author.bot or not message.guild:
            return
            
        # Add random XP between 5-15
        new_level = self.add_xp(message.guild.id, message.author.id, random.randint(5, 15))
        
        if new_level:
            # Level up!
            embed = discord.Embed(
                title="Level Up!",
                description=f"🎉 {message.author.mention} reached level {new_level}!",
                color=discord.Color.gold()
            )
            # Add random GIF logic later
            await message.channel.send(embed=embed)
