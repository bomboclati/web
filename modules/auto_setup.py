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
            title="🤖 Welcome to Immortal AI!",
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
        
        view = discord.ui.View()
        
        setup_btn = discord.ui.Button(
            label="Run Full Auto-Setup",
            style=discord.ButtonStyle.success,
            custom_id="auto_setup_run"
        )
        
        skip_btn = discord.ui.Button(
            label="Skip",
            style=discord.ButtonStyle.secondary,
            custom_id="auto_setup_skip"
        )
        
        async def setup_callback(interaction: discord.Interaction):
            if interaction.user.id != owner.id:
                await interaction.response.send_message("Only the server owner can run setup.", ephemeral=True)
                return
            
            await interaction.response.send_message("🚀 Running full auto-setup...", ephemeral=True)
            await self._run_full_auto_setup(guild, interaction.user)
        
        async def skip_callback(interaction: discord.Interaction):
            if interaction.user.id != owner.id:
                await interaction.response.send_message("Only the server owner can skip.", ephemeral=True)
                return
            
            self._pending_setups[guild.id].state = SetupState.SKIPPED
            await interaction.response.send_message("✅ Setup skipped. Use `/bot` anytime to create features!", ephemeral=True)
        
        setup_btn.callback = setup_callback
        skip_btn.callback = skip_callback
        
        view.add_item(setup_btn)
        view.add_item(skip_btn)
        
        try:
            await owner.send(embed=embed, view=view)
            logger.info(f"Sent welcome DM to {owner} for guild {guild.id}")
        except Exception as e:
            logger.error(f"Failed to send welcome DM: {e}")
            try:
                system_channel = guild.system_channel
                if system_channel:
                    await system_channel.send(embed=embed, view=view)
            except:
                pass

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
        category = discord.utils.get(guild.categories, name="Welcome")
        
        verify_role = discord.utils.get(guild.roles, name="Verified")
        if not verify_role:
            verify_role = await guild.create_role(
                name="Verified",
                color=discord.Color.green(),
                hoist=True
            )
        
        verify_channel = discord.utils.get(guild.text_channels, name="verify")
        if not verify_channel:
            verify_channel = await guild.create_text_channel("verify", category=category)
        
        embed = discord.Embed(
            title="✅ Verification",
            description="Click the button below to verify yourself and gain access to the server!",
            color=discord.Color.green()
        )
        
        view = discord.ui.View()
        
        verify_btn = discord.ui.Button(
            label="Verify Me",
            style=discord.ButtonStyle.success,
            custom_id="verify_button"
        )
        
        async def verify_callback(interaction: discord.Interaction):
            role = discord.utils.get(guild.roles, name="Verified")
            if role:
                await interaction.user.add_roles(role)
                await interaction.response.send_message("✅ You're verified! Enjoy the server!", ephemeral=True)
        
        verify_btn.callback = verify_callback
        view.add_item(verify_btn)
        
        try:
            await verify_channel.send(embed=embed, view=view)
        except:
            pass
        
        dm.update_guild_data(guild.id, "verify_channel", verify_channel.id)
        dm.update_guild_data(guild.id, "verify_role", verify_role.id)
        
        return True

    async def _setup_rules_channel(self, guild: discord.Guild) -> bool:
        category = discord.utils.get(guild.categories, name="Rules")
        if not category:
            try:
                category = await guild.create_category("Rules")
            except:
                category = None
        
        rules_channel = discord.utils.get(guild.text_channels, name="rules")
        if not rules_channel:
            rules_channel = await guild.create_text_channel("rules", category=category)
        
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
        
        view = discord.ui.View()
        
        accept_btn = discord.ui.Button(
            label="I Accept the Rules",
            style=discord.ButtonStyle.primary,
            custom_id="accept_rules"
        )
        
        async def accept_callback(interaction: discord.Interaction):
            role = discord.utils.get(guild.roles, name="Verified")
            if role:
                await interaction.user.add_roles(role)
                await interaction.response.send_message("✅ Thanks for accepting! You now have full access.", ephemeral=True)
            else:
                await interaction.response.send_message("✅ Thanks for accepting!", ephemeral=True)
        
        accept_btn.callback = accept_callback
        view.add_item(accept_btn)
        
        try:
            await rules_channel.send(embed=rules_embed, view=view)
        except:
            pass
        
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
            except:
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
        
        view = discord.ui.View()
        
        submit_btn = discord.ui.Button(
            label="Submit Suggestion",
            style=discord.ButtonStyle.primary,
            custom_id="suggestion_submit_btn"
        )
        
        async def submit_callback(interaction: discord.Interaction):
            await interaction.response.send_message("Use `!suggest` to submit a suggestion!", ephemeral=True)
        
        submit_btn.callback = submit_callback
        view.add_item(submit_btn)
        
        try:
            await suggestions_channel.send(embed=embed, view=view)
        except:
            pass
        
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
        
        view = discord.ui.View()
        
        create_ticket_btn = discord.ui.Button(
            label="Create Ticket",
            style=discord.ButtonStyle.primary,
            custom_id="create_ticket"
        )
        
        async def create_ticket_callback(interaction: discord.Interaction):
            thread = await tickets_channel.create_thread(
                name=f"{interaction.user.name}-ticket",
                inviter=interaction.user
            )
            await thread.send(f"🎫 Ticket created by {interaction.user.mention}")
            await interaction.response.send_message("✅ Ticket created!", ephemeral=True)
        
        create_ticket_btn.callback = create_ticket_callback
        view.add_item(create_ticket_btn)
        
        try:
            await tickets_channel.send(embed=embed, view=view)
        except:
            pass
        
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
            except:
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
        
        view = discord.ui.View()
        
        apply_btn = discord.ui.Button(
            label="Apply Now",
            style=discord.ButtonStyle.primary,
            custom_id="staff_apply"
        )
        
        async def apply_callback(interaction: discord.Interaction):
            modal = discord.ui.Modal(title="Staff Application")
            
            reason_input = discord.ui.TextInput(
                label="Why do you want to be staff?",
                style=discord.TextStyle.paragraph,
                placeholder="Tell us about yourself..."
            )
            experience_input = discord.ui.TextInput(
                label="Experience",
                style=discord.TextStyle.paragraph,
                placeholder="Any previous moderation experience?"
            )
            
            modal.add_item(reason_input)
            modal.add_item(experience_input)
            
            await interaction.response.send_modal(modal)
        
        apply_btn.callback = apply_callback
        view.add_item(apply_btn)
        
        try:
            await applications_channel.send(embed=embed, view=view)
        except:
            pass
        
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
                    role = await guild.create_role(name=role_name)
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
            description="Click the reactions below to get roles!",
            color=discord.Color.blue()
        )
        embed.add_field(name="🔔 Ping Updates", value="Get notified for announcements", inline=True)
        embed.add_field(name="🎮 Gaming", value="Gaming updates and events", inline=True)
        embed.add_field(name="🎨 Art", value="Art sharing and feedback", inline=True)
        
        view = discord.ui.View()
        
        for role_name, emoji in [("Ping Updates", "🔔"), ("Gaming", "🎮"), ("Art", "🎨")]:
            btn = discord.ui.Button(
                label=role_name,
                style=discord.ButtonStyle.secondary,
                custom_id=f"role_{role_name.lower().replace(' ', '_')}"
            )
            
            async def role_callback(interaction: discord.Interaction, role_name=role_name):
                role = discord.utils.get(guild.roles, name=role_name)
                if role:
                    if role in interaction.user.roles:
                        await interaction.user.remove_roles(role)
                        await interaction.response.send_message(f"Removed {role_name} role!", ephemeral=True)
                    else:
                        await interaction.user.add_roles(role)
                        await interaction.response.send_message(f"Added {role_name} role!", ephemeral=True)
            
            btn.callback = role_callback
            view.add_item(btn)
        
        try:
            await roles_channel.send(embed=embed, view=view)
        except:
            pass
        
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
                permissions=discord.Permissions(moderate_members=True),
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
            "model": "meta-llama/llama-3.1-405b-instruct",
            "temperature": 0.7,
            "max_tokens": 2000
        }
        
        dm.update_guild_data(guild.id, "ai_config", ai_config)
        
        return True

    async def _send_setup_results(self, guild: discord.Guild, owner: discord.Member, results: List[tuple]):
        success_count = sum(1 for _, success in results if success)
        
        embed = discord.Embed(
            title="✅ Auto-Setup Complete!",
            description=f"Successfully set up **{success_count}/{len(results)}** features for **{guild.name}**",
            color=discord.Color.green() if success_count == len(results) else discord.Color.orange()
        )
        
        for name, success in results:
            status = "✅" if success else "❌"
            embed.add_field(name=f"{status} {name}", value="Done" if success else "Failed", inline=True)
        
        embed.add_field(
            name="Next Steps",
            value="• Use `/bot` to create more features\n• Check `/help` for all commands\n• Configure with `/config`",
            inline=False
        )
        
        embed.set_footer(text="Immortal AI • Full Auto-Setup")
        
        try:
            await owner.send(embed=embed)
        except:
            system_channel = guild.system_channel
            if system_channel:
                await system_channel.send(embed=embed)

    async def on_guild_remove(self, guild: discord.Guild):
        logger.info(f"Bot removed from guild: {guild.name} (ID: {guild.id})")
        
        if guild.id in self._pending_setups:
            del self._pending_setups[guild.id]

    def get_setup_status(self, guild_id: int) -> Optional[ServerSetup]:
        return self._pending_setups.get(guild_id)