import discord
from discord.ext import commands
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
    rarity: float
    hidden: bool


@dataclass
class UserAchievement:
    achievement_id: str
    earned_at: float
    progress: int


@dataclass
class UserTitle:
    title_id: str
    unlocked_at: float
    active: bool


class AchievementSystem:
    def __init__(self, bot):
        self.bot = bot
        self._achievements: Dict[str, Achievement] = {}
        self._titles: Dict[str, dict] = {}
        self._notifications_enabled = False
        self._load_achievements()
        self._init_default_achievements()

    def _load_achievements(self):
        data = dm.load_json("achievements", default={})
        
        for ach_id, ach_data in data.items():
            self._achievements[ach_id] = Achievement(
                id=ach_id,
                name=ach_data["name"],
                description=ach_data["description"],
                category=ach_data["category"],
                icon=ach_data["icon"],
                requirement=ach_data["requirement"],
                reward=ach_data["reward"],
                rarity=ach_data.get("rarity", 0.5),
                hidden=ach_data.get("hidden", False)
            )
        
        titles_data = dm.load_json("titles", default={})
        self._titles = titles_data

    def _save_achievements(self):
        data = {}
        for ach_id, ach in self._achievements.items():
            data[ach_id] = {
                "name": ach.name,
                "description": ach.description,
                "category": ach.category,
                "icon": ach.icon,
                "requirement": ach.requirement,
                "reward": ach.reward,
                "rarity": ach.rarity,
                "hidden": ach.hidden
            }
        dm.save_json("achievements", data)

    def _init_default_achievements(self):
        defaults = [
            # Community
            Achievement("first_join", "First Steps", "Joined the server", "community", "👋", 
                        {"type": "join"}, {"xp": 10}, 0.95, False),
            Achievement("week_member", "Weekly Member", "Been a member for 7 days", "community", "📅",
                        {"type": "days_member", "count": 7}, {"coins": 50}, 0.7, False),
            Achievement("month_member", "Dedicated Member", "Been a member for 30 days", "community", "⭐",
                        {"type": "days_member", "count": 30}, {"coins": 200}, 0.4, False),
            Achievement("century_member", "Old Timer", "Been a member for 100 days", "community", "👴",
                        {"type": "days_member", "count": 100}, {"coins": 500}, 0.2, False),

            # Activity
            Achievement("first_message", "Hello World", "Sent your first message", "activity", "💬",
                        {"type": "messages", "count": 1}, {"xp": 5}, 0.9, False),
            Achievement("chatty", "Chatty", "Sent 100 messages", "activity", "🗣️",
                        {"type": "messages", "count": 100}, {"coins": 100, "xp": 50}, 0.6, False),
            Achievement("prolific", "Prolific", "Sent 1000 messages", "activity", "📣",
                        {"type": "messages", "count": 1000}, {"coins": 500, "xp": 250}, 0.3, False),
            Achievement("legendary_chatter", "Chat Legend", "Sent 10000 messages", "activity", "👑",
                        {"type": "messages", "count": 10000}, {"coins": 5000, "xp": 2500}, 0.05, False),

            # Voice
            Achievement("first_voice", "Mic Check", "Joined voice chat for the first time", "voice", "🎤",
                        {"type": "voice_minutes", "count": 1}, {"xp": 10}, 0.8, False),
            Achievement("voice_1hr", "Radio Host", "Spent 1 hour in voice channels", "voice", "🎧",
                        {"type": "voice_minutes", "count": 60}, {"coins": 100}, 0.5, False),
            Achievement("voice_10hr", "Podcast Pro", "Spent 10 hours in voice channels", "voice", "📻",
                        {"type": "voice_minutes", "count": 600}, {"coins": 500}, 0.2, False),
            Achievement("voice_100hr", "Voice Legend", "Spent 100 hours in voice channels", "voice", "💎",
                        {"type": "voice_minutes", "count": 6000}, {"coins": 2000}, 0.05, False),

            # Leveling
            Achievement("level_5", "Rising Star", "Reached level 5", "progression", "🌟",
                        {"type": "level", "count": 5}, {"coins": 100}, 0.7, False),
            Achievement("level_10", "Established", "Reached level 10", "progression", "💫",
                        {"type": "level", "count": 10}, {"coins": 250, "xp": 100}, 0.5, False),
            Achievement("level_25", "Veteran", "Reached level 25", "progression", "🔥",
                        {"type": "level", "count": 25}, {"coins": 500, "xp": 250}, 0.3, False),
            Achievement("level_50", "Elite", "Reached level 50", "progression", "👑",
                        {"type": "level", "count": 50}, {"coins": 1000, "xp": 500}, 0.15, False),
            Achievement("level_100", "Immortal", "Reached level 100", "progression", "🔱",
                        {"type": "level", "count": 100}, {"coins": 5000, "xp": 2500}, 0.02, False),

            # Economy
            Achievement("first_purchase", "Big Spender", "Made your first purchase in the shop", "economy", "🛒",
                        {"type": "purchases", "count": 1}, {"xp": 20}, 0.7, False),
            Achievement("spend_1000", "Investor", "Spent 1000 coins", "economy", "💸",
                        {"type": "coins_spent", "count": 1000}, {"xp": 100}, 0.4, False),
            Achievement("earn_10000", "Wealthy", "Earned 10000 coins total", "economy", "💰",
                        {"type": "total_coins", "count": 10000}, {"xp": 500}, 0.2, False),
            Achievement("richest", "Capitalist", "Become the richest member in the server", "economy", "🤑",
                        {"type": "richest_member"}, {"coins": 1000}, 0.01, True),

            # Social
            Achievement("first_reaction_given", "Expressive", "Gave your first reaction", "social", "👍",
                        {"type": "reactions_given", "count": 1}, {"xp": 5}, 0.9, False),
            Achievement("first_reaction_received", "Popular", "Received your first reaction", "social", "❤️",
                        {"type": "reactions_received", "count": 1}, {"xp": 5}, 0.9, False),
            Achievement("thread_creator", "Conversationalist", "Created your first thread", "social", "🧵",
                        {"type": "threads_created", "count": 1}, {"xp": 20}, 0.7, False),
            Achievement("helper_10", "Good Samaritan", "Helped 10 members via tickets", "social", "🤝",
                        {"type": "tickets_resolved", "count": 10}, {"coins": 500}, 0.3, False),

            # Events
            Achievement("first_event", "Party Goer", "Joined your first event", "events", "🎉",
                        {"type": "events_joined", "count": 1}, {"xp": 25}, 0.7, False),
            Achievement("attend_10_events", "Regular Attendee", "Attended 10 events", "events", "🎫",
                        {"type": "events_joined", "count": 10}, {"coins": 500}, 0.3, False),
            Achievement("attend_50_events", "Event Addict", "Attended 50 events", "events", "🏟️",
                        {"type": "events_joined", "count": 50}, {"coins": 2000}, 0.1, False),

            # Streaks
            Achievement("streak_7", "Consistent", "Maintain a 7-day activity streak", "streaks", "🔥",
                        {"type": "streak", "count": 7}, {"coins": 200}, 0.5, False),
            Achievement("streak_30", "Dedicated", "Maintain a 30-day activity streak", "streaks", "🌟",
                        {"type": "streak", "count": 30}, {"coins": 1000}, 0.2, False),
            Achievement("streak_100", "Unstoppable", "Maintain a 100-day activity streak", "streaks", "🔱",
                        {"type": "streak", "count": 100}, {"coins": 5000}, 0.05, False),

            # Staff
            Achievement("first_mod_action", "Justice Begins", "Performed your first moderation action", "staff", "⚖️",
                        {"type": "mod_actions", "count": 1}, {"xp": 50}, 0.1, False),
            Achievement("mod_100", "Guardian", "Performed 100 moderation actions", "staff", "🛡️",
                        {"type": "mod_actions", "count": 100}, {"coins": 1000}, 0.05, False),
            Achievement("top_mod_month", "Mod of the Month", "Become the top moderator of the month", "staff", "🥇",
                        {"type": "top_mod_month"}, {"coins": 2000}, 0.01, True),
        ]
        
        for ach in defaults:
            if ach.id not in self._achievements:
                self._achievements[ach.id] = ach
        
        self._save_achievements()
        
        default_titles = [
            {"id": "newcomer", "name": "Newcomer", "icon": "🌱", "requirement": {"type": "days_member", "count": 1}},
            {"id": "regular", "name": "Regular", "icon": "⭐", "requirement": {"type": "days_member", "count": 7}},
            {"id": "veteran", "name": "Veteran", "icon": "🏅", "requirement": {"type": "days_member", "count": 30}},
            {"id": "legend", "name": "Legend", "icon": "👑", "requirement": {"type": "days_member", "count": 100}},
            {"id": "helper", "name": "Helper", "icon": "🛠️", "requirement": {"type": "rep_received", "count": 10}},
            {"id": "champion", "name": "Champion", "icon": "🏆", "requirement": {"type": "events_won", "count": 1}},
            {"id": "elite", "name": "Elite", "icon": "💎", "requirement": {"type": "level", "count": 25}},
        ]
        
        for title in default_titles:
            if title["id"] not in self._titles:
                self._titles[title["id"]] = title
        
        dm.save_json("titles", self._titles)

    def get_guild_settings(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "achievement_settings", {
            "enabled": True,
            "notify_channel": None,
            "notify_dms": True,
            "show_progress": True
        })

    async def check_achievements(self, guild_id: int, user_id: int):
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})

        awarded = dm.load_json("awarded_badges", default={})
        gid_str = str(guild_id)
        uid_str = str(user_id)

        if gid_str not in awarded: awarded[gid_str] = {}
        if uid_str not in awarded[gid_str]: awarded[gid_str][uid_str] = []

        earned_ids = awarded[gid_str][uid_str]
        
        new_earned = []
        
        for ach_id, ach in self._achievements.items():
            if ach_id in earned_ids:
                continue
            
            req = ach.requirement
            earned_now = False
            
            if req["type"] == "join":
                earned_now = True
            elif req["type"] == "days_member":
                member = self.bot.get_guild(guild_id).get_member(user_id)
                if member:
                    days = (discord.utils.utcnow() - member.joined_at).days
                    earned_now = days >= req["count"]
            elif req["type"] == "messages":
                earned_now = user_data.get("total_messages", 0) >= req["count"]
            elif req["type"] == "commands":
                earned_now = user_data.get("total_commands", 0) >= req["count"]
            elif req["type"] == "quests_completed":
                earned_now = user_data.get("quests_completed", 0) >= req["count"]
            elif req["type"] == "giveaways_won":
                earned_now = user_data.get("giveaways_won", 0) >= req["count"]
            elif req["type"] == "events_won":
                earned_now = user_data.get("events_won", 0) >= req["count"]
            elif req["type"] == "events_joined":
                earned_now = user_data.get("events_joined", 0) >= req["count"]
            elif req["type"] == "rep_received":
                earned_now = user_data.get("rep_received", 0) >= req["count"]
            elif req["type"] == "rep_given":
                earned_now = user_data.get("rep_given", 0) >= req["count"]
            elif req["type"] == "level":
                earned_now = user_data.get("level", 1) >= req["count"]
            elif req["type"] == "total_coins":
                earned_now = user_data.get("total_coins_earned", 0) >= req["count"]
            elif req["type"] == "voice_minutes":
                earned_now = user_data.get("voice_minutes", 0) >= req["count"]
            elif req["type"] == "messages_starred":
                earned_now = user_data.get("messages_starred", 0) >= req["count"]
            elif req["type"] == "purchases":
                earned_now = user_data.get("total_purchases", 0) >= req["count"]
            elif req["type"] == "coins_spent":
                earned_now = user_data.get("total_coins_spent", 0) >= req["count"]
            elif req["type"] == "reactions_given":
                earned_now = user_data.get("reactions_given", 0) >= req["count"]
            elif req["type"] == "reactions_received":
                earned_now = user_data.get("reactions_received", 0) >= req["count"]
            elif req["type"] == "threads_created":
                earned_now = user_data.get("threads_created", 0) >= req["count"]
            elif req["type"] == "tickets_resolved":
                earned_now = user_data.get("tickets_resolved", 0) >= req["count"]
            elif req["type"] == "streak":
                streak = self.bot.leveling.get_streak(guild_id, user_id)
                earned_now = streak >= req["count"]
            elif req["type"] == "mod_actions":
                earned_now = user_data.get("mod_actions", 0) >= req["count"]
            
            if earned_now:
                await self._award_achievement(guild_id, user_id, ach, new_earned)
        
        return new_earned

    async def _award_achievement(self, guild_id: int, user_id: int, achievement: Achievement, new_earned: list):
        awarded = dm.load_json("awarded_badges", default={})
        gid_str = str(guild_id)
        uid_str = str(user_id)
        if gid_str not in awarded: awarded[gid_str] = {}
        if uid_str not in awarded[gid_str]: awarded[gid_str][uid_str] = []
        
        if achievement.id not in awarded[gid_str][uid_str]:
            awarded[gid_str][uid_str].append(achievement.id)
            dm.save_json("awarded_badges", awarded)

        earned = dm.get_guild_data(guild_id, f"achievements_{user_id}", [])
        earned.append({
            "achievement_id": achievement.id,
            "earned_at": time.time()
        })
        dm.update_guild_data(guild_id, f"achievements_{user_id}", earned)
        
        reward = achievement.reward
        if reward:
            if "coins" in reward:
                self.bot.economy.add_coins(guild_id, user_id, reward["coins"])
            if "xp" in reward:
                self.bot.leveling.add_xp(guild_id, user_id, reward["xp"])
            if "role_id" in reward:
                guild = self.bot.get_guild(guild_id)
                member = guild.get_member(user_id) if guild else None
                if member:
                    role = guild.get_role(int(reward["role_id"]))
                    if role:
                        try: await member.add_roles(role)
                        except: pass
        
        new_earned.append(achievement)
        await self._notify_achievement(guild_id, user_id, achievement)
        await self._check_title_unlock(guild_id, user_id)

    async def _notify_achievement(self, guild_id: int, user_id: int, achievement: Achievement):
        if not self._notifications_enabled:
            return

        settings = self.get_guild_settings(guild_id)
        guild = self.bot.get_guild(guild_id)
        if not guild: return
        member = guild.get_member(user_id)
        if not member: return

        embed = discord.Embed(
            title="🏆 Achievement Unlocked!",
            description=f"{member.mention} earned **{achievement.icon} {achievement.name}**!",
            color=discord.Color.gold()
        )
        embed.add_field(name="Description", value=achievement.description, inline=False)

        if achievement.reward:
            reward_text = []
            if achievement.reward.get("coins"): reward_text.append(f"💰 {achievement.reward['coins']} coins")
            if achievement.reward.get("xp"): reward_text.append(f"✨ {achievement.reward['xp']} XP")
            if reward_text:
                embed.add_field(name="Reward", value=", ".join(reward_text), inline=True)

        # 1. Post to announcement channel
        notify_ch_id = settings.get("notify_channel")
        if notify_ch_id:
            channel = guild.get_channel(int(notify_ch_id))
            if channel:
                await channel.send(embed=embed)
        else:
            # Fallback to system channel
            if guild.system_channel:
                await guild.system_channel.send(embed=embed)

        # 2. DM user
        if settings.get("notify_dms"):
            try:
                await member.send(embed=embed)
            except: pass

    async def _check_title_unlock(self, guild_id: int, user_id: int):
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        
        for title_id, title in self._titles.items():
            if title_id == "newcomer": continue
            req = title["requirement"]
            unlocked = False
            
            if req["type"] == "days_member":
                member = self.bot.get_guild(guild_id).get_member(user_id)
                if member:
                    days = (discord.utils.utcnow() - member.joined_at).days
                    unlocked = days >= req["count"]
            elif req["type"] == "rep_received":
                unlocked = user_data.get("rep_received", 0) >= req["count"]
            elif req["type"] == "events_won":
                unlocked = user_data.get("events_won", 0) >= req["count"]
            elif req["type"] == "level":
                unlocked = user_data.get("level", 1) >= req["count"]
            
            if unlocked:
                await self._unlock_title(guild_id, user_id, title_id)

    async def _unlock_title(self, guild_id: int, user_id: int, title_id: str):
        user_titles = dm.get_guild_data(guild_id, f"titles_{user_id}", [])
        existing = [t["title_id"] for t in user_titles]

        if title_id not in existing:
            user_titles.append({
                "title_id": title_id,
                "unlocked_at": time.time(),
                "active": False
            })
            dm.update_guild_data(guild_id, f"titles_{user_id}", user_titles)

            if self._notifications_enabled:
                title = self._titles.get(title_id)
                if title:
                    guild = self.bot.get_guild(guild_id)
                    member = guild.get_member(user_id) if guild else None
                    if member:
                        embed = discord.Embed(
                            title="🎖️ Title Unlocked!",
                            description=f"You unlocked the title: **{title['icon']} {title['name']}**",
                            color=discord.Color.gold()
                        )
                        try: await member.send(embed=embed)
                        except: pass

    def set_active_title(self, guild_id: int, user_id: int, title_id: str) -> bool:
        user_titles = dm.get_guild_data(guild_id, f"titles_{user_id}", [])
        for title in user_titles:
            title["active"] = (title["title_id"] == title_id)
        dm.update_guild_data(guild_id, f"titles_{user_id}", user_titles)
        return True

    def get_user_achievements(self, guild_id: int, user_id: int) -> List[dict]:
        awarded = dm.load_json("awarded_badges", default={})
        earned_ids = awarded.get(str(guild_id), {}).get(str(user_id), [])
        
        result = []
        for ach_id in earned_ids:
            ach = self._achievements.get(ach_id)
            if ach:
                result.append({
                    "id": ach.id, "name": ach.name, "description": ach.description,
                    "icon": ach.icon, "category": ach.category, "earned_at": time.time()
                })
        return result

    def get_user_titles(self, guild_id: int, user_id: int) -> List[dict]:
        user_titles = dm.get_guild_data(guild_id, f"titles_{user_id}", [])
        result = []
        for t in user_titles:
            title = self._titles.get(t["title_id"])
            if title:
                result.append({
                    "id": title["id"], "name": title["name"], "icon": title["icon"],
                    "unlocked_at": t["unlocked_at"], "active": t.get("active", False)
                })
        return result

    def get_active_title(self, guild_id: int, user_id: int) -> Optional[dict]:
        user_titles = dm.get_guild_data(guild_id, f"titles_{user_id}", [])
        for t in user_titles:
            if t.get("active", False):
                title = self._titles.get(t["title_id"])
                if title: return {"name": title["name"], "icon": title["icon"]}
        return None

    def get_leaderboard(self, guild_id: int) -> List[dict]:
        all_users = {}
        data_dir = "data"
        if os.path.exists(data_dir):
            for filename in os.listdir(data_dir):
                if filename.startswith("guild_") and filename.endswith(".json"):
                    try:
                        guild_data = dm.load_json(filename[:-5])
                        if guild_data.get("guild_id") != guild_id: continue
                        for key, value in guild_data.items():
                            if key.startswith("achievements_"):
                                user_id = int(key[13:])
                                all_users[user_id] = len(value)
                    except: pass
        
        sorted_users = sorted(all_users.items(), key=lambda x: x[1], reverse=True)[:10]
        return [{"rank": i+1, "user_id": uid, "achievements": count} for i, (uid, count) in enumerate(sorted_users)]

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        
        # Setup channel
        channel = discord.utils.get(guild.text_channels, name="achievements")
        if not channel:
            channel = await guild.create_text_channel("achievements")
        settings["notify_channel"] = channel.id
        dm.update_guild_data(guild.id, "achievement_settings", settings)
        
        # Register prefix commands
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        custom_cmds["achievements"] = json.dumps({"command_type": "list_achievements"})
        custom_cmds["titles"] = json.dumps({"command_type": "list_titles"})
        custom_cmds["settitle"] = json.dumps({"command_type": "set_title"})
        custom_cmds["badges"] = json.dumps({"command_type": "list_achievements"})
        custom_cmds["progress"] = json.dumps({"command_type": "list_achievements"})
        custom_cmds["achievementsleaderboard"] = json.dumps({"command_type": "achievements_leaderboard"})
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        doc_embed = discord.Embed(title="🏆 Achievement System Guide", color=discord.Color.gold())
        doc_embed.add_field(name="Commands", value="`!achievements` - View your earned badges\n`!titles` - View unlocked titles\n`!settitle <name>` - Set your active title\n`!achievementsleaderboard` - See top earners", inline=False)
        await channel.send(embed=doc_embed)

        await interaction.followup.send(f"Achievement system set up in {channel.mention}!", ephemeral=True)
        return True
