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
import os

class QuestType(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CHALLENGE = "challenge"

class QuestStatus(Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CLAIMED = "claimed"

@dataclass
class Quest:
    id: str
    guild_id: int
    user_id: int # If 0, it's a global challenge
    quest_type: QuestType
    title: str
    description: str
    requirements: dict
    rewards: dict
    expires_at: float
    status: QuestStatus
    progress: int
    created_at: float

class AdaptiveGamification:
    def __init__(self, bot):
        self.bot = bot
        self._active_quests: Dict[str, Quest] = {}
        self._seasonal_events: Dict[int, dict] = {}
        self._load_data()

    def _load_data(self):
        data_dir = "data"
        if os.path.exists(data_dir):
            for filename in os.listdir(data_dir):
                if filename.startswith("guild_") and filename.endswith(".json"):
                    try:
                        guild_id_str = filename[6:-5]
                        if not guild_id_str.isdigit(): continue
                        guild_id = int(guild_id_str)
                        guild_data = dm.load_json(filename[:-5], default={})

                        # Load quests
                        quests_data = guild_data.get("quests", {})
                        for quest_id, data in quests_data.items():
                            quest = Quest(
                                id=quest_id,
                                guild_id=guild_id,
                                user_id=data.get("user_id", 0),
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

                        # Load seasonal events
                        self._seasonal_events[guild_id] = guild_data.get("seasonal_event", {})
                    except Exception as e:
                        logger.error(f"Failed to load gamification data: {e}")

    def _save_quests(self, quest: Quest):
        guild_id = quest.guild_id
        quests = dm.get_guild_data(guild_id, "quests", {})
        quests[quest.id] = {
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
        dm.update_guild_data(guild_id, "quests", quests)

    async def update_streak(self, guild_id: int, user_id: int):
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        last_activity = user_data.get("last_activity_date")
        today = datetime.now().date().isoformat()
        
        if last_activity == today:
            return
            
        yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
        streak = user_data.get("current_streak", 0)
        
        if last_activity == yesterday:
            streak += 1
        else:
            streak = 1
            
        user_data["current_streak"] = streak
        user_data["last_activity_date"] = today

        milestones = {7: 500, 30: 2500, 100: 10000}
        if streak in milestones:
            bonus = milestones[streak]
            user_data["coins"] = user_data.get("coins", 0) + bonus
            
        dm.update_guild_data(guild_id, f"user_{user_id}", user_data)

    async def handle_prestige(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        guild_id = interaction.guild_id
        user_data = dm.get_guild_data(guild_id, f"user_{user_id}", {})
        
        prestige_level_req = dm.get_guild_data(guild_id, "prestige_req", 100)
        current_level = user_data.get("level", 1)
        
        if current_level < prestige_level_req:
            return await interaction.response.send_message(f"❌ You must be at least Level {prestige_level_req} to prestige!", ephemeral=True)

        prestige_count = user_data.get("prestige", 0) + 1
        user_data["prestige"] = prestige_count
        user_data["level"] = 1
        user_data["xp"] = 0
        
        dm.update_guild_data(guild_id, f"user_{user_id}", user_data)
        
        embed = discord.Embed(
            title="👑 PRESTIGE REACHED!",
            description=f"Congratulations {interaction.user.mention}! You have ascended to **Prestige {prestige_count}**.\nYour level has been reset, but you've earned a permanent prestige badge!",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed)

    def get_rank_title(self, level: int) -> str:
        if level >= 100: return "Legend"
        if level >= 50: return "Elite"
        if level >= 25: return "Veteran"
        if level >= 10: return "Regular"
        return "Newcomer"

    async def mini_game_dice(self, interaction: discord.Interaction, bet: int):
        user_data = dm.get_guild_data(interaction.guild.id, f"user_{interaction.user.id}", {})
        if user_data.get("coins", 0) < bet:
            return await interaction.response.send_message("❌ You don't have enough coins!", ephemeral=True)

        user_roll = random.randint(1, 6)
        bot_roll = random.randint(1, 6)
        
        if user_roll > bot_roll:
            user_data["coins"] += bet
            msg = f"🎲 You rolled **{user_roll}**, I rolled **{bot_roll}**. **You win {bet} coins!**"
        elif user_roll < bot_roll:
            user_data["coins"] -= bet
            msg = f"🎲 You rolled **{user_roll}**, I rolled **{bot_roll}**. **You lost {bet} coins.**"
        else:
            msg = f"🎲 We both rolled **{user_roll}**. It's a draw!"
            
        dm.update_guild_data(interaction.guild.id, f"user_{interaction.user.id}", user_data)
        await interaction.response.send_message(msg)

    async def mini_game_flip(self, interaction: discord.Interaction, choice: str, bet: int):
        user_data = dm.get_guild_data(interaction.guild.id, f"user_{interaction.user.id}", {})
        if user_data.get("coins", 0) < bet:
            return await interaction.response.send_message("❌ You don't have enough coins!", ephemeral=True)
            
        result = random.choice(["heads", "tails"])
        if choice.lower() == result:
            user_data["coins"] += bet
            msg = f"🪙 It's **{result.upper()}**! **You win {bet} coins!**"
        else:
            user_data["coins"] -= bet
            msg = f"🪙 It's **{result.upper()}**... **You lost {bet} coins.**"
            
        dm.update_guild_data(interaction.guild.id, f"user_{interaction.user.id}", user_data)
        await interaction.response.send_message(msg)

    async def mini_game_slots(self, interaction: discord.Interaction, bet: int):
        user_data = dm.get_guild_data(interaction.guild.id, f"user_{interaction.user.id}", {})
        if user_data.get("coins", 0) < bet:
            return await interaction.response.send_message("❌ You don't have enough coins!", ephemeral=True)
            
        emojis = ["🍒", "🍋", "🍇", "💎", "⭐"]
        s1, s2, s3 = random.choice(emojis), random.choice(emojis), random.choice(emojis)

        win = 0
        if s1 == s2 == s3:
            win = bet * 10
            msg = f"🎰 [ {s1} | {s2} | {s3} ]\n**JACKPOT! You won {win} coins!**"
        elif s1 == s2 or s2 == s3 or s1 == s3:
            win = bet * 2
            msg = f"🎰 [ {s1} | {s2} | {s3} ]\n**Two of a kind! You won {win} coins!**"
        else:
            msg = f"🎰 [ {s1} | {s2} | {s3} ]\n**Better luck next time.**"
            user_data["coins"] -= bet
            
        if win: user_data["coins"] += win
        dm.update_guild_data(interaction.guild.id, f"user_{interaction.user.id}", user_data)
        await interaction.response.send_message(msg)

    async def mini_game_trivia(self, interaction: discord.Interaction):
        questions = [
            {"q": "What is the capital of France?", "a": "paris"},
            {"q": "Who created Discord?", "a": "jason citron"},
            {"q": "What is 10 + 10?", "a": "20"},
            {"q": "Which planet is known as the Red Planet?", "a": "mars"}
        ]
        q = random.choice(questions)
        await interaction.response.send_message(f"❓ **Trivia:** {q['q']}\n*(Type your answer in the chat)*")
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel

        try:
            msg = await self.bot.wait_for('message', check=check, timeout=15.0)
            if msg.content.lower() == q['a'].lower():
                user_data = dm.get_guild_data(interaction.guild.id, f"user_{interaction.user.id}", {})
                user_data["coins"] = user_data.get("coins", 0) + 50
                dm.update_guild_data(interaction.guild.id, f"user_{interaction.user.id}", user_data)
                await interaction.channel.send(f"✅ Correct {interaction.user.mention}! You earned 50 coins.")
            else:
                await interaction.channel.send(f"❌ Wrong! The answer was **{q['a']}**.")
        except asyncio.TimeoutError:
            await interaction.channel.send(f"⏰ Time's up {interaction.user.mention}!")

    async def setup(self, interaction: discord.Interaction, params: dict = None):
        guild_id = interaction.guild_id
        dm.update_guild_data(guild_id, "gamification_enabled", True)
        dm.update_guild_data(guild_id, "prestige_req", 100)
        
        # Register commands
        custom_cmds = dm.get_guild_data(guild_id, "custom_commands", {})
        cmds = ["trivia", "dice", "flip", "slots", "quests", "prestige", "leaderboard", "titles"]
        for c in cmds:
            custom_cmds[c] = json.dumps({"command_type": f"game_{c}"})
        dm.update_guild_data(guild_id, "custom_commands", custom_cmds)
        
        await interaction.followup.send("🎮 Gamification system (Quests, Streaks, Prestige, Games) setup complete!")
        return True

    def start_quest_refresh(self):
        asyncio.create_task(self._quest_refresh_loop())

    async def _quest_refresh_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            # Hourly check for quest expiry and leaderboard updates
            await asyncio.sleep(3600)
            await self._update_all_leaderboards()

    async def _update_all_leaderboards(self):
        for guild in self.bot.guilds:
            lb_channel_id = dm.get_guild_data(guild.id, "gamification_lb_channel")
            if lb_channel_id:
                channel = guild.get_channel(lb_channel_id)
                if channel:
                    # Update leaderboard logic here
                    pass
