import discord
from discord import ui, Interaction, app_commands
import asyncio
import json
import os
import time
import re
from datetime import datetime, timezone, timedelta
from data_manager import dm
from logger import logger
from typing import Dict, Any, List, Optional

def log_panel_action(guild_id: int, user_id: int, action: str):
    """Log an admin panel action to the guild's action logs."""
    logs = dm.get_guild_data(guild_id, "action_logs", [])
    logs.append({
        "ts": time.time(),
        "user_id": user_id,
        "action": action
    })
    dm.update_guild_data(guild_id, "action_logs", logs[-100:])

class ConfigPanelView(ui.View):
    """Base class for all persistent system configuration panels."""
    def __init__(self, guild_id: int, system_name: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.system_name = system_name

    def get_config(self) -> Dict[str, Any]:
        return dm.get_guild_data(self.guild_id, f"{self.system_name}_config", {})

    def save_config(self, config: Dict[str, Any]):
        dm.update_guild_data(self.guild_id, f"{self.system_name}_config", config)
        # Register commands
        from modules.auto_setup import AutoSetup
        bot = getattr(self, "bot", None)
        setup_helper = AutoSetup(bot)
        setup_helper._register_system_commands(self.guild_id, self.system_name)

    async def update_panel(self, interaction: Interaction):
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self) -> discord.Embed:
        return discord.Embed(title=f"Config: {self.system_name.title()}")

# --- Reusable Components ---

class _GenericRoleSelect(ui.RoleSelect):
    def __init__(self, parent: ConfigPanelView, key: str, placeholder: str):
        super().__init__(placeholder=placeholder, min_values=1, max_values=1)
        self.parent = parent
        self.key = key

    async def callback(self, interaction: Interaction):
        config = self.parent.get_config()
        config[self.key] = self.values[0].id
        self.parent.save_config(config)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Set {self.key} to {self.values[0].name}")
        await interaction.response.send_message(f"✅ Set **{self.key.replace('_',' ').title()}** to {self.values[0].mention}", ephemeral=True)

class _GenericChannelSelect(ui.ChannelSelect):
    def __init__(self, parent: ConfigPanelView, key: str, placeholder: str, channel_types=None):
        super().__init__(
            placeholder=placeholder,
            channel_types=channel_types or [discord.ChannelType.text],
            min_values=1, max_values=1,
        )
        self.parent = parent
        self.key = key

    async def callback(self, interaction: Interaction):
        config = self.parent.get_config()
        config[self.key] = self.values[0].id
        self.parent.save_config(config)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Set {self.key} to #{self.values[0].name}")
        await interaction.response.send_message(f"✅ Set **{self.key.replace('_',' ').title()}** to <#{self.values[0].id}>", ephemeral=True)

class _NumberModal(ui.Modal):
    value_input = ui.TextInput(label="Value", required=True, max_length=15)

    def __init__(self, parent: ConfigPanelView, key: str, label: str, min_v: int = 0, max_v: int = 999999999999):
        super().__init__(title=label)
        self.parent = parent
        self.key = key
        self.min_v, self.max_v = min_v, max_v
        self.value_input.label = label
        existing = parent.get_config().get(key)
        if existing is not None:
            self.value_input.default = str(existing)

    async def on_submit(self, interaction: Interaction):
        try:
            v = int(self.value_input.value)
            if v < self.min_v or v > self.max_v:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(f"❌ Enter a valid number.", ephemeral=True)
        config = self.parent.get_config()
        config[self.key] = v
        self.parent.save_config(config)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Set {self.key} to {v}")
        await interaction.response.send_message(f"✅ {self.key.replace('_',' ').title()} set to **{v}**.", ephemeral=True)

class _TextModal(ui.Modal):
    value_input = ui.TextInput(label="Value", style=discord.TextStyle.paragraph, required=True, max_length=1500)

    def __init__(self, parent: ConfigPanelView, key: str, label: str):
        super().__init__(title=label)
        self.parent = parent
        self.key = key
        self.value_input.label = label
        existing = parent.get_config().get(key, "")
        if existing:
            self.value_input.default = str(existing)

    async def on_submit(self, interaction: Interaction):
        config = self.parent.get_config()
        config[self.key] = self.value_input.value
        self.parent.save_config(config)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Updated text field {self.key}")
        await interaction.response.send_message(f"✅ {self.key.replace('_',' ').title()} updated.", ephemeral=True)

def _picker_view(component: ui.Item) -> ui.View:
    v = ui.View(timeout=120)
    v.add_item(component)
    return v

# --- Specialized Panels ---

class VerificationConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "verification")

    def create_embed(self) -> discord.Embed:
        c = self.get_config()
        embed = discord.Embed(title="🛡️ Verification System", color=discord.Color.green() if c.get("enabled", True) else discord.Color.red())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Verified Role", value=f"<@&{c.get('verified_role_id')}>" if c.get('verified_role_id') else "_None_", inline=True)
        embed.add_field(name="Unverified Role", value=f"<@&{c.get('unverified_role_id')}>" if c.get('unverified_role_id') else "_None_", inline=True)
        embed.add_field(name="Channel", value=f"<#{c.get('channel_id')}>" if c.get('channel_id') else "_None_", inline=True)
        embed.add_field(name="CAPTCHA", value="🧮 On" if c.get("captcha_enabled") else "Off", inline=True)
        embed.add_field(name="Min Age", value=f"{c.get('min_account_age_days', 0)}d", inline=True)
        embed.add_field(name="Phone", value="📱 Required" if c.get("phone_required") else "Off", inline=True)
        embed.add_field(name="Log Count", value=str(len(c.get("verification_log", []))), inline=True)
        return embed

    @ui.button(label="Toggle System", emoji="✅", style=discord.ButtonStyle.success, row=0)
    async def toggle(self, i, b):
        c = self.get_config(); c["enabled"] = not c.get("enabled", True); self.save_config(c)
        log_panel_action(i.guild_id, i.user.id, f"Toggled verification to {c['enabled']}")
        await self.update_panel(i)

    @ui.button(label="Set Verified Role", emoji="🔢", style=discord.ButtonStyle.primary, row=0)
    async def set_v(self, i, b):
        await i.response.send_message("Select Role:", view=_picker_view(_GenericRoleSelect(self, "verified_role_id", "Verified Role")), ephemeral=True)

    @ui.button(label="Set Unverified Role", emoji="🔒", style=discord.ButtonStyle.primary, row=0)
    async def set_uv(self, i, b):
        await i.response.send_message("Select Role:", view=_picker_view(_GenericRoleSelect(self, "unverified_role_id", "Unverified Role")), ephemeral=True)

    @ui.button(label="Set Verify Channel", emoji="📣", style=discord.ButtonStyle.primary, row=0)
    async def set_ch(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "channel_id", "Verify Channel")), ephemeral=True)

    @ui.button(label="Min Account Age", emoji="⏱️", style=discord.ButtonStyle.secondary, row=1)
    async def set_age(self, i, b):
        await i.response.send_modal(_NumberModal(self, "min_account_age_days", "Min Age (Days)"))

    @ui.button(label="Toggle CAPTCHA", emoji="🧮", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_c(self, i, b):
        c = self.get_config(); c["captcha_enabled"] = not c.get("captcha_enabled", False); self.save_config(c); await self.update_panel(i)

    @ui.button(label="Toggle Phone Gate", emoji="📱", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_p(self, i, b):
        c = self.get_config(); c["phone_required"] = not c.get("phone_required", False); self.save_config(c); await self.update_panel(i)

    @ui.button(label="Set Welcome DM", emoji="📩", style=discord.ButtonStyle.secondary, row=1)
    async def set_dm(self, i, b):
        await i.response.send_modal(_TextModal(self, "welcome_dm", "Welcome DM Message"))

    @ui.button(label="View Log", emoji="📋", style=discord.ButtonStyle.secondary, row=2)
    async def view_log(self, i, b):
        log = self.get_config().get("verification_log", [])[-20:][::-1]
        msg = "\n".join([f"<t:{int(e['ts'])}:R> <@{e['user_id']}> ({e['method']})" for e in log]) or "No logs."
        await i.response.send_message(embed=discord.Embed(title="Verification Log", description=msg), ephemeral=True)

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2)
    async def stats(self, i, b):
        log = self.get_config().get("verification_log", [])
        await i.response.send_message(f"📊 Total Verifications: {len(log)}", ephemeral=True)

    @ui.button(label="Reset Log", emoji="🗑️", style=discord.ButtonStyle.danger, row=2)
    async def reset(self, i, b):
        c = self.get_config(); c["verification_log"] = []; self.save_config(c)
        log_panel_action(i.guild_id, i.user.id, "Reset verification log")
        await i.response.send_message("Log Reset", ephemeral=True)

    @ui.button(label="Re-verify All", emoji="🔁", style=discord.ButtonStyle.danger, row=2)
    async def reverify(self, i: Interaction, b):
        await i.response.defer(ephemeral=True)
        uv_role, v_role = None, None
        config = self.get_config()
        uv_id = config.get("unverified_role_id")
        v_id = config.get("verified_role_id")
        if uv_id: uv_role = i.guild.get_role(uv_id)
        if v_id: v_role = i.guild.get_role(v_id)
        if not v_role: return await i.followup.send("❌ Verified role not set.")
        count = 0
        for member in v_role.members:
            try:
                await member.remove_roles(v_role)
                if uv_role: await member.add_roles(uv_role)
                count += 1
            except: pass
        log_panel_action(i.guild_id, i.user.id, f"Triggered re-verification for {count} members")
        await i.followup.send(f"✅ Re-verification triggered for {count} members.")

class AntiRaidConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "antiraid")

    def create_embed(self) -> discord.Embed:
        c = self.get_config()
        embed = discord.Embed(title="🛡️ Anti-Raid System", color=discord.Color.red() if c.get("enabled", True) else discord.Color.greyple())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Join Threshold", value=f"{c.get('mass_join_threshold', 10)}/{c.get('mass_join_window', 10)}s", inline=True)
        embed.add_field(name="Action", value=c.get("action", "lockdown").upper(), inline=True)
        embed.add_field(name="Filters", value=f"Age: {'ON' if c.get('age_filter_enabled') else 'OFF'} | Link: {'ON' if c.get('link_spam_enabled') else 'OFF'} | Inv: {'ON' if c.get('invite_filter_enabled') else 'OFF'}", inline=False)
        embed.add_field(name="Spam", value=f"Ment: {c.get('mention_threshold',5)} | Dup: {c.get('duplicate_threshold',3)}", inline=True)
        embed.add_field(name="Whitelist", value=f"{len(c.get('whitelist', []))} users", inline=True)
        return embed

    @ui.button(label="Toggle Anti-Raid", emoji="🛡️", style=discord.ButtonStyle.success, row=0)
    async def toggle(self, i, b):
        c = self.get_config(); c["enabled"] = not c.get("enabled", True); self.save_config(c); await self.update_panel(i)

    @ui.button(label="Set Join Threshold", emoji="👥", style=discord.ButtonStyle.primary, row=0)
    async def set_thresh(self, i, b):
        await i.response.send_modal(_NumberModal(self, "mass_join_threshold", "Joins (X)"))

    @ui.button(label="Set Trigger Action", emoji="⚡", style=discord.ButtonStyle.primary, row=0)
    async def set_action(self, i, b):
        view = ui.View()
        select = ui.Select(placeholder="Select Action", options=[
            discord.SelectOption(label="Lockdown", value="lockdown"),
            discord.SelectOption(label="Kick", value="kick"),
            discord.SelectOption(label="Ban", value="ban"),
            discord.SelectOption(label="Mute", value="mute")
        ])
        async def callback(it):
            c = self.get_config(); c["action"] = select.values[0]; self.save_config(c); await it.response.send_message(f"Action set to {c['action']}", ephemeral=True)
        select.callback = callback
        view.add_item(select)
        await i.response.send_message("Choose action:", view=view, ephemeral=True)

    @ui.button(label="View Raid Log", emoji="📋", style=discord.ButtonStyle.secondary, row=1)
    async def view_log(self, i, b):
        log = self.get_config().get("raid_log", [])[-10:][::-1]
        msg = "\n".join([f"<t:{int(e['ts'])}:R> {e['type']} -> {e['action']}" for e in log]) or "No raids."
        await i.response.send_message(embed=discord.Embed(title="Raid Log", description=msg), ephemeral=True)

    @ui.button(label="Whitelist User", emoji="✅", style=discord.ButtonStyle.secondary, row=1)
    async def whitelist(self, i, b):
        await i.response.send_modal(_NumberModal(self, "whitelist_add", "Add User ID to Whitelist"))

    @ui.button(label="View Whitelist", emoji="📜", style=discord.ButtonStyle.secondary, row=1)
    async def v_white(self, i, b):
        w = self.get_config().get("whitelist", [])
        await i.response.send_message(f"Whitelist: {', '.join([str(u) for u in w]) or 'Empty'}", ephemeral=True)

    @ui.button(label="Manual Lockdown", emoji="🔒", style=discord.ButtonStyle.danger, row=2)
    async def lockdown(self, i: Interaction, b):
        from modules.anti_raid import AntiRaidSystem
        ar = AntiRaidSystem(i.client)
        await ar._lockdown(i.guild)
        log_panel_action(i.guild_id, i.user.id, "Manual Lockdown")
        await i.response.send_message("🔒 Locked Down.", ephemeral=True)

    @ui.button(label="Unlock Server", emoji="🔓", style=discord.ButtonStyle.success, row=2)
    async def unlock(self, i: Interaction, b):
        from modules.anti_raid import AntiRaidSystem
        ar = AntiRaidSystem(i.client)
        await ar.lift_lockdown(i.guild)
        log_panel_action(i.guild_id, i.user.id, "Lifted Lockdown")
        await i.response.send_message("🔓 Unlocked.", ephemeral=True)

    @ui.button(label="Set Min Age", emoji="👶", style=discord.ButtonStyle.secondary, row=2)
    async def set_age(self, i, b):
        await i.response.send_modal(_NumberModal(self, "min_account_age_days", "Min Age (Days)"))

    @ui.button(label="Toggle Link Filter", emoji="🔗", style=discord.ButtonStyle.secondary, row=3)
    async def t_link(self, i, b):
        c = self.get_config(); c["link_spam_enabled"] = not c.get("link_spam_enabled", True); self.save_config(c); await self.update_panel(i)

    @ui.button(label="Toggle Mention Filter", emoji="📣", style=discord.ButtonStyle.secondary, row=3)
    async def s_ment(self, i, b):
        c = self.get_config(); c["mention_filter_enabled"] = not c.get("mention_filter_enabled", True); self.save_config(c); await self.update_panel(i)

    @ui.button(label="Toggle Duplicate Filter", emoji="💬", style=discord.ButtonStyle.secondary, row=3)
    async def s_dup(self, i, b):
        c = self.get_config(); c["duplicate_filter_enabled"] = not c.get("duplicate_filter_enabled", True); self.save_config(c); await self.update_panel(i)

    @ui.button(label="Toggle Invite Filter", emoji="🌐", style=discord.ButtonStyle.secondary, row=4)
    async def t_inv(self, i, b):
        c = self.get_config(); c["invite_filter_enabled"] = not c.get("invite_filter_enabled", True); self.save_config(c); await self.update_panel(i)

    @ui.button(label="Raid Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=4)
    async def r_stats(self, i, b):
        log = self.get_config().get("raid_log", [])
        await i.response.send_message(f"Total Raids: {len(log)}", ephemeral=True)

    @ui.button(label="Silence Raid Alerts", emoji="🔕", style=discord.ButtonStyle.secondary, row=4)
    async def silence(self, i, b):
        await i.response.send_message("🔕 Notifications Silenced (Simulated).", ephemeral=True)

class GuardianConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "guardian")

    def create_embed(self) -> discord.Embed:
        c = self.get_config()
        embed = discord.Embed(title="🛡️ Guardian System", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Response Levels", value=f"Tox: {c.get('toxicity_level','WARN')} | Scam: {c.get('scam_level','MUTE')} | Nuke: {c.get('nuke_level','BAN')}", inline=False)
        embed.add_field(name="Alert Channel", value=f"<#{c.get('alert_channel')}>" if c.get('alert_channel') else "_None_", inline=True)
        embed.add_field(name="Mass DM", value=f"{c.get('mass_dm_threshold',10)}/min", inline=True)
        return embed

    @ui.button(label="Toggle Guardian", emoji="⚔️", style=discord.ButtonStyle.success, row=0)
    async def toggle(self, i, b):
        c = self.get_config(); c["enabled"] = not c.get("enabled", True); self.save_config(c); await self.update_panel(i)

    @ui.button(label="Toxicity Filter", emoji="☣️", style=discord.ButtonStyle.primary, row=0)
    async def set_tox(self, i, b):
        await self._set_level(i, "toxicity_level")

    @ui.button(label="Scam Filter", emoji="🔗", style=discord.ButtonStyle.primary, row=0)
    async def set_scam(self, i, b):
        await self._set_level(i, "scam_level")

    @ui.button(label="Impersonation Det.", emoji="🎭", style=discord.ButtonStyle.primary, row=0)
    async def set_imp(self, i, b):
        await self._set_level(i, "impersonation_level")

    async def _set_level(self, i, key):
        view = ui.View()
        select = ui.Select(placeholder="Select Level", options=[
            discord.SelectOption(label="Off", value="OFF"),
            discord.SelectOption(label="Warn", value="WARN"),
            discord.SelectOption(label="Mute", value="MUTE"),
            discord.SelectOption(label="Kick", value="KICK"),
            discord.SelectOption(label="Ban", value="BAN")
        ])
        async def callback(it):
            c = self.get_config(); c[key] = select.values[0]; self.save_config(c); await it.response.send_message(f"Set to {c[key]}", ephemeral=True)
        select.callback = callback; view.add_item(select); await i.response.send_message("Choose Level:", view=view, ephemeral=True)

    @ui.button(label="Mass DM Detection", emoji="📨", style=discord.ButtonStyle.secondary, row=1)
    async def set_dm(self, i, b):
        await i.response.send_modal(_NumberModal(self, "mass_dm_threshold", "Messages per Minute"))

    @ui.button(label="Nuke Protection", emoji="💣", style=discord.ButtonStyle.secondary, row=1)
    async def set_nuke(self, i, b):
        await self._set_level(i, "nuke_level")

    @ui.button(label="Bot Token Detection", emoji="🤖", style=discord.ButtonStyle.secondary, row=1)
    async def set_token(self, i, b):
        c = self.get_config(); c["token_detection"] = not c.get("token_detection", True); self.save_config(c); await self.update_panel(i)

    @ui.button(label="Malware Detection", emoji="📎", style=discord.ButtonStyle.secondary, row=2)
    async def set_mal(self, i, b):
        await self._set_level(i, "malware_level")

    @ui.button(label="Self-Bot Detection", emoji="⚡", style=discord.ButtonStyle.secondary, row=2)
    async def set_self(self, i, b):
        await self._set_level(i, "selfbot_level")

    @ui.button(label="Guardian Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2)
    async def g_stats(self, i, b):
        log = self.get_config().get("guardian_log", [])
        await i.response.send_message(f"Incidents: {len(log)}", ephemeral=True)

    @ui.button(label="View Guardian Log", emoji="📋", style=discord.ButtonStyle.secondary, row=3)
    async def view_log(self, i, b):
        log = self.get_config().get("guardian_log", [])[-15:][::-1]
        msg = "\n".join([f"<t:{int(e['ts'])}:R> {e['type']} - {e['action']}" for e in log]) or "No incidents."
        await i.response.send_message(embed=discord.Embed(title="Guardian Log", description=msg), ephemeral=True)

    @ui.button(label="Whitelist User", emoji="🔕", style=discord.ButtonStyle.secondary, row=3)
    async def white(self, i, b):
        await i.response.send_modal(_NumberModal(self, "whitelist_add", "User ID"))

    @ui.button(label="View Whitelist", emoji="📜", style=discord.ButtonStyle.secondary, row=3)
    async def v_white(self, i, b):
        w = self.get_config().get("whitelist", [])
        await i.response.send_message(f"Whitelisted: {', '.join([str(u) for u in w]) or 'None'}", ephemeral=True)

    @ui.button(label="Configure Responses", emoji="🔧", style=discord.ButtonStyle.secondary, row=4)
    async def conf_resp(self, i, b):
        await i.response.send_message("Use individual toggle buttons to set levels.", ephemeral=True)

    @ui.button(label="Test Guardian", emoji="🧪", style=discord.ButtonStyle.success, row=4)
    async def test(self, i, b):
        log_panel_action(i.guild_id, i.user.id, "Test Guardian Alert")
        await i.response.send_message("🧪 Test alert sent to logs.", ephemeral=True)

    @ui.button(label="Set Alert Channel", emoji="📣", style=discord.ButtonStyle.primary, row=4)
    async def set_ch(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "alert_channel", "Alert Channel")), ephemeral=True)

    @ui.button(label="Reset All Rules", emoji="🔄", style=discord.ButtonStyle.danger, row=5)
    async def reset(self, i, b):
        c = self.get_config(); c.clear(); self.save_config(c); await i.response.send_message("Rules Reset.", ephemeral=True)

class TicketsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "tickets")

    def create_embed(self) -> discord.Embed:
        c = self.get_config()
        embed = discord.Embed(title="🎫 Ticket System", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Category", value=f"<#{c.get('category_id')}>" if c.get('category_id') else "_None_", inline=True)
        embed.add_field(name="Staff Role", value=f"<@&{c.get('staff_role_id')}>" if c.get('staff_role_id') else "_None_", inline=True)
        return embed

    @ui.button(label="Toggle System", style=discord.ButtonStyle.success, row=0)
    async def toggle(self, i, b):
        c = self.get_config(); c["enabled"] = not c.get("enabled", True); self.save_config(c); await self.update_panel(i)

    @ui.button(label="Set Category", style=discord.ButtonStyle.primary, row=0)
    async def set_cat(self, i, b):
        await i.response.send_message("Select Category:", view=_picker_view(_GenericChannelSelect(self, "category_id", "Ticket Category", [discord.ChannelType.category])), ephemeral=True)

    @ui.button(label="Set Staff Role", style=discord.ButtonStyle.primary, row=0)
    async def set_staff(self, i, b):
        await i.response.send_message("Select Staff Role:", view=_picker_view(_GenericRoleSelect(self, "staff_role_id", "Staff Role")), ephemeral=True)

    @ui.button(label="Send Public Panel", style=discord.ButtonStyle.success, row=1)
    async def send_panel(self, i, b):
        embed = discord.Embed(title="🎫 Open a Ticket", description="Click below to speak with staff.", color=discord.Color.green())
        await i.channel.send(embed=embed, view=TicketOpenButton())
        await i.response.send_message("Panel Sent.", ephemeral=True)

class TicketOpenButton(ui.View):
    def __init__(self): super().__init__(timeout=None)
    @ui.button(label="🎫 Open Ticket", style=discord.ButtonStyle.success, custom_id="ticket_open_v2")
    async def open(self, i, b): await i.response.send_message("Opening ticket...", ephemeral=True)

class TicketCloseButton(ui.View):
    def __init__(self): super().__init__(timeout=None)
    @ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_close_v2")
    async def close(self, i, b): await i.response.send_message("Closing...", ephemeral=False)

# --- Registry ---

SPECIALIZED_VIEWS = {
    "verification": VerificationConfigView,
    "antiraid": AntiRaidConfigView,
    "guardian": GuardianConfigView,
    "tickets": TicketsConfigView,
}

def get_config_panel(guild_id: int, system: str) -> Optional[ui.View]:
    system_key = system.lower().replace("_", "")
    if system_key in SPECIALIZED_VIEWS: return SPECIALIZED_VIEWS[system_key](guild_id)
    return None

async def handle_config_panel_command(message: discord.Message, system: str):
    view = get_config_panel(message.guild.id, system)
    if not view: return await message.channel.send(f"❌ System '{system}' not found.")
    await message.channel.send(embed=view.create_embed(), view=view)

def register_all_persistent_views(bot: discord.Client):
    bot.add_view(VerificationConfigView(0))
    bot.add_view(AntiRaidConfigView(0))
    bot.add_view(GuardianConfigView(0))
    bot.add_view(TicketsConfigView(0))
    bot.add_view(TicketOpenButton())
    bot.add_view(TicketCloseButton())
    logger.info("Config panels registered.")
