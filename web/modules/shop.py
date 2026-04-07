import discord
from data_manager import dm
import datetime

class Shop:
    """
    Shop for Coins and Gems.
    Supports items: roles, custom colors, channels.
    Temporary items expire via scheduled tasks (conceptually handled by checking timestamps).
    """
    def __init__(self, bot):
        self.bot = bot

    def get_shop_items(self, guild_id: int):
        return dm.get_guild_data(guild_id, "shop_items", {
            "Wealthy Role": {"price": 1000, "currency": "coins", "type": "role", "role_id": 0},
            "Gem Master": {"price": 50, "currency": "gems", "type": "role", "role_id": 0},
            "Custom Color": {"price": 500, "currency": "coins", "type": "color"}
        })

    async def show_shop(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        items = self.get_shop_items(guild_id)
        
        embed = discord.Embed(title="🛒 Server Shop", color=discord.Color.blue())
        for name, data in items.items():
            price = data['price']
            currency = "💰 Coins" if data['currency'] == "coins" else "💎 Gems"
            embed.add_field(name=name, value=f"Price: {price} {currency}\nType: {data['type']}", inline=True)
            
        await interaction.response.send_message(embed=embed)

    async def buy_item(self, interaction: discord.Interaction, item_name: str):
        guild_id = interaction.guild.id
        user_id = interaction.user.id
        items = self.get_shop_items(guild_id)
        
        if item_name not in items:
            return await interaction.response.send_message("Item not found in shop.", ephemeral=True)
            
        item = items[item_name]
        price = item['price']
        currency = item['currency']
        
        if currency == "coins":
            if self.bot.economy.get_coins(guild_id, user_id) < price:
                return await interaction.response.send_message("Not enough coins!", ephemeral=True)
            self.bot.economy.add_coins(guild_id, user_id, -price)
        else:
            if not self.bot.leveling.spend_gems(guild_id, user_id, price):
                return await interaction.response.send_message("Not enough gems!", ephemeral=True)
                
        # Grant Item
        if item['type'] == "role":
            role = interaction.guild.get_role(item['role_id'])
            if role:
                await interaction.user.add_roles(role)
                await interaction.response.send_message(f"✅ Purchased **{item_name}**! Role assigned.")
            else:
                await interaction.response.send_message(f"✅ Purchased **{item_name}**! (Role missing from server, contact staff)")
        else:
            await interaction.response.send_message(f"✅ Purchased **{item_name}**!")

        # Log purchase
        purchases = dm.get_guild_data(guild_id, "purchases", [])
        purchases.append({
            "user_id": user_id,
            "item": item_name,
            "timestamp": str(datetime.datetime.now())
        })
        dm.update_guild_data(guild_id, "purchases", purchases)
