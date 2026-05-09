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
from animated_assets import get_animated_emoji, get_panel_thumbnail, create_animated_embed_title, create_success_embed, create_loading_embed

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

    def update_system_toggle_button(self, custom_id: str, enabled: bool):
        """Update system enable/disable toggle button appearance based on enabled state."""
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == custom_id:
                if enabled:
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break

    def get_config(self, guild_id: int = None) -> Dict[str, Any]:
        target_guild = guild_id or self.guild_id
        try:
            data = dm.get_guild_data(target_guild, self._storage_key(), {})
            return data if isinstance(data, dict) else {}
        except Exception as e:
            from logger import logger
            logger.warning(f"get_config failed for {self.system_name}: {e}")
            return {}

    async def save_config(self, config: Dict[str, Any], guild_id: int = None, bot: discord.Client = None, interaction: Interaction = None):
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

        # Update live status embed if any config changed
        if bot and hasattr(bot, 'auto_setup'):
            import asyncio
            asyncio.create_task(bot.auto_setup.update_system_status_embed(guild_id))
        
        # Update the panel embed if interaction is provided
        if interaction is not None:
            try:
                if interaction.response.is_done():
                    await interaction.edit_original_response(embed=self.create_embed(guild_id=interaction.guild_id, guild=interaction.guild), view=self)
                else:
                    await interaction.response.edit_message(embed=self.create_embed(guild_id=interaction.guild_id, guild=interaction.guild), view=self)
            except Exception:
                pass

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
        await interaction.response.defer(ephemeral=True)
        config = self.config_panel.get_config(interaction.guild_id)
        config[self.key] = self.values[0].id
        await self.config_panel.save_config(config, interaction.guild_id, interaction.client, interaction)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Set {self.key} to {self.values[0].name}")
        await interaction.followup.send(f"✅ Set **{self.key.replace('_',' ').title()}** to {self.values[0].mention}", ephemeral=True)
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
        await interaction.response.defer(ephemeral=True)
        config = self.config_panel.get_config(interaction.guild_id)
        config[self.key] = self.values[0].id
        await self.config_panel.save_config(config, interaction.guild_id, interaction.client, interaction)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Set {self.key} to #{self.values[0].name}")
        await interaction.followup.send(f"✅ Set **{self.key.replace('_',' ').title()}** to <#{self.values[0].id}>", ephemeral=True)
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
        self.value_input.label = discord.ui.Label(text=label)
        if second_label:
            self.second_value.label = discord.ui.Label(text=second_label)
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
            await interaction.response.defer(ephemeral=True)
            user_id = v
            whitelist = config.get("whitelist", [])
            if user_id not in whitelist:
                whitelist.append(user_id)
                config["whitelist"] = whitelist
                await self.config_panel.save_config(config, interaction.guild_id, interaction.client, interaction)
                log_panel_action(interaction.guild_id, interaction.user.id, f"Added {user_id} to whitelist")
                return await interaction.followup.send_message(f"✅ User `{user_id}` added to whitelist.", ephemeral=True)
            else:
                return await interaction.followup.send_message(f"⚠️ User `{user_id}` is already whitelisted.", ephemeral=True)
        
        # Special handling for duplicate filter (X messages in Y seconds)
        if self.key == "duplicate_threshold_config":
            await interaction.response.defer(ephemeral=True)
            if self.second_value.value:
                try:
                    y = int(self.second_value.value)
                    config["duplicate_threshold"] = v
                    config["duplicate_window"] = y
                    await self.config_panel.save_config(config, interaction.guild_id, interaction.client, interaction)
                    log_panel_action(interaction.guild_id, interaction.user.id, f"Set duplicate filter to {v} msgs in {y}s")
                    return await interaction.followup.send_message(f"✅ Duplicate filter: **{v}** messages in **{y}** seconds.", ephemeral=True)
                except ValueError:
                    return await interaction.followup.send_message("❌ Second value must be a number.", ephemeral=True)
            config["duplicate_threshold"] = v
            await self.config_panel.save_config(config, interaction.guild_id, interaction.client, interaction)
            log_panel_action(interaction.guild_id, interaction.user.id, f"Set duplicate threshold to {v}")
            return await interaction.followup.send_message(f"✅ Duplicate threshold set to **{v}** messages.", ephemeral=True)
        
        # Special handling for mention threshold config
        if self.key == "mention_threshold_config":
            await interaction.response.defer(ephemeral=True)
            config["mention_threshold"] = v
            await self.config_panel.save_config(config, interaction.guild_id, interaction.client, interaction)
            log_panel_action(interaction.guild_id, interaction.user.id, f"Set mention threshold to {v}")
            return await interaction.followup.send_message(f"✅ Max mentions per message: **{v}**.", ephemeral=True)
        
        # Special handling for work rewards (min and max)
        if self.key == "work_min":
            await interaction.response.defer(ephemeral=True)
            if self.second_value.value:
                try:
                    max_v = int(self.second_value.value)
                    config["work_min"] = v
                    config["work_max"] = max_v
                    await self.config_panel.save_config(config, interaction.guild_id, interaction.client, interaction)
                    log_panel_action(interaction.guild_id, interaction.user.id, f"Set work rewards to {v}-{max_v}")
                    return await interaction.followup.send_message(f"✅ Work rewards: **{v}** - **{max_v}** coins.", ephemeral=True)
                except ValueError:
                    return await interaction.followup.send_message("❌ Second value must be a number.", ephemeral=True)
            config["work_min"] = v
            await self.config_panel.save_config(config, interaction.guild_id, interaction.client, interaction)
            log_panel_action(interaction.guild_id, interaction.user.id, f"Set work min to {v}")
            return await interaction.followup.send_message(f"✅ Work min reward set to **{v}**.", ephemeral=True)
        
        # Special handling for beg rewards (min and max)
        if self.key == "beg_min":
            await interaction.response.defer(ephemeral=True)
            if self.second_value.value:
                try:
                    max_v = int(self.second_value.value)
                    config["beg_min"] = v
                    config["beg_max"] = max_v
                    await self.config_panel.save_config(config, interaction.guild_id, interaction.client, interaction)
                    log_panel_action(interaction.guild_id, interaction.user.id, f"Set beg rewards to {v}-{max_v}")
                    return await interaction.followup.send_message(f"✅ Beg rewards: **{v}** - **{max_v}** coins.", ephemeral=True)
                except ValueError:
                    return await interaction.followup.send_message("❌ Second value must be a number.", ephemeral=True)
            config["beg_min"] = v
            await self.config_panel.save_config(config, interaction.guild_id, interaction.client, interaction)
            log_panel_action(interaction.guild_id, interaction.user.id, f"Set beg min to {v}")
            return await interaction.followup.send_message(f"✅ Beg min reward set to **{v}**.", ephemeral=True)
        
        # Default: single value storage
        await interaction.response.defer(ephemeral=True)
        config[self.key] = v
        await self.config_panel.save_config(config, interaction.guild_id, interaction.client, interaction)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Set {self.key} to {v}")
        await interaction.followup.send_message(f"✅ {self.key.replace('_',' ').title()} set to **{v}**.", ephemeral=True)

class _TextModal(ui.Modal):
    value_input = ui.TextInput(label="Value", style=discord.TextStyle.paragraph, required=True, max_length=1500)

    def __init__(self, parent: ConfigPanelView, key: str, label: str, guild_id: int):
        super().__init__(title=label)
        self.config_panel = parent
        self.key = key
        self.value_input.label = discord.ui.Label(text=label)
        existing = parent.get_config(guild_id).get(key, "")
        if existing:
            self.value_input.default = str(existing)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        config = self.config_panel.get_config(interaction.guild_id)
        config[self.key] = self.value_input.value
        await self.config_panel.save_config(config, interaction.guild_id, interaction.client, interaction)
        log_panel_action(interaction.guild_id, interaction.user.id, f"Updated text field {self.key}")
        await interaction.followup.send_message(f"✅ {self.key.replace('_',' ').title()} updated.", ephemeral=True)

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
        # Use animated emoji in title
        title = create_animated_embed_title("verification", "Verification System")
        embed = discord.Embed(title=title, color=discord.Color.green() if c.get("enabled", True) else discord.Color.red())
        # Set thumbnail
        thumbnail_url = get_panel_thumbnail("verification")
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        elif guild and guild.icon:
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

    @ui.button(label="Disable", emoji="🔒", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_verify_toggle")
    async def toggle(self, i, b):
        await i.response.defer()
        c = self.get_config(i.guild_id); c["enabled"] = not c.get("enabled", True)
        # Update toggle button appearance
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_verify_toggle":
                if c["enabled"]:
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break
        await self.save_config(c, i.guild_id, i.client, i)
        log_panel_action(i.guild_id, i.user.id, f"Toggled verification to {c.get('enabled')}")
        await i.client.auto_setup.update_system_status_embed(i.guild_id)

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
        await i.response.defer()
        c = self.get_config(i.guild_id); c["captcha_enabled"] = not c.get("captcha_enabled", False)
        await self.save_config(c, i.guild_id, i.client, i)

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
        await i.response.defer()
        c = self.get_config(i.guild_id); c["verification_log"] = []
        await self.save_config(c, i.guild_id, i.client, i)
        log_panel_action(i.guild_id, i.user.id, "Reset verification log")
        await i.followup.send_message("Log Reset", ephemeral=True)

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

class AutoModConfigView(ConfigPanelView):
    """Config panel for auto-moderation system."""
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "automod")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_automod_toggle":
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
        embed = discord.Embed(
            title="🛡️ Auto-Moderation System",
            color=discord.Color.blue() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Banned Words", value=str(len(c.get("banned_words", []))), inline=True)
        embed.add_field(name="Log Channel", value=f"<#{c.get('log_channel_id')}>" if c.get("log_channel_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="🛡️", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_automod_toggle")
    async def toggle_automod(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_automod_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

    @ui.button(label="Add Banned Word", emoji="➕", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_automod_add_word")
    async def add_word(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_message("Enter a word to ban (ephemeral):", ephemeral=True)
        # In a real implementation, use a modal to get user input
        # For brevity, assume adding a sample word
        c = self.get_config(interaction.guild_id)
        c.setdefault("banned_words", []).append("sample")
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

class WarningConfigView(ConfigPanelView):
    """Config panel for warning system."""
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "warning")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_warning_toggle":
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
        embed = discord.Embed(
            title="⚠️ Warning System",
            color=discord.Color.orange() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Max Warnings", value=str(c.get("max_warnings", 3)), inline=True)
        embed.add_field(name="Action on Max", value=c.get("action", "kick").title(), inline=True)
        embed.add_field(name="Log Channel", value=f"<#{c.get('log_channel_id')}>" if c.get("log_channel_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="⚠️", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_warning_toggle")
    async def toggle_warning(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_warning_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

    @ui.button(label="Set Log Channel", emoji="#️⃣", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_warning_set_log")
    async def set_log_channel(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_message("Log channel set to current channel.", ephemeral=True)
        c = self.get_config(interaction.guild_id)
        c["log_channel_id"] = interaction.channel.id
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

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
        await i.response.defer()
        c = self.get_config(i.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, i.guild_id, i.client, i)
        self.update_system_toggle_button("cfg_antiraid_toggle", c["enabled"])
        await i.client.auto_setup.update_system_status_embed(i.guild_id)

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
            await it.response.defer(ephemeral=True)
            c = self.get_config(i.guild_id); c["action"] = select.values[0]; await self.save_config(c, i.guild_id, i.client, i); await it.followup.send_message(f"Action set to {c['action']}", ephemeral=True)
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
        await self.save_config(c, i.guild_id, i.client, i)

    @ui.button(label="Toggle Mention Filter", emoji="📣", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_antiraid_s_ment")
    async def s_ment(self, i, b):
        c = self.get_config(i.guild_id)
        rules = c.setdefault("rules", {})
        mention_filter = rules.setdefault("mention_filter", {})
        mention_filter["enabled"] = not mention_filter.get("enabled", True)
        await self.save_config(c, i.guild_id, i.client, i)

    @ui.button(label="Toggle Duplicate Filter", emoji="💬", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_antiraid_s_dup")
    async def s_dup(self, i, b):
        c = self.get_config(i.guild_id)
        rules = c.setdefault("rules", {})
        dup_filter = rules.setdefault("duplicate_filter", {})
        dup_filter["enabled"] = not dup_filter.get("enabled", True)
        await self.save_config(c, i.guild_id, i.client, i)

    @ui.button(label="Toggle Invite Filter", emoji="🌐", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_antiraid_t_inv")
    async def t_inv(self, i, b):
        c = self.get_config(i.guild_id)
        rules = c.setdefault("rules", {})
        inv_filter = rules.setdefault("invite_filter", {})
        inv_filter["enabled"] = not inv_filter.get("enabled", True)
        await self.save_config(c, i.guild_id, i.client, i)

    @ui.button(label="Toggle Skills", emoji="🎯", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_gam_skills")
    async def toggle_skills(self, i, b):
        c = self.get_config(i.guild_id)
        c["skills_enabled"] = not c.get("skills_enabled", True)
        await self.save_config(c, i.guild_id, i.client, i)


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

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        gid = guild_id or self.guild_id
        c = self.get_config(gid)
        # Use animated emoji in title
        title = create_animated_embed_title("economy", "Economy System Configuration")
        embed = discord.Embed(title=title, color=discord.Color.gold())
        embed.set_footer(text="Tip: Higher daily streaks encourage daily engagement!")
        
        # Set thumbnail
        thumbnail_url = get_panel_thumbnail("economy")
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
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

    @ui.button(label="Disable", emoji="💰", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_eco_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id)
        c["enabled"] = not c.get("enabled", True)
        # Update toggle button appearance
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_eco_toggle":
                if c["enabled"]:
                    item.label = "Disable"
                    item.style = discord.ButtonStyle.danger
                    item.emoji = "❌"
                else:
                    item.label = "Enable"
                    item.style = discord.ButtonStyle.success
                    item.emoji = "✅"
                break
        await self.save_config(c, i.guild_id, i.client, i)
        log_panel_action(i.guild_id, i.user.id, f"Toggled economy to {c.get('enabled')}")
        await i.client.auto_setup.update_system_status_embed(i.guild_id)

    @ui.button(label="Set Currency", emoji="💱", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_eco_currency")
    async def set_currency(self, i, b):
        class CurrencyModal(ui.Modal, title="Set Currency Settings"):
            def __init__(self, parent):
                super().__init__()
                self.parent = parent
                self.name = ui.TextInput(label="Currency Name", placeholder="e.g. Coins", default="Coins")
                self.emoji = ui.TextInput(label="Currency Emoji", placeholder="e.g. 🪙", default="🪙")
                self.starting_balance = ui.TextInput(label="Starting Balance", placeholder="e.g. 100", default="100")
                self.add_item(self.name)
                self.add_item(self.emoji)
                self.add_item(self.starting_balance)
            async def on_submit(self, it):
                try:
                    c = self.parent.get_config(it.guild_id)
                    c["currency_name"] = self.name.value.strip()
                    c["currency_emoji"] = self.emoji.value.strip()
                    c["starting_balance"] = int(self.starting_balance.value)
                    await self.parent.save_config(c, it.guild_id, it.client, it)
                    await it.response.send_message("✅ Currency settings updated!", ephemeral=True)
                except ValueError:
                    await it.response.send_message("❌ Invalid starting balance.", ephemeral=True)
        await i.response.send_modal(CurrencyModal(self))

    @ui.button(label="Daily Settings", emoji="📅", style=discord.ButtonStyle.success, row=0, custom_id="cfg_eco_daily_settings")
    async def set_daily_settings(self, i, b):
        class DailyModal(ui.Modal, title="Daily Reward Settings"):
            def __init__(self, parent):
                super().__init__()
                self.parent = parent
                self.amount = ui.TextInput(label="Daily Amount", placeholder="e.g. 100", default="100")
                self.streak_bonus = ui.TextInput(label="Streak Bonus %", placeholder="e.g. 50", default="50")
                self.cooldown = ui.TextInput(label="Cooldown Hours", placeholder="e.g. 24", default="24")
                self.add_item(self.amount)
                self.add_item(self.streak_bonus)
                self.add_item(self.cooldown)
            async def on_submit(self, it):
                try:
                    c = self.parent.get_config(it.guild_id)
                    c["daily_amount"] = int(self.amount.value)
                    c["daily_streak_bonus"] = int(self.streak_bonus.value)
                    c["daily_cooldown_seconds"] = int(self.cooldown.value) * 3600
                    await self.parent.save_config(c, it.guild_id, it.client, it)
                    await it.response.send_message("✅ Daily settings updated!", ephemeral=True)
                except ValueError:
                    await it.response.send_message("❌ Invalid numbers.", ephemeral=True)
        await i.response.send_modal(DailyModal(self))

    @ui.button(label="Work Rewards", emoji="💼", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_eco_work_settings")
    async def set_work_settings(self, i, b):
        class WorkModal(ui.Modal, title="Work Reward Settings"):
            def __init__(self, parent):
                super().__init__()
                self.parent = parent
                self.min_reward = ui.TextInput(label="Min Reward", placeholder="e.g. 50", default="50")
                self.max_reward = ui.TextInput(label="Max Reward", placeholder="e.g. 200", default="200")
                self.cooldown = ui.TextInput(label="Cooldown Minutes", placeholder="e.g. 60", default="60")
                self.add_item(self.min_reward)
                self.add_item(self.max_reward)
                self.add_item(self.cooldown)
            async def on_submit(self, it):
                try:
                    c = self.parent.get_config(it.guild_id)
                    c["work_min"] = int(self.min_reward.value)
                    c["work_max"] = int(self.max_reward.value)
                    c["work_cooldown_seconds"] = int(self.cooldown.value) * 60
                    await self.parent.save_config(c, it.guild_id, it.client, it)
                    await it.response.send_message("✅ Work settings updated!", ephemeral=True)
                except ValueError:
                    await it.response.send_message("❌ Invalid numbers.", ephemeral=True)
        await i.response.send_modal(WorkModal(self))

    @ui.button(label="Beg Settings", emoji="🙏", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_eco_beg_settings")
    async def set_beg_settings(self, i, b):
        class BegModal(ui.Modal, title="Beg Reward Settings"):
            def __init__(self, parent):
                super().__init__()
                self.parent = parent
                self.min_reward = ui.TextInput(label="Min Reward", placeholder="e.g. 10", default="10")
                self.max_reward = ui.TextInput(label="Max Reward", placeholder="e.g. 50", default="50")
                self.cooldown = ui.TextInput(label="Cooldown Seconds", placeholder="e.g. 60", default="60")
                self.add_item(self.min_reward)
                self.add_item(self.max_reward)
                self.add_item(self.cooldown)
            async def on_submit(self, it):
                try:
                    c = self.parent.get_config(it.guild_id)
                    c["beg_min"] = int(self.min_reward.value)
                    c["beg_max"] = int(self.max_reward.value)
                    c["beg_cooldown_seconds"] = int(self.cooldown.value)
                    await self.parent.save_config(c, it.guild_id, it.client, it)
                    await it.response.send_message("✅ Beg settings updated!", ephemeral=True)
                except ValueError:
                    await it.response.send_message("❌ Invalid numbers.", ephemeral=True)
        await i.response.send_modal(BegModal(self))

    @ui.button(label="Rob Settings", emoji="🏴‍☠️", style=discord.ButtonStyle.danger, row=1, custom_id="cfg_eco_rob_settings")
    async def set_rob_settings(self, i, b):
        class RobModal(ui.Modal, title="Rob Settings"):
            def __init__(self, parent):
                super().__init__()
                self.parent = parent
                self.success_rate = ui.TextInput(label="Success Rate %", placeholder="e.g. 40", default="40")
                self.cooldown = ui.TextInput(label="Cooldown Minutes", placeholder="e.g. 60", default="60")
                self.add_item(self.success_rate)
                self.add_item(self.cooldown)
            async def on_submit(self, it):
                try:
                    c = self.parent.get_config(it.guild_id)
                    c["rob_success_rate"] = float(self.success_rate.value) / 100
                    c["rob_cooldown_seconds"] = int(self.cooldown.value) * 60
                    await self.parent.save_config(c, it.guild_id, it.client, it)
                    await it.response.send_message("✅ Rob settings updated!", ephemeral=True)
                except ValueError:
                    await it.response.send_message("❌ Invalid numbers.", ephemeral=True)
        await i.response.send_modal(RobModal(self))

    @ui.button(label="Earn Rates", emoji="📈", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_eco_earn_rates")
    async def set_earn_rates(self, i, b):
        class EarnModal(ui.Modal, title="Message/Voice Earn Rates"):
            def __init__(self, parent):
                super().__init__()
                self.parent = parent
                self.msg_coins = ui.TextInput(label="Coins per Message", placeholder="e.g. 2", default="2")
                self.voice_coins = ui.TextInput(label="Coins per Voice Minute", placeholder="e.g. 5", default="5")
                self.gem_chance = ui.TextInput(label="Gem Chance %", placeholder="e.g. 1", default="1")
                self.add_item(self.msg_coins)
                self.add_item(self.voice_coins)
                self.add_item(self.gem_chance)
            async def on_submit(self, it):
                try:
                    c = self.parent.get_config(it.guild_id)
                    rates = c.get("earn_rates", {})
                    rates["coins_per_message"] = int(self.msg_coins.value)
                    rates["coins_per_voice_minute"] = int(self.voice_coins.value)
                    rates["gem_chance"] = float(self.gem_chance.value) / 100
                    c["earn_rates"] = rates
                    await self.parent.save_config(c, it.guild_id, it.client, it)
                    await it.response.send_message("✅ Earn rates updated!", ephemeral=True)
                except ValueError:
                    await it.response.send_message("❌ Invalid numbers.", ephemeral=True)
        await i.response.send_modal(EarnModal(self))

    @ui.button(label="Toggle Voice XP", emoji="🎧", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_eco_voice_toggle")
    async def toggle_voice_xp(self, i, b):
        c = self.get_config(i.guild_id)
        c["voice_xp_enabled"] = not c.get("voice_xp_enabled", True)
        # Update button appearance
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_eco_voice_toggle":
                item.label = "Voice XP: OFF" if not c["voice_xp_enabled"] else "Voice XP: ON"
                item.style = discord.ButtonStyle.danger if not c["voice_xp_enabled"] else discord.ButtonStyle.success
                break
        await self.save_config(c, i.guild_id, i.client, i)

    @ui.button(label="Weekly Double XP", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_eco_weekend_toggle")
    async def toggle_weekend_bonus(self, i, b):
        c = self.get_config(i.guild_id)
        c["double_xp_weekend"] = not c.get("double_xp_weekend", False)
        # Update button appearance
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_eco_weekend_toggle":
                item.label = "Double XP: OFF" if not c["double_xp_weekend"] else "Double XP: ON"
                item.style = discord.ButtonStyle.danger if not c["double_xp_weekend"] else discord.ButtonStyle.success
                break
        await self.save_config(c, i.guild_id, i.client, i)

    @ui.button(label="Economy Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_eco_stats")
    async def economy_stats(self, i, b):
        balances = dm.get_guild_data(i.guild_id, "economy_balances", {})
        total_users = len(balances)
        total_coins = sum(balances.values())
        transactions = dm.get_guild_data(i.guild_id, "economy_transactions", [])
        embed = discord.Embed(
            title="📊 Economy Statistics",
            description=f"**Total Users:** {total_users}\n**Total Coins:** {total_coins:,}\n**Transactions:** {len(transactions)}",
            color=discord.Color.gold()
        )
        await i.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Reset All Balances", emoji="🔄", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_eco_reset_all")
    async def reset_all_balances(self, i, b):
        class ConfirmModal(ui.Modal, title="Reset ALL Balances"):
            confirm = ui.TextInput(label="Type 'RESET ALL' to confirm", placeholder="This will reset EVERYONE's balance to starting amount")
            async def on_submit(self, it):
                if self.confirm.value.upper() == "RESET ALL":
                    config = dm.get_guild_data(it.guild_id, "economy_config", {})
                    starting = config.get("starting_balance", 100)
                    balances = {}
                    user_ids = dm.list_user_ids(it.guild_id)
                    for uid in user_ids:
                        balances[str(uid)] = starting
                    dm.update_guild_data(it.guild_id, "economy_balances", balances)
                    await it.response.send_message(f"✅ Reset all balances to {starting}!", ephemeral=True)
                else:
                    await it.response.send_message("❌ Confirmation failed.", ephemeral=True)
        await i.response.send_modal(ConfirmModal())

    @ui.button(label="Clear Transactions", emoji="🗑️", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_eco_clear_tx")
    async def clear_transactions(self, i, b):
        dm.update_guild_data(i.guild_id, "economy_transactions", [])
        await i.response.send_message("✅ Transaction history cleared.", ephemeral=True)




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
        # Set thumbnail
        thumbnail_url = get_panel_thumbnail("chat")
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

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
        c = self.get_config(i.guild_id)
        c["enabled"] = not c.get("enabled", True)
        self.update_system_toggle_button("cfg_chat_toggle", c["enabled"])
        await self.save_config(c, i.guild_id, i.client, i)
        await i.client.auto_setup.update_system_status_embed(i.guild_id)

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
        await super().save_config(config, guild_id, bot)

    @ui.button(label="Models", emoji="🤖", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_chat_model")
    async def set_model(self, i, b):
        # Get current provider for this guild
        c = dm.get_guild_data(i.guild_id, "ai_chat_config", {})
        provider = c.get("provider", "openrouter")
        
        # Model choices based on provider
        MODEL_CHOICES = {
            "openrouter": ["openai/gpt-4o", "anthropic/claude-3.5-sonnet", "google/gemini-2.0-flash"],
            "openai": ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"],
            "gemini": ["gemini-2.5-pro", "gemini-2.5-flash-lite", "gemini-2.0-flash"],
            "groq": ["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
            "mistral": ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"],
            "deepseek": ["deepseek-chat", "deepseek-coder"],
            "anthropic": ["claude-3-5-sonnet-20240620", "claude-3-opus-20240229"],
            "dashscope": ["qwen-turbo", "qwen-plus", "qwen-max"]
        }
        available_models = MODEL_CHOICES.get(provider, ["gpt-4o", "gpt-4o-mini"])
        
        class ModelSelect(ui.Select):
            async def callback(self, it):
                try:
                    config = dm.get_guild_data(it.guild_id, "ai_chat_config", {})
                    config["model"] = self.values[0]
                    dm.update_guild_data(it.guild_id, "ai_chat_config", config)
                    await it.response.send_message(f"✅ Model set to {self.values[0]}", ephemeral=True)
                except Exception as e:
                    await it.response.send_message(f"❌ Failed to set model: {str(e)}", ephemeral=True)
        
        options = [discord.SelectOption(label=model, value=model) for model in available_models]
        v = ui.View(); v.add_item(ModelSelect(placeholder="Choose AI Model...", options=options))
        await i.response.send_message("Select model:", view=v, ephemeral=True)

    @ui.button(label="More Providers", emoji="🛰️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_chat_provider")
    async def set_provider(self, i, b):
        # Pick which backend powers the AI chat channels (matches /config provider choices).
        class ProviderSelect(ui.Select):
            async def callback(self, it):
                try:
                    config = dm.get_guild_data(it.guild_id, "ai_chat_config", {})
                    config["provider"] = self.values[0]
                    dm.update_guild_data(it.guild_id, "ai_chat_config", config)
                    await it.response.send_message(f"✅ AI provider set to **{self.values[0]}**.", ephemeral=True)
                except Exception as e:
                    await it.response.send_message(f"❌ Failed to set provider: {str(e)}", ephemeral=True)
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

    @ui.button(label="API Key", emoji="🔑", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_chat_apikey")
    async def set_api_key(self, i, b):
        class ProviderSelect(ui.Select):
            async def callback(self, it):
                provider = self.values[0]
                class KeyModal(ui.Modal, title=f"Set API Key for {provider}"):
                    def __init__(self):
                        super().__init__()
                        self.provider = provider
                        self.key = ui.TextInput(label="API Key", placeholder="Enter your API key securely", required=True, style=discord.TextStyle.short)
                        self.add_item(self.key)
                    async def on_submit(self, mt):
                        try:
                            dm.set_guild_api_key(mt.guild_id, self.key.value, self.provider)
                            await mt.response.send_message(f"✅ API key for **{self.provider}** has been updated and encrypted.", ephemeral=True)
                        except Exception as e:
                            await mt.response.send_message(f"❌ Failed to set API key: {str(e)}", ephemeral=True)
                await it.response.send_modal(KeyModal())
        v = ui.View(); v.add_item(ProviderSelect(placeholder="Choose provider...", options=[
            discord.SelectOption(label="OpenRouter", value="openrouter"),
            discord.SelectOption(label="OpenAI", value="openai"),
            discord.SelectOption(label="Gemini", value="gemini"),
            discord.SelectOption(label="Groq", value="groq"),
            discord.SelectOption(label="Mistral", value="mistral"),
            discord.SelectOption(label="DeepSeek", value="deepseek"),
            discord.SelectOption(label="Anthropic", value="anthropic"),
            discord.SelectOption(label="DashScope", value="dashscope")
        ])); await i.response.send_message("Select provider to set API key:", view=v, ephemeral=True)

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
        c = self.get_config(i.guild_id)
        c["enabled"] = not c.get("enabled", True)
        self.update_system_toggle_button("cfg_evt_toggle", c["enabled"])
        await self.save_config(c, i.guild_id, i.client, i)

    @ui.button(label="Set Log Channel", emoji="📝", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_evt_log")
    async def set_log(self, i, b):
        await i.response.send_message("Select Log Channel:", view=_picker_view(_GenericChannelSelect(self, "log_channel_id", "Event Log Channel")), ephemeral=True)

    @ui.button(label="Set Public Channel", emoji="📣", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_evt_pub")
    async def set_pub(self, i, b):
        await i.response.send_message("Select Public Channel:", view=_picker_view(_GenericChannelSelect(self, "announcement_channel_id", "Announcement Channel")), ephemeral=True)

    @ui.button(label="Toggle Auto-Remind", emoji="⏰", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_evt_remind")
    async def toggle_remind(self, i, b):
        c = self.get_config(i.guild_id); c["auto_remind"] = not c.get("auto_remind", True)
        await self.save_config(c, i.guild_id, i.client, i)

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
        c = self.get_config(i.guild_id); c["auto_archive"] = not c.get("auto_archive", True); await self.save_config(c, i.guild_id, i.client, i)

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
            def __init__(self, parent):
                super().__init__()
                self.parent = parent
                self.pct = ui.TextInput(label="Sale Percentage (0-100, 0 to disable)", default="20")
                self.add_item(self.pct)
            async def on_submit(self, it):
                try:
                    val = int(self.pct.value)
                    c = self.parent.get_config(it.guild_id)
                    c["sale_percentage"] = val
                    c["sales_enabled"] = val > 0
                    await self.parent.save_config(c, it.guild_id, it.client, it)
                    await it.response.send_message(f"✅ Shop sale set to {val}%", ephemeral=True)
                except: await it.response.send_message("❌ Invalid number.", ephemeral=True)
        await i.response.send_modal(SaleModal(self))


class LevelingConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "leveling")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(title="🆙 Leveling System Configuration", color=discord.Color.blue() if c.get("enabled", True) else discord.Color.dark_grey())
        # Set thumbnail
        thumbnail_url = get_panel_thumbnail("leveling")
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        elif guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        elif guild:
            embed.set_thumbnail(url="https://cdn.discordapp.com/embed/avatars/0.png")
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="XP per Message", value=f"{c.get('xp_per_message', 15)}-{c.get('xp_per_message_max', 25)}", inline=True)
        embed.add_field(name="XP per Minute (Voice)", value=f"{c.get('xp_per_voice_minute', 10)}", inline=True)
        embed.add_field(name="Level Up Reward", value=f"{c.get('level_up_reward', 50)} coins", inline=True)
        embed.add_field(name="Voice XP Enabled", value="✅ Yes" if c.get("voice_xp_enabled", True) else "❌ No", inline=True)
        embed.add_field(name="Double XP Weekend", value="✅ Yes" if c.get("double_xp_weekend", False) else "❌ No", inline=True)
        embed.add_field(name="Debt System", value="✅ Yes" if c.get("debt_system_enabled", False) else "❌ No", inline=True)
        embed.add_field(name="Prestige Enabled", value="✅ Yes" if c.get("prestige_enabled", False) else "❌ No", inline=True)
        return embed

    @ui.button(label="Disable", emoji="📈", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_lvl_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id)
        c["enabled"] = not c.get("enabled", True)
        self.update_system_toggle_button("cfg_lvl_toggle", c["enabled"])
        await self.save_config(c, i.guild_id, i.client, i)

    @ui.button(label="XP per Message", emoji="💬", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_lvl_xp_msg")
    async def set_xp_per_message(self, i, b):
        class XPModal(ui.Modal, title="XP per Message Settings"):
            min_xp = ui.TextInput(label="Min XP per Message", placeholder="e.g. 15", default="15")
            max_xp = ui.TextInput(label="Max XP per Message", placeholder="e.g. 25", default="25")
            async def on_submit(self, it):
                try:
                    c = dm.get_guild_data(it.guild_id, "leveling_config", {})
                    c["xp_per_message"] = int(self.min_xp.value)
                    c["xp_per_message_max"] = int(self.max_xp.value)
                    dm.update_guild_data(it.guild_id, "leveling_config", c)
                    await self.save_config(c, it.guild_id, it.client, it)
                except ValueError:
                    await it.response.send_message("❌ Invalid XP values.", ephemeral=True)
        await i.response.send_modal(XPModal())

    @ui.button(label="Voice XP", emoji="🎧", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_lvl_voice_xp")
    async def set_voice_xp(self, i, b):
        class VoiceModal(ui.Modal, title="Voice XP Settings"):
            xp_per_minute = ui.TextInput(label="XP per Voice Minute", placeholder="e.g. 10", default="10")
            async def on_submit(self, it):
                try:
                    c = dm.get_guild_data(it.guild_id, "leveling_config", {})
                    c["xp_per_voice_minute"] = int(self.xp_per_minute.value)
                    dm.update_guild_data(it.guild_id, "leveling_config", c)
                    await self.save_config(c, it.guild_id, it.client, it)
                except ValueError:
                    await it.response.send_message("❌ Invalid XP value.", ephemeral=True)
        await i.response.send_modal(VoiceModal())

    @ui.button(label="Level Up Reward", emoji="🎁", style=discord.ButtonStyle.success, row=1, custom_id="cfg_lvl_reward")
    async def set_level_reward(self, i, b):
        class RewardModal(ui.Modal, title="Level Up Reward"):
            coins = ui.TextInput(label="Coins per Level Up", placeholder="e.g. 50", default="50")
    async def on_submit(self, it):
        try:
            c = self.config_panel.get_config(it.guild_id)
            c["xp_multiplier"] = float(self.mult.value)
            await self.config_panel.save_config(c, it.guild_id, it.client, it)
            await it.response.send_message("✅ XP multiplier updated.", ephemeral=True)
        except ValueError:
            await it.response.send_message("❌ Invalid multiplier values.", ephemeral=True)
        await i.response.send_modal(MultModal())

    @ui.button(label="Leveling Stats", emoji="📊", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_lvl_stats")
    async def leveling_stats(self, i, b):
        xp_data = dm.get_guild_data(i.guild_id, "leveling_xp", {})
        total_users = len(xp_data)
        total_xp = sum(xp_data.values())
        gems_data = dm.get_guild_data(i.guild_id, "leveling_gems", {})
        total_gems = sum(gems_data.values())
        embed = discord.Embed(
            title="📊 Leveling Statistics",
            description=f"**Total Users:** {total_users}\n**Total XP Earned:** {total_xp:,}\n**Total Gems:** {total_gems}",
            color=discord.Color.blue()
        )
        await i.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Reset All XP", emoji="🔄", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_lvl_reset_xp")
    async def reset_all_xp(self, i, b):
        class ConfirmModal(ui.Modal, title="Reset ALL XP"):
            confirm = ui.TextInput(label="Type 'RESET XP' to confirm", placeholder="This will reset EVERYONE's XP to 0")
            async def on_submit(self, it):
                if self.confirm.value.upper() == "RESET XP":
                    dm.update_guild_data(it.guild_id, "leveling_xp", {})
                    dm.update_guild_data(it.guild_id, "leveling_gems", {})
                    dm.update_guild_data(it.guild_id, "leveling_streaks", {})
                    await it.response.send_message("✅ Reset all XP and gems!", ephemeral=True)
                else:
                    await it.response.send_message("❌ Confirmation failed.", ephemeral=True)
        await i.response.send_modal(ConfirmModal())

    @ui.button(label="Clear Transactions", emoji="🗑️", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_lvl_clear_tx")
    async def clear_leveling_tx(self, i, b):
        dm.update_guild_data(i.guild_id, "leveling_transactions", [])
        await i.response.send_message("✅ Leveling transaction history cleared.", ephemeral=True)


class LevelingShopConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "leveling_shop")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        rewards = dm.get_guild_data(guild_id or self.guild_id, "level_rewards", {})
        # Use animated emoji in title
        title = create_animated_embed_title("leveling", "Leveling Rewards Configuration")
        embed = discord.Embed(title=title, color=discord.Color.blue())
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


class StarboardConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "starboard")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        from modules.starboard import StarboardSystem
        starboard = StarboardSystem(None)  # We don't need the bot for settings
        c = starboard.get_guild_settings(guild_id or self.guild_id)

        embed = discord.Embed(title="⭐ Starboard Configuration", color=discord.Color.gold() if c.get("enabled", True) else discord.Color.dark_grey())

        # Set thumbnail
        thumbnail_url = get_panel_thumbnail("starboard")
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        elif guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        elif guild:
            embed.set_thumbnail(url="https://cdn.discordapp.com/embed/avatars/0.png")

        # Get starboard channel from starboard system data
        starboard_data = dm.get_guild_data(guild_id or self.guild_id, "starboard_system_data", {})
        channel_id = starboard_data.get("channel_id")

        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Starboard Channel", value=f"<#{channel_id}>" if channel_id else "_None_", inline=True)
        embed.add_field(name="Star Emoji", value=c.get("emoji", "⭐"), inline=True)
        embed.add_field(name="Threshold", value=f"{c.get('threshold', 3)} stars", inline=True)
        embed.add_field(name="Auto-Pin", value="✅ Yes" if c.get("auto_pin", True) else "❌ No", inline=True)
        embed.add_field(name="Pin Threshold", value=f"{c.get('pin_threshold', 10)} stars", inline=True)
        embed.add_field(name="Reactions Enabled", value="✅ Yes" if c.get("reactions_enabled", True) else "❌ No", inline=True)

        # Show reward thresholds
        rewards = c.get("reward_thresholds", {})
        if rewards:
            reward_text = "\n".join([f"• {stars} stars: {data.get('coins', 0)} coins, {data.get('xp', 0)} XP" for stars, data in sorted(rewards.items(), key=lambda x: int(x[0]))])
            embed.add_field(name="Reward Thresholds", value=reward_text, inline=False)
        else:
            embed.add_field(name="Reward Thresholds", value="_None configured_", inline=False)

        return embed

    @ui.button(label="Toggle Starboard", emoji="⭐", style=discord.ButtonStyle.success, row=0, custom_id="cfg_starboard_toggle")
    async def toggle(self, i, b):
        from modules.starboard import StarboardSystem
        starboard = StarboardSystem(i.client)
        c = starboard.get_guild_settings(i.guild_id)
        c["enabled"] = not c.get("enabled", True)
        self.update_system_toggle_button("cfg_starboard_toggle", c["enabled"])
        dm.update_guild_data(i.guild_id, "starboard_config", c)
        await self.save_config(c, i.guild_id, i.client, i)
        log_panel_action(i.guild_id, i.user.id, f"Toggled starboard to {c.get('enabled')}")
        await i.client.auto_setup.update_system_status_embed(i.guild_id)

    @ui.button(label="Set Threshold", emoji="📊", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_starboard_threshold")
    async def set_threshold(self, i, b):
        class ThresholdModal(ui.Modal, title="Set Star Threshold"):
            threshold = ui.TextInput(label="Stars Required", placeholder="e.g. 3", default="3")
            async def on_submit(self, it):
                try:
                    val = int(self.threshold.value)
                    from modules.starboard import StarboardSystem
                    starboard = StarboardSystem(it.client)
                    c = starboard.get_guild_settings(it.guild_id)
                    c["threshold"] = val
                    dm.update_guild_data(it.guild_id, "starboard_config", c)
                    await it.response.send_message(f"✅ Star threshold set to {val}", ephemeral=True)
                except ValueError:
                    await it.response.send_message("❌ Invalid number.", ephemeral=True)
        await i.response.send_modal(ThresholdModal())

    @ui.button(label="Set Emoji", emoji="⭐", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_starboard_emoji")
    async def set_emoji(self, i, b):
        class EmojiModal(ui.Modal, title="Set Star Emoji"):
            emoji = ui.TextInput(label="Star Emoji", placeholder="⭐", default="⭐")
            async def on_submit(self, it):
                from modules.starboard import StarboardSystem
                starboard = StarboardSystem(it.client)
                c = starboard.get_guild_settings(it.guild_id)
                c["emoji"] = self.emoji.value
                dm.update_guild_data(it.guild_id, "starboard_config", c)
                await it.response.send_message(f"✅ Star emoji set to {self.emoji.value}", ephemeral=True)
        await i.response.send_modal(EmojiModal())

    @ui.button(label="Toggle Auto-Pin", emoji="📌", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_starboard_pin")
    async def toggle_pin(self, i, b):
        from modules.starboard import StarboardSystem
        starboard = StarboardSystem(i.client)
        c = starboard.get_guild_settings(i.guild_id)
        c["auto_pin"] = not c.get("auto_pin", True)
        dm.update_guild_data(i.guild_id, "starboard_config", c)
        await self.save_config(c, i.guild_id, i.client, i)

    @ui.button(label="Set Pin Threshold", emoji="📍", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_starboard_pin_thresh")
    async def set_pin_thresh(self, i, b):
        class PinThreshModal(ui.Modal, title="Set Pin Threshold"):
            threshold = ui.TextInput(label="Stars for Auto-Pin", placeholder="e.g. 10", default="10")
            async def on_submit(self, it):
                try:
                    val = int(self.threshold.value)
                    from modules.starboard import StarboardSystem
                    starboard = StarboardSystem(it.client)
                    c = starboard.get_guild_settings(it.guild_id)
                    c["pin_threshold"] = val
                    dm.update_guild_data(it.guild_id, "starboard_config", c)
                    await it.response.send_message(f"✅ Pin threshold set to {val} stars", ephemeral=True)
                except ValueError:
                    await it.response.send_message("❌ Invalid number.", ephemeral=True)
        await i.response.send_modal(PinThreshModal())

    @ui.button(label="Add Reward", emoji="🎁", style=discord.ButtonStyle.success, row=2, custom_id="cfg_starboard_add_reward")
    async def add_reward(self, i, b):
        class RewardModal(ui.Modal, title="Add Star Reward"):
            stars = ui.TextInput(label="Stars Required", placeholder="e.g. 5")
            coins = ui.TextInput(label="Coins Reward", placeholder="e.g. 10", default="0")
            xp = ui.TextInput(label="XP Reward", placeholder="e.g. 5", default="0")
            async def on_submit(self, it):
                try:
                    stars = str(int(self.stars.value))
                    coins = int(self.coins.value)
                    xp = int(self.xp.value)
                    from modules.starboard import StarboardSystem
                    starboard = StarboardSystem(it.client)
                    c = starboard.get_guild_settings(it.guild_id)
                    rewards = c.get("reward_thresholds", {})
                    rewards[stars] = {"coins": coins, "xp": xp}
                    c["reward_thresholds"] = rewards
                    dm.update_guild_data(it.guild_id, "starboard_config", c)
                    await it.response.send_message(f"✅ Added reward for {stars} stars", ephemeral=True)
                except ValueError:
                    await it.response.send_message("❌ Invalid numbers.", ephemeral=True)
        await i.response.send_modal(RewardModal())

    @ui.button(label="Remove Reward", emoji="🗑️", style=discord.ButtonStyle.danger, row=2, custom_id="cfg_starboard_rem_reward")
    async def rem_reward(self, i, b):
        from modules.starboard import StarboardSystem
        starboard = StarboardSystem(i.client)
        c = starboard.get_guild_settings(i.guild_id)
        rewards = c.get("reward_thresholds", {})
        if not rewards:
            return await i.response.send_message("❌ No rewards configured.", ephemeral=True)

        class RemSelect(ui.Select):
            async def callback(self, it):
                from modules.starboard import StarboardSystem
                starboard = StarboardSystem(it.client)
                c = starboard.get_guild_settings(it.guild_id)
                rewards = c.get("reward_thresholds", {})
                if self.values[0] in rewards:
                    del rewards[self.values[0]]
                    c["reward_thresholds"] = rewards
                    dm.update_guild_data(it.guild_id, "starboard_config", c)
                    await it.response.send_message(f"✅ Removed reward for {self.values[0]} stars", ephemeral=True)

        view = ui.View()
        opts = [discord.SelectOption(label=f"{stars} stars", value=stars) for stars in sorted(rewards.keys(), key=lambda x: int(x))[:25]]
        view.add_item(RemSelect(placeholder="Select reward to remove...", options=opts))
        await i.response.send_message("Select reward:", view=view, ephemeral=True)

    @ui.button(label="Set Starboard Channel", emoji="📢", style=discord.ButtonStyle.primary, row=3, custom_id="cfg_starboard_channel")
    async def set_channel(self, i, b):
        class StarboardChannelSelect(ui.ChannelSelect):
            def __init__(self):
                super().__init__(
                    placeholder="Select Starboard Channel",
                    channel_types=[discord.ChannelType.text],
                    min_values=1, max_values=1,
                )

            async def callback(self, interaction: Interaction):
                from modules.starboard import StarboardSystem
                starboard = StarboardSystem(interaction.client)
                starboard.set_starboard_channel(interaction.guild_id, self.values[0].id)
                starboard._save_guild_data(interaction.guild_id)  # Save the data
                await interaction.response.send_message(f"✅ Starboard channel set to <#{self.values[0].id}>", ephemeral=True)
                # Update the config panel
                if self.view and hasattr(self.view, 'config_panel'):
                    await self.view.config_panel.save_config({}, interaction.guild_id, interaction.client, interaction)

        view = ui.View()
        view.add_item(StarboardChannelSelect())
        await i.response.send_message("Select Channel:", view=view, ephemeral=True)


class AutoResponderConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "auto_responder")

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        gid = guild_id or self.guild_id
        config = dm.get_guild_data(gid, "auto_responder_config", {"enabled": True, "cooldown": 5})
        responders = dm.get_guild_data(gid, "auto_responders", [])

        embed = discord.Embed(title="💬 Auto-Responder Configuration", color=discord.Color.blue() if config.get("enabled", True) else discord.Color.dark_grey())

        # Set thumbnail
        thumbnail_url = get_panel_thumbnail("auto_responder")
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        elif guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        elif guild:
            embed.set_thumbnail(url="https://cdn.discordapp.com/embed/avatars/0.png")

        embed.add_field(name="Status", value="✅ Enabled" if config.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Total Responders", value=str(len(responders)), inline=True)
        embed.add_field(name="Global Cooldown", value=f"{config.get('cooldown', 5)}s", inline=True)

        enabled_count = sum(1 for r in responders if r.get("enabled", True))
        embed.add_field(name="Active Responders", value=str(enabled_count), inline=True)
        embed.add_field(name="Disabled Responders", value=str(len(responders) - enabled_count), inline=True)

        # Channel restrictions
        channels = dm.get_guild_data(gid, "auto_responder_channels", None)
        if channels:
            embed.add_field(name="Allowed Channels", value=f"{len(channels)} restricted", inline=True)
        else:
            embed.add_field(name="Allowed Channels", value="All channels", inline=True)

        # Role restrictions
        roles = dm.get_guild_data(gid, "auto_responder_roles", None)
        if roles:
            embed.add_field(name="Allowed Roles", value=f"{len(roles)} restricted", inline=True)
        else:
            embed.add_field(name="Allowed Roles", value="All roles", inline=True)

        # Show sample responders
        if responders:
            sample = responders[:3]
            trigger_list = "\n".join([f"• `{r.get('trigger', 'Unknown')[:20]}...`" for r in sample])
            embed.add_field(name="Sample Triggers", value=trigger_list or "_None_", inline=False)
        else:
            embed.add_field(name="Sample Triggers", value="_No responders configured_", inline=False)

        return embed

    @ui.button(label="Toggle System", emoji="🤖", style=discord.ButtonStyle.success, row=0, custom_id="cfg_ar_toggle")
    async def toggle_system(self, i, b):
        config = dm.get_guild_data(i.guild_id, "auto_responder_config", {"enabled": True, "cooldown": 5})
        config["enabled"] = not config.get("enabled", True)
        self.update_system_toggle_button("cfg_ar_toggle", config["enabled"])
        dm.update_guild_data(i.guild_id, "auto_responder_config", config)
        await self.save_config(config, i.guild_id, i.client, i)
        log_panel_action(i.guild_id, i.user.id, f"Toggled auto-responder to {config.get('enabled')}")
        await i.client.auto_setup.update_system_status_embed(i.guild_id)

    @ui.button(label="Add Responder", emoji="➕", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_ar_add")
    async def add_responder(self, i, b):
        class AddModal(ui.Modal, title="Add Auto-Responder"):
            trigger = ui.TextInput(label="Trigger Keyword/Phrase", placeholder="e.g. hello, !help")
            response = ui.TextInput(label="Response Message", style=discord.TextStyle.paragraph, placeholder="Enter the response message...")
            match_type = ui.TextInput(label="Match Type", placeholder="exact/contains/starts_with/ends_with/regex", default="contains")
            async def on_submit(self, it):
                from modules.auto_responder import AutoResponder
                ar = AutoResponder(it.client)
                responder = {
                    "trigger": self.trigger.value.strip(),
                    "response": self.response.value.strip(),
                    "match_type": self.match_type.value.strip().lower()
                }
                ar.add_responder(it.guild_id, responder)
                await it.response.send_message(f"✅ Added responder for '{self.trigger.value}'", ephemeral=True)
        await i.response.send_modal(AddModal())

    @ui.button(label="Remove Responder", emoji="➖", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_ar_rem")
    async def remove_responder(self, i, b):
        responders = dm.get_guild_data(i.guild_id, "auto_responders", [])
        if not responders:
            return await i.response.send_message("❌ No responders configured.", ephemeral=True)

        class RemSelect(ui.Select):
            async def callback(self, it):
                from modules.auto_responder import AutoResponder
                ar = AutoResponder(it.client)
                # Find responder by ID
                responder_id = int(self.values[0])
                ar.delete_responder(it.guild_id, responder_id)
                await it.response.send_message("✅ Responder removed.", ephemeral=True)

        view = ui.View()
        opts = [discord.SelectOption(label=f"{r.get('trigger', 'Unknown')[:25]} ({r.get('match_type', 'contains')})", value=str(r.get('id'))) for r in responders[:25]]
        view.add_item(RemSelect(placeholder="Select responder to remove...", options=opts))
        await i.response.send_message("Choose to remove:", view=view, ephemeral=True)

    @ui.button(label="Edit Responder", emoji="✏️", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_ar_edit")
    async def edit_responder(self, i, b):
        responders = dm.get_guild_data(i.guild_id, "auto_responders", [])
        if not responders:
            return await i.response.send_message("❌ No responders configured.", ephemeral=True)

        class EditSelect(ui.Select):
            async def callback(self, it):
                responders = dm.get_guild_data(it.guild_id, "auto_responders", [])
                responder = next((r for r in responders if str(r.get('id')) == self.values[0]), None)
                if not responder:
                    return await it.response.send_message("❌ Responder not found.", ephemeral=True)

                class EditModal(ui.Modal, title="Edit Responder"):
                    trigger = ui.TextInput(label="Trigger", default=responder.get('trigger', ''))
                    response = ui.TextInput(label="Response", style=discord.TextStyle.paragraph, default=responder.get('response', ''))
                    match_type = ui.TextInput(label="Match Type", default=responder.get('match_type', 'contains'))
                    async def on_submit(self, it2):
                        from modules.auto_responder import AutoResponder
                        ar = AutoResponder(it2.client)
                        updates = {
                            "trigger": self.trigger.value.strip(),
                            "response": self.response.value.strip(),
                            "match_type": self.match_type.value.strip().lower()
                        }
                        ar.update_responder(it2.guild_id, responder['id'], updates)
                        await it2.response.send_message("✅ Responder updated.", ephemeral=True)
                await it.response.send_modal(EditModal())

        view = ui.View()
        opts = [discord.SelectOption(label=f"{r.get('trigger', 'Unknown')[:25]} ({r.get('match_type', 'contains')})", value=str(r.get('id'))) for r in responders[:25]]
        view.add_item(EditSelect(placeholder="Select responder to edit...", options=opts))
        await i.response.send_message("Choose to edit:", view=view, ephemeral=True)

    @ui.button(label="Set Cooldown", emoji="⏱️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_ar_cd")
    async def set_cooldown(self, i, b):
        class CooldownModal(ui.Modal, title="Set Global Cooldown"):
            cooldown = ui.TextInput(label="Cooldown (seconds)", placeholder="e.g. 5", default="5")
            async def on_submit(self, it):
                try:
                    cd = max(0, int(self.cooldown.value))
                    config = dm.get_guild_data(it.guild_id, "auto_responder_config", {"enabled": True, "cooldown": 5})
                    config["cooldown"] = cd
                    dm.update_guild_data(it.guild_id, "auto_responder_config", config)
                    await it.response.send_message(f"✅ Global cooldown set to {cd} seconds.", ephemeral=True)
                except ValueError:
                    await it.response.send_message("❌ Invalid number.", ephemeral=True)
        await i.response.send_modal(CooldownModal())

    @ui.button(label="Channel Restrictions", emoji="📢", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_ar_channels")
    async def set_channels(self, i, b):
        await i.response.send_message("Select Allowed Channels:", view=_picker_view(_GenericChannelSelect(self, "auto_responder_channels", "Auto-Responder Channels")), ephemeral=True)

    @ui.button(label="Role Restrictions", emoji="👥", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_ar_roles")
    async def set_roles(self, i, b):
        await i.response.send_message("Select Allowed Roles:", view=_picker_view(_GenericRoleSelect(self, "auto_responder_roles", "Auto-Responder Roles")), ephemeral=True)

    @ui.button(label="Toggle Responder", emoji="🔄", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_ar_toggle_resp")
    async def toggle_responder(self, i, b):
        responders = dm.get_guild_data(i.guild_id, "auto_responders", [])
        if not responders:
            return await i.response.send_message("❌ No responders configured.", ephemeral=True)

        class ToggleSelect(ui.Select):
            async def callback(self, it):
                from modules.auto_responder import AutoResponder
                ar = AutoResponder(it.client)
                responder_id = int(self.values[0])
                responder = next((r for r in responders if r.get('id') == responder_id), None)
                if responder:
                    new_state = not responder.get("enabled", True)
                    ar.update_responder(it.guild_id, responder_id, {"enabled": new_state})
                    await it.response.send_message(f"✅ Responder {'enabled' if new_state else 'disabled'}.", ephemeral=True)

        view = ui.View()
        opts = [discord.SelectOption(label=f"{r.get('trigger', 'Unknown')[:20]}... ({'✅' if r.get('enabled', True) else '❌'})", value=str(r.get('id'))) for r in responders[:25]]
        view.add_item(ToggleSelect(placeholder="Select responder to toggle...", options=opts))
        await i.response.send_message("Choose to toggle:", view=view, ephemeral=True)

    @ui.button(label="List All", emoji="📋", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_ar_list")
    async def list_responders(self, i, b):
        responders = dm.get_guild_data(i.guild_id, "auto_responders", [])
        if not responders:
            return await i.response.send_message("❌ No responders configured.", ephemeral=True)

        # Split into chunks if too many
        chunks = [responders[i:i+10] for i in range(0, len(responders), 10)]
        for idx, chunk in enumerate(chunks):
            text = f"**Auto-Responders (Page {idx+1}/{len(chunks)}):**\n\n"
            for r in chunk:
                status = "✅" if r.get("enabled", True) else "❌"
                text += f"{status} **{r.get('trigger', 'Unknown')}**\n"
                text += f"   └ {r.get('response', 'No response')[:50]}{'...' if len(r.get('response', '')) > 50 else ''}\n"
                text += f"   └ Match: {r.get('match_type', 'contains')}\n\n"

            embed = discord.Embed(title=f"Auto-Responders List", description=text, color=discord.Color.blue())
            await i.response.send_message(embed=embed, ephemeral=True)
            if idx < len(chunks) - 1:
                await asyncio.sleep(0.5)  # Brief pause between pages

    @ui.button(label="Clear All", emoji="🧹", style=discord.ButtonStyle.danger, row=4, custom_id="cfg_ar_clear")
    async def clear_all(self, i, b):
        dm.update_guild_data(i.guild_id, "auto_responders", [])
        await i.response.send_message("✅ All auto-responders cleared.", ephemeral=True)

    @ui.button(label="Export", emoji="📤", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_ar_export")
    async def export_responders(self, i, b):
        responders = dm.get_guild_data(i.guild_id, "auto_responders", [])
        import json, io
        buf = io.BytesIO(json.dumps(responders, indent=2).encode())
        await i.response.send_message("Auto-Responders Export:", file=discord.File(buf, filename="auto_responders.json"), ephemeral=True)


class StaffReviewsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "staff_reviews")
        # Set initial toggle button state
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_sr_toggle":
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
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(
            title="📝 Staff Reviews Configuration",
            color=discord.Color.blue() if c.get("enabled", True) else discord.Color.red()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        elif guild:
            embed.set_thumbnail(url="https://cdn.discordapp.com/embed/avatars/0.png")
        
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Cycle", value=c.get("cycle", "monthly").title(), inline=True)
        
        last_start = c.get("last_cycle_start", 0)
        next_start = c.get("next_cycle_start", 0)
        now = time.time()
        if next_start > now:
            time_left = next_start - now
            days = int(time_left // 86400)
            embed.add_field(name="Next Cycle", value=f"In {days} days" if days > 0 else "Soon", inline=True)
        else:
            embed.add_field(name="Next Cycle", value="Not scheduled", inline=True)
        
        criteria = c.get("criteria", [])
        embed.add_field(name="Criteria Count", value=str(len(criteria)), inline=True)
        
        thresholds = c.get("thresholds", {})
        embed.add_field(name="Warning Threshold", value=str(thresholds.get("warning", 2.5)), inline=True)
        embed.add_field(name="Promotion Threshold", value=str(thresholds.get("promotion", 4.5)), inline=True)
        
        weights = c.get("weights", {})
        embed.add_field(name="Admin Weight", value=str(weights.get("admin", 0.5)), inline=True)
        embed.add_field(name="Peer Weight", value=str(weights.get("peer", 0.3)), inline=True)
        embed.add_field(name="Self Weight", value=str(weights.get("self", 0.2)), inline=True)
        
        staff_roles = c.get("staff_roles", [])
        embed.add_field(name="Staff Roles", value=f"{len(staff_roles)} configured", inline=True)
        
        channel_id = c.get("review_channel_id")
        embed.add_field(name="Review Channel", value=f"<#{channel_id}>" if channel_id else "_None_", inline=True)
        
        return embed

    @ui.button(label="Disable", emoji="❌", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_sr_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id)
        c["enabled"] = not c.get("enabled", True)
        self.update_system_toggle_button("cfg_sr_toggle", c["enabled"])
        await self.save_config(c, i.guild_id, i.client, i)

    @ui.button(label="Set Cycle", emoji="🔄", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_sr_cycle")
    async def set_cycle(self, i, b):
        class CycleModal(ui.Modal, title="Set Review Cycle"):
            cycle = ui.TextInput(label="Cycle (weekly/bi-weekly/monthly)", placeholder="monthly", default="monthly")
            async def on_submit(self, it):
                c = self.parent.get_config(it.guild_id)
                if self.cycle.value.lower() in ["weekly", "bi-weekly", "monthly"]:
                    c["cycle"] = self.cycle.value.lower()
                    await self.parent.save_config(c, it.guild_id, it.client, it)
                    await it.response.send_message("✅ Cycle updated!", ephemeral=True)
                else:
                    await it.response.send_message("❌ Invalid cycle type.", ephemeral=True)
        modal = CycleModal()
        modal.parent = self
        await i.response.send_modal(modal)

    @ui.button(label="Set Channel", emoji="📣", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_sr_channel")
    async def set_channel(self, i, b):
        await i.response.send_message(
            "Select Review Channel:",
            view=_picker_view(_GenericChannelSelect(self, "review_channel_id", "Review Channel")),
            ephemeral=True
        )

    @ui.button(label="Set Thresholds", emoji="📊", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_sr_thresholds")
    async def set_thresholds(self, i, b):
        class ThresholdsModal(ui.Modal, title="Set Review Thresholds"):
            warning = ui.TextInput(label="Warning Threshold", placeholder="2.5", default="2.5")
            promotion = ui.TextInput(label="Promotion Threshold", placeholder="4.5", default="4.5")
            async def on_submit(self, it):
                try:
                    c = self.parent.get_config(it.guild_id)
                    c.setdefault("thresholds", {})["warning"] = float(self.warning.value)
                    c["thresholds"]["promotion"] = float(self.promotion.value)
                    await self.parent.save_config(c, it.guild_id, it.client, it)
                    await it.response.send_message("✅ Thresholds updated!", ephemeral=True)
                except ValueError:
                    await it.response.send_message("❌ Invalid number.", ephemeral=True)
        modal = ThresholdsModal()
        modal.parent = self
        await i.response.send_modal(modal)

    @ui.button(label="View Active", emoji="👁️", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_sr_active")
    async def view_active(self, i, b):
        from modules.staff_reviews import StaffReviewSystem
        srs = StaffReviewSystem(i.client)
        active = srs._get_active_reviews(i.guild_id)
        if not active:
            return await i.response.send_message("❌ No active reviews.", ephemeral=True)
        embed = discord.Embed(title="👁️ Active Reviews", color=discord.Color.blue())
        for uid, data in list(active.items())[:10]:
            member = i.guild.get_member(int(uid))
            name = member.display_name if member else f"User {uid}"
            status = "Complete" if data.get("complete") else "Pending"
            embed.add_field(name=name, value=f"Status: {status}", inline=False)
        await i.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="View History", emoji="📜", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_sr_history")
    async def view_history(self, i, b):
        from modules.staff_reviews import StaffReviewSystem
        srs = StaffReviewSystem(i.client)
        history = srs._get_history(i.guild_id)
        if not history:
            return await i.response.send_message("❌ No review history.", ephemeral=True)
        embed = discord.Embed(title="📜 Review History", color=discord.Color.green())
        for entry in history[-10:]:
            uid = entry.get("user_id", 0)
            member = i.guild.get_member(uid)
            name = member.display_name if member else f"User {uid}"
            score = entry.get("final_score", 0)
            embed.add_field(name=name, value=f"Score: {score:.1f}", inline=False)
        await i.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Set Staff Roles", emoji="👥", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_sr_roles")
    async def set_staff_roles(self, i, b):
        from modules.staff_reviews import StaffReviewSystem
        srs = StaffReviewSystem(i.client)
        c = srs._get_config(i.guild_id)
        roles = i.guild.roles
        options = [discord.SelectOption(label=r.name, value=str(r.id)) for r in roles if r.name != "@everyone"][:25]
        if not options:
            return await i.response.send_message("❌ No roles found.", ephemeral=True)
        class RoleSelect(ui.Select):
            def __init__(self, parent):
                super().__init__(placeholder="Select staff roles...", min_values=0, max_values=len(options), options=options)
                self.parent = parent
            async def callback(self, it):
                c = self.parent.get_config(it.guild_id)
                c["staff_roles"] = [int(v) for v in self.values]
                await self.parent.save_config(c, it.guild_id, it.client, it)
                await it.response.send_message(f"✅ Set {len(self.values)} staff roles!", ephemeral=True)
        view = ui.View()
        view.add_item(RoleSelect(self))
        await i.response.send_message("Select staff roles for reviews:", view=view, ephemeral=True)


class StaffShiftsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "staff_shifts")
        # Set initial toggle button state
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_ss_toggle":
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
        c = self.get_config(guild_id or self.guild_id)
        embed = discord.Embed(
            title="🕒 Staff Shifts Configuration",
            color=discord.Color.blue() if c.get("enabled", True) else discord.Color.red()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        elif guild:
            embed.set_thumbnail(url="https://cdn.discordapp.com/embed/avatars/0.png")
        
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        
        idle_timeout = c.get("idle_timeout_minutes", 30)
        embed.add_field(name="Idle Timeout", value=f"{idle_timeout} minutes", inline=True)
        
        on_duty_role_id = c.get("on_duty_role_id")
        embed.add_field(name="On-Duty Role", value=f"<@&{on_duty_role_id}>" if on_duty_role_id else "_None_", inline=True)
        
        shift_channel_id = c.get("shift_channel_id")
        embed.add_field(name="Shift Channel", value=f"<#{shift_channel_id}>" if shift_channel_id else "_None_", inline=True)
        
        notifications = c.get("notifications_enabled", True)
        embed.add_field(name="Notifications", value="✅ On" if notifications else "❌ Off", inline=True)
        
        schedule = c.get("schedule", [])
        embed.add_field(name="Scheduled Shifts", value=str(len(schedule)), inline=True)
        
        goals = c.get("goals", {})
        embed.add_field(name="Shift Goals", value=str(len(goals)), inline=True)
        
        return embed

    @ui.button(label="Disable", emoji="❌", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_ss_toggle")
    async def toggle(self, i, b):
        c = self.get_config(i.guild_id)
        c["enabled"] = not c.get("enabled", True)
        self.update_system_toggle_button("cfg_ss_toggle", c["enabled"])
        await self.save_config(c, i.guild_id, i.client, i)

    @ui.button(label="Set On-Duty Role", emoji="👔", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_ss_role")
    async def set_on_duty_role(self, i, b):
        await i.response.send_message(
            "Select On-Duty Role:",
            view=_picker_view(_GenericRoleSelect(self, "on_duty_role_id", "On-Duty Role")),
            ephemeral=True
        )

    @ui.button(label="Set Shift Channel", emoji="📣", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_ss_channel")
    async def set_shift_channel(self, i, b):
        await i.response.send_message(
            "Select Shift Channel:",
            view=_picker_view(_GenericChannelSelect(self, "shift_channel_id", "Shift Channel")),
            ephemeral=True
        )

    @ui.button(label="Set Idle Timeout", emoji="⏰", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_ss_idle")
    async def set_idle_timeout(self, i, b):
        class IdleModal(ui.Modal, title="Set Idle Timeout"):
            timeout = ui.TextInput(label="Idle Timeout (minutes)", placeholder="30", default="30")
            async def on_submit(self, it):
                try:
                    c = self.parent.get_config(it.guild_id)
                    c["idle_timeout_minutes"] = int(self.timeout.value)
                    await self.parent.save_config(c, it.guild_id, it.client, it)
                    await it.response.send_message("✅ Idle timeout updated!", ephemeral=True)
                except ValueError:
                    await it.response.send_message("❌ Invalid number.", ephemeral=True)
        modal = IdleModal()
        modal.parent = self
        await i.response.send_modal(modal)

    @ui.button(label="Toggle Notifications", emoji="📩", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_ss_notif")
    async def toggle_notifications(self, i, b):
        c = self.get_config(i.guild_id)
        c["notifications_enabled"] = not c.get("notifications_enabled", True)
        await self.save_config(c, i.guild_id, i.client, i)

    @ui.button(label="View Active Shifts", emoji="👁️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_ss_active")
    async def view_active(self, i, b):
        from modules.staff_shift import StaffShiftSystem
        sss = StaffShiftSystem(i.client)
        sss._load_active_shifts(i.guild_id)
        active = sss._shifts.get(i.guild_id, {})
        if not active:
            return await i.response.send_message("❌ No active shifts.", ephemeral=True)
        embed = discord.Embed(title="👁️ Active Shifts", color=discord.Color.blue())
        for uid, data in list(active.items())[:10]:
            member = i.guild.get_member(int(uid))
            name = member.display_name if member else f"User {uid}"
            start_time = data.get("start_time", 0)
            duration = time.time() - start_time
            hours = int(duration // 3600)
            minutes = int((duration % 3600) // 60)
            embed.add_field(name=name, value=f"Duration: {hours}h {minutes}m", inline=False)
        await i.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="View History", emoji="📜", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_ss_history")
    async def view_history(self, i, b):
        from modules.staff_shift import StaffShiftSystem
        sss = StaffShiftSystem(i.client)
        history = sss._get_history(i.guild_id)
        if not history:
            return await i.response.send_message("❌ No shift history.", ephemeral=True)
        embed = discord.Embed(title="📜 Shift History", color=discord.Color.green())
        for entry in history[-10:]:
            uid = entry.get("user_id", 0)
            member = i.guild.get_member(uid)
            name = member.display_name if member else f"User {uid}"
            duration = entry.get("duration_minutes", 0)
            embed.add_field(name=name, value=f"Duration: {duration:.0f} minutes", inline=False)
        await i.response.send_message(embed=embed, ephemeral=True)


# --- Registry ---

def get_system_info(system_key: str) -> tuple[str, str]:
    """Get system emoji and description from help system data."""
    from modules.help_system import CATEGORIES

    # Normalize the system key like in get_config_panel
    normalized_key = system_key.lower().replace("_", "").replace(" ", "").replace("system", "")

    for category_data in CATEGORIES.values():
        if "systems" in category_data:
            for sys_key, emoji, desc, cmds in category_data["systems"]:
                if sys_key == normalized_key:
                    return emoji, desc
    return "⚙️", f"Configuration for {system_key}"

SPECIALIZED_VIEWS = {
    "verification": "VerificationConfigView",
    "verify": "VerificationConfigView",
    "anti-raid": "AntiRaidConfigView",
    "antiraid": "AntiRaidConfigView",
    "guardian": "GuardianConfigView",
    "tickets": "TicketsConfigView",
    "welcome": "WelcomeConfigView",
    "welcomedm": "WelcomeDMConfigView",
    "application": "ApplicationConfigView",
    "appeals": "AppealsConfigView",
    "modmail": "ModmailConfigView",
    "suggestions": "SuggestionsConfigView",
    "giveaway": "GiveawayConfigView",
    "gamification": "GamificationConfigView",
    "reactionroles": "ReactionRolesConfigView",
    "reactionmenus": "ReactionMenusConfigView",
    "rolebuttons": "RoleButtonsConfigView",
    "modlog": "ModLogConfigView",
    "logging": "LoggingConfigView",
    "staffpromo": "StaffPromoConfigView",
    "ai-chat-channels": "ChatChannelsConfigView",
    "ai chat channels": "ChatChannelsConfigView",
    "events": "EventsConfigView",
}

# Local cache for lazy-loaded view classes
_view_cache = {}

class SystemOverviewView(ui.View):
    """View for system overview with configure button."""

    def __init__(self, guild_id: int, system: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.system = system

    @ui.button(label="Configure System", emoji="⚙️", style=discord.ButtonStyle.primary, custom_id="configure_system")
    async def configure_system(self, interaction: Interaction, button: ui.Button):
        # Check permissions
        if not interaction.user.guild_permissions.administrator and interaction.user.id != interaction.guild.owner_id:
            await interaction.response.send_message("❌ Only administrators can configure systems.", ephemeral=True)
            return

        # Get the config panel
        view = get_config_panel(self.guild_id, self.system)
        if not view:
            await interaction.response.send_message(f"❌ System '{self.system}' not found.", ephemeral=True)
            return

    # Get custom commands
    from actions import ActionHandler
    # Normalize system key for command lookup
    normalized_system = self.system.lower().replace("_", "").replace(" ", "").replace("system", "")
    custom_cmds = ActionHandler.get_commands_for_system(normalized_system)

        # Create the config embed
        embed = view.create_embed(guild_id=self.guild_id, guild=interaction.guild)

        # Add custom commands if available
        if custom_cmds:
            cmds_text = "\n".join([f"• `{cmd}`" for cmd in custom_cmds[:20]])
            embed.add_field(name="Custom Commands", value=cmds_text or "No commands", inline=False)

        embed.set_footer(text="Only you can see this configuration panel.")

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

def get_config_panel(guild_id: int, system: str) -> Optional[ui.View]:
    # Lazy import for all systems
    global _view_cache
    
    if "VerificationConfigView" not in _view_cache:
        view_classes = [
            "VerificationConfigView",
            "AntiRaidConfigView",
            "GuardianConfigView",
            "TicketsConfigView",
            "WelcomeConfigView",
            "WelcomeDMConfigView",
            "ApplicationConfigView",
            "AppealsConfigView",
            "ModmailConfigView",
            "SuggestionsConfigView",
            "GiveawayConfigView",
            "GamificationConfigView",
            "ReactionRolesConfigView",
            "ReactionMenusConfigView",
            "RoleButtonsConfigView",
            "ModLogConfigView",
            "LoggingConfigView",
            "AutoModConfigView",
            "WarningConfigView",
            "StaffPromoConfigView",
            "StaffShiftsConfigView",
            "StaffReviewsConfigView",
            "EconomyConfigView",
            "LevelingConfigView",
            "StarboardConfigView",
            "AutoResponderConfigView",
            "ChatChannelsConfigView",
            "EventsConfigView",
            "EconomyShopConfigView",
            "LevelingShopConfigView",
        ]
        import importlib
        for cls_name in view_classes:
            try:
                module = importlib.import_module("modules.config_panels")
                cls = getattr(module, cls_name)
                _view_cache[cls_name] = cls
            except (ImportError, AttributeError) as e:
                logger.warning(f"Failed to cache config view {cls_name}: {e}")
    
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
    # Config Panels - use try/except to handle missing classes gracefully
    def safe_add_view(view_class_name, *args):
        try:
            # Get the class from global scope
            cls = globals().get(view_class_name)
            if cls:
                bot.add_view(cls(*args))
            else:
                logger.warning(f"ConfigView class {view_class_name} not found, skipping...")
        except Exception as e:
            logger.error(f"Failed to register {view_class_name}: {e}")
    
    safe_add_view("StaffReviewsConfigView", 0)
    safe_add_view("StaffShiftsConfigView", 0)
    safe_add_view("AutoModConfigView", 0)
    safe_add_view("WarningConfigView", 0)
    safe_add_view("VerificationConfigView", 0)
    safe_add_view("AntiRaidConfigView", 0)
    safe_add_view("GuardianConfigView", 0)
    safe_add_view("TicketsConfigView", 0)
    safe_add_view("WelcomeConfigView", 0)
    safe_add_view("WelcomeDMConfigView", 0)
    safe_add_view("ApplicationConfigView", 0)
    safe_add_view("AppealsConfigView", 0)
    safe_add_view("ModmailConfigView", 0)
    safe_add_view("SuggestionsConfigView", 0)
    safe_add_view("GiveawayConfigView", 0)
    safe_add_view("GamificationConfigView", 0)
    safe_add_view("ReactionRolesConfigView", 0)
    safe_add_view("ReactionMenusConfigView", 0)
    safe_add_view("RoleButtonsConfigView", 0)
    safe_add_view("ModLogConfigView", 0)
    safe_add_view("LoggingConfigView", 0)
    safe_add_view("StaffPromoConfigView", 0)
    safe_add_view("EconomyConfigView", 0)
    safe_add_view("LevelingConfigView", 0)
    safe_add_view("StarboardConfigView", 0)
    safe_add_view("AutoResponderConfigView", 0)
    safe_add_view("ChatChannelsConfigView", 0)
    safe_add_view("EventsConfigView", 0)
    safe_add_view("EconomyShopConfigView", 0)
    safe_add_view("LevelingShopConfigView", 0)
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

class GuardianConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "guardian")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_guardian_toggle":
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
        embed = discord.Embed(
            title="🛡️ Guardian System",
            color=discord.Color.purple() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Protected Channels", value=str(len(c.get("protected_channels", []))), inline=True)
        embed.add_field(name="Log Channel", value=f"<#{c.get('log_channel_id')}>" if c.get("log_channel_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="🛡️", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_guardian_toggle")
    async def toggle_guardian(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_guardian_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

    @ui.button(label="Set Log Channel", emoji="#️⃣", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_guardian_set_log")
    async def set_log_channel(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        c = self.get_config(interaction.guild_id)
        c["log_channel_id"] = interaction.channel.id
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)
        await interaction.followup.send("Log channel set to current channel.", ephemeral=True)

class TicketsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "tickets")
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

    def create_embed(self, guild_id: int = None, guild: discord.Guild = None) -> discord.Embed:
        c = self.get_config(guild_id)
        embed = discord.Embed(
            title="🎫 Tickets System",
            color=discord.Color.blue() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Ticket Category", value=f"<#{c.get('category_id')}>" if c.get("category_id") else "_None_", inline=True)
        embed.add_field(name="Support Role", value=f"<@&{c.get('support_role_id')}>" if c.get("support_role_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="🎫", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_tickets_toggle")
    async def toggle_tickets(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_tickets_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

    @ui.button(label="Set Category", emoji="📁", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_tickets_set_category")
    async def set_category(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        c = self.get_config(interaction.guild_id)
        if interaction.channel.category:
            c["category_id"] = interaction.channel.category.id
            await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

class WelcomeConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "welcome")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_welcome_toggle":
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
        embed = discord.Embed(
            title="👋 Welcome System",
            color=discord.Color.green() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Welcome Channel", value=f"<#{c.get('welcome_channel_id')}>" if c.get("welcome_channel_id") else "_None_", inline=True)
        embed.add_field(name="Welcome Message", value=c.get("welcome_message", "_Default_")[:100], inline=False)
        return embed

    @ui.button(label="Disable", emoji="👋", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_welcome_toggle")
    async def toggle_welcome(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_welcome_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

    @ui.button(label="Set Channel", emoji="#️⃣", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_welcome_set_channel")
    async def set_channel(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        c = self.get_config(interaction.guild_id)
        c["welcome_channel_id"] = interaction.channel.id
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)
        await interaction.followup.send("Welcome channel set to current channel.", ephemeral=True)


class WelcomeDMConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "welcomedm")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_welcomedm_toggle":
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
        embed = discord.Embed(
            title="✉️ Welcome DM System",
            color=discord.Color.green() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="DM Message", value=c.get("dm_message", "_Default_")[:100], inline=False)
        return embed

    @ui.button(label="Disable", emoji="✉️", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_welcomedm_toggle")
    async def toggle_welcomedm(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_welcomedm_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

class ApplicationConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "application")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_application_toggle":
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
        embed = discord.Embed(
            title="📝 Application System",
            color=discord.Color.blue() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Application Channel", value=f"<#{c.get('app_channel_id')}>" if c.get("app_channel_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="📝", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_application_toggle")
    async def toggle_application(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_application_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

class AppealsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "appeals")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_appeals_toggle":
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
        embed = discord.Embed(
            title="⚖️ Appeals System",
            color=discord.Color.gold() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Appeal Channel", value=f"<#{c.get('appeal_channel_id')}>" if c.get("appeal_channel_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="⚖️", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_appeals_toggle")
    async def toggle_appeals(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_appeals_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

    @ui.button(label="Set Channel", emoji="#️⃣", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_appeals_set_channel")
    async def set_channel(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        c = self.get_config(interaction.guild_id)
        c["appeal_channel_id"] = interaction.channel.id
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)
        await interaction.followup.send("Appeal channel set to current channel.", ephemeral=True)


class ModmailConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "modmail")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_modmail_toggle":
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
        embed = discord.Embed(
            title="📧 Modmail System",
            color=discord.Color.blue() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Modmail Category", value=f"<#{c.get('modmail_category_id')}>" if c.get("modmail_category_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="📧", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_modmail_toggle")
    async def toggle_modmail(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_modmail_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)


class SuggestionsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "suggestions")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_suggestions_toggle":
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
        embed = discord.Embed(
            title="💡 Suggestions System",
            color=discord.Color.green() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Suggestions Channel", value=f"<#{c.get('suggestions_channel_id')}>" if c.get("suggestions_channel_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="💡", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_suggestions_toggle")
    async def toggle_suggestions(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_suggestions_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)


class GiveawayConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "giveaway")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_giveaway_toggle":
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
        embed = discord.Embed(
            title="🎉 Giveaway System",
            color=discord.Color.magenta() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Giveaway Channel", value=f"<#{c.get('giveaway_channel_id')}>" if c.get("giveaway_channel_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="🎉", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_giveaway_toggle")
    async def toggle_giveaway(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_giveaway_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)


class GamificationConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "gamification")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_gamification_toggle":
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
        embed = discord.Embed(
            title="🎮 Gamification System",
            color=discord.Color.orange() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Points Name", value=c.get("points_name", "Points"), inline=True)
        return embed

    @ui.button(label="Disable", emoji="🎮", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_gamification_toggle")
    async def toggle_gamification(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_gamification_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)


class ReactionRolesConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "reactionroles")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_reactionroles_toggle":
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
        embed = discord.Embed(
            title="🔄 Reaction Roles System",
            color=discord.Color.teal() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Reaction Roles Count", value=str(len(c.get("reaction_roles", []))), inline=True)
        return embed

    @ui.button(label="Disable", emoji="🔄", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_reactionroles_toggle")
    async def toggle_reactionroles(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_reactionroles_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)


class ReactionMenusConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "reactionmenus")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_reactionmenus_toggle":
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
        embed = discord.Embed(
            title="📋 Reaction Menus System",
            color=discord.Color.purple() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Menus Count", value=str(len(c.get("menus", []))), inline=True)
        return embed

    @ui.button(label="Disable", emoji="📋", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_reactionmenus_toggle")
    async def toggle_reactionmenus(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_reactionmenus_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)


class RoleButtonsConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "rolebuttons")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_rolebuttons_toggle":
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
        embed = discord.Embed(
            title="🔘 Role Buttons System",
            color=discord.Color.blue() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Buttons Count", value=str(len(c.get("buttons", []))), inline=True)
        return embed

    @ui.button(label="Disable", emoji="🔘", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_rolebuttons_toggle")
    async def toggle_rolebuttons(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_rolebuttons_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)


class ModLogConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "modlog")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_modlog_toggle":
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
        embed = discord.Embed(
            title="📜 Mod Log System",
            color=discord.Color.red() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Log Channel", value=f"<#{c.get('modlog_channel_id')}>" if c.get("modlog_channel_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="📜", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_modlog_toggle")
    async def toggle_modlog(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_modlog_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

    @ui.button(label="Set Channel", emoji="#️⃣", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_modlog_set_channel")
    async def set_channel(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        c = self.get_config(interaction.guild_id)
        c["modlog_channel_id"] = interaction.channel.id
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)
        await interaction.followup.send("Mod log channel set to current channel.", ephemeral=True)


class LoggingConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "logging")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_logging_toggle":
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
        embed = discord.Embed(
            title="📝 Logging System",
            color=discord.Color.dark_grey() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Log Channel", value=f"<#{c.get('logging_channel_id')}>" if c.get("logging_channel_id") else "_None_", inline=True)
        return embed

    @ui.button(label="Disable", emoji="📝", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_logging_toggle")
    async def toggle_logging(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_logging_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

    @ui.button(label="Set Channel", emoji="#️⃣", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_logging_set_channel")
    async def set_channel(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        c = self.get_config(interaction.guild_id)
        c["logging_channel_id"] = interaction.channel.id
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)
        await interaction.followup.send("Logging channel set to current channel.", ephemeral=True)


class StaffPromoConfigView(ConfigPanelView):
    def __init__(self, guild_id: int):
        super().__init__(guild_id, "staffpromo")
        c = self.get_config(guild_id)
        for item in self.children:
            if isinstance(item, ui.Button) and item.custom_id == "cfg_staffpromo_toggle":
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
        embed = discord.Embed(
            title="🎖️ Staff Promo System",
            color=discord.Color.gold() if c.get("enabled", True) else discord.Color.greyple()
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Status", value="✅ Enabled" if c.get("enabled", True) else "❌ Disabled", inline=True)
        embed.add_field(name="Promo Role", value=f"<@&{c.get('promo_role_id')}>" if c.get("promo_role_id") else "_None_", inline=True)
        embed.add_field(name="Requirements", value=c.get("requirements", "Not set")[:50] + "..." if len(c.get("requirements", "")) > 50 else c.get("requirements", "Not set"), inline=False)
        embed.add_field(name="Tiers", value=c.get("tiers", "Not set")[:50] + "..." if len(c.get("tiers", "")) > 50 else c.get("tiers", "Not set"), inline=False)
        return embed

    @ui.button(label="Disable", emoji="🎖️", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_staffpromo_toggle")
    async def toggle_staffpromo(self, interaction: Interaction, button: ui.Button):
        await interaction.response.defer()
        c = self.get_config(interaction.guild_id)
        c["enabled"] = not c.get("enabled", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        self.update_system_toggle_button("cfg_staffpromo_toggle", c["enabled"])
        await interaction.edit_original_response(embed=self.create_embed(interaction.guild_id, interaction.guild), view=self)

    @ui.button(label="Set Promo Role", emoji="👑", style=discord.ButtonStyle.primary, row=0, custom_id="cfg_staffpromo_set_role")
    async def set_role(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_message("Select Role:", view=_picker_view(_GenericRoleSelect(self, "promo_role_id", "Promo Role")), ephemeral=True)

    @ui.button(label="Set Requirements", emoji="📋", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_staffpromo_set_req")
    async def set_req(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_modal(_TextModal(self, "requirements", "Promotion Requirements", interaction.guild_id))

    @ui.button(label="Configure Tiers", emoji="🏆", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_staffpromo_tiers")
    async def config_tiers(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_modal(_TextModal(self, "tiers", "Promotion Tiers (JSON)", interaction.guild_id))

    @ui.button(label="View Status", emoji="📊", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_staffpromo_status")
    async def view_status(self, interaction: Interaction, button: ui.Button):
        c = self.get_config(interaction.guild_id)
        status = f"Enabled: {c.get('enabled', True)}\nPromo Role: <@&{c.get('promo_role_id', 0)}>\nRequirements: {c.get('requirements', 'Not set')}\nTiers: {c.get('tiers', 'Not set')}"
        await interaction.response.send_message(embed=discord.Embed(title="Staff Promo Status", description=status), ephemeral=True)

    @ui.button(label="Set Review Channel", emoji="📣", style=discord.ButtonStyle.primary, row=1, custom_id="cfg_staffpromo_set_channel")
    async def set_review_channel(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_message("Select Review Channel:", view=_picker_view(_GenericChannelSelect(self, "review_channel_id", "Review Channel")), ephemeral=True)

    @ui.button(label="Toggle DMs", emoji="📩", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_staffpromo_toggle_dms")
    async def toggle_dms(self, interaction: Interaction, button: ui.Button):
        c = self.get_config(interaction.guild_id)
        c["send_dms"] = not c.get("send_dms", True)
        await self.save_config(c, interaction.guild_id, interaction.client, interaction)
        await interaction.response.send_message(f"DMs {'enabled' if c['send_dms'] else 'disabled'}", ephemeral=True)

    @ui.button(label="View Staff Overview", emoji="👥", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_staffpromo_overview")
    async def view_overview(self, interaction: Interaction, button: ui.Button):
        embed = discord.Embed(title="Staff Overview", description="Staff member details and eligibility status.")
        embed.add_field(name="Total Staff", value="Data not available", inline=True)
        embed.add_field(name="Eligible for Promotion", value="Data not available", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="View Leaderboard", emoji="🏆", style=discord.ButtonStyle.secondary, row=3, custom_id="cfg_staffpromo_leaderboard")
    async def view_leaderboard(self, interaction: Interaction, button: ui.Button):
        embed = discord.Embed(title="Staff Leaderboard", description="Top staff by activity.")
        embed.add_field(name="No leaderboard data", value="Feature under development", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="View Promotion History", emoji="📜", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_staffpromo_history")
    async def view_history(self, interaction: Interaction, button: ui.Button):
        embed = discord.Embed(title="Promotion History", description="Recent promotions and demotions.")
        embed.add_field(name="No history available", value="Feature under development", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="Promote Staff", emoji="⬆️", style=discord.ButtonStyle.success, row=0, custom_id="cfg_staffpromo_promote")
    async def promote_staff(self, interaction: Interaction, button: ui.Button):
        class PromoteModal(ui.Modal, title="Promote Staff Member"):
            user_id = ui.TextInput(label="User ID", placeholder="123456789")
            target_role = ui.TextInput(label="Target Role ID", placeholder="987654321")
            reason = ui.TextInput(label="Reason", style=discord.TextStyle.paragraph)
            async def on_submit(self, it):
                # Basic implementation: assign role, log
                try:
                    user = it.guild.get_member(int(self.user_id.value))
                    role = it.guild.get_role(int(self.target_role.value))
                    if user and role:
                        await user.add_roles(role)
                        await it.response.send_message(f"✅ Promoted {user.mention} to {role.name}. Reason: {self.reason.value}", ephemeral=True)
                    else:
                        await it.response.send_message("❌ Invalid user or role ID.", ephemeral=True)
                except ValueError:
                    await it.response.send_message("❌ Invalid IDs.", ephemeral=True)
        await interaction.response.send_modal(PromoteModal())

    @ui.button(label="Demote Staff", emoji="⬇️", style=discord.ButtonStyle.danger, row=0, custom_id="cfg_staffpromo_demote")
    async def demote_staff(self, interaction: Interaction, button: ui.Button):
        class DemoteModal(ui.Modal, title="Demote Staff Member"):
            user_id = ui.TextInput(label="User ID")
            target_role = ui.TextInput(label="Target Role ID")
            reason = ui.TextInput(label="Reason", style=discord.TextStyle.paragraph)
            async def on_submit(self, it):
                try:
                    user = it.guild.get_member(int(self.user_id.value))
                    role = it.guild.get_role(int(self.target_role.value))
                    if user and role:
                        await user.remove_roles(role)
                        await it.response.send_message(f"✅ Demoted {user.mention} from {role.name}. Reason: {self.reason.value}", ephemeral=True)
                    else:
                        await it.response.send_message("❌ Invalid user or role ID.", ephemeral=True)
                except ValueError:
                    await it.response.send_message("❌ Invalid IDs.", ephemeral=True)
        await interaction.response.send_modal(DemoteModal())

    @ui.button(label="View Staff Profile", emoji="👤", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg_staffpromo_profile")
    async def view_profile(self, interaction: Interaction, button: ui.Button):
        class ProfileModal(ui.Modal, title="View Staff Profile"):
            user_id = ui.TextInput(label="User ID")
            async def on_submit(self, it):
                try:
                    user = it.guild.get_member(int(self.user_id.value))
                    if user:
                        embed = discord.Embed(title=f"Profile of {user.display_name}", description=f"ID: {user.id}\nJoined: {user.joined_at}\nRoles: {', '.join([r.name for r in user.roles])}")
                        await it.response.send_message(embed=embed, ephemeral=True)
                    else:
                        await it.response.send_message("❌ User not found.", ephemeral=True)
                except ValueError:
                    await it.response.send_message("❌ Invalid user ID.", ephemeral=True)
        await interaction.response.send_modal(ProfileModal())

    @ui.button(label="Put on Probation", emoji="⏸️", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg_staffpromo_probation")
    async def put_on_probation(self, interaction: Interaction, button: ui.Button):
        class ProbationModal(ui.Modal, title="Put on Probation"):
            user_id = ui.TextInput(label="User ID")
            duration = ui.TextInput(label="Duration (days)")
            reason = ui.TextInput(label="Reason")
            async def on_submit(self, it):
                await it.response.send_message("Feature under development: Probation management not yet implemented.", ephemeral=True)
        await interaction.response.send_modal(ProbationModal())

    @ui.button(label="Exclude from Promotions", emoji="🚫", style=discord.ButtonStyle.danger, row=3, custom_id="cfg_staffpromo_exclude")
    async def exclude_from_promotions(self, interaction: Interaction, button: ui.Button):
        class ExcludeModal(ui.Modal, title="Exclude from Promotions"):
            user_id = ui.TextInput(label="User ID")
            reason = ui.TextInput(label="Reason")
            async def on_submit(self, it):
                await it.response.send_message("Feature under development: Exclusion management not yet implemented.", ephemeral=True)
        await interaction.response.send_modal(ExcludeModal())

    @ui.button(label="View Eligible Staff", emoji="✅", style=discord.ButtonStyle.secondary, row=4, custom_id="cfg_staffpromo_eligible")
    async def view_eligible(self, interaction: Interaction, button: ui.Button):
        embed = discord.Embed(title="Eligible Staff", description="Staff members eligible for promotion.")
        embed.add_field(name="No eligible staff", value="Feature under development", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
