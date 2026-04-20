import discord
from discord.ext import commands
import asyncio
import json
import time
from typing import Dict, List, Optional
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
class ServerSetup:
    guild_id: int
    state: SetupState
    started_at: float
    completed_at: Optional[float]
    steps_completed: List[str]
    config: dict


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


class AutoSetupView(discord.ui.View):
    """Persistent view for auto-setup buttons in DMs"""
    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id

        # Set dynamic custom_ids for persistent functionality
        self.children[0].custom_id = f"auto_setup_run_{guild_id}"
        self.children[1].custom_id = f"auto_setup_skip_{guild_id}"

    @discord.ui.button(label="Run Full Auto-Setup", style=discord.ButtonStyle.success)
    async def run_setup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Extract guild_id from custom_id
        custom_id_parts = interaction.data['custom_id'].split('_')
        guild_id = int(custom_id_parts[-1])

        guild = self.bot.get_guild(guild_id)
        if not guild:
            await interaction.response.send_message("❌ Guild not found.", ephemeral=True)
            return

        if interaction.user.id != guild.owner.id:
            await interaction.response.send_message("❌ Only the server owner can run setup.", ephemeral=True)
            return

        await interaction.response.send_message("🚀 Running full auto-setup...", ephemeral=True)

        try:
            await self.bot.auto_setup._run_full_auto_setup(guild, interaction.user)
        except Exception as e:
            logger.error(f"Failed to run auto-setup: {e}")
            await interaction.followup.send("❌ An error occurred during setup. Please try again or contact support.", ephemeral=True)

    @discord.ui.button(label="Skip / Use Defaults", style=discord.ButtonStyle.secondary)
    async def skip_setup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Extract guild_id from custom_id
        custom_id_parts = interaction.data['custom_id'].split('_')
        guild_id = int(custom_id_parts[-1])

        guild = self.bot.get_guild(guild_id)
        if not guild:
            await interaction.response.send_message("❌ Guild not found.", ephemeral=True)
            return

        if interaction.user.id != guild.owner.id:
            await interaction.response.send_message("❌ Only the server owner can skip setup.", ephemeral=True)
            return

        self.bot.auto_setup._pending_setups[guild.id].state = SetupState.SKIPPED
        await interaction.response.send_message("✅ Setup skipped. Use `/bot` anytime to create features!", ephemeral=True)


class AutoSetup:
    def __init__(self, bot):
        self.bot = bot
        self._pending_setups: Dict[int, ServerSetup] = {}
        self._setup_messages: Dict[int, int] = {}
        self._startup_guilds = set()

    async def on_guild_join(self, guild: discord.Guild):
        logger.info(f"Bot joined new guild: {guild.name} (ID: {guild.id})")
        
        self._pending_setups[guild.id] = ServerSetup(
            guild_id=guild.id,
            state=SetupState.PENDING,
            started_at=time.time(),
            completed_at=None,
            steps_completed=[],
            config={}
        )
        
        await self._send_welcome_dm(guild)
        
        await self._initialize_server_data(guild)

    async def _send_welcome_dm(self, guild: discord.Guild):
        owner = guild.owner
        
        if not owner:
            return
        
        embed = discord.Embed(
            title="🤖 Welcome to Miro AI!",
            description=f"I've been added to **{guild.name}**!",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="What I Do",
            value="I'm an AI-powered Discord bot that can build features for your server using natural language. Just use `/bot` to tell me what you want!",
            inline=False
        )
        
        embed.add_field(
            name="Quick Start",
            value="• `/bot` - Tell me what to build\n• `/help` - See all commands\n• `/status` - Check system health",
            inline=False
        )
        
        embed.add_field(
            name="Auto-Setup",
            value="Would you like me to automatically set up 12 features for your server?",
            inline=False
        )

        # Configuration Guide Embed
        config_embed = discord.Embed(
            title=":gear: Configuration Guide",
            description="Use these commands to configure me for your server:",
            color=discord.Color.green()
        )
        config_embed.add_field(
            name="/config provider <name>",
            value="Switch AI provider (e.g., openrouter, openai)",
            inline=False
        )
        config_embed.add_field(
            name="/config model <model>",
            value="Set model (depends on provider, e.g., gpt-4)",
            inline=False
        )
        config_embed.add_field(
            name="/config key <provider> <api_key>",
            value="Set API key for the provider",
            inline=False
        )
        config_embed.set_footer(text="Example: /config provider openrouter")

        view = AutoSetupView(self.bot, guild.id)

        try:
            await owner.send(embeds=[embed, config_embed], view=view)
            logger.info(f"Sent welcome DM to {owner} for guild {guild.id}")
        except Exception as e:
            logger.warning(f"Failed to send welcome DM to {owner} (DMs likely closed): {e}. Falling back to server channel.")
            # Fall back to system channel or first available text channel
            fallback_channel = guild.system_channel
            
            if not fallback_channel or not fallback_channel.permissions_for(guild.me).send_messages:
                # Find first text channel where bot can send messages
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).send_messages and channel.permissions_for(guild.me).view_channel:
                        fallback_channel = channel
                        break
            
            if fallback_channel:
                embed.description = f"I've been added to the server! (Sent here because the owner's DMs are closed.)\n\n<@{owner.id}>"
                try:
                    await fallback_channel.send(content=f"Hey <@{owner.id}>, your DMs were closed! Here is your setup menu:", embed=embed, view=view)
                    logger.info(f"Sent welcome fallback to channel {fallback_channel.name} in guild {guild.id}")
                except Exception as ex:
                    logger.error(f"Failed to send fallback welcome to channel {fallback_channel.name}: {ex}")
            else:
                logger.error(f"Could not find any channel to send welcome message in guild {guild.id}")

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

    async def _run_full_auto_setup(self, guild: discord.Guild, owner: discord.Member):
        setup = self._pending_setups.get(guild.id)
        if not setup:
            return
        
        setup.state = SetupState.STARTED
        setup.started_at = time.time()
        
        results = []
        
        features = [
            ("Welcome System", self._setup_welcome_system),
            ("Verification System", self._setup_verification_system),
            ("Rules Channel", self._setup_rules_channel),
            ("Announcements Channel", self._setup_announcements),
            ("Suggestion System", self._setup_suggestions),
            ("Modmail System", self._setup_modmail_system),
            ("Ticket System", self._setup_ticket_system),
            ("Applications Channel", self._setup_applications),
            ("Logging System", self._setup_logging),
            ("Leveling System", self._setup_leveling),
            ("Reaction Roles", self._setup_reaction_roles),
            ("Basic Moderation", self._setup_basic_moderation),
            ("AI Configuration", self._setup_ai_config)
        ]
        
        for name, func in features:
            try:
                result = await func(guild)
                results.append((name, result))
                setup.steps_completed.append(name.lower().replace(" ", "_"))
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"{name} setup failed: {e}")
                results.append((name, False))
        
        setup.state = SetupState.COMPLETED
        setup.completed_at = time.time()
        
        # Mark guild as completed setup
        completed = dm.load_json("completed_setups", default={})
        completed[str(guild.id)] = time.time()
        dm.save_json("completed_setups", completed)
        
        await self._send_setup_results(guild, owner, results)

    async def _setup_welcome_system(self, guild: discord.Guild) -> bool:
        category = discord.utils.get(guild.categories, name="Welcome")
        if not category:
            try:
                category = await guild.create_category("Welcome")
            except:
                category = None
        
        welcome_channel = discord.utils.get(guild.text_channels, name="welcome")
        if not welcome_channel:
            welcome_channel = await guild.create_text_channel("welcome", category=category)
            await welcome_channel.send("👋 Welcome! Read the rules in #rules to access the rest of the server.")
        
        dm.update_guild_data(guild.id, "welcome_channel", welcome_channel.id)
        
        return True

    async def _setup_verification_system(self, guild: discord.Guild) -> bool:
        # Use the specialized verification module for consistency
        try:
            from modules.verification import Verification
            v_system = Verification(self.bot)
            unverified_role, verified_role = await v_system.setup(guild)

            dm.update_guild_data(guild.id, "verify_role", verified_role.id)
            dm.update_guild_data(guild.id, "unverified_role", unverified_role.id)

            # Find the #verify channel created by Verification.setup
            verify_channel = discord.utils.get(guild.text_channels, name="verify")
            if verify_channel:
                dm.update_guild_data(guild.id, "verify_channel", verify_channel.id)

            return True
        except Exception as e:
            logger.error(f"Failed to setup verification via modules.verification: {e}")
            return False

    async def _setup_rules_channel(self, guild: discord.Guild) -> bool:
        category = discord.utils.get(guild.categories, name="Rules")
        if not category:
            try:
                category = await guild.create_category("Rules")
            except Exception as e:
                logger.error(f"Failed to create Rules category: {e}")
                category = None
        
        rules_channel = discord.utils.get(guild.text_channels, name="rules")
        if not rules_channel:
            rules_channel = await guild.create_text_channel("rules", category=category)
        
        # Get the verified role ID for the button
        verify_role = discord.utils.get(guild.roles, name="Verified")
        role_id = verify_role.id if verify_role else None
        
        rules_embed = discord.Embed(
            title="📜 Server Rules",
            description="Please read and accept our rules to access the server.",
            color=discord.Color.blue()
        )
        rules_embed.add_field(
            name="1. Be Respectful",
            value="Treat all members with respect. No harassment, hate speech, or toxicity.",
            inline=False
        )
        rules_embed.add_field(
            name="2. No Spam",
            value="Don't spam messages, reactions, or commands.",
            inline=False
        )
        rules_embed.add_field(
            name="3. Follow Discord TOS",
            value="All activity must follow Discord's Terms of Service.",
            inline=False
        )
        rules_embed.add_field(
            name="4. No NSFW",
            value="Keep all content appropriate for all ages.",
            inline=False
        )
        rules_embed.add_field(
            name="5. Listen to Staff",
            value="Follow instructions from moderators and admins.",
            inline=False
        )
        
        # Use persistent view for reliable button functionality
        view = AcceptRulesButton(guild.id, role_id)
        
        try:
            await rules_channel.send(embed=rules_embed, view=view)
        except Exception as e:
            logger.error(f"Failed to send rules message: {e}")
        
        dm.update_guild_data(guild.id, "rules_channel", rules_channel.id)
        
        return True

    async def _setup_announcements(self, guild: discord.Guild) -> bool:
        category = discord.utils.get(guild.categories, name="Information")
        if not category:
            try:
                category = await guild.create_category("Information")
            except:
                category = None
        
        announcements_channel = discord.utils.get(guild.text_channels, name="announcements")
        if not announcements_channel:
            announcements_channel = await guild.create_text_channel(
                "announcements",
                category=category,
                topic="Server news and updates"
            )
        
        await announcements_channel.send("📢 Announcements will be posted here!")
        
        dm.update_guild_data(guild.id, "announcements_channel", announcements_channel.id)
        
        return True

    async def _setup_suggestions(self, guild: discord.Guild) -> bool:
        category = discord.utils.get(guild.categories, name="Feedback")
        if not category:
            try:
                category = await guild.create_category("Feedback")
            except Exception as e:
                logger.error(f"Failed to create Feedback category: {e}")
                category = None
        
        suggestions_channel = discord.utils.get(guild.text_channels, name="suggestions")
        if not suggestions_channel:
            suggestions_channel = await guild.create_text_channel(
                "suggestions",
                category=category,
                topic="Submit your ideas for the server!"
            )
        
        embed = discord.Embed(
            title="💡 Suggestion Box",
            description="Have an idea to improve the server? Submit it here!",
            color=discord.Color.green()
        )
        embed.add_field(
            name="How to Submit",
            value="Click the button below to submit a suggestion",
            inline=False
        )
        embed.add_field(
            name="Voting",
            value="React with ✅ to support or ❌ to oppose",
            inline=False
        )
        
        # Use persistent view for reliable button functionality
        view = SuggestionButton(guild.id)
        
        try:
            await suggestions_channel.send(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Failed to send suggestion message: {e}")
        
        dm.update_guild_data(guild.id, "suggestions_channel", suggestions_channel.id)
        
        suggestion_config = {
            "enabled": True,
            "require_approval": True,
            "anonymous": False,
            "upvote_emoji": "✅",
            "downvote_emoji": "❌"
        }
        dm.update_guild_data(guild.id, "suggestion_config", suggestion_config)
        
        return True

    async def _setup_modmail_system(self, guild: discord.Guild) -> bool:
        category = discord.utils.get(guild.categories, name="Support")
        if not category:
            try:
                category = await guild.create_category("Support")
            except:
                category = None
        
        modmail_channel = discord.utils.get(guild.text_channels, name="modmail")
        if not modmail_channel:
            modmail_channel = await guild.create_text_channel(
                "modmail",
                category=category,
                topic="Staff will see member DMs here"
            )
        
        embed = discord.Embed(
            title="📩 Modmail System",
            description="Members can DM the bot and messages will be forwarded here.",
            color=discord.Color.blurple()
        )
        embed.add_field(
            name="How to Use",
            value="Members DM the bot → Message appears here → Staff replies → Message sent to member",
            inline=False
        )
        
        await modmail_channel.send(embed=embed)
        
        modmail_config = {
            "enabled": True,
            "auto_close_days": 7,
            "notify_channel": modmail_channel.id
        }
        dm.update_guild_data(guild.id, "modmail_config", modmail_config)
        dm.update_guild_data(guild.id, "modmail_channel", modmail_channel.id)
        
        return True

    async def _setup_ticket_system(self, guild: discord.Guild) -> bool:
        category = discord.utils.get(guild.categories, name="Support")
        
        support_role = discord.utils.get(guild.roles, name="Support")
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
                hoist=True
            )
        
        tickets_channel = discord.utils.get(guild.text_channels, name="ticket-queue")
        if not tickets_channel:
            tickets_channel = await guild.create_text_channel(
                "ticket-queue",
                category=category,
                topic="Create a ticket for support"
            )
        
        embed = discord.Embed(
            title="🎫 Support Tickets",
            description="Need help? Create a ticket!",
            color=discord.Color.blue()
        )
        
        # Use persistent view for reliable button functionality
        view = CreateTicketButton(guild.id, tickets_channel.id)
        
        try:
            await tickets_channel.send(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Failed to send ticket message: {e}")
        
        tickets_config = {
            "enabled": True,
            "categories": ["General", "Support", "Billing", "Other"],
            "support_role": support_role.id
        }
        dm.update_guild_data(guild.id, "tickets_config", tickets_config)
        dm.update_guild_data(guild.id, "tickets_channel", tickets_channel.id)
        
        return True

    async def _setup_applications(self, guild: discord.Guild) -> bool:
        category = discord.utils.get(guild.categories, name="Staff")
        if not category:
            try:
                category = await guild.create_category("Staff")
            except Exception as e:
                logger.error(f"Failed to create Staff category: {e}")
                category = None
        
        applications_channel = discord.utils.get(guild.text_channels, name="applications")
        if not applications_channel:
            applications_channel = await guild.create_text_channel(
                "applications",
                category=category,
                topic="Staff applications"
            )
        
        embed = discord.Embed(
            title="📝 Staff Applications",
            description="Want to join the staff team? Apply below!",
            color=discord.Color.purple()
        )
        embed.add_field(
            name="How to Apply",
            value="Click the button to open an application form",
            inline=False
        )
        
        # Use persistent view for reliable button functionality
        view = ApplyStaffButton(guild.id)
        
        try:
            await applications_channel.send(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Failed to send application message: {e}")
        
        dm.update_guild_data(guild.id, "applications_channel", applications_channel.id)
        
        return True

    async def _setup_logging(self, guild: discord.Guild) -> bool:
        category = discord.utils.get(guild.categories, name="Logs")
        if not category:
            try:
                category = await guild.create_category("Logs")
            except:
                category = None
        
        log_channel = discord.utils.get(guild.text_channels, name="bot-logs")
        if not log_channel:
            log_channel = await guild.create_text_channel("bot-logs", category=category)
            await log_channel.send("📝 I'll log moderator actions here.")
        
        dm.update_guild_data(guild.id, "log_channel", log_channel.id)
        
        return True

    async def _setup_leveling(self, guild: discord.Guild) -> bool:
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
        
        for level, role_name in leveling_config.get("level_roles", {}).items():
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                try:
                    role = await guild.create_role(
                        name=role_name,
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
                        hoist=True
                    )
                except:
                    pass
        
        dm.update_guild_data(guild.id, "leveling_config", leveling_config)
        
        return True

    async def _setup_reaction_roles(self, guild: discord.Guild) -> bool:
        category = discord.utils.get(guild.categories, name="Roles")
        if not category:
            try:
                category = await guild.create_category("Roles")
            except:
                category = None
        
        roles_channel = discord.utils.get(guild.text_channels, name="role-selection")
        if not roles_channel:
            roles_channel = await guild.create_text_channel(
                "role-selection",
                category=category,
                topic="Pick your roles here!"
            )
        
        ping_role = discord.utils.get(guild.roles, name="Ping Updates")
        if not ping_role:
            ping_role = await guild.create_role(name="Ping Updates", color=discord.Color.orange())
        
        gaming_role = discord.utils.get(guild.roles, name="Gaming")
        if not gaming_role:
            gaming_role = await guild.create_role(name="Gaming", color=discord.Color.red())
        
        art_role = discord.utils.get(guild.roles, name="Art")
        if not art_role:
            art_role = await guild.create_role(name="Art", color=discord.Color.magenta())
        
        embed = discord.Embed(
            title="🎭 Role Selection",
            description="Click the buttons below to get roles!",
            color=discord.Color.blue()
        )
        embed.add_field(name="🔔 Ping Updates", value="Get notified for announcements", inline=True)
        embed.add_field(name="🎮 Gaming", value="Gaming updates and events", inline=True)
        embed.add_field(name="🎨 Art", value="Art sharing and feedback", inline=True)
        
        # Create a View with role selection buttons
        view = discord.ui.View(timeout=None)
        
        role_data = [
            ("Ping Updates", "🔔", ping_role.id if ping_role else None),
            ("Gaming", "🎮", gaming_role.id if gaming_role else None),
            ("Art", "🎨", art_role.id if art_role else None)
        ]
        
        for role_name, emoji, role_id in role_data:
            btn = RoleSelectButton(guild.id, role_name, role_id, emoji)
            view.add_item(btn)
        
        try:
            await roles_channel.send(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Failed to send reaction roles message: {e}")
        
        reaction_roles = [
            {"role": ping_role.id, "emoji": "🔔", "name": "Ping Updates"},
            {"role": gaming_role.id, "emoji": "🎮", "name": "Gaming"},
            {"role": art_role.id, "emoji": "🎨", "name": "Art"}
        ]
        dm.update_guild_data(guild.id, "reaction_roles", reaction_roles)
        
        return True

    async def _setup_basic_moderation(self, guild: discord.Guild) -> bool:
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
        
        mod_config = {
            "enabled": True,
            "ai_enabled": True,
            "sensitivity": "medium",
            "auto_moderation": True,
            "mod_role": mod_role.id
        }
        
        dm.update_guild_data(guild.id, "moderation_config", mod_config)
        
        return True

    async def _setup_ai_config(self, guild: discord.Guild) -> bool:
        ai_config = {
            "provider": "openrouter",
            "model": "meta-llama/llama-3.1-70b-instruct",
            "temperature": 0.7,
            "max_tokens": 2000
        }
        
        dm.update_guild_data(guild.id, "ai_config", ai_config)
        
        return True

    async def _send_setup_results(self, guild: discord.Guild, owner: discord.Member, results: List[tuple]):
        success_count = sum(1 for _, success in results if success)
        
        embed = discord.Embed(
            title="✅ Auto-Setup Results",
            description=f"Auto-setup for **{guild.name}** finished with **{success_count}/{len(results)}** components successful.",
            color=discord.Color.green() if success_count == len(results) else discord.Color.orange()
        )
        
        status_lines = []
        for name, success in results:
            status = "✅" if success else "❌"
            status_lines.append(f"{status} **{name}**")

        embed.add_field(name="Summary", value="\n".join(status_lines), inline=False)
        
        embed.add_field(
            name="Next Steps",
            value="• Use `/bot` to create custom features\n• Type `!help` to see available commands\n• Check `#verify` or `#rules` in your server!",
            inline=False
        )
        
        embed.set_footer(text="Miro AI • Infrastructure Buildout")
        embed.timestamp = discord.utils.utcnow()
        
        try:
            await owner.send(embed=embed)
        except:
            # Fallback if DMs are closed
            log_ch_id = dm.get_guild_data(guild.id, "log_channel")
            log_ch = guild.get_channel(log_ch_id) if log_ch_id else guild.system_channel
            if log_ch:
                await log_ch.send(content=f"{owner.mention}", embed=embed)

    async def on_guild_remove(self, guild: discord.Guild):
        logger.info(f"Bot removed from guild: {guild.name} (ID: {guild.id})")
        
        if guild.id in self._pending_setups:
            del self._pending_setups[guild.id]

    def get_setup_status(self, guild_id: int) -> Optional[ServerSetup]:
        return self._pending_setups.get(guild_id)

    # --- Individual Setup Methods for AI Actions ---
    
    async def setup_verification(self, interaction: discord.Interaction, params: dict) -> bool:
        """Setup verification system with button embed for AI actions."""
        guild = interaction.guild
        return await self._setup_verification_system(guild)
    
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