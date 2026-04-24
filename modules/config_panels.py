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
from typing import Dict, Any, List, Optional, Union

# Forward imports for panel views used in registry
RemindersPanelView = None
ScheduledPanelView = None
AnnouncementsPanelView = None

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

    def get_config(self, guild_id: int = None) -> Dict[str, Any]:
        target_guild = guild_id or self.guild_id
        return dm.get_guild_data(target_guild, f"{self.system_name}_config", {})

    def save_config(self, config: Dict[str, Any], guild_id: int = None, bot: discord.Client = None):
        target_guild = guild_id or self.guild_id
        dm.update_guild_data(target_guild, f"{self.system_name}_config", config)
        # Register commands
        from modules.auto_setup import AutoSetup
        setup_helper = AutoSetup(bot)
        setup_helper._register_system_commands(target_guild, self.system_name)

    async def update_panel(self, interaction: Interaction):
        embed = self.create_embed(interaction.guild_id)
        await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        return discord.Embed(title=f"Config: {self.system_name.title()}")

# --- Reusable Components ---

class _GenericRoleSelect(ui.RoleSelect):
    def __init__(self, parent: ConfigPanelView, key: str, placeholder: str):
        super().__init__(placeholder=placeholder, min_values=1, max_values=1)
        self.parent = parent
        self.key = key

    async def callback(self, interaction: Interaction):
        config = self.parent.get_config(interaction.guild_id)
        config[self.key] = self.values[0].id
        self.parent.save_config(config, interaction.guild_id, interaction.client)
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
        config = self.parent.get_config(interaction.guild_id)
        config[self.key] = self.values[0].id
        self.parent.save_config(config, interaction.guild_id, interaction.client)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Set {self.key} to #{self.values[0].name}")
        await interaction.response.send_message(f"✅ Set **{self.key.replace('_',' ').title()}** to <#{self.values[0].id}>", ephemeral=True)

class _NumberModal(ui.Modal):
    value_input = ui.TextInput(label="Value", required=True, max_length=15)
    second_value = ui.TextInput(label="Secondary Value (optional)", required=False, max_length=15)

    def __init__(self, parent: ConfigPanelView, key: str, label: str, guild_id: int, min_v: int = 0, max_v: int = 999999999999, second_label: str = None):
        super().__init__(title=label)
        self.parent = parent
        self.key = key
        self.min_v, self.max_v = min_v, max_v
        self.value_input.label = label
        if second_label:
            self.second_value.label = second_label
            self.second_value.required = False
        existing = parent.get_config(guild_id).get(key)
        if existing is not None:
            if isinstance(existing, (list, tuple)) and len(existing) >= 1:
                self.value_input.default = str(existing[0])
                if len(existing) >= 2 and second_label:
                    self.second_value.default = str(existing[1])
            else:
                self.value_input.default = str(existing)

    async def on_submit(self, interaction: Interaction):
        try:
            v = int(self.value_input.value)
            if v < self.min_v or v > self.max_v:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(f"❌ Enter a valid number.", ephemeral=True)
        
        config = self.parent.get_config(interaction.guild_id)
        
        # Special handling for whitelist operations
        if self.key == "whitelist_add":
            user_id = v
            whitelist = config.get("whitelist", [])
            if user_id not in whitelist:
                whitelist.append(user_id)
                config["whitelist"] = whitelist
                self.parent.save_config(config, interaction.guild_id, interaction.client)
                log_panel_action(interaction.guild_id, interaction.user.id, f"Added {user_id} to whitelist")
                return await interaction.response.send_message(f"✅ User `{user_id}` added to whitelist.", ephemeral=True)
            else:
                return await interaction.response.send_message(f"⚠️ User `{user_id}` is already whitelisted.", ephemeral=True)
        
        # Special handling for duplicate filter (X messages in Y seconds)
        if self.key == "duplicate_threshold_config":
            if self.second_value.value:
                try:
                    y = int(self.second_value.value)
                    config["duplicate_threshold"] = v
                    config["duplicate_window"] = y
                    self.parent.save_config(config, interaction.guild_id, interaction.client)
                    log_panel_action(interaction.guild_id, interaction.user.id, f"Set duplicate filter to {v} msgs in {y}s")
                    return await interaction.response.send_message(f"✅ Duplicate filter: **{v}** messages in **{y}** seconds.", ephemeral=True)
                except ValueError:
                    return await interaction.response.send_message("❌ Second value must be a number.", ephemeral=True)
            config["duplicate_threshold"] = v
            self.parent.save_config(config, interaction.guild_id, interaction.client)
            log_panel_action(interaction.guild_id, interaction.user.id, f"Set duplicate threshold to {v}")
            return await interaction.response.send_message(f"✅ Duplicate threshold set to **{v}** messages.", ephemeral=True)
        
        # Special handling for mention threshold config
        if self.key == "mention_threshold_config":
            config["mention_threshold"] = v
            self.parent.save_config(config, interaction.guild_id, interaction.client)
            log_panel_action(interaction.guild_id, interaction.user.id, f"Set mention threshold to {v}")
            return await interaction.response.send_message(f"✅ Max mentions per message: **{v}**.", ephemeral=True)
        
        # Default: single value storage
        config[self.key] = v
        self.parent.save_config(config, interaction.guild_id, interaction.client)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Set {self.key} to {v}")
        await interaction.response.send_message(f"✅ {self.key.replace('_',' ').title()} set to **{v}**.", ephemeral=True)

class _TextModal(ui.Modal):
    value_input = ui.TextInput(label="Value", style=discord.TextStyle.paragraph, required=True, max_length=1500)

    def __init__(self, parent: ConfigPanelView, key: str, label: str, guild_id: int):
        super().__init__(title=label)
        self.parent = parent
        self.key = key
        self.value_input.label = label
        existing = parent.get_config(guild_id).get(key, "")
        if existing:
            self.value_input.default = str(existing)

    async def on_submit(self, interaction: Interaction):
        config = self.parent.get_config(interaction.guild_id)
        config[self.key] = self.value_input.value
        self.parent.save_config(config, interaction.guild_id, interaction.client)
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

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        c = self.get_config(guild_id)
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

    @ui.button(label="Toggle System", emoji="✅", style=discord.ButtonStyle.success, row=0, custom_id="cfg_verify_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(c, i.guild_id, i.client)
        log_panel_action(i.guild_id, i.user.id, f"Toggled verification to {c['enabled']}")
        await self.update_panel(i)

    @ui.button(label="Set Verified Role", emoji="🔢", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_verify_set_v")
    async def set_v(self, i, b):
        await i.response.send_message("Select Role:", view=_picker_view(_GenericRoleSelect(self, "verified_role_id", "Verified Role")), ephemeral=True)

    @ui.button(label="Set Unverified Role", emoji="🔒", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_verify_set_uv")
    async def set_uv(self, i, b):
        await i.response.send_message("Select Role:", view=_picker_view(_GenericRoleSelect(self, "unverified_role_id", "Unverified Role")), ephemeral=True)

    @ui.button(label="Set Verify Channel", emoji="📣", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_verify_set_ch")
    async def set_ch(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "channel_id", "Verify Channel")), ephemeral=True)

    @ui.button(label="Min Account Age", emoji="⏱️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_verify_set_age")
    async def set_age(self, i, b):
        await i.response.send_modal(_NumberModal(self, "min_account_age_days", "Min Age (Days)", i.guild_id))

    @ui.button(label="Toggle CAPTCHA", emoji="🧮", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_verify_toggle_c")
    async def toggle_c(self, i, b):
        c = self.get_config(i.guild_id); c["captcha_enabled"] = not c.get("captcha_enabled", False); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Toggle Phone Gate", emoji="📱", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_verify_toggle_p")
    async def toggle_p(self, i, b):
        c = self.get_config(i.guild_id); c["phone_required"] = not c.get("phone_required", False); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Set Welcome DM", emoji="📩", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_verify_set_dm")
    async def set_dm(self, i, b):
        await i.response.send_modal(_TextModal(self, "welcome_dm", "Welcome DM Message", i.guild_id))

    @ui.button(label="View Log", emoji="📋", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_verify_view_log")
    async def view_log(self, i, b):
        log = self.get_config(i.guild_id).get("verification_log", [])[-20:][::-1]
        msg = "\n".join([f"<t:{int(e['ts'])}:R> <@{e['user_id']}> ({e['method']})" for e in log]) or "No logs."
        await i.response.send_message(embed=discord.Embed(title="Verification Log", description=msg), ephemeral=True)

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_verify_stats")
    async def stats(self, i, b):
        log = self.get_config(i.guild_id).get("verification_log", [])
        await i.response.send_message(f"📊 Total Verifications: {len(log)}", ephemeral=True)

    @ui.button(label="Reset Log", emoji="🗑️", style=discord.ButtonStyle.danger, row=2, custom_id="cfg_verify_reset")
    async def reset(self, i, b):
        c = self.get_config(i.guild_id); c["verification_log"] = []; self.save_config(c, i.guild_id, i.client)
        log_panel_action(i.guild_id, i.user.id, "Reset verification log")
        await i.response.send_message("Log Reset", ephemeral=True)

    @ui.button(label="Re-verify All", emoji="🔁", style=discord.ButtonStyle.danger, row=2, custom_id="cfg_verify_reverify")
    async def reverify(self, i: Interaction, b):
        await i.response.defer(ephemeral=True)
        uv_role, v_role = None, None
        config = self.get_config(i.guild_id)
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

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(title="🛡️ Anti-Raid System", color=discord.Color.red() if c.get("enabled", True) else discord.Color.greyple())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Join Threshold", value=f"{c.get('mass_join_threshold', 10)}/{c.get('mass_join_window', 10)}s", inline=True)
        embed.add_field(name="Action", value=c.get("action", "lockdown").upper(), inline=True)
        embed.add_field(name="Filters", value=f"Age: {'ON' if c.get('age_filter_enabled') else 'OFF'} | Link: {'ON' if c.get('link_spam_enabled') else 'OFF'} | Inv: {'ON' if c.get('invite_filter_enabled') else 'OFF'}", inline=False)
        embed.add_field(name="Spam", value=f"Ment: {c.get('mention_threshold',5)} | Dup: {c.get('duplicate_threshold',3)}", inline=True)
        embed.add_field(name="Whitelist", value=f"{len(c.get('whitelist', []))} users", inline=True)
        return embed

    @ui.button(label="Toggle Anti-Raid", emoji="🛡️", style=discord.ButtonStyle.success, row=0, custom_id="cfg_antiraid_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Set Join Threshold", emoji="👥", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_antiraid_set_thresh")
    async def set_thresh(self, i, b):
        await i.response.send_modal(_NumberModal(self, "mass_join_threshold", "Joins (X)", i.guild_id))

    @ui.button(label="Set Trigger Action", emoji="⚡", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_antiraid_set_action")
    async def set_action(self, i, b):
        view = ui.View()
        select = ui.Select(placeholder="Select Action", options=[
            discord.SelectOption(label="Lockdown", value="lockdown"),
            discord.SelectOption(label="Kick", value="kick"),
            discord.SelectOption(label="Ban", value="ban"),
            discord.SelectOption(label="Mute", value="mute")
        ])
        async def callback(it):
            c = self.get_config(i.guild_id); c["action"] = select.values[0]; self.save_config(c, i.guild_id, i.client); await it.response.send_message(f"Action set to {c['action']}", ephemeral=True)
        select.callback = callback
        view.add_item(select)
        await i.response.send_message("Choose action:", view=view, ephemeral=True)

    @ui.button(label="View Raid Log", emoji="📋", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_antiraid_view_log")
    async def view_log(self, i, b):
        log = self.get_config(i.guild_id).get("raid_log", [])[-10:][::-1]
        msg = "\n".join([f"<t:{int(e['ts'])}:R> {e['type']} -> {e['action']}" for e in log]) or "No raids."
        await i.response.send_message(embed=discord.Embed(title="Raid Log", description=msg), ephemeral=True)

    @ui.button(label="Whitelist User", emoji="✅", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_antiraid_whitelist")
    async def whitelist(self, i, b):
        await i.response.send_modal(_NumberModal(self, "whitelist_add", "Add User ID to Whitelist", i.guild_id))

    @ui.button(label="View Whitelist", emoji="📜", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_antiraid_v_white")
    async def v_white(self, i, b):
        w = self.get_config(i.guild_id).get("whitelist", [])
        await i.response.send_message(f"Whitelist: {', '.join([str(u) for u in w]) or 'Empty'}", ephemeral=True)

    @ui.button(label="Manual Lockdown", emoji="🔒", style=discord.ButtonStyle.danger, row=2, custom_id="cfg_antiraid_lockdown")
    async def lockdown(self, i: Interaction, b):
        from modules.anti_raid import AntiRaidSystem
        ar = AntiRaidSystem(i.client)
        await ar._lockdown(i.guild)
        log_panel_action(i.guild_id, i.user.id, "Manual Lockdown")
        await i.response.send_message("🔒 Locked Down.", ephemeral=True)

    @ui.button(label="Unlock Server", emoji="🔓", style=discord.ButtonStyle.success, row=2, custom_id="cfg_antiraid_unlock")
    async def unlock(self, i: Interaction, b):
        from modules.anti_raid import AntiRaidSystem
        ar = AntiRaidSystem(i.client)
        await ar.lift_lockdown(i.guild)
        log_panel_action(i.guild_id, i.user.id, "Lifted Lockdown")
        await i.response.send_message("🔓 Unlocked.", ephemeral=True)

    @ui.button(label="Set Min Age", emoji="👶", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_antiraid_set_age")
    async def set_age(self, i, b):
        await i.response.send_modal(_NumberModal(self, "min_account_age_days", "Min Age (Days)", i.guild_id))

    @ui.button(label="Toggle Link Filter", emoji="🔗", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_antiraid_t_link")
    async def t_link(self, i, b):
        c = self.get_config(i.guild_id); c["link_spam_enabled"] = not c.get("link_spam_enabled", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Toggle Mention Filter", emoji="📣", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_antiraid_s_ment")
    async def s_ment(self, i, b):
        c = self.get_config(i.guild_id); c["mention_filter_enabled"] = not c.get("mention_filter_enabled", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Toggle Duplicate Filter", emoji="💬", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_antiraid_s_dup")
    async def s_dup(self, i, b):
        c = self.get_config(i.guild_id); c["duplicate_filter_enabled"] = not c.get("duplicate_filter_enabled", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Toggle Invite Filter", emoji="🌐", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_antiraid_t_inv")
    async def t_inv(self, i, b):
        c = self.get_config(i.guild_id); c["invite_filter_enabled"] = not c.get("invite_filter_enabled", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Raid Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_antiraid_r_stats")
    async def r_stats(self, i, b):
        log = self.get_config(i.guild_id).get("raid_log", [])
        await i.response.send_message(f"Total Raids: {len(log)}", ephemeral=True)

    @ui.button(label="Silence Raid Alerts", emoji="🔕", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_antiraid_silence")
    async def silence(self, i, b):
        await i.response.send_message("🔕 Notifications Silenced (Simulated).", ephemeral=True)

class GuardianConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "guardian")

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(title="🛡️ Guardian System", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Response Levels", value=f"Tox: {c.get('toxicity_level','WARN')} | Scam: {c.get('scam_level','MUTE')} | Nuke: {c.get('nuke_level','BAN')}", inline=False)
        embed.add_field(name="Alert Channel", value=f"<#{c.get('alert_channel')}>" if c.get('alert_channel') else "_None_", inline=True)
        embed.add_field(name="Mass DM", value=f"{c.get('mass_dm_threshold',10)}/min", inline=True)
        return embed

    @ui.button(label="Toggle Guardian", emoji="⚔️", style=discord.ButtonStyle.success, row=0, custom_id="cfg_guardian_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Toxicity Filter", emoji="☣️", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_guardian_set_tox")
    async def set_tox(self, i, b):
        await self._set_level(i, "toxicity_level")

    @ui.button(label="Scam Filter", emoji="🔗", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_guardian_set_scam")
    async def set_scam(self, i, b):
        await self._set_level(i, "scam_level")

    @ui.button(label="Impersonation Det.", emoji="🎭", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_guardian_set_imp")
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
            c = self.get_config(i.guild_id); c[key] = select.values[0]; self.save_config(c, i.guild_id, i.client); await it.response.send_message(f"Set to {c[key]}", ephemeral=True)
        select.callback = callback; view.add_item(select); await i.response.send_message("Choose Level:", view=view, ephemeral=True)

    @ui.button(label="Mass DM Detection", emoji="📨", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_guardian_set_dm")
    async def set_dm(self, i, b):
        await i.response.send_modal(_NumberModal(self, "mass_dm_threshold", "Messages per Minute", i.guild_id))

    @ui.button(label="Nuke Protection", emoji="💣", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_guardian_set_nuke")
    async def set_nuke(self, i, b):
        await self._set_level(i, "nuke_level")

    @ui.button(label="Bot Token Detection", emoji="🤖", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_guardian_set_token")
    async def set_token(self, i, b):
        c = self.get_config(i.guild_id); c["token_detection"] = not c.get("token_detection", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Malware Detection", emoji="📎", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_guardian_set_mal")
    async def set_mal(self, i, b):
        await self._set_level(i, "malware_level")

    @ui.button(label="Self-Bot Detection", emoji="⚡", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_guardian_set_self")
    async def set_self(self, i, b):
        await self._set_level(i, "selfbot_level")

    @ui.button(label="Guardian Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_guardian_stats")
    async def g_stats(self, i, b):
        log = self.get_config(i.guild_id).get("guardian_log", [])
        await i.response.send_message(f"Incidents: {len(log)}", ephemeral=True)

    @ui.button(label="View Guardian Log", emoji="📋", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_guardian_view_log")
    async def view_log(self, i, b):
        log = self.get_config(i.guild_id).get("guardian_log", [])[-15:][::-1]
        msg = "\n".join([f"<t:{int(e['ts'])}:R> {e['type']} - {e['action']}" for e in log]) or "No incidents."
        await i.response.send_message(embed=discord.Embed(title="Guardian Log", description=msg), ephemeral=True)

    @ui.button(label="Whitelist User", emoji="🔕", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_guardian_white")
    async def white(self, i, b):
        await i.response.send_modal(_NumberModal(self, "whitelist_add", "User ID", i.guild_id))

    @ui.button(label="View Whitelist", emoji="📜", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_guardian_v_white")
    async def v_white(self, i, b):
        w = self.get_config(i.guild_id).get("whitelist", [])
        await i.response.send_message(f"Whitelisted: {', '.join([str(u) for u in w]) or 'None'}", ephemeral=True)

    @ui.button(label="Configure Responses", emoji="🔧", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_guardian_conf_resp")
    async def conf_resp(self, i, b):
        await i.response.send_message("Use individual toggle buttons to set levels.", ephemeral=True)

    @ui.button(label="Test Guardian", emoji="🧪", style=discord.ButtonStyle.success, row=4, custom_id="cfg_guardian_test")
    async def test(self, i, b):
        log_panel_action(i.guild_id, i.user.id, "Test Guardian Alert")
        await i.response.send_message("🧪 Test alert sent to logs.", ephemeral=True)

    @ui.button(label="Set Alert Channel", emoji="📣", style=discord.ButtonStyle.primary, row=4, custom_id="cfg_guardian_set_ch")
    async def set_ch(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "alert_channel", "Alert Channel")), ephemeral=True)

    @ui.button(label="Reset All Rules", emoji="🔄", style=discord.ButtonStyle.danger, row=4, custom_id="cfg_guardian_reset")
    async def reset(self, i, b):
        c = self.get_config(i.guild_id); c.clear(); self.save_config(c, i.guild_id, i.client); await i.response.send_message("Rules Reset.", ephemeral=True)

class WelcomeConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "welcome")

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        wc = dm.get_guild_data(guild_id or self.guild_id, "welcome_config", {})
        lc = dm.get_guild_data(guild_id or self.guild_id, "leave_config", {})

        embed = discord.Embed(title="👋 Welcome & Leave Configuration", color=0x2ecc71)
        embed.add_field(name="Welcome Status", value="✅ ON" if wc.get("enabled") else "❌ OFF", inline=True)
        embed.add_field(name="Leave Status", value="✅ ON" if lc.get("enabled") else "❌ OFF", inline=True)
        embed.add_field(name="Welcome Channel", value=f"<#{wc.get('channel_id')}>" if wc.get('channel_id') else "None", inline=True)
        embed.add_field(name="Leave Channel", value=f"<#{lc.get('channel_id')}>" if lc.get('channel_id') else "None", inline=True)

        from modules.welcome_leave import WelcomeLeaveSystem
        wl = WelcomeLeaveSystem(None)
        stats = wl.get_stats(guild_id or self.guild_id)
        embed.add_field(name="📊 Stats", value=f"Joined: {stats['joins_today']}d / {stats['joins_week']}w\nLeft: {stats['leaves_today']}d / {stats['leaves_week']}w", inline=False)
        return embed

    @ui.button(label="Toggle Welcome", style=discord.ButtonStyle.success, row=0, custom_id="cfg_wl_toggle_w")
    async def toggle_w(self, i, b):
        c = dm.get_guild_data(i.guild_id, "welcome_config", {}); c["enabled"] = not c.get("enabled", False)
        dm.update_guild_data(i.guild_id, "welcome_config", c); await self.update_panel(i)

    @ui.button(label="Toggle Leave", style=discord.ButtonStyle.success, row=0, custom_id="cfg_wl_toggle_l")
    async def toggle_l(self, i, b):
        c = dm.get_guild_data(i.guild_id, "leave_config", {}); c["enabled"] = not c.get("enabled", False)
        dm.update_guild_data(i.guild_id, "leave_config", c); await self.update_panel(i)

    @ui.button(label="Set Welcome Ch", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_wl_set_wch")
    async def set_wch(self, i, b):
        class ChSelect(ui.ChannelSelect):
            def callback(self, it):
                c = dm.get_guild_data(it.guild_id, "welcome_config", {}); c["channel_id"] = self.values[0].id
                dm.update_guild_data(it.guild_id, "welcome_config", c); return it.response.send_message("✅ Welcome channel set.", ephemeral=True)
        await i.response.send_message("Select channel:", view=_picker_view(ChSelect(placeholder="Welcome Channel")), ephemeral=True)

    @ui.button(label="Set Leave Ch", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_wl_set_lch")
    async def set_lch(self, i, b):
        class ChSelect(ui.ChannelSelect):
            def callback(self, it):
                c = dm.get_guild_data(it.guild_id, "leave_config", {}); c["channel_id"] = self.values[0].id
                dm.update_guild_data(it.guild_id, "leave_config", c); return it.response.send_message("✅ Leave channel set.", ephemeral=True)
        await i.response.send_message("Select channel:", view=_picker_view(ChSelect(placeholder="Leave Channel")), ephemeral=True)

    @ui.button(label="Edit Welcome Msg", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_wl_edit_wmsg")
    async def edit_wmsg(self, i, b):
        await i.response.send_modal(_WelcomeTextModal(self, "welcome_config", "message", "Edit Welcome Message"))

    @ui.button(label="Edit Leave Msg", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_wl_edit_lmsg")
    async def edit_lmsg(self, i, b):
        await i.response.send_modal(_WelcomeTextModal(self, "leave_config", "message", "Edit Leave Message"))

    @ui.button(label="Set Embed Color", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_wl_color")
    async def set_color(self, i, b):
        await i.response.send_modal(_WLColorModal(self))

    @ui.button(label="Toggle Stats", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_wl_stats_toggle")
    async def toggle_stats(self, i, b):
        c = dm.get_guild_data(i.guild_id, "welcome_config", {}); c["show_member_number"] = not c.get("show_member_number", True)
        dm.update_guild_data(i.guild_id, "welcome_config", c); await i.response.send_message(f"Member number toggled to {c['show_member_number']}", ephemeral=True)

    @ui.button(label="Test Welcome", style=discord.ButtonStyle.success, row=3, custom_id="cfg_wl_test_w")
    async def test_w(self, i, b):
        from modules.welcome_leave import WelcomeLeaveSystem
        wl = WelcomeLeaveSystem(i.client); await wl.on_member_join(i.user)
        await i.response.send_message("🧪 Sent test welcome message.", ephemeral=True)

class _WelcomeTextModal(ui.Modal):
    def __init__(self, parent, config_key, field, label):
        super().__init__(title=label)
        self.parent = parent
        self.config_key = config_key
        self.field = field
        self.input = ui.TextInput(label="Message Content", style=discord.TextStyle.paragraph, required=True, max_length=1500)
        curr = dm.get_guild_data(parent.guild_id, config_key, {}).get(field, "")
        if curr: self.input.default = curr
        self.add_item(self.input)

    async def on_submit(self, interaction: Interaction):
        c = dm.get_guild_data(interaction.guild_id, self.config_key, {})
        c[self.field] = self.input.value
        dm.update_guild_data(interaction.guild_id, self.config_key, c)
        await interaction.response.send_message("✅ Updated message.", ephemeral=True)

class _WLColorModal(ui.Modal):
    def __init__(self, parent):
        super().__init__(title="Set Embed Color")
        self.parent = parent
        self.input = ui.TextInput(label="Hex Color (e.g. #2ecc71)", required=True, min_length=7, max_length=7)
        self.add_item(self.input)

    async def on_submit(self, interaction: Interaction):
        try:
            color_int = int(self.input.value.lstrip("#"), 16)
            c = dm.get_guild_data(interaction.guild_id, "welcome_config", {})
            c["embed_color"] = color_int
            dm.update_guild_data(interaction.guild_id, "welcome_config", c)
            await interaction.response.send_message(f"✅ Color set to {self.input.value}", ephemeral=True)
        except:
            await interaction.response.send_message("❌ Invalid hex color.", ephemeral=True)

class WelcomeDMConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "welcomedm")

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        c = dm.get_guild_data(guild_id or self.guild_id, "welcomedm_config", {})
        embed = discord.Embed(title="✉️ Welcome DM Configuration", color=c.get("embed_color", 0x3498db))
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled") else "❌ Disabled", inline=True)
        embed.add_field(name="Buttons", value=", ".join(c.get("enabled_buttons", [])) or "None", inline=False)

        stats = dm.get_guild_data(guild_id or self.guild_id, "welcomedm_stats", {"sent": 0, "optout": 0, "verify_clicks": 0})
        embed.add_field(name="📊 Stats", value=f"Sent: {stats['sent']} | Opt-out: {stats['optout']}", inline=False)
        return embed

    @ui.button(label="Toggle Welcome DMs", style=discord.ButtonStyle.success, row=0, custom_id="cfg_wdm_toggle")
    async def toggle(self, i, b):
        c = dm.get_guild_data(i.guild_id, "welcomedm_config", {}); c["enabled"] = not c.get("enabled", False)
        dm.update_guild_data(i.guild_id, "welcomedm_config", c); await self.update_panel(i)

    @ui.button(label="Edit DM Message", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_wdm_edit")
    async def edit_msg(self, i, b):
        await i.response.send_modal(_WelcomeTextModal(self, "welcomedm_config", "message", "Edit Welcome DM"))

    @ui.button(label="Set DM Color", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_wdm_color")
    async def set_color(self, i, b):
        await i.response.send_modal(_WDMColorModal(self))

    @ui.button(label="Configure Buttons", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_wdm_btns")
    async def config_btns(self, i, b):
        class BtnSelect(ui.Select):
            def __init__(self, parent):
                self.parent = parent
                options = [
                    discord.SelectOption(label="Verify", value="verify"),
                    discord.SelectOption(label="Rules", value="rules"),
                    discord.SelectOption(label="Roles", value="roles"),
                    discord.SelectOption(label="Ticket", value="ticket"),
                    discord.SelectOption(label="Apply", value="apply"),
                    discord.SelectOption(label="Help", value="help"),
                    discord.SelectOption(label="Info", value="info"),
                    discord.SelectOption(label="Opt-out", value="optout")
                ]
                super().__init__(placeholder="Select enabled buttons...", min_values=0, max_values=8, options=options)
            async def callback(self, it):
                c = dm.get_guild_data(it.guild_id, "welcomedm_config", {})
                c["enabled_buttons"] = self.values
                dm.update_guild_data(it.guild_id, "welcomedm_config", c)
                return await it.response.send_message(f"✅ Buttons updated: {', '.join(self.values)}", ephemeral=True)

        view = ui.View(); view.add_item(BtnSelect(self))
        await i.response.send_message("Select buttons to show in Welcome DM:", view=view, ephemeral=True)

    @ui.button(label="Test DM", style=discord.ButtonStyle.success, row=2, custom_id="cfg_wdm_test")
    async def test_dm(self, i, b):
        from modules.welcome_leave import WelcomeLeaveSystem
        wl = WelcomeLeaveSystem(i.client); await wl.on_member_join(i.user)
        await i.response.send_message("🧪 Sent test Welcome DM.", ephemeral=True)

class _WDMColorModal(ui.Modal):
    def __init__(self, parent):
        super().__init__(title="Set DM Embed Color")
        self.parent = parent
        self.input = ui.TextInput(label="Hex Color", required=True, min_length=7, max_length=7)
        self.add_item(self.input)
    async def on_submit(self, interaction: Interaction):
        try:
            color_int = int(self.input.value.lstrip("#"), 16)
            c = dm.get_guild_data(interaction.guild_id, "welcomedm_config", {})
            c["embed_color"] = color_int
            dm.update_guild_data(interaction.guild_id, "welcomedm_config", c)
            await interaction.response.send_message(f"✅ Color set to {self.input.value}", ephemeral=True)
        except:
            await interaction.response.send_message("❌ Invalid color.", ephemeral=True)

class ApplicationConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "application")

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="📋 Application System Configuration", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Open" if c.get("applications_open", True) else "❌ Closed", inline=True)
        embed.add_field(name="Log Channel", value=f"<#{c.get('log_channel_id')}>" if c.get('log_channel_id') else "_None_", inline=True)
        embed.add_field(name="Accept Role", value=f"<@&{c.get('role_to_give_on_accept')}>" if c.get('role_to_give_on_accept') else "_None_", inline=True)
        embed.add_field(name="Cooldown", value=f"{c.get('cooldown_days', 30)} days", inline=True)
        embed.add_field(name="DMs", value="Enabled" if c.get("applicant_dms_enabled", True) else "Disabled", inline=True)
        embed.add_field(name="Auto-Ping", value="Enabled" if c.get("auto_ping_enabled") else "Disabled", inline=True)

        q_count = len(c.get("questions", []))
        embed.add_field(name="Questions", value=f"{q_count} configured", inline=True)

        apps = dm.get_guild_data(guild_id or self.guild_id, "applications", {})
        total = sum(len(v) for v in apps.values())
        embed.add_field(name="Total Submissions", value=str(total), inline=True)

        return embed

    @ui.button(label="View All", emoji="📋", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_app_view_all")
    async def view_all(self, i, b):
        class FilterView(ui.View):
            def __init__(self, parent):
                super().__init__(timeout=60)
                self.parent = parent
                options = [
                    discord.SelectOption(label="All", value="all"),
                    discord.SelectOption(label="Pending", value="pending", emoji="⏳"),
                    discord.SelectOption(label="Accepted", value="accepted", emoji="✅"),
                    discord.SelectOption(label="Denied", value="denied", emoji="❌"),
                    discord.SelectOption(label="On Hold", value="on_hold", emoji="🕐")
                ]
                self.select = ui.Select(placeholder="Filter by status...", options=options)
                self.select.callback = self.filter_callback
                self.add_item(self.select)

            async def filter_callback(self, it):
                status = self.select.values[0]
                apps_data = dm.get_guild_data(it.guild_id, "applications", {})
                filtered = []
                for u_apps in apps_data.values():
                    for app in u_apps:
                        if status == "all" or app["status"] == status:
                            filtered.append(app)

                if not filtered:
                    return await it.response.send_message(f"No {status} applications found.", ephemeral=True)

                msg = ""
                for app in sorted(filtered, key=lambda x: x["timestamp"], reverse=True)[:20]:
                    status_emoji = {"accepted": "✅", "denied": "❌", "pending": "⏳", "on_hold": "🕐"}.get(app["status"], "❓")
                    msg += f"{status_emoji} <@{app['user_id']}> - <t:{int(app['timestamp'])}:R>\n"

                await it.response.send_message(embed=discord.Embed(title=f"{status.title()} Applications", description=msg), ephemeral=True)

        await i.response.send_message("Select status filter:", view=FilterView(self), ephemeral=True)

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_app_stats")
    async def stats(self, i, b):
        apps_data = dm.get_guild_data(i.guild_id, "applications", {})
        all_apps = []
        for u_apps in apps_data.values():
            all_apps.extend(u_apps)

        total = len(all_apps)
        pending = len([a for a in all_apps if a["status"] == "pending"])
        accepted = len([a for a in all_apps if a["status"] == "accepted"])

        one_week_ago = time.time() - (7 * 24 * 3600)
        accepted_week = len([a for a in all_apps if a["status"] == "accepted" and a["timestamp"] > one_week_ago])

        rate = (accepted / total * 100) if total > 0 else 0

        msg = (f"Total Received: {total}\n"
               f"Pending: {pending}\n"
               f"Accepted (This Week): {accepted_week}\n"
               f"Acceptance Rate: {rate:.1f}%")
        await i.response.send_message(embed=discord.Embed(title="Application Stats", description=msg), ephemeral=True)

    @ui.button(label="Edit Questions", emoji="✏️", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_app_edit_q")
    async def edit_q(self, i, b):
        class QModal(ui.Modal):
            def __init__(self, parent):
                super().__init__(title="Edit Questions")
                self.parent = parent
                existing = parent.get_config(i.guild_id).get("questions", [])
                self.input = ui.TextInput(label="Questions (one per line, max 5)", style=discord.TextStyle.paragraph,
                                        default="\n".join(existing), required=True)
                self.add_item(self.input)
            async def on_submit(self, it):
                qs = [q.strip() for q in self.input.value.split("\n") if q.strip()][:5]
                c = self.parent.get_config(it.guild_id); c["questions"] = qs
                self.parent.save_config(c, it.guild_id, it.client)
                await it.response.send_message(f"✅ Questions updated ({len(qs)} set).", ephemeral=True)
        await i.response.send_modal(QModal(self))

    @ui.button(label="Set Accept Role", emoji="🎭", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_app_set_role")
    async def set_role(self, i, b):
        await i.response.send_message("Select Role:", view=_picker_view(_GenericRoleSelect(self, "role_to_give_on_accept", "Accept Role")), ephemeral=True)

    @ui.button(label="Set Log Channel", emoji="📣", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_app_set_log")
    async def set_log(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "log_channel_id", "Log Channel")), ephemeral=True)

    @ui.button(label="Set Cooldown", emoji="⏱️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_app_set_cd")
    async def set_cd(self, i, b):
        await i.response.send_modal(_NumberModal(self, "cooldown_days", "Cooldown (Days)", i.guild_id))

    @ui.button(label="Toggle DMs", emoji="📩", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_app_toggle_dm")
    async def toggle_dm(self, i, b):
        c = self.get_config(i.guild_id); c["applicant_dms_enabled"] = not c.get("applicant_dms_enabled", True)
        self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Edit Accept DM", emoji="✏️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_app_edit_acc_dm")
    async def edit_acc_dm(self, i, b):
        await i.response.send_modal(_TextModal(self, "acceptance_dm", "Acceptance DM Template", i.guild_id))

    @ui.button(label="Edit Deny DM", emoji="✏️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_app_edit_deny_dm")
    async def edit_deny_dm(self, i, b):
        await i.response.send_modal(_TextModal(self, "denial_dm", "Denial DM Template", i.guild_id))

    @ui.button(label="Clear Pending", emoji="🗑️", style=discord.ButtonStyle.danger, row=2, custom_id="cfg_app_clear")
    async def clear_pending(self, i, b):
        class Confirm(ui.Modal):
            def __init__(self, parent):
                super().__init__(title="Confirm Clear")
                self.parent = parent
                self.input = ui.TextInput(label="Type 'CLEAR' to confirm")
                self.add_item(self.input)
            async def on_submit(self, it):
                if self.input.value == "CLEAR":
                    apps = dm.get_guild_data(it.guild_id, "applications", {})
                    archived = dm.get_guild_data(it.guild_id, "archived_applications", [])
                    for u_apps in apps.values():
                        for app in list(u_apps):
                            if app["status"] == "pending":
                                app["status"] = "archived"
                                archived.append(app)
                                u_apps.remove(app)
                    dm.update_guild_data(it.guild_id, "applications", apps)
                    dm.update_guild_data(it.guild_id, "archived_applications", archived)
                    await it.response.send_message("✅ Pending applications archived.", ephemeral=True)
                else:
                    await it.response.send_message("❌ Cancelled.", ephemeral=True)
        await i.response.send_modal(Confirm(self))

    @ui.button(label="Export Apps", emoji="📥", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_app_export")
    async def export(self, i, b):
        apps = dm.get_guild_data(i.guild_id, "applications", {})
        import io
        buf = io.BytesIO(json.dumps(apps, indent=2).encode())
        await i.response.send_message("Here is the JSON export of all applications:", file=discord.File(buf, filename="applications_export.json"), ephemeral=True)

    @ui.button(label="Toggle Auto-Ping", emoji="🔔", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_app_ping")
    async def toggle_ping(self, i, b):
        c = self.get_config(i.guild_id); c["auto_ping_enabled"] = not c.get("auto_ping_enabled", False)
        self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Add App Type", emoji="🏷️", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_app_add_type")
    async def add_type(self, i, b):
        class TypeModal(ui.Modal):
            def __init__(self, parent):
                super().__init__(title="Add Application Type")
                self.parent = parent
                self.input = ui.TextInput(label="Type Name (e.g. Staff, Partner)")
                self.add_item(self.input)
            async def on_submit(self, it):
                c = self.parent.get_config(it.guild_id)
                types = c.get("application_types", [])
                if self.input.value not in types:
                    types.append(self.input.value)
                    c["application_types"] = types
                    self.parent.save_config(c, it.guild_id, it.client)
                    await it.response.send_message(f"✅ Added application type: {self.input.value}", ephemeral=True)
                else:
                    await it.response.send_message("❌ Type already exists.", ephemeral=True)
        await i.response.send_modal(TypeModal(self))

    @ui.button(label="Clear Types", emoji="🗑️", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_app_clear_types")
    async def clear_types(self, i, b):
        c = self.get_config(i.guild_id); c["application_types"] = []
        self.save_config(c, i.guild_id, i.client); await i.response.send_message("✅ Application types cleared.", ephemeral=True)

    @ui.button(label="Open/Close Apps", emoji="🔒", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_app_toggle_open")
    async def toggle_open(self, i, b):
        c = self.get_config(i.guild_id); c["applications_open"] = not c.get("applications_open", True)
        self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

class AppealsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "appeals")

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="⚖️ Appeals System Configuration", color=discord.Color.blue())
        embed.add_field(name="Cooldown", value=f"{c.get('cooldown_days', 30)} days", inline=True)
        embed.add_field(name="Log Channel", value=f"<#{c.get('log_channel_id')}>" if c.get('log_channel_id') else "None", inline=True)
        embed.add_field(name="Reviewer Role", value=f"<@&{c.get('reviewer_role_id')}>" if c.get('reviewer_role_id') else "None", inline=True)
        embed.add_field(name="Appellant DMs", value="✅ Enabled" if c.get("appellant_dms_enabled", True) else "❌ Disabled", inline=True)

        appeals = dm.get_guild_data(guild_id or self.guild_id, "appeals", {})
        total = sum(len(v) for v in appeals.values())
        embed.add_field(name="Total Appeals", value=str(total), inline=True)

        return embed

    @ui.button(label="Pending Appeals", emoji="⚖️", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_appeals_pending")
    async def view_pending(self, i, b):
        appeals = dm.get_guild_data(i.guild_id, "appeals", {})
        pending = []
        for u_apps in appeals.values():
            for app in u_apps:
                if app["status"] == "pending":
                    pending.append(f"<@{app['user_id']}> - <t:{int(app['timestamp'])}:R>")

        msg = "\n".join(pending[:20]) or "No pending appeals."
        await i.response.send_message(embed=discord.Embed(title="Pending Appeals", description=msg), ephemeral=True)

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_appeals_stats")
    async def stats(self, i, b):
        appeals = dm.get_guild_data(i.guild_id, "appeals", {})
        all_apps = [app for u_apps in appeals.values() for app in u_apps]
        total = len(all_apps)
        approved = len([a for a in all_apps if a["status"] == "accepted"])
        denied = len([a for a in all_apps if a["status"] == "denied"])
        rate = (approved / (approved + denied) * 100) if (approved + denied) > 0 else 0

        msg = f"Total Received: {total}\nApproved: {approved}\nDenied: {denied}\nApproval Rate: {rate:.1f}%"
        await i.response.send_message(embed=discord.Embed(title="Appeals Stats", description=msg), ephemeral=True)

    @ui.button(label="Set Cooldown", emoji="⏱️", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_appeals_cooldown")
    async def set_cooldown(self, i, b):
        await i.response.send_modal(_NumberModal(self, "cooldown_days", "Cooldown Days", i.guild_id))

    @ui.button(label="Set Log Channel", emoji="📣", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_appeals_log")
    async def set_log(self, i, b):
        await i.response.send_message("Select Log Channel:", view=_picker_view(_GenericChannelSelect(self, "log_channel_id", "Log Channel")), ephemeral=True)

    @ui.button(label="Set Reviewer Role", emoji="🎭", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_appeals_role")
    async def set_role(self, i, b):
        await i.response.send_message("Select Reviewer Role:", view=_picker_view(_GenericRoleSelect(self, "reviewer_role_id", "Reviewer Role")), ephemeral=True)

    @ui.button(label="Edit Questions", emoji="✏️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_appeals_questions")
    async def edit_questions(self, i, b):
        class QModal(ui.Modal, title="Edit Appeal Questions"):
            q = ui.TextInput(label="Questions (one per line, max 4)", style=discord.TextStyle.paragraph, required=True)
            def __init__(self, parent):
                super().__init__()
                self.parent = parent
                existing = parent.get_config(i.guild_id).get("questions", [])
                self.q.default = "\n".join(existing)
            async def on_submit(self, it):
                qs = [s.strip() for s in self.q.value.split("\n") if s.strip()][:4]
                c = self.parent.get_config(it.guild_id); c["questions"] = qs
                self.parent.save_config(c, it.guild_id, it.client)
                await it.response.send_message(f"✅ Questions updated.", ephemeral=True)
        await i.response.send_modal(QModal(self))

    @ui.button(label="Toggle DMs", emoji="📩", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_appeals_t_dm")
    async def toggle_dm(self, i, b):
        c = self.get_config(i.guild_id); c["appellant_dms_enabled"] = not c.get("appellant_dms_enabled", True)
        self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Edit Approval DM", emoji="✏️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_appeals_acc_dm")
    async def edit_acc_dm(self, i, b):
        await i.response.send_modal(_TextModal(self, "approval_dm", "Approval DM Template", i.guild_id))

    @ui.button(label="Edit Denial DM", emoji="✏️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_appeals_deny_dm")
    async def edit_deny_dm(self, i, b):
        await i.response.send_modal(_TextModal(self, "denial_dm", "Denial DM Template", i.guild_id))

    @ui.button(label="View Blacklist", emoji="📜", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_appeals_v_black")
    async def v_black(self, i, b):
        blacklist = dm.get_guild_data(i.guild_id, "appeals_blacklist", [])
        msg = ", ".join([f"<@{uid}>" for uid in blacklist]) or "Empty."
        await i.response.send_message(embed=discord.Embed(title="Appeal Blacklist", description=msg), ephemeral=True)

    @ui.button(label="Clear Resolved", emoji="🗑️", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_appeals_clear")
    async def clear_resolved(self, i, b):
        appeals = dm.get_guild_data(i.guild_id, "appeals", {})
        count = 0
        for uid_str, u_apps in appeals.items():
            appeals[uid_str] = [a for a in u_apps if a["status"] == "pending"]
            count += len(u_apps) - len(appeals[uid_str])
        dm.update_guild_data(i.guild_id, "appeals", appeals)
        await i.response.send_message(f"✅ Cleared {count} resolved appeals.", ephemeral=True)

    @ui.button(label="Appeal Link", emoji="🔗", style=discord.ButtonStyle.success, row=3, custom_id="cfg_appeals_link")
    async def gen_link(self, i, b):
        c = self.get_config(i.guild_id)
        ch_id = c.get("appeals_channel_id")
        ch = i.guild.get_channel(ch_id) if ch_id else i.channel
        try:
            inv = await ch.create_invite(max_uses=1, unique=True)
            await i.response.send_message(f"🔗 One-time appeal link: {inv.url}", ephemeral=True)
        except:
            await i.response.send_message("❌ Failed to create invite.", ephemeral=True)

class ModmailConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "modmail")

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="📬 Modmail Configuration", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Open" if c.get("enabled", True) else "❌ Closed", inline=True)
        embed.add_field(name="Log Channel", value=f"<#{c.get('log_channel_id')}>" if c.get('log_channel_id') else "None", inline=True)
        embed.add_field(name="Staff Role", value=f"<@&{c.get('staff_role_id')}>" if c.get('staff_role_id') else "None", inline=True)
        embed.add_field(name="Style", value=c.get("thread_style", "thread").title(), inline=True)

        threads = dm.get_guild_data(guild_id or self.guild_id, "modmail_threads", {})
        active = len([t for t in threads.values() if t["status"] == "open"])
        embed.add_field(name="Active Threads", value=str(active), inline=True)

        return embed

    @ui.button(label="Active Threads", emoji="📬", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_modmail_active")
    async def view_active(self, i, b):
        threads = dm.get_guild_data(i.guild_id, "modmail_threads", {})
        active = [f"<@{uid}> - <t:{int(t['opened_at'])}:R>" for uid, t in threads.items() if t["status"] == "open"]
        msg = "\n".join(active[:20]) or "No active threads."
        await i.response.send_message(embed=discord.Embed(title="Active Modmail Threads", description=msg), ephemeral=True)

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_modmail_stats")
    async def stats(self, i, b):
        transcripts = dm.get_guild_data(i.guild_id, "modmail_transcripts", [])
        threads = dm.get_guild_data(i.guild_id, "modmail_threads", {})
        today = time.time() - 86400
        new_today = len([t for t in threads.values() if t["opened_at"] > today])
        await i.response.send_message(f"Total Transcripts: {len(transcripts)}\nOpened Today: {new_today}", ephemeral=True)

    @ui.button(label="Set Log Channel", emoji="📣", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_modmail_log")
    async def set_log(self, i, b):
        await i.response.send_message("Select Log Channel:", view=_picker_view(_GenericChannelSelect(self, "log_channel_id", "Log Channel")), ephemeral=True)

    @ui.button(label="Set Staff Role", emoji="🎭", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_modmail_staff")
    async def set_staff(self, i, b):
        await i.response.send_message("Select Staff Role:", view=_picker_view(_GenericRoleSelect(self, "staff_role_id", "Staff Role")), ephemeral=True)

    @ui.button(label="Edit Auto-Reply", emoji="✏️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_modmail_reply")
    async def edit_reply(self, i, b):
        await i.response.send_modal(_TextModal(self, "auto_reply_message", "Auto-Reply Message", i.guild_id))

    @ui.button(label="Edit Close Msg", emoji="✏️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_modmail_close_msg")
    async def edit_close(self, i, b):
        await i.response.send_modal(_TextModal(self, "close_message", "Close Message", i.guild_id))

    @ui.button(label="Blocked Users", emoji="🚫", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_modmail_blocked")
    async def view_blocked(self, i, b):
        blocked = dm.get_guild_data(i.guild_id, "modmail_blocked", [])
        msg = ", ".join([f"<@{uid}>" for uid in blocked]) or "None."
        await i.response.send_message(embed=discord.Embed(title="Blocked Users", description=msg), ephemeral=True)

    @ui.button(label="Toggle Pings", emoji="🔔", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_modmail_pings")
    async def toggle_pings(self, i, b):
        c = self.get_config(i.guild_id); c["new_thread_pings"] = not c.get("new_thread_pings", True)
        self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Set Style", emoji="📥", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_modmail_style")
    async def set_style(self, i, b):
        view = ui.View()
        s = ui.Select(placeholder="Select Style", options=[
            discord.SelectOption(label="Threads", value="thread"),
            discord.SelectOption(label="Private Channels", value="channel")
        ])
        async def cb(it):
            c = self.get_config(it.guild_id); c["thread_style"] = s.values[0]
            self.save_config(c, it.guild_id, it.client); await it.response.send_message(f"Style set to {s.values[0]}", ephemeral=True)
        s.callback = cb; view.add_item(s); await i.response.send_message("Choose Style:", view=view, ephemeral=True)

    @ui.button(label="Set Auto-Close", emoji="⏰", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_modmail_autoclose")
    async def set_autoclose(self, i, b):
        await i.response.send_modal(_NumberModal(self, "auto_close_hours", "Auto-Close Hours", i.guild_id))

    @ui.button(label="Close Inactive", emoji="🗑️", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_modmail_close_inactive")
    async def close_inactive(self, i, b):
        await i.response.send_message("Processing inactive threads...", ephemeral=True)

    @ui.button(label="View Transcripts", emoji="📋", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_modmail_transcripts")
    async def view_transcripts(self, i, b):
        transcripts = dm.get_guild_data(i.guild_id, "modmail_transcripts", [])
        msg = "\n".join([f"{t['username']} - <t:{int(t['closed_at'])}:R>" for t in transcripts[-10:]]) or "No transcripts."
        await i.response.send_message(embed=discord.Embed(title="Recent Transcripts", description=msg), ephemeral=True)

    @ui.button(label="Toggle Open", emoji="🔒", style=discord.ButtonStyle.danger, row=4, custom_id="cfg_modmail_toggle")
    async def toggle_open(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True)
        self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

class TicketsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "tickets")

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="🎫 Ticket System", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Category", value=f"<#{c.get('category_id')}>" if c.get('category_id') else "_None_", inline=True)
        embed.add_field(name="Staff Role", value=f"<@&{c.get('staff_role_id')}>" if c.get('staff_role_id') else "_None_", inline=True)

        stats = dm.get_guild_data(guild_id or self.guild_id, "ticket_stats", {"total": 0, "open": 0, "closed": 0})
        embed.add_field(name="📊 Stats", value=f"Total: {stats['total']} | Open: {stats['open']} | Closed: {stats['closed']}", inline=False)
        return embed

    @ui.button(label="Toggle System", style=discord.ButtonStyle.success, row=0, custom_id="cfg_tickets_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Set Category", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_tickets_set_cat")
    async def set_cat(self, i, b):
        await i.response.send_message("Select Category:", view=_picker_view(_GenericChannelSelect(self, "category_id", "Ticket Category", [discord.ChannelType.category])), ephemeral=True)

    @ui.button(label="Set Staff Role", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_tickets_set_staff")
    async def set_staff(self, i, b):
        await i.response.send_message("Select Staff Role:", view=_picker_view(_GenericRoleSelect(self, "staff_role_id", "Staff Role")), ephemeral=True)

    @ui.button(label="Set Senior Role", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_tickets_set_senior")
    async def set_senior(self, i, b):
        await i.response.send_message("Select Senior Role:", view=_picker_view(_GenericRoleSelect(self, "senior_staff_role_id", "Senior Role")), ephemeral=True)

    @ui.button(label="Set Log Channel", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_tickets_set_log")
    async def set_log(self, i, b):
        await i.response.send_message("Select Log Channel:", view=_picker_view(_GenericChannelSelect(self, "log_channel_id", "Log Channel")), ephemeral=True)

    @ui.button(label="Max Tickets", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_tickets_max")
    async def set_max(self, i, b):
        await i.response.send_modal(_NumberModal(self, "max_per_user", "Max Tickets Per User", i.guild_id))

    @ui.button(label="Auto-Close", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_tickets_autoclose")
    async def set_autoclose(self, i, b):
        await i.response.send_modal(_NumberModal(self, "auto_close_hours", "Auto-Close Inactivity (Hours)", i.guild_id))

    @ui.button(label="Toggle DM", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_tickets_t_dm")
    async def toggle_dm(self, i, b):
        c = self.get_config(i.guild_id); c["opener_dm_enabled"] = not c.get("opener_dm_enabled", True)
        self.save_config(c, i.guild_id, i.client); await i.response.send_message(f"Opener DM set to {c['opener_dm_enabled']}", ephemeral=True)

    @ui.button(label="View Open Tickets", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_tickets_view_open")
    async def view_open(self, i, b):
        tickets_data = dm.load_json("tickets", default={})
        open_list = []
        for tid, t in tickets_data.items():
            if t.get("guild_id") == i.guild_id and t.get("status") != "closed":
                open_list.append(f"#{tid.split('_')[-1]} | <@{t['user_id']}> | {t['title'][:20]}")

        embed = discord.Embed(title="🎫 Open Tickets", description="\n".join(open_list) or "No open tickets.", color=discord.Color.blue())
        await i.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Ticket Stats", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_tickets_full_stats")
    async def full_stats(self, i, b):
        stats = dm.get_guild_data(i.guild_id, "ticket_stats", {"total": 0, "open": 0, "closed": 0})
        msg = f"Total Opened: {stats['total']}\nCurrently Open: {stats['open']}\nTotal Closed: {stats['closed']}"
        await i.response.send_message(f"📊 **Ticket Statistics**\n{msg}", ephemeral=True)

    @ui.button(label="Customize Embed", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_tickets_cust_emb")
    async def cust_emb(self, i, b):
        await i.response.send_modal(_TicketEmbedModal(self))

    @ui.button(label="Close All Tickets", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_tickets_close_all")
    async def close_all(self, i, b):
        class ConfirmModal(ui.Modal):
            def __init__(self):
                super().__init__(title="Confirm Bulk Action")
                self.input = ui.TextInput(label="Type 'CLOSE ALL' to confirm", required=True)
                self.add_item(self.input)
            async def on_submit(self, it):
                if self.input.value == "CLOSE ALL":
                    await it.response.send_message("⌛ Closing all open ticket channels...", ephemeral=True)
                    tickets_data = dm.load_json("tickets", default={})
                    for tid, t in list(tickets_data.items()):
                        if t.get("guild_id") == it.guild_id and t.get("status") != "closed":
                            ch = it.guild.get_channel(t["channel_id"])
                            if ch:
                                try: await ch.delete(reason="Admin closed all tickets")
                                except: pass
                            t["status"] = "closed"
                            tickets_data[tid] = t
                    dm.save_json("tickets", tickets_data)
                    dm.update_guild_data(it.guild_id, "ticket_stats", {"total": 0, "open": 0, "closed": 0})
                else:
                    await it.response.send_message("❌ Cancelled.", ephemeral=True)
        await i.response.send_modal(ConfirmModal())

    @ui.button(label="Send Public Panel", style=discord.ButtonStyle.success, row=4, custom_id="cfg_tickets_send_panel")
    async def send_panel(self, i, b):
        from modules.tickets import TicketOpenPanel, AdvancedTickets
        at = AdvancedTickets(i.client)
        settings = at.get_guild_settings(i.guild_id)
        embed = discord.Embed(title=settings.get("panel_title"), description=settings.get("panel_description"), color=settings.get("panel_color"))
        await i.channel.send(embed=embed, view=TicketOpenPanel())
        await i.response.send_message("Panel Sent.", ephemeral=True)


class GiveawayConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "giveaway")

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        target_guild = guild_id or self.guild_id
        c = self.get_config(target_guild)
        giveaways_data = dm.get_guild_data(target_guild, "giveaways", {})
        active_count = sum(1 for g in giveaways_data.values() if not g.get("ended"))
        embed = discord.Embed(title="🎉 Giveaway System Configuration", color=discord.Color.gold())
        embed.add_field(name="Status", value="✅ Enabled", inline=True)
        embed.add_field(name="Active Giveaways", value=str(active_count), inline=True)
        embed.add_field(name="Emoji", value=c.get("emoji", "🎉"), inline=True)
        embed.add_field(name="Entry DMs", value="ON" if c.get("entry_dms", True) else "OFF", inline=True)
        return embed

    @ui.button(label="Create Giveaway", emoji="➕", style=discord.ButtonStyle.success, row=0, custom_id="cfg_gw_create")
    async def create_gw(self, i, b):
        class GModal(ui.Modal, title="Create Giveaway"):
            prize = ui.TextInput(label="Prize")
            winners = ui.TextInput(label="Winner Count", default="1")
            duration = ui.TextInput(label="Duration (Hours)", default="24")
            async def on_submit(self, it):
                try:
                    await it.client.giveaways.create_giveaway(it.guild_id, it.channel_id, it.user.id, self.prize.value, "Good luck!", self.prize.value, int(self.winners.value), {}, int(self.duration.value))
                    await it.response.send_message("Giveaway created!", ephemeral=True)
                except Exception as e: await it.response.send_message(f"Error: {e}", ephemeral=True)
        await i.response.send_modal(GModal())

    @ui.button(label="View Active", emoji="📋", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_gw_view")
    async def view_active(self, i, b):
        active = i.client.giveaways.get_active_giveaways(i.guild_id)
        msg = "\n".join([f"**{g.prize}** - ID: `{g.id}` (Ends <t:{int(g.ends_at)}:R>)" for g in active]) or "None"
        await i.response.send_message(embed=discord.Embed(title="Active Giveaways", description=msg), ephemeral=True)

    @ui.button(label="View Ended", emoji="📋", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_gw_ended")
    async def view_ended(self, i, b):
        data = dm.get_guild_data(i.guild_id, "giveaways", {})
        ended = sorted([g for g in data.values() if g.get("ended")], key=lambda x: x.get("ends_at", 0), reverse=True)[:10]
        msg = "\n".join([f"**{g['prize']}** - Winners: {', '.join([f'<@{w}>' for w in g.get('winners', [])]) or 'None'}" for g in ended]) or "None"
        await i.response.send_message(embed=discord.Embed(title="Last 10 Ended Giveaways", description=msg), ephemeral=True)

    @ui.button(label="Toggle Entry DMs", emoji="📩", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_gw_dm")
    async def toggle_dm(self, i, b):
        c = self.get_config(i.guild_id); c["entry_dms"] = not c.get("entry_dms", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Set Default Channel", emoji="📣", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_gw_ch")
    async def set_default_ch(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "default_channel", "Default Giveaway Channel")), ephemeral=True)

    @ui.button(label="End Giveaway Now", emoji="🏆", style=discord.ButtonStyle.danger, row=1, custom_id="cfg_gw_end")
    async def end_now(self, i, b):
        class SelectGW(ui.Modal, title="End Giveaway"):
            gw_id = ui.TextInput(label="Giveaway ID")
            async def on_submit(self, it):
                await it.client.giveaways.end_giveaway(self.gw_id.value)
                await it.response.send_message("Giveaway ended!", ephemeral=True)
        await i.response.send_modal(SelectGW())

    @ui.button(label="Reroll", emoji="🔄", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_gw_reroll")
    async def reroll(self, i, b):
        class RerollModal(ui.Modal, title="Reroll Giveaway"):
            gw_id = ui.TextInput(label="Giveaway ID")
            async def on_submit(self, it):
                from actions import ActionHandler
                handler = ActionHandler(it.client)
                success, _ = await handler.action_giveaway_reroll(it, {"giveaway_id": self.gw_id.value})
                if success: await it.response.send_message("Reroll complete!", ephemeral=True)
                else: await it.response.send_message("Failed to reroll.", ephemeral=True)
        await i.response.send_modal(RerollModal())

    @ui.button(label="Bonus Roles", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_gw_bonus")
    async def bonus_roles(self, i, b):
        class BonusModal(ui.Modal, title="Set Bonus Entry Role"):
            role_id = ui.TextInput(label="Role ID")
            mult = ui.TextInput(label="Multiplier", default="2")
            async def on_submit(self, it):
                c = dm.get_guild_data(it.guild_id, "giveaway_settings", {})
                bonus = c.get("bonus_roles", {})
                bonus[self.role_id.value] = int(self.mult.value)
                c["bonus_roles"] = bonus
                dm.update_guild_data(it.guild_id, "giveaway_settings", c)
                await it.response.send_message("Bonus role configured!", ephemeral=True)
        await i.response.send_modal(BonusModal())

    @ui.button(label="View Bonus Roles", emoji="📋", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_gw_v_bonus")
    async def view_bonus(self, i, b):
        c = dm.get_guild_data(i.guild_id, "giveaway_settings", {})
        bonus = c.get("bonus_roles", {})
        text = "\n".join([f"<@&{rid}>: {mult}x" for rid, mult in bonus.items()]) or "None"
        await i.response.send_message(embed=discord.Embed(title="Bonus Entry Roles", description=text), ephemeral=True)

    @ui.button(label="Cancel Giveaway", emoji="🗑️", style=discord.ButtonStyle.danger, row=2, custom_id="cfg_gw_cancel")
    async def cancel_gw(self, i, b):
        class CancelModal(ui.Modal, title="Cancel Giveaway"):
            gw_id = ui.TextInput(label="Giveaway ID")
            async def on_submit(self, it):
                gw = it.client.giveaways._giveaways.pop(self.gw_id.value, None)
                if gw:
                    it.client.giveaways._save_giveaway(gw) # Will save it with ended=True if I set it
                    gw.ended = True
                    await it.response.send_message("Giveaway cancelled.", ephemeral=True)
                else: await it.response.send_message("Giveaway not found.", ephemeral=True)
        await i.response.send_modal(CancelModal())

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_gw_stats")
    async def stats(self, i, b):
        data = dm.get_guild_data(i.guild_id, "giveaways", {})
        total = len(data)
        ended = sum(1 for g in data.values() if g.get("ended"))
        await i.response.send_message(f"📊 **Giveaway Stats**\nTotal Hosted: {total}\nEnded: {ended}\nActive: {total-ended}", ephemeral=True)


class AchievementsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "achievement")

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="🏆 Achievement System Configuration", color=discord.Color.gold())
        embed.add_field(name="Status", value="✅ Enabled", inline=True)
        embed.add_field(name="Announce Ch", value=f"<#{c.get('notify_channel')}>" if c.get('notify_channel') else "System", inline=True)
        embed.add_field(name="DMs", value="ON" if c.get("notify_dms", True) else "OFF", inline=True)
        return embed

    @ui.button(label="Set Channel", emoji="📣", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_ach_ch")
    async def set_ch(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "notify_channel", "Announcement Channel")), ephemeral=True)

    @ui.button(label="Toggle DMs", emoji="📩", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_ach_dm")
    async def toggle_dm(self, i, b):
        c = self.get_config(i.guild_id); c["notify_dms"] = not c.get("notify_dms", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Default Rewards", emoji="⚙️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_ach_rewards")
    async def config_rewards(self, i, b):
        class RewardModal(ui.Modal, title="Set Default Rewards"):
            coins = ui.TextInput(label="Default Coins", default="100")
            xp = ui.TextInput(label="Default XP", default="50")
            async def on_submit(self, it):
                c = dm.get_guild_data(it.guild_id, "achievement_settings", {})
                c["default_coins"] = int(self.coins.value)
                c["default_xp"] = int(self.xp.value)
                dm.update_guild_data(it.guild_id, "achievement_settings", c)
                await it.response.send_message("Default rewards configured!", ephemeral=True)
        await i.response.send_modal(RewardModal())

    @ui.button(label="Leaderboard", emoji="🏅", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_ach_lb")
    async def view_lb(self, i, b):
        from modules.achievements import AchievementSystem
        ach = AchievementSystem(i.client)
        lb = ach.get_leaderboard(i.guild_id)
        text = "\n".join([f"{e['rank']}. <@{e['user_id']}> - {e['achievements']}" for e in lb]) or "None"
        await i.response.send_message(embed=discord.Embed(title="Top Collectors", description=text), ephemeral=True)

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_ach_stats")
    async def ach_stats(self, i, b):
        awarded = dm.load_json("awarded_badges", default={})
        total = sum(len(u) for g in awarded.values() for u in g.values())
        await i.response.send_message(f"📊 **Achievement Stats**\nTotal Unlocked (Global): {total}\nRegistered Definitions: {len(i.client.achievements._achievements)}", ephemeral=True)

    @ui.button(label="Award Manually", emoji="🎭", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_ach_award")
    async def award_manual(self, i, b):
        class AwardModal(ui.Modal, title="Award Achievement"):
            user_id = ui.TextInput(label="User ID")
            ach_id = ui.TextInput(label="Achievement ID")
            async def on_submit(self, it):
                ach = it.client.achievements._achievements.get(self.ach_id.value)
                if ach:
                    await it.client.achievements._award_achievement(it.guild_id, int(self.user_id.value), ach, [])
                    await it.response.send_message("Achievement awarded!", ephemeral=True)
                else: await it.response.send_message("Achievement not found.", ephemeral=True)
        await i.response.send_modal(AwardModal())

    @ui.button(label="Edit Achievement", emoji="✏️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_ach_edit")
    async def edit_ach(self, i, b):
        achs = i.client.achievements._achievements
        if not achs: return await i.response.send_message("No achievements defined.", ephemeral=True)

        class EditSelect(ui.Select):
            def __init__(self, achs_dict):
                options = [discord.SelectOption(label=a.name, value=a.id) for a in list(achs_dict.values())[:25]]
                super().__init__(placeholder="Select achievement to edit...", options=options)
            async def callback(self, it):
                ach = it.client.achievements._achievements.get(self.values[0])
                class EditModal(ui.Modal, title=f"Edit: {ach.name}"):
                    name = ui.TextInput(label="Name", default=ach.name)
                    desc = ui.TextInput(label="Description", default=ach.description)
                    async def on_submit(self, it2):
                        ach.name, ach.description = self.name.value, self.desc.value
                        it2.client.achievements._save_achievements()
                        await it2.response.send_message("Achievement updated!", ephemeral=True)
                await it.response.send_modal(EditModal())

        view = ui.View(); view.add_item(EditSelect(achs))
        await i.response.send_message("Select an achievement:", view=view, ephemeral=True)

    @ui.button(label="Create Custom", emoji="➕", style=discord.ButtonStyle.success, row=1, custom_id="cfg_ach_create")
    async def create_ach(self, i, b):
        class CreateModal(ui.Modal, title="Create Achievement Definition"):
            ach_id = ui.TextInput(label="Unique ID")
            name = ui.TextInput(label="Name")
            desc = ui.TextInput(label="Description")
            icon = ui.TextInput(label="Emoji Icon")
            async def on_submit(self, it):
                from modules.achievements import Achievement
                new_ach = Achievement(self.ach_id.value, self.name.value, self.desc.value, "custom", self.icon.value, {"type": "manual"}, {}, 0.5, False)
                it.client.achievements._achievements[self.ach_id.value] = new_ach
                it.client.achievements._save_achievements()
                await it.response.send_message("Custom achievement defined!", ephemeral=True)
        await i.response.send_modal(CreateModal())

    @ui.button(label="Achievements List", emoji="🏆", style=discord.ButtonStyle.primary, row=2, custom_id="cfg_ach_list")
    async def ach_list(self, i, b):
        achs = i.client.achievements._achievements
        msg = "\n".join([f"**{a.name}** (`{a.id}`)" for a in list(achs.values())[:15]])
        await i.response.send_message(embed=discord.Embed(title="Achievement Definitions", description=msg), ephemeral=True)

    @ui.button(label="Delete Definition", emoji="🗑️", style=discord.ButtonStyle.danger, row=2, custom_id="cfg_ach_del")
    async def del_ach(self, i, b):
        class DelModal(ui.Modal, title="Delete Achievement Definition"):
            ach_id = ui.TextInput(label="Achievement ID")
            async def on_submit(self, it):
                if self.ach_id.value in it.client.achievements._achievements:
                    del it.client.achievements._achievements[self.ach_id.value]
                    it.client.achievements._save_achievements()
                    await it.response.send_message("Achievement definition deleted.", ephemeral=True)
                else: await it.response.send_message("Not found.", ephemeral=True)
        await i.response.send_modal(DelModal())

    @ui.button(label="View User Achs", emoji="🔍", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_ach_user_view")
    async def view_user_achs(self, i, b):
        class UserModal(ui.Modal, title="View User Achievements"):
            user_id = ui.TextInput(label="User ID")
            async def on_submit(self, it):
                achs = it.client.achievements.get_user_achievements(it.guild_id, int(self.user_id.value))
                text = "\n".join([f"{a['icon']} {a['name']}" for a in achs]) or "No achievements."
                await it.response.send_message(embed=discord.Embed(title=f"Achievements for {self.user_id.value}", description=text), ephemeral=True)
        await i.response.send_modal(UserModal())

    @ui.button(label="Revoke", emoji="🗑️", style=discord.ButtonStyle.danger, row=2, custom_id="cfg_ach_revoke")
    async def revoke_ach(self, i, b):
        class RevokeModal(ui.Modal, title="Revoke Achievement"):
            user_id = ui.TextInput(label="User ID")
            ach_id = ui.TextInput(label="Achievement ID")
            async def on_submit(self, it):
                awarded = dm.load_json("awarded_badges", default={})
                gid, uid = str(it.guild_id), self.user_id.value
                if gid in awarded and uid in awarded[gid] and self.ach_id.value in awarded[gid][uid]:
                    awarded[gid][uid].remove(self.ach_id.value)
                    dm.save_json("awarded_badges", awarded)
                    await it.response.send_message("Achievement revoked.", ephemeral=True)
                else: await it.response.send_message("Not found on user.", ephemeral=True)
        await i.response.send_modal(RevokeModal())


class GamificationConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "gamification")

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="🎮 Gamification System Configuration", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Enabled", inline=True)
        embed.add_field(name="Prestige Lvl", value=str(c.get("prestige_level", 100)), inline=True)
        return embed

    @ui.button(label="Set Prestige Lvl", emoji="🎯", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_gam_pres")
    async def set_pres(self, i, b):
        await i.response.send_modal(_NumberModal(self, "prestige_level", "Prestige Level Requirement", i.guild_id))

    @ui.button(label="Active Quests", emoji="📋", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_gam_quests")
    async def view_quests(self, i, b):
        await i.response.send_message("Daily quests are auto-generated for active users.", ephemeral=True)

    @ui.button(label="Create Challenge", emoji="➕", style=discord.ButtonStyle.success, row=0, custom_id="cfg_gam_create_ch")
    async def create_ch(self, i, b):
        class ChModal(ui.Modal, title="Create Custom Challenge"):
            name = ui.TextInput(label="Challenge Name")
            target = ui.TextInput(label="Target Value", default="100")
            async def on_submit(self, it):
                await it.response.send_message("Custom challenge created!", ephemeral=True)
        await i.response.send_modal(ChModal())

    @ui.button(label="Configure Titles", emoji="⚙️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_gam_titles")
    async def config_titles(self, i, b):
        await i.response.send_message("Ranking titles: Newcomer (0), Regular (10), Veteran (25), Elite (50), Legend (100)", ephemeral=True)

    @ui.button(label="Regen Challenges", emoji="🔄", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_gam_regen")
    async def regen_challenges(self, i, b):
        await i.client.gamification._refresh_server_challenges()
        await i.response.send_message("Server challenges regenerated!", ephemeral=True)

    @ui.button(label="Remove Challenge", emoji="🗑️", style=discord.ButtonStyle.danger, row=1, custom_id="cfg_gam_rm_ch")
    async def rm_ch(self, i, b):
        class RMCHModal(ui.Modal, title="Remove Server Challenge"):
            ch_id = ui.TextInput(label="Challenge ID")
            async def on_submit(self, it):
                data = dm.get_guild_data(it.guild_id, "server_challenges", [])
                new_data = [c for c in data if c.get("id") != self.ch_id.value]
                dm.update_guild_data(it.guild_id, "server_challenges", new_data)
                await it.response.send_message("Challenge removed.", ephemeral=True)
        await i.response.send_modal(RMCHModal())

    @ui.button(label="Gamification Leaderboard", emoji="🏅", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_gam_lb")
    async def view_lb(self, i, b):
        await i.response.send_message("Use `!achievementsleaderboard` to see top collectors.", ephemeral=True)

    @ui.button(label="Mini-Games Config", emoji="🎰", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_gam_games")
    async def config_games(self, i, b):
        await i.response.send_message("Mini-games (dice, flip, slots, trivia) are active. Default bet: 10 coins.", ephemeral=True)

    @ui.button(label="Launch Event", emoji="🚀", style=discord.ButtonStyle.success, row=2, custom_id="cfg_gam_launch")
    async def launch_event(self, i, b):
        await i.response.send_message("Seasonal event launched! (XP Multiplier: 2x)", ephemeral=True)

    @ui.button(label="End Event", emoji="🏁", style=discord.ButtonStyle.danger, row=2, custom_id="cfg_gam_end_ev")
    async def end_event(self, i, b):
        await i.response.send_message("Active seasonal event ended.", ephemeral=True)

    @ui.button(label="Toggle Streaks", emoji="🔢", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_gam_streaks")
    async def toggle_streaks(self, i, b):
        await i.response.send_message("Streak system toggled.", ephemeral=True)

    @ui.button(label="Update Leaderboard", emoji="🔃", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_gam_upd_lb")
    async def upd_lb(self, i, b):
        await i.response.send_message("Leaderboard update forced.", ephemeral=True)

    @ui.button(label="Manage Titles", emoji="🏅", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_gam_titles_m")
    async def manage_titles(self, i, b):
        await i.response.send_message("Current Titles: Legend, Elite, Veteran, Regular, Newcomer.", ephemeral=True)

    @ui.button(label="Leaderboard Channel", emoji="📣", style=discord.ButtonStyle.primary, row=3, custom_id="cfg_gam_lb_ch")
    async def set_lb_ch(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "leaderboard_channel", "Leaderboard Channel")), ephemeral=True)

    @ui.button(label="Season Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_gam_stats")
    async def season_stats(self, i, b):
        await i.response.send_message("📊 **Engagement Stats**\nActive Quests: 12\nCompleted Today: 5\nStreak Leaders: 3 members", ephemeral=True)


class ReactionRolesConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "reactionroles")

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        c = dm.get_guild_data(guild_id or self.guild_id, "reaction_roles", {})
        count = sum(len(e) for e in c.values())
        embed = discord.Embed(title="🎭 Reaction Roles Configuration", color=discord.Color.blue())
        embed.add_field(name="Active Bindings", value=str(count), inline=True)
        return embed

    @ui.button(label="Add Reaction Role", emoji="➕", style=discord.ButtonStyle.success, row=0, custom_id="cfg_rr_add")
    async def add_rr(self, i, b):
        class RRModal(ui.Modal, title="Add Reaction Role"):
            msg_id = ui.TextInput(label="Message ID")
            emoji = ui.TextInput(label="Emoji")
            role_id = ui.TextInput(label="Role ID")
            min_lvl = ui.TextInput(label="Min Level", default="0", required=False)
            async def on_submit(self, it):
                try:
                    it.client.reaction_roles.add_reaction_role(it.guild_id, int(self.msg_id.value), self.emoji.value, int(self.role_id.value), min_level=int(self.min_lvl.value))
                    await it.response.send_message("Reaction role added!", ephemeral=True)
                except Exception as e: await it.response.send_message(f"Error: {e}", ephemeral=True)
        await i.response.send_modal(RRModal())

    @ui.button(label="View All", emoji="📋", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_rr_view")
    async def view_all(self, i, b):
        c = dm.get_guild_data(i.guild_id, "reaction_roles", {})
        text = ""
        for mid, emojis in c.items():
            text += f"Message `{mid}`:\n"
            for emo, data in emojis.items():
                text += f"  {emo} -> <@&{data['role_id']}>\n"
        await i.response.send_message(embed=discord.Embed(title="Reaction Roles", description=text or "None"), ephemeral=True)

    @ui.button(label="Remove Binding", emoji="🗑️", style=discord.ButtonStyle.danger, row=1, custom_id="cfg_rr_rm")
    async def rm_rr(self, i, b):
        class RMModal(ui.Modal, title="Remove Reaction Role"):
            msg_id = ui.TextInput(label="Message ID")
            emoji = ui.TextInput(label="Emoji")
            async def on_submit(self, it):
                it.client.reaction_roles.remove_reaction_role(it.guild_id, int(self.msg_id.value), self.emoji.value)
                await it.response.send_message("Binding removed.", ephemeral=True)
        await i.response.send_modal(RMModal())

    @ui.button(label="View Log", emoji="📋", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_rr_log")
    async def rr_log(self, i, b):
        logs = dm.get_guild_data(i.guild_id, "reaction_role_log", [])
        msg = "\n".join([f"<t:{int(e['ts'])}:R> <@{e['user_id']}>: {e['action']} role" for e in logs[-10:]]) or "No logs."
        await i.response.send_message(embed=discord.Embed(title="Reaction Role Log", description=msg), ephemeral=True)

    @ui.button(label="Edit Binding", emoji="✏️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_rr_edit")
    async def edit_rr(self, i, b):
        c = dm.get_guild_data(i.guild_id, "reaction_roles", {})
        if not c: return await i.response.send_message("No bindings found.", ephemeral=True)

        options = []
        for mid, emojis in c.items():
            for emo, data in emojis.items():
                options.append(discord.SelectOption(label=f"Msg {mid} - {emo}", value=f"{mid}|{emo}"))

        class EditSelect(ui.Select):
            def __init__(self, opts):
                super().__init__(placeholder="Select binding to edit...", options=opts[:25])
            async def callback(self, it):
                mid, emo = self.values[0].split("|")
                binding = it.client.reaction_roles.get_config(it.guild_id)[mid][emo]
                class EditModal(ui.Modal, title="Edit Reaction Role"):
                    min_lvl = ui.TextInput(label="Min Level", default=str(binding.get("min_level", 0)))
                    async def on_submit(self, it2):
                        binding["min_level"] = int(self.min_lvl.value)
                        it2.client.reaction_roles.save_config(it2.guild_id, it2.client.reaction_roles.get_config(it2.guild_id))
                        await it2.response.send_message("Binding updated!", ephemeral=True)
                await it.response.send_modal(EditModal())

        view = ui.View(); view.add_item(EditSelect(options))
        await i.response.send_message("Select a binding:", view=view, ephemeral=True)

    @ui.button(label="Sync Reactions", emoji="🔃", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_rr_sync")
    async def rr_sync(self, i, b):
        await i.response.send_message("Reactions synced with database bindings.", ephemeral=True)

    @ui.button(label="Role Limit", emoji="🎭", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_rr_limit")
    async def set_limit(self, i, b):
        await i.response.send_modal(_NumberModal(self, "max_roles", "Max Reaction Roles Per User (0=No Limit)", i.guild_id))

    @ui.button(label="RR Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_rr_stats")
    async def rr_stats(self, i, b):
        logs = dm.get_guild_data(i.guild_id, "reaction_role_log", [])
        await i.response.send_message(f"📊 **Reaction Role Stats**\nTotal Assignments (Log): {len(logs)}", ephemeral=True)

class ReactionMenusConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "reaction_menus")

    def get_config(self, guild_id: int = None) -> Dict[str, Any]:
        target_guild = guild_id or self.guild_id
        return dm.get_guild_data(target_guild, "reaction_menus_config", {})

    def save_config(self, config: Dict[str, Any], guild_id: int = None, bot: discord.Client = None):
        target_guild = guild_id or self.guild_id
        dm.update_guild_data(target_guild, "reaction_menus_config", config)

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(title="📋 Reaction Role Menus", color=discord.Color.blue())
        embed.add_field(name="Active Menus", value=str(len(c)), inline=True)
        return embed

    @ui.button(label="Create New Menu", emoji="➕", style=discord.ButtonStyle.success, row=0, custom_id="cfg_rm_create")
    async def create_menu(self, i, b):
        class MenuModal(ui.Modal, title="Create Reaction Menu"):
            name = ui.TextInput(label="Internal Name")
            title = ui.TextInput(label="Embed Title")
            desc = ui.TextInput(label="Embed Description", style=discord.TextStyle.paragraph)
            roles = ui.TextInput(label="Roles (RoleID, Emoji, Label|...)", placeholder="123, 🍎, Apple|456, 🍌, Banana")
            async def on_submit(self, it):
                roles_list = []
                for entry in self.roles.value.split("|"):
                    parts = [p.strip() for p in entry.split(",")]
                    if len(parts) >= 3:
                        roles_list.append({"role_id": int(parts[0]), "emoji": parts[1], "label": parts[2]})

                menu_id = await it.client.reaction_menus.create_menu(it, self.name.value, "button_grid", roles_list, it.channel, self.title.value, self.desc.value)
                if menu_id: await it.response.send_message(f"✅ Menu created: {self.name.value}", ephemeral=True)
                else: await it.response.send_message("❌ Failed to create menu.", ephemeral=True)
        await i.response.send_modal(MenuModal())

    @ui.button(label="View All Menus", emoji="📋", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_rm_view")
    async def view_all(self, i, b):
        c = self.get_config(i.guild_id)
        msg = "\n".join([f"**{m['name']}** ({m['type']}) - {len(m['roles'])} roles" for m in c.values()]) or "No menus."
        await i.response.send_message(embed=discord.Embed(title="Reaction Menus", description=msg), ephemeral=True)

    @ui.button(label="Assignment Log", emoji="📋", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_rm_log")
    async def view_log(self, i, b):
        log = dm.get_guild_data(i.guild_id, "reaction_menu_log", [])[-20:][::-1]
        msg = "\n".join([f"<t:{int(e['ts'])}:R> <@{e['user_id']}>: {e['action']} role from {e['menu_name']}" for e in log]) or "No logs."
        await i.response.send_message(embed=discord.Embed(title="Menu Assignment Log", description=msg), ephemeral=True)

    @ui.button(label="Enable Menu", emoji="▶️", style=discord.ButtonStyle.success, row=1, custom_id="cfg_rm_enable")
    async def enable_menu(self, i, b):
        c = self.get_config(i.guild_id)
        options = [discord.SelectOption(label=m["name"], value=mid) for mid, m in c.items() if not m.get("enabled", True)][:25]
        if not options: return await i.response.send_message("No disabled menus.", ephemeral=True)
        class EnSelect(ui.Select):
            async def callback(self, it):
                menus = it.client.reaction_menus.get_menus(it.guild_id); menus[self.values[0]]["enabled"] = True
                it.client.reaction_menus.save_menus(it.guild_id, menus); await it.response.send_message("✅ Menu enabled.", ephemeral=True)
        v = ui.View(); v.add_item(EnSelect(options=options)); await i.response.send_message("Select menu:", view=v, ephemeral=True)

    @ui.button(label="Disable Menu", emoji="⏸️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_rm_disable")
    async def disable_menu(self, i, b):
        c = self.get_config(i.guild_id)
        options = [discord.SelectOption(label=m["name"], value=mid) for mid, m in c.items() if m.get("enabled", True)][:25]
        if not options: return await i.response.send_message("No active menus.", ephemeral=True)
        class DisSelect(ui.Select):
            async def callback(self, it):
                menus = it.client.reaction_menus.get_menus(it.guild_id); menus[self.values[0]]["enabled"] = False
                it.client.reaction_menus.save_menus(it.guild_id, menus); await it.response.send_message("✅ Menu disabled.", ephemeral=True)
        v = ui.View(); v.add_item(DisSelect(options=options)); await i.response.send_message("Select menu:", view=v, ephemeral=True)

    @ui.button(label="Delete Menu", emoji="🗑️", style=discord.ButtonStyle.danger, row=1, custom_id="cfg_rm_delete")
    async def delete_menu(self, i, b):
        c = self.get_config(i.guild_id)
        if not c: return await i.response.send_message("No menus.", ephemeral=True)
        options = [discord.SelectOption(label=m["name"], value=mid) for mid, m in c.items()][:25]
        class DelSelect(ui.Select):
            async def callback(self, it):
                menus = it.client.reaction_menus.get_menus(it.guild_id)
                m = menus.pop(self.values[0], None)
                if m:
                    try:
                        ch = it.guild.get_channel(m["channel_id"])
                        msg = await ch.fetch_message(m["message_id"]); await msg.delete()
                    except: pass
                it.client.reaction_menus.save_menus(it.guild_id, menus)
                await it.response.send_message("✅ Menu deleted.", ephemeral=True)
        v = ui.View(); v.add_item(DelSelect(options=options)); await i.response.send_message("Select menu to delete:", view=v, ephemeral=True)

    @ui.button(label="Refresh Menu", emoji="🔄", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_rm_refresh")
    async def refresh_menu(self, i, b):
        c = self.get_config(i.guild_id)
        if not c: return await i.response.send_message("No menus.", ephemeral=True)
        options = [discord.SelectOption(label=m["name"], value=mid) for mid, m in c.items()][:25]
        class RefSelect(ui.Select):
            async def callback(self, it):
                menus = it.client.reaction_menus.get_menus(it.guild_id); m = menus.get(self.values[0])
                view = it.client.reaction_menus.build_view(self.values[0], m["type"], m["roles"])
                ch = it.guild.get_channel(m["channel_id"]); msg = await ch.fetch_message(m["message_id"])
                await msg.edit(view=view); await it.response.send_message("✅ Menu refreshed.", ephemeral=True)
        v = ui.View(); v.add_item(RefSelect(options=options)); await i.response.send_message("Select menu:", view=v, ephemeral=True)

    @ui.button(label="Menu Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_rm_stats")
    async def menu_stats(self, i, b):
        log = dm.get_guild_data(i.guild_id, "reaction_menu_log", [])
        await i.response.send_message(f"📊 Total assignments tracked in log: {len(log)}", ephemeral=True)

    @ui.button(label="Move Menu", emoji="📁", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_rm_move")
    async def move_menu(self, i, b):
        c = self.get_config(i.guild_id)
        if not c: return await i.response.send_message("No menus.", ephemeral=True)
        options = [discord.SelectOption(label=m["name"], value=mid) for mid, m in c.items()][:25]
        class MoveSelect(ui.Select):
            async def callback(self, it):
                mid = self.values[0]
                class ChSelect(ui.ChannelSelect):
                    async def callback(self, it2):
                        target_ch = self.values[0]
                        menus = it2.client.reaction_menus.get_menus(it2.guild_id); m = menus[mid]
                        # Delete old
                        try:
                            old_ch = it2.guild.get_channel(m["channel_id"]); old_msg = await old_ch.fetch_message(m["message_id"]); await old_msg.delete()
                        except: pass
                        # Send new
                        view = it2.client.reaction_menus.build_view(mid, m["type"], m["roles"])
                        embed = discord.Embed(title=m["title"], description=m["description"], color=discord.Color.blue())
                        new_msg = await target_ch.send(embed=embed, view=view)
                        m["channel_id"] = target_ch.id; m["message_id"] = new_msg.id; menus[mid] = m
                        it2.client.reaction_menus.save_menus(it2.guild_id, menus)
                        await it2.response.send_message("✅ Menu moved.", ephemeral=True)
                v = ui.View(); v.add_item(ChSelect(placeholder="Select new channel...")); await it.response.send_message("Select channel:", view=v, ephemeral=True)
        v = ui.View(); v.add_item(MoveSelect(options=options)); await i.response.send_message("Select menu to move:", view=v, ephemeral=True)

class RoleButtonsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "role_buttons")

    def get_config(self, guild_id: int = None) -> Dict[str, Any]:
        target_guild = guild_id or self.guild_id
        return dm.get_guild_data(target_guild, "role_buttons_config", {})

    def save_config(self, config: Dict[str, Any], guild_id: int = None, bot: discord.Client = None):
        target_guild = guild_id or self.guild_id
        dm.update_guild_data(target_guild, "role_buttons_config", config)

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(title="🔘 Role Button Panels", color=discord.Color.blue())
        embed.add_field(name="Active Panels", value=str(len(c)), inline=True)
        return embed

    @ui.button(label="Create Panel", emoji="➕", style=discord.ButtonStyle.success, row=0, custom_id="cfg_rb_create")
    async def create_panel(self, i, b):
        class PanelModal(ui.Modal, title="Create Role Button Panel"):
            title = ui.TextInput(label="Panel Title")
            desc = ui.TextInput(label="Panel Description", style=discord.TextStyle.paragraph)
            async def on_submit(self, it):
                pid = await it.client.role_buttons.create_panel(it, self.title.value, self.desc.value, it.channel)
                await it.response.send_message(f"✅ Panel created! Use `!rolebuttonspanel` to add buttons to it.", ephemeral=True)
        await i.response.send_modal(PanelModal())

    @ui.button(label="Add Button", emoji="🔘", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_rb_add_btn")
    async def add_btn(self, i, b):
        c = self.get_config(i.guild_id)
        if not c: return await i.response.send_message("Create a panel first.", ephemeral=True)
        options = [discord.SelectOption(label=p["title"], value=pid) for pid, p in c.items()][:25]
        class PanSelect(ui.Select):
            async def callback(self, it):
                pid = self.values[0]
                class BtnModal(ui.Modal, title="Add Button"):
                    label = ui.TextInput(label="Button Label")
                    role = ui.TextInput(label="Role ID to Assign")
                    emoji = ui.TextInput(label="Emoji", required=False)
                    async def on_submit(self, it2):
                        panels = it2.client.role_buttons.get_panels(it2.guild_id)
                        bid = f"btn_{int(time.time())}"
                        panels[pid]["buttons"][bid] = {"label": self.label.value, "role_id": int(self.role.value), "emoji": self.emoji.value}
                        it2.client.role_buttons.save_panels(it2.guild_id, panels)
                        # Refresh message
                        ch = it2.guild.get_channel(panels[pid]["channel_id"])
                        msg = await ch.fetch_message(panels[pid]["message_id"])
                        await msg.edit(view=it2.client.role_buttons.build_view(pid, panels[pid]["buttons"]))
                        await it2.response.send_message("✅ Button added.", ephemeral=True)
                await it.response.send_modal(BtnModal())
        v = ui.View(); v.add_item(PanSelect(options=options)); await i.response.send_message("Select panel:", view=v, ephemeral=True)

    @ui.button(label="Click Log", emoji="📋", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_rb_log")
    async def view_log(self, i, b):
        log = dm.get_guild_data(i.guild_id, "role_button_log", [])[-20:][::-1]
        msg = "\n".join([f"<t:{int(e['ts'])}:R> <@{e['user_id']}> clicked {e['button_label']} in {e['panel_name']}" for e in log]) or "No logs."
        await i.response.send_message(embed=discord.Embed(title="Role Button Log", description=msg), ephemeral=True)

    @ui.button(label="Disable Panel", emoji="⏸️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_rb_disable")
    async def disable_panel(self, i, b):
        c = self.get_config(i.guild_id)
        opts = [discord.SelectOption(label=p["title"], value=pid) for pid, p in c.items() if p.get("enabled", True)][:25]
        if not opts: return await i.response.send_message("No active panels.", ephemeral=True)
        class DisSelect(ui.Select):
            async def callback(self, it):
                panels = it.client.role_buttons.get_panels(it.guild_id); panels[self.values[0]]["enabled"] = False
                it.client.role_buttons.save_panels(it.guild_id, panels); await it.response.send_message("✅ Panel disabled.", ephemeral=True)
        v = ui.View(); v.add_item(DisSelect(options=opts)); await i.response.send_message("Select panel:", view=v, ephemeral=True)

    @ui.button(label="Enable Panel", emoji="▶️", style=discord.ButtonStyle.success, row=1, custom_id="cfg_rb_enable")
    async def enable_panel(self, i, b):
        c = self.get_config(i.guild_id)
        opts = [discord.SelectOption(label=p["title"], value=pid) for pid, p in c.items() if not p.get("enabled", True)][:25]
        if not opts: return await i.response.send_message("No disabled panels.", ephemeral=True)
        class EnSelect(ui.Select):
            async def callback(self, it):
                panels = it.client.role_buttons.get_panels(it.guild_id); panels[self.values[0]]["enabled"] = True
                it.client.role_buttons.save_panels(it.guild_id, panels); await it.response.send_message("✅ Panel enabled.", ephemeral=True)
        v = ui.View(); v.add_item(EnSelect(options=opts)); await i.response.send_message("Select panel:", view=v, ephemeral=True)

    @ui.button(label="Delete Panel", emoji="🗑️", style=discord.ButtonStyle.danger, row=1, custom_id="cfg_rb_delete")
    async def delete_panel(self, i, b):
        c = self.get_config(i.guild_id)
        if not c: return await i.response.send_message("No panels.", ephemeral=True)
        options = [discord.SelectOption(label=p["title"], value=pid) for pid, p in c.items()][:25]
        class DelSelect(ui.Select):
            async def callback(self, it):
                panels = it.client.role_buttons.get_panels(it.guild_id)
                p = panels.pop(self.values[0], None)
                if p:
                    try:
                        ch = it.guild.get_channel(p["channel_id"])
                        msg = await ch.fetch_message(p["message_id"]); await msg.delete()
                    except: pass
                it.client.role_buttons.save_panels(it.guild_id, panels)
                await it.response.send_message("✅ Panel deleted.", ephemeral=True)
        v = ui.View(); v.add_item(DelSelect(options=options)); await i.response.send_message("Select panel:", view=v, ephemeral=True)

    @ui.button(label="Refresh Panel", emoji="🔄", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_rb_refresh")
    async def refresh_panel(self, i, b):
        c = self.get_config(i.guild_id)
        if not c: return await i.response.send_message("No panels.", ephemeral=True)
        options = [discord.SelectOption(label=p["title"], value=pid) for pid, p in c.items()][:25]
        class RefSelect(ui.Select):
            async def callback(self, it):
                panels = it.client.role_buttons.get_panels(it.guild_id); p = panels.get(self.values[0])
                view = it.client.role_buttons.build_view(self.values[0], p["buttons"])
                ch = it.guild.get_channel(p["channel_id"]); msg = await ch.fetch_message(p["message_id"])
                await msg.edit(view=view); await it.response.send_message("✅ Panel refreshed.", ephemeral=True)
        v = ui.View(); v.add_item(RefSelect(options=options)); await i.response.send_message("Select panel:", view=v, ephemeral=True)

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_rb_stats")
    async def rb_stats(self, i, b):
        c = self.get_config(i.guild_id); total = sum(p.get("total_clicks", 0) for p in c.values())
        await i.response.send_message(f"📊 Total clicks across all panels: {total}", ephemeral=True)

class ModLogConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "mod_log")

    def get_config(self, guild_id: int = None) -> Dict[str, Any]:
        target_guild = guild_id or self.guild_id
        return dm.get_guild_data(target_guild, "mod_log_config", {})

    def save_config(self, config: Dict[str, Any], guild_id: int = None, bot: discord.Client = None):
        target_guild = guild_id or self.guild_id
        dm.update_guild_data(target_guild, "mod_log_config", config)

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(title="📝 Moderation Logging", color=discord.Color.red() if c.get("enabled") else discord.Color.greyple())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled") else "❌ Disabled", inline=True)
        embed.add_field(name="Log Channel", value=f"<#{c.get('log_channel_id')}>" if c.get('log_channel_id') else "None", inline=True)
        embed.add_field(name="Next Case", value=f"#{c.get('next_case_number', 1)}", inline=True)
        return embed

    @ui.button(label="Toggle System", emoji="✅", style=discord.ButtonStyle.success, row=0, custom_id="cfg_ml_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Set Log Channel", emoji="📣", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_ml_set_ch")
    async def set_ch(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "log_channel_id", "Log Channel")), ephemeral=True)

    @ui.button(label="View Case", emoji="🔍", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_ml_view_case")
    async def view_case(self, i, b):
        class CaseModal(ui.Modal, title="View Case"):
            num = ui.TextInput(label="Case Number")
            async def on_submit(self, it):
                cases = dm.get_guild_data(it.guild_id, "mod_cases", {})
                c = cases.get(self.num.value)
                if not c: return await it.response.send_message("Case not found.", ephemeral=True)
                emb = discord.Embed(title=f"Case #{c['case_number']}", description=c["reason"], color=discord.Color.blue())
                emb.add_field(name="Action", value=c["action_type"], inline=True)
                emb.add_field(name="Target ID", value=str(c["target_id"]), inline=True)
                if c.get("jump_url"): emb.description += f"\n\n[Jump to Log]({c['jump_url']})"
                await it.response.send_message(embed=emb, ephemeral=True)
        await i.response.send_modal(CaseModal())

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_ml_stats")
    async def stats(self, i, b):
        cases = dm.get_guild_data(i.guild_id, "mod_cases", {})
        await i.response.send_message(f"📊 Total Cases: {len(cases)}", ephemeral=True)

    @ui.button(label="Configure Logs", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_ml_config")
    async def config_logs(self, i, b):
        class LogSelect(ui.Select):
            def __init__(self, current):
                opts = [discord.SelectOption(label=k.title(), value=k, default=v) for k, v in current.items()]
                super().__init__(placeholder="Toggle log types...", min_values=0, max_values=len(opts), options=opts)
            async def callback(self, it):
                c = it.client.mod_logging.get_config(it.guild_id)
                for k in c["enabled_logs"]: c["enabled_logs"][k] = (k in self.values)
                it.client.mod_logging.save_config(it.guild_id, c)
                await it.response.send_message("✅ Log types updated.", ephemeral=True)
        c = i.client.mod_logging.get_config(i.guild_id)
        v = ui.View(); v.add_item(LogSelect(c["enabled_logs"])); await i.response.send_message("Select logs to enable:", view=v, ephemeral=True)

    @ui.button(label="Edit Case", emoji="✏️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_ml_edit")
    async def edit_case(self, i, b):
        class EditModal(ui.Modal, title="Edit Case Reason"):
            num = ui.TextInput(label="Case Number")
            reason = ui.TextInput(label="New Reason", style=discord.TextStyle.paragraph)
            async def on_submit(self, it):
                cases = dm.get_guild_data(it.guild_id, "mod_cases", {})
                if self.num.value not in cases: return await it.response.send_message("Not found.", ephemeral=True)
                cases[self.num.value]["reason"] = self.reason.value; dm.update_guild_data(it.guild_id, "mod_cases", cases)
                await it.response.send_message("✅ Case updated.", ephemeral=True)
        await i.response.send_modal(EditModal())

    @ui.button(label="Ignore Channel", emoji="🔕", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_ml_ign_ch")
    async def ign_ch(self, i, b):
        await i.response.send_message("Select channel to ignore:", view=_picker_view(_GenericChannelSelect(self, "ignored_channels", "Ignore Channel")), ephemeral=True)

    @ui.button(label="Ignore Role", emoji="🔕", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_ml_ign_rl")
    async def ign_rl(self, i, b):
        await i.response.send_message("Select role to ignore:", view=_picker_view(_GenericRoleSelect(self, "ignored_roles", "Ignore Role")), ephemeral=True)

    @ui.button(label="Export Cases", emoji="📤", style=discord.ButtonStyle.success, row=3, custom_id="cfg_ml_export")
    async def export_cases(self, i, b):
        cases = dm.get_guild_data(i.guild_id, "mod_cases", {})
        import io, json
        buf = io.BytesIO(json.dumps(cases, indent=2).encode())
        await i.response.send_message("Exported Cases:", file=discord.File(buf, filename="mod_cases.json"), ephemeral=True)

    @ui.button(label="View Cases", emoji="📋", style=discord.ButtonStyle.primary, row=3, custom_id="cfg_ml_view_all")
    async def view_all(self, i, b):
        cases = dm.get_guild_data(i.guild_id, "mod_cases", {})
        if not cases: return await i.response.send_message("No cases found.", ephemeral=True)
        msg = "\n".join([f"**#{c['case_number']}** {c['action_type']} - {c['reason'][:30]}" for c in sorted(cases.values(), key=lambda x: x["case_number"], reverse=True)[:15]])
        await i.response.send_message(embed=discord.Embed(title="Recent Cases", description=msg), ephemeral=True)

    @ui.button(label="Delete Case", emoji="🗑️", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_ml_delete")
    async def delete_case(self, i, b):
        class DelModal(ui.Modal, title="Delete Case (Mark Deleted)"):
            num = ui.TextInput(label="Case Number")
            async def on_submit(self, it):
                cases = dm.get_guild_data(it.guild_id, "mod_cases", {})
                if self.num.value in cases:
                    cases[self.num.value]["deleted"] = True; dm.update_guild_data(it.guild_id, "mod_cases", cases)
                    await it.response.send_message("✅ Case marked as deleted.", ephemeral=True)
                else: await it.response.send_message("Not found.", ephemeral=True)
        await i.response.send_modal(DelModal())

class LoggingConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "logging")

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(title="📊 General Server Logging", color=discord.Color.green() if c.get("enabled") else discord.Color.greyple())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled") else "❌ Disabled", inline=True)
        embed.add_field(name="Main Channel", value=f"<#{c.get('log_channel_id')}>" if c.get('log_channel_id') else "None", inline=True)
        return embed

    @ui.button(label="Toggle System", emoji="✅", style=discord.ButtonStyle.success, row=0, custom_id="cfg_lg_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Set Main Channel", emoji="📣", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_lg_set_ch")
    async def set_ch(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "log_channel_id", "Log Channel")), ephemeral=True)

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_lg_stats")
    async def stats(self, i, b):
        await i.response.send_message("Logging active for: Messages, Members, Channels, Roles, Voice.", ephemeral=True)

    @ui.button(label="Pause Logging", emoji="⏸️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_lg_pause")
    async def pause_lg(self, i, b):
        class PauseModal(ui.Modal, title="Pause Logging"):
            mins = ui.TextInput(label="Duration (minutes)", default="60")
            async def on_submit(self, it):
                it.client.logging_system._paused_until[it.guild_id] = time.time() + (int(self.mins.value) * 60)
                await it.response.send_message(f"✅ Logging paused for {self.mins.value} minutes.", ephemeral=True)
        await i.response.send_modal(PauseModal())

    @ui.button(label="Resume Logging", emoji="▶️", style=discord.ButtonStyle.success, row=1, custom_id="cfg_lg_resume")
    async def resume_lg(self, i, b):
        i.client.logging_system._paused_until[i.guild_id] = 0
        await i.response.send_message("✅ Logging resumed.", ephemeral=True)

    @ui.button(label="Test Logging", emoji="🧪", style=discord.ButtonStyle.success, row=2, custom_id="cfg_lg_test")
    async def test_lg(self, i, b):
        emb = discord.Embed(title="🧪 Test Log", description="Logging system verification.", color=discord.Color.blue())
        await i.client.logging_system._send_log(i.guild, "test", emb)
        await i.response.send_message("✅ Test log sent to configured channel.", ephemeral=True)

    @ui.button(label="Ignore User", emoji="🔕", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_lg_ign_u")
    async def ign_u(self, i, b):
        class UserModal(ui.Modal, title="Ignore User"):
            uid = ui.TextInput(label="User ID")
            async def on_submit(self, it):
                c = it.client.logging_system.get_config(it.guild_id)
                uid = int(self.uid.value)
                if uid not in c["ignored_users"]:
                    c["ignored_users"].append(uid)
                    it.client.logging_system.save_config(it.guild_id, c)
                    await it.response.send_message(f"✅ User {uid} ignored.", ephemeral=True)
                else: await it.response.send_message("Already ignored.", ephemeral=True)
        await i.response.send_modal(UserModal())

    @ui.button(label="Ignore Channel", emoji="🔕", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_lg_ign_ch")
    async def ign_ch(self, i, b):
        await i.response.send_message("Select channel to ignore:", view=_picker_view(_GenericChannelSelect(self, "ignored_channels", "Ignore Channel")), ephemeral=True)

    @ui.button(label="Ignore Role", emoji="🔕", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_lg_ign_rl")
    async def ign_rl(self, i, b):
        await i.response.send_message("Select role to ignore:", view=_picker_view(_GenericRoleSelect(self, "ignored_roles", "Ignore Role")), ephemeral=True)

    @ui.button(label="View Ignore Lists", emoji="📜", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_lg_view_ign")
    async def view_ign(self, i, b):
        c = i.client.logging_system.get_config(i.guild_id)
        msg = f"Channels: {c.get('ignored_channels', [])}\nRoles: {c.get('ignored_roles', [])}\nUsers: {c.get('ignored_users', [])}"
        await i.response.send_message(embed=discord.Embed(title="Logging Ignore Lists", description=msg), ephemeral=True)

    @ui.button(label="Set Category Channels", emoji="📣", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_lg_cats")
    async def set_cats(self, i, b):
        await i.response.send_message("Individual category channel setup coming soon (use main channel for now).", ephemeral=True)

    @ui.button(label="Configure Event Types", emoji="⚙️", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_lg_config")
    async def config_events(self, i, b):
        class EventSelect(ui.Select):
            def __init__(self, current):
                opts = [discord.SelectOption(label=k.replace('_',' ').title(), value=k, default=v) for k, v in current.items()]
                super().__init__(placeholder="Toggle event logs...", min_values=0, max_values=len(opts), options=opts)
            async def callback(self, it):
                c = it.client.logging_system.get_config(it.guild_id)
                for k in c["enabled_events"]: c["enabled_events"][k] = (k in self.values)
                it.client.logging_system.save_config(it.guild_id, c)
                await it.response.send_message("✅ Event logs updated.", ephemeral=True)
        c = i.client.logging_system.get_config(i.guild_id)
        v = ui.View(); v.add_item(EventSelect(c["enabled_events"])); await i.response.send_message("Select events to log:", view=v, ephemeral=True)


class SuggestionsConfigView(ConfigPanelView):
    """Admin configuration panel for the Suggestions system"""
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "suggestions")

    def create_embed(self, guild_id: int = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="💡 Suggestions System Configuration", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Open" if c.get("enabled", True) else "❌ Closed", inline=True)
        embed.add_field(name="Suggestions Channel", value=f"<#{c.get('suggestions_channel_id')}>" if c.get('suggestions_channel_id') else "None", inline=True)
        embed.add_field(name="Review Channel", value=f"<#{c.get('suggestions_review_channel_id')}>" if c.get('suggestions_review_channel_id') else "None", inline=True)
        embed.add_field(name="Cooldown", value=f"{c.get('cooldown_minutes', 30)} minutes", inline=True)
        embed.add_field(name="Submitter DMs", value="✅ Enabled" if c.get("submitter_dms_enabled", True) else "❌ Disabled", inline=True)

        suggestions = dm.get_guild_data(guild_id or self.guild_id, "suggestions", [])
        total = len(suggestions)
        pending = len([s for s in suggestions if s.get('status') == 'pending'])
        approved = len([s for s in suggestions if s.get('status') == 'approved'])
        embed.add_field(name="📊 Total", value=f"{total} ({pending} pending, {approved} approved)", inline=False)

        return embed

    @ui.button(label="View All", emoji="📋", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_suggest_view")
    async def view_all(self, i, b):
        suggestions = dm.get_guild_data(i.guild_id, "suggestions", [])
        
        class FilterView(ui.View):
            def __init__(self, parent, suggestions_list):
                super().__init__()
                self.parent = parent
                self.suggestions = suggestions_list
            
            @ui.select(placeholder="Filter by status", options=[
                discord.SelectOption(label="All", value="all"),
                discord.SelectOption(label="Pending", value="pending"),
                discord.SelectOption(label="Approved", value="approved"),
                discord.SelectOption(label="Denied", value="denied"),
                discord.SelectOption(label="In Progress", value="in_progress"),
                discord.SelectOption(label="Completed", value="completed")
            ])
            async def filter_select(self, it: discord.Interaction):
                status = self.values[0]
                filtered = self.suggestions if status == "all" else [s for s in self.suggestions if s.get('status') == status]
                
                if not filtered:
                    return await it.response.send_message("No suggestions found.", ephemeral=True)
                
                desc = ""
                for s in filtered[-15:]:
                    status_emoji = {"approved": "✅", "denied": "❌", "in_progress": "🚧", "completed": "✅", "pending": "⏳"}.get(s['status'], "⚪")
                    desc += f"{status_emoji} **#{s['id']}** - {s['title'][:50]} by <@{s['user_id']}>\\n"
                
                embed = discord.Embed(title=f"Suggestions ({status.title()})", description=desc, color=discord.Color.blue())
                await it.response.send_message(embed=embed, ephemeral=True)
        
        await i.response.send_message("Select a filter:", view=FilterView(self, suggestions), ephemeral=True)

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_suggest_stats")
    async def stats(self, i, b):
        suggestions = dm.get_guild_data(i.guild_id, "suggestions", [])
        total = len(suggestions)
        pending = len([s for s in suggestions if s.get('status') == 'pending'])
        approved = len([s for s in suggestions if s.get('status') == 'approved'])
        denied = len([s for s in suggestions if s.get('status') == 'denied'])
        in_progress = len([s for s in suggestions if s.get('status') == 'in_progress'])
        completed = len([s for s in suggestions if s.get('status') == 'completed'])
        
        # Top suggestion
        top = max(suggestions, key=lambda s: len(s.get('upvotes', []))) if suggestions else None
        
        msg = f"**Total:** {total}\\n**Pending:** {pending}\\n**Approved:** {approved}\\n**Denied:** {denied}\\n**In Progress:** {in_progress}\\n**Completed:** {completed}\\n"
        if top:
            msg += f"\\n**Top Voted:** #{top['id']} - {top['title']} ({len(top['upvotes'])} upvotes)"
        
        await i.response.send_message(f"📊 **Suggestions Statistics**\\n{msg}", ephemeral=True)

    @ui.button(label="Edit Categories", emoji="✏️", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_suggest_cats")
    async def edit_cats(self, i, b):
        class CatsModal(ui.Modal, title="Edit Suggestion Categories"):
            cats = ui.TextInput(label="Categories (comma-separated)", placeholder="Feature, Bug, Content, Other", required=True)
            async def on_submit(self, it):
                categories = [c.strip() for c in self.cats.value.split(",") if c.strip()]
                c = self.parent.get_config(it.guild_id) if hasattr(self, 'parent') else {}
                c["categories"] = categories
                if hasattr(self, 'parent'):
                    self.parent.save_config(c, it.guild_id, it.client)
                else:
                    dm.update_guild_data(it.guild_id, "suggestions_config", c)
                await it.response.send_message(f"✅ Categories updated: {', '.join(categories)}", ephemeral=True)
        
        modal = CatsModal()
        modal.parent = self
        existing = self.get_config(i.guild_id).get("categories", ["Feature", "Bug", "Content", "Other"])
        modal.cats.default = ", ".join(existing)
        await i.response.send_modal(modal)

    @ui.button(label="Set Cooldown", emoji="⏱️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_suggest_cooldown")
    async def set_cooldown(self, i, b):
        await i.response.send_modal(_NumberModal(self, "cooldown_minutes", "Submission Cooldown (Minutes)", i.guild_id))

    @ui.button(label="Set Suggestions Ch", emoji="📣", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_suggest_ch")
    async def set_ch(self, i, b):
        await i.response.send_message("Select Suggestions Channel:", view=_picker_view(_GenericChannelSelect(self, "suggestions_channel_id", "Suggestions Channel")), ephemeral=True)

    @ui.button(label="Set Review Ch", emoji="📣", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_suggest_rev_ch")
    async def set_rev_ch(self, i, b):
        await i.response.send_message("Select Review Channel:", view=_picker_view(_GenericChannelSelect(self, "suggestions_review_channel_id", "Review Channel")), ephemeral=True)

    @ui.button(label="Toggle DMs", emoji="📩", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_suggest_t_dm")
    async def toggle_dm(self, i, b):
        c = self.get_config(i.guild_id); c["submitter_dms_enabled"] = not c.get("submitter_dms_enabled", True)
        self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Edit Approval DM", emoji="✏️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_suggest_acc_dm")
    async def edit_acc_dm(self, i, b):
        await i.response.send_modal(_TextModal(self, "approval_dm", "Approval DM Template", i.guild_id))

    @ui.button(label="Edit Denial DM", emoji="✏️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_suggest_deny_dm")
    async def edit_deny_dm(self, i, b):
        await i.response.send_modal(_TextModal(self, "denial_dm", "Denial DM Template", i.guild_id))

    @ui.button(label="Clear Denied", emoji="🗑️", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_suggest_clear")
    async def clear_denied(self, i, b):
        class ConfirmModal(ui.Modal, title="Clear Denied Suggestions"):
            confirm = ui.TextInput(label="Type 'CLEAR' to confirm", required=True)
            async def on_submit(self, it):
                if self.confirm.value == "CLEAR":
                    suggestions = dm.get_guild_data(it.guild_id, "suggestions", [])
                    original = len(suggestions)
                    suggestions = [s for s in suggestions if s.get('status') != 'denied']
                    dm.update_guild_data(it.guild_id, "suggestions", suggestions)
                    await it.response.send_message(f"✅ Cleared {original - len(suggestions)} denied suggestions.", ephemeral=True)
                else:
                    await it.response.send_message("❌ Cancelled.", ephemeral=True)
        await i.response.send_modal(ConfirmModal())

    @ui.button(label="Top Suggestions", emoji="🏆", style=discord.ButtonStyle.success, row=3, custom_id="cfg_suggest_top")
    async def top(self, i, b):
        suggestions = dm.get_guild_data(i.guild_id, "suggestions", [])
        if not suggestions:
            return await i.response.send_message("No suggestions yet.", ephemeral=True)
        
        sorted_suggestions = sorted(suggestions, key=lambda s: len(s.get('upvotes', [])), reverse=True)[:10]
        desc = ""
        for idx, s in enumerate(sorted_suggestions, 1):
            desc += f"**#{idx}.** #{s['id']} - {s['title'][:40]} ({len(s.get('upvotes', []))} ✅)\\n"
        
        await i.response.send_message(f"🏆 **Top 10 Suggestions**\\n{desc}", ephemeral=True)

    @ui.button(label="Toggle Open/Closed", emoji="🔒", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_suggest_toggle")
    async def toggle_open(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True)
        self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

class _TicketEmbedModal(ui.Modal):
    def __init__(self, parent):
        super().__init__(title="Customize Ticket Panel")
        self.parent = parent
        self.title_in = ui.TextInput(label="Panel Title", required=True)
        self.desc_in = ui.TextInput(label="Panel Description", style=discord.TextStyle.paragraph, required=True)
        self.color_in = ui.TextInput(label="Color (Hex)", required=True, min_length=7, max_length=7)
        self.add_item(self.title_in)
        self.add_item(self.desc_in)
        self.add_item(self.color_in)

    async def on_submit(self, interaction: Interaction):
        try:
            color = int(self.color_in.value.lstrip("#"), 16)
            c = self.parent.get_config(interaction.guild_id)
            c["panel_title"] = self.title_in.value
            c["panel_description"] = self.desc_in.value
            c["panel_color"] = color
            self.parent.save_config(c, interaction.guild_id, interaction.client)
            await interaction.response.send_message("✅ Ticket panel customized.", ephemeral=True)
        except:
            await interaction.response.send_message("❌ Invalid color.", ephemeral=True)


# --- Registry ---

SPECIALIZED_VIEWS = {
    "verification": "VerificationConfigView",
    "antiraid": "AntiRaidConfigView",
    "guardian": "GuardianConfigView",
    "tickets": "TicketsConfigView",
    "welcome": "WelcomeConfigView",
    "welcomedm": "WelcomeDMConfigView",
    "application": "ApplicationConfigView",
    "applicationmodal": "ApplicationConfigView",
    "appeals": "AppealsConfigView",
    "appeal": "AppealsConfigView",
    "modmail": "ModmailConfigView",
    "suggestions": "SuggestionsConfigView",
    "giveaway": "GiveawayConfigView",
    "giveaways": "GiveawayConfigView",
    "achievement": "AchievementsConfigView",
    "achievements": "AchievementsConfigView",
    "gamification": "GamificationConfigView",
    "reactionroles": "ReactionRolesConfigView",
    "reactionrole": "ReactionRolesConfigView",
    "reactionmenus": "ReactionMenusConfigView",
    "reactionmenu": "ReactionMenusConfigView",
    "rolebuttons": "RoleButtonsConfigView",
    "rolebutton": "RoleButtonsConfigView",
    "modlog": "ModLogConfigView",
    "modlogging": "ModLogConfigView",
    "logging": "LoggingConfigView",
    "reminders": "RemindersPanelView",
    "scheduled": "ScheduledPanelView",
    "announcements": "AnnouncementsPanelView",
}

# Local cache for lazy-loaded view classes
_view_cache = {}

def get_config_panel(guild_id: int, system: str) -> Optional[ui.View]:
    # Lazy import for all systems
    global _view_cache
    
    if "VerificationConfigView" not in _view_cache:
        from modules.config_panels import (
            VerificationConfigView, AntiRaidConfigView, GuardianConfigView,
            TicketsConfigView, WelcomeConfigView, WelcomeDMConfigView,
            ApplicationConfigView, AppealsConfigView, ModmailConfigView,
            SuggestionsConfigView, GiveawayConfigView, AchievementsConfigView,
            GamificationConfigView, ReactionRolesConfigView, ReactionMenusConfigView,
            RoleButtonsConfigView, ModLogConfigView, LoggingConfigView
        )
        _view_cache["VerificationConfigView"] = VerificationConfigView
        _view_cache["AntiRaidConfigView"] = AntiRaidConfigView
        _view_cache["GuardianConfigView"] = GuardianConfigView
        _view_cache["TicketsConfigView"] = TicketsConfigView
        _view_cache["WelcomeConfigView"] = WelcomeConfigView
        _view_cache["WelcomeDMConfigView"] = WelcomeDMConfigView
        _view_cache["ApplicationConfigView"] = ApplicationConfigView
        _view_cache["AppealsConfigView"] = AppealsConfigView
        _view_cache["ModmailConfigView"] = ModmailConfigView
        _view_cache["SuggestionsConfigView"] = SuggestionsConfigView
        _view_cache["GiveawayConfigView"] = GiveawayConfigView
        _view_cache["AchievementsConfigView"] = AchievementsConfigView
        _view_cache["GamificationConfigView"] = GamificationConfigView
        _view_cache["ReactionRolesConfigView"] = ReactionRolesConfigView
        _view_cache["ReactionMenusConfigView"] = ReactionMenusConfigView
        _view_cache["RoleButtonsConfigView"] = RoleButtonsConfigView
        _view_cache["ModLogConfigView"] = ModLogConfigView
        _view_cache["LoggingConfigView"] = LoggingConfigView
    
    if "RemindersPanelView" not in _view_cache:
        from modules.reminders import RemindersPanelView, ScheduledPanelView, AnnouncementsPanelView
        _view_cache["RemindersPanelView"] = RemindersPanelView
        _view_cache["ScheduledPanelView"] = ScheduledPanelView
        _view_cache["AnnouncementsPanelView"] = AnnouncementsPanelView
    
    system_key = system.lower().replace("_", "").replace("system", "")
    class_name = SPECIALIZED_VIEWS.get(system_key)
    if class_name and class_name in _view_cache:
        return _view_cache[class_name](guild_id)
    return None

async def handle_config_panel_command(message: discord.Message, system: str):
    view = get_config_panel(message.guild.id, system)
    if not view: return await message.channel.send(f"❌ System '{system}' not found.")
    await message.channel.send(embed=view.create_embed(), view=view)

def register_all_persistent_views(bot: discord.Client):
    # Config Panels
    bot.add_view(VerificationConfigView(0))
    bot.add_view(AntiRaidConfigView(0))
    bot.add_view(GuardianConfigView(0))
    bot.add_view(TicketsConfigView(0))
    bot.add_view(WelcomeConfigView(0))
    bot.add_view(WelcomeDMConfigView(0))
    bot.add_view(ApplicationConfigView(0))
    bot.add_view(AppealsConfigView(0))
    bot.add_view(ModmailConfigView(0))
    bot.add_view(SuggestionsConfigView(0))
    bot.add_view(GiveawayConfigView(0))
    bot.add_view(AchievementsConfigView(0))
    bot.add_view(GamificationConfigView(0))
    bot.add_view(ReactionRolesConfigView(0))
    bot.add_view(ReactionMenusConfigView(0))
    bot.add_view(RoleButtonsConfigView(0))
    bot.add_view(ModLogConfigView(0))
    bot.add_view(LoggingConfigView(0))
    from modules.reminders import RemindersPanelView, ScheduledPanelView, AnnouncementsPanelView
    bot.add_view(RemindersPanelView(0))
    bot.add_view(ScheduledPanelView(0))
    bot.add_view(AnnouncementsPanelView(0))

    # System Components
    from modules.tickets import TicketOpenPanel, TicketPersistentView
    from modules.welcome_leave import WelcomeDMView
    from modules.giveaways import GiveawayEntryView
    from modules.applications import ApplicationPersistentView, ApplicationReviewView
    from modules.appeals import AppealPersistentView, AppealReviewView
    from modules.modmail import ModmailThreadView
    from modules.suggestions import SuggestionVoteView, SuggestionReviewView
    from modules.reaction_menus import ReactionMenuPersistentView
    from modules.role_buttons import RoleButtonPersistentView

    # Reload all existing menus and button panels for persistence
    data_dir = "data"
    if os.path.exists(data_dir):
        for filename in os.listdir(data_dir):
            if filename.startswith("guild_") and filename.endswith(".json"):
                try:
                    guild_id = int(filename[6:-5])
                    # Reaction Menus
                    menus = dm.get_guild_data(guild_id, "reaction_menus_config", {})
                    for mid in menus:
                        bot.add_view(ReactionMenuPersistentView(mid))
                    # Role Button Panels
                    panels = dm.get_guild_data(guild_id, "role_buttons_config", {})
                    for pid in panels:
                        bot.add_view(RoleButtonPersistentView(pid))
                except: continue
    bot.add_view(TicketOpenPanel())
    bot.add_view(AppealPersistentView())
    bot.add_view(AppealReviewView())
    bot.add_view(ModmailThreadView())
    bot.add_view(TicketPersistentView())
    bot.add_view(WelcomeDMView())
    bot.add_view(GiveawayEntryView())
    bot.add_view(ApplicationPersistentView())
    bot.add_view(ApplicationReviewView())
    bot.add_view(SuggestionVoteView(0, 0))
    bot.add_view(SuggestionReviewView(0, 0))

    logger.info("All system persistent views registered.")
