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
    "verification": VerificationConfigView,
    "antiraid": AntiRaidConfigView,
    "guardian": GuardianConfigView,
    "tickets": TicketsConfigView,
    "welcome": WelcomeConfigView,
    "welcomedm": WelcomeDMConfigView,
    "application": ApplicationConfigView,
    "applicationmodal": ApplicationConfigView,
    "appeals": AppealsConfigView,
    "appeal": AppealsConfigView,
    "modmail": ModmailConfigView,
}

def get_config_panel(guild_id: int, system: str) -> Optional[ui.View]:
    system_key = system.lower().replace("_", "").replace("system", "")
    if system_key in SPECIALIZED_VIEWS: return SPECIALIZED_VIEWS[system_key](guild_id)
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

    # System Components
    from modules.tickets import TicketOpenPanel, TicketPersistentView
    from modules.welcome_leave import WelcomeDMView
    from modules.applications import ApplicationPersistentView, ApplicationReviewView
    from modules.appeals import AppealPersistentView, AppealReviewView
    from modules.modmail import ModmailThreadView
    bot.add_view(TicketOpenPanel())
    bot.add_view(AppealPersistentView())
    bot.add_view(AppealReviewView())
    bot.add_view(ModmailThreadView())
    bot.add_view(TicketPersistentView())
    bot.add_view(WelcomeDMView())
    bot.add_view(ApplicationPersistentView())
    bot.add_view(ApplicationReviewView())

    logger.info("All system persistent views registered.")
