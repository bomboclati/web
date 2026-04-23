import discord
from discord import ui, Interaction, app_commands
import json
import os
import time
import random
from datetime import datetime, timezone
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

class _RoleSelectFor(ui.RoleSelect):
    def __init__(self, parent: "VerificationConfigView", config_key: str, placeholder: str):
        super().__init__(placeholder=placeholder, min_values=1, max_values=1)
        self.parent = parent
        self.config_key = config_key

    async def callback(self, interaction: Interaction):
        role = self.values[0]
        config = self.parent.get_config()
        config[self.config_key] = role.id
        self.parent.save_config(config)
        await interaction.response.send_message(
            f"✅ Set **{self.config_key.replace('_', ' ').title()}** to {role.mention}",
            ephemeral=True,
        )


class _ChannelSelectFor(ui.ChannelSelect):
    def __init__(self, parent: "VerificationConfigView", config_key: str, placeholder: str):
        super().__init__(
            placeholder=placeholder,
            channel_types=[discord.ChannelType.text],
            min_values=1, max_values=1,
        )
        self.parent = parent
        self.config_key = config_key

    async def callback(self, interaction: Interaction):
        channel = self.values[0]
        config = self.parent.get_config()
        config[self.config_key] = channel.id
        self.parent.save_config(config)
        await interaction.response.send_message(
            f"✅ Set **{self.config_key.replace('_', ' ').title()}** to <#{channel.id}>",
            ephemeral=True,
        )


def _picker_view(component: ui.Item) -> ui.View:
    v = ui.View(timeout=120)
    v.add_item(component)
    return v


class _MinAgeModal(ui.Modal, title="Set Minimum Account Age"):
    days = ui.TextInput(label="Days (0 = no minimum)", placeholder="e.g. 7", required=True, max_length=4)

    def __init__(self, parent: "VerificationConfigView"):
        super().__init__()
        self.parent = parent

    async def on_submit(self, interaction: Interaction):
        try:
            d = int(self.days.value)
            if d < 0 or d > 3650:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message("❌ Enter a whole number between 0 and 3650.", ephemeral=True)
        config = self.parent.get_config()
        config["min_account_age_days"] = d
        self.parent.save_config(config)
        await interaction.response.send_message(f"✅ Minimum account age set to **{d} days**.", ephemeral=True)


class _WelcomeDMModal(ui.Modal, title="Set Verification Welcome DM"):
    message = ui.TextInput(
        label="Welcome DM (use {user} {server})",
        style=discord.TextStyle.paragraph,
        placeholder="Welcome {user} to {server}! Glad to have you.",
        required=True, max_length=1500,
    )

    def __init__(self, parent: "VerificationConfigView"):
        super().__init__()
        self.parent = parent
        existing = parent.get_config().get("welcome_dm", "")
        if existing:
            self.message.default = existing

    async def on_submit(self, interaction: Interaction):
        config = self.parent.get_config()
        config["welcome_dm"] = self.message.value
        self.parent.save_config(config)
        await interaction.response.send_message("✅ Welcome DM updated.", ephemeral=True)


class _ResetLogConfirm(ui.View):
    def __init__(self, parent: "VerificationConfigView"):
        super().__init__(timeout=60)
        self.parent = parent

    @ui.button(label="Yes, wipe the log", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: Interaction, button: ui.Button):
        config = self.parent.get_config()
        config["verification_log"] = []
        self.parent.save_config(config)
        await interaction.response.edit_message(content="🗑️ Verification log cleared.", view=None)

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: Interaction, button: ui.Button):
        await interaction.response.edit_message(content="Cancelled.", view=None)


class _ReverifyAllConfirm(ui.View):
    def __init__(self, parent: "VerificationConfigView"):
        super().__init__(timeout=60)
        self.parent = parent

    @ui.button(label="Yes, force re-verify everyone", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        verified = discord.utils.get(guild.roles, name="Verified")
        unverified = discord.utils.get(guild.roles, name="Unverified")
        if not verified:
            return await interaction.followup.send("❌ Verified role not found.", ephemeral=True)

        affected = 0
        for member in list(verified.members):
            if member.bot:
                continue
            try:
                await member.remove_roles(verified, reason="Re-verify all triggered by admin")
                if unverified and unverified not in member.roles:
                    await member.add_roles(unverified, reason="Re-verify all triggered by admin")
                affected += 1
            except Exception as e:
                logger.warning(f"Re-verify failed for {member}: {e}")
        await interaction.followup.send(f"🔁 Re-verification triggered for **{affected}** members.", ephemeral=True)

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: Interaction, button: ui.Button):
        await interaction.response.edit_message(content="Cancelled.", view=None)


class VerificationConfigView(ConfigPanelView):
    """Full verification admin panel — 12 working buttons per blueprint."""

    def __init__(self, guild_id: int):
        super().__init__(guild_id, "verification")

    def create_embed(self) -> discord.Embed:
        config = self.get_config()
        enabled = config.get("enabled", True)
        channel_id = config.get("channel_id")
        verified_role_id = config.get("verified_role_id") or config.get("role_id")
        unverified_role_id = config.get("unverified_role_id")
        min_age = config.get("min_account_age_days", 0)
        captcha = config.get("captcha_enabled", False)
        phone = config.get("phone_required", False)
        log = config.get("verification_log", [])
        welcome_dm = config.get("welcome_dm", "")

        embed = discord.Embed(
            title="🛡️ Verification System",
            description="Manage server verification. Live config below — every button works.",
            color=discord.Color.green() if enabled else discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Status", value="✅ Enabled" if enabled else "❌ Disabled", inline=True)
        embed.add_field(name="Verify Channel", value=f"<#{channel_id}>" if channel_id else "_Not Set_", inline=True)
        embed.add_field(name="Verified Role", value=f"<@&{verified_role_id}>" if verified_role_id else "_Not Set_", inline=True)
        embed.add_field(name="Unverified Role", value=f"<@&{unverified_role_id}>" if unverified_role_id else "_Not Set_", inline=True)
        embed.add_field(name="Min Account Age", value=f"{min_age} days" if min_age else "No minimum", inline=True)
        embed.add_field(name="CAPTCHA", value="🧮 On" if captcha else "Off", inline=True)
        embed.add_field(name="Phone Gate", value="📱 Required" if phone else "Off", inline=True)
        embed.add_field(name="Total Verified Logged", value=str(len(log)), inline=True)
        embed.add_field(name="Welcome DM", value="✏️ Set" if welcome_dm else "_None_", inline=True)
        embed.set_footer(text=f"Guild ID: {self.guild_id}")
        return embed

    # Row 0
    @ui.button(label="Toggle System", emoji="✅", style=discord.ButtonStyle.success, custom_id="verify_toggle", row=0)
    async def toggle(self, interaction: Interaction, button: ui.Button):
        config = self.get_config()
        config["enabled"] = not config.get("enabled", True)
        self.save_config(config)
        await self.update_panel(interaction)

    @ui.button(label="Set Verified Role", emoji="🔢", style=discord.ButtonStyle.primary, custom_id="verify_setverified", row=0)
    async def set_verified_role(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_message(
            "Pick the **Verified** role:",
            view=_picker_view(_RoleSelectFor(self, "verified_role_id", "Select Verified role…")),
            ephemeral=True,
        )

    @ui.button(label="Set Unverified Role", emoji="🔒", style=discord.ButtonStyle.primary, custom_id="verify_setunverified", row=0)
    async def set_unverified_role(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_message(
            "Pick the **Unverified** role:",
            view=_picker_view(_RoleSelectFor(self, "unverified_role_id", "Select Unverified role…")),
            ephemeral=True,
        )

    @ui.button(label="Set Verify Channel", emoji="📣", style=discord.ButtonStyle.primary, custom_id="verify_setchannel", row=0)
    async def set_channel(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_message(
            "Pick the **#verify** channel:",
            view=_picker_view(_ChannelSelectFor(self, "channel_id", "Select verify channel…")),
            ephemeral=True,
        )

    # Row 1
    @ui.button(label="Min Account Age", emoji="⏱️", style=discord.ButtonStyle.secondary, custom_id="verify_minage", row=1)
    async def set_min_age(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_modal(_MinAgeModal(self))

    @ui.button(label="Toggle CAPTCHA", emoji="🧮", style=discord.ButtonStyle.secondary, custom_id="verify_captcha", row=1)
    async def toggle_captcha(self, interaction: Interaction, button: ui.Button):
        config = self.get_config()
        config["captcha_enabled"] = not config.get("captcha_enabled", False)
        self.save_config(config)
        await self.update_panel(interaction)

    @ui.button(label="Toggle Phone Gate", emoji="📱", style=discord.ButtonStyle.secondary, custom_id="verify_phone", row=1)
    async def toggle_phone(self, interaction: Interaction, button: ui.Button):
        config = self.get_config()
        config["phone_required"] = not config.get("phone_required", False)
        self.save_config(config)
        await self.update_panel(interaction)

    @ui.button(label="Set Welcome DM", emoji="📩", style=discord.ButtonStyle.secondary, custom_id="verify_welcomedm", row=1)
    async def set_welcome_dm(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_modal(_WelcomeDMModal(self))

    # Row 2
    @ui.button(label="View Log", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="verify_viewlog", row=2)
    async def view_log(self, interaction: Interaction, button: ui.Button):
        log = self.get_config().get("verification_log", [])
        recent = log[-20:][::-1]
        if not recent:
            return await interaction.response.send_message("📋 No verifications logged yet.", ephemeral=True)
        lines = []
        for e in recent:
            ts = e.get("ts", 0)
            uid = e.get("user_id", "?")
            method = e.get("method", "button")
            lines.append(f"<t:{int(ts)}:R> — <@{uid}> via `{method}`")
        embed = discord.Embed(
            title=f"📋 Last {len(recent)} Verifications",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, custom_id="verify_stats", row=2)
    async def stats(self, interaction: Interaction, button: ui.Button):
        log = self.get_config().get("verification_log", [])
        now = time.time()
        day = sum(1 for e in log if now - e.get("ts", 0) < 86400)
        week = sum(1 for e in log if now - e.get("ts", 0) < 604800)
        month = sum(1 for e in log if now - e.get("ts", 0) < 2592000)
        embed = discord.Embed(title="📊 Verification Stats", color=discord.Color.blurple())
        embed.add_field(name="Last 24h", value=str(day), inline=True)
        embed.add_field(name="Last 7d", value=str(week), inline=True)
        embed.add_field(name="Last 30d", value=str(month), inline=True)
        embed.add_field(name="All Time", value=str(len(log)), inline=True)
        unverified = discord.utils.get(interaction.guild.roles, name="Unverified")
        if unverified:
            embed.add_field(name="Currently Unverified", value=str(len(unverified.members)), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Reset Log", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="verify_resetlog", row=2)
    async def reset_log(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_message(
            "⚠️ This wipes the verification history. Continue?",
            view=_ResetLogConfirm(self),
            ephemeral=True,
        )

    @ui.button(label="Re-Verify All", emoji="🔁", style=discord.ButtonStyle.danger, custom_id="verify_reverify", row=2)
    async def reverify_all(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_message(
            "⚠️ This **removes Verified** from every member and forces them to verify again. Continue?",
            view=_ReverifyAllConfirm(self),
            ephemeral=True,
        )

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
