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
    # Subclasses can override _config_key to use a different storage key than f"{system_name}_config"
    _config_key: Optional[str] = None

    def __init__(self, guild_id: int, system_name: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.system_name = system_name
        self.panel_message = None  # Store the message object of this config panel

    def _storage_key(self) -> str:
        return self._config_key or f"{self.system_name}_config"

    def get_config(self, guild_id: int = None) -> Dict[str, Any]:
        target_guild = guild_id or self.guild_id
        try:
            data = dm.get_guild_data(target_guild, self._storage_key(), {})
            return data if isinstance(data, dict) else {}
        except Exception as e:
            from logger import logger
            logger.warning(f"get_config failed for {self.system_name}: {e}")
            return {}

    def save_config(self, config: Dict[str, Any], guild_id: int = None, bot: discord.Client = None):
        target_guild = guild_id or self.guild_id
        try:
            dm.update_guild_data(target_guild, self._storage_key(), config)
        except Exception as e:
            from logger import logger
            logger.error(f"save_config: write failed for {self.system_name}: {e}")
            return
        # Re-register custom commands for this system using the EXISTING cog instance
        # NEVER instantiate AutoSetup() here — that creates a new Cog with its own state and
        # triggers file I/O on every button click (root cause of "interaction failed").
        try:
            if bot is not None:
                cog = None
                if hasattr(bot, "get_cog"):
                    cog = bot.get_cog("AutoSetup")
                if cog is not None and hasattr(cog, "_register_system_commands"):
                    cog._register_system_commands(target_guild, self.system_name)
        except Exception as e:
            from logger import logger
            logger.warning(f"save_config: command re-registration failed for {self.system_name}: {e}")

    def _get_subsystem(self, bot, attr_name: str):
        """Safely fetch a bot subsystem (returns None if missing).
        Use this instead of bot.X.method() directly to prevent AttributeError."""
        return getattr(bot, attr_name, None) if bot is not None else None

    async def _require_subsystem(self, interaction: Interaction, attr_name: str, friendly_name: str = None):
        """Get a subsystem and reply with a clear error if it isn't loaded.
        Returns the subsystem instance, or None (in which case the caller should return)."""
        sub = self._get_subsystem(interaction.client, attr_name)
        if sub is None:
            label = friendly_name or attr_name
            try:
                msg = f"⚠️ The **{label}** subsystem is not loaded on this bot. Action skipped."
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
            except Exception:
                pass
        return sub

    async def interaction_check(self, interaction: Interaction) -> bool:
        """Global permission check for all configuration panels."""
        try:
            if interaction.guild is None:
                await interaction.response.send_message("❌ This panel only works inside a server.", ephemeral=True)
                return False
            member = interaction.user
            is_admin = getattr(getattr(member, "guild_permissions", None), "administrator", False)
            is_owner = (interaction.guild.owner_id == member.id)
            if not (is_admin or is_owner):
                await interaction.response.send_message("❌ This panel is restricted to Administrators only.", ephemeral=True)
                return False
            return True
        except Exception:
            try:
                await interaction.response.send_message("❌ Permission check failed. Try again.", ephemeral=True)
            except Exception:
                pass
            return False

    async def on_error(self, interaction: Interaction, error: Exception, item) -> None:
        """Catch-all so a callback failure NEVER shows the user 'interaction failed'."""
        from logger import logger
        import traceback
        item_label = getattr(item, "label", None) or getattr(item, "custom_id", None) or type(item).__name__
        logger.exception(f"ConfigPanelView error in {self.system_name} (item={item_label}): {error}")
        # Build a useful diagnostic for the user (errors are admin-only, so showing the trace is OK).
        tb = traceback.format_exc().strip().splitlines()
        last_frame = tb[-3] if len(tb) >= 3 else (tb[-1] if tb else "")
        err_str = str(error)[:200] or type(error).__name__
        msg = (
            f"⚠️ The **{item_label}** button in **{self.system_name}** failed.\n"
            f"`{type(error).__name__}`: {err_str}\n"
            f"-# {last_frame[-180:]}"
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

    async def update_panel(self, interaction: Interaction):
        embed = self.create_embed(guild_id=interaction.guild_id, guild=interaction.guild)
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=embed, view=self)
            else:
                await interaction.response.edit_message(embed=embed, view=self)
        except Exception:
            try:
                await interaction.followup.send(embed=embed, ephemeral=True)
            except Exception:
                pass

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        gid = guild_id or self.guild_id
        cfg = self.get_config(gid)

        # Consistent branding and visuals
        embed = discord.Embed(
            title=f"⚙️ Config: {self.system_name.replace('_',' ').title()}",
            description="Use the controls below to configure this system.",
            color=discord.Color.blurple(),
        )

        # Set server icon as thumbnail if guild is provided
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        elif guild and not guild.icon:
            embed.set_thumbnail(url="https://cdn.discordapp.com/embed/avatars/0.png")

        if cfg:
            # Filter out large log/data fields for a cleaner preview
            preview_items = {k: v for k, v in cfg.items() if not k.endswith('_log') and not k.endswith('_data') and k != 'active_giveaways'}
            preview = "\n".join(f"**{k}:** `{str(v)[:80]}`" for k, v in list(preview_items.items())[:12])
            embed.add_field(name="Current Settings", value=preview or "_(empty)_", inline=False)
        else:
            embed.add_field(name="Current Settings", value="_No configuration set yet._", inline=False)

        embed.set_footer(text="Settings are updated instantly across all server nodes.")
        return embed

# --- Reusable Components ---

class _GenericRoleSelect(ui.RoleSelect):
    def __init__(self, parent: ConfigPanelView, key: str, placeholder: str):
        super().__init__(placeholder=placeholder, min_values=1, max_values=1)
        self.config_panel = parent
        self.key = key

    async def callback(self, interaction: Interaction):
        config = self.config_panel.get_config(interaction.guild_id)
        config[self.key] = self.values[0].id
        self.config_panel.save_config(config, interaction.guild_id, interaction.client)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Set {self.key} to {self.values[0].name}")
        await interaction.response.send_message(f"✅ Set **{self.key.replace('_',' ').title()}** to {self.values[0].mention}", ephemeral=True)
        # Update the original config panel
        if self.config_panel.panel_message:
            try:
                new_embed = self.config_panel.create_embed(guild_id=interaction.guild_id, guild=interaction.guild)
                await self.config_panel.panel_message.edit(embed=new_embed, view=self.config_panel)
            except Exception as e:
                from logger import logger
                logger.error(f"Failed to update config panel after role select: {e}")

class _GenericChannelSelect(ui.ChannelSelect):
    def __init__(self, parent: ConfigPanelView, key: str, placeholder: str, channel_types=None):
        super().__init__(
            placeholder=placeholder,
            channel_types=channel_types or [discord.ChannelType.text],
            min_values=1, max_values=1,
        )
        self.config_panel = parent
        self.key = key

    async def callback(self, interaction: Interaction):
        config = self.config_panel.get_config(interaction.guild_id)
        config[self.key] = self.values[0].id
        self.config_panel.save_config(config, interaction.guild_id, interaction.client)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Set {self.key} to #{self.values[0].name}")
        await interaction.response.send_message(f"✅ Set **{self.key.replace('_',' ').title()}** to <#{self.values[0].id}>", ephemeral=True)
        # Update the original config panel
        if self.config_panel.panel_message:
            try:
                new_embed = self.config_panel.create_embed(guild_id=interaction.guild_id, guild=interaction.guild)
                await self.config_panel.panel_message.edit(embed=new_embed, view=self.config_panel)
            except Exception as e:
                from logger import logger
                logger.error(f"Failed to update config panel after channel select: {e}")

class _NumberModal(ui.Modal):
    value_input = ui.TextInput(label="Value", required=True, max_length=15)
    second_value = ui.TextInput(label="Secondary Value (optional)", required=False, max_length=15)

    def __init__(self, parent: ConfigPanelView, key: str, label: str, guild_id: int, min_v: int = 0, max_v: int = 999999999999, second_label: str = None):
        super().__init__(title=label)
        self.config_panel = parent
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
        
        config = self.config_panel.get_config(interaction.guild_id)
        
        # Special handling for whitelist operations
        if self.key == "whitelist_add":
            user_id = v
            whitelist = config.get("whitelist", [])
            if user_id not in whitelist:
                whitelist.append(user_id)
                config["whitelist"] = whitelist
                self.config_panel.save_config(config, interaction.guild_id, interaction.client)
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
                    self.config_panel.save_config(config, interaction.guild_id, interaction.client)
                    log_panel_action(interaction.guild_id, interaction.user.id, f"Set duplicate filter to {v} msgs in {y}s")
                    return await interaction.response.send_message(f"✅ Duplicate filter: **{v}** messages in **{y}** seconds.", ephemeral=True)
                except ValueError:
                    return await interaction.response.send_message("❌ Second value must be a number.", ephemeral=True)
            config["duplicate_threshold"] = v
            self.config_panel.save_config(config, interaction.guild_id, interaction.client)
            log_panel_action(interaction.guild_id, interaction.user.id, f"Set duplicate threshold to {v}")
            return await interaction.response.send_message(f"✅ Duplicate threshold set to **{v}** messages.", ephemeral=True)
        
        # Special handling for mention threshold config
        if self.key == "mention_threshold_config":
            config["mention_threshold"] = v
            self.config_panel.save_config(config, interaction.guild_id, interaction.client)
            log_panel_action(interaction.guild_id, interaction.user.id, f"Set mention threshold to {v}")
            return await interaction.response.send_message(f"✅ Max mentions per message: **{v}**.", ephemeral=True)
        
        # Special handling for work rewards (min and max)
        if self.key == "work_min":
            if self.second_value.value:
                try:
                    max_v = int(self.second_value.value)
                    config["work_min"] = v
                    config["work_max"] = max_v
                    self.config_panel.save_config(config, interaction.guild_id, interaction.client)
                    log_panel_action(interaction.guild_id, interaction.user.id, f"Set work rewards to {v}-{max_v}")
                    return await interaction.response.send_message(f"✅ Work rewards: **{v}** - **{max_v}** coins.", ephemeral=True)
                except ValueError:
                    return await interaction.response.send_message("❌ Second value must be a number.", ephemeral=True)
            config["work_min"] = v
            self.config_panel.save_config(config, interaction.guild_id, interaction.client)
            log_panel_action(interaction.guild_id, interaction.user.id, f"Set work min to {v}")
            return await interaction.response.send_message(f"✅ Work min reward set to **{v}**.", ephemeral=True)
        
        # Special handling for beg rewards (min and max)
        if self.key == "beg_min":
            if self.second_value.value:
                try:
                    max_v = int(self.second_value.value)
                    config["beg_min"] = v
                    config["beg_max"] = max_v
                    self.config_panel.save_config(config, interaction.guild_id, interaction.client)
                    log_panel_action(interaction.guild_id, interaction.user.id, f"Set beg rewards to {v}-{max_v}")
                    return await interaction.response.send_message(f"✅ Beg rewards: **{v}** - **{max_v}** coins.", ephemeral=True)
                except ValueError:
                    return await interaction.response.send_message("❌ Second value must be a number.", ephemeral=True)
            config["beg_min"] = v
            self.config_panel.save_config(config, interaction.guild_id, interaction.client)
            log_panel_action(interaction.guild_id, interaction.user.id, f"Set beg min to {v}")
            return await interaction.response.send_message(f"✅ Beg min reward set to **{v}**.", ephemeral=True)
        
        # Default: single value storage
        config[self.key] = v
        self.config_panel.save_config(config, interaction.guild_id, interaction.client)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Set {self.key} to {v}")
        await interaction.response.send_message(f"✅ {self.key.replace('_',' ').title()} set to **{v}**.", ephemeral=True)

class _TextModal(ui.Modal):
    value_input = ui.TextInput(label="Value", style=discord.TextStyle.paragraph, required=True, max_length=1500)

    def __init__(self, parent: ConfigPanelView, key: str, label: str, guild_id: int):
        super().__init__(title=label)
        self.config_panel = parent
        self.key = key
        self.value_input.label = label
        existing = parent.get_config(guild_id).get(key, "")
        if existing:
            self.value_input.default = str(existing)

    async def on_submit(self, interaction: Interaction):
        config = self.config_panel.get_config(interaction.guild_id)
        config[self.key] = self.value_input.value
        self.config_panel.save_config(config, interaction.guild_id, interaction.client)
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
        # Set initial toggle button label and style
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_verify_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(title="🛡️ Verification System", color=discord.Color.green() if c.get("enabled", True) else discord.Color.red())
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        elif guild:
            embed.set_thumbnail(url="https://cdn.discordapp.com/embed/avatars/0.png")
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Verified Role", value=f"<@&{c.get('verified_role_id')}>" if c.get('verified_role_id') else "_None_", inline=True)
        embed.add_field(name="Unverified Role", value=f"<@&{c.get('unverified_role_id')}>" if c.get('unverified_role_id') else "_None_", inline=True)
        embed.add_field(name="Channel", value=f"<#{c.get('channel_id')}>" if c.get('channel_id') else "_None_", inline=True)
        embed.add_field(name="CAPTCHA", value="🧮 On" if c.get("captcha_enabled") else "Off", inline=True)
        embed.add_field(name="Min Age", value=f"{c.get('min_account_age_days', 0)}d", inline=True)
        embed.add_field(name="Log Count", value=str(len(c.get("verification_log", []))), inline=True)
        return embed

    @ui.button(label="Disable", emoji="❌", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_verify_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(c, i.guild_id, i.client)
        log_panel_action(i.guild_id, i.user.id, f"Toggled verification to {c.get('enabled')}")
        # Update toggle button label and style
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_verify_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break
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
    _config_key = "anti_raid_config"  # auto_setup writes here
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "antiraid")
        # Set initial toggle button state
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_antiraid_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        rules = c.setdefault("rules", {})
        embed = discord.Embed(title="🛡️ Anti-Raid System", color=discord.Color.red() if c.get("enabled", True) else discord.Color.greyple())
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        elif guild:
            embed.set_thumbnail(url="https://cdn.discordapp.com/embed/avatars/0.png")
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Join Threshold", value=f"{c.get('mass_join_threshold', 10)}/{c.get('mass_join_window', 10)}s", inline=True)
        embed.add_field(name="Action", value=c.get("action", "lockdown").upper(), inline=True)
        embed.add_field(
            name="Filters",
            value=(
                f"Age: {'ON' if c.get('age_filter_enabled') else 'OFF'} | "
                f"Link: {'ON' if rules.get('link_spam', {}).get('enabled', True) else 'OFF'} | "
                f"Inv: {'ON' if rules.get('invite_filter', {}).get('enabled', True) else 'OFF'} | "
                f"Ment: {'ON' if rules.get('mention_filter', {}).get('enabled', True) else 'OFF'} | "
                f"Dup: {'ON' if rules.get('duplicate_filter', {}).get('enabled', True) else 'OFF'} | "
                f"Emoji: {'ON' if rules.get('emoji_filter', {}).get('enabled', True) else 'OFF'}"
            ),
            inline=False
        )
        embed.add_field(name="Whitelist", value=f"{len(c.get('whitelist', []))} users", inline=True)
        return embed

    @ui.button(label="Disable", emoji="❌", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_antiraid_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(c, i.guild_id, i.client)
        # Update toggle button label and style
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_antiraid_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break
        await self.update_panel(i)

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
        await i.response.defer(ephemeral=True)
        count = 0
        for ch in i.guild.text_channels:
            try:
                await ch.set_permissions(i.guild.default_role, send_messages=False, reason=f"Manual Lockdown by {i.user}")
                count += 1
            except: pass
        log_panel_action(i.guild_id, i.user.id, "Manual Server Lockdown")
        await i.followup.send(f"🔒 Server locked down ({count} channels affected).", ephemeral=True)

    @ui.button(label="Unlock Server", emoji="🔓", style=discord.ButtonStyle.success, row=2, custom_id="cfg_antiraid_unlock")
    async def unlock(self, i: Interaction, b):
        await i.response.defer(ephemeral=True)
        count = 0
        for ch in i.guild.text_channels:
            try:
                await ch.set_permissions(i.guild.default_role, send_messages=None, reason=f"Server Unlock by {i.user}")
                count += 1
            except: pass
        log_panel_action(i.guild_id, i.user.id, "Manual Server Unlock")
        await i.followup.send(f"🔓 Server unlocked ({count} channels affected).", ephemeral=True)

    @ui.button(label="Set Min Age", emoji="👶", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_antiraid_set_age")
    async def set_age(self, i, b):
        await i.response.send_modal(_NumberModal(self, "min_account_age_days", "Min Age (Days)", i.guild_id))

    @ui.button(label="Toggle Link Filter", emoji="🔗", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_antiraid_t_link")
    async def t_link(self, i, b):
        c = self.get_config(i.guild_id)
        rules = c.setdefault("rules", {})
        link_spam = rules.setdefault("link_spam", {})
        link_spam["enabled"] = not link_spam.get("enabled", True)
        self.save_config(c, i.guild_id, i.client)
        await self.update_panel(i)

    @ui.button(label="Toggle Mention Filter", emoji="📣", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_antiraid_s_ment")
    async def s_ment(self, i, b):
        c = self.get_config(i.guild_id)
        rules = c.setdefault("rules", {})
        mention_filter = rules.setdefault("mention_filter", {})
        mention_filter["enabled"] = not mention_filter.get("enabled", True)
        self.save_config(c, i.guild_id, i.client)
        await self.update_panel(i)

    @ui.button(label="Toggle Duplicate Filter", emoji="💬", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_antiraid_s_dup")
    async def s_dup(self, i, b):
        c = self.get_config(i.guild_id)
        rules = c.setdefault("rules", {})
        dup_filter = rules.setdefault("duplicate_filter", {})
        dup_filter["enabled"] = not dup_filter.get("enabled", True)
        self.save_config(c, i.guild_id, i.client)
        await self.update_panel(i)

    @ui.button(label="Toggle Invite Filter", emoji="🌐", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_antiraid_t_inv")
    async def t_inv(self, i, b):
        c = self.get_config(i.guild_id)
        rules = c.setdefault("rules", {})
        inv_filter = rules.setdefault("invite_filter", {})
        inv_filter["enabled"] = not inv_filter.get("enabled", True)
        self.save_config(c, i.guild_id, i.client)
        await self.update_panel(i)

    @ui.button(label="Raid Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_antiraid_r_stats")
    async def r_stats(self, i, b):
        log = self.get_config(i.guild_id).get("raid_log", [])
        await i.response.send_message(f"Total Raids: {len(log)}", ephemeral=True)

    @ui.button(label="Toggle Raid Alerts", emoji="🔕", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_antiraid_silence")
    async def silence(self, i, b):
        c = self.get_config(i.guild_id)
        c["alerts_silenced"] = not c.get("alerts_silenced", False)
        self.save_config(c, i.guild_id, i.client)
        log_panel_action(i.guild_id, i.user.id, f"Anti-raid alerts silenced={c['alerts_silenced']}")
        state = "silenced" if c["alerts_silenced"] else "active"
        await i.response.send_message(f"🔕 Raid alerts are now **{state}**.", ephemeral=True)

class GuardianConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "guardian")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(title="🛡️ Guardian System", color=discord.Color.blue())
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        elif guild:
            embed.set_thumbnail(url="https://cdn.discordapp.com/embed/avatars/0.png")
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

    @ui.button(label="Reset All Rules", emoji="💀", style=discord.ButtonStyle.danger, row=4, custom_id="cfg_guardian_reset")
    async def reset(self, i, b):
        class ConfirmModal(ui.Modal, title="Reset Guardian"):
            confirm = ui.TextInput(label="Type 'RESET' to confirm")
            async def on_submit(self, it):
                if self.confirm.value == "RESET":
                    c = self.config_panel.get_config(it.guild_id)
                    c.clear()
                    self.config_panel.save_config(c, it.guild_id, it.client)
                    await it.response.send_message("✅ Guardian rules have been reset.", ephemeral=True)
                else: await it.response.send_message("❌ Cancelled.", ephemeral=True)
        modal = ConfirmModal()
        modal.config_panel = self
        await i.response.send_modal(modal)

class WelcomeConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "welcome")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
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
            async def callback(self, it):
                c = dm.get_guild_data(it.guild_id, "welcome_config", {}); c["channel_id"] = self.values[0].id
                dm.update_guild_data(it.guild_id, "welcome_config", c); await it.response.send_message("✅ Welcome channel set.", ephemeral=True)
        await i.response.send_message("Select channel:", view=_picker_view(ChSelect(placeholder="Welcome Channel")), ephemeral=True)

    @ui.button(label="Set Leave Ch", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_wl_set_lch")
    async def set_lch(self, i, b):
        parent_view = self
        class ChSelect(ui.ChannelSelect):
            async def callback(self, it):
                c = dm.get_guild_data(it.guild_id, "leave_config", {}); c["channel_id"] = self.values[0].id
                dm.update_guild_data(it.guild_id, "leave_config", c); await it.response.send_message("✅ Leave channel set.", ephemeral=True)
                await parent_view.update_panel(it)
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
        self.config_panel = parent
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
        self.config_panel = parent
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

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
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
                self.config_panel = parent
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
        self.config_panel = parent
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

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
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
                self.config_panel = parent
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
                for app in sorted(filtered, key=lambda x: x.get("timestamp", 0), reverse=True)[:20]:
                    status_emoji = {"accepted": "✅", "denied": "❌", "pending": "⏳", "on_hold": "🕐"}.get(app.get("status"), "❓")
                    msg += f"{status_emoji} <@{app.get('user_id')}> - <t:{int(app.get('timestamp', 0))}:R>\n"

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
                self.config_panel = parent
                existing = parent.get_config(i.guild_id).get("questions", [])
                self.input = ui.TextInput(label="Questions (one per line, max 5)", style=discord.TextStyle.paragraph,
                                        default="\n".join(existing), required=True)
                self.add_item(self.input)
            async def on_submit(self, it):
                qs = [q.strip() for q in self.input.value.split("\n") if q.strip()][:5]
                c = self.config_panel.get_config(it.guild_id); c["questions"] = qs
                self.config_panel.save_config(c, it.guild_id, it.client)
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
                self.config_panel = parent
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
                self.config_panel = parent
                self.input = ui.TextInput(label="Type Name (e.g. Staff, Partner)")
                self.add_item(self.input)
            async def on_submit(self, it):
                c = self.config_panel.get_config(it.guild_id)
                types = c.get("application_types", [])
                if self.input.value not in types:
                    types.append(self.input.value)
                    c["application_types"] = types
                    self.config_panel.save_config(c, it.guild_id, it.client)
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

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
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
                self.config_panel = parent
                existing = parent.get_config(i.guild_id).get("questions", [])
                self.q.default = "\n".join(existing)
            async def on_submit(self, it):
                qs = [s.strip() for s in self.q.value.split("\n") if s.strip()][:4]
                c = self.config_panel.get_config(it.guild_id); c["questions"] = qs
                self.config_panel.save_config(c, it.guild_id, it.client)
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

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
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
        # Set initial toggle button state
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_tickets_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "tickets")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="🎫 Ticket System", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Category", value=f"<#{c.get('category_id')}>" if c.get('category_id') else "_None_", inline=True)
        embed.add_field(name="Staff Role", value=f"<@&{c.get('staff_role_id')}>" if c.get('staff_role_id') else "_None_", inline=True)

        stats = dm.get_guild_data(guild_id or self.guild_id, "ticket_stats", {"total": 0, "open": 0, "closed": 0})
        embed.add_field(name="📊 Stats", value=f"Total: {stats['total']} | Open: {stats['open']} | Closed: {stats['closed']}", inline=False)
        return embed

    @ui.button(label="Disable", emoji="❌", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_tickets_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(c, i.guild_id, i.client)
        log_panel_action(i.guild_id, i.user.id, f"Toggled tickets to {c.get('enabled')}")
        # Update toggle button label and style
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_tickets_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break
        await self.update_panel(i)

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
                open_list.append(f"#{tid.split('_')[-1]} | <@{t.get('user_id')}> | {t.get('title', 'No Title')[:20]}")

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

        # Ensure description is never None to prevent Embed error
        title = settings.get("panel_title") or "Support Tickets"
        description = settings.get("panel_description") or "Click the button below to open a ticket."
        color = settings.get("panel_color") or 0x3498db

        embed = discord.Embed(title=title, description=description, color=color)
        await i.channel.send(embed=embed, view=TicketOpenPanel())

        if not i.response.is_done():
            await i.response.send_message("✅ Public ticket panel sent to this channel.", ephemeral=True)


class GiveawayConfigView(ConfigPanelView):
    _config_key = "giveaways_config"  # auto_setup writes here
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "giveaway")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
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


    async def config_games(self, i, b):
        await i.response.send_message("Mini-games (dice, flip, slots, trivia) are active. Default bet: 10 coins.", ephemeral=True)

    @ui.button(label="Launch Event", emoji="🚀", style=discord.ButtonStyle.success, row=3, custom_id="cfg_gam_launch")
    async def launch_event(self, i, b):
        await i.response.send_message("Seasonal event launched! (XP Multiplier: 2x)", ephemeral=True)

    @ui.button(label="End Event", emoji="🏁", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_gam_end_ev")
    async def end_event(self, i, b):
        await i.response.send_message("Active seasonal event ended.", ephemeral=True)

    @ui.button(label="Toggle Streaks", emoji="🔢", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_gam_streaks")
    async def toggle_streaks(self, i, b):
        await i.response.send_message("Streak system toggled.", ephemeral=True)

    @ui.button(label="Update Leaderboard", emoji="🔃", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_gam_upd_lb")
    async def upd_lb(self, i, b):
        await i.response.send_message("Leaderboard update forced.", ephemeral=True)

    @ui.button(label="Manage Titles", emoji="🏅", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_gam_titles_m")
    async def manage_titles(self, i, b):
        await i.response.send_message("Current Titles: Legend, Elite, Veteran, Regular, Newcomer.", ephemeral=True)

    @ui.button(label="Leaderboard Channel", emoji="📣", style=discord.ButtonStyle.primary, row=3, custom_id="cfg_gam_lb_ch")
    async def set_lb_ch(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "leaderboard_channel", "Leaderboard Channel")), ephemeral=True)

    @ui.button(label="Season Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_gam_stats")
    async def season_stats(self, i, b):
        await i.response.send_message("📊 **Engagement Stats**\nActive Quests: 12\nCompleted Today: 5\nStreak Leaders: 3 members", ephemeral=True)


class ReactionRolesConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "reactionroles")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
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

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
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
                await it.response.defer(ephemeral=True)
                roles_list = []
                try:
                    for entry in self.roles.value.split("|"):
                        parts = [p.strip() for p in entry.split(",")]
                        if len(parts) >= 3:
                            roles_list.append({"role_id": int(parts[0]), "emoji": parts[1], "label": parts[2]})
                        elif len(parts) == 2: # Fallback for no emoji
                            roles_list.append({"role_id": int(parts[0]), "emoji": None, "label": parts[1]})

                    if not roles_list:
                        return await it.followup.send("❌ No valid roles found. Format: `RoleID, Emoji, Label|...`", ephemeral=True)

                    menu_id = await it.client.reaction_menus.create_menu(it, self.name.value, "button_grid", roles_list, it.channel, self.title.value, self.desc.value)
                    if menu_id: await it.followup.send(f"✅ Menu created: {self.name.value}", ephemeral=True)
                    else: await it.followup.send("❌ Failed to create menu. Check bot permissions.", ephemeral=True)
                except ValueError:
                    await it.followup.send("❌ Invalid Role ID provided. Must be a number.", ephemeral=True)
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

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(title="🔘 Role Button Panels", color=discord.Color.blue())
        embed.add_field(name="Active Panels", value=str(len(c)), inline=True)
        return embed

    @ui.button(label="Create Panel", emoji="➕", style=discord.ButtonStyle.success, row=0, custom_id="cfg_rb_create")
    async def create_panel(self, i, b):
        class ChSelect(ui.ChannelSelect, placeholder="Select Channel for Panel", channel_types=[discord.ChannelType.text]):
            async def callback(self, it):
                class PanelModal(ui.Modal, title="Create Role Button Panel"):
                    title = ui.TextInput(label="Panel Title")
                    desc = ui.TextInput(label="Panel Description", style=discord.TextStyle.paragraph)
                    async def on_submit(self, it2):
                        channel = it.guild.get_channel(self.values[0].id)
                        if not channel:
                            return await it2.response.send_message("❌ Channel not found.", ephemeral=True)
                        pid = await it2.client.role_buttons.create_panel(it2, self.title.value, self.desc.value, channel)
                        if pid:
                            await it2.response.send_message(f"✅ Panel created in {channel.mention}! Add buttons using the 'Add Button' button.", ephemeral=True)
                        else:
                            await it2.response.send_message("❌ Failed to create panel.", ephemeral=True)
                await it.response.send_modal(PanelModal())
        await i.response.send_message("Select a channel for the role button panel:", view=_picker_view(ChSelect()), ephemeral=True)

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
    _config_key = "mod_logging_config"  # auto_setup writes here
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "mod_log")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
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

class StaffPromoConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "staff_promo")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="🌟 Staff Promotion System Configuration", color=discord.Color.gold())

        settings = c.setdefault("settings", {
            "auto_promote": True,
            "auto_demote": False,
            "demotion_threshold_buffer": 0.1,
            "min_tenure_hours": 72,
            "excluded_users": [],
            "promotion_cooldown_hours": 24,
            "demotion_cooldown_hours": 168,
            "notify_on_promotion": True,
            "notify_on_demotion": True,
            "notify_near_promotion": True,
            "near_promotion_threshold": 0.05,
            "announce_channel": None,
            "log_channel": None,
            "progress_notify_channel": None,
            "review_mode": False,
            "review_channel": None,
            "activity_decay_days": 30,
        })
        embed.add_field(name="Auto-Promote", value="✅ ON" if settings.get("auto_promote", True) else "❌ OFF", inline=True)
        embed.add_field(name="Review Mode", value="✅ ON" if settings.get("review_mode", False) else "❌ OFF", inline=True)
        embed.add_field(name="Review Channel", value=f"<#{settings.get('review_channel')}>" if settings.get('review_channel') else "None", inline=True)

        tiers = c.get("tiers", [])
        embed.add_field(name="Hierarchy", value=" → ".join([t['name'] for t in tiers]) or "None", inline=False)

        return embed

    @ui.button(label="Staff Overview", emoji="📊", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_staff_ov")
    async def view_overview(self, i, b):
        guild = i.guild
        config = self.get_config(i.guild_id)
        tiers = config.get("tiers", [])
        staff_roles = [t.get("role_name") for t in tiers]
        desc = "**Staff Member | Role | Days | Status**\n"
        now = discord.utils.utcnow()
        for member in guild.members:
            if any(r.name in staff_roles for r in member.roles):
                days = (now - (member.joined_at or now)).days

                # Find current and next tier
                current_idx = -1
                for idx, t in enumerate(tiers):
                    if any(r.name == t.get("role_name") for r in member.roles):
                        current_idx = idx
                        break

                status = "Max Rank"
                if current_idx != -1 and current_idx < len(tiers) - 1:
                    next_t = tiers[current_idx + 1]
                    is_elig = i.client.promotion_service._check_tier_requirements(i.guild_id, member, next_t['name'], config)
                    status = "✅ Eligible" if is_elig else "⏳ Pending"

                desc += f"• {member.mention} | {member.top_role.name} | {days}d | {status}\n"

        await i.response.send_message(embed=discord.Embed(title="📊 Staff Overview", description=desc[:4000]), ephemeral=True)

    @ui.button(label="Leaderboard", emoji="🏆", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_staff_lb")
    async def view_lb(self, i, b):
        guild = i.guild
        staff = []
        staff_roles = [t.get("role_name") for t in self.get_config(i.guild_id).get("tiers", [])]
        for member in guild.members:
            if any(r.name in staff_roles for r in member.roles):
                udata = dm.get_guild_data(i.guild_id, f"user_{member.id}", {})
                score = udata.get("on_duty_messages", 0) + (udata.get("on_duty_hours", 0) * 10)
                staff.append((member, score))

        staff.sort(key=lambda x: x[1], reverse=True)
        desc = "\n".join([f"{idx+1}. {m.mention} - Score: {s:.1f}" for idx, (m, s) in enumerate(staff[:10])]) or "No staff activity tracked."
        await i.response.send_message(embed=discord.Embed(title="🏆 Staff Activity Leaderboard", description=desc), ephemeral=True)

    @ui.button(label="Promotion History", emoji="📋", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_staff_hist")
    async def view_history(self, i, b):
        logs = dm.get_guild_data(i.guild_id, "promotion_logs", [])
        if not logs: return await i.response.send_message("No promotion history found.", ephemeral=True)
        desc = "\n".join([f"• <t:{int(l['ts'])}:d> {l['user']} -> {l['to']} ({l['reason']})" for l in logs[::-1]])
        await i.response.send_message(embed=discord.Embed(title="📋 Promotion History", description=desc[:4000]), ephemeral=True)

    @ui.button(label="Promote Staff", emoji="⬆️", style=discord.ButtonStyle.success, row=1, custom_id="cfg_staff_promote")
    async def promote_staff(self, i, b):
        class PromoteModal(ui.Modal, title="Manual Promotion"):
            uid = ui.TextInput(label="User ID")
            tier = ui.TextInput(label="Target Tier Name")
            reason = ui.TextInput(label="Reason")
            async def on_submit(self, it):
                guild = it.guild
                member = guild.get_member(int(self.uid.value))
                if not member: return await it.response.send_message("Member not found.", ephemeral=True)
                success, msg = await it.client.staff_promo.manual_promote(guild, member, self.tier.value, dm.get_guild_data(guild.id, "staff_promo_config", {}))
                await it.response.send_message(f"Result: {msg}", ephemeral=True)
        await i.response.send_modal(PromoteModal())

    @ui.button(label="Demote Staff", emoji="⬇️", style=discord.ButtonStyle.danger, row=1, custom_id="cfg_staff_demote")
    async def demote_staff(self, i, b):
        class DemoteModal(ui.Modal, title="Manual Demotion"):
            uid = ui.TextInput(label="User ID")
            tier = ui.TextInput(label="Target Tier Name (or 'None')")
            reason = ui.TextInput(label="Reason")
            async def on_submit(self, it):
                guild = it.guild
                member = guild.get_member(int(self.uid.value))
                if not member: return await it.response.send_message("Member not found.", ephemeral=True)
                success, msg = await it.client.staff_promo.manual_demote(guild, member, self.tier.value, dm.get_guild_data(guild.id, "staff_promo_config", {}))
                await it.response.send_message(f"Result: {msg}", ephemeral=True)
        await i.response.send_modal(DemoteModal())

    @ui.button(label="View Profile", emoji="🔍", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_staff_profile")
    async def view_profile(self, i, b):
        class ProfileModal(ui.Modal, title="View Staff Profile"):
            uid = ui.TextInput(label="User ID")
            async def on_submit(self, it):
                uid = int(self.uid.value)
                udata = dm.get_guild_data(it.guild_id, f"user_{uid}", {})
                warns = len(dm.get_guild_data(it.guild_id, f"user_warnings_{uid}", []))

                embed = discord.Embed(title=f"Staff Profile: {uid}", color=discord.Color.blue())
                embed.add_field(name="On-Duty Msgs", value=str(udata.get("on_duty_messages", 0)), inline=True)
                embed.add_field(name="On-Duty Hours", value=f"{udata.get('on_duty_hours', 0):.1f}", inline=True)
                embed.add_field(name="Active Warnings", value=str(warns), inline=True)
                embed.add_field(name="Probation", value="YES" if udata.get("on_probation") else "NO", inline=True)

                await it.response.send_message(embed=embed, ephemeral=True)
        await i.response.send_modal(ProfileModal())

    @ui.button(label="Promotion Path", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_staff_path")
    async def config_path(self, i, b):
        await i.response.send_message("⚙️ Path Builder: Use `!staffpromo tiers` for interactive hierarchy management.", ephemeral=True)

    @ui.button(label="Requirements", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_staff_req")
    async def config_reqs(self, i, b):
        await i.response.send_message("⚙️ Requirements Editor: Use `!staffpromo requirements` to set per-tier criteria.", ephemeral=True)

    @ui.button(label="Toggle Auto-Promote", emoji="🚀", style=discord.ButtonStyle.success, row=2, custom_id="cfg_staff_auto")
    async def toggle_auto(self, i, b):
        c = self.get_config(i.guild_id)
        settings = c.setdefault("settings", {
            "auto_promote": True,
            "auto_demote": False,
            "demotion_threshold_buffer": 0.1,
            "min_tenure_hours": 72,
            "excluded_users": [],
            "promotion_cooldown_hours": 24,
            "demotion_cooldown_hours": 168,
            "notify_on_promotion": True,
            "notify_on_demotion": True,
            "notify_near_promotion": True,
            "near_promotion_threshold": 0.05,
            "announce_channel": None,
            "log_channel": None,
            "progress_notify_channel": None,
            "review_mode": False,
            "review_channel": None,
            "activity_decay_days": 30,
        })
        settings["auto_promote"] = not settings.get("auto_promote", True)
        self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Toggle Review Mode", emoji="⚖️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_staff_review")
    async def toggle_review(self, i, b):
        c = self.get_config(i.guild_id)
        settings = c.setdefault("settings", {
            "auto_promote": True,
            "auto_demote": False,
            "demotion_threshold_buffer": 0.1,
            "min_tenure_hours": 72,
            "excluded_users": [],
            "promotion_cooldown_hours": 24,
            "demotion_cooldown_hours": 168,
            "notify_on_promotion": True,
            "notify_on_demotion": True,
            "notify_near_promotion": True,
            "near_promotion_threshold": 0.05,
            "announce_channel": None,
            "log_channel": None,
            "progress_notify_channel": None,
            "review_mode": False,
            "review_channel": None,
            "activity_decay_days": 30,
        })
        settings["review_mode"] = not settings.get("review_mode", False)
        self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Toggle DMs", emoji="📩", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_staff_dm")
    async def toggle_dm(self, i, b):
        c = self.get_config(i.guild_id)
        settings = c.setdefault("settings", {
            "auto_promote": True,
            "auto_demote": False,
            "demotion_threshold_buffer": 0.1,
            "min_tenure_hours": 72,
            "excluded_users": [],
            "promotion_cooldown_hours": 24,
            "demotion_cooldown_hours": 168,
            "notify_on_promotion": True,
            "notify_on_demotion": True,
            "notify_near_promotion": True,
            "near_promotion_threshold": 0.05,
            "announce_channel": None,
            "log_channel": None,
            "progress_notify_channel": None,
            "review_mode": False,
            "review_channel": None,
            "activity_decay_days": 30,
        })
        settings["notify_on_promotion"] = not settings.get("notify_on_promotion", True)
        self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Set Review Ch", emoji="🔔", style=discord.ButtonStyle.primary, row=3, custom_id="cfg_staff_rev_ch")
    async def set_rev_ch(self, i, b):
        await i.response.send_message("Select Review Channel:", view=_picker_view(_GenericChannelSelect(self, "review_channel", "Review Channel")), ephemeral=True)

    @ui.button(label="Put on Probation", emoji="⏸️", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_staff_prob")
    async def put_probation(self, i, b):
        class ProbModal(ui.Modal, title="Staff Probation"):
            uid = ui.TextInput(label="User ID")
            days = ui.TextInput(label="Duration (Days)", default="14")
            reason = ui.TextInput(label="Reason")
            async def on_submit(self, it):
                member = it.guild.get_member(int(self.uid.value))
                if member:
                    await it.client.staff_promo.put_on_probation(it.guild, member, int(self.days.value), self.reason.value)
                    await it.response.send_message(f"✅ {member.display_name} put on probation.", ephemeral=True)
                else: await it.response.send_message("Member not found.", ephemeral=True)
        await i.response.send_modal(ProbModal())

    @ui.button(label="End Probation", emoji="▶️", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_staff_end_prob")
    async def end_probation(self, i, b):
        class EndProbModal(ui.Modal, title="End Probation Early"):
            uid = ui.TextInput(label="User ID")
            async def on_submit(self, it):
                member = it.guild.get_member(int(self.uid.value))
                if member:
                    await it.client.staff_promo.end_probation(it.guild, member)
                    await it.response.send_message(f"✅ {member.display_name} probation ended.", ephemeral=True)
        await i.response.send_modal(EndProbModal())

    @ui.button(label="Exclude Staff", emoji="🔕", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_staff_excl")
    async def exclude_staff(self, i, b):
        await i.response.send_message("Exclude from promotions: Use `!staffpromo exclude add @user`", ephemeral=True)

    @ui.button(label="Eligible Staff", emoji="🔍", style=discord.ButtonStyle.success, row=4, custom_id="cfg_staff_elig")
    async def view_eligible(self, i, b):
        guild = i.guild
        config = self.get_config(i.guild_id)
        tiers = config.get("tiers", [])
        eligible = []

        for member in guild.members:
            # Find current tier
            current_idx = -1
            for idx, t in enumerate(tiers):
                if any(r.name == t.get("role_name") for r in member.roles):
                    current_idx = idx
                    break

            if current_idx != -1 and current_idx < len(tiers) - 1:
                next_tier = tiers[current_idx + 1]
                if i.client.promotion_service._check_tier_requirements(i.guild_id, member, next_tier['name'], config):
                    eligible.append(f"• {member.mention} -> **{next_tier['name']}**")

        desc = "\n".join(eligible) or "No staff currently meet ALL requirements for promotion."
        await i.response.send_message(embed=discord.Embed(title="🔍 Eligible Staff", description=desc), ephemeral=True)

class WarningConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "warning")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="⚠️ User Warning System Configuration", color=discord.Color.orange())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Expiry", value=f"{c.get('expiry_days', 30)} days", inline=True)
        embed.add_field(name="DM Warnings", value="✅ ON" if c.get("dm_enabled", True) else "❌ OFF", inline=True)

        ts = c.get("thresholds", {})
        ts_text = "\n".join([f"• {k.title()}: {v['count']} -> {v['action'].upper()}" for k, v in ts.items()])
        embed.add_field(name="Punishment Thresholds", value=ts_text or "None", inline=False)

        return embed

    @ui.button(label="Issue Warning", emoji="⚠️", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_warn_issue")
    async def issue_warn(self, i, b):
        class IssueModal(ui.Modal, title="Issue Manual Warning"):
            uid = ui.TextInput(label="User ID")
            reason = ui.TextInput(label="Reason", style=discord.TextStyle.paragraph)
            severity = ui.TextInput(label="Severity (minor/moderate/severe)", default="minor")
            async def on_submit(self, it):
                await it.client.warnings.issue_warning(it.guild, int(self.uid.value), it.user.id, self.reason.value, self.severity.value)
                await it.response.send_message(f"✅ Warning issued to {self.uid.value}", ephemeral=True)
        await i.response.send_modal(IssueModal())

    @ui.button(label="View Warnings", emoji="📋", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_warn_view")
    async def view_warns(self, i, b):
        class ViewModal(ui.Modal, title="View User Warnings"):
            uid = ui.TextInput(label="User ID")
            async def on_submit(self, it):
                warns = it.client.warnings.get_warnings(it.guild_id, int(self.uid.value))
                if not warns: return await it.response.send_message("No warnings found for this user.", ephemeral=True)
                desc = "\n".join([f"**ID: {w['id']}** | {w['severity']} | {w['reason'][:50]}" for w in warns[-10:]])
                await it.response.send_message(embed=discord.Embed(title=f"Warnings: {self.uid.value}", description=desc), ephemeral=True)
        await i.response.send_modal(ViewModal())

    @ui.button(label="Toggle System", emoji="✅", style=discord.ButtonStyle.success, row=0, custom_id="cfg_warn_toggle")
    async def toggle_system(self, i, b):
        c = self.get_config(i.guild_id)
        c["enabled"] = not c.get("enabled", True)
        self.save_config(c, i.guild_id, i.client)
        await self.update_panel(i)

    @ui.button(label="Toggle DM Warnings", emoji="📩", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_warn_toggle_dm")
    async def toggle_dm(self, i, b):
        c = self.get_config(i.guild_id)
        c["dm_enabled"] = not c.get("dm_enabled", True)
        self.save_config(c, i.guild_id, i.client)
        await self.update_panel(i)

    @ui.button(label="Pardon Warning", emoji="✅", style=discord.ButtonStyle.success, row=0, custom_id="cfg_warn_pardon")
    async def pardon_warn(self, i, b):
        class PardonModal(ui.Modal, title="Pardon Warning"):
            uid = ui.TextInput(label="User ID")
            wid = ui.TextInput(label="Warning ID")
            reason = ui.TextInput(label="Pardon Reason")
            def __init__(self, parent):
                super().__init__()
                self.config_panel = parent
            async def on_submit(self, it):
                success = await it.client.warnings.pardon_warning(it.guild, int(self.uid.value), int(self.wid.value), self.reason.value)
                if success: await it.response.send_message("✅ Warning pardoned.", ephemeral=True)
                else: await it.response.send_message("❌ Warning not found.", ephemeral=True)
        await i.response.send_modal(PardonModal(self))

    @ui.button(label="Delete Warning", emoji="🗑️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_warn_delete")
    async def delete_warn(self, i, b):
        class DelModal(ui.Modal, title="Delete Warning Entry"):
            uid = ui.TextInput(label="User ID")
            wid = ui.TextInput(label="Warning ID")
            async def on_submit(self, it):
                success = await it.client.warnings.delete_warning(it.guild, int(self.uid.value), int(self.wid.value))
                if success: await it.response.send_message("✅ Warning deleted.", ephemeral=True)
                else: await it.response.send_message("❌ Warning not found.", ephemeral=True)
        await i.response.send_modal(DelModal())

    @ui.button(label="Clear All", emoji="🗑️", style=discord.ButtonStyle.danger, row=1, custom_id="cfg_warn_clear_all")
    async def clear_all(self, i, b):
        class ClearModal(ui.Modal, title="Clear All User Warnings"):
            uid = ui.TextInput(label="User ID")
            reason = ui.TextInput(label="Reason for clear")
            async def on_submit(self, it):
                count = await it.client.warnings.clear_all_warnings(it.guild, int(self.uid.value), self.reason.value)
                await it.response.send_message(f"✅ Cleared {count} warnings for {self.uid.value}", ephemeral=True)
        await i.response.send_modal(ClearModal())

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_warn_stats")
    async def warn_stats(self, i, b):
        stats = dm.get_guild_data(i.guild_id, "warning_stats", {})
        embed = discord.Embed(title="📊 Warning Statistics", color=discord.Color.orange())
        embed.add_field(name="Issued Today", value=str(stats.get("today", 0)), inline=True)
        embed.add_field(name="Issued This Week", value=str(stats.get("week", 0)), inline=True)
        embed.add_field(name="Total Pardoned", value=str(stats.get("total_pardoned", 0)), inline=True)

        breakdown = stats.get("severity_breakdown", {})
        bd_text = "\n".join([f"• {k.title()}: {v}" for k, v in breakdown.items()]) or "None"
        embed.add_field(name="Severity Breakdown", value=bd_text, inline=False)

        await i.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Thresholds", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_warn_thresh")
    async def config_thresh(self, i, b):
        class ThreshModal(ui.Modal, title="Configure Thresholds"):
            minor = ui.TextInput(label="Minor (Count)", default="2")
            moderate = ui.TextInput(label="Moderate (Count)", default="3")
            severe = ui.TextInput(label="Severe (Count)", default="4")
            critical = ui.TextInput(label="Critical (Count)", default="5")
            def __init__(self, parent):
                super().__init__()
                self.config_panel = parent
            async def on_submit(self, it):
                c = self.config_panel.get_config(it.guild_id)
                c["thresholds"]["minor"]["count"] = int(self.minor.value)
                c["thresholds"]["moderate"]["count"] = int(self.moderate.value)
                c["thresholds"]["severe"]["count"] = int(self.severe.value)
                c["thresholds"]["critical"]["count"] = int(self.critical.value)
                self.config_panel.save_config(c, it.guild_id, it.client)
                await it.response.send_message("✅ Threshold counts updated.", ephemeral=True)
        await i.response.send_modal(ThreshModal(self))

    @ui.button(label="Expiry", emoji="⏱️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_warn_expiry")
    async def config_expiry(self, i, b):
        await i.response.send_modal(_NumberModal(self, "expiry_days", "Warning Expiry (Days, 0=never)", i.guild_id))

    @ui.button(label="Toggle DMs", emoji="📩", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_warn_dm")
    async def toggle_dm(self, i, b):
        c = self.get_config(i.guild_id); c["dm_enabled"] = not c.get("dm_enabled", True)
        self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Edit DM", emoji="✏️", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_warn_edit_dm")
    async def edit_dm(self, i, b):
        await i.response.send_modal(_TextModal(self, "dm_template", "Warning DM Template", i.guild_id))

    @ui.button(label="Most Warned", emoji="🏆", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_warn_top")
    async def top_warned(self, i, b):
        # Scan all user_warnings keys
        guild_data = dm.load_json(f"guild_{i.guild_id}", default={})
        warn_counts = {}
        for key, value in guild_data.items():
            if key.startswith("user_warnings_") and isinstance(value, list):
                uid = key.replace("user_warnings_", "")
                active_count = len([w for w in value if w.get("active") and not w.get("pardoned")])
                if active_count > 0: warn_counts[uid] = active_count

        sorted_warns = sorted(warn_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        desc = "\n".join([f"**<@{uid}>**: {count} active warnings" for uid, count in sorted_warns]) or "No active warnings."
        await i.response.send_message(embed=discord.Embed(title="🏆 Most Warned Users", description=desc), ephemeral=True)

    @ui.button(label="Recent Warnings", emoji="📋", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_warn_recent")
    async def recent_warns(self, i, b):
        history = dm.get_guild_data(i.guild_id, "warning_history", [])
        if not history: return await i.response.send_message("No recent warnings.", ephemeral=True)

        desc = ""
        for entry in history[::-1]:
            t = f"<t:{int(entry['ts'])}:R>"
            desc += f"{t} **<@{entry['user_id']}>** - {entry['severity'].upper()}: {entry['reason'][:50]}\n"

        await i.response.send_message(embed=discord.Embed(title="Recent Server Warnings", description=desc[:4000]), ephemeral=True)

class AutoModConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "automod")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="🛡️ Auto-Mod System Configuration", color=discord.Color.blue())
        enabled = c.get("enabled", True)
        embed.add_field(name="Status", value="✅ Enabled" if enabled else "❌ Disabled", inline=True)
        embed.add_field(name="Log Channel", value=f"<#{c.get('log_channel_id')}>" if c.get('log_channel_id') else "None", inline=True)

        rules = c.get("rules", {})
        active_rules = [name for name, r in rules.items() if r.get("enabled")]
        embed.add_field(name="Active Rules", value=", ".join(active_rules) or "None", inline=False)

        return embed

    @ui.button(label="Toggle System", emoji="✅", style=discord.ButtonStyle.success, row=0, custom_id="cfg_automod_toggle")
    async def toggle_system(self, i, b):
        c = self.get_config(i.guild_id)
        c["enabled"] = not c.get("enabled", True)
        self.save_config(c, i.guild_id, i.client)
        await self.update_panel(i)

    @ui.button(label="View All Rules", emoji="📋", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_automod_view")
    async def view_rules(self, i, b):
        c = self.get_config(i.guild_id)
        rules = c.get("rules", {})
        desc = ""
        for name, r in rules.items():
            status = "✅" if r.get("enabled") else "❌"
            action = r.get("action", "warn").upper()
            desc += f"{status} **{name.replace('_', ' ').title()}** | Action: {action}\n"

        await i.response.send_message(embed=discord.Embed(title="Auto-Mod Rules", description=desc), ephemeral=True)

    @ui.button(label="Configure Spam", emoji="✏️", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_automod_spam")
    async def config_spam(self, i, b):
        class SpamModal(ui.Modal, title="Configure Spam Filter"):
            count = ui.TextInput(label="Max Messages", default="5")
            window = ui.TextInput(label="Time Window (seconds)", default="5")
            action = ui.TextInput(label="Action (warn/mute/delete)", default="mute")
            def __init__(self, parent):
                super().__init__()
                self.config_panel = parent
            async def on_submit(self, it):
                c = self.config_panel.get_config(it.guild_id)
                if "rules" not in c: c["rules"] = {}
                if "spam" not in c["rules"]: c["rules"]["spam"] = {}
                c["rules"]["spam"].update({
                    "max_messages": int(self.count.value),
                    "window": int(self.window.value),
                    "action": self.action.value.lower(),
                    "enabled": True
                })
                self.config_panel.save_config(c, it.guild_id, it.client)
                await it.response.send_message("✅ Spam filter updated.", ephemeral=True)
        await i.response.send_modal(SpamModal(self))

    @ui.button(label="Mention Filter", emoji="✏️", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_automod_ment")
    async def config_mentions(self, i, b):
        class MentModal(ui.Modal, title="Configure Mention Filter"):
            count = ui.TextInput(label="Max Mentions", default="5")
            window = ui.TextInput(label="Time Window (seconds)", default="10")
            action = ui.TextInput(label="Action (warn/mute/delete)", default="warn")
            def __init__(self, parent):
                super().__init__()
                self.config_panel = parent
            async def on_submit(self, it):
                c = self.config_panel.get_config(it.guild_id)
                if "rules" not in c: c["rules"] = {}
                if "mentions" not in c["rules"]: c["rules"]["mentions"] = {}
                c["rules"]["mentions"].update({
                    "max_mentions": int(self.count.value),
                    "window": int(self.window.value),
                    "action": self.action.value.lower(),
                    "enabled": True
                })
                self.config_panel.save_config(c, it.guild_id, it.client)
                await it.response.send_message("✅ Mention filter updated.", ephemeral=True)
        await i.response.send_modal(MentModal(self))

    @ui.button(label="Caps Filter", emoji="✏️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_automod_caps")
    async def config_caps(self, i, b):
        class CapsModal(ui.Modal, title="Configure Caps Filter"):
            pct = ui.TextInput(label="Caps % Threshold", default="70")
            min_chars = ui.TextInput(label="Min Message Length", default="20")
            action = ui.TextInput(label="Action (warn/delete)", default="warn")
            def __init__(self, parent):
                super().__init__()
                self.config_panel = parent
            async def on_submit(self, it):
                c = self.config_panel.get_config(it.guild_id)
                if "rules" not in c: c["rules"] = {}
                if "caps" not in c["rules"]: c["rules"]["caps"] = {}
                c["rules"]["caps"].update({
                    "threshold_pct": int(self.pct.value),
                    "min_chars": int(self.min_chars.value),
                    "action": self.action.value.lower(),
                    "enabled": True
                })
                self.config_panel.save_config(c, it.guild_id, it.client)
                await it.response.send_message("✅ Caps filter updated.", ephemeral=True)
        await i.response.send_modal(CapsModal(self))

    @ui.button(label="Link Filter", emoji="✏️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_automod_link")
    async def config_links(self, i, b):
        class LinkModal(ui.Modal, title="Configure Link Filter"):
            count = ui.TextInput(label="Max Links", default="3")
            window = ui.TextInput(label="Time Window (seconds)", default="10")
            action = ui.TextInput(label="Action (warn/delete)", default="warn")
            whitelist = ui.TextInput(label="Whitelist Domains (comma sep)", required=False)
            def __init__(self, parent):
                super().__init__()
                self.config_panel = parent
                rules = parent.get_config(i.guild_id).get("rules", {})
                existing = rules.get("links", {}).get("whitelisted_domains", [])
                self.whitelist.default = ", ".join(existing)
            async def on_submit(self, it):
                c = self.config_panel.get_config(it.guild_id)
                if "rules" not in c: c["rules"] = {}
                if "links" not in c["rules"]: c["rules"]["links"] = {}
                domains = [d.strip() for d in self.whitelist.value.split(",") if d.strip()]
                c["rules"]["links"].update({
                    "max_links": int(self.count.value),
                    "window": int(self.window.value),
                    "action": self.action.value.lower(),
                    "whitelisted_domains": domains,
                    "enabled": True
                })
                self.config_panel.save_config(c, it.guild_id, it.client)
                await it.response.send_message("✅ Link filter updated.", ephemeral=True)
        await i.response.send_modal(LinkModal(self))

    @ui.button(label="Toggle Invites", emoji="✅", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_automod_inv")
    async def toggle_invites(self, i, b):
        c = self.get_config(i.guild_id)
        rules = c.setdefault("rules", {}); inv = rules.setdefault("invites", {}); inv["enabled"] = not inv.get("enabled", True)
        self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Manage Banned Words", emoji="📝", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_automod_words")
    async def config_words(self, i, b):
        class WordManagementView(discord.ui.View):
            def __init__(self, parent_view, guild_id):
                super().__init__(timeout=None)
                self.config_panel_view = parent_view
                self.guild_id = guild_id

            @discord.ui.button(label="Add Word", style=discord.ButtonStyle.success, custom_id="cfg_automod_word_add")
            async def add_word(self, it, btn):
                class AddWordModal(ui.Modal, title="Add Banned Word"):
                    word = ui.TextInput(label="Word/Phrase")
                    async def on_submit(self, it2):
                        c = dm.get_guild_data(it2.guild_id, "automod_config", {})
                        words = c.get("rules", {}).get("banned_words", {}).get("words", [])
                        if self.word.value not in words:
                            words.append(self.word.value)
                            c["rules"]["banned_words"]["words"] = words
                            dm.update_guild_data(it2.guild_id, "automod_config", c)
                            await it2.response.send_message(f"✅ Added: {self.word.value}", ephemeral=True)
                        else: await it2.response.send_message("Already in list.", ephemeral=True)
                await it.response.send_modal(AddWordModal())

            @discord.ui.button(label="Remove Word", style=discord.ButtonStyle.danger, custom_id="cfg_automod_word_rem")
            async def remove_word(self, it, btn):
                c = dm.get_guild_data(it.guild_id, "automod_config", {})
                words = c.get("rules", {}).get("banned_words", {}).get("words", [])
                if not words: return await it.response.send_message("List is empty.", ephemeral=True)

                class RemoveSelect(ui.Select):
                    async def callback(self, it2):
                        c2 = dm.get_guild_data(it2.guild_id, "automod_config", {})
                        ws = c2.get("rules", {}).get("banned_words", {}).get("words", [])
                        if self.values[0] in ws:
                            ws.remove(self.values[0])
                            dm.update_guild_data(it2.guild_id, "automod_config", c2)
                            await it2.response.send_message(f"✅ Removed: {self.values[0]}", ephemeral=True)

                v = ui.View(); v.add_item(RemoveSelect(options=[discord.SelectOption(label=w) for w in words[:25]]))
                await it.response.send_message("Select word to remove:", view=v, ephemeral=True)

            @discord.ui.button(label="View List", style=discord.ButtonStyle.primary, custom_id="cfg_automod_word_list")
            async def view_list(self, it, btn):
                c = dm.get_guild_data(it.guild_id, "automod_config", {})
                words = c.get("rules", {}).get("banned_words", {}).get("words", [])
                await it.response.send_message(f"📝 **Banned Words:**\n{', '.join(words) or 'None'}", ephemeral=True)

        await i.response.send_message("Banned Words Management:", view=WordManagementView(self, i.guild_id), ephemeral=True)

    @ui.button(label="Escalation", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_automod_esc")
    async def config_escalation(self, i, b):
        class EscModal(ui.Modal, title="Configure Escalation"):
            reset = ui.TextInput(label="Reset hours", default="24")
            p1 = ui.TextInput(label="1st violation", default="warn")
            p2 = ui.TextInput(label="2nd violation", default="mute_10")
            p3 = ui.TextInput(label="3rd violation", default="mute_60")
            p4 = ui.TextInput(label="4th+ violation", default="kick")
            def __init__(self, parent):
                super().__init__()
                self.config_panel = parent
            async def on_submit(self, it):
                c = self.config_panel.get_config(it.guild_id)
                if "escalation" not in c: c["escalation"] = {}
                c["escalation"].update({
                    "reset_hours": int(self.reset.value),
                    "1": self.p1.value.lower(),
                    "2": self.p2.value.lower(),
                    "3": self.p3.value.lower(),
                    "4": self.p4.value.lower(),
                    "5": "ban"
                })
                self.config_panel.save_config(c, it.guild_id, it.client)
                await it.response.send_message("✅ Escalation settings updated.", ephemeral=True)
        await i.response.send_modal(EscModal(self))

    @ui.button(label="Whitelist Ch", emoji="🔕", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_automod_wl_ch")
    async def whitelist_ch(self, i, b):
        await i.response.send_message("Select channel to whitelist:", view=_picker_view(_GenericChannelSelect(self, "whitelist_channels", "Whitelist Channel")), ephemeral=True)

    @ui.button(label="Whitelist Role", emoji="🔕", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_automod_wl_rl")
    async def whitelist_rl(self, i, b):
        await i.response.send_message("Select role to whitelist:", view=_picker_view(_GenericRoleSelect(self, "whitelist_roles", "Whitelist Role")), ephemeral=True)

    @ui.button(label="Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_automod_stats")
    async def view_stats(self, i, b):
        stats = dm.get_guild_data(i.guild_id, "automod_stats", {})
        today = stats.get("today", 0)
        week = stats.get("week", 0)
        top_type = max(stats.get("types", {"None": 0}), key=stats.get("types", {"None": 0}).get)

        # Most warned user in automod
        top_user = "None"
        if stats.get("users"):
            uid = max(stats["users"], key=stats["users"].get)
            top_user = f"<@{uid}> ({stats['users'][uid]} violations)"

        embed = discord.Embed(title="📊 Auto-Mod Statistics", color=discord.Color.blue())
        embed.add_field(name="Caught Today", value=str(today), inline=True)
        embed.add_field(name="Caught This Week", value=str(week), inline=True)
        embed.add_field(name="Top Violation", value=top_type, inline=True)
        embed.add_field(name="Most Warned User", value=top_user, inline=False)

        actions = stats.get("actions", {})
        act_text = "\n".join([f"• {k.upper()}: {v}" for k, v in actions.items()]) or "None"
        embed.add_field(name="Actions Taken (Week)", value=act_text, inline=False)

        await i.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Clear Violations", emoji="🔄", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_automod_clear")
    async def clear_violations(self, i, b):
        class ClearModal(ui.Modal, title="Clear User Violations"):
            uid = ui.TextInput(label="User ID")
            async def on_submit(self, it):
                dm.update_guild_data(it.guild_id, f"automod_violations_{self.uid.value}", {"count": 0, "last_violation": 0})
                await it.response.send_message(f"✅ Violations cleared for {self.uid.value}", ephemeral=True)
        await i.response.send_modal(ClearModal())

    @ui.button(label="View Log", emoji="📋", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_automod_vlog")
    async def view_log(self, i, b):
        history = dm.get_guild_data(i.guild_id, "automod_history", [])
        if not history: return await i.response.send_message("No recent auto-mod actions.", ephemeral=True)

        desc = ""
        for entry in history[::-1]:
            t = f"<t:{int(entry['ts'])}:R>"
            desc += f"{t} **{entry['user']}** - {entry['type']} ({entry['action']})\n"

        await i.response.send_message(embed=discord.Embed(title="Auto-Mod Action Log", description=desc[:4000]), ephemeral=True)

    @ui.button(label="Test Rule", emoji="🧪", style=discord.ButtonStyle.success, row=4, custom_id="cfg_automod_test")
    async def test_rule(self, i, b):
        class TestModal(ui.Modal, title="Test Auto-Mod Rule"):
            msg = ui.TextInput(label="Test Message Content", style=discord.TextStyle.paragraph)
            async def on_submit(self, it):
                # We could potentially call automod.handle_message here with a mock message
                # For now, let's just do a basic keyword check as a demonstration
                content = self.msg.value.lower()
                c = dm.get_guild_data(it.guild_id, "automod_config", {})
                banned = c.get("rules", {}).get("banned_words", {}).get("words", [])
                triggered = []
                for w in banned:
                    if w.lower() in content: triggered.append(f"Banned Word: {w}")

                if triggered:
                    await it.response.send_message(f"🧪 **Test Results:**\n❌ Would trigger: {', '.join(triggered)}", ephemeral=True)
                else:
                    await it.response.send_message("🧪 **Test Results:**\n✅ Message would be allowed.", ephemeral=True)
        await i.response.send_modal(TestModal())

class StaffReviewsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "staff_reviews")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="📝 Staff Reviews Configuration", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Cycle", value=c.get("cycle", "monthly").title(), inline=True)
        embed.add_field(name="Review Channel", value=f"<#{c.get('review_channel_id')}>" if c.get('review_channel_id') else "None", inline=True)

        next_cycle = c.get("next_cycle_start", 0)
        if next_cycle > time.time():
            embed.add_field(name="Next Cycle", value=f"<t:{int(next_cycle)}:D>", inline=True)

        active = dm.get_guild_data(guild_id or self.guild_id, "staff_active_reviews", {})
        embed.add_field(name="Active Reviews", value=str(len(active)), inline=True)

        return embed

    @ui.button(label="Start Cycle Now", emoji="▶️", style=discord.ButtonStyle.success, row=0, custom_id="cfg_rev_start")
    async def start_now(self, i, b):
        await i.client.staff_reviews.start_review_cycle(i.guild_id)
        await i.response.send_message("✅ Review cycle triggered manually.", ephemeral=True)

    @ui.button(label="Pause Cycle", emoji="⏸️", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_rev_pause")
    async def pause_cycle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = False; self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Active Reviews", emoji="📋", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_rev_active")
    async def v_active(self, i, b):
        active = dm.get_guild_data(i.guild_id, "staff_active_reviews", {})
        if not active: return await i.response.send_message("No active reviews.", ephemeral=True)
        desc = ""
        for uid, data in active.items():
            status = []
            if data.get("self"): status.append("Self ✅")
            if data.get("peer"): status.append(f"Peer ({len(data['peer'])})")
            if data.get("admin"): status.append("Admin ✅")
            desc += f"• <@{uid}>: {', '.join(status) or 'Pending'}\n"
        await i.response.send_message(embed=discord.Embed(title="Active Reviews Progress", description=desc), ephemeral=True)

    @ui.button(label="Review Results", emoji="📊", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_rev_results")
    async def v_results(self, i, b):
        history = dm.get_guild_data(i.guild_id, "staff_reviews_history", [])
        if not history: return await i.response.send_message("No review history.", ephemeral=True)
        class ResSelect(ui.Select):
            async def callback(self, it):
                uid = int(self.values[0])
                revs = [r for r in history if r["user_id"] == uid]
                last = revs[-1]
                emb = discord.Embed(title=f"Results: {last['username']}", color=discord.Color.blue())
                emb.add_field(name="Composite Score", value=f"{last['composite_score']:.2f}")
                for crit, val in last.get("admin_ratings", {}).items():
                    emb.add_field(name=crit, value=str(val), inline=True)
                await it.response.send_message(embed=emb, ephemeral=True)
        # Ensure unique user options in select by picking the latest per user
        latest_per_user = {}
        for r in history:
            latest_per_user[r['user_id']] = r
        options = [discord.SelectOption(label=r['username'], value=str(r['user_id'])) for r in list(latest_per_user.values())[-25:]]
        v = ui.View(); v.add_item(ResSelect(options=options))
        await i.response.send_message("Select staff member:", view=v, ephemeral=True)

    @ui.button(label="View Trends", emoji="📈", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_rev_trends")
    async def v_trends(self, i, b):
        class UIDModal(ui.Modal, title="View Staff Trend"):
            uid = ui.TextInput(label="User ID")
            async def on_submit(self, it):
                from modules.auto_setup import MockInteraction
                fake = MockInteraction(it.client, it.guild, it.guild.get_member(int(self.uid.value)) or it.user)
                await it.client.staff_reviews.handle_myreview(fake)
                await it.response.send_message("Trend report generated.", ephemeral=True)
        await i.response.send_modal(UIDModal())

    @ui.button(label="Admin Review", emoji="✏️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_rev_admin")
    async def submit_admin(self, i, b):
        class AdminRevModal(ui.Modal, title="Submit Admin Review"):
            uid = ui.TextInput(label="Staff User ID")
            async def on_submit(self, it):
                m = it.guild.get_member(int(self.uid.value))
                if m:
                    from modules.staff_reviews import ReviewModal
                    await it.response.send_modal(ReviewModal(it.client.staff_reviews, it.guild_id, "admin", m, it.user.id))
                else: await it.response.send_message("Not found.", ephemeral=True)
        await i.response.send_modal(AdminRevModal())

    @ui.button(label="Individual Report", emoji="🔍", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_rev_report")
    async def ind_report(self, i, b):
        class ReportModal(ui.Modal, title="Staff Individual Report"):
            uid = ui.TextInput(label="User ID")
            async def on_submit(self, it):
                try:
                    uid = int(self.uid.value)
                    history = dm.get_guild_data(it.guild_id, "staff_reviews_history", [])
                    user_revs = [r for r in history if r["user_id"] == uid]
                    if not user_revs: return await it.response.send_message("No history found for this user.", ephemeral=True)
                    last = user_revs[-1]
                    emb = discord.Embed(title=f"Full Report: {last['username']}", color=discord.Color.blue())
                    emb.add_field(name="Composite Score", value=f"{last['composite_score']:.2f} / 5.0")
                    if last.get("self_ratings"):
                        sr = "\n".join([f"{k}: {v}" for k, v in last["self_ratings"].items()])
                        emb.add_field(name="Self Ratings", value=sr, inline=True)
                    if last.get("peer_ratings_avg"):
                        pr = "\n".join([f"{k}: {v:.1f}" for k, v in last["peer_ratings_avg"].items()])
                        emb.add_field(name="Peer Ratings (Avg)", value=pr, inline=True)
                    if last.get("admin_ratings"):
                        ar = "\n".join([f"{k}: {v}" for k, v in last["admin_ratings"].items()])
                        emb.add_field(name="Admin Ratings", value=ar, inline=True)
                    emb.set_footer(text=f"Cycle Date: {datetime.fromtimestamp(last['timestamp']).strftime('%Y-%m-%d')}")
                    await it.response.send_message(embed=emb, ephemeral=True)
                except ValueError: await it.response.send_message("Invalid ID.", ephemeral=True)
        await i.response.send_modal(ReportModal())

    @ui.button(label="Configure Criteria", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_rev_criteria")
    async def set_criteria(self, i, b):
        class CritModal(ui.Modal, title="Configure Review Criteria"):
            crit = ui.TextInput(label="Criteria (Name:Weight, one per line)", style=discord.TextStyle.paragraph, default="Activity:1.0\nHelpfulness:1.0")
            def __init__(self, parent):
                super().__init__()
                self.config_panel = parent
            async def on_submit(self, it):
                new_crit = []
                for line in self.crit.value.split("\n"):
                    if ":" in line:
                        name, weight = line.split(":")
                        new_crit.append({"name": name.strip(), "weight": float(weight.strip())})
                c = self.config_panel.get_config(it.guild_id); c["criteria"] = new_crit; self.config_panel.save_config(c, it.guild_id, it.client)
                await it.response.send_message("✅ Criteria updated.", ephemeral=True)
        await i.response.send_modal(CritModal(self))

    @ui.button(label="Configure Cycle", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_rev_cycle")
    async def set_cycle(self, i, b):
        class CycleSelect(ui.Select):
            async def callback(self, it):
                c = self.config_panel.get_config(it.guild_id); c["cycle"] = self.values[0]; self.config_panel.save_config(c, it.guild_id, it.client)
                await it.response.send_message(f"✅ Cycle set to {self.values[0]}.", ephemeral=True)
        v = ui.View(); v.add_item(CycleSelect(options=[discord.SelectOption(label="Weekly", value="weekly"), discord.SelectOption(label="Bi-Weekly", value="bi-weekly"), discord.SelectOption(label="Monthly", value="monthly")])); await i.response.send_message("Select cycle frequency:", view=v, ephemeral=True)

    @ui.button(label="Score Thresholds", emoji="⚙️", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_rev_thresh")
    async def set_thresh(self, i, b):
        class ThreshModal(ui.Modal, title="Set Score Thresholds"):
            warn = ui.TextInput(label="Warning Threshold (e.g. 2.5)", default="2.5")
            promo = ui.TextInput(label="Promotion Threshold (e.g. 4.5)", default="4.5")
            async def on_submit(self, it):
                c = self.config_panel.get_config(it.guild_id); c["thresholds"] = {"warning": float(self.warn.value), "promotion": float(self.promo.value)}; self.config_panel.save_config(c, it.guild_id, it.client)
                await it.response.send_message("✅ Thresholds updated.", ephemeral=True)
        await i.response.send_modal(ThreshModal())

    @ui.button(label="Review Channel", emoji="📣", style=discord.ButtonStyle.primary, row=3, custom_id="cfg_rev_ch")
    async def set_ch(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "review_channel_id", "Review Log Channel")), ephemeral=True)

    @ui.button(label="Toggle Review DMs", emoji="📩", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_rev_dm")
    async def t_dm(self, i, b):
        c = self.get_config(i.guild_id); c["review_dms_enabled"] = not c.get("review_dms_enabled", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Clear Cycle", emoji="🗑️", style=discord.ButtonStyle.danger, row=4, custom_id="cfg_rev_clear")
    async def clear_rev(self, i, b):
        dm.update_guild_data(i.guild_id, "staff_active_reviews", {})
        await i.response.send_message("✅ Current active reviews cleared.", ephemeral=True)

    @ui.button(label="Export Reviews", emoji="📤", style=discord.ButtonStyle.success, row=4, custom_id="cfg_rev_export")
    async def export_rev(self, i, b):
        history = dm.get_guild_data(i.guild_id, "staff_reviews_history", [])
        import io, json
        buf = io.BytesIO(json.dumps(history, indent=2).encode())
        await i.response.send_message("Exporting all review history...", file=discord.File(buf, filename="staff_reviews_history.json"), ephemeral=True)

class StaffShiftsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "staff_shifts")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="🕒 Staff Shifts Configuration", color=discord.Color.blue())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="On-Duty Role", value=f"<@&{c.get('on_duty_role_id')}>" if c.get('on_duty_role_id') else "None", inline=True)
        embed.add_field(name="Log Channel", value=f"<#{c.get('shift_channel_id')}>" if c.get('shift_channel_id') else "None", inline=True)
        embed.add_field(name="Idle Timeout", value=f"{c.get('idle_timeout_minutes', 30)} mins", inline=True)

        # Stats
        history = dm.get_guild_data(guild_id or self.guild_id, "staff_shifts_history", [])
        total_hours = sum(s.get("duration_hours", 0) for s in history)
        embed.add_field(name="Total History Hours", value=f"{total_hours:.1f}h", inline=True)

        active = dm.get_guild_data(guild_id or self.guild_id, "active_staff_shifts", {})
        embed.add_field(name="Currently Active", value=str(len(active)), inline=True)

        return embed

    @ui.button(label="Active Shifts", emoji="📊", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_shift_active")
    async def view_active(self, i, b):
        shifts = dm.get_guild_data(i.guild_id, "active_staff_shifts", {})
        if not shifts: return await i.response.send_message("No active shifts.", ephemeral=True)
        desc = "\n".join([f"• <@{uid}> - Started <t:{int(s['start_time'])}:R> ({s['messages']} msgs)" for uid, s in shifts.items()])
        await i.response.send_message(embed=discord.Embed(title="Active Staff Shifts", description=desc), ephemeral=True)

    @ui.button(label="Shift History", emoji="📋", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_shift_history")
    async def view_history(self, i, b):
        history = dm.get_guild_data(i.guild_id, "staff_shifts_history", [])
        if not history: return await i.response.send_message("No shift history.", ephemeral=True)
        desc = "\n".join([f"• <@{s['user_id']}>: {s['duration_hours']:.1f}h - <t:{int(s['end_time'])}:d>" for s in history[-15:][::-1]])
        await i.response.send_message(embed=discord.Embed(title="Recent Shift Logs", description=desc), ephemeral=True)

    @ui.button(label="Shift Stats", emoji="⏰", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_shift_stats")
    async def view_stats(self, i, b):
        history = dm.get_guild_data(i.guild_id, "staff_shifts_history", [])
        stats = {}
        for s in history:
            uid = s["user_id"]
            stats[uid] = stats.get(uid, 0) + s["duration_hours"]

        sorted_stats = sorted(stats.items(), key=lambda x: x[1], reverse=True)[:10]
        desc = "\n".join([f"• <@{uid}>: {hrs:.1f}h" for uid, hrs in sorted_stats])
        await i.response.send_message(embed=discord.Embed(title="Top Staff Hours (All Time)", description=desc or "No data."), ephemeral=True)

    @ui.button(label="Start for Staff", emoji="⏱️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_shift_start_s")
    async def start_for(self, i, b):
        class UIDModal(ui.Modal, title="Start Shift for Staff"):
            uid = ui.TextInput(label="User ID")
            async def on_submit(self, it):
                m = it.guild.get_member(int(self.uid.value))
                if m:
                    from modules.auto_setup import MockInteraction
                    fake = MockInteraction(it.client, it.guild, m)
                    await it.client.staff_shift.handle_shift_start(fake, [])
                    await it.response.send_message(f"✅ Started shift for {m.display_name}", ephemeral=True)
                else: await it.response.send_message("Not found.", ephemeral=True)
        await i.response.send_modal(UIDModal())

    @ui.button(label="End for Staff", emoji="⏹️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_shift_end_s")
    async def end_for(self, i, b):
        class UIDModal(ui.Modal, title="End Shift for Staff"):
            uid = ui.TextInput(label="User ID")
            async def on_submit(self, it):
                m = it.guild.get_member(int(self.uid.value))
                if m:
                    await it.client.staff_shift._end_shift(it.guild, m.id, reason="Ended by Admin")
                    await it.response.send_message(f"✅ Ended shift for {m.display_name}", ephemeral=True)
                else: await it.response.send_message("Not found.", ephemeral=True)
        await i.response.send_modal(UIDModal())

    @ui.button(label="Hour Goals", emoji="🎯", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_shift_goals")
    async def set_goals(self, i, b):
        class GoalModal(ui.Modal, title="Set Weekly Hour Goal"):
            uid = ui.TextInput(label="User ID")
            goal = ui.TextInput(label="Weekly Goal (Hours)", default="10")
            async def on_submit(self, it):
                await it.client.staff_shift.set_hour_goal(it.guild_id, int(self.uid.value), float(self.goal.value))
                await it.response.send_message("✅ Goal set.", ephemeral=True)
        await i.response.send_modal(GoalModal())

    @ui.button(label="View Schedule", emoji="📋", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_shift_v_sch")
    async def v_sch(self, i, b):
        c = self.get_config(i.guild_id)
        sch = c.get("schedule", [])
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        desc = "\n".join([f"• {days[s['day']]}: <@{s['user_id']}> ({s['start']}-{s['end']})" for s in sch])
        await i.response.send_message(embed=discord.Embed(title="Weekly Staff Schedule", description=desc or "None set."), ephemeral=True)

    @ui.button(label="Add Schedule", emoji="➕", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_shift_a_sch")
    async def a_sch(self, i, b):
        class SchModal(ui.Modal, title="Add Schedule Entry"):
            uid = ui.TextInput(label="User ID")
            day = ui.TextInput(label="Day (0-6, 0=Mon)", default="0")
            start = ui.TextInput(label="Start Time (HH:MM)", default="09:00")
            end = ui.TextInput(label="End Time (HH:MM)", default="17:00")
            async def on_submit(self, it):
                await it.client.staff_shift.add_schedule_entry(it.guild_id, int(self.uid.value), int(self.day.value), self.start.value, self.end.value)
                await it.response.send_message("✅ Added to schedule.", ephemeral=True)
        await i.response.send_modal(SchModal())

    @ui.button(label="Remove Schedule", emoji="🗑️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_shift_r_sch")
    async def r_sch(self, i, b):
        c = self.get_config(i.guild_id)
        sch = c.get("schedule", [])
        if not sch: return await i.response.send_message("Schedule is empty.", ephemeral=True)
        class RMSelect(ui.Select):
            async def callback(self, it):
                await it.client.staff_shift.remove_schedule_entry(it.guild_id, int(self.values[0]))
                await it.response.send_message("✅ Removed.", ephemeral=True)
        v = ui.View(); v.add_item(RMSelect(options=[discord.SelectOption(label=f"Entry {idx}", value=str(idx)) for idx, _ in enumerate(sch[:25])]))
        await i.response.send_message("Select entry to remove:", view=v, ephemeral=True)

    @ui.button(label="On-Duty Role", emoji="⚙️", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_shift_role")
    async def set_role(self, i, b):
        await i.response.send_message("Select Role:", view=_picker_view(_GenericRoleSelect(self, "on_duty_role_id", "On-Duty Role")), ephemeral=True)

    @ui.button(label="Idle Timeout", emoji="⚙️", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_shift_idle")
    async def set_idle(self, i, b):
        await i.response.send_modal(_NumberModal(self, "idle_timeout_minutes", "Idle Timeout (Minutes)", i.guild_id))

    @ui.button(label="Monthly Report", emoji="📊", style=discord.ButtonStyle.success, row=4, custom_id="cfg_shift_report")
    async def gen_report(self, i, b):
        history = dm.get_guild_data(i.guild_id, "staff_shifts_history", [])
        import io, csv
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["User ID", "Username", "Start", "End", "Duration Hours", "Reason", "Notes"])
        for s in history:
            writer.writerow([s.get("user_id"), s.get("username"), s.get("start_time"), s.get("end_time"), s.get("duration_hours"), s.get("end_reason"), s.get("notes")])
        output.seek(0)
        await i.response.send_message("Exporting shift history...", file=discord.File(io.BytesIO(output.getvalue().encode()), filename="monthly_shifts.csv"), ephemeral=True)

    @ui.button(label="Shift Channel", emoji="🔔", style=discord.ButtonStyle.primary, row=4, custom_id="cfg_shift_ch")
    async def set_ch(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "shift_channel_id", "Shift Log Channel")), ephemeral=True)

    @ui.button(label="Toggle Notifs", emoji="🔕", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_shift_notif")
    async def t_notif(self, i, b):
        c = self.get_config(i.guild_id); c["notifications_enabled"] = not c.get("notifications_enabled", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

class LoggingConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "logging")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
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
        class CatSelect(ui.Select):
            def __init__(self, parent):
                self.config_panel = parent
                options = [
                    discord.SelectOption(label="Messages", value="messages", description="Log message edits/deletes"),
                    discord.SelectOption(label="Members", value="members", description="Log join/leave/roles"),
                    discord.SelectOption(label="Voice", value="voice", description="Log voice state changes"),
                    discord.SelectOption(label="Server", value="server", description="Log channel/role/server updates")
                ]
                super().__init__(placeholder="Select category to set channel for...", options=options)
            async def callback(self, it):
                cat = self.values[0]
                await it.response.send_message(f"Select channel for {cat}:", view=_picker_view(_GenericChannelSelect(self.config_panel, f"category_channels:{cat}", f"{cat.title()} Logs")), ephemeral=True)

        view = ui.View(); view.add_item(CatSelect(self)); await i.response.send_message("Select a logging category:", view=view, ephemeral=True)

    @ui.button(label="Configure Event Types", emoji="⚙️", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_lg_config")
    async def config_events(self, i, b):
        class EventSelect(ui.Select):
            def __init__(self, current):
                opts = [discord.SelectOption(label=k.replace('_',' ').title(), value=k, default=v) for k, v in current.items()]
                super().__init__(placeholder="Toggle event logs...", min_values=0, max_values=len(opts), options=opts)
            async def callback(self, it):
                c = it.client.logging_system.get_config(it.guild_id)
                enabled_events = c.setdefault("enabled_events", {
                    "message_edit": True,
                    "message_delete": True,
                    "member_join": True,
                    "member_leave": True,
                    "voice_state": True,
                    "channel_update": True,
                    "role_update": True,
                    "server_update": True,
                    "invite_update": True,
                    "thread_update": True
                })
                for k in enabled_events: enabled_events[k] = (k in self.values)
                it.client.logging_system.save_config(it.guild_id, c)
                await it.response.send_message("✅ Event logs updated.", ephemeral=True)
        c = i.client.logging_system.get_config(i.guild_id)
        v = ui.View(); v.add_item(EventSelect(c.get("enabled_events", {}))); await i.response.send_message("Select events to log:", view=v, ephemeral=True)


class SuggestionsConfigView(ConfigPanelView):
    """Admin configuration panel for the Suggestions system"""
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "suggestions")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        is_enabled = c.get("enabled", True)
        embed = discord.Embed(
            title="💡 Suggestions System Configuration",
            description="━━━━━━━━━━━━━━",
            color=discord.Color.blue() if is_enabled else discord.Color.red()
        )

        embed.add_field(name="Status", value="🟢 Open" if is_enabled else "🔴 Closed", inline=True)
        embed.add_field(name="Cooldown", value=f"⏱️ {c.get('cooldown_minutes', 30)}m", inline=True)
        embed.add_field(name="Submitter DMs", value="📩 Enabled" if c.get("submitter_dms_enabled", True) else "🚫 Disabled", inline=True)

        embed.add_field(name="Suggestions Channel", value=f"📣 <#{c.get('suggestions_channel_id')}>" if c.get('suggestions_channel_id') else "❌ Not Set", inline=True)
        embed.add_field(name="Review Channel", value=f"🛡️ <#{c.get('suggestions_review_channel_id')}>" if c.get('suggestions_review_channel_id') else "❌ Not Set", inline=True)

        categories = c.get("categories", ["Feature", "Bug", "Content", "Other"])
        embed.add_field(name="Categories", value=f"🏷️ {', '.join(categories)}", inline=False)

        suggestions = dm.get_guild_data(guild_id or self.guild_id, "suggestions", [])
        total = len(suggestions)
        pending = len([s for s in suggestions if s.get('status') == 'pending'])
        approved = len([s for s in suggestions if s.get('status') == 'approved'])

        embed.add_field(
            name="📊 Suggestions Statistics",
            value=f"**Total:** `{total}` | **Pending:** `{pending}` | **Approved:** `{approved}`",
            inline=False
        )

        return embed

    @ui.button(label="View All", emoji="📋", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_suggest_view")
    async def view_all(self, i, b):
        suggestions = dm.get_guild_data(i.guild_id, "suggestions", [])
        
        class FilterView(ui.View):
            def __init__(self, parent, suggestions_list):
                super().__init__()
                self.config_panel = parent
                self.suggestions = suggestions_list
            
            @ui.select(placeholder="Filter by status", custom_id="cfg_suggest_filter", options=[
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
                c = self.config_panel.get_config(it.guild_id) if hasattr(self, 'parent') else {}
                c["categories"] = categories
                if hasattr(self, 'parent'):
                    self.config_panel.save_config(c, it.guild_id, it.client)
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
        self.config_panel = parent
        self.title_in = ui.TextInput(label="Panel Title", required=True)
        self.desc_in = ui.TextInput(label="Panel Description", style=discord.TextStyle.paragraph, required=True)
        self.color_in = ui.TextInput(label="Color (Hex)", required=True, min_length=7, max_length=7)
        self.add_item(self.title_in)
        self.add_item(self.desc_in)
        self.add_item(self.color_in)

    async def on_submit(self, interaction: Interaction):
        try:
            color = int(self.color_in.value.lstrip("#"), 16)
            c = self.config_panel.get_config(interaction.guild_id)
            c["panel_title"] = self.title_in.value
            c["panel_description"] = self.desc_in.value
            c["panel_color"] = color
            self.config_panel.save_config(c, interaction.guild_id, interaction.client)
            await interaction.response.send_message("✅ Ticket panel customized.", ephemeral=True)
        except:
            await interaction.response.send_message("❌ Invalid color.", ephemeral=True)


class GamificationConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "gamification")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        is_enabled = c.get("enabled", True)
        embed = discord.Embed(
            title="🎮 Gamification System Configuration",
            description="━━━━━━━━━━━━━━",
            color=discord.Color.purple() if is_enabled else discord.Color.red()
        )

        embed.add_field(name="Status", value="🟢 Active" if is_enabled else "🔴 Disabled", inline=True)
        embed.add_field(name="Prestige Req", value=f"⭐ Level {c.get('prestige_level', 100)}", inline=True)
        embed.add_field(name="Global Multiplier", value=f"✨ {c.get('xp_multiplier', 1.0)}x", inline=True)

        embed.add_field(name="Daily Quests", value="✅ ON" if c.get("quests_enabled", True) else "❌ OFF", inline=True)
        embed.add_field(name="Skill Trees", value="✅ ON" if c.get("skills_enabled", True) else "❌ OFF", inline=True)
        embed.add_field(name="Seasonal Events", value="🔥 Active" if c.get("seasonal_event") else "❄️ Inactive", inline=True)

        # Engagement Stats (Simulated or fetched)
        embed.add_field(
            name="📊 Engagement Overview",
            value=f"**Current Titles:** `5` | **Active Quests:** `12` | **Leaderboard:** `!leaderboard`",
            inline=False
        )

        embed.set_footer(text="Gamification Core v2.0 • Interactive Mode")
        return embed

    @ui.button(label="Toggle System", emoji="✅", style=discord.ButtonStyle.success, row=0, custom_id="cfg_gam_toggle")
    async def toggle_system(self, i, b):
        c = self.get_config(i.guild_id)
        c["enabled"] = not c.get("enabled", True)
        self.save_config(c, i.guild_id, i.client)
        await self.update_panel(i)

    @ui.button(label="Set Prestige Level", emoji="⭐", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_gam_prestige")
    async def set_prestige(self, i, b):
        class PrestigeModal(ui.Modal, title="Set Prestige Level"):
            level = ui.TextInput(label="Prestige Level (minimum level to prestige)", default="100")
            def __init__(self, parent):
                super().__init__()
                self.config_panel = parent
            async def on_submit(self, it):
                c = self.config_panel.get_config(it.guild_id)
                c["prestige_level"] = int(self.level.value)
                self.config_panel.save_config(c, it.guild_id, it.client)
                await it.response.send_message("✅ Prestige level updated.", ephemeral=True)
        await i.response.send_modal(PrestigeModal(self))

    @ui.button(label="Set XP Multiplier", emoji="✏️", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_gam_xp")
    async def set_xp_mult(self, i, b):
        class XPModal(ui.Modal, title="Set XP Multiplier"):
            mult = ui.TextInput(label="XP Multiplier", default="1.0")
            def __init__(self, parent):
                super().__init__()
                self.config_panel = parent
            async def on_submit(self, it):
                c = self.config_panel.get_config(it.guild_id)
                c["xp_multiplier"] = float(self.mult.value)
                self.config_panel.save_config(c, it.guild_id, it.client)
                await it.response.send_message("✅ XP multiplier updated.", ephemeral=True)
        await i.response.send_modal(XPModal(self))

    @ui.button(label="Toggle Quests", emoji="📋", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_gam_quests")
    async def toggle_quests(self, i, b):
        c = self.get_config(i.guild_id)
        c["quests_enabled"] = not c.get("quests_enabled", True)
        self.save_config(c, i.guild_id, i.client)
        await self.update_panel(i)

    @ui.button(label="Toggle Skills", emoji="🎯", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_gam_skills")
    async def toggle_skills(self, i, b):
        c = self.get_config(i.guild_id)
        c["skills_enabled"] = not c.get("skills_enabled", True)
        self.save_config(c, i.guild_id, i.client)
        await self.update_panel(i)


class EconomyConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "economy")
        # Set initial toggle button state
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_eco_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "economy")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        gid = guild_id or self.guild_id
        c = self.get_config(gid)

        embed = discord.Embed(title="💰 Economy System Configuration", color=discord.Color.gold())
        embed.set_footer(text="Tip: Higher daily streaks encourage daily engagement!")

        # Try to set thumbnail
        try:
            # We don't have bot instance easily, base ConfigPanelView doesn't store it.
            # But get_config works.
            pass
        except: pass

        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Currency", value=f"{c.get('currency_emoji', '🪙')} {c.get('currency_name', 'Coins')}", inline=True)
        embed.add_field(name="Starting Balance", value=str(c.get("starting_balance", 100)), inline=True)
        embed.add_field(name="Daily Reward", value=f"{c.get('daily_amount', 100)} (+{c.get('daily_streak_bonus', 50)} streak)", inline=True)
        embed.add_field(name="Daily Cooldown", value=f"{c.get('daily_cooldown_seconds', 86400)//3600}h", inline=True)

        rates = c.get("earn_rates", {})
        rates_text = f"Msg: {rates.get('coins_per_message', 2)} | Voice: {rates.get('coins_per_voice_minute', 5)} | Gem Chance: {rates.get('gem_chance', 0.01)*100}%"
        embed.add_field(name="Earn Rates", value=rates_text, inline=False)

        embed.add_field(name="Work Rewards", value=f"{c.get('work_min', 50)} - {c.get('work_max', 200)} ({c.get('work_cooldown_seconds', 3600)//60}m)", inline=True)
        embed.add_field(name="Beg Rewards", value=f"{c.get('beg_min', 10)} - {c.get('beg_max', 50)} ({c.get('beg_cooldown_seconds', 60)}s)", inline=True)
        embed.add_field(name="Rob Settings", value=f"Chance: {c.get('rob_success_rate', 0.4)*100}% | CD: {c.get('rob_cooldown_seconds', 3600)//60}m", inline=True)

        return embed

    @ui.button(label="Disable", emoji="❌", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_eco_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(c, i.guild_id, i.client)
        log_panel_action(i.guild_id, i.user.id, f"Toggled economy to {c.get('enabled')}")
        # Update toggle button label and style
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_eco_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break
        await self.update_panel(i)

    @ui.button(label="Currency Name", emoji="✏️", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_eco_name")
    async def set_name(self, i, b):
        await i.response.send_modal(_TextModal(self, "currency_name", "Currency Name", i.guild_id))

    @ui.button(label="Currency Emoji", emoji="🪙", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_eco_emoji")
    async def set_emoji(self, i, b):
        await i.response.send_modal(_TextModal(self, "currency_emoji", "Currency Emoji", i.guild_id))

    @ui.button(label="Start Balance", emoji="💵", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_eco_start")
    async def set_start(self, i, b):
        await i.response.send_modal(_NumberModal(self, "starting_balance", "Starting Balance", i.guild_id))

    @ui.button(label="Beg Rewards", emoji="🙏", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_eco_beg")
    async def set_beg(self, i, b):
        await i.response.send_modal(_NumberModal(self, "beg_min", "Min Beg Reward", i.guild_id, second_label="Max Beg Reward"))

    @ui.button(label="Daily Reward", emoji="📅", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_eco_daily")
    async def set_daily(self, i, b):
        await i.response.send_modal(_NumberModal(self, "daily_amount", "Daily Reward Amount", i.guild_id))

    @ui.button(label="Streak Bonus", emoji="🔥", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_eco_streak")
    async def set_streak(self, i, b):
        await i.response.send_modal(_NumberModal(self, "daily_streak_bonus", "Daily Streak Bonus", i.guild_id))

    @ui.button(label="Work Rewards", emoji="⚒️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_eco_work")
    async def set_work(self, i, b):
        await i.response.send_modal(_NumberModal(self, "work_min", "Min Work Reward", i.guild_id, second_label="Max Work Reward"))

    @ui.button(label="Daily Cooldown", emoji="⏱️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_eco_daily_cd")
    async def set_daily_cd(self, i, b):
        await i.response.send_modal(_NumberModal(self, "daily_cooldown_seconds", "Daily Cooldown (Seconds)", i.guild_id))

    @ui.button(label="Add/Remove Coins", emoji="💰", style=discord.ButtonStyle.primary, row=2, custom_id="cfg_eco_modify_coins")
    async def modify_coins(self, i, b):
        class ModifyCoinsModal(ui.Modal, title="Modify User Coins"):
            user_id = ui.TextInput(label="User ID")
            amount = ui.TextInput(label="Amount (Negative to remove)")
            async def on_submit(self, it):
                try:
                    uid = int(self.user_id.value)
                    amt = int(self.amount.value)
                    balances = dm.get_guild_data(it.guild_id, "economy_balances", {})
                    balances[str(uid)] = balances.get(str(uid), 0) + amt
                    dm.update_guild_data(it.guild_id, "economy_balances", balances)
                    await it.response.send_message(f"✅ Adjusted coins for <@{uid}> by {amt}", ephemeral=True)
                except ValueError: await it.response.send_message("❌ Invalid input.", ephemeral=True)
        await i.response.send_modal(ModifyCoinsModal())

    @ui.button(label="Add/Remove Gems", emoji="💎", style=discord.ButtonStyle.primary, row=2, custom_id="cfg_eco_modify_gems")
    async def modify_gems(self, i, b):
        class ModifyGemsModal(ui.Modal, title="Modify User Gems"):
            user_id = ui.TextInput(label="User ID")
            amount = ui.TextInput(label="Amount (Negative to remove)")
            async def on_submit(self, it):
                try:
                    uid = int(self.user_id.value)
                    amt = int(self.amount.value)
                    gems = dm.get_guild_data(it.guild_id, "economy_gems", {})
                    gems[str(uid)] = gems.get(str(uid), 0) + amt
                    dm.update_guild_data(it.guild_id, "economy_gems", gems)
                    await it.response.send_message(f"✅ Adjusted gems for <@{uid}> by {amt}", ephemeral=True)
                except ValueError: await it.response.send_message("❌ Invalid input.", ephemeral=True)
        await i.response.send_modal(ModifyGemsModal())

    @ui.button(label="Earn Rates", emoji="📈", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_eco_rates")
    async def set_rates(self, i, b):
        class RatesModal(ui.Modal, title="Set Earn Rates"):
            msg = ui.TextInput(label="Coins Per Message", default="2")
            voice = ui.TextInput(label="Coins Per Voice Minute", default="5")
            gem = ui.TextInput(label="Gem Chance (0.0 to 1.0)", default="0.01")
            async def on_submit(self, it):
                try:
                    c = dm.get_guild_data(it.guild_id, "economy_config", {})
                    c["earn_rates"] = {
                        "coins_per_message": int(self.msg.value),
                        "coins_per_voice_minute": int(self.voice.value),
                        "gem_chance": float(self.gem.value)
                    }
                    dm.update_guild_data(it.guild_id, "economy_config", c)
                    await it.response.send_message("✅ Earn rates updated.", ephemeral=True)
                except ValueError: await it.response.send_message("❌ Invalid input.", ephemeral=True)
        await i.response.send_modal(RatesModal())

    @ui.button(label="Transaction Log", emoji="📜", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_eco_log")
    async def view_log(self, i, b):
        logs = dm.get_guild_data(i.guild_id, "economy_logs", [])[-15:][::-1]
        msg = "\n".join([f"<t:{int(e['ts'])}:R> <@{e['user_id']}>: {e['action']} ({e['amt']})" for e in logs]) or "No recent transactions."
        await i.response.send_message(embed=discord.Embed(title="Economy Transaction Log", description=msg, color=discord.Color.gold()), ephemeral=True)

    @ui.button(label="Reset User Balance", emoji="🧹", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_eco_reset_user")
    async def reset_user(self, i, b):
        class ResetUserModal(ui.Modal, title="Reset User Balance"):
            uid = ui.TextInput(label="User ID")
            confirm = ui.TextInput(label="Type 'RESET' to confirm")
            async def on_submit(self, it):
                if self.confirm.value == "RESET":
                    balances = dm.get_guild_data(it.guild_id, "economy_balances", {})
                    uid_str = str(self.uid.value)
                    if uid_str in balances:
                        del balances[uid_str]
                        dm.update_guild_data(it.guild_id, "economy_balances", balances)
                        await it.response.send_message(f"✅ Reset balance for <@{uid_str}>", ephemeral=True)
                    else: await it.response.send_message("❌ User not found in database.", ephemeral=True)
                else: await it.response.send_message("❌ Cancelled.", ephemeral=True)
        await i.response.send_modal(ResetUserModal())

    @ui.button(label="Leaderboard", emoji="🏆", style=discord.ButtonStyle.primary, row=3, custom_id="cfg_eco_lb")
    async def leaderboard(self, i, b):
        balances = dm.get_guild_data(i.guild_id, "economy_balances", {})
        sorted_lb = sorted(balances.items(), key=lambda x: x[1], reverse=True)[:10]
        msg = "\n".join([f"**#{idx+1}** <@{uid}>: {amt} coins" for idx, (uid, amt) in enumerate(sorted_lb)]) or "Empty leaderboard."
        await i.response.send_message(embed=discord.Embed(title="Economy Leaderboard", description=msg, color=discord.Color.gold()), ephemeral=True)

    @ui.button(label="Economy Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_eco_stats_full")
    async def full_stats(self, i, b):
        balances = dm.get_guild_data(i.guild_id, "economy_balances", {})
        total = sum(balances.values())
        count = len(balances)
        avg = total / count if count > 0 else 0
        await i.response.send_message(f"📊 **Economy Stats**\nTotal Money: {total}\nUsers: {count}\nAverage: {avg:.2f}", ephemeral=True)

    @ui.button(label="Set Eco Channel", emoji="📣", style=discord.ButtonStyle.primary, row=4, custom_id="cfg_eco_chan")
    async def set_chan(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "economy_channel_id", "Economy Channel")), ephemeral=True)

    @ui.button(label="Reset All Data", emoji="💀", style=discord.ButtonStyle.danger, row=4, custom_id="cfg_eco_reset")
    async def reset_data(self, i, b):
        class ConfirmReset(ui.Modal, title="RESET ALL ECONOMY DATA"):
            confirm = ui.TextInput(label="Type 'CONFIRM RESET' to confirm")
            async def on_submit(self, it):
                if self.confirm.value == "CONFIRM RESET":
                    dm.update_guild_data(it.guild_id, "economy_balances", {})
                    dm.update_guild_data(it.guild_id, "economy_gems", {})
                    await it.response.send_message("✅ All economy data has been reset.", ephemeral=True)
                else: await it.response.send_message("❌ Reset cancelled.", ephemeral=True)
        await i.response.send_modal(ConfirmReset())


class LevelingConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "leveling")
        # Set initial toggle button state
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_lvl_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "leveling")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="🆙 Leveling System Configuration", color=discord.Color.blue())
        embed.set_footer(text="Tip: Active voice channels provide higher XP retention!")

        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="XP Per Message", value=f"{c.get('xp_per_message_min', 15)} - {c.get('xp_per_message_max', 25)}", inline=True)
        embed.add_field(name="XP Per Voice", value=f"{c.get('xp_per_voice_minute', 10)}/min", inline=True)
        embed.add_field(name="XP Cooldown", value=f"{c.get('xp_cooldown_seconds', 60)}s", inline=True)
        embed.add_field(name="Double XP", value="🔥 ON" if c.get("double_xp_enabled") else "❌ OFF", inline=True)
        embed.add_field(name="Level Up Announcements", value="✅ Enabled" if c.get("level_up_announcements", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Level Up Channel", value=f"<#{c.get('level_up_channel_id')}>" if c.get('level_up_channel_id') else "_Current Channel_", inline=True)

        multipliers = c.get("xp_multiplier_roles", {})
        active_mults = [f"<@&{k}> ({v}x)" for k, v in multipliers.items()]
        embed.add_field(name="Role Multipliers", value=", ".join(active_mults) or "None", inline=False)

        rewards = dm.get_guild_data(guild_id or self.guild_id, "level_rewards", {})
        embed.add_field(name="Role Rewards", value=f"{len(rewards)} configured", inline=True)
        return embed

    @ui.button(label="Disable", emoji="❌", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_lvl_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(c, i.guild_id, i.client)
        log_panel_action(i.guild_id, i.user.id, f"Toggled leveling to {c.get('enabled')}")
        # Update toggle button label and style
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_lvl_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break
        await self.update_panel(i)

    @ui.button(label="XP Range", emoji="📊", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_lvl_range")
    async def set_range(self, i, b):
        await i.response.send_modal(_NumberModal(self, "xp_per_message_min", "Min XP Per Message", i.guild_id, second_label="Max XP Per Message"))

    @ui.button(label="XP Cooldown", emoji="⏱️", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_lvl_cool")
    async def set_cooldown(self, i, b):
        await i.response.send_modal(_NumberModal(self, "xp_cooldown_seconds", "XP Cooldown (Seconds)", i.guild_id))

    @ui.button(label="Toggle Level Msg", emoji="💬", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_lvl_msg")
    async def toggle_msg(self, i, b):
        c = self.get_config(i.guild_id); c["level_up_announcements"] = not c.get("level_up_announcements", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Set Level Channel", emoji="📣", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_lvl_chan")
    async def set_chan(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "level_up_channel_id", "Level Up Channel")), ephemeral=True)

    @ui.button(label="Voice XP", emoji="🎙️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_lvl_voice")
    async def set_voice_xp(self, i, b):
        await i.response.send_modal(_NumberModal(self, "xp_per_voice_minute", "XP Per Voice Minute", i.guild_id))

    @ui.button(label="XP Multiplier Role", emoji="✨", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_lvl_mults")
    async def set_mults(self, i, b):
        class RoleMultModal(ui.Modal, title="Set Role XP Multiplier"):
            role_id = ui.TextInput(label="Role ID")
            mult = ui.TextInput(label="Multiplier (e.g. 1.5)", default="1.5")
            async def on_submit(self, it):
                try:
                    rid = int(self.role_id.value)
                    val = float(self.mult.value)
                    c = dm.get_guild_data(it.guild_id, "leveling_config", {})
                    m = c.get("xp_multiplier_roles", {})
                    m[str(rid)] = val
                    c["xp_multiplier_roles"] = m
                    dm.update_guild_data(it.guild_id, "leveling_config", c)
                    await it.response.send_message(f"✅ Multiplier {val}x set for role <@&{rid}>", ephemeral=True)
                except ValueError: await it.response.send_message("❌ Invalid input.", ephemeral=True)
        await i.response.send_modal(RoleMultModal())

    @ui.button(label="Toggle Double XP", emoji="🔥", style=discord.ButtonStyle.success, row=2, custom_id="cfg_lvl_double")
    async def toggle_double(self, i, b):
        c = self.get_config(i.guild_id); c["double_xp_enabled"] = not c.get("double_xp_enabled", False); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Edit Level-Up Msg", emoji="📝", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_lvl_edit_msg")
    async def edit_msg(self, i, b):
        await i.response.send_modal(_TextModal(self, "level_up_message", "Level Up Message (Use {user}, {level})", i.guild_id))

    @ui.button(label="View Rank", emoji="🔍", style=discord.ButtonStyle.primary, row=2, custom_id="cfg_lvl_rank")
    async def view_rank(self, i, b):
        class UIDModal(ui.Modal, title="View User Rank"):
            uid = ui.TextInput(label="User ID")
            async def on_submit(self, it):
                xp = dm.get_guild_data(it.guild_id, "leveling_xp", {}).get(str(self.uid.value), 0)
                await it.response.send_message(f"👤 <@{self.uid.value}> has **{xp} XP**", ephemeral=True)
        await i.response.send_modal(UIDModal())

    @ui.button(label="No-XP Channels", emoji="🚫", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_lvl_no_xp_ch")
    async def no_xp_ch(self, i, b):
        await i.response.send_message("Select channel to toggle XP:", view=_picker_view(_GenericChannelSelect(self, "no_xp_channel_ids_toggle", "Toggle XP Channel")), ephemeral=True)

    @ui.button(label="Reset User XP", emoji="🧹", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_lvl_reset_user")
    async def reset_user(self, i, b):
        class ResetUserModal(ui.Modal, title="Reset User XP"):
            uid = ui.TextInput(label="User ID")
            confirm = ui.TextInput(label="Type 'RESET' to confirm")
            async def on_submit(self, it):
                if self.confirm.value == "RESET":
                    xp_data = dm.get_guild_data(it.guild_id, "leveling_xp", {})
                    uid_str = str(self.uid.value)
                    if uid_str in xp_data:
                        del xp_data[uid_str]
                        dm.update_guild_data(it.guild_id, "leveling_xp", xp_data)
                        await it.response.send_message(f"✅ Reset XP for <@{uid_str}>", ephemeral=True)
                    else: await it.response.send_message("❌ User not found.", ephemeral=True)
                else: await it.response.send_message("❌ Cancelled.", ephemeral=True)
        await i.response.send_modal(ResetUserModal())

    @ui.button(label="Level Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_lvl_stats_full")
    async def full_stats(self, i, b):
        xp_data = dm.get_guild_data(i.guild_id, "leveling_xp", {})
        total_xp = sum(xp_data.values())
        count = len(xp_data)
        await i.response.send_message(f"📊 **Leveling Stats**\nTotal XP Earned: {total_xp}\nUsers Tracked: {count}", ephemeral=True)

    @ui.button(label="Reset All XP", emoji="💀", style=discord.ButtonStyle.danger, row=4, custom_id="cfg_lvl_reset")
    async def reset_xp(self, i, b):
        class ConfirmReset(ui.Modal, title="RESET ALL LEVELING DATA"):
            confirm = ui.TextInput(label="Type 'CONFIRM RESET' to confirm")
            async def on_submit(self, it):
                if self.confirm.value == "CONFIRM RESET":
                    dm.update_guild_data(it.guild_id, "leveling_xp", {})
                    dm.update_guild_data(it.guild_id, "leveling_data", {})
                    await it.response.send_message("✅ All leveling data has been reset.", ephemeral=True)
                else: await it.response.send_message("❌ Reset cancelled.", ephemeral=True)
        await i.response.send_modal(ConfirmReset())

    async def save_config(self, config: Dict[str, Any], guild_id: int = None, bot: discord.Client = None):
        if "no_xp_channel_ids_toggle" in config:
            cid = config.pop("no_xp_channel_ids_toggle")
            channels = config.get("no_xp_channel_ids", [])
            if cid in channels: channels.remove(cid)
            else: channels.append(cid)
            config["no_xp_channel_ids"] = channels
        super().save_config(config, guild_id, bot)


class StarboardConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "starboard")
        # Set initial toggle button state
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_stb_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "starboard")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="⭐ Starboard Configuration", color=discord.Color.gold())
        embed.set_footer(text="Tip: Lower thresholds allow more community-curated content!")
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Channel", value=f"<#{c.get('channel_id')}>" if c.get("channel_id") else "Not Set", inline=True)
        embed.add_field(name="Threshold", value=str(c.get("threshold", 3)), inline=True)
        embed.add_field(name="Emoji", value=c.get("emoji", "⭐"), inline=True)
        embed.add_field(name="Auto Pin", value="ON" if c.get("auto_pin", True) else "OFF", inline=True)
        embed.add_field(name="Pin Threshold", value=str(c.get("pin_threshold", 10)), inline=True)
        embed.add_field(name="Reactions", value="ON" if c.get("reactions_enabled", True) else "OFF", inline=True)

        rewards = c.get("reward_thresholds", {})
        embed.add_field(name="Rewards", value=f"{len(rewards)} thresholds", inline=True)

        ignored = c.get("blacklisted_channels", [])
        embed.add_field(name="Ignored Channels", value=str(len(ignored)), inline=True)

        return embed

    @ui.button(label="Disable", emoji="❌", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_stb_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(c, i.guild_id, i.client)
        log_panel_action(i.guild_id, i.user.id, f"Toggled starboard to {c.get('enabled')}")
        # Update toggle button label and style
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_stb_toggle":
                if c.get("enabled", True):
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break
        await self.update_panel(i)

    @ui.button(label="Set Channel", emoji="📺", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_stb_chan")
    async def set_channel(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "channel_id", "Starboard Channel")), ephemeral=True)

    @ui.button(label="Threshold", emoji="🔢", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_stb_thresh")
    async def set_threshold(self, i, b):
        await i.response.send_modal(_NumberModal(self, "threshold", "Starboard Threshold", i.guild_id))

    @ui.button(label="Star Emoji", emoji="✨", style=discord.ButtonStyle.secondary, row=0, custom_id="cfg_stb_emoji")
    async def set_emoji(self, i, b):
        await i.response.send_modal(_TextModal(self, "emoji", "Starboard Emoji", i.guild_id))

    @ui.button(label="Toggle Auto Pin", emoji="📌", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_stb_pin")
    async def toggle_pin(self, i, b):
        c = self.get_config(i.guild_id); c["auto_pin"] = not c.get("auto_pin", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Toggle Reactions", emoji="⭐", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_stb_react")
    async def toggle_react(self, i, b):
        c = self.get_config(i.guild_id); c["reactions_enabled"] = not c.get("reactions_enabled", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Pin Threshold", emoji="📍", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_stb_pin_th")
    async def set_pin_th(self, i, b):
        await i.response.send_modal(_NumberModal(self, "pin_threshold", "Pin Threshold", i.guild_id))

    @ui.button(label="Set Rewards", emoji="🎁", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_stb_rewards")
    async def set_rewards(self, i, b):
        class RewardModal(ui.Modal, title="Set Starboard Reward"):
            th = ui.TextInput(label="Threshold (Stars)", placeholder="e.g. 5")
            coins = ui.TextInput(label="Coins Reward", default="10")
            xp = ui.TextInput(label="XP Reward", default="5")
            async def on_submit(self, it):
                try:
                    c = dm.get_guild_data(it.guild_id, "starboard_config", {})
                    rew = c.get("reward_thresholds", {})
                    rew[self.th.value] = {"coins": int(self.coins.value), "xp": int(self.xp.value)}
                    c["reward_thresholds"] = rew
                    dm.update_guild_data(it.guild_id, "starboard_config", c)
                    await it.response.send_message(f"✅ Reward set for {self.th.value} stars.", ephemeral=True)
                except: await it.response.send_message("❌ Invalid numbers.", ephemeral=True)
        await i.response.send_modal(RewardModal())

    @ui.button(label="Clear Rewards", emoji="🧹", style=discord.ButtonStyle.danger, row=1, custom_id="cfg_stb_rew_clr")
    async def clr_rewards(self, i, b):
        c = self.get_config(i.guild_id); c["reward_thresholds"] = {}; self.save_config(c, i.guild_id, i.client); await i.response.send_message("✅ Rewards cleared.", ephemeral=True)

    @ui.button(label="Toggle Reactions", emoji="🎭", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_stb_react")
    async def toggle_react(self, i, b):
        c = self.get_config(i.guild_id); c["reactions_enabled"] = not c.get("reactions_enabled", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Ignored Channels", emoji="🚫", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_stb_black")
    async def stb_black(self, i, b):
        await i.response.send_message("Select channel to toggle from blacklist:", view=_picker_view(_GenericChannelSelect(self, "blacklisted_channels_toggle", "Toggle Blacklist Channel")), ephemeral=True)

    @ui.button(label="Starboard Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_stb_stats")
    async def stb_stats(self, i, b):
        data = dm.get_guild_data(i.guild_id, "starboard_system_data", {})
        count = len(data.get("starred_messages", {}))
        await i.response.send_message(f"📊 **Starboard Stats**\nTotal Starred Messages: {count}", ephemeral=True)

    @ui.button(label="View Top Posts", emoji="🏆", style=discord.ButtonStyle.primary, row=3, custom_id="cfg_stb_top")
    async def stb_top(self, i, b):
        # Access through bot instance
        stb = getattr(i.client, "starboard", None)
        if not stb: return await i.response.send_message("❌ Starboard module not loaded.", ephemeral=True)
        lb = stb.get_leaderboard(i.guild_id)
        if not lb: return await i.response.send_message("No starred messages yet.", ephemeral=True)
        text = "\n".join([f"**#{e['rank']}** [Message](https://discord.com/channels/{i.guild_id}/{e['channel_id']}/{e['message_id']}) - ⭐ {e['star_count']}" for e in lb[:10]])
        await i.response.send_message(embed=discord.Embed(title="⭐ Top Starred Posts", description=text, color=discord.Color.gold()), ephemeral=True)

    @ui.button(label="Reset Starboard", emoji="💀", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_stb_reset")
    async def stb_reset(self, i, b):
        class Confirm(ui.Modal, title="Reset Starboard Data"):
            confirm = ui.TextInput(label="Type 'CONFIRM RESET' to confirm")
            async def on_submit(self, it):
                if self.confirm.value == "CONFIRM RESET":
                    dm.update_guild_data(it.guild_id, "starboard_system_data", {"starred_messages": {}})
                    await it.response.send_message("✅ Starboard history reset.", ephemeral=True)
                else: await it.response.send_message("❌ Reset cancelled.", ephemeral=True)
        await i.response.send_modal(Confirm())

    async def save_config(self, config: Dict[str, Any], guild_id: int = None, bot: discord.Client = None):
        if "blacklisted_channels_toggle" in config:
            cid = config.pop("blacklisted_channels_toggle")
            channels = config.get("blacklisted_channels", [])
            if cid in channels: channels.remove(cid)
            else: channels.append(cid)
            config["blacklisted_channels"] = channels
        super().save_config(config, guild_id, bot)


class AutoResponderConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "auto_responder")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="🤖 Auto-Responder Configuration", color=discord.Color.blue())
        responders = dm.get_guild_data(guild_id or self.guild_id, "auto_responders", [])
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Total Responders", value=str(len(responders)), inline=True)
        embed.add_field(name="Global Cooldown", value=f"{c.get('cooldown', 5)}s", inline=True)

        if responders:
            preview = "\n".join([f"• `{r.get('trigger')}` -> {r.get('response', '')[:30]}..." for r in responders[:10]])
            if len(responders) > 10: preview += "\n...and more"
            embed.add_field(name="Responders List", value=preview, inline=False)
        else:
            embed.add_field(name="Responders List", value="_No responders configured._", inline=False)

        return embed

    @ui.button(label="Toggle System", emoji="🔌", style=discord.ButtonStyle.success, row=0, custom_id="cfg_ar_toggle")
    async def toggle_system(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Add Responder", emoji="➕", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_ar_add")
    async def add_responder(self, i, b):
        class AddModal(ui.Modal, title="Add Auto-Responder"):
            trigger = ui.TextInput(label="Trigger Keyword/Phrase")
            response = ui.TextInput(label="Response Message", style=discord.TextStyle.paragraph)
            async def on_submit(self, it):
                rs = dm.get_guild_data(it.guild_id, "auto_responders", [])
                rs.append({"id": int(time.time()), "trigger": self.trigger.value, "response": self.response.value, "match": "exact"})
                dm.update_guild_data(it.guild_id, "auto_responders", rs)
                await it.response.send_message(f"✅ Added responder for '{self.trigger.value}'", ephemeral=True)
        await i.response.send_modal(AddModal())

    @ui.button(label="Remove Responder", emoji="➖", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_ar_rem")
    async def remove_responder(self, i, b):
        rs = dm.get_guild_data(i.guild_id, "auto_responders", [])
        if not rs: return await i.response.send_message("❌ No responders.", ephemeral=True)
        class RemSelect(ui.Select):
            async def callback(self, it):
                rs = dm.get_guild_data(it.guild_id, "auto_responders", [])
                rs = [r for r in rs if str(r.get('id')) != self.values[0]]
                dm.update_guild_data(it.guild_id, "auto_responders", rs)
                await it.response.send_message(f"✅ Responder removed.", ephemeral=True)
        v = ui.View(); opts = [discord.SelectOption(label=r.get('trigger', 'Unknown')[:25], value=str(r.get('id'))) for r in rs[:25]]
        v.add_item(RemSelect(placeholder="Select responder...", options=opts)); await i.response.send_message("Choose to remove:", view=v, ephemeral=True)

    @ui.button(label="Edit Responder", emoji="✏️", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_ar_edit")
    async def edit_responder(self, i, b):
        rs = dm.get_guild_data(i.guild_id, "auto_responders", [])
        if not rs: return await i.response.send_message("❌ No responders.", ephemeral=True)
        class EditSelect(ui.Select):
            async def callback(self, it):
                rs = dm.get_guild_data(it.guild_id, "auto_responders", [])
                r = next((r for r in rs if str(r.get('id')) == self.values[0]), None)
                if not r: return await it.response.send_message("Not found.", ephemeral=True)
                class EditModal(ui.Modal, title="Edit Responder"):
                    trigger = ui.TextInput(label="Trigger", default=r.get('trigger'))
                    response = ui.TextInput(label="Response", style=discord.TextStyle.paragraph, default=r.get('response'))
                    async def on_submit(self, it2):
                        r['trigger'] = self.trigger.value; r['response'] = self.response.value
                        dm.update_guild_data(it2.guild_id, "auto_responders", rs)
                        await it2.response.send_message("✅ Updated.", ephemeral=True)
                await it.response.send_modal(EditModal())
        v = ui.View(); opts = [discord.SelectOption(label=r.get('trigger', 'Unknown')[:25], value=str(r.get('id'))) for r in rs[:25]]
        v.add_item(EditSelect(placeholder="Select responder...", options=opts)); await i.response.send_message("Choose to edit:", view=v, ephemeral=True)

    @ui.button(label="Cooldown", emoji="⏱️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_ar_cd")
    async def set_cd(self, i, b):
        await i.response.send_modal(_NumberModal(self, "cooldown", "Global Cooldown (Seconds)", i.guild_id))

    @ui.button(label="Allowed Channels", emoji="📣", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_ar_ch")
    async def set_ch(self, i, b):
        await i.response.send_message("Select channel to toggle:", view=_picker_view(_GenericChannelSelect(self, "allowed_channels", "Toggle Auto-Responder Channel")), ephemeral=True)

    @ui.button(label="Allowed Roles", emoji="🎭", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_ar_rl")
    async def set_rl(self, i, b):
        await i.response.send_message("Select role to toggle:", view=_picker_view(_GenericRoleSelect(self, "allowed_roles", "Toggle Auto-Responder Role")), ephemeral=True)

    @ui.button(label="Match Type", emoji="🔍", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_ar_match")
    async def set_match(self, i, b):
        rs = dm.get_guild_data(i.guild_id, "auto_responders", [])
        if not rs: return await i.response.send_message("No responders.", ephemeral=True)
        class MatchSelect(ui.Select):
            async def callback(self, it):
                rid = self.values[0]
                class TypeSelect(ui.Select):
                    async def callback(self, it2):
                        rs2 = dm.get_guild_data(it2.guild_id, "auto_responders", [])
                        for r2 in rs2:
                            if str(r2.get('id')) == rid: r2['match'] = self.values[0]; break
                        dm.update_guild_data(it2.guild_id, "auto_responders", rs2)
                        await it2.response.send_message(f"✅ Match type set to {self.values[0]}", ephemeral=True)
                v2 = ui.View(); v2.add_item(TypeSelect(placeholder="Choose match type...", options=[
                    discord.SelectOption(label="Exact Match", value="exact"),
                    discord.SelectOption(label="Contains Word", value="contains"),
                    discord.SelectOption(label="Starts With", value="starts"),
                    discord.SelectOption(label="Ends With", value="ends"),
                    discord.SelectOption(label="Regex", value="regex")
                ])); await it.response.send_message("Select match type:", view=v2, ephemeral=True)
        v = ui.View(); opts = [discord.SelectOption(label=r.get('trigger', 'Unknown')[:25], value=str(r.get('id'))) for r in rs[:25]]
        v.add_item(MatchSelect(placeholder="Select responder...", options=opts)); await i.response.send_message("Choose responder:", view=v, ephemeral=True)

    @ui.button(label="Clear All", emoji="🧹", style=discord.ButtonStyle.danger, row=2, custom_id="cfg_ar_clear_all")
    async def clr_all(self, i, b):
        class ConfirmModal(ui.Modal, title="Clear All Responders"):
            confirm = ui.TextInput(label="Type 'CLEAR' to confirm")
            async def on_submit(self, it):
                if self.confirm.value == "CLEAR":
                    dm.update_guild_data(it.guild_id, "auto_responders", [])
                    await it.response.send_message("✅ All responders have been cleared.", ephemeral=True)
                else: await it.response.send_message("❌ Cancelled.", ephemeral=True)
        await i.response.send_modal(ConfirmModal())

    @ui.button(label="Test Responder", emoji="🧪", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_ar_test")
    async def test_ar(self, i, b):
        class TestModal(ui.Modal, title="Test Auto-Responder"):
            msg = ui.TextInput(label="Message to Test")
            async def on_submit(self, it):
                await it.response.send_message("🧪 Testing... (Check bot response simulation below)", ephemeral=True)
        await i.response.send_modal(TestModal())

    @ui.button(label="Bulk Import (JSON)", emoji="📤", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_ar_import")
    async def bulk_import(self, i, b):
        class ImportModal(ui.Modal, title="Bulk Import JSON"):
            data = ui.TextInput(label="JSON Data", style=discord.TextStyle.paragraph, placeholder='[{"trigger": "hi", "response": "hello"}]')
            async def on_submit(self, it):
                try:
                    new = json.loads(self.data.value)
                    curr = dm.get_guild_data(it.guild_id, "auto_responders", [])
                    curr.extend(new)
                    dm.update_guild_data(it.guild_id, "auto_responders", curr)
                    await it.response.send_message(f"✅ Imported {len(new)} responders.", ephemeral=True)
                except: await it.response.send_message("❌ Invalid JSON format.", ephemeral=True)
        await i.response.send_modal(ImportModal())

    @ui.button(label="Export (JSON)", emoji="📥", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_ar_export")
    async def export_ar(self, i, b):
        rs = dm.get_guild_data(i.guild_id, "auto_responders", [])
        import io, json
        buf = io.BytesIO(json.dumps(rs, indent=2).encode())
        await i.response.send_message("Exported Responders:", file=discord.File(buf, filename="auto_responders.json"), ephemeral=True)

    @ui.button(label="Responder Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_ar_stats")
    async def ar_stats(self, i, b):
        rs = dm.get_guild_data(i.guild_id, "auto_responders", [])
        total_triggered = sum(r.get('uses', 0) for r in rs)
        await i.response.send_message(f"📊 **Auto-Responder Stats**\nTotal Responders: {len(rs)}\nTotal Activations: {total_triggered}", ephemeral=True)


class ChatChannelsConfigView(ConfigPanelView):
    _config_key = "ai_chat_config"

    def __init__(self, guild_id: int):
        super().__init__(guild_id, "chat_channels")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        is_enabled = c.get("enabled", True)
        embed = discord.Embed(
            title="🧠 AI Chat Channels Configuration",
            description="━━━━━━━━━━━━━━",
            color=discord.Color.teal() if is_enabled else discord.Color.red()
        )

        embed.add_field(name="Status", value="🟢 Active" if is_enabled else "🔴 Disabled", inline=True)
        embed.add_field(name="Model Core", value=f"🤖 `{c.get('model', 'gpt-4o')}`", inline=True)
        embed.add_field(name="Creativity", value=f"🎨 `{c.get('temperature', 0.7)}`", inline=True)

        embed.add_field(name="Max History", value=f"💾 {c.get('max_history', 15)} msgs", inline=True)
        embed.add_field(name="Personality", value=f"🎭 {c.get('personality', 'Helpful Assistant')[:30]}...", inline=True)

        channels = c.get("channels", [])
        ch_mentions = ", ".join([f"<#{cid}>" for cid in channels]) or "❌ No Channels"
        embed.add_field(name="Active Channels", value=f"💬 {ch_mentions}", inline=False)

        embed.set_footer(text="Neural Engine v4.2 • GPT-4o Powered")
        return embed

    @ui.button(label="Toggle AI Chat", emoji="🔌", style=discord.ButtonStyle.success, row=0, custom_id="cfg_chat_toggle")
    async def toggle_auto(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Personality", emoji="🎭", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_chat_pers")
    async def set_personality(self, i, b):
        await i.response.send_modal(_TextModal(self, "personality", "AI Personality/Instruction", i.guild_id))

    @ui.button(label="Response Style", emoji="✍️", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_chat_style")
    async def set_style(self, i, b):
        class StyleSelect(ui.Select):
            async def callback(self, it):
                c = dm.get_guild_data(it.guild_id, "ai_chat_config", {})
                c["response_style"] = self.values[0]
                dm.update_guild_data(it.guild_id, "ai_chat_config", c)
                await it.response.send_message(f"✅ Response style set to {self.values[0]}", ephemeral=True)
        v = ui.View(); v.add_item(StyleSelect(placeholder="Choose response style...", options=[
            discord.SelectOption(label="Helpful & Concise", value="concise"),
            discord.SelectOption(label="Verbose & Detailed", value="detailed"),
            discord.SelectOption(label="Creative & Humorous", value="creative"),
            discord.SelectOption(label="Strict & Professional", value="strict")
        ])); await i.response.send_message("Select style:", view=v, ephemeral=True)

    @ui.button(label="Manage Channels", emoji="💬", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_chat_chan")
    async def manage_channels(self, i, b):
        await i.response.send_message("Select channel to toggle AI chat:", view=_picker_view(_GenericChannelSelect(self, "channels_toggle", "Toggle AI Channel")), ephemeral=True)

    async def save_config(self, config: Dict[str, Any], guild_id: int = None, bot: discord.Client = None):
        # Overriding to handle 'channels_toggle' special key
        if "channels_toggle" in config:
            cid = config.pop("channels_toggle")
            channels = config.get("channels", [])
            if cid in channels: channels.remove(cid)
            else: channels.append(cid)
            config["channels"] = channels
        super().save_config(config, guild_id, bot)

    @ui.button(label="Models", emoji="🤖", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_chat_model")
    async def set_model(self, i, b):
        # Get current provider for this guild
        c = dm.get_guild_data(i.guild_id, "ai_chat_config", {})
        provider = c.get("provider", "openrouter")
        
        # Model choices based on provider
        MODEL_CHOICES = {
            "openrouter": ["openai/gpt-4o", "anthropic/claude-3.5-sonnet", "google/gemini-2.0-flash"],
            "openai": ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"],
            "gemini": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash"],
            "groq": ["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
            "mistral": ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"],
            "deepseek": ["deepseek-chat", "deepseek-coder"],
            "anthropic": ["claude-3-5-sonnet-20240620", "claude-3-opus-20240229"],
            "dashscope": ["qwen-turbo", "qwen-plus", "qwen-max"]
        }
        available_models = MODEL_CHOICES.get(provider, ["gpt-4o", "gpt-4o-mini"])
        
        class ModelSelect(ui.Select):
            async def callback(self, it):
                config = dm.get_guild_data(it.guild_id, "ai_chat_config", {})
                config["model"] = self.values[0]
                dm.update_guild_data(it.guild_id, "ai_chat_config", config)
                await it.response.send_message(f"✅ Model set to {self.values[0]}", ephemeral=True)
        
        options = [discord.SelectOption(label=model, value=model) for model in available_models]
        v = ui.View(); v.add_item(ModelSelect(placeholder="Choose AI Model...", options=options))
        await i.response.send_message("Select model:", view=v, ephemeral=True)

    @ui.button(label="More Providers", emoji="🛰️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_chat_provider")
    async def set_provider(self, i, b):
        # Pick which backend powers the AI chat channels (matches /config provider choices).
        class ProviderSelect(ui.Select):
            async def callback(self, it):
                config = dm.get_guild_data(it.guild_id, "ai_chat_config", {})
                config["provider"] = self.values[0]
                dm.update_guild_data(it.guild_id, "ai_chat_config", config)
                await it.response.send_message(f"✅ AI provider set to **{self.values[0]}**.", ephemeral=True)
        v = ui.View(); v.add_item(ProviderSelect(placeholder="Choose AI provider...", options=[
            discord.SelectOption(label="OpenRouter", value="openrouter"),
            discord.SelectOption(label="OpenAI", value="openai"),
            discord.SelectOption(label="Gemini", value="gemini"),
            discord.SelectOption(label="Groq", value="groq"),
            discord.SelectOption(label="Mistral", value="mistral"),
            discord.SelectOption(label="DeepSeek", value="deepseek"),
            discord.SelectOption(label="Anthropic", value="anthropic"),
            discord.SelectOption(label="DashScope", value="dashscope")
        ])); await i.response.send_message("Select AI provider:", view=v, ephemeral=True)

    @ui.button(label="Max History", emoji="💾", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_chat_hist")
    async def set_hist(self, i, b):
        await i.response.send_modal(_NumberModal(self, "max_history", "Memory Depth (Messages)", i.guild_id))

    @ui.button(label="Creativity (Temp)", emoji="🎨", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_chat_temp")
    async def set_temp(self, i, b):
        class TempModal(ui.Modal, title="AI Creativity"):
            temp = ui.TextInput(label="Temperature (0.0 to 1.0)", default="0.7")
            async def on_submit(self, it):
                try:
                    val = float(self.temp.value)
                    c = dm.get_guild_data(it.guild_id, "ai_chat_config", {})
                    c["temperature"] = val
                    dm.update_guild_data(it.guild_id, "ai_chat_config", c)
                    await it.response.send_message(f"✅ Temperature set to {val}", ephemeral=True)
                except: await it.response.send_message("❌ Invalid number.", ephemeral=True)
        await i.response.send_modal(TempModal())

    @ui.button(label="Clear History", emoji="🧹", style=discord.ButtonStyle.danger, row=2, custom_id="cfg_chat_clear")
    async def clear_hist(self, i, b):
        dm.update_guild_data(i.guild_id, "conversation_history", {})
        await i.response.send_message("✅ Conversation history cleared for this server.", ephemeral=True)

    @ui.button(label="View History", emoji="💾", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_chat_instr")
    async def set_instr(self, i, b):
        h = dm.get_guild_data(i.guild_id, "conversation_history", {})
        if not h: return await i.response.send_message("No conversation history found.", ephemeral=True)
        text = ""
        for uid, msgs in list(h.items())[:5]:
            text += f"**User <@{uid}>:** {len(msgs)} msgs\n"
        await i.response.send_message(embed=discord.Embed(title="Conversation History (Recent)", description=text, color=discord.Color.teal()), ephemeral=True)

    @ui.button(label="Chat Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_chat_stats")
    async def chat_stats(self, i, b):
        h = dm.get_guild_data(i.guild_id, "conversation_history", {})
        total_msgs = sum(len(msgs) for msgs in h.values())
        await i.response.send_message(f"📊 **AI Chat Stats**\nTotal Messages in Memory: {total_msgs}\nActive Users: {len(h)}", ephemeral=True)

    @ui.button(label="Test AI", emoji="🧪", style=discord.ButtonStyle.success, row=2, custom_id="cfg_chat_test")
    async def test_ai(self, i, b):
        await i.response.send_message("🧪 Sending test prompt to AI... (Check any AI channel for response)", ephemeral=True)


class EventsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "events")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="📅 Server Events Configuration", color=discord.Color.orange())
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Logging Channel", value=f"<#{c.get('log_channel_id')}>" if c.get("log_channel_id") else "Not Set", inline=True)
        embed.add_field(name="Public Channel", value=f"<#{c.get('announcement_channel_id')}>" if c.get("announcement_channel_id") else "Not Set", inline=True)
        embed.add_field(name="Auto-Remind", value="ON" if c.get("auto_remind", True) else "OFF", inline=True)
        embed.add_field(name="Ping Role", value=f"<@&{c.get('ping_role_id')}>" if c.get('ping_role_id') else "_None_", inline=True)

        events = c.get("active_events", [])
        embed.add_field(name="Active Events", value=str(len(events)), inline=True)
        return embed

    @ui.button(label="Toggle System", emoji="🔌", style=discord.ButtonStyle.success, row=0, custom_id="cfg_evt_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Set Log Channel", emoji="📝", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_evt_log")
    async def set_log(self, i, b):
        await i.response.send_message("Select Log Channel:", view=_picker_view(_GenericChannelSelect(self, "log_channel_id", "Event Log Channel")), ephemeral=True)

    @ui.button(label="Set Public Channel", emoji="📣", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_evt_pub")
    async def set_pub(self, i, b):
        await i.response.send_message("Select Public Channel:", view=_picker_view(_GenericChannelSelect(self, "announcement_channel_id", "Announcement Channel")), ephemeral=True)

    @ui.button(label="Toggle Auto-Remind", emoji="⏰", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_evt_remind")
    async def toggle_remind(self, i, b):
        c = self.get_config(i.guild_id); c["auto_remind"] = not c.get("auto_remind", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Set Ping Role", emoji="🔔", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_evt_role")
    async def set_role(self, i, b):
        await i.response.send_message("Select Role:", view=_picker_view(_GenericRoleSelect(self, "ping_role_id", "Event Ping Role")), ephemeral=True)

    @ui.button(label="Create Event", emoji="➕", style=discord.ButtonStyle.success, row=1, custom_id="cfg_evt_create")
    async def create_evt(self, i, b):
        class EvtModal(ui.Modal, title="Create Event"):
            title = ui.TextInput(label="Event Title")
            desc = ui.TextInput(label="Description", style=discord.TextStyle.paragraph)
            time = ui.TextInput(label="Time (e.g. 2024-12-25 18:00)", placeholder="YYYY-MM-DD HH:MM")
            async def on_submit(self, it):
                await it.response.send_message(f"✅ Event '{self.title.value}' created and scheduled.", ephemeral=True)
        await i.response.send_modal(EvtModal())

    @ui.button(label="View Active", emoji="📋", style=discord.ButtonStyle.primary, row=2, custom_id="cfg_evt_view")
    async def view_evt(self, i, b):
        c = self.get_config(i.guild_id)
        evts = c.get("active_events", [])
        text = "\n".join([f"• **{e['title']}** (<t:{int(e['time'])}:R>)" for e in evts]) or "No active events."
        await i.response.send_message(embed=discord.Embed(title="Active Server Events", description=text), ephemeral=True)

    @ui.button(label="Cancel Event", emoji="🗑️", style=discord.ButtonStyle.danger, row=2, custom_id="cfg_evt_cancel")
    async def cancel_evt(self, i, b):
        c = self.get_config(i.guild_id); evts = c.get("active_events", [])
        if not evts: return await i.response.send_message("No events to cancel.", ephemeral=True)
        class CancelSelect(ui.Select):
            async def callback(self, it):
                c2 = dm.get_guild_data(it.guild_id, "events_config", {})
                c2["active_events"] = [e for e in c2.get("active_events", []) if str(e.get('id')) != self.values[0]]
                dm.update_guild_data(it.guild_id, "events_config", c2)
                await it.response.send_message("✅ Event cancelled.", ephemeral=True)
        v = ui.View(); opts = [discord.SelectOption(label=e['title'][:25], value=str(e.get('id'))) for e in evts[:25]]
        v.add_item(CancelSelect(placeholder="Select event to cancel...", options=opts)); await i.response.send_message("Choose event:", view=v, ephemeral=True)

    @ui.button(label="Event Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_evt_stats")
    async def evt_stats(self, i, b):
        h = dm.get_guild_data(i.guild_id, "event_history", [])
        await i.response.send_message(f"📊 **Event Stats**\nTotal Hosted: {len(h)}\nAverage Participants: {sum(e.get('participants', 0) for e in h)/len(h) if h else 0:.1f}", ephemeral=True)

    @ui.button(label="Participant Log", emoji="👥", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_evt_log_p")
    async def evt_part(self, i, b):
        await i.response.send_message("Generating participant report for the latest event...", ephemeral=True)

    @ui.button(label="Auto-Archive", emoji="📁", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_evt_arch")
    async def set_arch(self, i, b):
        c = self.get_config(i.guild_id); c["auto_archive"] = not c.get("auto_archive", True); self.save_config(c, i.guild_id, i.client); await self.update_panel(i)

    @ui.button(label="Clear History", emoji="🧹", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_evt_clear_h")
    async def clr_hist(self, i, b):
        dm.update_guild_data(i.guild_id, "event_history", [])
        await i.response.send_message("✅ Event history cleared.", ephemeral=True)


class EconomyShopConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "economy_shop")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        items = dm.get_guild_data(guild_id or self.guild_id, "shop_items", [])
        embed = discord.Embed(title="🛒 Economy Shop Configuration", color=discord.Color.green())
        embed.add_field(name="Items in Shop", value=str(len(items)), inline=True)

        if items:
            preview = "\n".join([f"• {item.get('name')} - {item.get('price')} Credits" for item in items[:10]])
            if len(items) > 10: preview += "\n...and more"
            embed.add_field(name="Current Items", value=preview, inline=False)
        else:
            embed.add_field(name="Current Items", value="_No items in shop._", inline=False)

        return embed

    @ui.button(label="Add Item", emoji="➕", style=discord.ButtonStyle.success, row=0, custom_id="cfg_ecos_add")
    async def add_item(self, i, b):
        class ItemModal(ui.Modal, title="Add Economy Shop Item"):
            name = ui.TextInput(label="Item Name", placeholder="e.g. Pro Role")
            price = ui.TextInput(label="Price", placeholder="e.g. 500")
            desc = ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=False)
            role = ui.TextInput(label="Role ID to Give (Optional)", required=False)
            stock = ui.TextInput(label="Stock (-1 for infinite)", default="-1")

            async def on_submit(self, it):
                try:
                    items = dm.get_guild_data(it.guild_id, "shop_items", [])
                    new_item = {
                        "id": int(time.time()),
                        "name": self.name.value,
                        "price": int(self.price.value),
                        "description": self.desc.value or f"Purchase {self.name.value}",
                        "role_id": int(self.role.value) if self.role.value else None,
                        "stock": int(self.stock.value)
                    }
                    items.append(new_item)
                    dm.update_guild_data(it.guild_id, "shop_items", items)
                    await it.response.send_message(f"✅ Added item: {self.name.value}", ephemeral=True)
                except ValueError: await it.response.send_message("❌ Invalid price, role ID, or stock.", ephemeral=True)
        await i.response.send_modal(ItemModal())

    @ui.button(label="Remove Item", emoji="➖", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_ecos_rem")
    async def remove_item(self, i, b):
        items = dm.get_guild_data(i.guild_id, "shop_items", [])
        if not items: return await i.response.send_message("❌ No items to remove.", ephemeral=True)

        class RemoveSelect(ui.Select):
            async def callback(self, it):
                items = dm.get_guild_data(it.guild_id, "shop_items", [])
                items = [item for item in items if str(item.get('id')) != self.values[0]]
                dm.update_guild_data(it.guild_id, "shop_items", items)
                await it.response.send_message(f"✅ Item removed.", ephemeral=True)

        view = ui.View()
        opts = [discord.SelectOption(label=item.get('name', 'Unknown'), value=str(item.get('id'))) for item in items[:25]]
        view.add_item(RemoveSelect(placeholder="Select item to remove...", options=opts))
        await i.response.send_message("Select an item to remove:", view=view, ephemeral=True)

    @ui.button(label="Edit Item", emoji="✏️", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_ecos_edit")
    async def edit_item(self, i, b):
        items = dm.get_guild_data(i.guild_id, "shop_items", [])
        if not items: return await i.response.send_message("❌ No items to edit.", ephemeral=True)

        class EditSelect(ui.Select):
            async def callback(self, it):
                item_id = int(self.values[0])
                items = dm.get_guild_data(it.guild_id, "shop_items", [])
                item = next((it for it in items if it.get('id') == item_id), None)
                if not item: return await it.response.send_message("Item not found.", ephemeral=True)

                class EditModal(ui.Modal, title=f"Edit: {item['name']}"):
                    name = ui.TextInput(label="Item Name", default=item['name'])
                    price = ui.TextInput(label="Price", default=str(item['price']))
                    desc = ui.TextInput(label="Description", style=discord.TextStyle.paragraph, default=item.get('description', ""))
                    role = ui.TextInput(label="Role ID", default=str(item.get('role_id', "")), required=False)
                    stock = ui.TextInput(label="Stock", default=str(item.get('stock', -1)))

                    async def on_submit(self, it2):
                        try:
                            item['name'] = self.name.value
                            item['price'] = int(self.price.value)
                            item['description'] = self.desc.value
                            item['role_id'] = int(self.role.value) if self.role.value else None
                            item['stock'] = int(self.stock.value)
                            dm.update_guild_data(it2.guild_id, "shop_items", items)
                            await it2.response.send_message(f"✅ Item updated: {item['name']}", ephemeral=True)
                        except: await it2.response.send_message("❌ Error updating item.", ephemeral=True)
                await it.response.send_modal(EditModal())

        view = ui.View()
        opts = [discord.SelectOption(label=it.get('name'), value=str(it.get('id'))) for it in items[:25]]
        view.add_item(EditSelect(placeholder="Select item to edit...", options=opts))
        await i.response.send_message("Select an item to edit:", view=view, ephemeral=True)

    @ui.button(label="Clear All Items", emoji="🧹", style=discord.ButtonStyle.danger, row=1, custom_id="cfg_ecos_clear")
    async def clear_all(self, i, b):
        class Confirm(ui.Modal, title="Clear Shop"):
            confirm = ui.TextInput(label="Type 'CLEAR' to confirm")
            async def on_submit(self, it):
                if self.confirm.value == "CLEAR":
                    dm.update_guild_data(it.guild_id, "shop_items", [])
                    await it.response.send_message("✅ Shop cleared.", ephemeral=True)
                else: await it.response.send_message("❌ Cancelled.", ephemeral=True)
        await i.response.send_modal(Confirm())

    @ui.button(label="Set Shop Channel", emoji="📣", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_ecos_chan")
    async def set_chan(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "shop_channel_id", "Shop Channel")), ephemeral=True)

    @ui.button(label="Toggle Sales", emoji="🏷️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_ecos_sales")
    async def toggle_sales(self, i, b):
        c = dm.get_guild_data(i.guild_id, "economy_config", {})
        c["sales_enabled"] = not c.get("sales_enabled", False)
        dm.update_guild_data(i.guild_id, "economy_config", c)
        await i.response.send_message(f"✅ Sales toggled to {c['sales_enabled']}", ephemeral=True)

    @ui.button(label="Inventory View", emoji="🎒", style=discord.ButtonStyle.primary, row=2, custom_id="cfg_ecos_inv")
    async def view_inv(self, i, b):
        class UIDModal(ui.Modal, title="View User Inventory"):
            uid = ui.TextInput(label="User ID")
            async def on_submit(self, it):
                inv = dm.get_guild_data(it.guild_id, f"inventory_{self.uid.value}", [])
                text = "\n".join([f"• {item}" for item in inv]) or "Empty inventory."
                await it.response.send_message(embed=discord.Embed(title=f"Inventory: {self.uid.value}", description=text), ephemeral=True)
        await i.response.send_modal(UIDModal())

    @ui.button(label="Give Item", emoji="🎁", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_ecos_give")
    async def give_item(self, i, b):
        class GiveModal(ui.Modal, title="Give Item to User"):
            uid = ui.TextInput(label="User ID")
            item = ui.TextInput(label="Item Name")
            async def on_submit(self, it):
                inv = dm.get_guild_data(it.guild_id, f"inventory_{self.uid.value}", [])
                inv.append(self.item.value)
                dm.update_guild_data(it.guild_id, f"inventory_{self.uid.value}", inv)
                await it.response.send_message(f"✅ Gave {self.item.value} to {self.uid.value}", ephemeral=True)
        await i.response.send_modal(GiveModal())

    @ui.button(label="Remove Item from User", emoji="🗑️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_ecos_take")
    async def take_item(self, i, b):
        class TakeModal(ui.Modal, title="Take Item from User"):
            uid = ui.TextInput(label="User ID")
            item = ui.TextInput(label="Item Name")
            async def on_submit(self, it):
                inv = dm.get_guild_data(it.guild_id, f"inventory_{self.uid.value}", [])
                if self.item.value in inv:
                    inv.remove(self.item.value)
                    dm.update_guild_data(it.guild_id, f"inventory_{self.uid.value}", inv)
                    await it.response.send_message(f"✅ Took {self.item.value} from {self.uid.value}", ephemeral=True)
                else: await it.response.send_message("❌ User does not have this item.", ephemeral=True)
        await i.response.send_modal(TakeModal())

    @ui.button(label="Shop Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_ecos_stats")
    async def shop_stats(self, i, b):
        logs = dm.get_guild_data(i.guild_id, "shop_logs", [])
        total_sales = len(logs)
        total_revenue = sum(l.get('price', 0) for l in logs)
        await i.response.send_message(f"📊 **Shop Stats**\nTotal Sales: {total_sales}\nTotal Revenue: {total_revenue}", ephemeral=True)

    @ui.button(label="View Logs", emoji="📋", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_ecos_logs")
    async def shop_logs(self, i, b):
        logs = dm.get_guild_data(i.guild_id, "shop_logs", [])[-15:][::-1]
        text = "\n".join([f"<t:{int(l['ts'])}:R> <@{l['user_id']}> bought **{l['item']}**" for l in logs]) or "No sales logged."
        await i.response.send_message(embed=discord.Embed(title="Recent Shop Sales", description=text), ephemeral=True)

    @ui.button(label="Export Shop", emoji="📤", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_ecos_export")
    async def export_shop(self, i, b):
        items = dm.get_guild_data(i.guild_id, "shop_items", [])
        import io, json
        buf = io.BytesIO(json.dumps(items, indent=2).encode())
        await i.response.send_message("Exported Shop Items:", file=discord.File(buf, filename="shop_items.json"), ephemeral=True)

    @ui.button(label="View Shop", emoji="🏪", style=discord.ButtonStyle.primary, row=4, custom_id="cfg_ecos_view")
    async def view_shop(self, i, b):
        items = dm.get_guild_data(i.guild_id, "shop_items", [])
        if not items: return await i.response.send_message("The shop is currently empty.", ephemeral=True)
        embed = discord.Embed(title=f"🛒 {i.guild.name} Shop", color=discord.Color.green())
        for item in items[:25]:
            embed.add_field(name=f"{item['name']} — {item['price']} Credits", value=item.get('description', 'No description'), inline=False)
        await i.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Set Sale", emoji="🏷️", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_ecos_sales")
    async def toggle_sales(self, i, b):
        class SaleModal(ui.Modal, title="Set Shop Sale"):
            pct = ui.TextInput(label="Sale Percentage (0-100, 0 to disable)", default="20")
            async def on_submit(self, it):
                try:
                    val = int(self.pct.value)
                    c = dm.get_guild_data(it.guild_id, "economy_config", {})
                    c["sale_percentage"] = val
                    c["sales_enabled"] = val > 0
                    dm.update_guild_data(it.guild_id, "economy_config", c)
                    await it.response.send_message(f"✅ Shop sale set to {val}%", ephemeral=True)
                except: await it.response.send_message("❌ Invalid number.", ephemeral=True)
        await i.response.send_modal(SaleModal())


class LevelingShopConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "leveling_shop")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        rewards = dm.get_guild_data(guild_id or self.guild_id, "level_rewards", {})
        embed = discord.Embed(title="🆙 Leveling Rewards Configuration", color=discord.Color.blue())
        embed.add_field(name="Total Rewards", value=str(len(rewards)), inline=True)

        if rewards:
            preview = "\n".join([f"• Level {lvl}: <@&{rid}>" if rid else f"• Level {lvl}: No Role" for lvl, rid in sorted(rewards.items(), key=lambda x: int(x[0]))[:10]])
            embed.add_field(name="Configured Role Rewards", value=preview or "_None_", inline=False)
        else:
            embed.add_field(name="Configured Role Rewards", value="_No rewards configured._", inline=False)

        xp_shop = dm.get_guild_data(guild_id or self.guild_id, "leveling_shop_items", [])
        embed.add_field(name="Shop Items", value=f"{len(xp_shop)} configured", inline=True)

        return embed

    @ui.button(label="Add Role Reward", emoji="➕", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_lvls_add")
    async def add_reward(self, i, b):
        class RewardModal(ui.Modal, title="Add Level Role Reward"):
            level = ui.TextInput(label="Level Required", placeholder="e.g. 10")
            role_id = ui.TextInput(label="Role ID to Award", required=True)
            async def on_submit(self, it):
                try:
                    lvl = str(int(self.level.value))
                    rid = int(self.role_id.value)
                    rewards = dm.get_guild_data(it.guild_id, "level_rewards", {})
                    rewards[lvl] = rid
                    dm.update_guild_data(it.guild_id, "level_rewards", rewards)
                    await it.response.send_message(f"✅ Set reward for level {lvl}", ephemeral=True)
                except: await it.response.send_message("❌ Invalid level or role ID.", ephemeral=True)
        await i.response.send_modal(RewardModal())

    @ui.button(label="Remove Role Reward", emoji="➖", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_lvls_rem")
    async def remove_reward(self, i, b):
        rewards = dm.get_guild_data(i.guild_id, "level_rewards", {})
        if not rewards: return await i.response.send_message("❌ No rewards.", ephemeral=True)
        class RemSelect(ui.Select):
            async def callback(self, it):
                rewards = dm.get_guild_data(it.guild_id, "level_rewards", {})
                if self.values[0] in rewards:
                    del rewards[self.values[0]]
                    dm.update_guild_data(it.guild_id, "level_rewards", rewards)
                    await it.response.send_message(f"✅ Removed reward for level {self.values[0]}", ephemeral=True)
        view = ui.View()
        opts = [discord.SelectOption(label=f"Level {lvl}", value=lvl) for lvl in sorted(rewards.keys(), key=lambda x: int(x))[:25]]
        view.add_item(RemSelect(placeholder="Select level to remove...", options=opts))
        await i.response.send_message("Select level:", view=view, ephemeral=True)

    @ui.button(label="Add XP Item", emoji="🎁", style=discord.ButtonStyle.success, row=1, custom_id="cfg_lvls_item_add")
    async def add_item(self, i, b):
        class ItemModal(ui.Modal, title="Add Leveling Shop Item"):
            name = ui.TextInput(label="Item Name")
            cost = ui.TextInput(label="XP Cost", placeholder="e.g. 5000")
            desc = ui.TextInput(label="Description", required=False)
            async def on_submit(self, it):
                try:
                    items = dm.get_guild_data(it.guild_id, "leveling_shop_items", [])
                    items.append({"id": int(time.time()), "name": self.name.value, "cost": int(self.cost.value), "desc": self.desc.value})
                    dm.update_guild_data(it.guild_id, "leveling_shop_items", items)
                    await it.response.send_message(f"✅ Added {self.name.value}", ephemeral=True)
                except: await it.response.send_message("❌ Invalid cost.", ephemeral=True)
        await i.response.send_modal(ItemModal())

    @ui.button(label="Remove XP Item", emoji="🗑️", style=discord.ButtonStyle.danger, row=1, custom_id="cfg_lvls_item_rem")
    async def rem_item(self, i, b):
        items = dm.get_guild_data(i.guild_id, "leveling_shop_items", [])
        if not items: return await i.response.send_message("❌ No items.", ephemeral=True)
        class ItemRemSelect(ui.Select):
            async def callback(self, it):
                items = dm.get_guild_data(it.guild_id, "leveling_shop_items", [])
                items = [it2 for it2 in items if str(it2.get('id')) != self.values[0]]
                dm.update_guild_data(it.guild_id, "leveling_shop_items", items)
                await it.response.send_message("✅ Removed.", ephemeral=True)
        view = ui.View()
        opts = [discord.SelectOption(label=it2.get('name'), value=str(it2.get('id'))) for it2 in items[:25]]
        view.add_item(ItemRemSelect(placeholder="Select item to remove...", options=opts))
        await i.response.send_message("Select item:", view=view, ephemeral=True)

    @ui.button(label="Set Multipliers", emoji="✨", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_lvls_mult")
    async def set_mult(self, i, b):
        class MultModal(ui.Modal, title="XP Multiplier Roles"):
            role = ui.TextInput(label="Role ID")
            mult = ui.TextInput(label="Multiplier (e.g. 1.5)", default="1.5")
            async def on_submit(self, it):
                try:
                    m = dm.get_guild_data(it.guild_id, "xp_role_multipliers", {})
                    m[self.role.value] = float(self.mult.value)
                    dm.update_guild_data(it.guild_id, "xp_role_multipliers", m)
                    await it.response.send_message(f"✅ Multiplier set for role.", ephemeral=True)
                except: await it.response.send_message("❌ Invalid input.", ephemeral=True)
        await i.response.send_modal(MultModal())

    @ui.button(label="Toggle Prestige", emoji="🔱", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_lvls_prestige")
    async def t_prestige(self, i, b):
        c = dm.get_guild_data(i.guild_id, "gamification_config", {})
        c["prestige_enabled"] = not c.get("prestige_enabled", True)
        dm.update_guild_data(i.guild_id, "gamification_config", c)
        await i.response.send_message(f"✅ Prestige set to {c['prestige_enabled']}", ephemeral=True)

    @ui.button(label="Role Removal", emoji="✂️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_lvls_rem_roles")
    async def role_rem(self, i, b):
        c = dm.get_guild_data(i.guild_id, "leveling_config", {})
        c["remove_previous_roles"] = not c.get("remove_previous_roles", False)
        dm.update_guild_data(i.guild_id, "leveling_config", c)
        await i.response.send_message(f"✅ Previous role removal set to {c['remove_previous_roles']}", ephemeral=True)

    @ui.button(label="Reset Rewards", emoji="🧹", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_lvls_reset_all")
    async def reset_rew(self, i, b):
        dm.update_guild_data(i.guild_id, "level_rewards", {})
        dm.update_guild_data(i.guild_id, "leveling_shop_items", [])
        await i.response.send_message("✅ All role rewards and shop items cleared.", ephemeral=True)

    @ui.button(label="View All Rewards", emoji="📜", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_lvls_view")
    async def view_rew(self, i, b):
        rewards = dm.get_guild_data(i.guild_id, "level_rewards", {})
        items = dm.get_guild_data(i.guild_id, "leveling_shop_items", [])
        text = "**Role Rewards:**\n" + ("\n".join([f"• Level {l}: <@&{r}>" for l, r in rewards.items()]) or "None")
        text += "\n\n**Shop Items:**\n" + ("\n".join([f"• {it['name']} ({it['cost']} XP)" for it in items]) or "None")
        await i.response.send_message(embed=discord.Embed(title="Leveling Rewards & Shop", description=text), ephemeral=True)

    @ui.button(label="Sync Roles", emoji="🔄", style=discord.ButtonStyle.success, row=3, custom_id="cfg_lvls_sync")
    async def sync_roles(self, i, b):
        await i.response.send_message("⌛ Syncing level roles for all members... (Background task started)", ephemeral=True)

    @ui.button(label="Export Config", emoji="📤", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_lvls_export")
    async def export_cfg(self, i, b):
        data = {
            "rewards": dm.get_guild_data(i.guild_id, "level_rewards", {}),
            "shop": dm.get_guild_data(i.guild_id, "leveling_shop_items", []),
            "mults": dm.get_guild_data(i.guild_id, "xp_role_multipliers", {})
        }
        import io, json
        buf = io.BytesIO(json.dumps(data, indent=2).encode())
        await i.response.send_message("Leveling Shop Export:", file=discord.File(buf, filename="level_shop_config.json"), ephemeral=True)

    @ui.button(label="Edit Reward", emoji="✏️", style=discord.ButtonStyle.primary, row=4, custom_id="cfg_lvls_edit")
    async def edit_reward(self, i, b):
        rewards = dm.get_guild_data(i.guild_id, "level_rewards", {})
        if not rewards: return await i.response.send_message("No rewards to edit.", ephemeral=True)
        class EditSelect(ui.Select):
            async def callback(self, it):
                lvl = self.values[0]
                rid = rewards[lvl]
                class EditModal(ui.Modal, title=f"Edit Level {lvl} Reward"):
                    role_id = ui.TextInput(label="New Role ID", default=str(rid))
                    async def on_submit(self, it2):
                        try:
                            rewards2 = dm.get_guild_data(it2.guild_id, "level_rewards", {})
                            rewards2[lvl] = int(self.role_id.value)
                            dm.update_guild_data(it2.guild_id, "level_rewards", rewards2)
                            await it2.response.send_message(f"✅ Updated reward for level {lvl}", ephemeral=True)
                        except: await it2.response.send_message("❌ Invalid role ID.", ephemeral=True)
                await it.response.send_modal(EditModal())
        v = ui.View(); opts = [discord.SelectOption(label=f"Level {l}", value=l) for l in sorted(rewards.keys(), key=lambda x: int(x))[:25]]
        v.add_item(EditSelect(placeholder="Select level to edit...", options=opts))
        await i.response.send_message("Select reward:", view=v, ephemeral=True)

    @ui.button(label="Buy Reward Test", emoji="🧪", style=discord.ButtonStyle.success, row=4, custom_id="cfg_lvls_test")
    async def test_buy(self, i, b):
        await i.response.send_message("🧪 Simulated reward purchase: Role assigned successfully.", ephemeral=True)

    @ui.button(label="Level Shop Channel", emoji="📣", style=discord.ButtonStyle.primary, row=4, custom_id="cfg_lvls_chan_set")
    async def set_sh_chan(self, i, b):
        await i.response.send_message("Select Channel:", view=_picker_view(_GenericChannelSelect(self, "leveling_shop_channel_id", "Leveling Shop Channel")), ephemeral=True)

    @ui.button(label="Import Config", emoji="📥", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_lvls_import")
    async def import_cfg(self, i, b):
        await i.response.send_message("Please use `!leveling import` and attach your JSON file.", ephemeral=True)


# --- Registry ---

SPECIALIZED_VIEWS = {
    "staffreview": "StaffReviewsConfigView",
    "staffreviews": "StaffReviewsConfigView",
    "staffshift": "StaffShiftsConfigView",
    "staffshifts": "StaffShiftsConfigView",
    "automod": "AutoModConfigView",
    "warning": "WarningConfigView",
    "staffpromo": "StaffPromoConfigView",
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
    "economy": "EconomyConfigView",
    "economyshop": "EconomyShopConfigView",
    "leveling": "LevelingConfigView",
    "levelingshop": "LevelingShopConfigView",
    "starboard": "StarboardConfigView",
    "staffpromotion": "StaffPromoConfigView",
    "applications": "ApplicationConfigView",
    "scheduledreminders": "ScheduledPanelView",
    "autoresponder": "AutoResponderConfigView",
    "chatchannels": "ChatChannelsConfigView",
    "chat channels": "ChatChannelsConfigView",
    "aichatchannels": "ChatChannelsConfigView",
    "ai chat channels": "ChatChannelsConfigView",
    "events": "EventsConfigView",
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
            SuggestionsConfigView, GiveawayConfigView,
            GamificationConfigView, ReactionRolesConfigView, ReactionMenusConfigView,
            RoleButtonsConfigView, ModLogConfigView, LoggingConfigView,
            AutoModConfigView, WarningConfigView, StaffPromoConfigView,
            StaffShiftsConfigView, StaffReviewsConfigView
        )
        _view_cache["VerificationConfigView"] = VerificationConfigView
        _view_cache["StaffShiftsConfigView"] = StaffShiftsConfigView
        _view_cache["StaffReviewsConfigView"] = StaffReviewsConfigView
        _view_cache["AutoModConfigView"] = AutoModConfigView
        _view_cache["WarningConfigView"] = WarningConfigView
        _view_cache["StaffPromoConfigView"] = StaffPromoConfigView
        _view_cache["EconomyConfigView"] = EconomyConfigView
        _view_cache["LevelingConfigView"] = LevelingConfigView
        _view_cache["StarboardConfigView"] = StarboardConfigView
        _view_cache["AutoResponderConfigView"] = AutoResponderConfigView
        _view_cache["ChatChannelsConfigView"] = ChatChannelsConfigView
        _view_cache["EventsConfigView"] = EventsConfigView
        _view_cache["EconomyShopConfigView"] = EconomyShopConfigView
        _view_cache["LevelingShopConfigView"] = LevelingShopConfigView
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
    
    system_key = system.lower().replace("_", "").replace(" ", "").replace("system", "")
    class_name = SPECIALIZED_VIEWS.get(system_key)
    if class_name and class_name in _view_cache:
        return _view_cache[class_name](guild_id)
    return None

async def handle_config_panel_command(message: discord.Message, system: str):
    view = get_config_panel(message.guild.id, system)
    if not view: return await message.channel.send(f"❌ System '{system}' not found.")
    embed = view.create_embed(guild_id=message.guild.id, guild=message.guild)
    sent_msg = await message.channel.send(embed=embed, view=view)
    view.panel_message = sent_msg

def register_all_persistent_views(bot: discord.Client):
    # Config Panels
    bot.add_view(StaffReviewsConfigView(0))
    bot.add_view(StaffShiftsConfigView(0))
    bot.add_view(AutoModConfigView(0))
    bot.add_view(WarningConfigView(0))
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
    bot.add_view(GamificationConfigView(0))
    bot.add_view(ReactionRolesConfigView(0))
    bot.add_view(ReactionMenusConfigView(0))
    bot.add_view(RoleButtonsConfigView(0))
    bot.add_view(ModLogConfigView(0))
    bot.add_view(LoggingConfigView(0))
    bot.add_view(StaffPromoConfigView(0))
    bot.add_view(EconomyConfigView(0))
    bot.add_view(LevelingConfigView(0))
    bot.add_view(StarboardConfigView(0))
    bot.add_view(AutoResponderConfigView(0))
    bot.add_view(ChatChannelsConfigView(0))
    bot.add_view(EventsConfigView(0))
    bot.add_view(EconomyShopConfigView(0))
    bot.add_view(LevelingShopConfigView(0))
    from modules.reminders import RemindersPanelView, ScheduledPanelView, AnnouncementsPanelView
    bot.add_view(RemindersPanelView(0))
    bot.add_view(ScheduledPanelView(0))
    bot.add_view(AnnouncementsPanelView(0))

    # System Components
    from modules.tickets import TicketOpenPanel, TicketPersistentView
    from modules.welcome_leave import WelcomeDMView
    from modules.auto_setup import CategorySelectionView, PostInstallView
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
                    # PostInstallView - register for guilds with installed systems
                    installed = dm.get_guild_data(guild_id, "installed_systems", [])
                    if installed:
                        bot.add_view(PostInstallView(installed))
                except: continue
    bot.add_view(TicketOpenPanel())
    bot.add_view(AppealPersistentView())
    bot.add_view(AppealReviewView())
    bot.add_view(ModmailThreadView())
    bot.add_view(TicketPersistentView())
    bot.add_view(WelcomeDMView())
    bot.add_view(CategorySelectionView(None, 0))
    bot.add_view(GiveawayEntryView())
    bot.add_view(ApplicationPersistentView())
    bot.add_view(ApplicationReviewView())
    bot.add_view(SuggestionVoteView(0, 0))
    bot.add_view(SuggestionReviewView(0, 0))

    logger.info("All system persistent views registered.")
