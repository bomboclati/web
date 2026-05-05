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
    
    def calculate_xp(self, guild_id: int, member: discord.Member, base_xp: int) -> tuple:
        """Calculate XP with all multipliers. Returns (final_xp, multipliers_applied)."""
        config = dm.get_guild_data(guild_id, "leveling_config", {})
        final = float(base_xp)
        applied = []
        
        # 1. Global Multiplier / Double XP
        if config.get("double_xp_enabled"):
            final *= 2.0
            applied.append("Double XP (2x)")

        # 2. Weekend bonus
        if datetime.datetime.now().weekday() >= 5:
            final *= 2.0
            applied.append("weekend (2x)")
        
        # 3. Role Multipliers
        role_mults = config.get("xp_multiplier_roles", {})
        highest_mult = 1.0
        for role_id_str, mult in role_mults.items():
            if any(str(r.id) == role_id_str for r in member.roles):
                highest_mult = max(highest_mult, float(mult))

        if highest_mult > 1.0:
            final *= highest_mult
            applied.append(f"Role Bonus ({highest_mult}x)")

        # 4. Active multipliers (manual event toggles)
        for name, mult in self.get_active_multipliers(guild_id):
            final *= mult
            applied.append(f"{name} ({mult}x)")
        
        # 5. Streak bonus
        streak_bonus = self.get_streak_bonus(guild_id, member.id)
        if streak_bonus > 1.0:
            final *= streak_bonus
            applied.append(f"streak {self.get_streak(guild_id, member.id)} ({streak_bonus}x)")
        
        return int(final), applied
    
    async def handle_message(self, message: discord.Message):
        """Passive XP gain per message with multipliers."""
        if message.author.bot or not message.guild:
            return
        
        config = dm.get_guild_data(message.guild.id, "leveling_config", {})
        if not config.get("enabled", True):
            return

        # Check for no-XP channels
        if message.channel.id in config.get("no_xp_channel_ids", []):
            return

        # Check for no-XP roles
        if any(r.id in config.get("no_xp_role_ids", []) for r in message.author.roles):
            return

        # XP Cooldown check
        user_id = message.author.id
        guild_id = message.guild.id
        cooldown = config.get("xp_cooldown_seconds", 60)

        # We can store last XP gain in a temp cache or guild data
        last_xp_key = f"last_xp_{user_id}"
        last_xp_time = dm.get_guild_data(guild_id, last_xp_key, 0)
        if time.time() - last_xp_time < cooldown:
            return

        xp_min = config.get("xp_per_message_min", 15)
        xp_max = config.get("xp_per_message_max", 25)
        base_xp = random.randint(xp_min, xp_max)

        final_xp, applied = self.calculate_xp(guild_id, message.author, base_xp)
        
        new_level = self.add_xp(guild_id, user_id, final_xp)
        dm.update_guild_data(guild_id, last_xp_key, time.time())
        
        if new_level and config.get("level_up_announcements", True):
            bonus_text = " + ".join(applied) if applied else ""

            # Use custom level up message if set
            msg_template = config.get("level_up_message", "Congratulations {user}, you leveled up to level {level}!")
            content = msg_template.replace("{user}", message.author.mention).replace("{level}", str(new_level))

            embed = discord.Embed(
                title="Level Up!",
                description=content,
                color=discord.Color.gold()
            )
            if bonus_text:
                embed.add_field(name="Bonuses", value=bonus_text, inline=False)

            # Send to specific channel if configured
            target_ch_id = config.get("level_up_channel_id")
            target_ch = message.guild.get_channel(target_ch_id) if target_ch_id else message.channel

            try:
                await target_ch.send(embed=embed)
            except:
                pass

            # Role rewards logic
            rewards = dm.get_guild_data(guild_id, "level_rewards", {})
            role_to_give_id = rewards.get(str(new_level))
            if role_to_give_id:
                role = message.guild.get_role(int(role_to_give_id))
                if role:
                    try:
                        # Optional: remove previous roles
                        if config.get("remove_previous_roles"):
                            for lvl, rid in rewards.items():
                                if int(lvl) < new_level:
                                    prev_role = message.guild.get_role(int(rid))
                                    if prev_role and prev_role in message.author.roles:
                                        await message.author.remove_roles(prev_role)

                        await message.author.add_roles(role)
                    except:
                        pass
    
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

    def get_hourly_stats(self, guild_id: int, hours: int = 24) -> dict:
        """
        Get hourly statistics for the past N hours.
        Returns a dictionary with hourly data for message count, unique chatters, and XP gained.
        """
        # This would typically pull from a time-series database or cache
        # For now, we'll return a placeholder structure that the analytics system expects
        # In a full implementation, this would track hourly metrics
        
        # Try to load cached hourly data if available
        hourly_cache_key = f"leveling_hourly_stats_{guild_id}"
        cached_data = dm.get_guild_data(guild_id, hourly_cache_key, {})
        
        if cached_data:
            # Return the last N hours of data
            sorted_hours = sorted(cached_data.keys(), reverse=True)[:hours]
            return {hour: cached_data[hour] for hour in sorted_hours if hour in cached_data}
        
        # If no cached data, return empty structure
        return {}

    # Prefix command handlers
    async def handle_rank(self, message):
        """Handle !rank prefix command"""
        from actions import ActionHandler
        handler = ActionHandler(message.guild._state._get_client())
        return await handler.handle_leveling_rank(message)

    async def handle_leveling_leaderboard(self, message):
        """Handle !lvlleaderboard prefix command"""
        from actions import ActionHandler
        handler = ActionHandler(message.guild._state._get_client())
        return await handler.handle_leveling_leaderboard(message)

    async def handle_levels(self, message):
        """Handle !levels prefix command"""
        from actions import ActionHandler
        handler = ActionHandler(message.guild._state._get_client())
        return await handler.handle_leveling_levels(message)

    async def handle_rewards(self, message):
        """Handle !rewards prefix command"""
        from actions import ActionHandler
        handler = ActionHandler(message.guild._state._get_client())
        return await handler.handle_leveling_rewards(message)
