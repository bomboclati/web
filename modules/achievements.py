import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional
from dataclasses import dataclass

from data_manager import dm
from logger import logger


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
            Achievement("first_join", "First Steps", "Joined the server", "community", "👋", 
                        {"type": "join"}, {"xp": 10}, 0.95, False),
            Achievement("week_member", "Weekly Member", "Been a member for 7 days", "community", "📅",
                        {"type": "days_member", "count": 7}, {"coins": 50}, 0.7, False),
            Achievement("month_member", "Dedicated Member", "Been a member for 30 days", "community", "⭐",
                        {"type": "days_member", "count": 30}, {"coins": 200}, 0.4, False),
            Achievement("first_message", "Hello World", "Sent your first message", "activity", "💬",
                        {"type": "messages", "count": 1}, {"xp": 5}, 0.9, False),
            Achievement("chatty", "Chatty", "Sent 100 messages", "activity", "🗣️",
                        {"type": "messages", "count": 100}, {"coins": 100, "xp": 50}, 0.6, False),
            Achievement("prolific", "Prolific", "Sent 500 messages", "activity", "📣",
                        {"type": "messages", "count": 500}, {"coins": 300, "xp": 150}, 0.3, False),
            Achievement("first_command", "Explorer", "Used your first command", "activity", "🔍",
                        {"type": "commands", "count": 1}, {"xp": 5}, 0.9, False),
            Achievement("command_user", "Regular User", "Used 50 commands", "activity", "⌨️",
                        {"type": "commands", "count": 50}, {"coins": 100}, 0.6, False),
            Achievement("first_quest", "Adventurer", "Completed your first quest", "gamification", "🗺️",
                        {"type": "quests_completed", "count": 1}, {"xp": 25}, 0.8, False),
            Achievement("quest_master", "Quest Master", "Completed 25 quests", "gamification", "🎖️",
                        {"type": "quests_completed", "count": 25}, {"coins": 500, "xp": 250}, 0.3, False),
            Achievement("first_giveaway", "Lucky", "Won a giveaway", "events", "🎁",
                        {"type": "giveaways_won", "count": 1}, {"coins": 100}, 0.7, False),
            Achievement("event_winner", "Champion", "Won 5 events", "events", "🏆",
                        {"type": "events_won", "count": 5}, {"coins": 500}, 0.3, False),
            Achievement("first_ticket", "Reporter", "Created your first ticket", "support", "🎫",
                        {"type": "tickets_created", "count": 1}, {"xp": 10}, 0.8, False),
            Achievement("helper", "Helper", "Had 10 tickets resolved", "support", "🛠️",
                        {"type": "tickets_resolved", "count": 10}, {"coins": 200}, 0.5, False),
            Achievement("first_rep", "Kind", "Received first reputation", "social", "💖",
                        {"type": "rep_received", "count": 1}, {"xp": 10}, 0.8, False),
            Achievement("popular", "Popular", "Received 50 reputation", "social", "❤️",
                        {"type": "rep_received", "count": 50}, {"coins": 300}, 0.4, False),
            Achievement("generous", "Generous", "Gave 25 reputation", "social", "🎁",
                        {"type": "rep_given", "count": 25}, {"coins": 150}, 0.5, False),
            Achievement("first_event", "Party Goer", "Joined your first event", "events", "🎉",
                        {"type": "events_joined", "count": 1}, {"xp": 25}, 0.7, False),
            Achievement("tournament_participant", "Competitor", "Joined a tournament", "events", "⚔️",
                        {"type": "tournaments_joined", "count": 1}, {"xp": 50}, 0.6, False),
            Achievement("tournament_winner", "Ultimate Champion", "Won a tournament", "events", "👑",
                        {"type": "tournaments_won", "count": 1}, {"coins": 1000, "xp": 500}, 0.2, True),
            Achievement("first_star", "Starred", "Got your first star", "content", "⭐",
                        {"type": "messages_starred", "count": 1}, {"coins": 25}, 0.7, False),
            Achievement("voice_time", "Voice Active", "Spent 1 hour in voice", "activity", "🎤",
                        {"type": "voice_minutes", "count": 60}, {"xp": 50}, 0.6, False),
            Achievement("voice_regular", "Voice Regular", "Spent 10 hours in voice", "activity", "🎧",
                        {"type": "voice_minutes", "count": 600}, {"coins": 500, "xp": 250}, 0.3, False),
            Achievement("level_5", "Rising Star", "Reached level 5", "progression", "🌟",
                        {"type": "level", "count": 5}, {"coins": 100}, 0.7, False),
            Achievement("level_10", "Established", "Reached level 10", "progression", "💫",
                        {"type": "level", "count": 10}, {"coins": 250, "xp": 100}, 0.5, False),
            Achievement("level_25", "Veteran", "Reached level 25", "progression", "🔥",
                        {"type": "level", "count": 25}, {"coins": 500, "xp": 250}, 0.3, False),
            Achievement("level_50", "Elite", "Reached level 50", "progression", "👑",
                        {"type": "level", "count": 50}, {"coins": 1000, "xp": 500}, 0.15, False),
            Achievement("rich", "Wealthy", "Earned 1000 coins total", "economy", "💰",
                        {"type": "total_coins", "count": 1000}, {"xp": 100}, 0.6, False),
            Achievement("millionaire", "Millionaire", "Earned 10000 coins total", "economy", "🤑",
                        {"type": "total_coins", "count": 10000}, {"coins": 500}, 0.25, True),
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
            "show_progress": True
        })

    async def check_achievements(self, guild_id: int, user_id: int):
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        earned = dm.get_guild_data(guild_id, f"achievements_{user_id}", [])
        earned_ids = [a["achievement_id"] for a in earned]
        
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
            
            if earned_now:
                await self._award_achievement(guild_id, user_id, ach, new_earned)
        
        return new_earned

    async def _award_achievement(self, guild_id: int, user_id: int, achievement: Achievement, new_earned: list):
        earned = dm.get_guild_data(guild_id, f"achievements_{user_id}", [])
        
        earned.append({
            "achievement_id": achievement.id,
            "earned_at": time.time()
        })
        
        dm.update_guild_data(guild_id, f"achievements_{user_id}", earned)
        
        reward = achievement.reward
        if reward:
            user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
            user_data["coins"] = user_data.get("coins", 0) + reward.get("coins", 0)
            user_data["xp"] = user_data.get("xp", 0) + reward.get("xp", 0)
            dm.update_guild_data(guild_id, f"user_{user_id}", user_data)
        
        new_earned.append(achievement)
        
        await self._notify_achievement(guild_id, user_id, achievement)
        
        await self._check_title_unlock(guild_id, user_id)

    async def _notify_achievement(self, guild_id: int, user_id: int, achievement: Achievement):
        settings = self.get_guild_settings(guild_id)
        
        member = self.bot.get_guild(guild_id).get_member(user_id)
        if not member:
            return
        
        embed = discord.Embed(
            title=f"🏆 Achievement Unlocked!",
            description=f"**{achievement.icon} {achievement.name}**",
            color=discord.Color.gold()
        )
        embed.add_field(name="Description", value=achievement.description, inline=False)
        
        if achievement.reward:
            reward_text = []
            if achievement.reward.get("coins"):
                reward_text.append(f"{achievement.reward['coins']} coins")
            if achievement.reward.get("xp"):
                reward_text.append(f"{achievement.reward['xp']} XP")
            if reward_text:
                embed.add_field(name="Reward", value=", ".join(reward_text), inline=True)
        
        if settings.get("notify_channel"):
            channel = self.bot.get_guild(guild_id).get_channel(int(settings["notify_channel"]))
            if channel:
                await channel.send(embed=embed)
        
        try:
            await member.send(embed=embed)
        except:
            pass

    async def _check_title_unlock(self, guild_id: int, user_id: int):
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        
        for title_id, title in self._titles.items():
            if title_id == "newcomer":
                continue
            
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
            
            title = self._titles.get(title_id)
            if title:
                member = self.bot.get_guild(guild_id).get_member(user_id)
                if member:
                    embed = discord.Embed(
                        title=f"🎖️ Title Unlocked!",
                        description=f"You unlocked: **{title['icon']} {title['name']}**",
                        color=discord.Color.gold()
                    )
                    try:
                        await member.send(embed=embed)
                    except:
                        pass

    def set_active_title(self, guild_id: int, user_id: int, title_id: str) -> bool:
        user_titles = dm.get_guild_data(guild_id, f"titles_{user_id}", [])
        
        for title in user_titles:
            title["active"] = (title["title_id"] == title_id)
        
        dm.update_guild_data(guild_id, f"titles_{user_id}", user_titles)
        
        return True

    def get_user_achievements(self, guild_id: int, user_id: int) -> List[dict]:
        earned = dm.get_guild_data(guild_id, f"achievements_{user_id}", [])
        
        result = []
        for e in earned:
            ach = self._achievements.get(e["achievement_id"])
            if ach:
                result.append({
                    "id": ach.id,
                    "name": ach.name,
                    "description": ach.description,
                    "icon": ach.icon,
                    "category": ach.category,
                    "earned_at": e["earned_at"]
                })
        
        return result

    def get_user_titles(self, guild_id: int, user_id: int) -> List[dict]:
        user_titles = dm.get_guild_data(guild_id, f"titles_{user_id}", [])
        
        result = []
        for t in user_titles:
            title = self._titles.get(t["title_id"])
            if title:
                result.append({
                    "id": title["id"],
                    "name": title["name"],
                    "icon": title["icon"],
                    "unlocked_at": t["unlocked_at"],
                    "active": t.get("active", False)
                })
        
        return result

    def get_active_title(self, guild_id: int, user_id: int) -> Optional[dict]:
        user_titles = dm.get_guild_data(guild_id, f"titles_{user_id}", [])
        
        for t in user_titles:
            if t.get("active", False):
                title = self._titles.get(t["title_id"])
                if title:
                    return {"name": title["name"], "icon": title["icon"]}
        
        return None

    def get_leaderboard(self, guild_id: int, category: str = None) -> List[dict]:
        all_users = {}
        
        data_dir = "data"
        import os
        if os.path.exists(data_dir):
            for filename in os.listdir(data_dir):
                if filename.startswith("guild_") and filename.endswith(".json"):
                    try:
                        guild_data = dm.load_json(filename[:-5])
                        if guild_data.get("guild_id") != guild_id:
                            continue
                        
                        for key, value in guild_data.items():
                            if key.startswith("achievements_"):
                                user_id = int(key[12:])
                                if user_id not in all_users:
                                    all_users[user_id] = 0
                                all_users[user_id] += len(value)
                    except:
                        pass
        
        sorted_users = sorted(all_users.items(), key=lambda x: x[1], reverse=True)[:10]
        
        leaderboard = []
        for i, (user_id, count) in enumerate(sorted_users):
            leaderboard.append({
                "rank": i + 1,
                "user_id": user_id,
                "achievements": count
            })
        
        return leaderboard

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        settings = self.get_guild_settings(guild.id)
        settings["enabled"] = True
        dm.update_guild_data(guild.id, "achievement_settings", settings)
        
        # Create a documentation channel
        try:
            doc_channel = await guild.create_text_channel("achievements-guide", category=None)
        except:
            doc_channel = interaction.channel
        
        # Post comprehensive documentation
        doc_embed = discord.Embed(
            title="🎯 Achievement System Guide",
            description="Complete guide to earning achievements, unlocking titles, and getting rewards!",
            color=discord.Color.gold()
        )
        doc_embed.add_field(
            name="📖 How It Works",
            value="Complete different activities to earn achievements. Each achievement grants coins/XP rewards. Unlock titles based on milestones.",
            inline=False
        )
        doc_embed.add_field(
            name="🎮 Available Commands",
            value="**!achievements** - View all your earned achievements\n" +
                  "**!titles** - View your unlocked titles\n" +
                  "**!settitle <title>** - Set your active title (use title name or ID)\n" +
                  "**!achievementsleaderboard** - See top achievement collectors\n" +
                  "**!help achievements** - Show this guide",
            inline=False
        )
        doc_embed.add_field(
            name="🏆 Achievement Categories",
            value="• **Community**: Join milestones (7d, 30d, 100d)\n" +
                  "• **Activity**: Messages, commands, voice time\n" +
                  "• **Gamification**: Quests completed\n" +
                  "• **Events**: Giveaways won, events joined\n" +
                  "• **Support**: Tickets created/resolved\n" +
                  "• **Social**: Reputation given/received\n" +
                  "• **Progression**: Level milestones\n" +
                  "• **Economy**: Coins earned",
            inline=False
        )
        doc_embed.add_field(
            name="🎖️ Available Titles",
            value="• 🌱 Newcomer (1+ day)\n" +
                  "• ⭐ Regular (7+ days)\n" +
                  "• 🏅 Veteran (30+ days)\n" +
                  "• 👑 Legend (100+ days)\n" +
                  "• 🛠️ Helper (10+ rep received)\n" +
                  "• 🏆 Champion (1+ event won)\n" +
                  "• 💎 Elite (Level 25+)",
            inline=False
        )
        doc_embed.add_field(
            name="💡 Tips",
            value="• Check your progress with !achievements\n" +
                  "• Active titles show next to your name\n" +
                  "• Some achievements are hidden!\n" +
                  "• Get notified when you unlock achievements",
            inline=False
        )
        doc_embed.set_footer(text="Created by Immortal AI • Use !help achievements for more info")
        
        await doc_channel.send(embed=doc_embed)
        await doc_channel.send("💡 **Quick Start:** Try these commands:\n" +
                              "• `!achievements` - See your achievements\n" +
                              "• `!titles` - See your titles\n" +
                              "• `!achievementsleaderboard` - Top collectors")
        
        help_embed = discord.Embed(
            title="🎯 Achievement System",
            description="Earn achievements for activities, unlock titles, and get rewards.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="Complete activities to earn achievements. Each achievement gives coins/XP rewards. Unlock titles based on milestones.",
            inline=False
        )
        help_embed.add_field(
            name="!achievements",
            value="View your achievements.",
            inline=False
        )
        help_embed.add_field(
            name="!titles",
            value="View your unlocked titles.",
            inline=False
        )
        help_embed.add_field(
            name="!settitle <title>",
            value="Set your active title.",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["achievements"] = json.dumps({
            "command_type": "list_achievements"
        })
        custom_cmds["titles"] = json.dumps({
            "command_type": "list_titles"
        })
        custom_cmds["settitle"] = json.dumps({
            "command_type": "set_title"
        })
        custom_cmds["achievementsleaderboard"] = json.dumps({
            "command_type": "achievements_leaderboard"
        })
        custom_cmds["help achievements"] = json.dumps({
            "command_type": "help_embed",
            "title": "🎯 Achievement System",
            "description": "Earn achievements and unlock titles.",
            "fields": [
                {"name": "!achievements", "value": "View your achievements.", "inline": False},
                {"name": "!titles", "value": "View your titles.", "inline": False},
                {"name": "!settitle <name>", "value": "Set your active title.", "inline": False},
                {"name": "!achievementsleaderboard", "value": "Top collectors.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands
