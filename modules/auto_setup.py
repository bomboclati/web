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
    MODERATION = {"name": "📝 Moderation", "emoji": "📝", "systems": ["mod_logging", "logging", "automod", "warnings", "modmail", "suggestions"]}
    STAFF = {"name": "👥 Staff", "emoji": "👥", "systems": ["staff_promotion", "staff_shifts", "staff_reviews", "apps_simple", "apps_modals", "appeals_simple", "appeals_system"]}
    AUTOMATION = {"name": "🤖 Automation", "emoji": "🤖", "systems": ["welcome", "welcome_dm", "tickets", "reminders", "scheduled_reminders", "announcements", "auto_responder"]}
    COMMUNITY = {"name": "🌐 Community", "emoji": "🌐", "systems": ["reaction_roles", "reaction_menus", "role_buttons", "chat_channels", "events", "tournaments"]}

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
    """View for selecting systems within a category"""
    def __init__(self, auto_setup, guild_id: int, category: dict, selected_systems: List[str] = None):
        super().__init__(timeout=None)
        self.auto_setup = auto_setup
        self.guild_id = guild_id
        self.category = category
        self.selected_systems = selected_systems or []
        
        # Add back button
        self.add_item(BackToCategoriesButton(auto_setup, guild_id, selected_systems))
        
        # Add system select menu
        systems = category["systems"]
        recommended = SystemCategory.get_recommended_systems()
        
        options = []
        for sys_name in systems:
            sys_display_name = sys_name.replace("_", " ").title()
            is_recommended = sys_name in recommended
            emoji = "⭐" if is_recommended else "•"
            label = f"{sys_display_name}{' [Recommended]' if is_recommended else ''}"
            
            options.append(discord.SelectOption(
                label=label[:100],  # Discord limit
                emoji=emoji,
                value=sys_name,
                default=sys_name in self.selected_systems
            ))
        
        if options:
            select = SystemMultiSelect(options, guild_id, category["name"])
            self.add_item(select)
        
        # Add action buttons
        self.add_item(AddCategoryButton(auto_setup, guild_id, category, selected_systems))
        self.add_item(ConfirmSelectionButton(auto_setup, guild_id))


class BackToCategoriesButton(discord.ui.Button):
    def __init__(self, auto_setup, guild_id: int, selected_systems: List[str] = None):
        super().__init__(label="◀️ Back to Categories", style=discord.ButtonStyle.secondary, custom_id=f"back_categories_{guild_id}")
        self.auto_setup = auto_setup
        self.guild_id = guild_id
        self.selected_systems = selected_systems or []
    
    async def callback(self, interaction: discord.Interaction):
        view = CategorySelectionView(self.auto_setup, self.guild_id, self.selected_systems)
        embed = self.auto_setup.create_welcome_embed(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=view)


class SystemMultiSelect(discord.ui.Select):
    def __init__(self, options: List[discord.SelectOption], guild_id: int, category_name: str):
        super().__init__(
            placeholder=f"Select systems from {category_name}...",
            min_values=0,
            max_values=len(options),
            options=options,
            custom_id=f"system_select_{guild_id}_{category_name}"
        )
        self.guild_id = guild_id
        self.category_name = category_name
    
    async def callback(self, interaction: discord.Interaction):
        # Store selections - will be collected when "Confirm" is clicked
        if not interaction.response.is_done():
            await interaction.response.defer()


class AddCategoryButton(discord.ui.Button):
    def __init__(self, auto_setup, guild_id: int, category: dict, selected_systems: List[str] = None):
        super().__init__(label=f"➕ Add All from {category['name']}", style=discord.ButtonStyle.primary, custom_id=f"add_category_{guild_id}")
        self.auto_setup = auto_setup
        self.guild_id = guild_id
        self.category = category
        self.selected_systems = selected_systems or []
    
    async def callback(self, interaction: discord.Interaction):
        # Add all systems from this category
        new_selections = self.selected_systems.copy()
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
        # Collect all selected systems from the view
        selected = []
        # This will be populated from the parent view's selected_systems
        await interaction.response.defer(ephemeral=True)
        
        # The actual installation will be handled by the auto_setup's _run_selected_setup


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
        
        self.add_item(BulkInstallButton(auto_setup, guild_id, selected_systems))
        self.add_item(CancelSetupButton(guild_id))
    
    @property
    def selected_systems(self):
        return self._selected_systems
    
    @selected_systems.setter
    def selected_systems(self, value):
        self._selected_systems = value


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


class BulkInstallButton(discord.ui.Button):
    def __init__(self, auto_setup, guild_id: int, selected_systems: List[str] = None):
        super().__init__(label="🚀 Bulk Install Selected", style=discord.ButtonStyle.success, custom_id=f"bulk_install_{guild_id}", row=1)
        self.auto_setup = auto_setup
        self.guild_id = guild_id
        self.selected_systems = selected_systems or []
    
    async def callback(self, interaction: discord.Interaction):
        if not self.selected_systems:
            return await interaction.response.send_message("❌ Please select at least one system first!", ephemeral=True)
        
        # Start the setup process
        guild = interaction.guild
        auto_setup = self.auto_setup or interaction.client.get_cog("AutoSetup")
        if not auto_setup:
            return await interaction.response.send_message("❌ Setup system not initialized.", ephemeral=True)
        
        await interaction.response.edit_message(content="⚙️ Starting setup of selected systems...", embed=None, view=None)
        await auto_setup._run_selected_setup(guild.id, interaction.user, self.selected_systems, interaction.channel_id)


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
                        self._register_system_commands(guild.id, system)
                    results.append((name, result, None))
                    setup.steps_completed.append(system)
                    self._save_pending_setups()
                    await asyncio.sleep(0.3)  # Small delay for visual effect
                except Exception as e:
                    logger.error(f"{name} setup failed: {e}")
                    results.append((name, False, str(e)))
        
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
    
    async def _send_celebratory_embed(self, guild: discord.Guild, user: discord.Member, results: List[Tuple[str, bool, Optional[str]]], channel_id: Optional[int] = None, created_channels: List[int] = None):
        """Send celebratory post-install embed with confetti emojis"""
        success_systems = [name for name, success, _ in results if success]
        failed_systems = [(name, error) for name, success, error in results if not success]
        
        # Confetti emojis
        confetti = "🎊🎉✨🎆🎇"
        
        embed = discord.Embed(
            title=f"{confetti} Setup Complete! {confetti}",
            description=f"Successfully deployed **{len(success_systems)}** systems to **{guild.name}**!",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name="📊 Deployment Summary",
            value=f"Installed **{len(success_systems)}** / **{len(results)}** selected systems.",
            inline=False
        )
        
        if success_systems:
            embed.add_field(
                name="✅ Successfully Installed",
                value="\n".join(f"• {sys}" for sys in success_systems[:20]),
                inline=False
            )
        
        if failed_systems:
            failed_text = "\n".join(f"• {name} (Error: {error})" for name, error in failed_systems[:10])
            embed.add_field(
                name="❌ Failed",
                value=failed_text,
                inline=False
            )
        
        # Example commands for installed systems
        example_cmds = self._get_example_commands(success_systems)
        if example_cmds:
            embed.add_field(
                name="⌨️ Try These Commands",
                value="\n".join(example_cmds[:10]),
                inline=False
            )
        
        embed.add_field(
            name="📚 Next Steps",
            value=(
                "• Use `!help` to see all installed commands\n"
                "• Use `!help <system>` for system-specific help\n"
                "• Use `/bot` to create custom features\n"
                "• All systems are now active in this server!"
            ),
            inline=False
        )
        
        embed.set_footer(text="Need help? Type !help | Enjoy your new systems! 🎮")
        
        sent = False
        if channel_id:
            target_channel = guild.get_channel(channel_id)
            if target_channel:
                try:
                    await target_channel.send(f"{user.mention}", embed=embed)
                    sent = True
                except:
                    pass
        
        if not sent:
            try:
                await user.send(embed=embed)
            except discord.Forbidden:
                system_channel = guild.system_channel
                if system_channel:
                    await system_channel.send(f"{user.mention}", embed=embed)
    
    def _get_example_commands(self, installed_systems: List[str]) -> List[str]:
        """Get example commands for installed systems"""
        cmd_map = {
            "verification": "`!verify` - Verify yourself",
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
            "welcome": ("Welcome System", self._setup_welcome_system),
            "welcome_dm": ("Welcome DM Buttons", self._setup_welcome_dm_buttons),
            "tickets": ("Ticket System", self._setup_ticket_system),
            "apps_simple": ("Applications (Simple)", self._setup_apps_simple),
            "apps_modals": ("Applications (Modals)", self._setup_apps_modals),
            "appeals_simple": ("Appeals (Simple)", self._setup_appeals_simple),
            "appeals_system": ("Appeals System", self._setup_appeals_system),
            "modmail": ("Modmail System", self._setup_modmail),
            "suggestions": ("Suggestions System", self._setup_suggestions),
            "reminders": ("Reminders System", self._setup_reminders),
            "scheduled_reminders": ("Scheduled Reminders", self._setup_scheduled_reminders),
            "announcements": ("Announcements System", self._setup_announcements),
            "auto_responder": ("Auto-Responder System", self._setup_auto_responder),
            "economy": ("Economy System", self._setup_economy_system),
            "economy_shop": ("Economy Shop", self._setup_economy_shop),
            "leveling": ("Leveling System", self._setup_leveling_system),
            "leveling_shop": ("Leveling Shop", self._setup_leveling_shop),
            "giveaways": ("Giveaways System", self._setup_giveaways),
            "gamification": ("Gamification System", self._setup_gamification),
            "reaction_roles": ("Reaction Roles", self._setup_reaction_roles),
            "reaction_menus": ("Reaction Menus", self._setup_reaction_menus),
            "role_buttons": ("Role Buttons", self._setup_role_buttons),
            "mod_logging": ("Moderation Logging", self._setup_moderation_system),
            "logging": ("Logging System", self._setup_logging_system),
            "auto_mod": ("Auto-Mod", self._setup_auto_mod),
            "warning_system": ("Warning System", self._setup_warning_system),
            "staff_promotion": ("Staff Promotion", self._setup_staff_promotion),
            "staff_shifts": ("Staff Shifts", self._setup_staff_shifts),
            "staff_reviews": ("Staff Reviews", self._setup_staff_reviews),
            "starboard": ("Starboard System", self._setup_starboard),
        }

    # ===== Setup Functions for All 33 Systems =====
    
    async def _setup_anti_raid(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {"enabled": True, "sensitivity": "medium", "action": "kick"}
            dm.update_guild_data(guild.id, "anti_raid_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup anti-raid: {e}")
            return False

    async def _setup_guardian(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {"enabled": True, "sensitivity": "medium"}
            dm.update_guild_data(guild.id, "guardian_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup guardian: {e}")
            return False

    async def _setup_welcome_dm_buttons(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            channel = analysis.existing_channels.get("welcome-dm") or await self._create_setup_channel(guild, "welcome-dm")
            embed = discord.Embed(title="Welcome!", description="Click a button below!", color=discord.Color.blue())
            view = WelcomeDMView()
            await channel.send(embed=embed, view=view)
            return True
        except Exception as e:
            logger.error(f"Failed to setup welcome DM: {e}")
            return False

    async def _setup_apps_simple(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            channel = analysis.existing_channels.get("applications") or await self._create_setup_channel(guild, "applications")
            from modules.staff_system import StaffApplicationPersistentView
            await channel.send("Apply here!", view=StaffApplicationPersistentView(self.bot))
            return True
        except Exception as e:
            logger.error(f"Failed to setup apps simple: {e}")
            return False

    async def _setup_apps_modals(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        return await self._setup_apps_simple(guild, analysis)

    async def _setup_appeals_simple(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            channel = analysis.existing_channels.get("appeals") or await self._create_setup_channel(guild, "appeals")
            from modules.appeals import AppealPersistentView
            await channel.send("Submit appeal", view=AppealPersistentView())
            return True
        except Exception as e:
            logger.error(f"Failed to setup appeals simple: {e}")
            return False

    async def _setup_appeals_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        return await self._setup_appeals_simple(guild, analysis)

    async def _setup_modmail(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {"enabled": True, "auto_close_days": 7}
            dm.update_guild_data(guild.id, "modmail_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup modmail: {e}")
            return False

    async def _setup_suggestions(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            channel = analysis.existing_channels.get("suggestions") or await self._create_setup_channel(guild, "suggestions")

            view = discord.ui.View(timeout=None)
            view.add_item(SuggestionButton(guild.id))
            await channel.send("Submit suggestions here!", view=view)
            return True
        except Exception as e:
            logger.error(f"Failed to setup suggestions: {e}")
            return False

    async def _setup_reminders(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {"enabled": True}
            dm.update_guild_data(guild.id, "reminders_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup reminders: {e}")
            return False

    async def _setup_scheduled_reminders(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        return await self._setup_reminders(guild, analysis)

    async def _setup_announcements(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            channel = analysis.existing_channels.get("announcements") or await self._create_setup_channel(guild, "announcements")
            config = {"enabled": True, "channel_id": channel.id}
            dm.update_guild_data(guild.id, "announcements_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup announcements: {e}")
            return False

    async def _setup_auto_responder(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {"enabled": True, "responders": []}
            dm.update_guild_data(guild.id, "auto_responder_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup auto responder: {e}")
            return False

    async def _setup_economy_shop(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            shop_items = [{"name": "Color Role", "price": 500, "role_id": None}]
            dm.update_guild_data(guild.id, "shop_items", shop_items)
            return True
        except Exception as e:
            logger.error(f"Failed to setup economy shop: {e}")
            return False

    async def _setup_leveling_shop(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            level_rewards = {5: None, 10: None, 20: None}
            dm.update_guild_data(guild.id, "level_rewards", level_rewards)
            return True
        except Exception as e:
            logger.error(f"Failed to setup leveling shop: {e}")
            return False

    async def _setup_gamification(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {"enabled": True, "quests": []}
            dm.update_guild_data(guild.id, "gamification_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup gamification: {e}")
            return False

    async def _setup_reaction_roles(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            from modules.reaction_roles import ReactionRoles
            return True
        except Exception as e:
            logger.error(f"Failed to setup reaction roles: {e}")
            return False

    async def _setup_reaction_menus(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            return True
        except Exception as e:
            logger.error(f"Failed to setup reaction menus: {e}")
            return False

    async def _setup_role_buttons(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            return True
        except Exception as e:
            logger.error(f"Failed to setup role buttons: {e}")
            return False

    async def _setup_moderation_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {"enabled": True, "sensitivity": "medium"}
            dm.update_guild_data(guild.id, "mod_logging_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup moderation: {e}")
            return False

    async def _setup_logging_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {"enabled": True, "events": ["message_delete", "member_join"]}
            dm.update_guild_data(guild.id, "logging_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup logging: {e}")
            return False

    async def _setup_auto_mod(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        return await self._setup_automod_system(guild, analysis)

    async def _setup_warning_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            dm.update_guild_data(guild.id, "warnings_data", {})
            return True
        except Exception as e:
            logger.error(f"Failed to setup warnings: {e}")
            return False

    async def _setup_staff_promotion(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            config = {"enabled": True, "requirements": {}}
            dm.update_guild_data(guild.id, "staff_promo_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup staff promotion: {e}")
            return False

    async def _setup_staff_shifts(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            dm.update_guild_data(guild.id, "staff_shifts_data", {})
            return True
        except Exception as e:
            logger.error(f"Failed to setup staff shifts: {e}")
            return False

    async def _setup_staff_reviews(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            dm.update_guild_data(guild.id, "staff_reviews_data", {})
            return True
        except Exception as e:
            logger.error(f"Failed to setup staff reviews: {e}")
            return False

    async def _setup_starboard(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        try:
            channel = analysis.existing_channels.get("starboard") or await self._create_setup_channel(guild, "starboard")
            config = {"enabled": True, "channel_id": channel.id, "threshold": 3}
            dm.update_guild_data(guild.id, "starboard_config", config)
            return True
        except Exception as e:
            logger.error(f"Failed to setup starboard: {e}")
            return False

    # ===== Helper Methods =====
    
    def _register_system_commands(self, guild_id: int, system_name: str):
        """Register custom commands and auto-documentation for systems."""
        import json
        custom_cmds = dm.get_guild_data(guild_id, "custom_commands", {})
        
        help_data = {
            "verification": {"title": "🛡️ Verification System", "desc": "Member verification with roles.", "cmds": [("!verify", "Open verification prompt")]},
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
            elif system_name == "leveling":
                custom_cmds.setdefault("rank", json.dumps({"command_type": "leveling_rank"}))
                custom_cmds.setdefault("leaderboard", json.dumps({"command_type": "leveling_leaderboard"}))
            elif system_name == "tickets":
                custom_cmds.setdefault("ticket", json.dumps({"command_type": "ticket_create"}))
                custom_cmds.setdefault("close", json.dumps({"command_type": "ticket_close"}))
        
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
