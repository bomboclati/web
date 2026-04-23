import discord
from discord import ui, Interaction, app_commands
import asyncio
import json
import os
import time
import re
import io
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
    def __init__(self, system_name: str, guild_id: int = 0):
        super().__init__(timeout=None)
        self.system_name = system_name
        self.guild_id = guild_id

    def get_config(self, gid: int) -> Dict[str, Any]:
        return dm.get_guild_data(gid, f"{self.system_name}_config", {})

    def save_config(self, gid: int, config: Dict[str, Any]):
        dm.update_guild_data(gid, f"{self.system_name}_config", config)
        # Register commands
        from modules.auto_setup import AutoSetup
        bot = getattr(self, "bot", None)
        setup_helper = AutoSetup(bot)
        setup_helper._register_system_commands(gid, self.system_name)

    async def update_panel(self, interaction: Interaction):
        embed = self.create_embed(interaction.guild_id)
        await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self, gid: int) -> discord.Embed:
        return discord.Embed(title=f"Config: {self.system_name.title()}")

# --- Reusable Components ---

class _GenericRoleSelect(ui.RoleSelect):
    def __init__(self, system_name: str, key: str, placeholder: str):
        custom_id = f"sel_role_{system_name}_{key}"
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, custom_id=custom_id)
        self.system_name = system_name
        self.key = key

    async def callback(self, interaction: Interaction):
        config = dm.get_guild_data(interaction.guild_id, f"{self.system_name}_config", {})
        config[self.key] = self.values[0].id
        dm.update_guild_data(interaction.guild_id, f"{self.system_name}_config", config)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Set {self.key} to {self.values[0].name}")
        await interaction.response.send_message(f"✅ Set **{self.key.replace('_',' ').title()}** to {self.values[0].mention}", ephemeral=True)

class _GenericChannelSelect(ui.ChannelSelect):
    def __init__(self, system_name: str, key: str, placeholder: str, channel_types=None):
        custom_id = f"sel_ch_{system_name}_{key}"
        super().__init__(
            placeholder=placeholder,
            channel_types=channel_types or [discord.ChannelType.text],
            min_values=1, max_values=1,
            custom_id=custom_id
        )
        self.system_name = system_name
        self.key = key

    async def callback(self, interaction: Interaction):
        config = dm.get_guild_data(interaction.guild_id, f"{self.system_name}_config", {})
        config[self.key] = self.values[0].id
        dm.update_guild_data(interaction.guild_id, f"{self.system_name}_config", config)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Set {self.key} to #{self.values[0].name}")
        await interaction.response.send_message(f"✅ Set **{self.key.replace('_',' ').title()}** to <#{self.values[0].id}>", ephemeral=True)

class _NumberModal(ui.Modal):
    value_input = ui.TextInput(label="Value", required=True, max_length=15)

    def __init__(self, system_name: str, key: str, label: str, min_v: int = 0, max_v: int = 999999999999):
        super().__init__(title=label)
        self.system_name = system_name
        self.key = key
        self.min_v, self.max_v = min_v, max_v
        self.value_input.label = label

    async def on_submit(self, interaction: Interaction):
        try:
            v = int(self.value_input.value)
            if v < self.min_v or v > self.max_v:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(f"❌ Enter a valid number.", ephemeral=True)
        config = dm.get_guild_data(interaction.guild_id, f"{self.system_name}_config", {})
        config[self.key] = v
        dm.update_guild_data(interaction.guild_id, f"{self.system_name}_config", config)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Set {self.key} to {v}")
        await interaction.response.send_message(f"✅ {self.key.replace('_',' ').title()} set to **{v}**.", ephemeral=True)

class _TextModal(ui.Modal):
    value_input = ui.TextInput(label="Value", style=discord.TextStyle.paragraph, required=True, max_length=1500)

    def __init__(self, system_name: str, key: str, label: str):
        super().__init__(title=label)
        self.system_name = system_name
        self.key = key
        self.value_input.label = label

    async def on_submit(self, interaction: Interaction):
        config = dm.get_guild_data(interaction.guild_id, f"{self.system_name}_config", {})
        config[self.key] = self.value_input.value
        dm.update_guild_data(interaction.guild_id, f"{self.system_name}_config", config)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Updated text field {self.key}")
        await interaction.response.send_message(f"✅ {self.key.replace('_',' ').title()} updated.", ephemeral=True)

def _picker_view(component: ui.Item) -> ui.View:
    v = ui.View(timeout=120)
    v.add_item(component)
    return v

# --- Specialized Panels ---

class VerificationConfigView(ConfigPanelView):
    def __init__(self, guild_id: int = 0):
        super().__init__("verification", guild_id)

    def create_embed(self, gid: int) -> discord.Embed:
        c = self.get_config(gid)
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

    @ui.button(label="Toggle System", emoji="✅", style=discord.ButtonStyle.success, row=0, custom_id="verify_btn_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(i.guild_id, c)
        log_panel_action(i.guild_id, i.user.id, f"Toggled verification to {c['enabled']}")
        await self.update_panel(i)

    @ui.button(label="Set Verified Role", emoji="🔢", style=discord.ButtonStyle.primary, row=0, custom_id="verify_btn_setv")
    async def set_v(self, i, b):
        await i.response.send_message("Select Role:", view=_picker_view(_GenericRoleSelect("verification", "verified_role_id", "Verified Role")), ephemeral=True)

    @ui.button(label="Set Unverified Role", emoji="🔒", style=discord.ButtonStyle.primary, row=0, custom_id="verify_btn_setuv")
    async def set_uv(self, i, b):
        await i.response.send_message("Select Role:", view=_picker_view(_GenericRoleSelect("verification", "unverified_role_id", "Unverified Role")), ephemeral=True)

    @ui.button(label="Set Verify Channel", emoji="📣", style=discord.ButtonStyle.primary, row=0, custom_id="verify_btn_setch")
    async def set_ch(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect("verification", "channel_id", "Verify Channel")), ephemeral=True)

    @ui.button(label="Min Account Age", emoji="⏱️", style=discord.ButtonStyle.secondary, row=1, custom_id="verify_btn_age")
    async def set_age(self, i, b):
        await i.response.send_modal(_NumberModal("verification", "min_account_age_days", "Min Age (Days)"))

    @ui.button(label="Toggle CAPTCHA", emoji="🧮", style=discord.ButtonStyle.secondary, row=1, custom_id="verify_btn_captcha")
    async def toggle_c(self, i, b):
        c = self.get_config(i.guild_id); c["captcha_enabled"] = not c.get("captcha_enabled", False); self.save_config(i.guild_id, c); await self.update_panel(i)

    @ui.button(label="Toggle Phone Gate", emoji="📱", style=discord.ButtonStyle.secondary, row=1, custom_id="verify_btn_phone")
    async def toggle_p(self, i, b):
        c = self.get_config(i.guild_id); c["phone_required"] = not c.get("phone_required", False); self.save_config(i.guild_id, c); await self.update_panel(i)

    @ui.button(label="Set Welcome DM", emoji="📩", style=discord.ButtonStyle.secondary, row=1, custom_id="verify_btn_dm")
    async def set_dm(self, i, b):
        await i.response.send_modal(_TextModal("verification", "welcome_dm", "Welcome DM Message"))

    @ui.button(label="View Log", emoji="📋", style=discord.ButtonStyle.secondary, row=2, custom_id="verify_btn_log")
    async def view_log(self, i, b):
        log = self.get_config(i.guild_id).get("verification_log", [])[-20:][::-1]
        msg = "\n".join([f"<t:{int(e['ts'])}:R> <@{e['user_id']}> ({e['method']})" for e in log]) or "No logs."
        await i.response.send_message(embed=discord.Embed(title="Verification Log", description=msg), ephemeral=True)

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="verify_btn_stats")
    async def stats(self, i, b):
        log = self.get_config(i.guild_id).get("verification_log", [])
        await i.response.send_message(f"📊 Total Verifications: {len(log)}", ephemeral=True)

    @ui.button(label="Reset Log", emoji="🗑️", style=discord.ButtonStyle.danger, row=2, custom_id="verify_btn_reset")
    async def reset(self, i, b):
        c = self.get_config(i.guild_id); c["verification_log"] = []; self.save_config(i.guild_id, c)
        log_panel_action(i.guild_id, i.user.id, "Reset verification log")
        await i.response.send_message("Log Reset", ephemeral=True)

    @ui.button(label="Re-verify All", emoji="🔁", style=discord.ButtonStyle.danger, row=2, custom_id="verify_btn_reverify")
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

class WelcomeConfigView(ConfigPanelView):
    def __init__(self, guild_id: int = 0):
        super().__init__("welcome", guild_id)

    def create_embed(self, gid: int) -> discord.Embed:
        c = self.get_config(gid)
        embed = discord.Embed(title="👋 Welcome & Leave Configuration", color=discord.Color.blue())
        embed.add_field(name="Welcome", value="✅ ON" if c.get("welcome_enabled", True) else "❌ OFF", inline=True)
        embed.add_field(name="Leave", value="✅ ON" if c.get("leave_enabled", True) else "❌ OFF", inline=True)
        embed.add_field(name="Welcome Ch", value=f"<#{c.get('welcome_channel')}>" if c.get('welcome_channel') else "_None_", inline=True)
        embed.add_field(name="Leave Ch", value=f"<#{c.get('leave_channel')}>" if c.get('leave_channel') else "_None_", inline=True)
        embed.add_field(name="Flags", value=f"Member#: {'✅' if c.get('show_member_number') else '❌'} | Age: {'✅' if c.get('show_account_age') else '❌'} | Ping: {'✅' if c.get('ping_on_welcome') else '❌'}", inline=False)
        return embed

    @ui.button(label="Toggle Welcome", style=discord.ButtonStyle.success, row=0, custom_id="wel_btn_tog_w")
    async def toggle_w(self, i, b):
        c = self.get_config(i.guild_id); c["welcome_enabled"] = not c.get("welcome_enabled", True); self.save_config(i.guild_id, c); await self.update_panel(i)

    @ui.button(label="Toggle Leave", style=discord.ButtonStyle.success, row=0, custom_id="wel_btn_tog_l")
    async def toggle_l(self, i, b):
        c = self.get_config(i.guild_id); c["leave_enabled"] = not c.get("leave_enabled", True); self.save_config(i.guild_id, c); await self.update_panel(i)

    @ui.button(label="Set Welcome Ch", style=discord.ButtonStyle.primary, row=0, custom_id="wel_btn_ch_w")
    async def set_ch_w(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect("welcome", "welcome_channel", "Welcome Channel")), ephemeral=True)

    @ui.button(label="Set Leave Ch", style=discord.ButtonStyle.primary, row=0, custom_id="wel_btn_ch_l")
    async def set_ch_l(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect("welcome", "leave_channel", "Leave Channel")), ephemeral=True)

    @ui.button(label="Edit Welcome Msg", emoji="✏️", style=discord.ButtonStyle.secondary, row=1, custom_id="wel_btn_edit_w")
    async def edit_w(self, i, b):
        await i.response.send_modal(_TextModal("welcome", "welcome_message", "Welcome Message Content"))

    @ui.button(label="Edit Leave Msg", emoji="✏️", style=discord.ButtonStyle.secondary, row=1, custom_id="wel_btn_edit_l")
    async def edit_l(self, i, b):
        await i.response.send_modal(_TextModal("welcome", "leave_message", "Leave Message Content"))

    @ui.button(label="Embed Color", emoji="🎨", style=discord.ButtonStyle.secondary, row=1, custom_id="wel_btn_color")
    async def set_color(self, i, b):
        await i.response.send_modal(_TextModal("welcome", "welcome_color", "Hex Color (e.g. #00ff00)"))

    @ui.button(label="Toggle Member#", emoji="👤", style=discord.ButtonStyle.secondary, row=2, custom_id="wel_btn_num")
    async def toggle_num(self, i, b):
        c = self.get_config(i.guild_id); c["show_member_number"] = not c.get("show_member_number", False); self.save_config(i.guild_id, c); await self.update_panel(i)

    @ui.button(label="Toggle Acc. Age", emoji="📅", style=discord.ButtonStyle.secondary, row=2, custom_id="wel_btn_age")
    async def toggle_age(self, i, b):
        c = self.get_config(i.guild_id); c["show_account_age"] = not c.get("show_account_age", False); self.save_config(i.guild_id, c); await self.update_panel(i)

    @ui.button(label="Toggle Ping", emoji="🔔", style=discord.ButtonStyle.secondary, row=2, custom_id="wel_btn_ping")
    async def toggle_ping(self, i, b):
        c = self.get_config(i.guild_id); c["ping_on_welcome"] = not c.get("ping_on_welcome", False); self.save_config(i.guild_id, c); await self.update_panel(i)

    @ui.button(label="Variable Ref", emoji="📋", style=discord.ButtonStyle.secondary, row=3, custom_id="wel_btn_vars")
    async def var_ref(self, i, b):
        await i.response.send_message("Variables:\n{user} {user.mention} {user.name} {user.id}\n{server} {server.membercount}\n{date} {time}", ephemeral=True)

    @ui.button(label="Test Welcome", emoji="🧪", style=discord.ButtonStyle.success, row=3, custom_id="wel_btn_test_w")
    async def test_w(self, i, b):
        from modules.welcome_leave import WelcomeLeaveSystem
        wl = WelcomeLeaveSystem(i.client)
        await wl.on_member_join(i.user)
        await i.response.send_message("Test Welcome Sent to your configured channel.", ephemeral=True)

    @ui.button(label="Test Leave", emoji="🧪", style=discord.ButtonStyle.success, row=3, custom_id="wel_btn_test_l")
    async def test_l(self, i, b):
        from modules.welcome_leave import WelcomeLeaveSystem
        wl = WelcomeLeaveSystem(i.client)
        await wl.on_member_remove(i.user)
        await i.response.send_message("Test Leave Sent to your configured channel.", ephemeral=True)

    @ui.button(label="Set Image URL", emoji="🖼️", style=discord.ButtonStyle.secondary, row=4, custom_id="wel_btn_img")
    async def set_img(self, i, b):
        await i.response.send_modal(_TextModal("welcome", "welcome_image", "Welcome Image URL"))

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=4, custom_id="wel_btn_stats")
    async def stats(self, i, b):
        c = self.get_config(i.guild_id)
        s = c.get("stats", {})
        await i.response.send_message(f"📊 Joins Today: {s.get('joins_today', 0)}\nLeaves Today: {s.get('leaves_today', 0)}", ephemeral=True)

class AntiRaidConfigView(ConfigPanelView):
    def __init__(self, guild_id: int = 0):
        super().__init__("antiraid", guild_id)

    def create_embed(self, gid: int) -> discord.Embed:
        c = self.get_config(gid)
        embed = discord.Embed(title="🛡️ Anti-Raid System", color=discord.Color.red() if c.get("enabled", True) else discord.Color.greyple())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Join Threshold", value=f"{c.get('mass_join_threshold', 10)}/{c.get('mass_join_window', 10)}s", inline=True)
        embed.add_field(name="Action", value=c.get("action", "lockdown").upper(), inline=True)
        embed.add_field(name="Filters", value=f"Age: {'ON' if c.get('age_filter_enabled') else 'OFF'} | Link: {'ON' if c.get('link_spam_enabled') else 'OFF'} | Inv: {'ON' if c.get('invite_filter_enabled') else 'OFF'}", inline=False)
        embed.add_field(name="Spam", value=f"Ment: {c.get('mention_threshold',5)} | Dup: {c.get('duplicate_threshold',3)}", inline=True)
        embed.add_field(name="Whitelist", value=f"{len(c.get('whitelist', []))} users", inline=True)
        return embed

    @ui.button(label="Toggle Anti-Raid", emoji="🛡️", style=discord.ButtonStyle.success, row=0, custom_id="ar_btn_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(i.guild_id, c); await self.update_panel(i)

    @ui.button(label="Set Join Threshold", emoji="👥", style=discord.ButtonStyle.primary, row=0, custom_id="ar_btn_thresh")
    async def set_thresh(self, i, b):
        await i.response.send_modal(_NumberModal("antiraid", "mass_join_threshold", "Joins (X)"))

    @ui.button(label="Set Trigger Action", emoji="⚡", style=discord.ButtonStyle.primary, row=0, custom_id="ar_btn_action")
    async def set_action(self, i, b):
        view = ui.View()
        select = ui.Select(placeholder="Select Action", options=[
            discord.SelectOption(label="Lockdown", value="lockdown"),
            discord.SelectOption(label="Kick", value="kick"),
            discord.SelectOption(label="Ban", value="ban"),
            discord.SelectOption(label="Mute", value="mute")
        ])
        async def callback(it):
            c = dm.get_guild_data(it.guild_id, "antiraid_config", {}); c["action"] = select.values[0]; dm.update_guild_data(it.guild_id, "antiraid_config", c); await it.response.send_message(f"Action set to {c['action']}", ephemeral=True)
        select.callback = callback; view.add_item(select); await i.response.send_message("Choose action:", view=view, ephemeral=True)

    @ui.button(label="View Raid Log", emoji="📋", style=discord.ButtonStyle.secondary, row=1, custom_id="ar_btn_log")
    async def view_log(self, i, b):
        log = self.get_config(i.guild_id).get("raid_log", [])[-10:][::-1]
        msg = "\n".join([f"<t:{int(e['ts'])}:R> {e['type']} -> {e['action']}" for e in log]) or "No raids."
        await i.response.send_message(embed=discord.Embed(title="Raid Log", description=msg), ephemeral=True)

    @ui.button(label="Whitelist User", emoji="✅", style=discord.ButtonStyle.secondary, row=1, custom_id="ar_btn_white_add")
    async def whitelist(self, i, b):
        await i.response.send_modal(_NumberModal("antiraid", "whitelist_add", "Add User ID to Whitelist"))

    @ui.button(label="View Whitelist", emoji="📜", style=discord.ButtonStyle.secondary, row=1, custom_id="ar_btn_white_v")
    async def v_white(self, i, b):
        w = self.get_config(i.guild_id).get("whitelist", [])
        await i.response.send_message(f"Whitelist: {', '.join([str(u) for u in w]) or 'Empty'}", ephemeral=True)

    @ui.button(label="Manual Lockdown", emoji="🔒", style=discord.ButtonStyle.danger, row=2, custom_id="ar_btn_lock")
    async def lockdown(self, i: Interaction, b):
        from modules.anti_raid import AntiRaidSystem
        ar = AntiRaidSystem(i.client)
        await ar._lockdown(i.guild)
        log_panel_action(i.guild_id, i.user.id, "Manual Lockdown")
        await i.response.send_message("🔒 Locked Down.", ephemeral=True)

    @ui.button(label="Unlock Server", emoji="🔓", style=discord.ButtonStyle.success, row=2, custom_id="ar_btn_unlock")
    async def unlock(self, i: Interaction, b):
        from modules.anti_raid import AntiRaidSystem
        ar = AntiRaidSystem(i.client)
        await ar.lift_lockdown(i.guild)
        log_panel_action(i.guild_id, i.user.id, "Lifted Lockdown")
        await i.response.send_message("🔓 Unlocked.", ephemeral=True)

    @ui.button(label="Set Min Age", emoji="👶", style=discord.ButtonStyle.secondary, row=2, custom_id="ar_btn_age")
    async def set_age(self, i, b):
        await i.response.send_modal(_NumberModal("antiraid", "min_account_age_days", "Min Age (Days)"))

    @ui.button(label="Toggle Link Filter", emoji="🔗", style=discord.ButtonStyle.secondary, row=3, custom_id="ar_btn_link")
    async def t_link(self, i, b):
        c = self.get_config(i.guild_id); c["link_spam_enabled"] = not c.get("link_spam_enabled", True); self.save_config(i.guild_id, c); await self.update_panel(i)

    @ui.button(label="Toggle Mention Filter", emoji="📣", style=discord.ButtonStyle.secondary, row=3, custom_id="ar_btn_ment")
    async def s_ment(self, i, b):
        c = self.get_config(i.guild_id); c["mention_filter_enabled"] = not c.get("mention_filter_enabled", True); self.save_config(i.guild_id, c); await self.update_panel(i)

    @ui.button(label="Toggle Duplicate Filter", emoji="💬", style=discord.ButtonStyle.secondary, row=3, custom_id="ar_btn_dup")
    async def s_dup(self, i, b):
        c = self.get_config(i.guild_id); c["duplicate_filter_enabled"] = not c.get("duplicate_filter_enabled", True); self.save_config(i.guild_id, c); await self.update_panel(i)

    @ui.button(label="Toggle Invite Filter", emoji="🌐", style=discord.ButtonStyle.secondary, row=4, custom_id="ar_btn_inv")
    async def t_inv(self, i, b):
        c = self.get_config(i.guild_id); c["invite_filter_enabled"] = not c.get("invite_filter_enabled", True); self.save_config(i.guild_id, c); await self.update_panel(i)

    @ui.button(label="Raid Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=4, custom_id="ar_btn_stats")
    async def r_stats(self, i, b):
        log = self.get_config(i.guild_id).get("raid_log", [])
        await i.response.send_message(f"Total Raids: {len(log)}", ephemeral=True)

    @ui.button(label="Silence Raid Alerts", emoji="🔕", style=discord.ButtonStyle.secondary, row=4, custom_id="ar_btn_silence")
    async def silence(self, i, b):
        await i.response.send_message("🔕 Notifications Silenced (Simulated).", ephemeral=True)

class GuardianConfigView(ConfigPanelView):
    def __init__(self, guild_id: int = 0):
        super().__init__("guardian", guild_id)

    def create_embed(self, gid: int) -> discord.Embed:
        c = self.get_config(gid)
        embed = discord.Embed(title="🛡️ Guardian System", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Response Levels", value=f"Tox: {c.get('toxicity_level','WARN')} | Scam: {c.get('scam_level','MUTE')} | Nuke: {c.get('nuke_level','BAN')}", inline=False)
        embed.add_field(name="Alert Channel", value=f"<#{c.get('alert_channel')}>" if c.get('alert_channel') else "_None_", inline=True)
        embed.add_field(name="Mass DM", value=f"{c.get('mass_dm_threshold',10)}/min", inline=True)
        return embed

    @ui.button(label="Toggle Guardian", emoji="⚔️", style=discord.ButtonStyle.success, row=0, custom_id="gua_btn_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(i.guild_id, c); await self.update_panel(i)

    @ui.button(label="Toxicity Filter", emoji="☣️", style=discord.ButtonStyle.primary, row=0, custom_id="gua_btn_tox")
    async def set_tox(self, i, b):
        await self._set_level(i, "toxicity_level")

    @ui.button(label="Scam Filter", emoji="🔗", style=discord.ButtonStyle.primary, row=0, custom_id="gua_btn_scam")
    async def set_scam(self, i, b):
        await self._set_level(i, "scam_level")

    @ui.button(label="Impersonation Det.", emoji="🎭", style=discord.ButtonStyle.primary, row=0, custom_id="gua_btn_imp")
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
            c = dm.get_guild_data(it.guild_id, "guardian_config", {}); c[key] = select.values[0]; dm.update_guild_data(it.guild_id, "guardian_config", c); await it.response.send_message(f"Set to {c[key]}", ephemeral=True)
        select.callback = callback; view.add_item(select); await i.response.send_message("Choose Level:", view=view, ephemeral=True)

    @ui.button(label="Mass DM Detection", emoji="📨", style=discord.ButtonStyle.secondary, row=1, custom_id="gua_btn_massdm")
    async def set_dm(self, i, b):
        await i.response.send_modal(_NumberModal("guardian", "mass_dm_threshold", "Messages per Minute"))

    @ui.button(label="Nuke Protection", emoji="💣", style=discord.ButtonStyle.secondary, row=1, custom_id="gua_btn_nuke")
    async def set_nuke(self, i, b):
        await self._set_level(i, "nuke_level")

    @ui.button(label="Bot Token Detection", emoji="🤖", style=discord.ButtonStyle.secondary, row=1, custom_id="gua_btn_token")
    async def set_token(self, i, b):
        c = self.get_config(i.guild_id); c["token_detection"] = not c.get("token_detection", True); self.save_config(i.guild_id, c); await self.update_panel(i)

    @ui.button(label="Malware Detection", emoji="📎", style=discord.ButtonStyle.secondary, row=2, custom_id="gua_btn_mal")
    async def set_mal(self, i, b):
        await self._set_level(i, "malware_level")

    @ui.button(label="Self-Bot Detection", emoji="⚡", style=discord.ButtonStyle.secondary, row=2, custom_id="gua_btn_self")
    async def set_self(self, i, b):
        await self._set_level(i, "selfbot_level")

    @ui.button(label="Guardian Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="gua_btn_stats")
    async def g_stats(self, i, b):
        log = self.get_config(i.guild_id).get("guardian_log", [])
        await i.response.send_message(f"Incidents: {len(log)}", ephemeral=True)

    @ui.button(label="View Guardian Log", emoji="📋", style=discord.ButtonStyle.secondary, row=3, custom_id="gua_btn_log")
    async def view_log(self, i, b):
        log = self.get_config(i.guild_id).get("guardian_log", [])[-15:][::-1]
        msg = "\n".join([f"<t:{int(e['ts'])}:R> {e['type']} - {e['action']}" for e in log]) or "No incidents."
        await i.response.send_message(embed=discord.Embed(title="Guardian Log", description=msg), ephemeral=True)

    @ui.button(label="Whitelist User", emoji="🔕", style=discord.ButtonStyle.secondary, row=3, custom_id="gua_btn_white")
    async def white(self, i, b):
        await i.response.send_modal(_NumberModal("guardian", "whitelist_add", "User ID"))

    @ui.button(label="View Whitelist", emoji="📜", style=discord.ButtonStyle.secondary, row=3, custom_id="gua_btn_white_v")
    async def v_white(self, i, b):
        w = self.get_config(i.guild_id).get("whitelist", [])
        await i.response.send_message(f"Whitelisted: {', '.join([str(u) for u in w]) or 'None'}", ephemeral=True)

    @ui.button(label="Configure Responses", emoji="🔧", style=discord.ButtonStyle.secondary, row=4, custom_id="gua_btn_resp")
    async def conf_resp(self, i, b):
        await i.response.send_message("Use individual toggle buttons to set levels.", ephemeral=True)

    @ui.button(label="Test Guardian", emoji="🧪", style=discord.ButtonStyle.success, row=4, custom_id="gua_btn_test")
    async def test(self, i, b):
        log_panel_action(i.guild_id, i.user.id, "Test Guardian Alert")
        await i.response.send_message("🧪 Test alert sent to logs.", ephemeral=True)

    @ui.button(label="Set Alert Channel", emoji="📣", style=discord.ButtonStyle.primary, row=4, custom_id="gua_btn_ch")
    async def set_ch(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect("guardian", "alert_channel", "Alert Channel")), ephemeral=True)

    @ui.button(label="Reset Rules", emoji="🔄", style=discord.ButtonStyle.danger, row=4, custom_id="gua_btn_reset")
    async def reset(self, i, b):
        c = self.get_config(i.guild_id); c.clear(); self.save_config(i.guild_id, c); await i.response.send_message("Rules Reset.", ephemeral=True)

class WelcomeDMConfigView(ConfigPanelView):
    def __init__(self, guild_id: int = 0):
        super().__init__("welcomedm", guild_id)

    def create_embed(self, gid: int) -> discord.Embed:
        c = self.get_config(gid)
        embed = discord.Embed(title="📩 Welcome DM Configuration", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ ON" if c.get("enabled", True) else "❌ OFF", inline=True)
        embed.add_field(name="DM Message", value="✏️ Set" if c.get("dm_message") else "_Default_", inline=True)
        embed.add_field(name="Help Ping Role", value=f"<@&{c.get('help_ping_role')}>" if c.get('help_ping_role') else "_None_", inline=True)
        embed.add_field(name="Buttons", value=", ".join(c.get("enabled_buttons", ["verify", "rules"])), inline=False)
        return embed

    @ui.button(label="Toggle DMs", emoji="📩", style=discord.ButtonStyle.success, row=0, custom_id="wdm_btn_tog")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(i.guild_id, c); await self.update_panel(i)

    @ui.button(label="Edit DM Message", emoji="✏️", style=discord.ButtonStyle.primary, row=0, custom_id="wdm_btn_edit")
    async def edit_msg(self, i, b):
        await i.response.send_modal(_TextModal("welcomedm", "dm_message", "Welcome DM Embed Content"))

    @ui.button(label="Configure Buttons", emoji="🔘", style=discord.ButtonStyle.primary, row=0, custom_id="wdm_btn_btns")
    async def conf_btns(self, i, b):
        view = ui.View()
        select = ui.Select(placeholder="Select Enabled Buttons", min_values=1, max_values=8, options=[
            discord.SelectOption(label="Verify Now", value="verify"),
            discord.SelectOption(label="Read Rules", value="rules"),
            discord.SelectOption(label="Pick Roles", value="roles"),
            discord.SelectOption(label="Open Ticket", value="ticket"),
            discord.SelectOption(label="Apply Staff", value="apply"),
            discord.SelectOption(label="Get Help", value="help"),
            discord.SelectOption(label="Server Info", value="info"),
            discord.SelectOption(label="Opt Out", value="optout")
        ])
        async def callback(it):
            c = dm.get_guild_data(it.guild_id, "welcomedm_config", {}); c["enabled_buttons"] = select.values; dm.update_guild_data(it.guild_id, "welcomedm_config", c); await it.response.send_message("Buttons Configured.", ephemeral=True)
        select.callback = callback; view.add_item(select); await i.response.send_message("Choose buttons to show:", view=view, ephemeral=True)

    @ui.button(label="Set DM Color", emoji="🎨", style=discord.ButtonStyle.secondary, row=1, custom_id="wdm_btn_color")
    async def set_color(self, i, b):
        await i.response.send_modal(_TextModal("welcomedm", "dm_color", "Hex Color Code"))

    @ui.button(label="Test DM", emoji="🧪", style=discord.ButtonStyle.success, row=1, custom_id="wdm_btn_test")
    async def test_dm(self, i, b):
        from modules.welcome_dm import WelcomeDMSystem
        wdm = WelcomeDMSystem(i.client)
        await wdm.send_welcome_dm(i.user)
        await i.response.send_message("Test DM Sent to your inbox.", ephemeral=True)

    @ui.button(label="DM Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=1, custom_id="wdm_btn_stats")
    async def dm_stats(self, i, b):
        await i.response.send_message("📊 DM System Stats (Simulated).", ephemeral=True)

    @ui.button(label="Opted-Out Users", emoji="🚫", style=discord.ButtonStyle.secondary, row=2, custom_id="wdm_btn_optout")
    async def view_opt(self, i, b):
        await i.response.send_message("📜 List of Opted-Out users (Simulated).", ephemeral=True)

    @ui.button(label="Edit Info Content", emoji="✏️", style=discord.ButtonStyle.secondary, row=2, custom_id="wdm_btn_info")
    async def edit_info(self, i, b):
        await i.response.send_modal(_TextModal("welcomedm", "info_content", "Server Info Button Content"))

    @ui.button(label="Help Ping Role", emoji="🔗", style=discord.ButtonStyle.primary, row=2, custom_id="wdm_btn_ping")
    async def set_ping(self, i, b):
        await i.response.send_message("Select Role:", view=_picker_view(_GenericRoleSelect("welcomedm", "help_ping_role", "Help Ping Role")), ephemeral=True)

    @ui.button(label="Set Apply Redirect", emoji="⚙️", style=discord.ButtonStyle.primary, row=3, custom_id="wdm_btn_apply")
    async def set_apply(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect("welcomedm", "apply_channel", "Apply Channel")), ephemeral=True)

class TicketsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int = 0):
        super().__init__("tickets", guild_id)

    def create_embed(self, gid: int) -> discord.Embed:
        c = self.get_config(gid)
        embed = discord.Embed(title="🎫 Ticket System Configuration", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Staff Role", value=f"<@&{c.get('staff_role_id')}>" if c.get('staff_role_id') else "_None_", inline=True)
        embed.add_field(name="Category", value=f"<#{c.get('category_id')}>" if c.get('category_id') else "_None_", inline=True)
        embed.add_field(name="Log Channel", value=f"<#{c.get('log_channel_id')}>" if c.get('log_channel_id') else "_None_", inline=True)
        embed.add_field(name="Max/User", value=str(c.get("max_per_user", 1)), inline=True)
        embed.add_field(name="Auto-Close", value=f"{c.get('auto_close_hours',0)}h", inline=True)
        embed.add_field(name="Open Tickets", value=str(len(c.get("open_tickets", {}))), inline=True)
        return embed

    @ui.button(label="View Open", emoji="🎫", style=discord.ButtonStyle.secondary, row=0, custom_id="tk_btn_view")
    async def view_open(self, i, b):
        c = self.get_config(i.guild_id)
        msg = "\n".join([f"<@{u}>: {n} open" for u, n in c.get("open_tickets", {}).items()]) or "No open tickets."
        await i.response.send_message(embed=discord.Embed(title="Open Tickets", description=msg), ephemeral=True)

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=0, custom_id="tk_btn_stats")
    async def stats(self, i, b):
        c = self.get_config(i.guild_id)
        s = c.get("stats", {})
        await i.response.send_message(f"📊 Total Opened: {s.get('total',0)}\nTotal Closed: {s.get('closed',0)}", ephemeral=True)

    @ui.button(label="Set Staff Role", emoji="⚙️", style=discord.ButtonStyle.primary, row=0, custom_id="tk_btn_staff")
    async def set_staff(self, i, b):
        await i.response.send_message("Select Role:", view=_picker_view(_GenericRoleSelect("tickets", "staff_role_id", "Staff Role")), ephemeral=True)

    @ui.button(label="Set Category", emoji="📁", style=discord.ButtonStyle.primary, row=0, custom_id="tk_btn_cat")
    async def set_cat(self, i, b):
        await i.response.send_message("Select Category:", view=_picker_view(_GenericChannelSelect("tickets", "category_id", "Ticket Category", [discord.ChannelType.category])), ephemeral=True)

    @ui.button(label="Set Log Channel", emoji="📣", style=discord.ButtonStyle.primary, row=1, custom_id="tk_btn_log")
    async def set_log(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect("tickets", "log_channel_id", "Log Channel")), ephemeral=True)

    @ui.button(label="Max Tickets", emoji="🔢", style=discord.ButtonStyle.secondary, row=1, custom_id="tk_btn_max")
    async def set_max(self, i, b):
        await i.response.send_modal(_NumberModal("tickets", "max_per_user", "Max Tickets per User"))

    @ui.button(label="Auto-Close", emoji="⏰", style=discord.ButtonStyle.secondary, row=1, custom_id="tk_btn_auto")
    async def set_auto(self, i, b):
        await i.response.send_modal(_NumberModal("tickets", "auto_close_hours", "Inactivity Hours"))

    @ui.button(label="Close All", emoji="🗑️", style=discord.ButtonStyle.danger, row=1, custom_id="tk_btn_clear")
    async def close_all(self, i, b):
        class ClearConfirm(ui.Modal, title="Confirm Bulk Close"):
            check = ui.TextInput(label="Type 'CLOSE ALL' to confirm")
            async def on_submit(self, it):
                if self.check.value == "CLOSE ALL":
                    c = dm.get_guild_data(it.guild_id, "tickets_config", {})
                    c["open_tickets"] = {}
                    dm.update_guild_data(it.guild_id, "tickets_config", c)
                    log_panel_action(it.guild_id, it.user.id, "Bulk closed all tickets")
                    await it.response.send_message("✅ All tickets closed in database.", ephemeral=True)
                else: await it.response.send_message("❌ Confirmation failed.", ephemeral=True)
        await i.response.send_modal(ClearConfirm())

    @ui.button(label="View Transcripts", emoji="📋", style=discord.ButtonStyle.secondary, row=2, custom_id="tk_btn_trans")
    async def v_trans(self, i, b):
        await i.response.send_message("📋 Please check the log channel for transcripts.", ephemeral=True)

    @ui.button(label="Panel Buttons", emoji="🔘", style=discord.ButtonStyle.secondary, row=2, custom_id="tk_btn_btns")
    async def c_btns(self, i, b):
        await i.response.send_message("🔘 Interactive button config is handled via core code.", ephemeral=True)

    @ui.button(label="Customize Embed", emoji="🎨", style=discord.ButtonStyle.secondary, row=2, custom_id="tk_btn_embed")
    async def c_embed(self, i, b):
        await i.response.send_modal(_TextModal("tickets", "ticket_embed", "Panel Embed Settings"))

    @ui.button(label="Ticket Types", emoji="🏷️", style=discord.ButtonStyle.secondary, row=2, custom_id="tk_btn_types")
    async def c_types(self, i, b):
        await i.response.send_modal(_TextModal("tickets", "ticket_types", "Ticket Types (CSV)"))

    @ui.button(label="Toggle Opener DM", emoji="📩", style=discord.ButtonStyle.secondary, row=3, custom_id="tk_btn_dm")
    async def t_dm(self, i, b):
        c = self.get_config(i.guild_id); c["opener_dm"] = not c.get("opener_dm", True); self.save_config(i.guild_id, c); await self.update_panel(i)

class EconomyConfigView(ConfigPanelView):
    def __init__(self, guild_id: int = 0):
        super().__init__("economy", guild_id)

    def create_embed(self, gid: int) -> discord.Embed:
        c = self.get_config(gid)
        embed = discord.Embed(title="💰 Economy System", color=discord.Color.gold())
        embed.add_field(name="Currency", value=c.get("currency_name", "coins"), inline=True)
        embed.add_field(name="Daily", value=str(c.get("daily_reward", 100)), inline=True)
        return embed

    @ui.button(label="Set Daily", style=discord.ButtonStyle.primary, custom_id="eco_btn_daily")
    async def set_daily(self, i, b):
        await i.response.send_modal(_NumberModal("economy", "daily_reward", "Daily Reward"))

class TicketOpenButton(ui.View):
    def __init__(self): super().__init__(timeout=None)
    @ui.button(label="🎫 Open Ticket", style=discord.ButtonStyle.success, custom_id="ticket_open_v2")
    async def open(self, i, b):
        from modules.tickets import TicketModal
        await i.response.send_modal(TicketModal(i.client))

class TicketCloseButton(ui.View):
    def __init__(self): super().__init__(timeout=None)
    @ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_close_v2")
    async def close(self, i, b):
        await i.response.send_message("🔒 Closing ticket in 10 seconds...", ephemeral=False)
        await asyncio.sleep(10)
        try: await i.channel.delete()
        except: pass

# --- Generic Config ---

SYSTEM_METADATA = {
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

class GenericConfigPanelView(ConfigPanelView):
    def __init__(self, system_name: str, fields: List[Dict[str, Any]], guild_id: int = 0):
        super().__init__(system_name, guild_id)
        self.fields = fields
        for field in self.fields:
            btn = ui.Button(label=f"Set {field['name']}", style=discord.ButtonStyle.secondary, custom_id=f"btn_{system_name}_{field['key']}")
            btn.callback = self.create_callback(field)
            self.add_item(btn)

    def create_callback(self, field):
        async def callback(interaction: Interaction):
            await interaction.response.send_modal(_TextModal(self.system_name, field["key"], field["name"]))
        return callback

    def create_embed(self, gid: int) -> discord.Embed:
        config = self.get_config(gid)
        embed = discord.Embed(title=f"⚙️ {self.system_name.title()} Configuration", color=discord.Color.blue())
        for field in self.fields:
            key = field["key"]
            name = field["name"]
            value = config.get(key, field.get("default", "Not Set"))
            embed.add_field(name=name, value=str(value), inline=True)
        return embed

# --- Registry ---

SPECIALIZED_VIEWS = {
    "verification": VerificationConfigView,
    "welcome": WelcomeConfigView,
    "welcomedm": WelcomeDMConfigView,
    "antiraid": AntiRaidConfigView,
    "guardian": GuardianConfigView,
    "tickets": TicketsConfigView,
    "economy": EconomyConfigView
}

def get_config_panel(guild_id: int, system: str) -> Optional[ui.View]:
    system_key = system.lower().replace("_", "")
    if system_key in SPECIALIZED_VIEWS: return SPECIALIZED_VIEWS[system_key](guild_id)
    if system_key in SYSTEM_METADATA: return GenericConfigPanelView(system_key, SYSTEM_METADATA[system_key], guild_id)
    return None

async def handle_config_panel_command(message: discord.Message, system: str):
    view = get_config_panel(message.guild.id, system)
    if not view: return await message.channel.send(f"❌ System '{system}' not found.")
    await message.channel.send(embed=view.create_embed(message.guild.id), view=view)

def register_all_persistent_views(bot: discord.Client):
    bot.add_view(VerificationConfigView(0))
    bot.add_view(WelcomeConfigView(0))
    bot.add_view(WelcomeDMConfigView(0))
    bot.add_view(AntiRaidConfigView(0))
    bot.add_view(GuardianConfigView(0))
    bot.add_view(TicketsConfigView(0))
    bot.add_view(EconomyConfigView(0))
    bot.add_view(TicketOpenButton())
    bot.add_view(TicketCloseButton())
    from modules.tickets import TicketControlView
    bot.add_view(TicketControlView(0))
    for system_key, fields in SYSTEM_METADATA.items():
        bot.add_view(GenericConfigPanelView(system_key, fields))
    logger.info("All persistent config panels registered.")
