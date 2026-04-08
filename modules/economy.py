import discord
from data_manager import dm
import random
import datetime
import json

class Economy:
    """
    Coins per user. Earn per message, daily, transfers.
    Zero Data Loss with immediate writes.
    """
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
        dm.update_guild_data(guild_id, "last_daily", last_daily)
        
        await interaction.response.send_message(f"💰 You claimed your daily **{reward} coins**!", ephemeral=True)

    async def transfer(self, interaction: discord.Interaction, target: discord.Member, amount: int):
        if amount <= 0: return await interaction.response.send_message("Amount must be positive.", ephemeral=True)
        
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
                {"name": "!help economy", "value": "Show this help message.", "inline": False}
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
