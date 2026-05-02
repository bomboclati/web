import discord
from discord import ui
from data_manager import dm
from typing import Dict, List, Optional
import time

async def send_help(channel, guild_id, user, system_query=None, bot=None):
    """
    Standalone help entry point used by bot.py
    """
    helper = HelpSystem(bot)
    
    if system_query:
        # Search for category or command
        query = system_query.lower()
        category = helper.categories.get(query)
        if category:
            embed = discord.Embed(
                title=f"{category.emoji} {category.name} Commands",
                description=category.description,
                color=discord.Color.blue()
            )
            prefix = dm.get_guild_data(guild_id, "prefix", "!")
            for cmd in category.commands:
                embed.add_field(name=cmd["name"].replace("!", prefix), value=cmd["desc"], inline=False)
            return await channel.send(embed=embed)
            
        # If not a category, search commands
        for cat in helper.categories.values():
            for cmd in cat.commands:
                if query in cmd["name"].lower():
                    prefix = dm.get_guild_data(guild_id, "prefix", "!")
                    embed = discord.Embed(title=f"❓ Help: {cmd['name'].replace('!', prefix)}", description=cmd["desc"], color=discord.Color.green())
                    return await channel.send(embed=embed)

    # Default interactive help
    embed = discord.Embed(
        title="✨ Miro AI Help Center",
        description=(
            "Welcome to the **Miro AI Help System**! Use the dropdown menu below to explore available commands by category.\n\n"
            "**Quick Links:**\n"
            "🌐 [Dashboard](https://miro-bot.com/dash) | 📖 [Docs](https://miro-bot.com/docs)"
        ),
        color=discord.Color.from_rgb(88, 101, 242)
    )
    embed.add_field(name="Systems", value="33+ Active", inline=True)
    embed.add_field(name="AI Engine", value="Multimodal", inline=True)
    embed.add_field(name="Prefix", value=f"`{dm.get_guild_data(guild_id, 'prefix', '!')}`", inline=True)
    
    embed.set_footer(text=f"Requested by {user.display_name}")
    
    view = HelpView(helper.categories)
    await channel.send(embed=embed, view=view)

class HelpCategory:
    def __init__(self, name: str, emoji: str, description: str, commands: List[Dict[str, str]]):
        self.name = name
        self.emoji = emoji
        self.description = description
        self.commands = commands

class HelpDropdown(ui.Select):
    def __init__(self, categories: Dict[str, HelpCategory]):
        options = [
            discord.SelectOption(label=cat.name, emoji=cat.emoji, description=cat.description, value=name)
            for name, cat in categories.items()
        ]
        super().__init__(placeholder="Select a category to view commands...", options=options, custom_id="help_dropdown")
        self.categories = categories

    async def callback(self, interaction: discord.Interaction):
        category = self.categories.get(self.values[0])
        if not category:
            return await interaction.response.send_message("❌ Category not found.", ephemeral=True)

        embed = discord.Embed(
            title=f"{category.emoji} {category.name} Commands",
            description=category.description,
            color=discord.Color.blue()
        )
        
        prefix = dm.get_guild_data(interaction.guild_id, "prefix", "!")
        
        for cmd in category.commands:
            name = cmd["name"].replace("!", prefix)
            embed.add_field(name=name, value=cmd["desc"], inline=False)
            
        embed.set_footer(text=f"Miro AI Help System | Requested by {interaction.user.display_name}")
        await interaction.response.edit_message(embed=embed)

class HelpView(ui.View):
    def __init__(self, categories: Dict[str, HelpCategory]):
        super().__init__(timeout=180)
        self.add_item(HelpDropdown(categories))

class HelpSystem:
    def __init__(self, bot):
        self.bot = bot
        self.categories = {
            "core": HelpCategory("Core & Config", "⚙️", "Essential commands and server configuration.", [
                {"name": "!help", "desc": "Display this help menu."},
                {"name": "!prefix <char>", "desc": "Change the server's command prefix."},
                {"name": "!config", "desc": "Open the web-style configuration dashboard."},
                {"name": "!sync", "desc": "Sync slash commands (Admin only)."}
            ]),
            "moderation": HelpCategory("Moderation & Security", "🛡️", "Keep your server safe and organized.", [
                {"name": "!ban <user> [reason]", "desc": "Permanently ban a member."},
                {"name": "!kick <user> [reason]", "desc": "Kick a member from the server."},
                {"name": "!mute <user> <time> [reason]", "desc": "Temporarily mute a member."},
                {"name": "!warn <user> <reason>", "desc": "Issue a formal warning."},
                {"name": "!cases [user]", "desc": "View moderation history."},
                {"name": "!lock/!unlock", "desc": "Lock or unlock the current channel."},
                {"name": "!purge <amount>", "desc": "Delete a bulk of messages."}
            ]),
            "economy": HelpCategory("Economy & Games", "💰", "Earn coins and spend them in the shop.", [
                {"name": "!daily", "desc": "Claim your daily coin reward."},
                {"name": "!balance [user]", "desc": "Check coin balance."},
                {"name": "!work", "desc": "Work for 30 minutes to earn coins."},
                {"name": "!beg", "desc": "Beg for a few coins."},
                {"name": "!shop", "desc": "Browse items in the premium shop."},
                {"name": "!buy <item>", "desc": "Purchase an item from the shop."},
                {"name": "!pay <user> <amount>", "desc": "Transfer coins to another user."}
            ]),
            "social": HelpCategory("Roles & Social", "🎭", "Manage roles and engage with the community.", [
                {"name": "!rank [user]", "desc": "Check leveling rank and XP."},
                {"name": "!leaderboard", "desc": "View the top members by XP."},
                {"name": "!verify", "desc": "Open the verification prompt."},
                {"name": "!suggest <text>", "desc": "Submit a suggestion for the server."},
                {"name": "!apply", "desc": "Submit a staff application."}
            ]),
            "utility": HelpCategory("Utility & AI", "🤖", "Useful tools and AI integration.", [
                {"name": "!remind <time> <msg>", "desc": "Set a personal reminder."},
                {"name": "!reminders", "desc": "List your active reminders."},
                {"name": "!chat <message>", "desc": "Talk directly to Miro AI."},
                {"name": "!ping", "desc": "Check bot latency."},
                {"name": "!serverinfo", "desc": "View detailed server statistics."}
            ])
        }
