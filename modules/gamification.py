import discord
from discord.ext import commands
import asyncio
import json
import time
import random
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from data_manager import dm
from logger import logger


class QuestType(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    PERSONAL = "personal"
    SOCIAL = "social"
    CHALLENGE = "challenge"


class QuestStatus(Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CLAIMED = "claimed"


class BadgeCategory(Enum):
    COMMUNITY = "community"
    ACTIVITY = "activity"
    SKILL = "skill"
    EVENT = "event"
    SPECIAL = "special"
    SEASONAL = "seasonal"
    CHALLENGE = "challenge"


@dataclass
class Quest:
    id: str
    guild_id: int
    user_id: int
    quest_type: QuestType
    title: str
    description: str
    requirements: dict
    rewards: dict
    expires_at: float
    status: QuestStatus
    progress: int
    created_at: float


@dataclass
class Badge:
    id: str
    name: str
    description: str
    category: BadgeCategory
    icon: str
    requirements: dict
    rarity: float
    evolves_from: Optional[str]


@dataclass
class UserBadge:
    badge_id: str
    earned_at: float
    evolved_level: int


@dataclass
class Skill:
    name: str
    level: int
    xp: int
    xp_to_next: int


class AdaptiveGamification:
    def __init__(self, bot):
        self.bot = bot
        self._active_quests: Dict[str, Quest] = {}
        self._badge_definitions: Dict[str, Badge] = {}
        self._user_skills: Dict[int, Dict[int, Dict[str, Skill]]] = {}
        self._seasonal_events: Dict[int, dict] = {}
        self._load_data()
        self._init_default_badges()

    def _load_data(self):
        quests_data = dm.load_json("active_quests", default={})
        
        for quest_id, data in quests_data.items():
            try:
                quest = Quest(
                    id=quest_id,
                    guild_id=data["guild_id"],
                    user_id=data["user_id"],
                    quest_type=QuestType(data["quest_type"]),
                    title=data["title"],
                    description=data["description"],
                    requirements=data["requirements"],
                    rewards=data["rewards"],
                    expires_at=data["expires_at"],
                    status=QuestStatus(data["status"]),
                    progress=data["progress"],
                    created_at=data["created_at"]
                )
                
                if quest.status == QuestStatus.ACTIVE and quest.expires_at > time.time():
                    self._active_quests[quest_id] = quest
            except Exception as e:
                logger.error(f"Failed to load quest {quest_id}: {e}")
        
        seasonal = dm.load_json("seasonal_events", default={})
        self._seasonal_events = {int(k): v for k, v in seasonal.items()}

    def _save_quests(self):
        quests_data = {}
        
        for quest_id, quest in self._active_quests.items():
            quests_data[quest_id] = {
                "guild_id": quest.guild_id,
                "user_id": quest.user_id,
                "quest_type": quest.quest_type.value,
                "title": quest.title,
                "description": quest.description,
                "requirements": quest.requirements,
                "rewards": quest.rewards,
                "expires_at": quest.expires_at,
                "status": quest.status.value,
                "progress": quest.progress,
                "created_at": quest.created_at
            }
        
        dm.save_json("active_quests", quests_data)

    def _init_default_badges(self):
        default_badges = [
            Badge("newcomer", "Newcomer", "Joined the server", BadgeCategory.COMMUNITY, "👋", {"days_member": 1}, 0.9, None),
            Badge("regular", "Regular", "Active for 7 days", BadgeCategory.ACTIVITY, "📅", {"days_active": 7}, 0.7, "newcomer"),
            Badge("veteran", "Veteran", "Active for 30 days", BadgeCategory.ACTIVITY, "⭐", {"days_active": 30}, 0.5, "regular"),
            Badge("chatterbox", "Chatterbox", "Sent 100 messages", BadgeCategory.ACTIVITY, "💬", {"messages": 100}, 0.6, None),
            Badge("socialite", "Socialite", "Sent 500 messages", BadgeCategory.ACTIVITY, "🗣️", {"messages": 500}, 0.4, "chatterbox"),
            Badge("helper", "Helper", "Used 50 commands", BadgeCategory.SKILL, "🛠️", {"commands": 50}, 0.5, None),
            Badge("event_participant", "Event Participant", "Joined an event", BadgeCategory.EVENT, "🎮", {"events_joined": 1}, 0.8, None),
            Badge("event_winner", "Event Winner", "Won an event", BadgeCategory.EVENT, "🏆", {"events_won": 1}, 0.3, "event_participant"),
            Badge("first_quest", "Adventurer", "Completed first quest", BadgeCategory.CHALLENGE, "🗺️", {"quests_completed": 1}, 0.8, None),
            Badge("quest_master", "Quest Master", "Completed 25 quests", BadgeCategory.CHALLENGE, "🎖️", {"quests_completed": 25}, 0.3, "first_quest"),
        ]
        
        for badge in default_badges:
            self._badge_definitions[badge.id] = badge

    def start_quest_refresh(self):
        asyncio.create_task(self._quest_refresh_loop())

    async def _quest_refresh_loop(self):
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            try:
                await self._refresh_daily_quests()
                await self._check_quest_progress()
                await self._check_badge_awards()
            except Exception as e:
                logger.error(f"Quest refresh error: {e}")
            
            await asyncio.sleep(60)

    async def _refresh_daily_quests(self):
        for guild in self.bot.guilds:
            for member in guild.members:
                if member.bot:
                    continue
                
                await self._generate_daily_quest(guild.id, member.id)

    async def _generate_daily_quest(self, guild_id: int, user_id: int):
        existing_quest_count = sum(
            1 for q in self._active_quests.values()
            if q.guild_id == guild_id and q.user_id == user_id and q.quest_type == QuestType.DAILY and q.status == QuestStatus.ACTIVE
        )
        
        if existing_quest_count >= 3:
            return
        
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        interests = user_data.get("interests", ["general"])
        
        prompt = f"""Generate a daily quest for a Discord user.

User interests: {', '.join(interests)}

Respond with JSON only:
{{
    "title": "Quest title",
    "description": "What the player needs to do",
    "type": "daily",
    "requirements": {{"type": "messages", "count": 10}},
    "rewards": {{"coins": 50, "xp": 25}},
    "duration_hours": 24
}}

Make it fun and varied. Consider message sending, reactions, voice chat, command usage, etc."""

        try:
            result = await self.bot.ai.chat(
                guild_id=guild_id,
                user_id=user_id,
                user_input=prompt,
                system_prompt="You create fun daily quests for Discord users. Keep them achievable (5-20 actions)."
            )
            
            if not result or "error" in result:
                logger.warning(f"AI failed to provide quest data: {result.get('error', 'Unknown error')}")
                return
            
            quest_id = f"quest_{guild_id}_{user_id}_{int(time.time())}"
            
            quest = Quest(
                id=quest_id,
                guild_id=guild_id,
                user_id=user_id,
                quest_type=QuestType.DAILY,
                title=result.get("title", "Daily Quest"),
                description=result.get("description", "Complete this quest!"),
                requirements=result.get("requirements", {"type": "messages", "count": 10}),
                rewards=result.get("rewards", {"coins": 50, "xp": 25}),
                expires_at=time.time() + (result.get("duration_hours", 24) * 3600),
                status=QuestStatus.ACTIVE,
                progress=0,
                created_at=time.time()
            )
            
            self._active_quests[quest_id] = quest
            self._save_quests()
            
        except Exception as e:
            # If AI chat fails, log the specific reason surfaced by AIClient
            logger.error(f"Failed to generate daily quest for user {user_id} in guild {guild_id}. Error: {e}")
            # Ensure we don't try again immediately in the same loop
            return

    async def _check_quest_progress(self):
        current_time = time.time()
        
        for quest_id, quest in list(self._active_quests.items()):
            if quest.status != QuestStatus.ACTIVE:
                continue
            
            if quest.expires_at < current_time:
                quest.status = QuestStatus.EXPIRED
                self._save_quests()
                continue
            
            if quest.quest_type == QuestType.DAILY:
                user_data = dm.get_guild_data(quest.guild_id, f"user_{quest.user_id}", {})
                
                req_type = quest.requirements.get("type")
                req_count = quest.requirements.get("count", 10)
                
                if req_type == "messages":
                    current = user_data.get("messages_sent_today", 0)
                    quest.progress = min(current, req_count)
                elif req_type == "commands":
                    current = user_data.get("commands_used_today", 0)
                    quest.progress = min(current, req_count)
                elif req_type == "voice":
                    current = user_data.get("voice_minutes_today", 0)
                    quest.progress = min(current, req_count)
                
                if quest.progress >= req_count:
                    quest.status = QuestStatus.COMPLETED
                    await self._notify_quest_complete(quest)
                
                self._save_quests()

    async def _check_badge_awards(self):
        for guild in self.bot.guilds:
            for member in guild.members:
                if member.bot:
                    continue
                
                await self._check_and_award_badges(guild.id, member.id)

    async def _check_and_award_badges(self, guild_id: int, user_id: int):
        user_badges = dm.get_guild_data(guild_id, f"badges_{user_id}", [])
        earned_ids = {b["badge_id"] for b in user_badges}
        
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        
        for badge_id, badge_def in self._badge_definitions.items():
            if badge_id in earned_ids:
                continue
            
            req = badge_def.requirements
            
            earned = False
            
            if "days_member" in req:
                member = self.bot.get_guild(guild_id).get_member(user_id)
                if member and member.joined_at:
                    days = (discord.utils.utcnow() - member.joined_at).days
                    earned = days >= req["days_member"]
            
            if "days_active" in req and not earned:
                days_active = user_data.get("days_active", 0)
                earned = days_active >= req["days_active"]
            
            if "messages" in req and not earned:
                messages = user_data.get("total_messages", 0)
                earned = messages >= req["messages"]
            
            if "commands" in req and not earned:
                commands = user_data.get("total_commands", 0)
                earned = commands >= req["commands"]
            
            if "events_joined" in req and not earned:
                events = user_data.get("events_joined", 0)
                earned = events >= req["events_joined"]
            
            if "events_won" in req and not earned:
                events_won = user_data.get("events_won", 0)
                earned = events_won >= req["events_won"]
            
            if "quests_completed" in req and not earned:
                quests = user_data.get("quests_completed", 0)
                earned = quests >= req["quests_completed"]
            
            if earned:
                await self._award_badge(guild_id, user_id, badge_id)

    async def _award_badge(self, guild_id: int, user_id: int, badge_id: str):
        user_badges = dm.get_guild_data(guild_id, f"badges_{user_id}", [])
        
        user_badges.append({
            "badge_id": badge_id,
            "earned_at": time.time(),
            "evolved_level": 1
        })
        
        dm.update_guild_data(guild_id, f"badges_{user_id}", user_badges)
        
        badge = self._badge_definitions.get(badge_id)
        if badge:
            member = self.bot.get_guild(guild_id).get_member(user_id)
            if member:
                embed = discord.Embed(
                    title=f"🎖️ Badge Earned!",
                    description=f"{member.mention} earned the **{badge.name}** badge!",
                    color=discord.Color.gold()
                )
                embed.add_field(name="Description", value=badge.description, inline=False)
                embed.add_field(name="Category", value=badge.category.value.title(), inline=True)
                
                channel = self.bot.get_guild(guild_id).system_channel
                if channel:
                    await channel.send(embed=embed)

    async def _notify_quest_complete(self, quest: Quest):
        member = self.bot.get_guild(quest.guild_id).get_member(quest.user_id)
        if not member:
            return
        
        embed = discord.Embed(
            title="✅ Quest Completed!",
            description=f"**{quest.title}** - {quest.description}",
            color=discord.Color.green()
        )
        embed.add_field(
            name="Rewards",
            value=f"💰 {quest.rewards.get('coins', 0)} coins, XP {quest.rewards.get('xp', 0)}",
            inline=False
        )
        
        try:
            await member.send(embed=embed)
        except:
            pass

    async def claim_quest_reward(self, guild_id: int, user_id: int, quest_id: str) -> bool:
        if quest_id not in self._active_quests:
            return False
        
        quest = self._active_quests[quest_id]
        
        if quest.user_id != user_id or quest.guild_id != guild_id:
            return False
        
        if quest.status != QuestStatus.COMPLETED:
            return False
        
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        user_data["coins"] = user_data.get("coins", 0) + quest.rewards.get("coins", 0)
        user_data["xp"] = user_data.get("xp", 0) + quest.rewards.get("xp", 0)
        user_data["quests_completed"] = user_data.get("quests_completed", 0) + 1
        
        dm.update_guild_data(guild_id, f"user_{user_id}", user_data)
        
        quest.status = QuestStatus.CLAIMED
        self._save_quests()
        
        return True

    def get_user_quests(self, guild_id: int, user_id: int) -> List[dict]:
        user_quests = []
        
        for quest in self._active_quests.values():
            if quest.guild_id == guild_id and quest.user_id == user_id and quest.status in [QuestStatus.ACTIVE, QuestStatus.COMPLETED]:
                user_quests.append({
                    "id": quest.id,
                    "title": quest.title,
                    "description": quest.description,
                    "type": quest.quest_type.value,
                    "progress": quest.progress,
                    "requirements": quest.requirements,
                    "rewards": quest.rewards,
                    "status": quest.status.value,
                    "expires_at": quest.expires_at
                })
        
        return user_quests

    def get_user_badges(self, guild_id: int, user_id: int) -> List[dict]:
        user_badges = dm.get_guild_data(guild_id, f"badges_{user_id}", [])
        
        result = []
        for ub in user_badges:
            badge_def = self._badge_definitions.get(ub["badge_id"])
            if badge_def:
                result.append({
                    "id": ub["badge_id"],
                    "name": badge_def.name,
                    "description": badge_def.description,
                    "icon": badge_def.icon,
                    "category": badge_def.category.value,
                    "earned_at": ub["earned_at"],
                    "evolved_level": ub.get("evolved_level", 1)
                })
        
        return result

    def get_user_skills(self, guild_id: int, user_id: int) -> Dict[str, dict]:
        skills = {}
        
        skill_data = dm.get_guild_data(guild_id, f"skills_{user_id}", {})
        
        for skill_name, data in skill_data.items():
            skills[skill_name] = {
                "name": skill_name,
                "level": data.get("level", 1),
                "xp": data.get("xp", 0),
                "xp_to_next": data.get("xp_to_next", 100)
            }
        
        return skills

    async def generate_ai_quest(self, guild_id: int, user_id: int, request: str) -> Optional[Quest]:
        prompt = f"""Create a personalized quest based on this request: "{request}"

Respond with JSON only:
{{
    "title": "Quest name",
    "description": "What to do",
    "type": "personal",
    "requirements": {{"type": "specific_action", "detail": "..."}},
    "rewards": {{"coins": 100, "xp": 50}},
    "duration_hours": 48
}}"""

        try:
            result = await self.bot.ai.chat(
                guild_id=guild_id,
                user_id=user_id,
                user_input=prompt,
                system_prompt="You create personalized quests for Discord users. Make them fun and engaging."
            )
            
            quest_id = f"quest_{guild_id}_{user_id}_{int(time.time())}"
            
            quest = Quest(
                id=quest_id,
                guild_id=guild_id,
                user_id=user_id,
                quest_type=QuestType.PERSONAL,
                title=result.get("title", "Custom Quest"),
                description=result.get("description", "Complete this challenge!"),
                requirements=result.get("requirements", {}),
                rewards=result.get("rewards", {"coins": 100, "xp": 50}),
                expires_at=time.time() + (result.get("duration_hours", 48) * 3600),
                status=QuestStatus.ACTIVE,
                progress=0,
                created_at=time.time()
            )
            
            self._active_quests[quest_id] = quest
            self._save_quests()
            
            return quest
            
        except Exception as e:
            logger.error(f"Failed to generate AI quest: {e}")
            return None

    async def create_collaborative_challenge(self, guild_id: int, channel_id: int, created_by: int, params: dict) -> dict:
        challenge_id = f"challenge_{guild_id}_{int(time.time())}"
        
        challenge = {
            "id": challenge_id,
            "guild_id": guild_id,
            "channel_id": channel_id,
            "created_by": created_by,
            "title": params.get("title", "Group Challenge"),
            "description": params.get("description", "Work together!"),
            "goal": params.get("goal", 100),
            "participants": [],
            "current_progress": 0,
            "rewards": params.get("rewards", {"coins": 500, "xp": 250}),
            "created_at": time.time(),
            "expires_at": time.time() + params.get("duration_hours", 24) * 3600,
            "status": "active"
        }
        
        challenges = dm.get_guild_data(guild_id, "collaborative_challenges", {})
        challenges[challenge_id] = challenge
        dm.update_guild_data(guild_id, "collaborative_challenges", challenges)
        
        return challenge

    async def setup(self, interaction: discord.Interaction, params: Dict = None):
        guild = interaction.guild
        
        help_embed = discord.Embed(
            title="🎮 Adaptive Gamification System",
            description="Personalized quests, dynamic badges, and skill progression.",
            color=discord.Color.green()
        )
        help_embed.add_field(
            name="How it works",
            value="AI generates daily quests based on your interests. Earn badges, track skills, and complete challenges for rewards.",
            inline=False
        )
        help_embed.add_field(
            name="!quests",
            value="View your active and completed quests.",
            inline=False
        )
        help_embed.add_field(
            name="!badges",
            value="View your earned badges.",
            inline=False
        )
        help_embed.add_field(
            name="!skills",
            value="View your skill progression.",
            inline=False
        )
        help_embed.add_field(
            name="!claim <quest_id>",
            value="Claim completed quest rewards.",
            inline=False
        )
        
        await interaction.followup.send(embed=help_embed, ephemeral=True)
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["quests"] = json.dumps({
            "command_type": "list_quests"
        })
        custom_cmds["badges"] = json.dumps({
            "command_type": "list_badges"
        })
        custom_cmds["skills"] = json.dumps({
            "command_type": "list_skills"
        })
        custom_cmds["claim"] = json.dumps({
            "command_type": "claim_quest"
        })
        custom_cmds["help gamification"] = json.dumps({
            "command_type": "help_embed",
            "title": "🎮 Adaptive Gamification System",
            "description": "Personalized quests, dynamic badges, and skill progression.",
            "fields": [
                {"name": "!quests", "value": "View your active and completed quests.", "inline": False},
                {"name": "!badges", "value": "View your earned badges.", "inline": False},
                {"name": "!skills", "value": "View your skill progression.", "inline": False},
                {"name": "!claim <quest_id>", "value": "Claim completed quest rewards.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        
        return True


from discord import app_commands
