import discord
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
            await interaction.response.send_message("✅ Rules accepted!", ephemeral=True)


class CreateTicketButton(discord.ui.View):
    def __init__(self, guild_id: int = 0, channel_id: int = 0):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.primary, custom_id="create_ticket_persistent")
    async def create_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild: return
        
        # Try finding channel ID from various possible keys
        ch_id = dm.get_guild_data(guild.id, 'tickets_channel') or dm.get_guild_data(guild.id, 'ticket_queue_channel')
        channel = guild.get_channel(ch_id) if ch_id else discord.utils.get(guild.text_channels, name="ticket-queue")
        
        if not channel:
            return await interaction.response.send_message("❌ Ticket channel not found. Please contact staff.", ephemeral=True)

        try:
            thread = await channel.create_thread(
                name=f"ticket-{interaction.user.display_name}",
                type=discord.ChannelType.private_thread if guild.premium_tier >= 2 else discord.ChannelType.public_thread,
                inviter=interaction.user
            )
            await thread.send(f"🎫 **New Ticket**\n{interaction.user.mention} has opened a ticket. Staff will be with you shortly.")
            await interaction.response.send_message(f"✅ Ticket created! Go to {thread.mention}", ephemeral=True)
        except Exception as e:
            logger.error(f"Failed to create ticket: {e}")
            await interaction.response.send_message("❌ Failed to create ticket thread.", ephemeral=True)


class SuggestionButton(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True
    
    @discord.ui.button(label="Submit Suggestion", style=discord.ButtonStyle.primary, custom_id="suggestion_submit_persistent")
    async def submit_suggestion_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Use `/suggest` or `!suggest` to submit a suggestion!", ephemeral=True)


class ApplyStaffButton(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.primary, custom_id="staff_apply_persistent")
    async def apply_staff_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = StaffApplicationModal(guild_id=self.guild_id)
        await interaction.response.send_modal(modal)


class AppealButton(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    @discord.ui.button(label="Submit Appeal", style=discord.ButtonStyle.primary, custom_id="appeal_submit_persistent")
    async def appeal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AppealModal(guild_id=self.guild_id)
        await interaction.response.send_modal(modal)


class AppealModal(discord.ui.Modal):
    """Modal for ban/mute appeals"""
    def __init__(self, guild_id: int):
        super().__init__(title="Submit Ban/Mute Appeal", timeout=None)
        self.guild_id = guild_id

        self.reason_input = discord.ui.TextInput(
            label="What punishment are you appealing?",
            style=discord.TextStyle.short,
            placeholder="e.g., Ban, Mute, etc.",
            required=True,
            min_length=3,
            max_length=50
        )

        self.details_input = discord.ui.TextInput(
            label="Appeal Details",
            style=discord.TextStyle.paragraph,
            placeholder="Explain why you should be unbanned/unmuted. Include any relevant information or evidence.",
            required=True,
            min_length=50,
            max_length=1000
        )

        self.add_item(self.reason_input)
        self.add_item(self.details_input)

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Error: Guild not found.", ephemeral=True)
            return

        # Find appeal logs channel
        logs_channel_id = dm.get_guild_data(guild.id, "appeal_logs_channel")
        logs_channel = guild.get_channel(logs_channel_id)

        if logs_channel:
            embed = discord.Embed(
                title="⚖️ New Appeal Submission",
                description=f"Appeal from {interaction.user.mention}",
                color=discord.Color.orange()
            )
            embed.add_field(name="Punishment Type", value=self.reason_input.value, inline=True)
            embed.add_field(name="Details", value=self.details_input.value, inline=False)
            embed.set_footer(text=f"User ID: {interaction.user.id}")

            # Create approve/deny buttons
            view = AppealReviewView(interaction.user.id)
            await logs_channel.send(embed=embed, view=view)
            await interaction.response.send_message("✅ Your appeal has been submitted! Staff will review it shortly.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Appeal system is not properly configured. Please contact staff.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in appeal modal: {error}")
        await interaction.response.send_message("❌ An error occurred while submitting your appeal.", ephemeral=True)


class AppealReviewView(discord.ui.View):
    def __init__(self, applicant_id: int):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="appeal_approve", emoji="✅")
    async def approve_appeal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("You don't have permission to review appeals.", ephemeral=True)
            return

        applicant = interaction.guild.get_member(self.applicant_id)
        if applicant:
            try:
                # Attempt to unban/unmute (this is a simplified version)
                embed = discord.Embed(
                    title="✅ Appeal Approved",
                    description=f"Your appeal has been approved by {interaction.user.mention}",
                    color=discord.Color.green()
                )
                await applicant.send(embed=embed)
                await interaction.response.send_message(f"✅ Approved appeal for {applicant.mention}", ephemeral=True)
            except:
                await interaction.response.send_message("✅ Appeal approved, but couldn't DM the user.", ephemeral=True)
        else:
            await interaction.response.send_message("✅ Appeal approved (user not found in server)", ephemeral=True)

        # Disable buttons
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="appeal_deny", emoji="❌")
    async def deny_appeal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("You don't have permission to review appeals.", ephemeral=True)
            return

        applicant = interaction.guild.get_member(self.applicant_id)
        if applicant:
            try:
                embed = discord.Embed(
                    title="❌ Appeal Denied",
                    description=f"Your appeal has been denied by {interaction.user.mention}",
                    color=discord.Color.red()
                )
                await applicant.send(embed=embed)
                await interaction.response.send_message(f"❌ Denied appeal for {applicant.mention}", ephemeral=True)
            except:
                await interaction.response.send_message("❌ Appeal denied, but couldn't DM the user.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Appeal denied (user not found in server)", ephemeral=True)

        # Disable buttons
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)


class StaffApplicationModal(discord.ui.Modal):
    """Modal for staff applications"""
    def __init__(self, guild_id: int):
        super().__init__(title="Staff Application", timeout=None)
        self.guild_id = guild_id
        
        self.reason_input = discord.ui.TextInput(
            label="Why do you want to be staff?",
            style=discord.TextStyle.paragraph,
            placeholder="Tell us about yourself and why you'd be a good fit...",
            required=True,
            min_length=50,
            max_length=1000
        )
        
        self.experience_input = discord.ui.TextInput(
            label="Experience",
            style=discord.TextStyle.paragraph,
            placeholder="Any previous moderation experience? (optional)",
            required=False,
            min_length=0,
            max_length=1000
        )
        
        self.add_item(self.reason_input)
        self.add_item(self.experience_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Error: Guild not found.", ephemeral=True)
            return
        
        # Find or create applications channel
        apps_channel = discord.utils.get(guild.text_channels, name="applications")
        if not apps_channel:
            apps_channel = discord.utils.get(guild.text_channels, name="staff-applications")
        
        if apps_channel:
            embed = discord.Embed(
                title="📝 New Staff Application",
                description=f"Application from {interaction.user.mention}",
                color=discord.Color.purple()
            )
            embed.add_field(name="Reason", value=self.reason_input.value or "Not provided", inline=False)
            embed.add_field(name="Experience", value=self.experience_input.value or "Not provided", inline=False)
            embed.set_footer(text=f"User ID: {interaction.user.id}")
            
            await apps_channel.send(embed=embed)
            await interaction.response.send_message("✅ Your application has been submitted!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Applications channel not found. Please contact staff.", ephemeral=True)
    
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Error in staff application modal: {error}")
        await interaction.response.send_message("❌ An error occurred while submitting your application.", ephemeral=True)


class RoleSelectButton(discord.ui.Button):
    """A single button for role selection, not a View"""
    def __init__(self, guild_id: int, role_name: str, role_id: Optional[int] = None, emoji: str = None):
        # Create unique custom_id for each role button
        custom_id = f"role_select_{guild_id}_{role_name.replace(' ', '_').lower()}"
        super().__init__(
            label=role_name,
            style=discord.ButtonStyle.secondary,
            custom_id=custom_id,
            emoji=emoji
        )
        self.guild_id = guild_id
        self.role_name = role_name
        self.role_id = role_id
    
    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("Error: Guild not found.", ephemeral=True)
            return
        
        role = None
        if self.role_id:
            role = guild.get_role(self.role_id)
        if not role:
            role = discord.utils.get(guild.roles, name=self.role_name)
        
        if role:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role)
                await interaction.response.send_message(f"Removed {self.role_name} role!", ephemeral=True)
            else:
                await interaction.user.add_roles(role)
                await interaction.response.send_message(f"Added {self.role_name} role!", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Role '{self.role_name}' not found.", ephemeral=True)


class AutoSetup:
    def __init__(self, bot):
        self.bot = bot
        self._pending_setups: Dict[int, ServerSetup] = {}
        self._setup_messages: Dict[int, int] = {}
        self._startup_guilds = set()

    async def _analyze_server(self, guild: discord.Guild) -> ServerAnalysis:
        """Analyze existing server structure to avoid duplicates and respect permissions."""
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

                # Check if channel is private (has @everyone view_channel denied)
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

    async def on_guild_join(self, guild: discord.Guild):
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

    async def on_guild_remove(self, guild: discord.Guild):
        logger.info(f"Bot removed from guild: {guild.name} (ID: {guild.id})")
        # Clean up guild-specific data
        await self._remove_server_data(guild)

    async def _initialize_server_data(self, guild: discord.Guild):
        default_config = {
            "prefix": "!",
            "log_channel": None,
            "report_channel": None,
            "welcome_channel": None,
            "welcome_message": "Welcome {user} to {server}!",
            "leave_message": "{user} left {server}",
            "verify_channel": None,
            "rules_channel": None,
            "announcements_channel": None,
            "modmail_channel": None,
            "tickets_channel": None,
            "applications_channel": None,
            "moderation_config": {
                "enabled": False,
                "sensitivity": "medium"
            },
            "conflict_resolution_config": {
                "enabled": True,
                "sensitivity": "medium",
                "auto_intervene": True,
                "notify_mods": True
            },
            "community_health_config": {
                "enabled": True,
                "analysis_interval_hours": 24,
                "health_reports_enabled": True
            },
            "leveling_config": {
                "enabled": False,
                "xp_per_message": 1,
                "xp_per_voice_minute": 0.5
            },
            "economy_config": {
                "enabled": False,
                "daily_reward": 100
            },
            "modmail_config": {
                "enabled": False,
                "auto_close_days": 7,
                "notify_channel": None
            },
            "tickets_config": {
                "enabled": False,
                "categories": ["General", "Support", "Billing", "Other"]
            },
            "reaction_roles": []
        }

        for key, value in default_config.items():
            dm.update_guild_data(guild.id, key, value)

        logger.info(f"Initialized default data for guild {guild.id}")

    async def _remove_server_data(self, guild: discord.Guild):
        """Remove server data when the bot leaves a guild."""
        try:
            import os
            # Remove guild-specific data file
            filename = f"guild_{guild.id}.json"
            path = os.path.join("data", filename)
            if os.path.exists(path):
                os.remove(path)
                logger.info(f"Removed data file for guild {guild.id} ({guild.name})")
            else:
                logger.info(f"No data file found for guild {guild.id} ({guild.name})")
        except Exception as e:
            logger.error(f"Failed to remove data for guild {guild.id}: {e}")

    async def _send_welcome_dm(self, guild: discord.Guild):
        """Send a polished, friendly welcome DM to the server owner without auto-setup buttons."""
        owner = guild.owner or await guild.fetch_member(guild.owner_id)
        if not owner: return

        # Single polished embed with comprehensive welcome message
        embed = discord.Embed(
            title="🎉 Welcome to Miro Bot!",
            description=f"Hello {owner.mention}! I'm thrilled to be joining **{guild.name}**. Let's make your Discord server amazing together! 🤖",
            color=discord.Color.from_rgb(88, 101, 242) # Discord Blurple
        )

        embed.add_field(
            name="🤖 What I Can Do",
            value="I'm an AI-powered Discord bot designed to help you build and manage incredible features for your community. From automated systems to custom commands, I can create almost anything you need!",
            inline=False
        )

        embed.add_field(
            name="🚀 Getting Started",
            value="**Main Command:** Use `/bot` to create features - just describe what you want in plain English!\n\n**Bulk Setup:** Run `/autosetup` to quickly install 33 pre-built systems like verification, tickets, applications, and more.\n\n**Configuration:** Customize your AI settings with these commands:\n• `/config key <provider> <api_key>` - Set your API key\n• `/config provider <provider>` - Choose AI provider\n• `/config model <model>` - Select AI model\n\n*All settings can be changed later, so don't worry about getting it perfect right away!*",
            inline=False
        )

        embed.add_field(
            name="📚 Quick Examples",
            value="• `/bot create welcome system`\n• `/bot add verification`\n• `/bot setup ticket system`\n• `/bot make a daily reward command`",
            inline=False
        )

        embed.set_footer(text=f"Server: {guild.name} • ID: {guild.id} • Need help? Use /help anytime!")

        try:
            await owner.send(embed=embed)
            logger.info(f"Sent polished welcome DM to owner of {guild.name}")
        except discord.Forbidden:
            logger.warning(f"Could not DM owner of {guild.name} - DMs may be disabled")

    # Removed _start_interactive_setup method as part of eliminating auto-setup buttons
    # Users now use /autosetup command for bulk setup instead of interactive buttons

    # async def _start_interactive_setup(self, interaction: discord.Interaction, guild_id: int = None):
    #     """Start the interactive system selection setup."""
    #     if guild_id is None:
    #         guild_id = interaction.guild_id
    #
    #     embed = discord.Embed(
    #         title=":gear: Server Auto-Setup",
    #         description="Let's customize your server setup! Choose which systems you'd like me to install. I'll analyze your existing server structure and avoid creating duplicates.",
    #         color=discord.Color.blurple()
    #     )
    #
    #     embed.add_field(
    #         name=":white_check_mark: Available Systems",
    #         value="• **Verification** - Member verification with roles\n• **Tickets** - Support ticket system\n• **Applications** - Staff application system\n• **Appeals** - Ban/unmute appeal system\n• **Economy** - Virtual currency and shop\n• **Leveling** - XP and level-up system\n• **Welcome** - Welcome messages and roles\n• **Moderation** - Auto-mod and logging",
    #         inline=False
    #     )
    #
    #     embed.add_field(
    #         name=":information_source: How It Works",
    #         value="Click the buttons below to toggle systems on/off. When you're ready, click **Start Setup** to begin installation.",
    #         inline=False
    #     )
    #
    #     embed.set_footer(text="You can change these settings later using /bot commands.")
    #
    #     view = SystemSelectionView(self, guild_id)
    #     await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    def _register_system_commands(self, guild_id: int, system_name: str):
        """Helper to register custom commands for a system set up by the auto-setup."""
        import json
        from data_manager import dm
        custom_cmds = dm.get_guild_data(guild_id, "custom_commands", {})
        
        if system_name == "economy":
            custom_cmds["daily"] = json.dumps({"command_type": "economy_daily"})
            custom_cmds["balance"] = json.dumps({"command_type": "economy_balance"})
            custom_cmds["work"] = json.dumps({"command_type": "economy_work"})
            custom_cmds["beg"] = json.dumps({"command_type": "economy_beg"})
            custom_cmds["shop"] = json.dumps({"command_type": "help_embed", "title": "Premium Shop", "description": "Spend your coins on exclusive items.", "fields": [{"name": "!shop", "value": "Browse available items.", "inline": False}, {"name": "!buy <item>", "value": "Purchase an item.", "inline": False}]})
            custom_cmds["help economy"] = json.dumps({"command_type": "help_embed", "title": "Economy System Help", "description": "Manage your coins and trade with others.", "fields": [{"name": "!daily", "value": "Claim your daily coin reward.", "inline": False}, {"name": "!balance", "value": "Check your coin balance.", "inline": False}, {"name": "!work", "value": "Work to earn coins.", "inline": False}, {"name": "!beg", "value": "Beg for coins.", "inline": False}, {"name": "!shop", "value": "View the shop.", "inline": False}]})
            custom_cmds["challenge"] = json.dumps({"command_type": "help_embed", "title": "Daily Challenge", "description": "Complete daily challenges to earn bonus coins!", "fields": [{"name": "!challenge", "value": "View today's challenge progress.", "inline": False}]})
            custom_cmds["achievements"] = json.dumps({"command_type": "help_embed", "title": "Economy Achievements", "description": "Earn achievements for economy activities!", "fields": [{"name": "!achievements", "value": "View your achievements.", "inline": False}]})
            custom_cmds["help"] = json.dumps({"command_type": "help_all"})
            
        elif system_name == "tickets":
            custom_cmds["ticket"] = json.dumps({"command_type": "ticket_create"})
            custom_cmds["tickets"] = json.dumps({"command_type": "ticket_list"})
            custom_cmds["close"] = json.dumps({"command_type": "ticket_close"})
            custom_cmds["help tickets"] = json.dumps({"command_type": "help_embed", "title": "Support Ticket System", "description": "Manage support tickets.", "fields": [{"name": "!ticket", "value": "Create a new support ticket.", "inline": False}, {"name": "!tickets", "value": "List your open tickets.", "inline": False}, {"name": "!close", "value": "Close the current ticket.", "inline": False}]})
            
        elif system_name == "appeals":
            custom_cmds["appeal"] = json.dumps({"command_type": "appeal_create"})
            custom_cmds["help appeals"] = json.dumps({"command_type": "help_embed", "title": "Appeals System", "description": "Appeal moderation actions.", "fields": [{"name": "!appeal", "value": "Start an appeal.", "inline": False}]})
            
        elif system_name == "applications":
            custom_cmds["apply"] = json.dumps({"command_type": "application_status"})
            custom_cmds["help staffapply"] = json.dumps({"command_type": "help_embed", "title": "Staff Application System", "description": "Apply for staff positions.", "fields": [{"name": "!apply", "value": "Check your application status.", "inline": False}]})
            
        elif system_name == "verification":
            custom_cmds["triggers"] = json.dumps({"command_type": "list_triggers"})
            
        elif system_name == "moderation":
            custom_cmds["modstats"] = json.dumps({"command_type": "modstats"})
            custom_cmds["appeal"] = json.dumps({"command_type": "appeal_create"})
            
        elif system_name == "welcome":
            custom_cmds["welcome"] = json.dumps({"command_type": "welcome_config"})

        elif system_name == "leveling":
            custom_cmds["rank"] = json.dumps({"command_type": "leveling_rank"})
            custom_cmds["leaderboard"] = json.dumps({"command_type": "leveling_leaderboard"})

        # New systems added for config panels
        elif system_name == "antiraid":
            custom_cmds["help antiraid"] = json.dumps({"command_type": "help_embed", "title": "Anti-Raid System", "description": "Protects against mass joins.", "fields": [{"name": "!lockdown", "value": "Manually lock the server.", "inline": False}]})
        elif system_name == "guardian":
            custom_cmds["help guardian"] = json.dumps({"command_type": "help_embed", "title": "Guardian System", "description": "Advanced anti-abuse protection.", "fields": [{"name": "!guardian stats", "value": "View protection statistics.", "inline": False}]})
        elif system_name == "suggestion":
            custom_cmds["suggest"] = json.dumps({"command_type": "suggestion_create"})
            custom_cmds["help suggestions"] = json.dumps({"command_type": "help_embed", "title": "Suggestions System", "description": "Manage server suggestions.", "fields": [{"name": "!suggest <text>", "value": "Submit a new suggestion.", "inline": False}]})
        elif system_name == "reminder":
            custom_cmds["remind"] = json.dumps({"command_type": "reminder_create"})
            custom_cmds["help reminders"] = json.dumps({"command_type": "help_embed", "title": "Reminders System", "description": "Set personal and scheduled reminders.", "fields": [{"name": "!remind <time> <text>", "value": "Set a reminder.", "inline": False}]})
        elif system_name == "giveaway":
            custom_cmds["giveaway"] = json.dumps({"command_type": "giveaway_create"})
            custom_cmds["help giveaways"] = json.dumps({"command_type": "help_embed", "title": "Giveaways System", "description": "Host and manage giveaways.", "fields": [{"name": "!giveaway create", "value": "Start a new giveaway.", "inline": False}]})
        elif system_name == "warning":
            custom_cmds["warn"] = json.dumps({"command_type": "moderation_warn"})
            custom_cmds["warnings"] = json.dumps({"command_type": "moderation_warnings"})
            custom_cmds["help warnings"] = json.dumps({"command_type": "help_embed", "title": "Warning System", "description": "Manage user warnings.", "fields": [{"name": "!warn <user> <reason>", "value": "Issue a warning.", "inline": False}, {"name": "!warnings <user>", "value": "View user warnings.", "inline": False}]})

        dm.update_guild_data(guild_id, "custom_commands", custom_cmds)

    async def _run_selected_setup(self, guild_id: int, user: discord.Member, selected_systems: List[str]):
        """Run setup for selected systems with progress updates and final summary."""
        # Retrieve guild using guild_id to handle cases where interaction.guild is None (e.g., from DM)
        guild = self.bot.get_guild(guild_id)
        if not guild:
            # Send error message to user if guild not found
            try:
                await user.send("❌ Guild not found. The setup cannot proceed.")
            except discord.Forbidden:
                pass  # Can't DM user
            return

        setup = self._pending_setups.get(guild.id)
        if not setup:
            return

        # Analyze server structure first
        analysis = await self._analyze_server(guild)
        logger.info(f"Server analysis complete for {guild.name}: {len(analysis.existing_channels)} channels, {len(analysis.existing_roles)} roles")

        results = []
        system_map = {
            "verification": ("Verification System", self._setup_verification_system),
            "tickets": ("Ticket System", self._setup_ticket_system),
            "applications": ("Applications System", self._setup_applications_system),
            "appeals": ("Appeals System", self._setup_appeals_system),
            "economy": ("Economy System", self._setup_economy_system),
            "leveling": ("Leveling System", self._setup_leveling_system),
            "welcome": ("Welcome System", self._setup_welcome_system),
            "moderation": ("Moderation System", self._setup_moderation_system),
        }

        for system in selected_systems:
            if system in system_map:
                name, func = system_map[system]
                try:
                    logger.info(f"Setting up {name} for {guild.name}")
                    result = await func(guild, analysis)
                    if result:
                        self._register_system_commands(guild.id, system)
                    results.append((name, result, None))
                    setup.steps_completed.append(system)
                    # Send progress update to user
                    embed = discord.Embed(
                        title=":gear: Setup Progress",
                        description=f"✅ **{name}** setup completed!",
                        color=discord.Color.green()
                    )
                    try:
                        await user.send(embed=embed)
                    except discord.Forbidden:
                        pass  # Can't DM user
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"{name} setup failed: {e}")
                    results.append((name, False, str(e)))
            else:
                logger.warning(f"Unknown system: {system}")

        setup.state = SetupState.COMPLETED
        setup.completed_at = time.time()

        # Mark guild as completed setup
        completed = dm.load_json("completed_setups", default={})
        completed[str(guild.id)] = time.time()
        dm.save_json("completed_setups", completed)

        await self._send_setup_results(guild, user, results)

    async def _send_setup_results(self, guild: discord.Guild, user: discord.Member, results: List[Tuple[str, bool, Optional[str]]]):
        """Send final setup summary to user."""
        embed = discord.Embed(
            title=":white_check_mark: Auto-Setup Complete",
            description=f"Setup completed for **{guild.name}**!",
            color=discord.Color.green()
        )

        success_count = sum(1 for _, success, _ in results if success)
        total_count = len(results)
        embed.add_field(
            name="Summary",
            value=f"Successfully set up {success_count}/{total_count} systems.",
            inline=False
        )

        for name, success, error in results:
            status = "✅" if success else "❌"
            value = f"{status} {name}"
            if error:
                value += f" - Error: {error}"
            embed.add_field(name="", value=value, inline=False)

        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            # Try to send in a channel
            system_channel = guild.system_channel
            if system_channel:
                await system_channel.send(f"{user.mention}", embed=embed)


    async def _setup_welcome_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        """Set up welcome system with channel and message."""
        try:
            # Check if welcome channel already exists
            welcome_channel = analysis.existing_channels.get("welcome")
            if not welcome_channel:
                # Find or create category
                category = analysis.existing_categories.get("welcome") or analysis.existing_categories.get("general")
                if not category:
                    try:
                        category = await guild.create_category("Welcome")
                    except discord.Forbidden:
                        category = None

                welcome_channel = await guild.create_text_channel("welcome", category=category)

            # Send welcome message if channel is empty or doesn't have our message
            if welcome_channel.permissions_for(guild.me).send_messages:
                embed = discord.Embed(
                    title=":wave: Welcome to the Server!",
                    description="We're glad you joined! Please take a moment to read the rules and verify yourself to access all channels.",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name=":white_check_mark: Get Started",
                    value="1. Read the #rules channel\n2. Click the verification button\n3. Enjoy the server!",
                    inline=False
                )
                await welcome_channel.send(embed=embed)

            dm.update_guild_data(guild.id, "welcome_channel", welcome_channel.id)
            return True
        except Exception as e:
            logger.error(f"Failed to setup welcome system: {e}")
            return False

    async def _setup_verification_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        """Set up fully functional verification system."""
        try:
            # Check if verification system already exists
            if analysis.existing_channels.get("verify") and analysis.existing_roles.get("verified"):
                logger.info("Verification system already exists, skipping setup")
                return True

            # Create or get roles
            verified_role = analysis.existing_roles.get("verified")
            if not verified_role:
                verified_role = await guild.create_role(
                    name="Verified",
                    color=discord.Color.green(),
                    permissions=discord.Permissions(
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        attach_files=True,
                        embed_links=True,
                        add_reactions=True,
                        use_application_commands=True,
                        connect=True,
                        speak=True,
                    ),
                    hoist=True,
                    reason="Verification system setup"
                )

            unverified_role = analysis.existing_roles.get("unverified")
            if not unverified_role:
                unverified_role = await guild.create_role(
                    name="Unverified",
                    color=discord.Color.greyple(),
                    permissions=discord.Permissions(view_channel=True, send_messages=True, read_message_history=True),
                    hoist=False,
                    reason="Verification system setup"
                )

            # Create verify channel
            verify_channel = analysis.existing_channels.get("verify")
            if not verify_channel:
                # Find a public category
                public_category = None
                for category in analysis.existing_categories.values():
                    if category not in [ch for ch in analysis.private_channels if isinstance(ch, discord.CategoryChannel)]:
                        public_category = category
                        break

                verify_channel = await guild.create_text_channel("verify", category=public_category)

            # Send verification message with button
            embed = discord.Embed(
                title=":white_check_mark: Server Verification",
                description="Click the button below to verify yourself and gain access to the rest of the server!",
                color=discord.Color.green()
            )
            embed.add_field(
                name=":shield: What happens when I verify?",
                value="You'll receive the Verified role and gain access to all public channels.",
                inline=False
            )

            view = VerifyButton(guild.id, verified_role.id)
            await verify_channel.send(embed=embed, view=view)

            # Store configuration
            dm.update_guild_data(guild.id, "verify_channel", verify_channel.id)
            dm.update_guild_data(guild.id, "verify_role", verified_role.id)
            dm.update_guild_data(guild.id, "unverified_role", unverified_role.id)

            return True
        except Exception as e:
            logger.error(f"Failed to setup verification system: {e}")
            return False



    async def _setup_ticket_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        """Set up fully functional ticket system."""
        try:
            # Check if ticket system already exists
            if analysis.existing_channels.get("ticket-queue") or analysis.existing_channels.get("tickets"):
                logger.info("Ticket system already exists, skipping setup")
                return True

            # Create or get support role
            support_role = analysis.existing_roles.get("support") or analysis.existing_roles.get("staff") or analysis.existing_roles.get("moderator")
            if not support_role:
                support_role = await guild.create_role(
                    name="Support",
                    color=discord.Color.blue(),
                    permissions=discord.Permissions(
                        view_channel=True,
                        send_messages=True,
                        manage_messages=True,
                        read_message_history=True,
                        attach_files=True,
                        embed_links=True,
                        add_reactions=True,
                        use_application_commands=True,
                        connect=True,
                        speak=True,
                    ),
                    hoist=True,
                    reason="Ticket system setup"
                )

            # Create tickets category
            tickets_category = analysis.existing_categories.get("support") or analysis.existing_categories.get("tickets")
            if not tickets_category:
                tickets_category = await guild.create_category("Support")

            # Create tickets channel
            tickets_channel = await guild.create_text_channel(
                "ticket-queue",
                category=tickets_category,
                topic="Create a ticket for support"
            )

            # Send ticket creation message
            embed = discord.Embed(
                title=":ticket: Support Tickets",
                description="Need help? Click the button below to create a private ticket with our support team!",
                color=discord.Color.blue()
            )
            embed.add_field(
                name=":question: How it works",
                value="• Click **Create Ticket** to open a private channel\n• Our support team will respond shortly\n• Use the **Close** button when you're done",
                inline=False
            )

            view = CreateTicketButton(guild.id, tickets_channel.id)
            await tickets_channel.send(embed=embed, view=view)

            # Configure ticket system
            tickets_config = {
                "enabled": True,
                "categories": ["General", "Support", "Billing", "Other"],
                "support_role": support_role.id,
                "category": tickets_category.id
            }
            dm.update_guild_data(guild.id, "tickets_config", tickets_config)
            dm.update_guild_data(guild.id, "tickets_channel", tickets_channel.id)

            return True
        except Exception as e:
            logger.error(f"Failed to setup ticket system: {e}")
            return False

    async def _setup_applications_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        """Set up fully functional staff applications system."""
        try:
            # Check if applications system already exists
            if analysis.existing_channels.get("applications") or analysis.existing_channels.get("staff-applications"):
                logger.info("Applications system already exists, skipping setup")
                return True

            # Create staff category
            staff_category = analysis.existing_categories.get("staff") or analysis.existing_categories.get("applications")
            if not staff_category:
                staff_category = await guild.create_category("Staff")

            # Create applications channel (public)
            applications_channel = await guild.create_text_channel(
                "applications",
                category=staff_category,
                topic="Apply to join the staff team"
            )

            # Create application logs channel (staff only)
            application_logs = await guild.create_text_channel(
                "application-logs",
                category=staff_category,
                topic="Staff application submissions"
            )

            # Set permissions for logs channel (staff only)
            staff_roles = [analysis.existing_roles.get("staff"), analysis.existing_roles.get("moderator"), analysis.existing_roles.get("admin")]
            staff_roles = [role for role in staff_roles if role]

            if staff_roles:
                # Deny @everyone view
                await application_logs.set_permissions(guild.default_role, view_channel=False)
                # Allow staff roles
                for role in staff_roles:
                    await application_logs.set_permissions(role, view_channel=True, send_messages=True)

            # Send application message
            embed = discord.Embed(
                title=":memo: Staff Applications",
                description="Interested in joining our staff team? Click the button below to submit your application!",
                color=discord.Color.purple()
            )
            embed.add_field(
                name=":pencil: Application Process",
                value="• Click **Apply Now** to open the application form\n• Fill out all required fields\n• Staff will review your application\n• You'll be notified of the decision",
                inline=False
            )

            view = ApplyStaffButton(guild.id)
            await applications_channel.send(embed=embed, view=view)

            # Store configuration
            dm.update_guild_data(guild.id, "applications_channel", applications_channel.id)
            dm.update_guild_data(guild.id, "application_logs_channel", application_logs.id)

            return True
        except Exception as e:
            logger.error(f"Failed to setup applications system: {e}")
            return False

    async def _setup_appeals_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        """Set up fully functional appeals system."""
        try:
            # Check if appeals system already exists
            if analysis.existing_channels.get("appeals"):
                logger.info("Appeals system already exists, skipping setup")
                return True

            # Create appeals category
            appeals_category = analysis.existing_categories.get("appeals") or analysis.existing_categories.get("moderation")
            if not appeals_category:
                appeals_category = await guild.create_category("Appeals")

            # Create appeals channel (public)
            appeals_channel = await guild.create_text_channel(
                "appeals",
                category=appeals_category,
                topic="Submit ban/unmute appeals"
            )

            # Create appeal logs channel (staff only)
            appeal_logs = await guild.create_text_channel(
                "appeal-logs",
                category=appeals_category,
                topic="Staff appeal reviews"
            )

            # Set permissions for logs channel (staff only)
            staff_roles = [analysis.existing_roles.get("staff"), analysis.existing_roles.get("moderator"), analysis.existing_roles.get("admin")]
            staff_roles = [role for role in staff_roles if role]

            if staff_roles:
                await appeal_logs.set_permissions(guild.default_role, view_channel=False)
                for role in staff_roles:
                    await appeal_logs.set_permissions(role, view_channel=True, send_messages=True)

            # Send appeals message
            embed = discord.Embed(
                title=":scales: Ban/Mute Appeals",
                description="If you've been banned or muted and believe it was unjust, you can appeal the decision here.",
                color=discord.Color.orange()
            )
            embed.add_field(
                name=":warning: Important Notes",
                value="• Appeals are reviewed by staff members\n• Provide clear reasoning and evidence\n• False appeals may result in further action\n• Be respectful and patient",
                inline=False
            )

            # Create appeal modal button (reuse staff application modal for now)
            view = AppealButton(guild.id)
            await appeals_channel.send(embed=embed, view=view)

            # Store configuration
            dm.update_guild_data(guild.id, "appeals_channel", appeals_channel.id)
            dm.update_guild_data(guild.id, "appeal_logs_channel", appeal_logs.id)

            return True
        except Exception as e:
            logger.error(f"Failed to setup appeals system: {e}")
            return False



    async def _setup_economy_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        """Set up economy system with commands channel."""
        try:
            # Create economy commands channel
            economy_channel = analysis.existing_channels.get("economy-commands")
            if not economy_channel:
                general_category = analysis.existing_categories.get("general") or analysis.existing_categories.get("commands")
                if not general_category:
                    general_category = await guild.create_category("Commands")

                economy_channel = await guild.create_text_channel("economy-commands", category=general_category)

            # Send economy help embed
            embed = discord.Embed(
                title="💰 Economy System",
                description="Welcome to our server economy! Use these commands to earn and spend virtual currency.",
                color=discord.Color.gold()
            )
            embed.add_field(
                name=":moneybag: Earning Money",
                value="• `!daily` - Claim daily reward (100 coins)\n• `!work` - Work for coins\n• `!beg` - Beg for coins",
                inline=True
            )
            embed.add_field(
                name=":bank: Managing Money",
                value="• `!balance` - Check your balance\n• `!pay <user> <amount>` - Send coins\n• `!shop` - Browse items",
                inline=True
            )
            embed.add_field(
                name=":trophy: Leaderboards",
                value="• `!leaderboard` - Top earners\n• `!rank` - Your rank",
                inline=True
            )

            await economy_channel.send(embed=embed)

            # Enable economy system
            economy_config = {
                "enabled": True,
                "daily_reward": 100,
                "work_min": 50,
                "work_max": 200,
                "beg_min": 10,
                "beg_max": 50,
                "currency_name": "coins",
                "currency_symbol": "💰"
            }
            dm.update_guild_data(guild.id, "economy_config", economy_config)

            return True
        except Exception as e:
            logger.error(f"Failed to setup economy system: {e}")
            return False

    async def _setup_leveling_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        """Set up leveling system with commands channel."""
        try:
            # Create leveling commands channel
            leveling_channel = analysis.existing_channels.get("leveling-commands")
            if not leveling_channel:
                general_category = analysis.existing_categories.get("general") or analysis.existing_categories.get("commands")
                if not general_category:
                    general_category = await guild.create_category("Commands")

                leveling_channel = await guild.create_text_channel("leveling-commands", category=general_category)

            # Send leveling help embed
            embed = discord.Embed(
                title="⬆️ Leveling System",
                description="Chat and participate to level up and earn special roles!",
                color=discord.Color.blue()
            )
            embed.add_field(
                name=":speech_balloon: How to Level Up",
                value="• Send messages (1 XP each)\n• Spend time in voice channels\n• Level up automatically!",
                inline=True
            )
            embed.add_field(
                name=":trophy: Level Rewards",
                value="• Level 5: Newcomer role\n• Level 10: Regular role\n• Level 25: Veteran role\n• Level 50: Elite role\n• Level 100: Legend role",
                inline=True
            )
            embed.add_field(
                name=":bar_chart: Check Progress",
                value="• `!rank` - Your current level\n• `!leaderboard` - Top levels\n• `!level` - Detailed stats",
                inline=True
            )

            await leveling_channel.send(embed=embed)

            # Enable leveling system with roles
            leveling_config = {
                "enabled": True,
                "xp_per_message": 1,
                "xp_per_voice_minute": 0.5,
                "level_roles": {
                    "5": "Newcomer",
                    "10": "Regular",
                    "25": "Veteran",
                    "50": "Elite",
                    "100": "Legend"
                }
            }

            # Create level roles if they don't exist
            for level, role_name in leveling_config["level_roles"].items():
                if not analysis.existing_roles.get(role_name.lower()):
                    try:
                        await guild.create_role(
                            name=role_name,
                            color=discord.Color.blue(),
                            hoist=True,
                            reason="Leveling system setup"
                        )
                    except discord.Forbidden:
                        logger.warning(f"Could not create {role_name} role")

            dm.update_guild_data(guild.id, "leveling_config", leveling_config)

            return True
        except Exception as e:
            logger.error(f"Failed to setup leveling system: {e}")
            return False

    async def _setup_moderation_system(self, guild: discord.Guild, analysis: ServerAnalysis) -> bool:
        """Set up moderation logging system."""
        try:
            # Create moderation category
            mod_category = analysis.existing_categories.get("moderation") or analysis.existing_categories.get("logs")
            if not mod_category:
                mod_category = await guild.create_category("Moderation")

            # Create mod logs channel
            mod_logs = analysis.existing_channels.get("mod-logs") or analysis.existing_channels.get("moderation-logs")
            if not mod_logs:
                mod_logs = await guild.create_text_channel("mod-logs", category=mod_category)

            # Create moderator role if it doesn't exist
            mod_role = analysis.existing_roles.get("moderator") or analysis.existing_roles.get("mod")
            if not mod_role:
                mod_role = await guild.create_role(
                    name="Moderator",
                    color=discord.Color.red(),
                    permissions=discord.Permissions(
                        view_channel=True,
                        send_messages=True,
                        manage_messages=True,
                        kick_members=True,
                        moderate_members=True,
                        read_message_history=True,
                        attach_files=True,
                        embed_links=True,
                        add_reactions=True,
                        use_application_commands=True,
                        connect=True,
                        speak=True,
                        mute_members=True,
                        move_members=True,
                    ),
                    hoist=True,
                    reason="Moderation system setup"
                )

            # Configure moderation system
            moderation_config = {
                "enabled": True,
                "ai_enabled": True,
                "sensitivity": "medium",
                "auto_moderation": True,
                "mod_role": mod_role.id,
                "logs_channel": mod_logs.id
            }
            dm.update_guild_data(guild.id, "moderation_config", moderation_config)

            return True
        except Exception as e:
            logger.error(f"Failed to setup moderation system: {e}")
            return False

    async def _send_setup_results(self, guild: discord.Guild, owner: discord.Member, results: List[Tuple[str, bool, Optional[str]]]):
        """Send setup results summary."""
        success_count = sum(1 for _, success, _ in results if success)

        embed = discord.Embed(
            title=":white_check_mark: Setup Complete!",
            description=f"Auto-setup for **{guild.name}** finished with **{success_count}/{len(results)}** systems successfully installed.",
            color=discord.Color.green() if success_count == len(results) else discord.Color.orange()
        )

        successful_systems = []
        failed_systems = []

        for name, success, error in results:
            if success:
                successful_systems.append(f"✅ **{name}**")
            else:
                failed_systems.append(f"❌ **{name}**" + (f" - {error}" if error else ""))

        if successful_systems:
            embed.add_field(
                name=":white_check_mark: Successfully Installed",
                value="\n".join(successful_systems),
                inline=False
            )

        if failed_systems:
            embed.add_field(
                name=":x: Failed to Install",
                value="\n".join(failed_systems),
                inline=False
            )

        embed.add_field(
            name=":rocket: Next Steps",
            value="• Check your new channels for setup instructions\n• Use `/bot` to create additional features\n• Type `/help` to see all available commands\n• Configure system settings with `/settings`",
            inline=False
        )

        embed.set_footer(text="Miro Bot • Server Setup Complete")
        embed.timestamp = discord.utils.utcnow()

        # Try to DM owner first
        try:
            await owner.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            # Fallback to log channel or system channel
            log_ch_id = dm.get_guild_data(guild.id, "log_channel")
            fallback_channel = guild.get_channel(log_ch_id) if log_ch_id else guild.system_channel

            if fallback_channel and fallback_channel.permissions_for(guild.me).send_messages:
                try:
                    await fallback_channel.send(content=f"{owner.mention}", embed=embed)
                except Exception as e:
                    logger.error(f"Failed to send setup results: {e}")

    async def on_guild_remove(self, guild: discord.Guild):
        logger.info(f"Bot removed from guild: {guild.name} (ID: {guild.id})")
        
        if guild.id in self._pending_setups:
            del self._pending_setups[guild.id]

    def get_setup_status(self, guild_id: int) -> Optional[ServerSetup]:
        return self._pending_setups.get(guild_id)

    @discord.app_commands.command(name="autosetup", description="Quickly install all pre-built server systems (verification, tickets, applications, etc.)")
    async def autosetup(self, interaction: discord.Interaction):
        """Slash command to install all available systems at once."""
        if not interaction.user.guild_permissions.administrator and interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message("❌ Only administrators can use this command.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        # Select all available systems
        selected_systems = ["verification", "tickets", "applications", "appeals", "economy", "leveling", "welcome", "moderation"]

        # Initialize setup if not already done
        guild = interaction.guild
        if guild.id not in self._pending_setups:
            self._pending_setups[guild.id] = ServerSetup(
                guild_id=guild.id,
                state=SetupState.STARTED,
                started_at=time.time(),
                completed_at=None,
                steps_completed=[],
                config={},
                selected_systems=selected_systems
            )

        # Run setup for all systems
        await self._run_selected_setup(guild.id, interaction.user, selected_systems)

        await interaction.followup.send("✅ Auto-setup completed! All systems have been installed.", ephemeral=True)

async def setup(bot: commands.Bot):
    cog = AutoSetup(bot)
    await bot.add_cog(cog)
    # Registration of persistent views is handled in bot.py setup_hook

    async def setup_hook(self):
        # Register persistent views for setup buttons in channels
        self.bot.add_view(StartSetupPersistentView(self.bot))

    # --- Individual Setup Methods for AI Actions ---
    
    async def setup_verification(self, interaction: discord.Interaction, params: dict) -> bool:
        """Setup verification system with button embed for AI actions."""
        guild = interaction.guild
        analysis = await self._analyze_server(guild)
        return await self._setup_verification_system(guild, analysis)
    
    async def setup_tickets(self, interaction: discord.Interaction, params: dict) -> bool:
        """Setup ticket system with button embed for AI actions."""
        guild = interaction.guild
        category_name = params.get("category", "Support")
        queue_channel_name = params.get("queue_channel", "ticket-queue")
        
        # Create category
        category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            category = await guild.create_category(category_name)
        
        # Create queue channel
        queue_channel = discord.utils.get(guild.text_channels, name=queue_channel_name)
        if not queue_channel:
            queue_channel = await guild.create_text_channel(queue_channel_name, category=category)
        
        # Send embed with persistent button
        embed = discord.Embed(
            title="🎫 Support Tickets",
            description="Need help? Click the button below to create a ticket!",
            color=discord.Color.blue()
        )
        view = CreateTicketButton(guild.id, queue_channel.id)
        await queue_channel.send(embed=embed, view=view)
        
        # Store config
        dm.update_guild_data(guild.id, "ticket_queue_channel", queue_channel.id)
        dm.update_guild_data(guild.id, "ticket_category", category.id)
        
        logger.info(f"Setup ticket system in {guild.name}")
        self._register_system_commands(guild.id, "tickets")
        return True
    
    async def setup_applications(self, interaction: discord.Interaction, params: dict) -> bool:
        """Setup applications system with button embed for AI actions."""
        guild = interaction.guild
        category_name = params.get("category", "Applications")
        channel_name = params.get("channel", "applications")
        
        # Create category
        category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            category = await guild.create_category(category_name)
        
        # Create channel
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if not channel:
            channel = await guild.create_text_channel(channel_name, category=category)
        
        # Send embed with persistent button (opens modal)
        embed = discord.Embed(
            title="📝 Staff Applications",
            description="Want to join our staff team? Click below to apply!",
            color=discord.Color.purple()
        )
        view = ApplyStaffButton(guild.id)
        await channel.send(embed=embed, view=view)
        
        # Store config
        dm.update_guild_data(guild.id, "applications_channel", channel.id)
        
        logger.info(f"Setup applications system in {guild.name}")
        self._register_system_commands(guild.id, "applications")
        return True
    
    async def setup_appeals(self, interaction: discord.Interaction, params: dict) -> bool:
        """Setup appeals system with button embed for AI actions."""
        guild = interaction.guild
        category_name = params.get("category", "Appeals")
        channel_name = params.get("channel", "appeals")
        
        # Create category
        category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            category = await guild.create_category(category_name)
        
        # Create channel
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if not channel:
            channel = await guild.create_text_channel(channel_name, category=category)
        
        # Send embed with button
        embed = discord.Embed(
            title="⚖️ Ban Appeals",
            description="Want to appeal a ban? Click below to submit an appeal!",
            color=discord.Color.orange()
        )
        # Reuse the application modal for appeals
        view = ApplyStaffButton(guild.id)
        await channel.send(embed=embed, view=view)
        
        # Store config
        dm.update_guild_data(guild.id, "appeals_channel", channel.id)
        
        logger.info(f"Setup appeals system in {guild.name}")
        self._register_system_commands(guild.id, "appeals")
        return True
    
    async def setup_moderation(self, interaction: discord.Interaction, params: dict) -> bool:
        """Setup moderation logging system for AI actions."""
        guild = interaction.guild
        category_name = params.get("category", "Moderation")
        logs_channel_name = params.get("logs_channel", "mod-logs")
        
        # Create category
        category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            category = await guild.create_category(category_name)
        
        # Create logs channel
        logs_channel = discord.utils.get(guild.text_channels, name=logs_channel_name)
        if not logs_channel:
            logs_channel = await guild.create_text_channel(logs_channel_name, category=category)
        
        mod_role = discord.utils.get(guild.roles, name="Moderator")
        if not mod_role:
            mod_role = await guild.create_role(
                name="Moderator",
                color=discord.Color.red(),
                permissions=discord.Permissions(
                    view_channel=True,
                    send_messages=True,
                    manage_messages=True,
                    kick_members=True,
                    moderate_members=True,
                    read_message_history=True,
                    attach_files=True,
                    embed_links=True,
                    add_reactions=True,
                    use_application_commands=True,
                    connect=True,
                    speak=True,
                    mute_members=True,
                    move_members=True,
                ),
                hoist=True
            )
        
        # Store config
        mod_config = {
            "enabled": True,
            "ai_enabled": True,
            "sensitivity": "medium",
            "auto_moderation": True,
            "mod_role": mod_role.id,
            "logs_channel": logs_channel.id
        }
        dm.update_guild_data(guild.id, "moderation_config", mod_config)
        
        logger.info(f"Setup moderation system in {guild.name}")
        return True
    
    async def setup_logging(self, interaction: discord.Interaction, params: dict) -> bool:
        """Setup server logging system for AI actions."""
        guild = interaction.guild
        category_name = params.get("category", "Logs")
        logs_channel_name = params.get("channel", "server-logs")
        
        # Create category
        category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            category = await guild.create_category(category_name)
        
        # Create logs channel
        logs_channel = discord.utils.get(guild.text_channels, name=logs_channel_name)
        if not logs_channel:
            logs_channel = await guild.create_text_channel(logs_channel_name, category=category)
        
        # Store config
        dm.update_guild_data(guild.id, "logging_channel", logs_channel.id)
        dm.update_guild_data(guild.id, "logging_enabled", True)
        
        logger.info(f"Setup logging system in {guild.name}")
        return True

# Removed StartSetupView and related classes to eliminate auto-setup buttons from welcome DM
# The welcome DM now sends a clean embed without interactive components, encouraging users to use /bot and /autosetup commands instead

# class StartSetupView(discord.ui.View):
#     def __init__(self, auto_setup, guild_id):
#         super().__init__(timeout=None)
#         self.auto_setup = auto_setup
#         self.guild_id = guild_id

#         # Create buttons dynamically using stored guild_id in custom_id
#         start_button = discord.ui.Button(
#             label="Start Auto-Setup",
#             style=discord.ButtonStyle.success,
#             custom_id=f"start_auto_setup_{guild_id}",
#             emoji="🚀"
#         )
#         start_button.callback = self.start_setup
#         self.add_item(start_button)

#         skip_button = discord.ui.Button(
#             label="Skip / Use Defaults",
#             style=discord.ButtonStyle.secondary,
#             custom_id=f"skip_auto_setup_{guild_id}",
#             emoji="⏭️"
#         )
#         skip_button.callback = self.skip_setup
#         self.add_item(skip_button)

#     async def start_setup(self, interaction: discord.Interaction):
#         # Use self.guild_id to retrieve the guild
#         guild = interaction.client.get_guild(self.guild_id)

#         if not guild:
#             await interaction.response.send_message("Guild not found. This setup link may be expired.", ephemeral=True)
#             return

#         # Check if user is owner or admin
#         member = guild.get_member(interaction.user.id)
#         if not member or (not member.guild_permissions.administrator and interaction.user.id != guild.owner_id):
#             await interaction.response.send_message("Only server administrators or the owner can start auto-setup.", ephemeral=True)
#             return

#         await interaction.response.defer(ephemeral=True)

#         try:
#             await self.auto_setup._start_interactive_setup(interaction, guild_id=self.guild_id)
#         except Exception as e:
#             logger.error(f"Error starting interactive setup: {e}")
#             await interaction.followup.send("❌ An error occurred while starting setup. Please try again.", ephemeral=True)

#     async def skip_setup(self, interaction: discord.Interaction):
#         # Use self.guild_id to retrieve the guild
#         guild = interaction.client.get_guild(self.guild_id)

#         if not guild:
#             await interaction.response.send_message("Guild not found. This setup link may be expired.", ephemeral=True)
#             return

#         # Check if user is owner or admin
#         member = guild.get_member(interaction.user.id)
#         if not member or (not member.guild_permissions.administrator and interaction.user.id != guild.owner_id):
#             await interaction.response.send_message("Only server administrators or the owner can skip setup.", ephemeral=True)
#             return

#         setup = self.auto_setup._pending_setups.get(guild.id)
#         if setup:
#             setup.state = SetupState.SKIPPED

#         await interaction.response.send_message("✅ Setup skipped! You can use `/bot` anytime to create features manually.", ephemeral=True)


# # Persistent version for setup_hook registration - handles guild-specific custom_ids
# class StartSetupPersistentView(discord.ui.View):
#     def __init__(self, bot):
#         super().__init__(timeout=None)
#         self.bot = bot

#     async def interaction_check(self, interaction: discord.Interaction) -> bool:
#         # Handle start_auto_setup_{guild_id} pattern
#         if interaction.data and interaction.data.get("custom_id", "").startswith("start_auto_setup_"):
#             await self.handle_start_setup(interaction)
#             return False  # Prevent default handling

#         # Handle skip_auto_setup_{guild_id} pattern
#         if interaction.data and interaction.data.get("custom_id", "").startswith("skip_auto_setup_"):
#             await self.handle_skip_setup(interaction)
#             return False  # Prevent default handling

#         return True

#     async def handle_start_setup(self, interaction: discord.Interaction):
#         # Extract guild_id from custom_id
#         custom_id = interaction.data["custom_id"]
#         try:
#             button_guild_id = int(custom_id.split('_')[-1])
#         except (ValueError, IndexError):
#             await interaction.response.send_message("Invalid setup button.", ephemeral=True)
#             return

#         guild = interaction.guild or self.bot.get_guild(button_guild_id)

#         if not guild:
#             await interaction.response.send_message("Guild not found. This setup link may be expired.", ephemeral=True)
#             return

#         # Check if user is owner or admin
#         member = guild.get_member(interaction.user.id)
#         if not member or (not member.guild_permissions.administrator and interaction.user.id != guild.owner_id):
#             await interaction.response.send_message("Only server administrators or the owner can start auto-setup.", ephemeral=True)
#             return

#         await interaction.response.defer(ephemeral=True)

#         try:
#             # Create a temporary auto_setup instance for this interaction
#             from modules.auto_setup import AutoSetup
#             auto_setup = AutoSetup(self.bot)
#             await auto_setup._start_interactive_setup(interaction, guild_id=guild.id)
#         except Exception as e:
#             logger.error(f"Error starting interactive setup: {e}")
#             await interaction.followup.send("❌ An error occurred while starting setup. Please try again.", ephemeral=True)

#     async def handle_skip_setup(self, interaction: discord.Interaction):
#         # Extract guild_id from custom_id
#         custom_id = interaction.data["custom_id"]
#         try:
#             button_guild_id = int(custom_id.split('_')[-1])
#         except (ValueError, IndexError):
#             await interaction.response.send_message("Invalid setup button.", ephemeral=True)
#             return

#         guild = interaction.guild or self.bot.get_guild(button_guild_id)

#         if not guild:
#             await interaction.response.send_message("Guild not found. This setup link may be expired.", ephemeral=True)
#             return

#         # Check if user is owner or admin
#         member = guild.get_member(interaction.user.id)
#         if not member or (not member.guild_permissions.administrator and interaction.user.id != guild.owner_id):
#             await interaction.response.send_message("Only server administrators or the owner can skip setup.", ephemeral=True)
#             return

#         # Create a temporary auto_setup instance for this interaction
#         from modules.auto_setup import AutoSetup
#         auto_setup = AutoSetup(self.bot)
#         setup = auto_setup._pending_setups.get(guild.id)
#         if setup:
#            setup.state = SetupState.SKIPPED

#         await interaction.response.send_message("✅ Setup skipped! You can use `/bot` anytime to create features manually.", ephemeral=True)


# class SystemSelectionView(discord.ui.View):
#     def __init__(self, auto_setup, guild_id):
#         super().__init__(timeout=600)  # 10 minute timeout
#         self.auto_setup = auto_setup
#         self.guild_id = guild_id
#         self.selected_systems = set()

#     @discord.ui.button(label="Verification", style=discord.ButtonStyle.primary, custom_id="select_verification", emoji="✅")
#     async def toggle_verification(self, interaction: discord.Interaction, button: discord.ui.Button):
#         await self._toggle_system(interaction, "verification", button)

#     @discord.ui.button(label="Tickets", style=discord.ButtonStyle.primary, custom_id="select_tickets", emoji="🎫")
#     async def toggle_tickets(self, interaction: discord.Interaction, button: discord.ui.Button):
#         await self._toggle_system(interaction, "tickets", button)

#     @discord.ui.button(label="Applications", style=discord.ButtonStyle.primary, custom_id="select_applications", emoji="📝")
#     async def toggle_applications(self, interaction: discord.Interaction, button: discord.ui.Button):
#         await self._toggle_system(interaction, "applications", button)

#     @discord.ui.button(label="Appeals", style=discord.ButtonStyle.primary, custom_id="select_appeals", emoji="⚖️")
#     async def toggle_appeals(self, interaction: discord.Interaction, button: discord.ui.Button):
#         await self._toggle_system(interaction, "appeals", button)

#     @discord.ui.button(label="Economy", style=discord.ButtonStyle.secondary, custom_id="select_economy", emoji="💰")
#     async def toggle_economy(self, interaction: discord.Interaction, button: discord.ui.Button):
#         await self._toggle_system(interaction, "economy", button)

#     @discord.ui.button(label="Leveling", style=discord.ButtonStyle.secondary, custom_id="select_leveling", emoji="⬆️")
#     async def toggle_leveling(self, interaction: discord.Interaction, button: discord.ui.Button):
#         await self._toggle_system(interaction, "leveling", button)

#     async def _toggle_system(self, interaction: discord.Interaction, system: str, button: discord.ui.Button):
#         if system in self.selected_systems:
#             self.selected_systems.remove(system)
#             button.style = discord.ButtonStyle.secondary
#         else:
#             self.selected_systems.add(system)
#             button.style = discord.ButtonStyle.success

#         await interaction.response.edit_message(view=self)

#     @discord.ui.button(label="Start Setup", style=discord.ButtonStyle.success, custom_id="confirm_setup", emoji="🚀", row=2)
#     async def start_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
#         if not self.selected_systems:
#             await interaction.response.send_message("Please select at least one system to set up!", ephemeral=True)
#             return

#         # Fetch guild to ensure it exists with retry mechanism
#         guild = self.auto_setup.bot.get_guild(self.guild_id)
#         if not guild:
#             # Wait 1 second and try again
#             await asyncio.sleep(1)
#             guild = self.auto_setup.bot.get_guild(self.guild_id)
#             if not guild:
#                 logger.error(f"Guild {self.guild_id} not found after retry in auto-setup")
#                 await interaction.response.send_message("Something went wrong, please re-invite the bot", ephemeral=True)
#                 return

#         setup = self.auto_setup._pending_setups.get(self.guild_id)
#         if setup:
#             setup.selected_systems = list(self.selected_systems)
#             setup.state = SetupState.STARTED

#         await interaction.response.send_message("🚀 Starting setup... This may take a few moments.", ephemeral=True)
#         await self.auto_setup._run_selected_setup(self.guild_id, interaction.user, list(self.selected_systems))

#     @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, custom_id="cancel_setup", emoji="❌", row=2)
#     async def cancel_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
#         setup = self.auto_setup._pending_setups.get(self.guild_id)
#         if setup:
#             setup.state = SetupState.SKIPPED

#         await interaction.response.send_message("✅ Setup cancelled. You can start over anytime!", ephemeral=True)
#         self.stop()

