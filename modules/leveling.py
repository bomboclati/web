import discord
from data_manager import dm
import math
import random
import time
import datetime

class Leveling:
    """
    XP per message, Leveling Up, and Gems.
    Now includes XP multipliers and streak bonuses!
    """
    def __init__(self, bot):
        self.bot = bot
    
    """XP Multipliers"""
    XP_MULTIPLIERS = {
        "weekend": 2.0,      # 2x XP on weekends
        "vip": 1.5,         # 1.5x XP for VIPs
        "new_user": 2.0,    # 2x XP for first 7 days
        "event": 3.0,       # 3x XP during events
    }
    
    def get_xp(self, guild_id: int, user_id: int) -> int:
        xp_data = dm.get_guild_data(guild_id, "leveling_xp", {})
        return xp_data.get(str(user_id), 0)
    
    def add_xp(self, guild_id: int, user_id: int, amount: int, bonus_multiplier: float = 1.0):
        xp_data = dm.get_guild_data(guild_id, "leveling_xp", {})
        current = xp_data.get(str(user_id), 0)
        
        # Apply streak bonus
        streak_bonus = self.get_streak_bonus(guild_id, user_id)
        
        # Apply all multipliers
        final_amount = int(amount * bonus_multiplier * streak_bonus)
        new_xp = current + final_amount
        xp_data[str(user_id)] = new_xp
        dm.update_guild_data(guild_id, "leveling_xp", xp_data)
        
        # Update streak
        self.update_streak(guild_id, user_id)
        
        # Check for level up
        old_level = self.get_level_from_xp(current)
        new_level = self.get_level_from_xp(new_xp)
        
        if new_level > old_level:
            return new_level
        return None
    
    """Streak System"""
    def get_streak(self, guild_id: int, user_id: int) -> int:
        streaks = dm.get_guild_data(guild_id, "xp_streaks", {})
        return streaks.get(str(user_id), 0)
    
    def get_streak_bonus(self, guild_id: int, user_id: int) -> float:
        """Get XP multiplier based on streak. Max 2.0x at 30+ streak."""
        streak = self.get_streak(guild_id, user_id)
        if streak < 3:
            return 1.0
        elif streak < 7:
            return 1.25
        elif streak < 14:
            return 1.5
        elif streak < 30:
            return 1.75
        else:
            return 2.0
    
    def update_streak(self, guild_id: int, user_id: int):
        """Update streak - increment if active today, reset if not."""
        streaks = dm.get_guild_data(guild_id, "xp_streaks", {})
        last_active = streaks.get(f"{user_id}_last", 0)
        
        current_time = time.time()
        day_seconds = 86400
        
        if current_time - last_active < day_seconds * 2:
            # Active within 2 days - increment streak
            streaks[str(user_id)] = streaks.get(str(user_id), 0) + 1
        else:
            # Inactive too long - reset
            streaks[str(user_id)] = 1
        
        streaks[f"{user_id}_last"] = current_time
        dm.update_guild_data(guild_id, "xp_streaks", streaks)
    
    def get_level_from_xp(self, xp: int) -> int:
        return int(math.sqrt(xp / 100)) if xp > 0 else 0
    
    def get_gems(self, guild_id: int, user_id: int) -> int:
        xp = self.get_xp(guild_id, user_id)
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
    
    """XP Multiplier Management"""
    def get_active_multipliers(self, guild_id: int) -> list:
        config = dm.get_guild_data(guild_id, "xp_multipliers", {})
        active = []
        for name, multiplier in self.XP_MULTIPLIERS.items():
            if config.get(name, False):
                active.append((name, multiplier))
        return active
    
    def set_multiplier(self, guild_id: int, name: str, enabled: bool):
        config = dm.get_guild_data(guild_id, "xp_multipliers", {})
        config[name] = enabled
        dm.update_guild_data(guild_id, "xp_multipliers", config)
    
    def calculate_xp(self, guild_id: int, user_id: int, base_xp: int) -> tuple:
        """Calculate XP with all multipliers. Returns (final_xp, multipliers_applied)."""
        final = base_xp
        applied = []
        
        # Weekend bonus
        if datetime.datetime.now().weekday() >= 5:
            final *= 2.0
            applied.append("weekend (2x)")
        
        # Active multipliers
        for name, mult in self.get_active_multipliers(guild_id):
            final *= mult
            applied.append(f"{name} ({mult}x)")
        
        # Streak bonus
        streak_bonus = self.get_streak_bonus(guild_id, user_id)
        if streak_bonus > 1.0:
            final = int(final * streak_bonus)
            applied.append(f"streak {self.get_streak(guild_id, user_id)} ({streak_bonus}x)")
        
        return int(final), applied
    
    async def handle_message(self, message: discord.Message):
        """Passive XP gain per message with multipliers."""
        if message.author.bot or not message.guild:
            return
        
        base_xp = random.randint(5, 15)
        final_xp, applied = self.calculate_xp(message.guild.id, message.author.id, base_xp)
        
        new_level = self.add_xp(message.guild.id, message.author.id, final_xp)
        
        if new_level:
            bonus_text = " + ".join(applied) if applied else ""
            embed = discord.Embed(
                title="Level Up!",
                description=f"🎉 {message.author.mention} reached level {new_level}!",
                color=discord.Color.gold()
            )
            if bonus_text:
                embed.add_field(name="Bonuses", value=bonus_text, inline=False)
            await message.channel.send(embed=embed)
    
    """Leaderboard with streaks"""
    def get_leaderboard(self, guild_id: int, limit: int = 10) -> list:
        xp_data = dm.get_guild_data(guild_id, "leveling_xp", {})
        streaks = dm.get_guild_data(guild_id, "xp_streaks", {})
        
        sorted_users = sorted(xp_data.items(), key=lambda x: x[1], reverse=True)[:limit]
        
        leaderboard = []
        for i, (user_id, xp) in enumerate(sorted_users, 1):
            user = self.bot.get_user(int(user_id))
            name = user.display_name if user else f"User {user_id}"
            streak = streaks.get(str(user_id), 0)
            leaderboard.append({
                "rank": i,
                "user_id": int(user_id),
                "name": name,
                "xp": xp,
                "level": self.get_level_from_xp(xp),
                "streak": streak
            })
        return leaderboard
