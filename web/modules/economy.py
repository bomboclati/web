import discord
from data_manager import dm
import random
import datetime
import json
import time

class Economy:
    """
    Coins per user. Earn per message, daily, transfers.
    Zero Data Loss with immediate writes.
    Now includes daily challenges and achievements!
    """
    
    """Daily Challenges System"""
    DAILY_CHALLENGES = [
        {"id": "message_50", "name": "Chatty Cat", "desc": "Send 50 messages", "target": 50, "reward": 500},
        {"id": "react_10", "name": "Reactor", "desc": "Add 10 reactions", "target": 10, "reward": 300},
        {"id": "join_voice", "name": "Voice Joiner", "desc": "Join a voice channel", "target": 1, "reward": 200},
        {"id": "invite_1", "name": "Invoker", "desc": "Send 1 invite", "target": 1, "reward": 500},
        {"id": "help_3", "name": "Helper", "desc": "Use 3 help commands", "target": 3, "reward": 250},
    ]
    
    def __init__(self, bot):
        self.bot = bot
    
    def get_coins(self, guild_id: int, user_id: int) -> int:
        balances = dm.get_guild_data(guild_id, "economy_balances", {})
        return balances.get(str(user_id), 0)
    
    def add_coins(self, guild_id: int, user_id: int, amount: int):
        balances = dm.get_guild_data(guild_id, "economy_balances", {})
        current = balances.get(str(user_id), 0)
        balances[str(user_id)] = current + amount
        dm.update_guild_data(guild_id, "economy_balances", balances)
    
    """Daily Challenges"""
    def get_daily_challenge(self, guild_id: int) -> dict:
        """Get today's challenge for guild."""
        challenges = dm.get_guild_data(guild_id, "daily_challenges", {})
        today = datetime.datetime.now().date().isoformat()
        
        if challenges.get("date") != today:
            # Reset with new challenge
            challenge = random.choice(self.DAILY_CHALLENGES)
            challenge["progress"] = {}
            challenge["date"] = today
            challenges = challenge
            dm.update_guild_data(guild_id, "daily_challenges", challenge)
        
        return challenges
    
    def update_challenge_progress(self, guild_id: int, user_id: int, challenge_id: str):
        """Update user's progress on daily challenge."""
        challenge = self.get_daily_challenge(guild_id)
        
        if challenge.get("id") != challenge_id:
            return
        
        progress = challenge.get("progress", {})
        current = progress.get(str(user_id), 0)
        progress[str(user_id)] = current + 1
        
        challenge["progress"] = progress
        dm.update_guild_data(guild_id, "daily_challenges", challenge)
        
        # Check if completed
        if current + 1 >= challenge.get("target", 1):
            if not self._user_completed_challenge(guild_id, user_id):
                self._mark_challenge_complete(guild_id, user_id, challenge)
    
    def _user_completed_challenge(self, guild_id: int, user_id: int) -> bool:
        """Check if user already completed today's challenge."""
        completed = dm.get_guild_data(guild_id, "challenge_completed", {})
        today = datetime.datetime.now().date().isoformat()
        
        user_completed = completed.get(str(user_id), {})
        return user_completed.get("date") == today
    
    def _mark_challenge_complete(self, guild_id: int, user_id: int, challenge: dict):
        """Mark challenge complete and award reward."""
        reward = challenge.get("reward", 0)
        self.add_coins(guild_id, user_id, reward)
        
        completed = dm.get_guild_data(guild_id, "challenge_completed", {})
        completed[str(user_id)] = {
            "date": datetime.datetime.now().date().isoformat(),
            "challenge_id": challenge.get("id"),
            "reward": reward
        }
        dm.update_guild_data(guild_id, "challenge_completed", completed)
    
    def get_challenge_status(self, guild_id: int, user_id: int) -> dict:
        """Get user's challenge status."""
        challenge = self.get_daily_challenge(guild_id)
        progress = challenge.get("progress", {}).get(str(user_id), 0)
        target = challenge.get("target", 1)
        completed = self._user_completed_challenge(guild_id, user_id)
        
        return {
            "name": challenge.get("name"),
            "desc": challenge.get("desc"),
            "progress": f"{progress}/{target}",
            "percent": int((progress / target) * 100),
            "reward": challenge.get("reward", 0),
            "completed": completed
        }
    
    """Economy Achievements System"""
    ECONOMY_ACHIEVEMENTS = [
        {"id": "first_earn", "name": "First Coin", "desc": "Earn your first coin", "threshold": 1, "icon": "🪙"},
        {"id": "rich_1000", "name": "Getting Rich", "desc": "Have 1,000 coins", "threshold": 1000, "icon": "💰"},
        {"id": "rich_10000", "name": "Wealthy", "desc": "Have 10,000 coins", "threshold": 10000, "icon": "💎"},
        {"id": "millionaire", "name": "Millionaire", "desc": "Have 1,000,000 coins", "threshold": 1000000, "icon": "👑"},
        {"id": "daily_7", "name": "Dedicated", "desc": "Claim daily 7 times", "threshold": 7, "icon": "📅"},
        {"id": "daily_30", "name": "Loyal", "desc": "Claim daily 30 times", "threshold": 30, "icon": "🏆"},
        {"id": "giver", "name": "Generous", "desc": "Give 1,000 coins to others", "threshold": 1000, "icon": "🎁"},
        {"id": "shopper", "name": "Shopaholic", "desc": "Buy 10 items from shop", "threshold": 10, "icon": "🛒"},
    ]
    
    def get_achievements(self, guild_id: int, user_id: int) -> list:
        """Get user's earned achievements."""
        achievement_data = dm.get_guild_data(guild_id, "economy_achievements", {})
        return achievement_data.get(str(user_id), [])
    
    def check_achievements(self, guild_id: int, user_id: int) -> list:
        """Check and award new achievements."""
        new_achievements = []
        coins = self.get_coins(guild_id, user_id)
        current = self.get_achievements(guild_id, user_id)
        earned_ids = [a["id"] for a in current]
        
        # Check coin achievements
        for achievement in self.ECONOMY_ACHIEVEMENTS:
            if achievement["id"] in earned_ids:
                continue
            
            if achievement["id"].startswith("rich_"):
                threshold = achievement["threshold"]
                if coins >= threshold:
                    new_achievements.append(achievement)
                    current.append(achievement)
        
        if new_achievements:
            achievement_data = dm.get_guild_data(guild_id, "economy_achievements", {})
            achievement_data[str(user_id)] = current
            dm.update_guild_data(guild_id, "economy_achievements", achievement_data)
        
        return new_achievements
    
    def show_achievements(self, guild_id: int, user_id: int) -> discord.Embed:
        """Show user's achievements."""
        earned = self.get_achievements(guild_id, user_id)
        coins = self.get_coins(guild_id, user_id)
        
        embed = discord.Embed(title="🏆 Economy Achievements", color=discord.Color.gold())
        
        if not earned:
            embed.description = "No achievements yet! Keep playing to earn some."
            return embed
        
        earned_ids = [a["id"] for a in earned]
        for achievement in self.ECONOMY_ACHIEVEMENTS:
            earned = achievement["id"] in earned_ids
            icon = achievement.get("icon", "🔒")
            status = "✅" if earned else "❌"
            embed.add_field(
                name=f"{status} {icon} {achievement['name']}",
                value=f"{achievement['desc']}",
                inline=False
            )
        
        embed.set_footer(text=f"Coins: {coins:,}")
        return embed
    
    async def daily(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        
        last_daily = dm.get_guild_data(guild_id, "last_daily", {})
        last_time = last_daily.get(str(user_id))
        
        if last_time:
            last_date = datetime.datetime.fromisoformat(last_time)
            if (datetime.datetime.now() - last_date).days < 1:
                return await interaction.response.send_message("Daily reward already claimed today!", ephemeral=True)
        
        reward = 100
        self.add_coins(guild_id, user_id, reward)
        last_daily[str(user_id)] = str(datetime.datetime.now())
        
        # Track daily streak
        daily_count = dm.get_guild_data(guild_id, "daily_streaks", {})
        daily_count[str(user_id)] = daily_count.get(str(user_id), 0) + 1
        dm.update_guild_data(guild_id, "daily_streaks", daily_count)
        
        dm.update_guild_data(guild_id, "last_daily", last_daily)
        
        # Check for daily streak bonus
        streak = daily_count.get(str(user_id), 0)
        bonus = 0
        if streak % 7 == 0:
            bonus = 500
            self.add_coins(guild_id, user_id, bonus)
        
        # Build response
        msg = f"💰 You claimed your daily **{reward} coins**!"
        if bonus:
            msg += f"\n🎉 **Streak bonus: +{bonus} coins!** (day {streak})"
        
        # Check achievements
        new_achievements = self.check_achievements(guild_id, user_id)
        if new_achievements:
            for a in new_achievements:
                msg += f"\n🏆 **{a['icon']} Achievement Unlocked: {a['name']}!**"
        
        await interaction.response.send_message(msg, ephemeral=True)
    
    async def transfer(self, interaction: discord.Interaction, target: discord.Member, amount: int):
        if amount <= 0:
            return await interaction.response.send_message("Amount must be positive.", ephemeral=True)
        
        guild_id = interaction.guild.id
        sender_id = interaction.user.id
        
        if self.get_coins(guild_id, sender_id) < amount:
            return await interaction.response.send_message("Insufficient funds.", ephemeral=True)
        
        self.add_coins(guild_id, sender_id, -amount)
        self.add_coins(guild_id, target.id, amount)
        
        await interaction.response.send_message(f"💸 Transferred **{amount} coins** to {target.mention}.")
    
    async def setup(self, interaction: discord.Interaction, params: dict = None) -> bool:
        guild = interaction.guild
        
        economy_channel = await guild.create_text_channel("economy")
        shop_channel = await guild.create_text_channel("shop")
        
        custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
        
        custom_cmds["daily"] = json.dumps({
            "command_type": "economy_daily",
            "type": "system"
        })

        custom_cmds["balance"] = json.dumps({
            "command_type": "economy_balance",
            "type": "system"
        })

        custom_cmds["help economy"] = json.dumps({
            "command_type": "help_embed",
            "title": "Economy System Help",
            "description": "Manage your coins and trade with others.",
            "fields": [
                {"name": "!daily", "value": "Claim your daily coin reward.", "inline": False},
                {"name": "!balance", "value": "Check your coin balance.", "inline": False},
                {"name": "!transfer <user> <amount>", "value": "Send coins to another user.", "inline": False},
                {"name": "!help economy", "value": "Show this help message.", "inline": False},
                {"name": "!challenge", "value": "View today's daily challenge.", "inline": False},
                {"name": "!achievements", "value": "View your economy achievements.", "inline": False}
            ],
            "type": "system"
        })

        custom_cmds["challenge"] = json.dumps({
            "command_type": "help_embed",
            "title": "Daily Challenge",
            "description": "Complete daily challenges to earn bonus coins!",
            "fields": [
                {"name": "!challenge", "value": "View today's challenge progress.", "inline": False}
            ],
            "type": "system"
        })

        custom_cmds["achievements"] = json.dumps({
            "command_type": "help_embed",
            "title": "Economy Achievements",
            "description": "Earn achievements for economy activities!",
            "fields": [
                {"name": "!achievements", "value": "View your achievements.", "inline": False}
            ],
            "type": "system"
        })
        
        custom_cmds["help"] = json.dumps({
            "command_type": "help_all"
        })
        
        custom_cmds["shop"] = json.dumps({
            "command_type": "help_embed",
            "title": "Premium Shop",
            "description": "Spend your gems on exclusive items.",
            "fields": [
                {"name": "!shop", "value": "Browse available items.", "inline": False},
                {"name": "!buy <item>", "value": "Purchase an item.", "inline": False},
                {"name": "!help shop", "value": "Show shop help.", "inline": False}
            ]
        })
        
        dm.update_guild_data(guild.id, "custom_commands", custom_cmds)
        return True