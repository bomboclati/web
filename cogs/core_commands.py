import discord
from discord import app_commands
from discord.ext import commands
import logging

logger = logging.getLogger(__name__)

class CoreCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


    @app_commands.command(name="status", description="Check system health and memory depth")
    async def status_command(self, interaction: discord.Interaction):
        """Check system health"""
        await interaction.response.defer()
        
        # Gather system health information
        guild_count = len(self.bot.guilds)
        user_count = sum(g.member_count or 0 for g in self.bot.guilds)
        
        embed = discord.Embed(
            title="📊 System Status",
            color=discord.Color.green()
        )
        embed.add_field(name="Guilds", value=str(guild_count), inline=True)
        embed.add_field(name="Users", value=str(user_count), inline=True)
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
        embed.add_field(name="Uptime", value="Online", inline=True)
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="help", description="List all utility commands")
    async def help_command(self, interaction: discord.Interaction):
        """List all utility commands"""
        await interaction.response.defer()
        
        embed = discord.Embed(
            title="📚 Available Commands",
            description="Here are all the available slash commands:",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="/bot <text>",
            value="The main AI portal. Tell the AI what you want to build or do",
            inline=False
        )
        embed.add_field(
            name="/status",
            value="Check system health and memory depth",
            inline=False
        )
        embed.add_field(
            name="/help",
            value="List all utility commands",
            inline=False
        )
        embed.add_field(
            name="/list",
            value="See all active automations (custom commands, triggers)",
            inline=False
        )
        embed.add_field(
            name="/config",
            value="Adjust AI provider and model settings",
            inline=False
        )
        embed.add_field(
            name="/undo",
            value="Reverses the latest administrative actions",
            inline=False
        )
        embed.add_field(
            name="/setup",
            value="Configure various systems (moderation, economy, leveling, etc.)",
            inline=False
        )
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="list", description="See all active automations")
    async def list_command(self, interaction: discord.Interaction):
        """See all active automations"""
        await interaction.response.defer()
        
        # Get custom commands from data manager
        from data_manager import dm
        custom_commands = dm.get_guild_data(interaction.guild.id, "custom_commands", {})
        
        embed = discord.Embed(
            title="🔧 Active Automations",
            color=discord.Color.purple()
        )
        
        if custom_commands:
            cmd_list = "\n".join([f"• !{cmd}" for cmd in custom_commands.keys()])
            embed.add_field(name="Custom Commands", value=cmd_list or "None", inline=False)
        else:
            embed.add_field(name="Custom Commands", value="None configured", inline=False)
            
        # Add other automations info
        embed.add_field(name="Scheduled Tasks", value="Check dashboard or use !scheduled list", inline=False)
        embed.add_field(name="Triggers", value="Use !triggers to see active trigger roles", inline=False)
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="config", description="Adjust AI provider and model settings")
    @app_commands.describe(
        provider="AI provider to use (openrouter, openai, gemini)",
        model="Specific model to use"
    )
    @app_commands.choices(
        provider=[
            app_commands.Choice(name="OpenRouter", value="openrouter"),
            app_commands.Choice(name="OpenAI", value="openai"),
            app_commands.Choice(name="Gemini", value="gemini")
        ]
    )
    async def config_command(self, interaction: discord.Interaction, provider: str = None, model: str = None):
        """Adjust AI provider and model settings"""
        await interaction.response.defer()
        
        from data_manager import dm
        
        # Update guild configuration
        config = dm.get_guild_data(interaction.guild.id, "ai_config", {})
        if provider:
            config["provider"] = provider
        if model:
            config["model"] = model
            
        dm.update_guild_data(interaction.guild.id, "ai_config", config)
        
        embed = discord.Embed(
            title="⚙️ AI Configuration Updated",
            color=discord.Color.orange()
        )
        if provider:
            embed.add_field(name="Provider", value=provider, inline=True)
        if model:
            embed.add_field(name="Model", value=model, inline=True)
        embed.add_field(name="Guild ID", value=str(interaction.guild.id), inline=True)
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="undo", description="Reverses the latest administrative actions")
    async def undo_command(self, interaction: discord.Interaction):
        """Reverses the latest administrative actions"""
        await interaction.response.defer()
        
        # This would normally check for recent actions and undo them
        # For now, we'll just acknowledge
        await interaction.followup.send("↩️ Undo functionality would reverse the latest administrative actions.\n\n*This feature requires tracking of recent actions to work properly.*")

    @app_commands.command(name="setup", description="Configure various systems")
    @app_commands.describe(system="Which system to setup")
    @app_commands.choices(
        system=[
            app_commands.Choice(name="Moderation System", value="moderation"),
            app_commands.Choice(name="Economy System", value="economy"),
            app_commands.Choice(name="Leveling System", value="leveling"),
            app_commands.Choice(name="Ticket System", value="tickets"),
            app_commands.Choice(name="Modmail System", value="modmail"),
            app_commands.Choice(name="Welcome/Goodbye", value="welcome"),
            app_commands.Choice(name="Auto-Setup Wizard", value="wizard")
        ]
    )
    async def setup_command(self, interaction: discord.Interaction, system: str = None):
        """Configure various systems"""
        await interaction.response.defer()
        
        if system == "wizard" or not system:
            # Trigger the auto-setup wizard
            await interaction.followup.send("🧙‍♂️ Starting auto-setup wizard...\n\n*This would guide you through setting up various systems step by step.*")
        else:
            await interaction.followup.send(f"🔧 Setting up {system} system...\n\n*This would configure the {system} system for your server.*")

async def setup(bot):
    await bot.add_cog(CoreCommands(bot))