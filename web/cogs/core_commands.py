import os
import discord
from discord import app_commands
from discord.ext import commands
import logging
import json
from data_manager import dm

logger = logging.getLogger(__name__)

class CoreCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    config = app_commands.Group(name="config", description="Configure bot settings")

    @config.command(name="model", description="Set the AI model")
    @app_commands.describe(model="Model name (e.g. gpt-4, claude-3)")
    async def config_model(self, interaction: discord.Interaction, model: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only Administrators can change configuration.", ephemeral=True)
            return
        
        dm.update_guild_data(interaction.guild.id, "custom_model", model)
        await interaction.response.send_message(f"? AI model set to **{model}** for this server.", ephemeral=True)

    @config.command(name="provider", description="Set the active AI provider")
    @app_commands.choices(provider=[
        app_commands.Choice(name="OpenRouter", value="openrouter"),
        app_commands.Choice(name="OpenAI", value="openai"),
        app_commands.Choice(name="Gemini", value="gemini"),
        app_commands.Choice(name="Anthropic", value="anthropic"),
        app_commands.Choice(name="Cerebras", value="cerebras"),
        app_commands.Choice(name="SambaNova", value="sambanova"),
        app_commands.Choice(name="Together", value="together"),
        app_commands.Choice(name="Groq", value="groq"),
        app_commands.Choice(name="Mistral", value="mistral"),
        app_commands.Choice(name="DeepSeek", value="deepseek"),
        app_commands.Choice(name="DashScope", value="dashscope")
    ])
    async def config_provider(self, interaction: discord.Interaction, provider: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only Administrators can change configuration.", ephemeral=True)
            return
            
        if provider not in self.bot.ai.base_urls:
            await interaction.response.send_message(f"? Unknown provider. Valid: {', '.join(self.bot.ai.base_urls.keys())}", ephemeral=True)
            return
        
        current_key_data = dm.get_guild_api_key(interaction.guild.id, provider=provider)
        api_key = current_key_data.get("api_key") if current_key_data else None
        
        if not api_key:
            global_key = os.getenv("AI_API_KEY") if provider == os.getenv("AI_PROVIDER", "openrouter") else None
            if not global_key:
                await interaction.response.send_message(f"?? **Note:** Provider set to **{provider}**, but no API key is configured for it. Use `/config key` to set one.", ephemeral=True)
                return
        
        dm.set_guild_api_key(interaction.guild.id, api_key or "", provider)
        await interaction.response.send_message(f"? AI provider switched to **{provider}**.", ephemeral=True)

    @config.command(name="key", description="Set the API key for a provider")
    @app_commands.choices(provider=[
        app_commands.Choice(name="OpenRouter", value="openrouter"),
        app_commands.Choice(name="OpenAI", value="openai"),
        app_commands.Choice(name="Gemini", value="gemini"),
        app_commands.Choice(name="Anthropic", value="anthropic"),
        app_commands.Choice(name="Cerebras", value="cerebras"),
        app_commands.Choice(name="SambaNova", value="sambanova"),
        app_commands.Choice(name="Together", value="together"),
        app_commands.Choice(name="Groq", value="groq"),
        app_commands.Choice(name="Mistral", value="mistral"),
        app_commands.Choice(name="DeepSeek", value="deepseek"),
        app_commands.Choice(name="DashScope", value="dashscope")
    ])
    @app_commands.describe(api_key="Your API key for this provider")
    async def config_key(self, interaction: discord.Interaction, provider: str, api_key: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only Administrators can change configuration.", ephemeral=True)
            return
        
        dm.set_guild_api_key(interaction.guild.id, api_key, provider)
        await interaction.response.send_message(f"? API key for **{provider}** has been updated and encrypted.", ephemeral=True)

    @config.command(name="prefix", description="Set the server prefix")
    @app_commands.describe(prefix="New prefix character (max 5 chars)")
    async def config_prefix(self, interaction: discord.Interaction, prefix: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only Administrators can change configuration.", ephemeral=True)
            return
            
        if len(prefix) > 5:
            await interaction.response.send_message("? Prefix must be 5 characters or less.", ephemeral=True)
            return
            
        dm.update_guild_data(interaction.guild.id, "prefix", prefix)
        await interaction.response.send_message(f"? Server prefix set to **{prefix}**.", ephemeral=True)

    @config.command(name="cooldown", description="Set cooldown for custom ! commands")
    @app_commands.describe(command="Command name (without !)", seconds="Cooldown in seconds")
    async def config_cooldown(self, interaction: discord.Interaction, command: str, seconds: int):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only Administrators can configure cooldowns.", ephemeral=True)
            return

        if seconds < 0 or seconds > 86400:
            await interaction.response.send_message("Cooldown must be between 0 and 86400 seconds.", ephemeral=True)
            return

        custom_cmds = dm.get_guild_data(interaction.guild.id, "custom_commands", {})
        if command not in custom_cmds:
            await interaction.response.send_message(f"Command '!{command}' not found.", ephemeral=True)
            return

        # Update the command with cooldown
        cmd_data = json.loads(custom_cmds[command])
        cmd_data["cooldown"] = seconds
        custom_cmds[command] = json.dumps(cmd_data)
        dm.update_guild_data(interaction.guild.id, "custom_commands", custom_cmds)

        await interaction.response.send_message(f"? Cooldown for '!{command}' set to {seconds} seconds.", ephemeral=True)

    @config.command(name="sync", description="Force sync slash commands (Admin only)")
    async def config_sync(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only Administrators can sync commands.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await self.bot.tree.sync()
            await interaction.followup.send("? Slash commands synced successfully! It may take a few minutes to refresh in your Discord client.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"? Sync failed: {e}", ephemeral=True)

    @config.command(name="depth", description="Set memory depth")
    @app_commands.describe(depth="Number of messages to remember (5-100)")
    async def config_depth(self, interaction: discord.Interaction, depth: int):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only Administrators can change configuration.", ephemeral=True)
            return
            
        if depth < 5 or depth > 100:
            await interaction.response.send_message("? Depth must be between 5 and 100.", ephemeral=True)
            return
            
        dm.update_guild_data(interaction.guild.id, "memory_depth", depth)
        await interaction.response.send_message(f"? Memory depth set to **{depth}**.", ephemeral=True)

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
            await interaction.followup.send("????? Starting auto-setup wizard...\n\n*This would guide you through setting up various systems step by step.*")
        else:
            await interaction.followup.send(f"?? Setting up {system} system...\n\n*This would configure the {system} system for your server.*")

async def setup(bot):
    await bot.add_cog(CoreCommands(bot))