import discord
from discord import ui
import asyncio
import json
import time
from typing import Dict, List, Optional
from dataclasses import dataclass

from data_manager import dm
from logger import logger
import os

@dataclass
class Achievement:
    id: str
    name: str
    description: str
    category: str
    icon: str
    requirement: dict
    reward: dict
    rarity: float = 0.5
    hidden: bool = False

class AchievementSystem:
    def __init__(self, bot):
        self.bot = bot
        self._achievements: Dict[str, Achievement] = {}
        self._notifications_enabled = False
        self._load_achievements()
        self._init_default_achievements()

    def _load_achievements(self):
        data = dm.load_json("achievements", default={})
        for ach_id, ach_data in data.items():
            self._achievements[ach_id] = Achievement(id=ach_id, **ach_data)

    def _save_achievements(self):
        data = {ach_id: vars(ach) for ach_id, ach in self._achievements.items()}
        dm.save_json("achievements", data)

    def _init_default_achievements(self):
        defaults = [
            # Activity
            Achievement("msg_1", "First Message", "Sent your first message", "activity", "💬", {"type": "messages", "count": 1}, {"xp": 10}),
            Achievement("msg_100", "Centurion", "Sent 100 messages", "activity", "💯", {"type": "messages", "count": 100}, {"coins": 100}),
            Achievement("msg_1000", "Chatterbox", "Sent 1,000 messages", "activity", "🗣️", {"type": "messages", "count": 1000}, {"coins": 500}),
            Achievement("msg_10000", "Legendary Talker", "Sent 10,000 messages", "activity", "👑", {"type": "messages", "count": 10000}, {"coins": 5000}),

            # Voice
            Achievement("vc_1", "Hello?", "Joined your first voice chat", "voice", "🎤", {"type": "voice_minutes", "count": 1}, {"xp": 20}),
            Achievement("vc_60", "Radio Host", "Spent 1 hour in voice", "voice", "📻", {"type": "voice_minutes", "count": 60}, {"coins": 100}),
            Achievement("vc_600", "Podcaster", "Spent 10 hours in voice", "voice", "🎧", {"type": "voice_minutes", "count": 600}, {"coins": 1000}),
            Achievement("vc_6000", "Voice Legend", "Spent 100 hours in voice", "voice", "🎙️", {"type": "voice_minutes", "count": 6000}, {"coins": 10000}),

            # Level
            Achievement("lvl_5", "Rising Star", "Reached Level 5", "progression", "🌟", {"type": "level", "count": 5}, {"coins": 100}),
            Achievement("lvl_25", "Veteran", "Reached Level 25", "progression", "🎖️", {"type": "level", "count": 25}, {"coins": 1000}),
            Achievement("lvl_100", "Ancient One", "Reached Level 100", "progression", "🗿", {"type": "level", "count": 100}, {"coins": 10000}),

            # Economy
            Achievement("eco_earn", "First Payday", "Earned 10,000 coins", "economy", "💰", {"type": "total_coins", "count": 10000}, {"xp": 100}),
            Achievement("eco_spend", "Big Spender", "Spent 1,000 coins", "economy", "💸", {"type": "spent_coins", "count": 1000}, {"xp": 50}),

            # Streaks
            Achievement("streak_7", "Weekly Warrior", "7-Day Activity Streak", "streaks", "🔥", {"type": "streak", "count": 7}, {"coins": 200}),
            Achievement("streak_30", "Month of Fire", "30-Day Activity Streak", "streaks", "🌋", {"type": "streak", "count": 30}, {"coins": 1000}),

            # Staff
            Achievement("staff_1", "New Badge", "Performed your first moderation action", "staff", "🛡️", {"type": "mod_actions", "count": 1}, {"xp": 100}),
            Achievement("staff_100", "Enforcer", "Performed 100 moderation actions", "staff", "🔨", {"type": "mod_actions", "count": 100}, {"coins": 500}),
        ]
        for ach in defaults:
            if ach.id not in self._achievements:
                self._achievements[ach.id] = ach
        self._save_achievements()

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "achievements_config", {
            "enabled": True,
            "announcement_channel": None,
            "unlock_dms": True,
            "default_rewards": {"coins": 0, "xp": 0}
        })

    async def check_achievements(self, guild_id: int, user_id: int):
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        awarded = dm.load_json("awarded_badges", default={})
        gid_str, uid_str = str(guild_id), str(user_id)

        if gid_str not in awarded: awarded[gid_str] = {}
        if uid_str not in awarded[gid_str]: awarded[gid_str][uid_str] = []
        earned_ids = awarded[gid_str][uid_str]
        
        new_earned = []
        for ach_id, ach in self._achievements.items():
            if ach_id in earned_ids: continue
            
            req = ach.requirement
            rtype = req.get("type")
            rval = req.get("count", 1)
            earned_now = False
            
            if rtype == "messages": earned_now = user_data.get("total_messages", 0) >= rval
            elif rtype == "voice_minutes": earned_now = user_data.get("voice_minutes", 0) >= rval
            elif rtype == "level": earned_now = user_data.get("level", 1) >= rval
            elif rtype == "total_coins": earned_now = user_data.get("total_coins_earned", 0) >= rval
            elif rtype == "spent_coins": earned_now = user_data.get("total_coins_spent", 0) >= rval
            elif rtype == "streak": earned_now = user_data.get("current_streak", 0) >= rval
            elif rtype == "mod_actions": earned_now = user_data.get("mod_actions", 0) >= rval
            
            if earned_now:
                await self._award_achievement(guild_id, user_id, ach)
                new_earned.append(ach)
        
        return new_earned

    async def _award_achievement(self, guild_id: int, user_id: int, achievement: Achievement):
        awarded = dm.load_json("awarded_badges", default={})
        gid_str, uid_str = str(guild_id), str(user_id)
        if gid_str not in awarded: awarded[gid_str] = {}
        if uid_str not in awarded[gid_str]: awarded[gid_str][uid_str] = []
        
        if achievement.id not in awarded[gid_str][uid_str]:
            awarded[gid_str][uid_str].append(achievement.id)
            dm.save_json("awarded_badges", awarded)

            # Apply rewards
            reward = achievement.reward
            if reward:
                user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
                user_data["coins"] = user_data.get("coins", 0) + reward.get("coins", 0)
                user_data["xp"] = user_data.get("xp", 0) + reward.get("xp", 0)
                dm.update_guild_data(guild_id, f"user_{user_id}", user_data)

            if self._notifications_enabled:
                await self._notify_achievement(guild_id, user_id, achievement)

    async def _notify_achievement(self, guild_id: int, user_id: int, ach: Achievement):
        settings = self.get_guild_settings(guild_id)
        guild = self.bot.get_guild(guild_id)
        if not guild: return
        member = guild.get_member(user_id)
        if not member: return

        embed = discord.Embed(
            title="🏆 Achievement Unlocked!",
            description=f"{member.mention} earned **{ach.icon} {ach.name}**\n*{ach.description}*",
            color=discord.Color.gold()
        )
        if ach.reward:
            rewards = []
            if ach.reward.get("coins"): rewards.append(f"💰 {ach.reward['coins']} coins")
            if ach.reward.get("xp"): rewards.append(f"✨ {ach.reward['xp']} XP")
            if rewards: embed.add_field(name="Rewards", value=", ".join(rewards))

        # Channel
        ch_id = settings.get("announcement_channel")
        if ch_id:
            channel = guild.get_channel(int(ch_id))
            if channel: await channel.send(embed=embed)
        
        # DM
        if settings.get("unlock_dms", True):
            try: await member.send(embed=embed)
            except: pass

    def get_user_achievements(self, guild_id: int, user_id: int) -> List[dict]:
        awarded = dm.load_json("awarded_badges", default={})
        earned_ids = awarded.get(str(guild_id), {}).get(str(user_id), [])
        
        result = []
        for ach_id in earned_ids:
            ach = self._achievements.get(ach_id)
            if ach:
                result.append({
                    "id": ach.id,
                    "name": ach.name,
                    "description": ach.description,
                    "icon": ach.icon,
                    "category": ach.category,
                    "earned_at": time.time()
                })
        return result

    def get_user_titles(self, guild_id: int, user_id: int) -> List[dict]:
        # Backward compatibility or simplified titles based on level
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        level = user_data.get("level", 1)
        
        titles = []
        if level >= 1: titles.append({"id": "newcomer", "name": "Newcomer", "icon": "🌱"})
        if level >= 10: titles.append({"id": "regular", "name": "Regular", "icon": "⭐"})
        if level >= 25: titles.append({"id": "veteran", "name": "Veteran", "icon": "🏅"})
        if level >= 50: titles.append({"id": "elite", "name": "Elite", "icon": "💎"})
        if level >= 100: titles.append({"id": "legend", "name": "Legend", "icon": "👑"})
        
        return titles

    def get_active_title(self, guild_id: int, user_id: int) -> Optional[dict]:
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        active_id = user_data.get("active_title_id")
        if active_id:
            titles = self.get_user_titles(guild_id, user_id)
            for t in titles:
                if t["id"] == active_id: return t
        
        user_titles = self.get_user_titles(guild_id, user_id)
        if user_titles:
            return user_titles[-1] # Highest title is active
        return None

    def set_active_title(self, guild_id: int, user_id: int, title_id: str) -> bool:
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        user_data["active_title_id"] = title_id
        dm.update_guild_data(guild_id, f"user_{user_id}", user_data)
        return True

    def get_leaderboard(self, guild_id: int) -> List[dict]:
        awarded = dm.load_json("awarded_badges", default={})
        guild_awarded = awarded.get(str(guild_id), {})
        
        leaderboard = []
        for uid_str, achs in guild_awarded.items():
            leaderboard.append({"user_id": int(uid_str), "achievements": len(achs)})
        
        leaderboard.sort(key=lambda x: x["achievements"], reverse=True)
        for i, entry in enumerate(leaderboard):
            entry["rank"] = i + 1

        return leaderboard[:10]

    async def setup(self, interaction: discord.Interaction, params: dict = None):
        guild = interaction.guild
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "achievements_config", settings)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        custom_cmds["achievements"] = json.dumps({"command_type": "ach_list"})
        custom_cmds["badges"] = json.dumps({"command_type": "ach_badges"})
        custom_cmds["progress"] = json.dumps({"command_type": "ach_progress"})
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        await interaction.followup.send("🏆 Achievements system initialized with default sets and commands.")
        return True
