import discord
from discord import ui, Interaction, app_commands
import json
import os
from data_manager import dm
from logger import logger
from typing import Dict, Any, List, Optional

class ConfigPanelView(ui.View):
    """Base class for all persistent system configuration panels."""
    def __init__(self, guild_id: int, system_name: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.system_name = system_name
        self.custom_id_prefix = f"config_{system_name}_{guild_id}"

    def get_config(self) -> Dict[str, Any]:
        return dm.get_guild_data(self.guild_id, f"{self.system_name}_config", {})

    def save_config(self, config: Dict[str, Any]):
        dm.update_guild_data(self.guild_id, f"{self.system_name}_config", config)
        # Automatically register commands for the system
        from modules.auto_setup import AutoSetup
        setup_helper = AutoSetup(None) # bot not needed for static registration
        setup_helper._register_system_commands(self.guild_id, self.system_name)

    async def update_panel(self, interaction: Interaction):
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self) -> discord.Embed:
        # To be overridden by subclasses
        return discord.Embed(title=f"Config: {self.system_name.title()}")

# --- Specialized Views ---

class VerificationConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "verification")
    
    def create_embed(self) -> discord.Embed:
        config = self.get_config()
        enabled = config.get("enabled", False)
        channel_id = config.get("channel_id")
        role_id = config.get("role_id")
        
        embed = discord.Embed(
            title="🛡️ Verification System Configuration",
            description="Manage how users verify their account in this server.",
            color=discord.Color.green() if enabled else discord.Color.red()
        )
        embed.add_field(name="Status", value="✅ Enabled" if enabled else "❌ Disabled", inline=True)
        embed.add_field(name="Channel", value=f"<#{channel_id}>" if channel_id else "Not Set", inline=True)
        embed.add_field(name="Verified Role", value=f"<@&{role_id}>" if role_id else "Not Set", inline=True)
        return embed

    @ui.button(label="Toggle Status", style=discord.ButtonStyle.secondary, custom_id="verify_toggle")
    async def toggle(self, interaction: Interaction, button: ui.Button):
        config = self.get_config()
        config["enabled"] = not config.get("enabled", False)
        self.save_config(config)
        await self.update_panel(interaction)

    @ui.button(label="Set Channel", style=discord.ButtonStyle.primary, custom_id="verify_channel")
    async def set_channel(self, interaction: Interaction, button: ui.Button):
        # In a real implementation, this would open a channel select or a modal
        # For simplicity in this example, we use the current channel
        config = self.get_config()
        config["channel_id"] = interaction.channel.id
        self.save_config(config)
        await self.update_panel(interaction)

class EconomyConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "economy")

    def create_embed(self) -> discord.Embed:
        config = self.get_config()
        currency_name = config.get("currency_name", "coins")
        daily_reward = config.get("daily_reward", 100)
        
        embed = discord.Embed(
            title="💰 Economy System Configuration",
            description="Adjust the rewards and currency settings.",
            color=discord.Color.gold()
        )
        embed.add_field(name="Currency Name", value=currency_name.title(), inline=True)
        embed.add_field(name="Daily Reward", value=f"{daily_reward} {currency_name}", inline=True)
        return embed

    @ui.button(label="Set Daily Reward", style=discord.ButtonStyle.primary, custom_id="eco_daily")
    async def set_daily(self, interaction: Interaction, button: ui.Button):
        # Open a modal for number input
        class DailyModal(ui.Modal, title="Set Daily Reward"):
            amount = ui.TextInput(label="Amount", placeholder="e.g. 500", min_length=1, max_length=5)
            def __init__(self, parent):
                super().__init__()
                self.parent = parent
            async def on_submit(self, interaction: Interaction):
                if not self.amount.value.isdigit():
                    return await interaction.response.send_message("Please enter a number.", ephemeral=True)
                config = self.parent.get_config()
                config["daily_reward"] = int(self.amount.value)
                self.parent.save_config(config)
                await self.parent.update_panel(interaction)

        await interaction.response.send_modal(DailyModal(self))

class TicketsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "tickets")

    def create_embed(self) -> discord.Embed:
        config = self.get_config()
        category_id = config.get("category_id")
        
        embed = discord.Embed(
            title="🎫 Ticket System Configuration",
            description="Manage support tickets and categories.",
            color=discord.Color.blue()
        )
        embed.add_field(name="Ticket Category", value=f"<#{category_id}>" if category_id else "Not Set", inline=True)
        return embed

    @ui.button(label="Set Category", style=discord.ButtonStyle.primary, custom_id="ticket_cat")
    async def set_category(self, interaction: Interaction, button: ui.Button):
        config = self.get_config()
        # Find category logic
        config["category_id"] = interaction.channel.category_id if interaction.channel.category else None
        self.save_config(config)
        await self.update_panel(interaction)

# --- Config Modal for Generic Editing ---

class ConfigModal(ui.Modal):
    def __init__(self, parent: "GenericConfigPanelView", field: Dict[str, Any]):
        super().__init__(title=f"Edit {field['name']}")
        self.parent = parent
        self.field = field
        self.input = ui.TextInput(
            label=field["name"],
            default=str(parent.get_config().get(field["key"], field.get("default", ""))),
            required=True
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: Interaction):
        config = self.parent.get_config()
        value = self.input.value
        # Basic type conversion
        if isinstance(self.field.get("default"), bool):
            value = value.lower() in ("true", "1", "yes", "on")
        elif isinstance(self.field.get("default"), int):
            if value.isdigit():
                value = int(value)
            else:
                return await interaction.response.send_message("❌ Please enter a valid number.", ephemeral=True)
        
        config[self.field["key"]] = value
        self.parent.save_config(config)
        await self.parent.update_panel(interaction)

# --- Generic View for other systems ---

class GenericConfigPanelView(ConfigPanelView):
    def __init__(self, guild_id: int, system_name: str, fields: List[Dict[str, Any]]):
        super().__init__(guild_id, system_name)
        self.fields = fields
        # Dynamically add buttons for each field
        for field in self.fields:
            btn = ui.Button(label=f"Set {field['name']}", style=discord.ButtonStyle.secondary, custom_id=f"btn_{self.system_name}_{field['key']}")
            btn.callback = self.create_callback(field)
            self.add_item(btn)

    def create_callback(self, field):
        async def callback(interaction: Interaction):
            await interaction.response.send_modal(ConfigModal(self, field))
        return callback

    def create_embed(self) -> discord.Embed:
        config = self.get_config()
        embed = discord.Embed(
            title=f"⚙️ {self.system_name.replace('_', ' ').title()} Configuration",
            description=f"Manage settings for the {self.system_name} system.",
            color=discord.Color.blue()
        )
        for field in self.fields:
            key = field["key"]
            name = field["name"]
            value = config.get(key, field.get("default", "Not Set"))
            embed.add_field(name=name, value=str(value), inline=True)
        return embed

# --- System Metadata ---

SYSTEM_METADATA = {
    "antiraid": [{"key": "mass_join_threshold", "name": "Mass Join Threshold", "default": 10}, {"key": "lockdown_enabled", "name": "Auto-Lockdown", "default": False}],
    "guardian": [{"key": "spam_protection", "name": "Spam Protection", "default": True}, {"key": "invite_filtering", "name": "Invite Filtering", "default": True}],
    "welcome": [{"key": "channel_id", "name": "Welcome Channel"}, {"key": "message", "name": "Message", "default": "Welcome {user}!"}],
    "welcomedm": [{"key": "dm_enabled", "name": "DM Enabled", "default": True}, {"key": "dm_message", "name": "DM Message", "default": "Thanks for joining!"}],
    "application": [{"key": "logs_channel", "name": "Logs Channel"}, {"key": "staff_role", "name": "Reviewer Role"}],
    "applicationmodal": [{"key": "modal_title", "name": "Modal Title", "default": "Staff Application"}],
    "appeal": [{"key": "appeal_channel", "name": "Appeal Channel"}],
    "appealsystem": [{"key": "auto_unban", "name": "Auto Unban", "default": False}],
    "modmail": [{"key": "category_id", "name": "Modmail Category"}],
    "suggestion": [{"key": "suggestion_channel", "name": "Suggestions Channel"}],
    "reminder": [{"key": "max_reminders", "name": "Max Reminders", "default": 5}],
    "scheduledreminder": [{"key": "schedule_count", "name": "Active Schedules", "default": 0}],
    "announcement": [{"key": "ping_role", "name": "Ping Role"}],
    "autoresponder": [{"key": "responder_count", "name": "Active Responses", "default": 0}],
    "economyshop": [{"key": "shop_enabled", "name": "Shop Enabled", "default": True}],
    "leveling": [{"key": "xp_rate", "name": "XP Rate", "default": 1.0}],
    "levelingshop": [{"key": "item_count", "name": "Shop Items", "default": 0}],
    "giveaway": [{"key": "giveaway_logs", "name": "Giveaway Logs"}],
    "achievement": [{"key": "milestones_enabled", "name": "Milestones", "default": True}],
    "gamification": [{"key": "daily_quests", "name": "Daily Quests", "default": True}],
    "reactionrole": [{"key": "message_id", "name": "Message ID"}],
    "reactionrolemenu": [{"key": "menu_id", "name": "Menu ID"}],
    "rolebutton": [{"key": "button_count", "name": "Total Buttons", "default": 0}],
    "modlog": [{"key": "log_channel", "name": "Log Channel"}],
    "logging": [{"key": "voice_logs", "name": "Voice Logs", "default": True}],
    "automod": [{"key": "bad_words", "name": "Forbidden Words", "default": ""}],
    "warning": [{"key": "max_warnings", "name": "Max Warnings", "default": 3}],
    "staffpromo": [{"key": "score_threshold", "name": "Promotion Score", "default": 100}],
    "staffshift": [{"key": "shift_logs", "name": "Shift Logs"}],
    "staffreview": [{"key": "review_channel", "name": "Review Channel"}]
}

# --- Registry and Dispatch ---

SPECIALIZED_VIEWS = {
    "verification": VerificationConfigView,
    "economy": EconomyConfigView,
    "tickets": TicketsConfigView,
}

def get_config_panel(guild_id: int, system: str) -> Optional[ui.View]:
    system_key = system.lower()
    if system_key in SPECIALIZED_VIEWS:
        return SPECIALIZED_VIEWS[system_key](guild_id)
    elif system_key in SYSTEM_METADATA:
        return GenericConfigPanelView(guild_id, system_key, SYSTEM_METADATA[system_key])
    return None

async def handle_config_panel_command(message: discord.Message, system: str):
    """Handle !configpanel<system> prefix commands."""
    guild_id = message.guild.id
    view = get_config_panel(guild_id, system)
    if not view:
        return await message.channel.send(f"❌ System '{system}' not found.")
    
    embed = view.create_embed()
    await message.channel.send(embed=embed, view=view)

# --- Slash Command Group ---

configpanel_group = app_commands.Group(name="configpanel", description="Quick access to system configuration panels")

@configpanel_group.command(name="open", description="Open a specific configuration panel")
@app_commands.describe(system="The system configuration panel to open")
async def configpanel_open(interaction: Interaction, system: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Only administrators can use this command.", ephemeral=True)

    view = get_config_panel(interaction.guild.id, system)
    if not view:
        return await interaction.response.send_message(f"❌ System '{system}' not found. Use autocomplete to see available systems.", ephemeral=True)

    embed = view.create_embed()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@configpanel_open.autocomplete('system')
async def configpanel_system_autocomplete(interaction: Interaction, current: str) -> List[app_commands.Choice[str]]:
    all_systems = list(SPECIALIZED_VIEWS.keys()) + list(SYSTEM_METADATA.keys())
    return [
        app_commands.Choice(name=system.replace('_', ' ').title(), value=system)
        for system in all_systems if current.lower() in system.lower()
    ][:25]

def _create_standalone_panel_callback(system_name: str):
    async def callback(interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Only administrators can use this command.", ephemeral=True)

        view = get_config_panel(interaction.guild.id, system_name)
        if not view:
            return await interaction.response.send_message(f"❌ System '{system_name}' not found.", ephemeral=True)

        embed = view.create_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    return callback

def register_all_persistent_views(bot: discord.Client):
    """Register all persistent views in setup_hook.

    NOTE: Per the blueprint, /configpanel* slash commands are FORBIDDEN.
    Panels are opened only via /autosetup (master hub) or via /bot
    (AI-generated panels). Persistent views still need to be registered
    so panel buttons keep working across restarts. Prefix commands
    !configpanel<system> remain available via handle_config_panel_command.
    """
    # Register the specialized views (using a dummy guild_id=0 for registration)
    bot.add_view(VerificationConfigView(0))
    bot.add_view(EconomyConfigView(0))
    bot.add_view(TicketsConfigView(0))

    # Register generic views for all other systems
    for system_key, fields in SYSTEM_METADATA.items():
        if system_key not in SPECIALIZED_VIEWS:
            bot.add_view(GenericConfigPanelView(0, system_key, fields))

    logger.info("All 33 config panel persistent views registered (no slash commands per blueprint).")
