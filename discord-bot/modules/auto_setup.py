import discord
from discord import app_commands
import discord.ui as ui
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from data_manager import dm
from logger import logger
import os


class SetupState(Enum):
    PENDING = "pending"
    STARTED = "started"
    COMPLETED = "completed"
    SKIPPED = "skipped"


@dataclass
class ServerAnalysis:
    existing_channels: Dict[str, discord.abc.GuildChannel]
    existing_roles: Dict[str, discord.Role]
    existing_categories: Dict[str, discord.CategoryChannel]
    channel_permissions: Dict[int, Dict[int, discord.PermissionOverwrite]]
    private_channels: List[discord.abc.GuildChannel]
    public_channels: List[discord.abc.GuildChannel]


@dataclass
class ServerSetup:
    guild_id: int
    state: SetupState
    started_at: float
    completed_at: Optional[float]
    steps_completed: List[str]
    config: dict
    selected_systems: Optional[List[str]] = None


# System categories for paginated menus
class SystemCategory:
    SECURITY = {"name": "🛡️ Security", "emoji": "🛡️", "systems": ["verification", "anti_raid", "guardian", "auto_mod", "warning_system"]}
    ENGAGEMENT = {"name": "🎮 Engagement", "emoji": "🎮", "systems": ["economy", "economy_shop", "leveling", "leveling_shop", "giveaways", "gamification", "starboard"]}
    MODERATION = {"name": "📝 Moderation", "emoji": "📝", "systems": ["mod_logging", "logging", "modmail", "suggestions"]}
    STAFF = {"name": "👥 Staff", "emoji": "👥", "systems": ["staff_promotion", "staff_shifts", "staff_reviews", "applications", "appeals"]}
    AUTOMATION = {"name": "🤖 Automation", "emoji": "🤖", "systems": ["welcome", "welcome_dm", "tickets", "reminders", "scheduled_reminders", "announcements", "auto_responder"]}
    COMMUNITY = {"name": "🌐 Community", "emoji": "🌐", "systems": ["reaction_roles", "reaction_menus", "role_buttons", "chat_channels", "events"]}

    @classmethod
    def get_all_categories(cls):
        return [cls.SECURITY, cls.ENGAGEMENT, cls.MODERATION, cls.STAFF, cls.AUTOMATION, cls.COMMUNITY]
    
    @classmethod
    def get_recommended_systems(cls):
        return {"verification", "tickets", "economy", "leveling", "auto_mod"}


# Persistent View Classes for Auto-Setup Buttons - Stateless & Robust
class VerifyButton(discord.ui.View):
    def __init__(self, guild_id: int = 0, role_id: int = 0):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Verify Me", style=discord.ButtonStyle.success, custom_id="verify_button_persistent")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild: return

        role_id = dm.get_guild_data(guild.id, "verify_role")
        role = guild.get_role(role_id) if role_id else discord.utils.get(guild.roles, name="Verified")
        
        if not role:
            return await interaction.response.send_message("❌ Verification role not found. Please contact staff.", ephemeral=True)
        
        if role in interaction.user.roles:
            return await interaction.response.send_message("✅ You are already verified!", ephemeral=True)

        try:
            # Handle Unverified role removal if using the modules/verification system
            unverified = discord.utils.get(guild.roles, name="Unverified")
            if unverified and unverified in interaction.user.roles:
                await interaction.user.remove_roles(unverified)

            await interaction.user.add_roles(role)
            await interaction.response.send_message("✅ You're verified! Enjoy the server!", ephemeral=True)
            # Log action
            from logger import logger
            logger.info(f"User {interaction.user.id} verified in guild {guild.id}")
        except discord.Forbidden:
            await interaction.response.send_message("❌ I lack permissions to assign the Verified role. Check my role position!", ephemeral=True)


class AcceptRulesButton(discord.ui.View):
    def __init__(self, guild_id: int = 0, role_id: Optional[int] = None):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="I Accept the Rules", style=discord.ButtonStyle.primary, custom_id="accept_rules_persistent")
    async def accept_rules_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild: return
        
        role_id = dm.get_guild_data(guild.id, "verify_role")
        role = guild.get_role(role_id) if role_id else discord.utils.get(guild.roles, name="Verified")
        
        if role and role not in interaction.user.roles:
            try:
                await interaction.user.add_roles(role)
                await interaction.response.send_message("✅ Thanks for accepting! You now have full access.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("✅ Rules accepted (but I couldn't add your role).", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Failed to create ticket thread.", ephemeral=True)


class WelcomeDMView(discord.ui.View):
    """View for welcome DM with interactive buttons"""
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="🛡️ Verify Me", style=discord.ButtonStyle.success, custom_id="welcome_verify")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild: return
        
        role_id = dm.get_guild_data(guild.id, "verify_role")
        role = guild.get_role(role_id) if role_id else discord.utils.get(guild.roles, name="Verified")
        
        if role and role not in interaction.user.roles:
            try:
                await interaction.user.add_roles(role)
                await interaction.response.send_message("✅ You're verified!", ephemeral=True)
            except:
                await interaction.response.send_message("✅ Verified (role assignment failed).", ephemeral=True)
        else:
            await interaction.response.send_message("✅ Already verified!", ephemeral=True)
    
    @discord.ui.button(label="📋 Rules", style=discord.ButtonStyle.primary, custom_id="welcome_rules")
    async def rules_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Please check the rules channel!", ephemeral=True)
    
    @discord.ui.button(label="🎫 Support", style=discord.ButtonStyle.secondary, custom_id="welcome_support")
    async def support_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Contact a staff member for help!", ephemeral=True)


# Category Select Menu for Auto-Setup
class CategorySelect(discord.ui.Select):
    def __init__(self, categories: List[dict], guild_id: int):
        options = []
        for cat in categories:
            options.append(discord.SelectOption(
                label=cat["name"],
                emoji=cat["emoji"],
                description=f"{len(cat['systems'])} systems available",
                value=cat["name"]
            ))
        
        super().__init__(
            placeholder="📂 Select a category...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"category_select_{guild_id}"
        )
        self.categories = {cat["name"]: cat for cat in categories}
    
    async def callback(self, interaction: discord.Interaction):
        selected_category = self.values[0]
        category_data = self.categories.get(selected_category)
        
        if not category_data:
            return await interaction.response.send_message("❌ Category not found.", ephemeral=True)
        
        # Show system selection for this category
        view = SystemSelectView(self.view.auto_setup, interaction.guild.id, category_data, self.view.selected_systems)
        embed = self.view.auto_setup.create_category_embed(category_data)
        
        await interaction.response.edit_message(embed=embed, view=view)


class SystemSelectView(discord.ui.View):
    """View for selecting systems within a category. Holds the live selection state
    so SystemMultiSelect callbacks, AddCategory, Confirm, Back and BulkInstall all
    read/write the same authoritative list."""
    def __init__(self, auto_setup, guild_id: int, category: dict, selected_systems: List[str] = None):
        super().__init__(timeout=None)
        self.auto_setup = auto_setup
        self.guild_id = guild_id
        self.category = category
        # Selections accumulated from previous categories (kept across navigation).
        self.selected_systems: List[str] = list(selected_systems or [])
        # Selections the user just clicked in THIS category's dropdown.
        self.current_picks: List[str] = [s for s in self.selected_systems if s in category.get("systems", [])]

        # Add back button (uses live state)
        self.add_item(BackToCategoriesButton(auto_setup, guild_id))

        # Add system select menu
        systems = category["systems"]
        recommended = SystemCategory.get_recommended_systems()

        options = []
        for sys_name in systems:
            sys_display_name = sys_name.replace("_", " ").title()
            is_recommended = sys_name in recommended
            emoji = "⭐" if is_recommended else "⚪"
            label = f"{sys_display_name}{' [Recommended]' if is_recommended else ''}"

            options.append(discord.SelectOption(
                label=label[:100],  # Discord limit
                emoji=emoji,
                value=sys_name,
                default=sys_name in self.selected_systems,
            ))

        if options:
            self.select_menu = SystemMultiSelect(options, guild_id, category["name"])
            self.add_item(self.select_menu)
        else:
            self.select_menu = None

        # Add action buttons (no constructor snapshot — they read live from the view)
        self.add_item(AddCategoryButton(auto_setup, guild_id, category))
        self.add_item(ConfirmSelectionButton(auto_setup, guild_id))

    def get_full_selection(self) -> List[str]:
        """Authoritative current selection: previously-selected systems from other
        categories PLUS what's currently checked in this category's dropdown."""
        cat_systems = set(self.category.get("systems", []))
        # Drop this category's old picks (they may have been unchecked) and re-add live picks.
        kept = [s for s in self.selected_systems if s not in cat_systems]
        merged = kept + [s for s in self.current_picks if s not in kept]
        return merged

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        try:
            from logger import logger
            logger.exception(f"SystemSelectView error: {error}")
        except Exception:
            pass
        msg = f"⚠️ Wizard error: `{type(error).__name__}`. Try /autosetup again."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


class BackToCategoriesButton(discord.ui.Button):
    def __init__(self, auto_setup, guild_id: int, selected_systems: List[str] = None):
        super().__init__(label="◀️ Back to Categories", style=discord.ButtonStyle.secondary, custom_id=f"back_categories_{guild_id}")
        self.auto_setup = auto_setup
        self.guild_id = guild_id
        # Optional snapshot — but we always prefer live state from self.view if present.
        self.selected_systems = selected_systems or []

    async def callback(self, interaction: discord.Interaction):
        # Pull the LIVE selection from the parent SystemSelectView so picks made in
        # the dropdown aren't lost when navigating back.
        live = self.selected_systems
        parent = getattr(self, "view", None)
        if parent is not None and hasattr(parent, "get_full_selection"):
            try:
                live = parent.get_full_selection()
            except Exception:
                pass
        view = CategorySelectionView(self.auto_setup, self.guild_id, live)
        embed = self.auto_setup.create_welcome_embed(interaction.guild, live)
        await interaction.response.edit_message(embed=embed, view=view)


class SystemMultiSelect(discord.ui.Select):
    def __init__(self, options: List[discord.SelectOption], guild_id: int, category_name: str):
        super().__init__(
            placeholder=f"Select systems from {category_name}...",
            min_values=0,
            max_values=len(options),
            options=options,
            custom_id=f"system_select_{guild_id}_{category_name}",
        )
        self.guild_id = guild_id
        self.category_name = category_name

    async def callback(self, interaction: discord.Interaction):
        """CRITICAL: persist the picks onto the parent view so Confirm/Install can see them.
        Previously this just deferred and discarded `self.values` — that's why install did nothing."""
        try:
            picks = list(self.values or [])
            parent = self.view  # the SystemSelectView
            if parent is not None:
                parent.current_picks = picks
                # Mirror the picks into the persistent selected_systems list too,
                # so the Cancel/Back path retains them.
                cat_systems = set(parent.category.get("systems", []))
                kept = [s for s in (parent.selected_systems or []) if s not in cat_systems]
                parent.selected_systems = kept + [p for p in picks if p not in kept]
                # Update each SelectOption.default so the UI re-renders with checks intact.
                for opt in self.options:
                    opt.default = opt.value in picks
            if not interaction.response.is_done():
                await interaction.response.defer()
        except Exception as e:
            try:
                from logger import logger
                logger.exception(f"SystemMultiSelect.callback failed: {e}")
            except Exception:
                pass
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer()
            except Exception:
                pass


class AddCategoryButton(discord.ui.Button):
    def __init__(self, auto_setup, guild_id: int, category: dict, selected_systems: List[str] = None):
        super().__init__(label=f"➕ Add All from {category['name']}", style=discord.ButtonStyle.primary, custom_id=f"add_category_{guild_id}")
        self.auto_setup = auto_setup
        self.guild_id = guild_id
        self.category = category
        # Snapshot kept only as a fallback — live state from self.view wins.
        self.selected_systems = selected_systems or []

    async def callback(self, interaction: discord.Interaction):
        # Pull LIVE selection from the parent view, then add every system in this category.
        parent = getattr(self, "view", None)
        if parent is not None and hasattr(parent, "get_full_selection"):
            current = parent.get_full_selection()
        else:
            current = list(self.selected_systems)
        new_selections = list(current)
        for sys in self.category["systems"]:
            if sys not in new_selections:
                new_selections.append(sys)

        view = CategorySelectionView(self.auto_setup, self.guild_id, new_selections)
        embed = self.auto_setup.create_welcome_embed(interaction.guild, new_selections)
        await interaction.response.edit_message(embed=embed, view=view)


class ConfirmSelectionButton(discord.ui.Button):
    def __init__(self, auto_setup, guild_id: int):
        super().__init__(label="✅ Confirm & Install Selected", style=discord.ButtonStyle.success, custom_id=f"confirm_systems_{guild_id}")
        self.auto_setup = auto_setup
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        """Actually install the systems. Reads the live selection from the parent
        SystemSelectView (via get_full_selection). Previously this was a stub that
        only deferred — that's why nothing happened when users clicked it."""
        try:
            parent = getattr(self, "view", None)
            selected: List[str] = []
            if parent is not None and hasattr(parent, "get_full_selection"):
                selected = parent.get_full_selection()
            elif parent is not None:
                selected = list(getattr(parent, "selected_systems", []) or [])

            if not selected:
                return await interaction.response.send_message(
                    "❌ You haven't selected any systems yet. Pick at least one from the dropdown above, then click **Confirm & Install Selected** again.",
                    ephemeral=True,
                )

            guild = interaction.guild
            if guild is None:
                return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

            auto_setup = self.auto_setup or interaction.client.get_cog("AutoSetup")
            if not auto_setup:
                return await interaction.response.send_message(
                    "❌ Setup system not initialized. Please ask an admin to restart the bot.",
                    ephemeral=True,
                )

            # Edit the original message immediately so the user sees feedback within 3s.
            try:
                await interaction.response.edit_message(
                    content=f"⚙️ Starting setup of **{len(selected)}** systems: {', '.join(selected[:10])}{'...' if len(selected)>10 else ''}",
                    embed=None,
                    view=None,
                )
            except Exception:
                try:
                    await interaction.response.defer(ephemeral=True)
                except Exception:
                    pass

            # Run the install — wrap so errors surface via followup, not "interaction failed".
            try:
                await auto_setup._run_selected_setup(
                    guild.id, interaction.user, selected, interaction.channel_id
                )
            except Exception as e:
                logger.exception(f"ConfirmSelectionButton: install failed: {e}")
                try:
                    await interaction.followup.send(
                        f"⚠️ Install hit an error: `{type(e).__name__}: {e}`. Some systems may have been installed.",
                        ephemeral=True,
                    )
                except Exception:
                    pass
        except Exception as e:
            try:
                logger.exception(f"ConfirmSelectionButton outer error: {e}")
            except Exception:
                pass
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(f"⚠️ {type(e).__name__}: {e}", ephemeral=True)
                else:
                    await interaction.response.send_message(f"⚠️ {type(e).__name__}: {e}", ephemeral=True)
            except Exception:
                pass


class CategorySelectionView(discord.ui.View):
    """Main view showing category selection"""
    def __init__(self, auto_setup, guild_id: int, selected_systems: List[str] = None):
        super().__init__(timeout=None)
        self.auto_setup = auto_setup
        self.guild_id = guild_id
        self.selected_systems = selected_systems or []
        
        categories = SystemCategory.get_all_categories()
        self.add_item(CategorySelect(categories, guild_id))
        
        # Show currently selected count
        if self.selected_systems:
            self.add_item(ViewSelectedButton(auto_setup, guild_id, selected_systems))
        
        # Use ConfirmSelectionButton (the only install button now). It reads
        # `self.view.selected_systems` at click-time, so it always sees the latest picks.
        self.add_item(ConfirmSelectionButton(auto_setup, guild_id))
        self.add_item(CancelSetupButton(guild_id))
    
    @property
    def selected_systems(self):
        return self._selected_systems
    
    @selected_systems.setter
    def selected_systems(self, value):
        self._selected_systems = value

    def create_category_embed(self, category: dict) -> discord.Embed:
        """Build an embed for a chosen category. Delegates to the cog when available
        so the same render logic is used everywhere; otherwise renders a sane fallback.
        This prevents `AttributeError: 'CategorySelectionView' object has no attribute
        'create_category_embed'` when older code calls the method on the view itself."""
        if self.auto_setup is not None and hasattr(self.auto_setup, "create_category_embed"):
            try:
                return self.auto_setup.create_category_embed(category)
            except Exception:
                pass
        # Fallback inline implementation
        try:
            recommended = SystemCategory.get_recommended_systems()
        except Exception:
            recommended = set()
        embed = discord.Embed(
            title=f"{category.get('emoji', '📂')} {category.get('name', 'Category')} Systems",
            description=f"Select the systems you want to install from the **{category.get('name','')}** category.",
            color=discord.Color.green(),
        )
        for sys in category.get("systems", []):
            is_rec = sys in recommended
            embed.add_field(
                name=sys.replace("_", " ").title(),
                value="⭐ **[Recommended]**" if is_rec else "• Available",
                inline=True,
            )
        embed.set_footer(text="Use the dropdown above to select systems | Click 'Confirm & Install' when ready")
        return embed

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        """Never let the user see 'interaction failed' from the auto-setup wizard."""
        try:
            from logger import logger
            logger.exception(f"CategorySelectionView error: {error}")
        except Exception:
            pass
        msg = f"⚠️ Auto-Setup hit an error: `{type(error).__name__}`. Please try again or run /autosetup."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


class ViewSelectedButton(discord.ui.Button):
    def __init__(self, auto_setup, guild_id: int, selected_systems: List[str]):
        super().__init__(label=f"📋 View Selected ({len(selected_systems)})", style=discord.ButtonStyle.secondary, custom_id=f"view_selected_{guild_id}")
        self.auto_setup = auto_setup
        self.guild_id = guild_id
        self.selected_systems = selected_systems
    
    async def callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📋 Selected Systems",
            description=f"You have selected **{len(self.selected_systems)}** systems to install.",
            color=discord.Color.blue()
        )
        
        # Group by category
        categories = SystemCategory.get_all_categories()
        sys_to_cat = {}
        for cat in categories:
            for sys in cat["systems"]:
                sys_to_cat[sys] = cat["name"]
        
        by_category = {}
        for sys in self.selected_systems:
            cat_name = sys_to_cat.get(sys, "Other")
            if cat_name not in by_category:
                by_category[cat_name] = []
            by_category[cat_name].append(sys)
        
        for cat_name, systems in by_category.items():
            embed.add_field(
                name=cat_name,
                value="\n".join(f"• {s.replace('_', ' ').title()}" for s in systems),
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


class OpenConfigPanelButton(discord.ui.Button):
    """Button shown after install that opens a system's config panel right in the channel."""
    def __init__(self, system_name: str, label: Optional[str] = None):
        # Stable custom_id so the view stays interactive across restarts
        super().__init__(
            label=label or f"⚙️ Configure {system_name.replace('_',' ').title()}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"open_cfg_panel_{system_name}",
        )
        self.system_name = system_name

    async def callback(self, interaction: discord.Interaction):
        try:
            from modules.config_panels import get_config_panel
            view = get_config_panel(interaction.guild.id, self.system_name)
            if view is None:
                return await interaction.response.send_message(
                    f"❌ No config panel exists for **{self.system_name}**.",
                    ephemeral=True,
                )
            embed = view.create_embed(interaction.guild.id) if hasattr(view, "create_embed") else discord.Embed(
                title=f"⚙️ {self.system_name.replace('_',' ').title()} Config"
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            try:
                from logger import logger
                logger.exception(f"OpenConfigPanelButton error: {e}")
            except Exception:
                pass
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        f"⚠️ Could not open the **{self.system_name}** config panel: `{type(e).__name__}`. Try `!configpanel {self.system_name}`.",
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        f"⚠️ Could not open the **{self.system_name}** config panel: `{type(e).__name__}`. Try `!configpanel {self.system_name}`.",
                        ephemeral=True,
                    )
            except Exception:
                pass


class PostInstallView(discord.ui.View):
    """View shown in the celebratory embed after install — one button per installed system."""
    def __init__(self, installed_systems: List[str]):
        super().__init__(timeout=None)
        # Discord allows max 25 components per view (5 rows × 5 buttons). Cap at 20 to be safe.
        for s in installed_systems[:20]:
            try:
                self.add_item(OpenConfigPanelButton(s))
            except Exception:
                continue


class CancelSetupButton(discord.ui.Button):
    def __init__(self, guild_id: int):
        super().__init__(label="❌ Cancel", style=discord.ButtonStyle.danger, custom_id=f"cancel_setup_{guild_id}", row=1)
        self.guild_id = guild_id
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="❌ Setup cancelled.", embed=None, view=None)


# ====== AutoSetup Cog ======

class AutoSetup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._pending_setups: Dict[int, ServerSetup] = self._load_pending_setups()
        self._setup_messages: Dict[int, int] = {}
        self._status_messages: Dict[int, int] = {}  # guild_id -> message_id for live status embed
        self._status_channels: Dict[int, int] = {}  # guild_id -> channel_id for live status embed
        self._startup_guilds = set()
    
    def create_welcome_embed(self, guild: discord.Guild, selected_systems: List[str] = None) -> discord.Embed:
        """Create animated welcome embed for /autosetup command"""
        selected = selected_systems or []
        
        # Animated emoji (use animated if available, fallback to static)
        sparkle = "<a:sparkle:123>" if self._has_animated_emoji(guild, "sparkle") else "✨"
        rocket = "<a:rocket:123>" if self._has_animated_emoji(guild, "rocket") else "🚀"
        
        embed = discord.Embed(
            title=f"{sparkle} Welcome to Miro AI Auto-Setup {sparkle}",
            description=(
                f"{rocket} **Get your server running in minutes!** {rocket}\n\n"
                f"Select systems from the categories below to install. "
                f"Use the dropdown menus to explore **Security**, **Engagement**, **Moderation**, and more!"
            ),
            color=discord.Color.blue()
        )
        
        # Discord timestamp formatting
        current_time = int(time.time())
        embed.add_field(
            name="⏰ Setup Started",
            value=f"<t:{current_time}:F> (<t:{current_time}:R>)",
            inline=True
        )
        
        embed.add_field(
            name="🎯 Total Systems Available",
            value="**33** pre-built systems ready to deploy!",
            inline=True
        )
        
        if selected:
            embed.add_field(
                name=f"✅ Selected Systems ({len(selected)})",
                value="\n".join(f"• {s.replace('_', ' ').title()}" for s in selected[:10]) + ("\n...and more" if len(selected) > 10 else ""),
                inline=False
            )
        
        # Recommended systems badge
        recommended = SystemCategory.get_recommended_systems()
        rec_text = "\n".join(f"⭐ **{s.replace('_', ' ').title()}** (Recommended)" for s in sorted(recommended))
        embed.add_field(
            name="⭐ Recommended for New Servers",
            value=rec_text,
            inline=False
        )
        
        embed.set_footer(text="Need help? Type !help | Hype train is leaving the station! 🚂")
        return embed
    
    def _has_animated_emoji(self, guild: discord.Guild, name: str) -> bool:
        """Check if guild has an animated emoji with given name"""
        return any(e.animated and e.name == name for e in guild.emojis)

    def create_system_status_embed(self, guild: discord.Guild) -> discord.Embed:
        """Create live status embed showing all systems and their status."""
        embed = discord.Embed(
            title="🔄 Live System Status",
            description="Real-time status of all server systems. Updates automatically when changes occur.",
            color=discord.Color.blue()
        )

        categories = SystemCategory.get_all_categories()
        for cat in categories:
            systems_status = []
            for sys in cat["systems"]:
                config_key = f"{sys}_config"
                config = dm.get_guild_data(guild.id, config_key, {})
                enabled = config.get("enabled", False)
                status = "✅ Enabled" if enabled else "❌ Disabled"
                systems_status.append(f"{sys.replace('_', ' ').title()}: {status}")

            embed.add_field(
                name=f"{cat['emoji']} {cat['name']}",
                value="\n".join(systems_status) if systems_status else "No systems",
                inline=False
            )

        embed.set_footer(text="Use !configpanel <system> to configure | Updates live")
        return embed

    async def update_system_status_embed(self, guild_id: int):
        """Update the live status embed for a guild."""
        # Load from persistence if not in memory
        if guild_id not in self._status_messages:
            mid = dm.get_guild_data(guild_id, "status_message_id")
            if mid:
                self._status_messages[guild_id] = mid
        if guild_id not in self._status_channels:
            cid = dm.get_guild_data(guild_id, "status_channel_id")
            if cid:
                self._status_channels[guild_id] = cid

        if guild_id not in self._status_messages or guild_id not in self._status_channels:
            return
        message_id = self._status_messages[guild_id]
        channel_id = self._status_channels[guild_id]
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        channel = guild.get_channel(channel_id)
        if not channel:
            return
        embed = self.create_system_status_embed(guild)
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(embed=embed)
        except Exception as e:
            logger.warning(f"Failed to update status embed for guild {guild_id}: {e}")

    def create_category_embed(self, category: dict) -> discord.Embed:
        """Create embed for a specific category"""
        systems = category["systems"]
        recommended = SystemCategory.get_recommended_systems()
        
        embed = discord.Embed(
            title=f"{category['emoji']} {category['name']} Systems",
            description=f"Select the systems you want to install from the **{category['name']}** category.",
            color=discord.Color.green()
        )
        
        for sys in systems:
            is_rec = sys in recommended
            status = "⭐ **[Recommended]**" if is_rec else "• Available"
            embed.add_field(
                name=f"{sys.replace('_', ' ').title()}",
                value=status,
                inline=True
            )
        
        embed.set_footer(text="Use the dropdown above to select systems | Click 'Confirm & Install' when ready")
        return embed
    
    def create_progress_bar(self, current: int, total: int, length: int = 10) -> str:
        """Create a visual progress bar"""
        if total == 0:
            return "░" * length
        
        progress = int((current / total) * length)
        bar = "▒" * progress + "░" * (length - progress)
        return bar
    
    async def _run_selected_setup(self, guild_id: int, user: discord.Member, selected_systems: List[str], channel_id: Optional[int] = None):
        """Run setup for selected systems with live progress bar updates"""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        
        setup = self._pending_setups.get(guild.id)
        if not setup:
            setup = ServerSetup(
                guild_id=guild.id,
                state=SetupState.STARTED,
                started_at=time.time(),
                completed_at=None,
                steps_completed=[],
                config={},
                selected_systems=selected_systems
            )
            self._pending_setups[guild.id] = setup
        
        # Save to resumable queue
        dm.save_resumable_setup(guild_id, {
            "user_id": user.id,
            "selected_systems": selected_systems,
            "channel_id": channel_id,
            "timestamp": time.time()
        })
        
        self._created_channels = []
        analysis = await self._analyze_server(guild)
        
        results = []
        system_map = self._get_system_map()
        
        # Send initial progress message
        target_channel = guild.get_channel(channel_id) if channel_id else None
        progress_msg = None
        
        for i, system in enumerate(selected_systems):
            if system in system_map:
                name, func = system_map[system]
                try:
                    # Update live progress bar
                    progress_bar = self.create_progress_bar(i, len(selected_systems))
                    percentage = int((i / len(selected_systems)) * 100) if len(selected_systems) > 0 else 0
                    
                    embed = discord.Embed(
                        title="⚙️ Installation in Progress...",
                        description=f"Installing **{len(selected_systems)}** systems for **{guild.name}**.",
                        color=discord.Color.blue()
                    )
                    
                    # Live progress bar
                    embed.add_field(
                        name="Progress Bar",
                        value=f"`{progress_bar}` {percentage}%",
                        inline=False
                    )
                    
                    # Show completed systems
                    if results:
                        completed_text = "\n".join(f"✅ {r[0]}" for r in results if r[1])
                        if completed_text:
                            embed.add_field(name="✅ Completed", value=completed_text[:1024], inline=False)
                    
                    # Currently installing
                    embed.add_field(
                        name="⏳ Currently Installing",
                        value=f"**{name}**...",
                        inline=False
                    )
                    
                    remaining = len(selected_systems) - i - 1
                    if remaining > 0:
                        embed.add_field(
                            name="📋 Remaining",
                            value=f"{remaining} system(s) left to install",
                            inline=True
                        )
                    
                    embed.set_footer(text="Need help? Type !help")
                    
                    if not progress_msg:
                        if target_channel:
                            progress_msg = await target_channel.send(embed=embed)
                        else:
                            try:
                                progress_msg = await user.send(embed=embed)
                            except:
                                pass
                    else:
                        try:
                            await progress_msg.edit(embed=embed)
                        except:
                            pass
                    
                    logger.info(f"Setting up {name} for {guild.name}")
                    result = await func(guild, analysis)
                    if result:
                        try:
                            self._register_system_commands(guild.id, system)
                        except Exception as reg_err:
                            logger.warning(f"{name}: command registration failed (system still installed): {reg_err}")
                    results.append((name, bool(result), None))
                    setup.steps_completed.append(system)
                    try:
                        self._save_pending_setups()
                    except Exception:
                        pass
                    await asyncio.sleep(0.3)  # Small delay for visual effect
                except Exception as e:
                    logger.exception(f"{name} setup failed: {e}")
                    results.append((name, False, type(e).__name__))
        
        # Final progress update (100%)
        if progress_msg:
            embed = discord.Embed(
                title="✅ Installation Complete!",
                description=f"All **{len(selected_systems)}** systems have been processed.",
                color=discord.Color.green()
            )
            embed.add_field(
                name="Progress Bar",
                value=f"`{'▒' * 10}` 100%",
                inline=False
            )
            try:
                await progress_msg.edit(embed=embed)
            except:
                pass
        
        setup.state = SetupState.COMPLETED
        setup.completed_at = time.time()
        
        # Cleanup
        if guild.id in self._pending_setups:
            del self._pending_setups[guild.id]
            self._save_pending_setups()
        
        completed = dm.load_json("completed_setups", default={})
        completed[str(guild.id)] = time.time()
        dm.save_json("completed_setups", completed)
        dm.remove_resumable_setup(guild.id)
        
        # Send celebratory embed
        await self._send_celebratory_embed(guild, user, results, channel_id, self._created_channels)
    
    async def _send_status_embed(self, guild: discord.Guild, user: discord.Member, results: List[Tuple[str, bool, Optional[str]]], channel_id: Optional[int] = None, created_channels: List[int] = None):
        """Send live status embed showing system states"""
        embed = self.create_system_status_embed(guild)
        
        # Store installed systems
        success_systems = [name for name, success, _ in results if success]
        installed_keys: List[str] = []
        try:
            display_to_key = {display: key for key, (display, _func) in self._get_system_map().items()}
            for display in success_systems:
                key = display_to_key.get(display)
                if key:
                    installed_keys.append(key)
        except Exception as e:
            logger.debug(f"Could not build installed keys: {e}")
        
        if installed_keys:
            dm.update_guild_data(guild.id, "installed_systems", installed_keys)
        
        view: Optional[discord.ui.View] = PostInstallView(installed_keys) if installed_keys else None
        
        sent = False
        if channel_id:
            target_channel = guild.get_channel(channel_id)
            if target_channel:
                try:
                    if view is not None:
                        message = await target_channel.send(f"{user.mention}", embed=embed, view=view)
                    else:
                        message = await target_channel.send(f"{user.mention}", embed=embed)
                    self._status_messages[guild.id] = message.id
                    self._status_channels[guild.id] = target_channel.id
                    dm.update_guild_data(guild.id, "status_message_id", message.id)
                    dm.update_guild_data(guild.id, "status_channel_id", target_channel.id)
                    sent = True
                except Exception as e:
                    logger.warning(f"Failed to send status embed to channel: {e}")
        
        if not sent:
            try:
                if view is not None:
                    message = await user.send(embed=embed, view=view)
                else:
                    message = await user.send(embed=embed)
                # DM not stored for updates
            except discord.Forbidden:
                system_channel = guild.system_channel
                if system_channel:
                    try:
                        if view is not None:
                            message = await system_channel.send(f"{user.mention}", embed=embed, view=view)
                        else:
                            message = await system_channel.send(f"{user.mention}", embed=embed)
                        self._status_messages[guild.id] = message.id
                        self._status_channels[guild.id] = system_channel.id
                        dm.update_guild_data(guild.id, "status_message_id", message.id)
                        dm.update_guild_data(guild.id, "status_channel_id", system_channel.id)
                    except Exception as e:
                        logger.warning(f"Failed to send status embed to system channel: {e}")

    async def _send_celebratory_embed(self, guild: discord.Guild, user: discord.Member, results: List[Tuple[str, bool, Optional[str]]], channel_id: Optional[int] = None, created_channels: List[int] = None):
        """Send celebratory embed when setup is complete"""
        embed = discord.Embed(
            title="🎉 Setup Complete!",
            description=f"Congratulations {user.mention}! Your server has been successfully configured with the latest bot systems.",
            color=discord.Color.green()
        )

        # Count successes
        success_count = sum(1 for name, success, _ in results if success)
        total_count = len(results)

        embed.add_field(
            name="📊 Summary",
            value=f"**{success_count}/{total_count}** systems installed successfully!",
            inline=True
        )

        # List successful systems
        success_systems = [name for name, success, _ in results if success]
        if success_systems:
            embed.add_field(
                name="✅ Installed Systems",
                value="\n".join(f"• {sys}" for sys in success_systems[:10]),
                inline=True
            )

        # Created channels
        if created_channels:
            channel_mentions = [f"<#{cid}>" for cid in created_channels[:5]]
            embed.add_field(
                name="🏗️ Created Channels",
                value=" ".join(channel_mentions),
                inline=False
            )

        # Build specific configpanel commands
        config_steps = []
        for sys in success_systems[:5]:  # Limit to 5 to avoid embed length limit
            config_steps.append(f"• Use `!configpanel {sys}` to configure {sys}")

        next_steps = "\n".join(config_steps)
        next_steps += "\n• Run `!help` for command reference\n• Join our support server for help"

        embed.add_field(
            name="🚀 Next Steps",
            value=next_steps,
            inline=False
        )

        embed.set_footer(text="Thank you for choosing Miro Bot! | Setup by AutoSetup")
        embed.timestamp = discord.utils.utcnow()

        # Send to specified channel or DM
        sent = False
        if channel_id:
            target_channel = guild.get_channel(channel_id)
            if target_channel:
                try:
                    await target_channel.send(embed=embed)
                    sent = True
                except Exception as e:
                    logger.warning(f"Failed to send celebratory embed to channel: {e}")

        if not sent:
            try:
                await user.send(embed=embed)
            except discord.Forbidden:
                system_channel = guild.system_channel
                if system_channel:
                    try:
                        await system_channel.send(embed=embed)
                    except Exception as e:
                        logger.warning(f"Failed to send celebratory embed to system channel: {e}")

    def _get_example_commands(self, installed_systems: List[str]) -> List[str]:
        """Get example commands for installed systems"""
        cmd_map = {
            "tickets": "`!ticket` - Create a ticket | `!close` - Close ticket",
            "economy": "`!daily` - Claim daily coins | `!balance` - Check wallet | `!shop` - Open shop",
            "leveling": "`!rank` - Check your level | `!leaderboard` - Top members",
            "giveaways": "`!giveaway create` - Start a giveaway",
            "modmail": "DM the bot to contact staff",
            "suggestions": "`!suggest <text>` - Submit suggestion",
            "welcome": "`!welcome config` - Adjust settings",
            "automod": "`!automod status` - View filter settings",
            "warnings": "`!warn @user <reason>` - Issue warning",
            "starboard": "`!starboard` - View starred messages",
        }
        
        commands = []
        for sys in installed_systems:
            if sys in cmd_map:
                commands.append(cmd_map[sys])
        return commands
    
    def _get_system_map(self) -> Dict[str, Tuple[str, callable]]:
        """Get mapping of system names to setup functions"""
        return {
            "verification": ("Verification System", self._setup_verification_system),
            "anti_raid": ("Anti-Raid System", self._setup_anti_raid),
            "guardian": ("Guardian System", self._setup_guardian),
            "auto_mod": ("Auto-Mod System", self._setup_automod_system),
            "warning_system": ("Warning System", self._setup_warning_system),
            "economy": ("Economy System", self._setup_economy_system),
            "economy_shop": ("Economy Shop", self._setup_economy_shop),
            "leveling": ("Leveling System", self._setup_leveling_system),
            "leveling_shop": ("Leveling Shop", self._setup_leveling_shop),
            "giveaways": ("Giveaways System", self._setup_giveaways),
            "gamification": ("Gamification System", self._setup_gamification),
            "starboard": ("Starboard System", self._setup_starboard),
            "mod_logging": ("Moderation Logging", self._setup_mod_logging),
            "logging": ("Logging System", self._setup_logging_system),
            "modmail": ("Modmail System", self._setup_modmail),
            "suggestions": ("Suggestions System", self._setup_suggestions),
            "staff_promotion": ("Staff Promotion", self._setup_staff_promotion),
            "staff_shifts": ("Staff Shifts", self._setup_staff_shifts),
            "staff_reviews": ("Staff Reviews", self._setup_staff_reviews),
            "applications": ("Applications System", self._setup_applications),
            "appeals": ("Appeals System", self._setup_appeals),
            "welcome": ("Welcome System", self._setup_welcome_system),
            "welcome_dm": ("Welcome DM Buttons", self._setup_welcome_dm_buttons),
            "tickets": ("Ticket System", self._setup_ticket_system),
            "reminders": ("Reminders System", self._setup_reminders),
            "scheduled_reminders": ("Scheduled Reminders", self._setup_scheduled_reminders),
            "announcements": ("Announcements System", self._setup_announcements),
            "auto_responder": ("Auto-Responder System", self._setup_auto_responder),
            "reaction_roles": ("Reaction Roles", self._setup_reaction_roles),
            "reaction_menus": ("Reaction Menus", self._setup_reaction_menus),
            "role_buttons": ("Role Buttons", self._setup_role_buttons),
            "chat_channels": ("AI Chat Channels", self._setup_chat_channels),
            "events": ("Events System", self._setup_events),
        }

    # ===== Setup Functions for All 33 Systems =====
    
    async def _setup_anti_raid(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {
                "enabled": True,
                "sensitivity": "medium",
                "action": "lockdown",
                "mass_join_threshold": 10,
                "mass_join_window": 10,
                "rules": {
                    "link_spam": {"enabled": True},
                    "mention_spam": {"enabled": True, "threshold": 5},
                    "duplicate_spam": {"enabled": True, "threshold": 3},
                    "invites": {"enabled": True}
                },
                "whitelist": [],
                "raid_log": []
            }
            dm.update_guild_data(guild.id, "anti_raid_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup anti-raid: {e}")
            return False

    async def _setup_guardian(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {
                "enabled": True,
                "sensitivity": "medium",
                "alert_channel": None,
                "token_detection": True,
                "toxicity_level": "WARN",
                "scam_level": "MUTE",
                "nuke_level": "BAN",
                "mass_dm_threshold": 10,
                "whitelist": [],
                "guardian_log": []
            }
            dm.update_guild_data(guild.id, "guardian_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup guardian: {e}")
            return False

    async def _setup_welcome_dm_buttons(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            channel = analysis.existing_channels.get("welcome-dm") or await self._create_setup_channel(guild, "welcome-dm")
            config = {
                "enabled": True,
                "message": "Welcome to **{server}**! Click a button below to get started.",
                "embed_color": 0x3498db,
                "enabled_buttons": ["verify", "rules", "roles", "ticket"],
                "welcomedm_stats": {"sent": 0, "optout": 0, "verify_clicks": 0}
            }
            dm.update_guild_data(guild.id, "welcomedm_config", config)
            dm.update_guild_data(guild.id, "welcomedm_stats", config["welcomedm_stats"])
            embed = discord.Embed(title="Welcome!", description=config["message"].format(server=guild.name), color=config["embed_color"])
            view = WelcomeDMView()
            await channel.send(embed=embed, view=view)
            return True
        except Exception as e:
            logger.error(f"Failed to setup welcome DM: {e}")
            return False

    async def _setup_applications(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            channel = analysis.existing_channels.get("applications") or await self._create_setup_channel(guild, "applications")
            log_channel = analysis.existing_channels.get("apps-log") or await self._create_setup_channel(guild, "apps-log", overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False)
            })
            config = {
                "applications_open": True,
                "log_channel_id": log_channel.id,
                "questions": ["Why do you want to join?", "Previous experience?", "How active can you be?"],
                "cooldown_days": 30,
                "applicant_dms_enabled": True,
                "auto_ping_enabled": True,
                "ping_role_id": None,
                "role_to_give_on_accept": None,
                "application_types": ["Staff"]
            }
            dm.update_guild_data(guild.id, "application_config", config)
            from modules.applications import ApplicationPersistentView
            await channel.send("Apply here using the button below!", view=ApplicationPersistentView())
            return True
        except Exception as e:
            logger.error(f"Failed to setup applications: {e}")
            return False

    async def _setup_appeals(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            channel = analysis.existing_channels.get("appeals") or await self._create_setup_channel(guild, "appeals")
            log_channel = analysis.existing_channels.get("appeals-log") or await self._create_setup_channel(guild, "appeals-log", overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False)
            })
            config = {
                "enabled": True,
                "log_channel_id": log_channel.id,
                "reviewer_role_id": None,
                "appeals_channel_id": channel.id,
                "cooldown_days": 30,
                "appellant_dms_enabled": True,
                "questions": ["Reason for appeal?", "Why should we unban you?"],
                "approval_dm": "Your appeal has been approved.",
                "denial_dm": "Your appeal has been denied."
            }
            dm.update_guild_data(guild.id, "appeals_config", config)
            if dm.get_guild_data(guild.id, "appeals", None) is None:
                dm.update_guild_data(guild.id, "appeals", {})
            if dm.get_guild_data(guild.id, "appeals_blacklist", None) is None:
                dm.update_guild_data(guild.id, "appeals_blacklist", [])
            from modules.appeals import AppealPersistentView
            await channel.send("Submit your appeal here.", view=AppealPersistentView())
            return True
        except Exception as e:
            logger.error(f"Failed to setup appeals: {e}")
            return False

    async def _setup_modmail(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            log_channel = analysis.existing_channels.get("modmail-log") or await self._create_setup_channel(guild, "modmail-log", overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False)
            })
            config = {
                "enabled": True,
                "log_channel_id": log_channel.id,
                "staff_role_id": None,
                "auto_close_hours": 168,
                "thread_style": "thread",
                "new_thread_pings": True,
                "auto_reply_message": "Thank you for contacting staff. We will be with you shortly.",
                "close_message": "This thread has been closed. If you need further assistance, please message us again."
            }
            dm.update_guild_data(guild.id, "modmail_config", config)
            if dm.get_guild_data(guild.id, "modmail_threads", None) is None:
                dm.update_guild_data(guild.id, "modmail_threads", {})
            if dm.get_guild_data(guild.id, "modmail_blocked", None) is None:
                dm.update_guild_data(guild.id, "modmail_blocked", [])
            return True
        except Exception as e:
            logger.error(f"Failed to setup modmail: {e}")
            return False

    async def _setup_suggestions(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            channel = analysis.existing_channels.get("suggestions") or await self._create_setup_channel(guild, "suggestions")
            review_channel = analysis.existing_channels.get("suggestions-review") or await self._create_setup_channel(guild, "suggestions-review", overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False)
            })
            guide_channel = analysis.existing_channels.get("suggestions-guide") or await self._create_setup_channel(guild, "suggestions-guide")

            config = {
                "enabled": True,
                "suggestions_channel_id": channel.id,
                "suggestions_review_channel_id": review_channel.id,
                "guide_channel_id": guide_channel.id,
                "cooldown_minutes": 30,
                "submitter_dms_enabled": True,
                "categories": ["Feature", "Bug", "Content", "Other"]
            }
            dm.update_guild_data(guild.id, "suggestions_config", config)

            # Send documentation to guide channel
            doc_embed = discord.Embed(
                title="💡 Suggestions System Guide",
                description="Welcome to our feedback system! Use the button in #suggestions to submit your ideas.",
                color=discord.Color.blue()
            )
            doc_embed.add_field(name="Categories", value="• Feature\n• Bug\n• Content\n• Other")
            await guide_channel.send(embed=doc_embed)

            # Use local SuggestionButton to avoid import errors
            await channel.send("Submit suggestions here!", view=SuggestionButton(guild_id=guild.id))
            return True
        except Exception as e:
            logger.error(f"Failed to setup suggestions: {e}")
            return False

    async def _setup_reminders(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {
                "enabled": True,
                "max_per_user": 10,
                "allow_dms": True,
                "fallback_channel": None
            }
            dm.update_guild_data(guild.id, "reminders_config", config)
            dm.update_guild_data(guild.id, "reminders", [])
            return True
        except Exception as e:
            logger.error(f"Failed to setup reminders: {e}")
            return False

    async def _setup_scheduled_reminders(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {
                "enabled": True,
                "reminders": []
            }
            dm.update_guild_data(guild.id, "scheduled_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup scheduled reminders: {e}")
            return False

    async def _setup_announcements(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            channel = analysis.existing_channels.get("announcements") or await self._create_setup_channel(guild, "announcements")
            config = {
                "enabled": True,
                "channel_id": channel.id,
                "auto_pin": True,
                "cross_post": False,
                "require_approval": False,
                "announcements": []
            }
            dm.update_guild_data(guild.id, "announcements_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup announcements: {e}")
            return False

    async def _setup_auto_responder(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {
                "enabled": True,
                "cooldown": 5,
                "allowed_channels": [],
                "allowed_roles": []
            }
            dm.update_guild_data(guild.id, "auto_responder_config", config)
            dm.update_guild_data(guild.id, "auto_responders", [])
            return True
        except Exception as e:
            logger.error(f"Failed to setup auto responder: {e}")
            return False

    async def _setup_economy_shop(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            shop_items = [
                {"id": 1, "name": "VIP Role", "price": 1000, "description": "Grants VIP status", "role_id": None, "stock": -1},
                {"id": 2, "name": "Custom Color", "price": 500, "description": "Get a custom color role", "role_id": None, "stock": -1}
            ]
            dm.update_guild_data(guild.id, "shop_items", shop_items)
            dm.update_guild_data(guild.id, "shop_logs", [])
            dm.update_guild_data(guild.id, "shop_channel_id", None)
            return True
        except Exception as e:
            logger.error(f"Failed to setup economy shop: {e}")
            return False

    async def _setup_leveling_shop(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            level_rewards = {
                "5": None,
                "10": None,
                "20": None,
                "50": None
            }
            dm.update_guild_data(guild.id, "level_rewards", level_rewards)
            dm.update_guild_data(guild.id, "leveling_shop_items", [])
            dm.update_guild_data(guild.id, "xp_role_multipliers", {})
            dm.update_guild_data(guild.id, "leveling_shop_channel_id", None)
            return True
        except Exception as e:
            logger.error(f"Failed to setup leveling shop: {e}")
            return False

    async def _setup_gamification(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {
                "enabled": True,
                "prestige_level": 100,
                "xp_multiplier": 1.0,
                "quests_enabled": True,
                "skills_enabled": True,
                "seasonal_event": None,
                "leaderboard_channel": None
            }
            dm.update_guild_data(guild.id, "gamification_config", config)
            dm.update_guild_data(guild.id, "gamification_data", {})
            return True
        except Exception as e:
            logger.error(f"Failed to setup gamification: {e}")
            return False

    async def _setup_reaction_roles(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            dm.update_guild_data(guild.id, "reaction_roles", {})
            dm.update_guild_data(guild.id, "reaction_role_log", [])
            return True
        except Exception as e:
            logger.error(f"Failed to setup reaction roles: {e}")
            return False

    async def _setup_reaction_menus(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            dm.update_guild_data(guild.id, "reaction_menus_config", {})
            dm.update_guild_data(guild.id, "reaction_menu_log", [])
            return True
        except Exception as e:
            logger.error(f"Failed to setup reaction menus: {e}")
            return False

    async def _setup_role_buttons(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            dm.update_guild_data(guild.id, "role_buttons_config", {})
            dm.update_guild_data(guild.id, "role_button_log", [])
            return True
        except Exception as e:
            logger.error(f"Failed to setup role buttons: {e}")
            return False

    async def _setup_mod_logging(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            channel = analysis.existing_channels.get("mod-log") or await self._create_setup_channel(guild, "mod-log", overwrites={
                guild.default_role: discord.PermissionOverwrite(view_channel=False)
            })
            config = {
                "enabled": True,
                "log_channel_id": channel.id,
                "next_case_number": 1,
                "enabled_logs": {
                    "ban": True, "unban": True, "kick": True, "warn": True, "mute": True
                },
                "ignored_channels": [],
                "ignored_roles": []
            }
            dm.update_guild_data(guild.id, "mod_logging_config", config)
            if dm.get_guild_data(guild.id, "mod_cases", None) is None:
                dm.update_guild_data(guild.id, "mod_cases", {})
            return True
        except Exception as e:
            logger.error(f"Failed to setup mod logging: {e}")
            return False

    async def _setup_chat_channels(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            channel = analysis.existing_channels.get("ai-chat") or await self._create_setup_channel(guild, "ai-chat")
            config = {
                "enabled": True,
                "channels": [channel.id],
                "personality": "Helpful and friendly AI assistant",
                "model": "gpt-4o",
                "temperature": 0.7,
                "max_history": 15
            }
            dm.update_guild_data(guild.id, "ai_chat_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup chat channels: {e}")
            return False

    async def _setup_events(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            channel = analysis.existing_channels.get("events") or await self._create_setup_channel(guild, "events")
            config = {
                "enabled": True,
                "log_channel_id": None,
                "announcement_channel_id": channel.id,
                "auto_remind": True,
                "ping_role_id": None,
                "auto_archive": True,
                "active_events": []
            }
            dm.update_guild_data(guild.id, "events_config", config)
            dm.update_guild_data(guild.id, "event_history", [])
            return True
        except Exception as e:
            logger.error(f"Failed to setup events: {e}")
            return False

    async def _setup_logging_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {
                "enabled": True,
                "log_channel_id": None,
                "enabled_events": {
                    "message_delete": True,
                    "message_edit": True,
                    "member_join": True,
                    "member_remove": True,
                    "role_create": True,
                    "role_delete": True,
                    "channel_create": True,
                    "channel_delete": True,
                    "voice_join": True,
                    "voice_leave": True
                },
                "ignored_channels": [],
                "ignored_roles": [],
                "ignored_users": []
            }
            dm.update_guild_data(guild.id, "logging_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup logging: {e}")
            return False

    async def _setup_auto_mod(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        return await self._setup_automod_system(guild, analysis)

    async def _setup_warning_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {
                "enabled": True,
                "dm_enabled": True,
                "max_warnings": 5,
                "decay_days": 30,
                "punishments": {
                    "3": "mute_60",
                    "5": "kick"
                }
            }
            dm.update_guild_data(guild.id, "warning_config", config)
            dm.update_guild_data(guild.id, "warnings_data", {})
            return True
        except Exception as e:
            logger.error(f"Failed to setup warnings: {e}")
            return False

    async def _setup_staff_promotion(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {
                "enabled": True,
                "settings": {
                    "auto_promote": True,
                    "review_mode": False,
                    "notify_on_promotion": True,
                    "review_channel": None
                },
                "tiers": [],
                "requirements": {},
                "roles_by_tier": {},
                "pending_reviews": [],
                "promotion_logs": []
            }
            dm.update_guild_data(guild.id, "staff_promo_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup staff promotion: {e}")
            return False

    async def _setup_staff_shifts(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {
                "enabled": True,
                "on_duty_role_id": None,
                "shift_channel_id": None,
                "idle_timeout_minutes": 30,
                "clock_in_notifications": True
            }
            dm.update_guild_data(guild.id, "staff_shifts_config", config)
            dm.update_guild_data(guild.id, "staff_shifts_data", {})
            return True
        except Exception as e:
            logger.error(f"Failed to setup staff shifts: {e}")
            return False

    async def _setup_staff_reviews(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {
                "enabled": True,
                "cycle": "monthly",
                "review_channel_id": None,
                "notifications_enabled": True,
                "review_dms_enabled": True,
                "criteria": [
                    {"name": "Activity", "weight": 1.0},
                    {"name": "Helpfulness", "weight": 1.0},
                    {"name": "Professionalism", "weight": 1.0}
                ],
                "thresholds": {"warning": 2.5, "promotion": 4.5}
            }
            dm.update_guild_data(guild.id, "staff_reviews_config", config)
            dm.update_guild_data(guild.id, "staff_reviews_data", {})
            return True
        except Exception as e:
            logger.error(f"Failed to setup staff reviews: {e}")
            return False

    async def _setup_starboard(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            channel = analysis.existing_channels.get("starboard") or await self._create_setup_channel(guild, "starboard")
            config = {
                "enabled": True,
                "channel_id": channel.id,
                "threshold": 3,
                "emoji": "⭐",
                "auto_pin": True,
                "pin_threshold": 10,
                "reward_thresholds": {},
                "blacklisted_channels": []
            }
            dm.update_guild_data(guild.id, "starboard_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup starboard: {e}")
            return False

    # ===== Setup methods previously referenced but missing (caused AttributeError) =====

    async def _setup_verification_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        """Set up the verification channel + persistent button + verified role."""
        try:
            # Ensure a 'Verified' role exists
            verified_role = analysis.existing_roles.get("verified") or analysis.existing_roles.get("Verified")
            if not verified_role:
                try:
                    verified_role = await guild.create_role(
                        name="Verified",
                        reason="Auto-Setup: verification system",
                        permissions=discord.Permissions(send_messages=True, read_messages=True),
                    )
                except Exception as role_err:
                    logger.warning(f"verification: could not create role: {role_err}")

            unverified_role = analysis.existing_roles.get("unverified") or analysis.existing_roles.get("Unverified")

            channel = analysis.existing_channels.get("verify") or analysis.existing_channels.get("verification") \
                or await self._create_setup_channel(guild, "verify")

            config = {
                "enabled": True,
                "channel_id": channel.id if channel else None,
                "verified_role_id": verified_role.id if verified_role else None,
                "unverified_role_id": unverified_role.id if unverified_role else None,
                "method": "button",
                "captcha_enabled": False,
                "min_account_age_days": 0,
                "verification_log": []
            }
            dm.update_guild_data(guild.id, "verification_config", config)

            # Drop a persistent verification message with a button if possible
            try:
                from modules.verification import VerificationView
                embed = discord.Embed(
                    title="🛡️ Server Verification",
                    description="Click the button below to verify and gain access to the server.",
                    color=discord.Color.green(),
                )
                if channel:
                    await channel.send(embed=embed, view=VerificationView())
            except Exception as view_err:
                logger.info(f"verification: persistent view not available, config-only setup ({view_err})")
            return True
        except Exception as e:
            logger.error(f"Failed to setup verification: {e}")
            return False

    async def _setup_welcome_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        """Set up a welcome channel + default greeting message config."""
        try:
            channel = analysis.existing_channels.get("welcome") \
                or analysis.existing_channels.get("welcome-and-goodbye") \
                or await self._create_setup_channel(guild, "welcome")

            leave_channel = analysis.existing_channels.get("goodbye") or channel

            config = {
                "enabled": True,
                "channel_id": channel.id if channel else None,
                "message": "👋 Welcome {user} to **{server}**! You're member #{member_number}.",
                "embed_enabled": True,
                "embed_color": 0x2ecc71,
                "ping_user": True,
                "show_member_number": True
            }

            leave_config = {
                "enabled": True,
                "channel_id": leave_channel.id if leave_channel else None,
                "message": "👋 Goodbye {user}, we'll miss you.",
                "embed_enabled": True,
                "embed_color": 0xe74c3c
            }

            dm.update_guild_data(guild.id, "welcome_config", config)
            dm.update_guild_data(guild.id, "leave_config", leave_config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup welcome: {e}")
            return False

    async def _setup_ticket_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        """Set up a ticket category, panel channel, persistent panel + base config."""
        try:
            # Category
            category = analysis.existing_categories.get("tickets") \
                or analysis.existing_categories.get("Tickets")
            if not category:
                try:
                    category = await guild.create_category("Tickets", reason="Auto-Setup: ticket system")
                except Exception as cat_err:
                    logger.warning(f"tickets: could not create category: {cat_err}")
                    category = None

            # Panel channel
            channel = analysis.existing_channels.get("create-ticket") \
                or analysis.existing_channels.get("tickets") \
                or await self._create_setup_channel(guild, "create-ticket", category=category)

            # Optional support role
            support_role = analysis.existing_roles.get("support") or analysis.existing_roles.get("Support")
            if not support_role:
                try:
                    support_role = await guild.create_role(name="Support", reason="Auto-Setup: tickets")
                except Exception as r:
                    logger.info(f"tickets: support role not created ({r})")

            config = {
                "enabled": True,
                "category_id": category.id if category else None,
                "panel_channel_id": channel.id if channel else None,
                "support_role_id": support_role.id if support_role else None,
                "senior_staff_role_id": None,
                "log_channel_id": None,
                "max_per_user": 1,
                "auto_close_hours": 48,
                "opener_dm_enabled": True,
                "transcript_enabled": True,
                "panel_title": "Support Tickets",
                "panel_description": "Click the button below to open a ticket.",
                "panel_color": 0x3498db
            }
            dm.update_guild_data(guild.id, "tickets_config", config)

            # Drop a persistent panel
            try:
                from modules.tickets import TicketOpenPanel
                embed = discord.Embed(
                    title=config["panel_title"],
                    description=config["panel_description"],
                    color=config["panel_color"],
                )
                if channel:
                    await channel.send(embed=embed, view=TicketOpenPanel())
            except Exception as view_err:
                logger.info(f"tickets: panel view not available, config-only setup ({view_err})")
            return True
        except Exception as e:
            logger.error(f"Failed to setup tickets: {e}")
            return False

    async def _setup_economy_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        """Set up base economy config (currency, daily reward, starting balance)."""
        try:
            config = {
                "enabled": True,
                "currency_name": "coins",
                "currency_emoji": "🪙",
                "starting_balance": 100,
                "daily_amount": 250,
                "daily_streak_bonus": 50,
                "daily_cooldown_seconds": 86400,
                "work_min": 50,
                "work_max": 200,
                "work_cooldown_seconds": 3600,
                "beg_min": 10,
                "beg_max": 50,
                "beg_cooldown_seconds": 60,
                "rob_success_rate": 0.4,
                "rob_cooldown_seconds": 3600,
                "earn_rates": {
                    "coins_per_message": 2,
                    "coins_per_voice_minute": 5,
                    "gem_chance": 0.01
                }
            }
            dm.update_guild_data(guild.id, "economy_config", config)
            # Make sure a balances dict exists so other modules don't crash on first read
            if dm.get_guild_data(guild.id, "economy_balances", None) is None:
                dm.update_guild_data(guild.id, "economy_balances", {})
            if dm.get_guild_data(guild.id, "economy_gems", None) is None:
                dm.update_guild_data(guild.id, "economy_gems", {})
            return True
        except Exception as e:
            logger.error(f"Failed to setup economy: {e}")
            return False

    async def _setup_leveling_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        """Set up XP/leveling base config."""
        try:
            config = {
                "enabled": True,
                "xp_per_message_min": 15,
                "xp_per_message_max": 25,
                "xp_per_voice_minute": 10,
                "xp_cooldown_seconds": 60,
                "level_up_announcements": True,
                "level_up_message": "Congratulations {user}, you leveled up to level {level}!",
                "level_up_channel_id": None,  # falls back to the channel where the user leveled up
                "no_xp_channel_ids": [],
                "no_xp_role_ids": [],
                "xp_multiplier_roles": {},
                "double_xp_enabled": False
            }
            dm.update_guild_data(guild.id, "leveling_config", config)
            if dm.get_guild_data(guild.id, "leveling_data", None) is None:
                dm.update_guild_data(guild.id, "leveling_data", {})
            if dm.get_guild_data(guild.id, "leveling_xp", None) is None:
                dm.update_guild_data(guild.id, "leveling_xp", {})

            # Register custom commands
            custom_cmds = dm.get_guild_data(guild.id, "custom_commands", {})
            custom_cmds["rank"] = json.dumps({"command_type": "leveling_rank"})
            custom_cmds["leaderboard"] = json.dumps({"command_type": "leveling_leaderboard"})
            custom_cmds["levels"] = json.dumps({"command_type": "leveling_levels"})
            custom_cmds["rewards"] = json.dumps({"command_type": "leveling_rewards"})
            custom_cmds["levelshop"] = json.dumps({"command_type": "leveling_shop"})
            dm.update_guild_data(guild.id, "custom_commands", custom_cmds)

            return True
        except Exception as e:
            logger.error(f"Failed to setup leveling: {e}")
            return False

    async def _setup_giveaways(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        """Set up giveaway base config + announcement channel."""
        try:
            channel = analysis.existing_channels.get("giveaways") \
                or await self._create_setup_channel(guild, "giveaways")
            config = {
                "enabled": True,
                "default_channel_id": channel.id if channel else None,
                "ping_role_id": None,
                "emoji": "🎉",
                "entry_dms": True,
                "active_giveaways": [],
            }
            dm.update_guild_data(guild.id, "giveaways_config", config)
            dm.update_guild_data(guild.id, "giveaway_settings", {"bonus_roles": {}})
            dm.update_guild_data(guild.id, "giveaways", {})
            return True
        except Exception as e:
            logger.error(f"Failed to setup giveaways: {e}")
            return False

    async def _setup_automod_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        """Set up auto-moderation base config (alias target for _setup_auto_mod)."""
        try:
            config = {
                "enabled": True,
                "rules": {
                    "invites": {"enabled": True},
                    "links": {"enabled": False, "max_links": 3, "window": 10, "action": "warn", "whitelisted_domains": []},
                    "spam": {"enabled": True, "max_messages": 5, "window": 5, "action": "delete"},
                    "mentions": {"enabled": True, "max_mentions": 5, "window": 10, "action": "warn"},
                    "caps": {"enabled": False, "threshold_pct": 70, "min_chars": 20, "action": "warn"},
                    "banned_words": {"enabled": True, "words": []}
                },
                "escalation": {
                    "reset_hours": 24,
                    "1": "warn",
                    "2": "mute_10",
                    "3": "mute_60",
                    "4": "kick",
                    "5": "ban"
                },
                "whitelist_channels": [],
                "whitelist_roles": []
            }
            dm.update_guild_data(guild.id, "automod_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup automod: {e}")
            return False

    # ===== Helper Methods =====
    
    def _register_system_commands(self, guild_id: int, system_name: str):
        """Register custom commands and auto-documentation for systems."""
        import json
        custom_cmds = dm.get_guild_data(guild_id, "custom_commands", {})
        
        help_data = {
            "anti_raid": {"title": "⚔️ Anti-Raid System", "desc": "Protects against mass joins and spam.", "cmds": [("!raidstatus", "Check security status")]},
            "tickets": {"title": "🎫 Ticket System", "desc": "Private support ticket channels.", "cmds": [("!ticket", "Create ticket"), ("!close", "Close ticket")]},
            "economy": {"title": "💰 Economy System", "desc": "Virtual currency and engagement.", "cmds": [("!daily", "Claim daily coins"), ("!balance", "Check wallet")]},
            "leveling": {"title": "⬆️ Leveling System", "desc": "XP and level progression.", "cmds": [("!rank", "Check level"), ("!leaderboard", "Top members")]},
            "giveaways": {"title": "🎁 Giveaways System", "desc": "Automated prize giveaways.", "cmds": [("!giveaway create", "Start giveaway")]},
            "automod": {"title": "🛡️ Auto-Mod", "desc": "Filter spam and prohibited content.", "cmds": [("!automod status", "View filters")]},
            "warnings": {"title": "⚠️ Warning System", "desc": "Track user warnings.", "cmds": [("!warn @user", "Issue warning"), ("!warnings", "Check warnings")]},
            "starboard": {"title": "⭐ Starboard", "desc": "Star messages to post to starboard.", "cmds": [("!starboard", "View starred messages")]},
        }
        
        if system_name in help_data:
            data = help_data[system_name]
            fields = [{"name": cmd, "value": desc, "inline": False} for cmd, desc in data["cmds"]]
            custom_cmds[f"help {system_name}"] = json.dumps({
                "command_type": "help_embed",
                "title": data["title"],
                "description": data["desc"],
                "fields": fields
            })
            
            if system_name == "economy":
                custom_cmds.setdefault("daily", json.dumps({"command_type": "economy_daily"}))
                custom_cmds.setdefault("balance", json.dumps({"command_type": "economy_balance"}))
                custom_cmds.setdefault("work", json.dumps({"command_type": "economy_work"}))
                custom_cmds.setdefault("beg", json.dumps({"command_type": "economy_beg"}))
                custom_cmds.setdefault("economylb", json.dumps({"command_type": "economy_leaderboard"}))
                custom_cmds.setdefault("shop", json.dumps({"command_type": "economy_shop"}))
                custom_cmds.setdefault("buy", json.dumps({"command_type": "economy_buy"}))
                custom_cmds.setdefault("rob", json.dumps({"command_type": "economy_rob"}))
                custom_cmds.setdefault("transfer", json.dumps({"command_type": "economy_transfer"}))
            elif system_name == "leveling":
                custom_cmds.setdefault("rank", json.dumps({"command_type": "leveling_rank"}))
                custom_cmds.setdefault("leaderboard", json.dumps({"command_type": "leveling_leaderboard"}))
            elif system_name == "tickets":
                custom_cmds.setdefault("ticket", json.dumps({"command_type": "ticket_create"}))
                custom_cmds.setdefault("close", json.dumps({"command_type": "ticket_close"}))
            elif system_name == "verification":
                custom_cmds.setdefault("verify", json.dumps({"command_type": "verification_verify"}))
            elif system_name == "appeals":
                custom_cmds.setdefault("appeal", json.dumps({"command_type": "appeal_create"}))
            elif system_name == "applications":
                custom_cmds.setdefault("apply", json.dumps({"command_type": "application_apply"}))
        
        dm.update_guild_data(guild_id, "custom_commands", custom_cmds)

    # ===== Helper Methods =====
    
    async def _analyze_server(self, guild: discord.Guild) -> ServerAnalysis:
        """Analyze existing server structure."""
        existing_channels = {}
        existing_categories = {}
        channel_permissions = {}
        private_channels = []
        public_channels = []
        
        for channel in guild.channels:
            if isinstance(channel, discord.CategoryChannel):
                existing_categories[channel.name.lower()] = channel
            elif isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
                existing_channels[channel.name.lower()] = channel
                channel_permissions[channel.id] = dict(channel.overwrites)
                everyone_overwrite = channel.overwrites_for(guild.default_role)
                if everyone_overwrite.view_channel is False:
                    private_channels.append(channel)
                else:
                    public_channels.append(channel)
        
        existing_roles = {role.name.lower(): role for role in guild.roles}
        return ServerAnalysis(
            existing_channels=existing_channels,
            existing_roles=existing_roles,
            existing_categories=existing_categories,
            channel_permissions=channel_permissions,
            private_channels=private_channels,
            public_channels=public_channels
        )

    async def _create_setup_channel(self, guild: discord.Guild, name: str, **kwargs):
        """Helper to create channel and track it."""
        ch = await guild.create_text_channel(name, **kwargs)
        if hasattr(self, '_created_channels'):
            self._created_channels.append(ch.id)
        return ch

    def _load_pending_setups(self) -> Dict[int, ServerSetup]:
        """Load pending setups from persistent storage."""
        data = dm.load_json("pending_setups", default={})
        pending = {}
        for guild_id_str, setup_dict in data.items():
            try:
                guild_id = int(guild_id_str)
                setup = ServerSetup(
                    guild_id=guild_id,
                    state=SetupState(setup_dict["state"]),
                    started_at=setup_dict["started_at"],
                    completed_at=setup_dict.get("completed_at"),
                    steps_completed=setup_dict["steps_completed"],
                    config=setup_dict["config"],
                    selected_systems=setup_dict.get("selected_systems")
                )
                pending[guild_id] = setup
            except Exception as e:
                logger.error(f"Failed to load pending setup for guild {guild_id_str}: {e}")
        return pending

    def _save_pending_setups(self):
        """Save pending setups to persistent storage."""
        data = {}
        for guild_id, setup in self._pending_setups.items():
            data[str(guild_id)] = {
                "guild_id": setup.guild_id,
                "state": setup.state.value,
                "started_at": setup.started_at,
                "completed_at": setup.completed_at,
                "steps_completed": setup.steps_completed,
                "config": setup.config,
                "selected_systems": setup.selected_systems
            }
        dm.save_json("pending_setups", data)

    def _has_animated_emoji(self, guild: discord.Guild, name: str) -> bool:
        """Check if guild has an animated emoji with given name"""
        return any(e.animated and e.name == name for e in guild.emojis)

    # ===== Event Handlers =====
    
    async def on_guild_join(self, guild: discord.Guild):
        """Handle bot joining a new guild."""
        logger.info(f"Bot joined new guild: {guild.name} (ID: {guild.id})")
        self._pending_setups[guild.id] = ServerSetup(
            guild_id=guild.id,
            state=SetupState.PENDING,
            started_at=time.time(),
            completed_at=None,
            steps_completed=[],
            config={},
            selected_systems=None
        )
        await self._send_welcome_dm(guild)
        await self._initialize_server_data(guild)

    async def _send_welcome_dm(self, guild: discord.Guild):
        """Send enhanced welcome DM to server owner."""
        owner = guild.owner or await guild.fetch_member(guild.owner_id)
        if not owner:
            return
        
        sparkle = "<a:sparkle:123>" if self._has_animated_emoji(guild, "sparkle") else "✨"
        
        embed = discord.Embed(
            title=f"{sparkle} Welcome to Miro AI — Your Community's New Engine {sparkle}",
            description=(
                f"Hello {owner.mention}! I've successfully landed in **{guild.name}**. "
                "I'm an immortal AI partner designed to help you build, manage, and scale your community!"
            ),
            color=discord.Color.blue()
        )
        embed.add_field(
            name="🛠️ Instant Setup (Recommended)",
            value="I've arrived with **33 pre-built systems** ready to deploy. Run `/autosetup` to pick what you need!",
            inline=False
        )
        embed.add_field(
            name="📚 Quick Start",
            value="• `/autosetup` - Deploy systems\n• `!help` - View all commands\n• `/bot` - Create custom features",
            inline=False
        )
        embed.set_footer(text=f"Server: {guild.name} • Type !help for all systems")
        
        try:
            await owner.send(embed=embed)
        except discord.Forbidden:
            if guild.system_channel:
                await guild.system_channel.send(f"🎊 **Miro AI has arrived!** Check DMs, {owner.mention}!")

    async def _initialize_server_data(self, guild: discord.Guild):
        """Initialize default data for a guild."""
        default_config = {
            "prefix": "!",
            "log_channel": None,
            "welcome_channel": None,
            "verify_channel": None,
            "tickets_channel": None,
        }
        for key, value in default_config.items():
            dm.update_guild_data(guild.id, key, value)
        logger.info(f"Initialized default data for guild {guild.id}")

    # ===== Slash Command =====
    
    @app_commands.command(name="autosetup", description="Launch the interactive auto-setup wizard")
    @app_commands.checks.has_permissions(administrator=True)
    async def autosetup_command(self, interaction: discord.Interaction):
        """Interactive auto-setup command with paginated menus"""
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("❌ This command must be used in a server.", ephemeral=True)
        
        view = CategorySelectionView(self, guild.id)
        embed = self.create_welcome_embed(guild)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
    
    @autosetup_command.error
    async def autosetup_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need Administrator permissions to use this command.", ephemeral=True)


# Added imports for button components
from modules.tickets import TicketModal
from modules.suggestions import SuggestionModal
from modules.staff_system import StaffApplicationModal


class CreateTicketButton(discord.ui.View):
    def __init__(self, guild_id: int = 0):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label='Create Ticket', style=discord.ButtonStyle.primary, custom_id='create_ticket_button_persistent')
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        system = getattr(interaction.client, 'tickets', None)
        if not system:
            await interaction.response.send_message('❌ Ticket system is not available.', ephemeral=True)
            return
        settings = system.get_guild_settings(interaction.guild_id)
        max_per_user = settings.get('max_per_user', 0)
        if max_per_user > 0:
            open_tickets = system.get_user_tickets(interaction.guild_id, interaction.user.id)
            if len(open_tickets) >= max_per_user:
                await interaction.response.send_message(
                    f'❌ You already have {len(open_tickets)} open tickets. Please close one before opening another.',
                    ephemeral=True
                )
                return
        await interaction.response.send_modal(TicketModal())


class SuggestionButton(discord.ui.View):
    def __init__(self, guild_id: int = 0):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label='Submit Suggestion', style=discord.ButtonStyle.success, custom_id='suggestion_button_persistent')
    async def submit_suggestion(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.guild_id or interaction.guild_id
        if not guild_id:
            await interaction.response.send_message('❌ Cannot determine guild.', ephemeral=True)
            return
        modal = SuggestionModal(guild_id)
        await interaction.response.send_modal(modal)


class ApplyStaffButton(discord.ui.View):
    def __init__(self, guild_id: int = 0):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label='Apply for Staff', style=discord.ButtonStyle.primary, custom_id='apply_staff_button_persistent')
    async def apply_staff(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = StaffApplicationModal(interaction.client)
        await interaction.response.send_modal(modal)


class RoleSelectButton(ui.Button):
    def __init__(self, guild_id: int = 0):
        super().__init__(label='Select Role', style=discord.ButtonStyle.secondary, custom_id='role_select_button_persistent')
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message('❌ This can only be used in a server.', ephemeral=True)
            return
        view = ui.View(timeout=30)

        class RoleSelect(ui.RoleSelect):
            def __init__(self):
                super().__init__(placeholder='Select a role to assign or remove', min_values=1, max_values=1)

            async def callback(self, select_interaction: discord.Interaction):
                if not self.values:
                    await select_interaction.response.send_message('No role selected.', ephemeral=True)
                    return
                role = self.values[0]
                user = select_interaction.user
                try:
                    if role in user.roles:
                        await user.remove_roles(role)
                        await select_interaction.response.send_message(f'✅ Removed role: {role.name}', ephemeral=True)
                    else:
                        await user.add_roles(role)
                        await select_interaction.response.send_message(f'✅ Added role: {role.name}', ephemeral=True)
                except discord.Forbidden:
                    await select_interaction.response.send_message('❌ I lack permissions to manage roles.', ephemeral=True)
                except Exception as e:
                    logger.error(f'Error toggling role: {e}')
                    await select_interaction.response.send_message('❌ An error occurred.', ephemeral=True)

        view.add_item(RoleSelect())
        await interaction.response.send_message('Select a role to toggle:', view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(AutoSetup(bot))
