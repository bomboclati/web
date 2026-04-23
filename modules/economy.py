import discord
from discord.ui import Button, View, Modal, TextInput, Select
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
    
    def get_gems(self, guild_id: int, user_id: int) -> int:
        balances = dm.get_guild_data(guild_id, "economy_gems", {})
        return balances.get(str(user_id), 0)
    
    def add_coins(self, guild_id: int, user_id: int, amount: int):
        balances = dm.get_guild_data(guild_id, "economy_balances", {})
        current = balances.get(str(user_id), 0)
        balances[str(user_id)] = current + amount
        dm.update_guild_data(guild_id, "economy_balances", balances)
    
    def add_gems(self, guild_id: int, user_id: int, amount: int):
        gems = dm.get_guild_data(guild_id, "economy_gems", {})
        current = gems.get(str(user_id), 0)
        gems[str(user_id)] = current + amount
        dm.update_guild_data(guild_id, "economy_gems", gems)
    
    def log_transaction(self, guild_id: int, user_id: int, amount: int, tx_type: str, reason: str):
        transactions = dm.get_guild_data(guild_id, "economy_transactions", [])
        transactions.append({
            "user_id": user_id,
            "amount": amount,
            "type": tx_type,
            "reason": reason,
            "timestamp": datetime.datetime.now().isoformat()
        })
        # Keep last 1000 transactions
        if len(transactions) > 1000:
            transactions = transactions[-1000:]
        dm.update_guild_data(guild_id, "economy_transactions", transactions)
    
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
            "command_type": "economy_daily"
        })
        
        custom_cmds["balance"] = json.dumps({
            "command_type": "economy_balance"
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
            ]
        })
        
        custom_cmds["challenge"] = json.dumps({
            "command_type": "help_embed",
            "title": "Daily Challenge",
            "description": "Complete daily challenges to earn bonus coins!",
            "fields": [
                {"name": "!challenge", "value": "View today's challenge progress.", "inline": False}
            ]
        })
        
        custom_cmds["achievements"] = json.dumps({
            "command_type": "help_embed",
            "title": "Economy Achievements",
            "description": "Earn achievements for economy activities!",
            "fields": [
                {"name": "!achievements", "value": "View your achievements.", "inline": False}
            ]
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

# Panel classes for Economy module

class EconomyPanel(View):
    """Admin panel for Economy configuration."""
    
    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        self.economy = Economy(bot)
    
    @discord.ui.button(label="Add Coins", style=discord.ButtonStyle.success, row=0)
    async def add_coins(self, interaction: discord.Interaction, button: Button):
        modal = AddRemoveCoinsModal(self.bot, self.guild_id, action="add")
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Remove Coins", style=discord.ButtonStyle.danger, row=0)
    async def remove_coins(self, interaction: discord.Interaction, button: Button):
        modal = AddRemoveCoinsModal(self.bot, self.guild_id, action="remove")
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Add Gems", style=discord.ButtonStyle.success, row=0)
    async def add_gems(self, interaction: discord.Interaction, button: Button):
        modal = AddRemoveGemsModal(self.bot, self.guild_id, action="add")
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Remove Gems", style=discord.ButtonStyle.danger, row=0)
    async def remove_gems(self, interaction: discord.Interaction, button: Button):
        modal = AddRemoveGemsModal(self.bot, self.guild_id, action="remove")
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="View Balance", style=discord.ButtonStyle.primary, row=1)
    async def view_balance(self, interaction: discord.Interaction, button: Button):
        modal = ViewBalanceModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Transfer", style=discord.ButtonStyle.secondary, row=1)
    async def transfer_btn(self, interaction: discord.Interaction, button: Button):
        modal = TransferModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Leaderboard", style=discord.ButtonStyle.primary, row=1)
    async def leaderboard(self, interaction: discord.Interaction, button: Button):
        balances = dm.get_guild_data(self.guild_id, "economy_balances", {})
        sorted_users = sorted(balances.items(), key=lambda x: x[1], reverse=True)[:10]
        embed = discord.Embed(title="Top Richest Users", color=discord.Color.gold())
        for i, (user_id, amount) in enumerate(sorted_users, 1):
            user = self.bot.get_user(int(user_id))
            name = user.display_name if user else f"User {user_id}"
            embed.add_field(name=f"{i}. {name}", value=f"{amount:,} coins", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="Stats", style=discord.ButtonStyle.secondary, row=1)
    async def show_stats(self, interaction: discord.Interaction, button: Button):
        balances = dm.get_guild_data(self.guild_id, "economy_balances", {})
        total_coins = sum(balances.values())
        embed = discord.Embed(title="Economy Stats", color=discord.Color.gold())
        embed.add_field(name="Total Coins", value=f"{total_coins:,}", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="Add Shop Item", style=discord.ButtonStyle.success, row=2)
    async def add_shop_item(self, interaction: discord.Interaction, button: Button):
        modal = AddShopItemModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Configure Daily", style=discord.ButtonStyle.secondary, row=2)
    async def configure_daily(self, interaction: discord.Interaction, button: Button):
        modal = ConfigureDailyModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Set Currency Name", style=discord.ButtonStyle.secondary, row=2)
    async def set_currency_name(self, interaction: discord.Interaction, button: Button):
        modal = SetCurrencyNameModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Transaction Log", style=discord.ButtonStyle.primary, row=3)
    async def transaction_log(self, interaction: discord.Interaction, button: Button):
        transactions = dm.get_guild_data(self.guild_id, "economy_transactions", [])
        recent = transactions[-10:][::-1]
        embed = discord.Embed(title="Recent Transactions", color=discord.Color.blue())
        for tx in recent:
            embed.add_field(name=f"User {tx["user_id"]}: {tx["amount"]}", value=f"{tx["type"]} - {tx["reason"]}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="Reset User Balance", style=discord.ButtonStyle.danger, row=3)
    async def reset_balance(self, interaction: discord.Interaction, button: Button):
        modal = ResetBalanceModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)


class AddRemoveCoinsModal(Modal, title="Modify Coins"):
    def __init__(self, bot, guild_id: int, action: str):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.action = action
        self.economy = Economy(bot)
    
    user_input = TextInput(label="User ID or Mention", placeholder="@user or 123456789")
    amount_input = TextInput(label="Amount", placeholder="100")
    reason_input = TextInput(label="Reason", required=False, placeholder="Optional reason")
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = int(self.user_input.value.strip("<@!>"))
            amount = int(self.amount_input.value)
        except ValueError:
            return await interaction.response.send_message("Invalid input.", ephemeral=True)
        if amount <= 0:
            return await interaction.response.send_message("Amount must be positive.", ephemeral=True)
        if self.action == "remove":
            current = self.economy.get_coins(self.guild_id, user_id)
            if current < amount:
                return await interaction.response.send_message(f"User only has {current} coins.", ephemeral=True)
            amount = -amount
        self.economy.add_coins(self.guild_id, user_id, amount)
        self.economy.log_transaction(self.guild_id, user_id, amount, "admin_adjust", self.reason_input.value or "Admin adjustment")
        await interaction.response.send_message(f"Done!", ephemeral=True)


class AddRemoveGemsModal(Modal, title="Modify Gems"):
    def __init__(self, bot, guild_id: int, action: str):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.action = action
        self.economy = Economy(bot)
    
    user_input = TextInput(label="User ID or Mention", placeholder="@user or 123456789")
    amount_input = TextInput(label="Amount", placeholder="10")
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = int(self.user_input.value.strip("<@!>"))
            amount = int(self.amount_input.value)
        except ValueError:
            return await interaction.response.send_message("Invalid input.", ephemeral=True)
        if amount <= 0:
            return await interaction.response.send_message("Amount must be positive.", ephemeral=True)
        if self.action == "remove":
            current = self.economy.get_gems(self.guild_id, user_id)
            if current < amount:
                return await interaction.response.send_message(f"User only has {current} gems.", ephemeral=True)
            amount = -amount
        self.economy.add_gems(self.guild_id, user_id, amount)
        await interaction.response.send_message(f"Done!", ephemeral=True)


class ViewBalanceModal(Modal, title="View Balance"):
    def __init__(self, bot, guild_id: int):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.economy = Economy(bot)
    
    user_input = TextInput(label="User ID or Mention", placeholder="@user or 123456789")
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = int(self.user_input.value.strip("<@!>"))
        except ValueError:
            return await interaction.response.send_message("Invalid user ID.", ephemeral=True)
        coins = self.economy.get_coins(self.guild_id, user_id)
        gems = self.economy.get_gems(self.guild_id, user_id)
        embed = discord.Embed(title=f"Balance for <@{user_id}>", color=discord.Color.gold())
        embed.add_field(name="Coins", value=f"{coins:,}", inline=True)
        embed.add_field(name="Gems", value=f"{gems:,}", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class TransferModal(Modal, title="Transfer Coins"):
    def __init__(self, bot, guild_id: int):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.economy = Economy(bot)
    
    from_user = TextInput(label="From User ID", placeholder="123456789")
    to_user = TextInput(label="To User ID", placeholder="987654321")
    amount_input = TextInput(label="Amount", placeholder="100")
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            from_id = int(self.from_user.value)
            to_id = int(self.to_user.value)
            amount = int(self.amount_input.value)
        except ValueError:
            return await interaction.response.send_message("Invalid input.", ephemeral=True)
        if amount <= 0:
            return await interaction.response.send_message("Amount must be positive.", ephemeral=True)
        current = self.economy.get_coins(self.guild_id, from_id)
        if current < amount:
            return await interaction.response.send_message(f"User only has {current} coins.", ephemeral=True)
        self.economy.add_coins(self.guild_id, from_id, -amount)
        self.economy.add_coins(self.guild_id, to_id, amount)
        await interaction.response.send_message(f"Transferred {amount} coins!", ephemeral=True)


class AddShopItemModal(Modal, title="Add Shop Item"):
    def __init__(self, bot, guild_id: int):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
    
    item_name = TextInput(label="Item Name", placeholder="VIP Role")
    price = TextInput(label="Price", placeholder="1000")
    description = TextInput(label="Description", style=discord.TextStyle.long, placeholder="Grants VIP access")
    role_id = TextInput(label="Role ID (optional)", required=False)
    
    async def on_submit(self, interaction: discord.Interaction):
        items = dm.get_guild_data(self.guild_id, "shop_items", [])
        item = {"id": len(items) + 1, "name": self.item_name.value, "price": int(self.price.value), "description": self.description.value, "role_id": self.role_id.value if self.role_id.value else None, "stock": -1}
        items.append(item)
        dm.update_guild_data(self.guild_id, "shop_items", items)
        await interaction.response.send_message(f"Added to shop!", ephemeral=True)


class ConfigureDailyModal(Modal, title="Configure Daily Reward"):
    def __init__(self, bot, guild_id: int):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
    
    amount = TextInput(label="Daily Amount", placeholder="100")
    cooldown = TextInput(label="Cooldown Hours", placeholder="24")
    
    async def on_submit(self, interaction: discord.Interaction):
        config = dm.get_guild_data(self.guild_id, "economy_config", {})
        config["daily_amount"] = int(self.amount.value)
        config["daily_cooldown"] = int(self.cooldown.value)
        dm.update_guild_data(self.guild_id, "economy_config", config)
        await interaction.response.send_message(f"Daily configured!", ephemeral=True)


class SetCurrencyNameModal(Modal, title="Set Currency Names"):
    def __init__(self, bot, guild_id: int):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
    
    coin_name = TextInput(label="Coin Name", placeholder="Coins", default="Coins")
    gem_name = TextInput(label="Gem Name", placeholder="Gems", default="Gems")
    
    async def on_submit(self, interaction: discord.Interaction):
        config = dm.get_guild_data(self.guild_id, "economy_config", {})
        config["currency_name"] = self.coin_name.value
        config["gem_name"] = self.gem_name.value
        dm.update_guild_data(self.guild_id, "economy_config", config)
        await interaction.response.send_message("Currency names updated!", ephemeral=True)


class ResetBalanceModal(Modal, title="Reset User Balance"):
    def __init__(self, bot, guild_id: int):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.economy = Economy(bot)
    
    user_input = TextInput(label="User ID", placeholder="123456789")
    confirm = TextInput(label="Type RESET to confirm", placeholder="RESET")
    
    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.upper() != "RESET":
            return await interaction.response.send_message("Confirmation failed.", ephemeral=True)
        try:
            user_id = int(self.user_input.value)
        except ValueError:
            return await interaction.response.send_message("Invalid user ID.", ephemeral=True)
        balances = dm.get_guild_data(self.guild_id, "economy_balances", {})
        old_balance = balances.get(str(user_id), 0)
        balances[str(user_id)] = 0
        dm.update_guild_data(self.guild_id, "economy_balances", balances)
        self.economy.log_transaction(self.guild_id, user_id, -old_balance, "reset", "Admin reset")
        await interaction.response.send_message(f"Reset balance from {old_balance} to 0!", ephemeral=True)

