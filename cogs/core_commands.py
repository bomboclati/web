import os
import discord
from discord import app_commands
from discord.ext import commands
import logging
from data_manager import dm
from typing import List

logger = logging.getLogger(__name__)

class CoreCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Model lists for each provider
    MODEL_CHOICES = {
        "openrouter": [
            "openai/gpt-4o-mini", "openai/gpt-4o", "openai/gpt-4-turbo", "openai/gpt-3.5-turbo",
            "anthropic/claude-3-5-sonnet", "anthropic/claude-3-haiku", "anthropic/claude-3-opus",
            "google/gemini-pro-1.5", "google/gemini-flash-1.5",
            "meta-llama/llama-3.2-90b-text", "meta-llama/llama-3.1-405b-instruct",
            "mistralai/mistral-7b-instruct", "mistralai/mixtral-8x7b-instruct"
        ],
        "openai": [
            "gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"
        ],
        "gemini": [
            "gemini-1.5-flash-latest", "gemini-1.5-pro-latest", "gemini-1.0-pro"
        ],
        "anthropic": [
            "claude-3-5-sonnet-20240620", "claude-3-opus-20240229", "claude-3-haiku-20240307",
            "claude-3-sonnet-20240229"
        ],
        "groq": [
            "llama-3.3-70b-versatile", "llama-3.2-90b-text", "llama-3.1-70b-versatile",
            "llama-3.1-8b-instant", "mixtral-8x7b-32768", "gemma2-9b-it"
        ],
        "mistral": [
            "mistral-large-latest", "mistral-medium", "mistral-small", "open-mistral-7b"
        ],
        "deepseek": [
            "deepseek-chat", "deepseek-coder"
        ],
        "dashscope": [
            "qwen2.5-72b-instruct", "qwen2-72b-instruct", "qwen-turbo", "qwen-plus"
        ],
        "cerebras": [
            "llama3.3-70b", "llama3.1-70b", "llama3.1-8b"
        ],
        "sambanova": [
            "llama3.1-70b-instruct", "llama3.1-405b-instruct", "llama3.1-8b-instruct"
        ],
        "together": [
            "meta-llama/Llama-3.3-70B-Instruct-Turbo", "meta-llama/Llama-3.1-405B-Instruct",
            "meta-llama/Llama-3.1-70B-Instruct", "meta-llama/Llama-3.1-8B-Instruct"
        ]
    }

    def get_default_model(self, provider: str) -> str:
        """Get the default model for a provider"""
        defaults = {
            "openrouter": "openai/gpt-4o-mini",
            "openai": "gpt-4o-mini",
            "gemini": "gemini-1.5-flash-latest",
            "anthropic": "claude-3-5-sonnet-20240620",
            "groq": "llama-3.3-70b-versatile",
            "mistral": "mistral-large-latest",
            "deepseek": "deepseek-chat",
            "dashscope": "qwen2.5-72b-instruct",
            "cerebras": "llama3.3-70b",
            "sambanova": "llama3.1-70b-instruct",
            "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo"
        }
        return defaults.get(provider, "gpt-3.5-turbo")

    config = app_commands.Group(name="config", description="Configure bot settings")

    async def config_model_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete for model selection based on active provider"""
        # Get the guild's active provider
        current_config = dm.get_guild_api_key(interaction.guild.id) if interaction.guild else None
        provider = current_config.get("provider", "openrouter") if current_config else "openrouter"

        # Get models for this provider
        available_models = self.MODEL_CHOICES.get(provider, self.MODEL_CHOICES["openrouter"])

        # Filter models based on current input
        filtered_models = [m for m in available_models if current.lower() in m.lower()]

        # Return up to 25 choices
        return [app_commands.Choice(name=model, value=model) for model in filtered_models[:25]]

    @config.command(name="model", description="Set the AI model")
    @app_commands.describe(model="Model name (e.g. gpt-4, claude-3)")
    @app_commands.autocomplete(model=config_model_autocomplete)
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
        # Reset custom_model to provider's default
        default_model = self.get_default_model(provider)
        dm.update_guild_data(interaction.guild.id, "custom_model", default_model)
        await interaction.response.send_message(f"? AI provider switched to **{provider}** and model reset to **{default_model}**.", ephemeral=True)

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