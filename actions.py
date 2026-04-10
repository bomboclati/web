import discord
import json
import asyncio
import time
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional
from data_manager import dm
from logger import logger

class EditCommandModal(discord.ui.Modal):
    def __init__(self, cmd_name: str, user_id: int):
        super().__init__(title=f"Edit: !{cmd_name}")
        self.cmd_name = cmd_name
        self.user_id = user_id
        
        self.code_input = discord.ui.TextInput(
            label="Command Code (JSON)",
            style=discord.TextStyle.multiline,
            placeholder='{"command_type": "..."}',
            required=True
        )
        self.add_item(self.code_input)
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
        
        new_code = self.code_input.value
        
        try:
            json.loads(new_code)
        except json.JSONDecodeError:
            await interaction.response.send_message("❌ Invalid JSON! Please enter valid JSON.", ephemeral=True)
            return
        
        custom_cmds = dm.get_guild_data(interaction.guild.id, "custom_commands", {})
        custom_cmds[self.cmd_name] = new_code
        dm.update_guild_data(interaction.guild.id, "custom_commands", custom_cmds)
        
        await interaction.response.edit_message(content=f"✅ Command `!{self.cmd_name}` updated!", view=None)

class ActionHandler:
    def __init__(self, bot):
        self.bot = bot
        self._action_log = []
        self._setup_id = None
        self._artifacts = []

    async def execute_sequence(self, interaction: discord.Interaction, actions: List[Dict[str, Any]], auto_rollback: bool = True) -> Dict[str, Any]:
        """Executes a list of actions with automatic rollback on failure and crash recovery tracking."""
        import uuid
        setup_id = str(uuid.uuid4())
        results = []
        self._action_log = []
        guild_id = interaction.guild.id
        user_id = interaction.user.id

        pending_setups = dm.load_json("pending_setups", default={})
        pending_setups[setup_id] = {
            "guild_id": guild_id,
            "user_id": user_id,
            "actions_count": len(actions),
            "actions_taken": [],
            "started_at": time.time()
        }
        dm.save_json("pending_setups", pending_setups)
        self._setup_id = setup_id
        self._artifacts = []

        for i, action in enumerate(actions):
            name = action.get("name")
            params = action.get("parameters", {})
            
            try:
                success, undo_data = await self.dispatch(interaction, name, params)
                results.append((name, success))
                
                if success and undo_data:
                    self._action_log.append({
                        "action": name,
                        "undo_data": undo_data,
                        "guild_id": guild_id,
                        "user_id": user_id,
                        "timestamp": time.time()
                    })
                elif not success:
                    raise Exception(f"Action returned failure: {name}")
            except Exception as e:
                error_msg = str(e)
                logger.error("Action Error (%s): %s", name, error_msg)
                results.append((name, False))
                
                self._record_failure(guild_id, name, error_msg)
                
                if auto_rollback and self._action_log:
                    rollback_results = await self._rollback_sequence(interaction)
                    return {
                        "results": results,
                        "rolled_back": rollback_results,
                        "failed_at": i,
                        "failed_action": name,
                        "error": error_msg,
                        "success": False
                    }
                else:
                    return {
                        "results": results,
                        "rolled_back": [],
                        "failed_at": i,
                        "failed_action": name,
                        "error": error_msg,
                        "success": False
                    }

        if self._action_log:
            action_logs = dm.get_guild_data(guild_id, "action_logs", [])
            action_logs.extend(self._action_log)
            dm.update_guild_data(guild_id, "action_logs", action_logs)

        self._record_successes(guild_id, [a.get("name") for a in actions])
        
        pending_setups = dm.load_json("pending_setups", default={})
        pending_setups.pop(setup_id, None)
        dm.save_json("pending_setups", pending_setups)

        return {
            "results": results,
            "rolled_back": [],
            "failed_at": None,
            "success": True
        }

    async def _rollback_sequence(self, interaction: discord.Interaction) -> List[Tuple[str, bool]]:
        """Rollback all successfully executed actions in reverse order."""
        rollback_results = []
        for log_entry in reversed(self._action_log):
            undo_data = log_entry.get("undo_data", {})
            undo_action = undo_data.get("action")
            success = await self._execute_undo(interaction, undo_action, undo_data)
            rollback_results.append((log_entry.get("action", "unknown"), success))
        
        self._action_log = []
        return rollback_results

    def _record_failure(self, guild_id: int, action_name: str, error: str):
        """Record action failure for AI self-improvement."""
        failures = dm.get_guild_data(guild_id, "action_failures", {})
        if action_name not in failures:
            failures[action_name] = {"count": 0, "errors": [], "last_error": None}
        failures[action_name]["count"] += 1
        failures[action_name]["last_error"] = error[:200]
        if len(failures[action_name]["errors"]) < 10:
            failures[action_name]["errors"].append(error[:200])
        dm.update_guild_data(guild_id, "action_failures", failures)
        
        dm.record_global_action_result(action_name, False, error)

    def _record_successes(self, guild_id: int, action_names: List[str]):
        """Record action successes for AI self-improvement."""
        successes = dm.get_guild_data(guild_id, "action_successes", {})
        for name in action_names:
            if name not in successes:
                successes[name] = 0
            successes[name] += 1
        dm.update_guild_data(guild_id, "action_successes", successes)
        
        for name in action_names:
            dm.record_global_action_result(name, True)

    def _track_artifact(self, artifact_type: str, artifact_id: int, name: str):
        """Track a created artifact for crash recovery."""
        if self._setup_id:
            self._artifacts.append({"type": artifact_type, "id": artifact_id, "name": name})
            pending_setups = dm.load_json("pending_setups", default={})
            if self._setup_id in pending_setups:
                pending_setups[self._setup_id]["actions_taken"].append(
                    {"type": artifact_type, "id": artifact_id, "name": name}
                )
                dm.save_json("pending_setups", pending_setups)

    async def dispatch(self, interaction: discord.Interaction, name: str, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Routes action names to specific methods. Returns (success, undo_data)."""
        method_name = f"action_{name}"
        if hasattr(self, method_name):
            method = getattr(self, method_name)
            return await method(interaction, params)
        else:
            logger.warning("Unknown action: %s", name)
            return False, None

    # --- Basic Actions ---

    async def action_create_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        guild = interaction.guild
        name = params.get("name", "new-channel")
        channel_type = params.get("type", "text")
        category_name = params.get("category")
        allowed_roles = params.get("allowed_roles", [])
        denied_roles = params.get("denied_roles", [])

        category = None
        if category_name:
            category = discord.utils.get(guild.categories, name=category_name)
            if not category:
                category = await guild.create_category(category_name)

        if channel_type == "text":
            channel = await guild.create_text_channel(name, category=category)
        elif channel_type == "voice":
            channel = await guild.create_voice_channel(name, category=category)
        else:
            return False, None
        
        # Auto-detect permissions if not specified
        if not allowed_roles and not denied_roles:
            auto_perms = self._detect_channel_permissions(name, guild)
            allowed_roles = auto_perms.get("allowed", [])
            denied_roles = auto_perms.get("denied", [])
        
        # Set permissions if specified
        if allowed_roles or denied_roles:
            await self._set_channel_permissions(channel, guild, allowed_roles, denied_roles)
        
        # Send help embed to explain the channel
        await self._send_channel_guide(channel, name)
        
        self._track_artifact("channel", channel.id, channel.name)
        logger.info("Created channel: %s", channel.name)
        return True, {"action": "delete_channel", "channel_id": channel.id}

    def _detect_channel_permissions(self, channel_name: str, guild) -> dict:
        """Automatically detect what permissions a channel should have based on its name"""
        name_lower = channel_name.lower()
        
        # Permission rules based on channel keywords
        channel_rules = {
            # Staff/Admin channels - only staff can see
            "staff": {"allowed": ["Moderator", "Admin", "Administrator"], "denied": ["@everyone"]},
            "modmail": {"allowed": ["Moderator", "Admin", "Administrator"], "denied": ["@everyone"]},
            "admin": {"allowed": ["Administrator", "Admin"], "denied": ["@everyone"]},
            "logs": {"allowed": ["Moderator", "Admin", "Administrator"], "denied": ["@everyone"]},
            "bot-logs": {"allowed": ["Moderator", "Admin"], "denied": ["@everyone"]},
            
            # Applications - hidden from regular users until they apply
            "applications": {"allowed": ["Moderator", "Admin", "Administrator"], "denied": ["@everyone"]},
            "apply": {"allowed": ["Moderator", "Admin"], "denied": ["@everyone"]},
            "applications": {"allowed": [], "denied": []},  # Public but use button
            
            # Verification - new users need to verify
            "verify": {"allowed": [], "denied": []},  # Everyone can see, needs button
            
            # General channels - everyone can see
            "general": {"allowed": [], "denied": []},
            "chat": {"allowed": [], "denied": []},
            "talk": {"allowed": [], "denied": []},
            
            # Public channels - everyone can see
            "announcements": {"allowed": [], "denied": []},
            "rules": {"allowed": [], "denied": []},
            "welcome": {"allowed": [], "denied": []},
            "suggestions": {"allowed": [], "denied": []},
            
            # Support channels
            "tickets": {"allowed": ["Moderator", "Support"], "denied": ["@everyone"]},
            "ticket-queue": {"allowed": ["Moderator", "Support"], "denied": ["@everyone"]},
            
            # Media channels
            "media": {"allowed": [], "denied": []},
            "art": {"allowed": [], "denied": []},
            "gaming": {"allowed": [], "denied": []},
            "vc": {"allowed": [], "denied": []},
            
            # Voice channels - everyone can join
            "voice": {"allowed": [], "denied": []},
            "lounge": {"allowed": [], "denied": []},
        }
        
        # Find matching rule
        for keyword, perms in channel_rules.items():
            if keyword in name_lower:
                return perms
        
        # Default: public channel
        return {"allowed": [], "denied": []}

    async def _set_channel_permissions(self, channel, guild, allowed_roles, denied_roles):
        """Set view permissions for roles"""
        from discord import PermissionOverwrite
        
        overwrites = {}
        
        # Allow specified roles
        for role_name in allowed_roles:
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                overwrites[role] = PermissionOverwrite(
                    view_channel=True,
                    send_messages=True
                )
        
        # Deny specified roles
        for role_name in denied_roles:
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                overwrites[role] = PermissionOverwrite(
                    view_channel=False
                )
        
        # If there's any permission changes, apply them
        if overwrites:
            await channel.edit(overwrites=overwrites)
            logger.info(f"Set permissions on channel {channel.name}: +{allowed_roles} -{denied_roles}")

    async def _send_channel_guide(self, channel, channel_name: str):
        """Send a guide embed explaining what the channel is and available commands"""
        name_lower = channel_name.lower()
        
        guide_content = {
            "general": {
                "description": "This is a general chat channel for conversations.",
                "commands": ["!help", "!ping"]
            },
            "suggestions": {
                "description": "Submit your ideas for the server here!",
                "commands": ["!suggest <title> <description>"]
            },
            "verify": {
                "description": "Verify yourself to access the server!",
                "commands": ["Click the Verify button"]
            },
            "rules": {
                "description": "Please read and follow our server rules.",
                "commands": ["React to accept rules"]
            },
            "ticket": {
                "description": "Need help? Create a ticket!",
                "commands": ["!ticket <message>"]
            },
            "applications": {
                "description": "Apply to join our staff team!",
                "commands": ["!apply"]
            },
            "modmail": {
                "description": "DM the bot for staff assistance.",
                "commands": ["DM the bot directly"]
            },
            "announcements": {
                "description": "Server news and updates.",
                "commands": ["Staff only: Post announcements"]
            }
        }
        
        # Find matching guide
        guide = None
        for key, content in guide_content.items():
            if key in name_lower:
                guide = content
                break
        
        if not guide:
            return
        
        embed = discord.Embed(
            title=f"🔹 {channel_name}",
            description=guide["description"],
            color=discord.Color.blue()
        )
        
        cmd_list = "\n".join([f"• {cmd}" for cmd in guide["commands"]])
        embed.add_field(name="Available Commands", value=cmd_list, inline=False)
        
        await channel.send(embed=embed)

    async def action_create_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        guild = interaction.guild
        name = params.get("name")
        color_hex = params.get("color", "#99AAB5").replace("#", "")
        color = discord.Color(int(color_hex, 16))
        
        # Auto-detect role permissions
        role_perms = self._detect_role_permissions(name)
        
        permissions = discord.Permissions(
            view_channel=role_perms.get("view_channel", False),
            send_messages=role_perms.get("send_messages", False),
            manage_channels=role_perms.get("manage_channels", False),
            manage_roles=role_perms.get("manage_roles", False),
            kick_members=role_perms.get("kick_members", False),
            ban_members=role_perms.get("ban_members", False),
            moderate_members=role_perms.get("moderate_members", False),
            manage_messages=role_perms.get("manage_messages", False),
            mention_everyone=role_perms.get("mention_everyone", False)
        )
        
        role = await guild.create_role(
            name=name, 
            color=color, 
            permissions=permissions,
            hoist=role_perms.get("hoist", False),
            mentionable=role_perms.get("mentionable", False),
            reason="AI Action"
        )
        
        # Send role info
        await self._send_role_guide(interaction, name, role_perms)
        
        self._track_artifact("role", role.id, role.name)
        return True, {"action": "delete_role", "role_id": role.id}

    def _detect_role_permissions(self, role_name: str) -> dict:
        """Auto-detect what permissions a role should have based on its name"""
        name_lower = role_name.lower()
        
        role_rules = {
            # Admin role - full permissions
            "admin": {
                "view_channel": True, "manage_channels": True, "manage_roles": True,
                "kick_members": True, "ban_members": True, "moderate_members": True,
                "mention_everyone": True, "hoist": True, "mentionable": True
            },
            # Moderator role
            "mod": {
                "view_channel": True, "send_messages": True, "manage_messages": True,
                "kick_members": True, "moderate_members": True,
                "mention_everyone": False, "hoist": True, "mentionable": True
            },
            # Support role
            "support": {
                "view_channel": True, "send_messages": True, "manage_messages": True,
                "hoist": True, "mentionable": True
            },
            # Verified role - basic access
            "verified": {
                "view_channel": True, "send_messages": True,
                "hoist": True, "mentionable": False
            },
            # Member role
            "member": {
                "view_channel": True, "send_messages": True,
                "hoist": True
            },
            # Muted role - no permissions
            "muted": {
                "view_channel": True, "send_messages": False,
                "hoist": True, "mentionable": False
            },
            # Bot role
            "bot": {
                "view_channel": True, "send_messages": True,
                "manage_messages": True, "hoist": True
            },
            # VIP role
            "vip": {
                "view_channel": True, "send_messages": True,
                "hoist": True, "mentionable": True
            },
            # Event role
            "event": {
                "view_channel": True, "send_messages": True,
                "mention_everyone": False, "hoist": True, "mentionable": True
            },
            # Streaming role
            "streaming": {
                "view_channel": True, "send_messages": True,
                "mention_everyone": True, "hoist": True, "mentionable": True
            },
            # Gaming role
            "gaming": {
                "view_channel": True, "send_messages": True,
                "hoist": True
            },
            # Music role
            "music": {
                "view_channel": True, "send_messages": True,
                "hoist": True
            }
        }
        
        for keyword, perms in role_rules.items():
            if keyword in name_lower:
                return perms
        
        # Default: basic member permissions
        return {"view_channel": True, "send_messages": True, "hoist": False}

    async def _send_role_guide(self, interaction, role_name: str, role_perms: dict):
        """Send a guide embed explaining what the role is"""
        import discord
        
        name_lower = role_name.lower()
        
        guide_content = {
            "admin": "Full administrator role with all permissions.",
            "mod": "Moderator role for managing the server.",
            "support": "Support role for helping users.",
            "verified": "Verified members - access to all channels.",
            "member": "Default member role.",
            "muted": "Muted role - cannot send messages.",
            "vip": "VIP role - special perks and access.",
            "event": "Event participants role.",
            "gaming": "Gaming community role.",
            "music": "Music lovers role."
        }
        
        description = guide_content.get("default", f"Custom role: {role_name}")
        for key, desc in guide_content.items():
            if key in name_lower:
                description = desc
                break
        
        embed = discord.Embed(
            title=f"🔹 Role: {role_name}",
            description=description,
            color=discord.Color.blue()
        )
        
        perm_list = [f"View Channels", "Send Messages"] if role_perms.get("view_channel") else []
        if role_perms.get("moderate_members"): perm_list.append("Moderate Members")
        if role_perms.get("kick_members"): perm_list.append("Kick Members")
        if role_perms.get("ban_members"): perm_list.append("Ban Members")
        if role_perms.get("manage_channels"): perm_list.append("Manage Channels")
        
        if perm_list:
            embed.add_field(name="Permissions", value="\n".join([f"✅ {p}" for p in perm_list]), inline=False)
        
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def action_assign_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        user_id = params.get("user_id")
        role_id = params.get("role_id")
        guild = interaction.guild
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        role = guild.get_role(role_id)
        
        if member and role:
            await member.add_roles(role)
            return True, {"action": "remove_role", "user_id": user_id, "role_id": role_id}
        return False, None

    async def action_create_prefix_command(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Adds a custom '!' command to the guild structure."""
        guild_id = interaction.guild.id
        cmd_name = params.get("name")
        cmd_code = params.get("code")
        
        cmds = dm.get_guild_data(guild_id, "custom_commands", {})
        existing = cmds.get(cmd_name)
        cmds[cmd_name] = cmd_code
        dm.update_guild_data(guild_id, "custom_commands", cmds)
        return True, {"action": "delete_prefix_command", "cmd_name": cmd_name, "previous_code": existing}

    async def action_send_embed(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        channel_name = params.get("channel")
        title = params.get("title")
        description = params.get("description")
        color = params.get("color", 0x3498db)

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) or interaction.channel
        embed = discord.Embed(title=title, description=description, color=color)
        msg = await channel.send(embed=embed)
        return True, {"action": "delete_message", "channel_id": channel.id, "message_id": msg.id}

    async def action_post_documentation(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Posts a comprehensive, multi-section documentation embed for a newly created system."""
        channel_name = params.get("channel")
        channel = discord.utils.get(interaction.guild.channels, name=channel_name) or interaction.channel
        
        title = params.get("title", "System Documentation")
        description = params.get("description", "")
        sections = params.get("sections", [])
        footer = params.get("footer", "")
        color = params.get("color", 0x5865F2)
        
        embed = discord.Embed(title=title, description=description, color=color)
        
        for section in sections:
            section_title = section.get("title", "")
            section_content = section.get("content", "")
            if section_title and section_content:
                embed.add_field(name=section_title, value=section_content, inline=False)
        
        if footer:
            embed.set_footer(text=footer)
        
        embed.timestamp = datetime.datetime.utcnow()
        
        msg = await channel.send(embed=embed)
        return True, {"action": "delete_message", "channel_id": channel.id, "message_id": msg.id}

    # --- Specialized Systems ---

    async def action_setup_staff_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        from modules.staff_system import StaffSystem
        system = StaffSystem(self.bot)
        result = await system.setup(interaction, params)
        return result, {"action": "undo_staff_system", "guild_id": interaction.guild.id}

    async def action_setup_economy(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        from modules.economy import Economy
        system = Economy(self.bot)
        result = await system.setup(interaction, params)
        return result, {"action": "undo_economy", "guild_id": interaction.guild.id}

    async def action_setup_trigger_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        from modules.trigger_roles import TriggerRoles
        system = TriggerRoles(self.bot)
        result = await system.setup(interaction, params)
        return result, {"action": "undo_trigger_role", "guild_id": interaction.guild.id}

    async def action_schedule_ai_action(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Schedule an AI action to run on a cron schedule."""
        from task_scheduler import TaskScheduler
        
        name = params.get("name", f"scheduled_{int(time.time())}")
        cron = params.get("cron", "0 12 * * *")
        action_type = params.get("action_type", "announcement")
        action_params = params.get("action_params", {})
        channel_id = params.get("channel_id")
        
        guild_id = interaction.guild.id
        
        scheduler = getattr(self.bot, 'scheduler', None)
        if scheduler and hasattr(scheduler, 'add_ai_task'):
            scheduler.add_ai_task(name, guild_id, cron, action_type, action_params, channel_id)
            logger.info(f"Scheduled AI action: {name} for guild {guild_id}")
            return True, {"action": "remove_ai_task", "name": name}
        else:
            logger.error("Scheduler not available")
            return False, None

    # --- Execution Logic ---

    async def execute_custom_command(self, message: discord.Interaction, code: str, cmd_name: str = None):
        """
        Executes a custom '!' command's stored code.
        Can be a simple string, a list of actions, or a special command object.
        Includes error prevention based on learned patterns.
        """
        guild_id = message.guild.id
        cmd_data_obj = None
        
        try:
            data = json.loads(code)
            cmd_data_obj = data
            
            # Handle list of actions (existing functionality)
            if isinstance(data, list):
                # We'd need a way to pass 'message' context to execute_sequence
                # For now, just acknowledge the command
                await message.channel.send("Command executed (action list).")
                return True
            
            # Handle special command objects
            elif isinstance(data, dict):
                command_type = data.get("command_type")
                
                if command_type == "application_status":
                    return await self.handle_application_status(message)
                elif command_type == "appeal_status":
                    return await self.handle_appeal_status(message)
                elif command_type == "help_embed":
                    return await self.send_help_embed(message, data)
                elif command_type == "list_triggers":
                    return await self.list_triggers(message)
                elif command_type == "economy_daily":
                    return await self.handle_economy_daily(message)
                elif command_type == "economy_balance":
                    return await self.handle_economy_balance(message)
                elif command_type == "list_achievements":
                    return await self.handle_list_achievements(message)
                elif command_type == "list_titles":
                    return await self.handle_list_titles(message)
                elif command_type == "set_title":
                    return await self.handle_set_title(message)
                elif command_type == "achievements_leaderboard":
                    return await self.handle_achievements_leaderboard(message)
                elif command_type == "help_all":
                    return await self.handle_help_all(message)
                elif command_type == "staffpromo_status":
                    return await self.handle_staffpromo_status(message)
                elif command_type == "staffpromo_leaderboard":
                    return await self.handle_staffpromo_leaderboard(message)
                elif command_type == "staffpromo_config":
                    return await self.handle_staffpromo_config(message)
                elif command_type == "staffpromo_progress":
                    return await self.handle_staffpromo_progress(message)
                elif command_type == "staffpromo_promote":
                    return await self.handle_staffpromo_promote(message)
                elif command_type == "staffpromo_demote":
                    return await self.handle_staffpromo_demote(message)
                elif command_type == "staffpromo_exclude":
                    return await self.handle_staffpromo_exclude(message)
                elif command_type == "staffpromo_roles":
                    return await self.handle_staffpromo_roles(message)
                elif command_type == "staffpromo_review":
                    return await self.handle_staffpromo_review(message)
                elif command_type == "staffpromo_requirements":
                    return await self.handle_staffpromo_requirements(message)
                elif command_type == "staffpromo_bonuses":
                    return await self.handle_staffpromo_bonuses(message)
                else:
                    # Unknown dict type, fall back to sending as string
                    await message.channel.send(content=code)
                    return True
                    
            # Handle plain strings (existing functionality)
            else:
                await message.channel.send(content=code)
                return True
                
        except json.JSONDecodeError:
            # Not JSON, treat as plain string
            await message.channel.send(content=code)
            return True
        except Exception as e:
            logger.error("Error executing custom command: %s", e)
            
            # Check for error prevention
            prevention = self._get_error_prevention(guild_id, cmd_name, cmd_data_obj, str(e))
            if prevention:
                await message.channel.send(prevention.get("message", "An error occurred. Try: !help " + (cmd_name or "help")))
            else:
                await message.channel.send("An error occurred while executing this command.")
            return False
    
    def _get_error_prevention(self, guild_id: int, cmd_name: str, cmd_data: dict, error_msg: str) -> dict:
        """Get error prevention measures based on learned patterns."""
        if not cmd_name:
            return None
        
        # Check if command has pre-configured prevention
        if cmd_data and isinstance(cmd_data, dict):
            prevention = cmd_data.get("error_prevention")
            if prevention and prevention.get("prevention_enabled"):
                return prevention
        
        # Check stored prevention from AI analysis
        custom_cmds = dm.get_guild_data(guild_id, "custom_commands", {})
        if cmd_name in custom_cmds:
            stored = custom_cmds[cmd_name]
            try:
                stored_data = json.loads(stored) if isinstance(stored, str) else stored
                if isinstance(stored_data, dict):
                    prevention = stored_data.get("error_prevention")
                    if prevention and prevention.get("prevention_enabled"):
                        return prevention
            except:
                pass
        
        return None

    async def handle_application_status(self, message: discord.Message) -> bool:
        """Handle !apply status command"""
        apps = dm.load_json("applications", default={})
        user_id = str(message.author.id)
        
        if user_id not in apps:
            await message.channel.send("You have not submitted a staff application yet.")
            return True
            
        status = apps[user_id]["status"]
        timestamp = apps[user_id]["timestamp"]
        
        embed = discord.Embed(title="Your Staff Application Status", color=discord.Color.blue())
        embed.add_field(name="Status", value=status.capitalize(), inline=True)
        embed.add_field(name="Submitted", value=timestamp, inline=True)
        
        await message.channel.send(embed=embed)
        return True

    async def handle_appeal_status(self, message: discord.Message) -> bool:
        """Handle !appeal status command"""
        appeals = dm.load_json("appeals", default={})
        user_id = str(message.author.id)
        
        if user_id not in appeals:
            await message.channel.send("You have no active appeals.")
            return True
            
        appeal = appeals[user_id]
        status = appeal.get("status", "pending")
        timestamp = appeal.get("timestamp", "Unknown")
        action_id = appeal.get("action_id", "Unknown")
        
        embed = discord.Embed(title="Your Appeal Status", color=discord.Color.blue())
        embed.add_field(name="Action ID", value=str(action_id), inline=True)
        embed.add_field(name="Status", value=status.capitalize(), inline=True)
        embed.add_field(name="Submitted", value=str(timestamp), inline=True)
        
        await message.channel.send(embed=embed)
        return True

    async def send_help_embed(self, message: discord.Message, data: dict) -> bool:
        """Send a help embed based on stored data"""
        title = data.get("title", "Help")
        description = data.get("description", "")
        fields = data.get("fields", [])
        
        embed = discord.Embed(title=title, description=description, color=discord.Color.blue())
        
        for field in fields:
            embed.add_field(
                name=field.get("name", ""),
                value=field.get("value", ""),
                inline=field.get("inline", False)
            )
        
        await message.channel.send(embed=embed)
        return True

    async def list_triggers(self, message: discord.Message) -> bool:
        """List all active trigger words for the guild"""
        guild_id = message.guild.id
        triggers = dm.get_guild_data(guild_id, "trigger_roles", {})
        
        if not triggers:
            await message.channel.send("No trigger words are currently set up.")
            return True
            
        embed = discord.Embed(title="Active Trigger Words", color=discord.Color.blue())
        
        for word, role_id in triggers.items():
            role = message.guild.get_role(role_id)
            role_name = role.name if role else f"Unknown Role (ID: {role_id})"
            embed.add_field(
                name=f"Trigger: `{word}`",
                value=f"Assigns role: **{role_name}**",
                inline=False
            )
        
        await message.channel.send(embed=embed)
        return True

    async def handle_economy_daily(self, message: discord.Message) -> bool:
        """Handle !daily command"""
        from modules.economy import Economy
        economy = Economy(self.bot)
        guild_id = message.guild.id
        user_id = message.author.id
        
        last_daily = dm.get_guild_data(guild_id, "last_daily", {})
        last_time = last_daily.get(str(user_id))
        
        if last_time:
            last_date = datetime.datetime.fromisoformat(last_time)
            if (datetime.datetime.now() - last_date).days < 1:
                await message.channel.send("Daily reward already claimed today!")
                return True
        
        reward = 100
        economy.add_coins(guild_id, user_id, reward)
        last_daily[str(user_id)] = str(datetime.datetime.now())
        dm.update_guild_data(guild_id, "last_daily", last_daily)
        
        await message.channel.send(f"💰 {message.author.mention} claimed **{reward} coins**!")
        return True

    async def handle_economy_balance(self, message: discord.Message) -> bool:
        """Handle !balance command"""
        from modules.economy import Economy
        from modules.leveling import Leveling
        economy = Economy(self.bot)
        leveling = Leveling(self.bot)
        guild_id = message.guild.id
        user_id = message.author.id
        
        coins = economy.get_coins(guild_id, user_id)
        gems = leveling.get_gems(guild_id, user_id)
        xp = leveling.get_xp(guild_id, user_id)
        level = leveling.get_level_from_xp(xp)
        
        embed = discord.Embed(title=f"{message.author.name}'s Balance", color=discord.Color.gold())
        embed.add_field(name="Coins", value=str(coins), inline=True)
        embed.add_field(name="Gems", value=str(gems), inline=True)
        embed.add_field(name="Level", value=f"{level} ({xp} XP)", inline=True)
        
        await message.channel.send(embed=embed)
        return True

    async def handle_list_achievements(self, message: discord.Message) -> bool:
        """Handle !achievements command"""
        from modules.achievements import AchievementSystem
        achievements = AchievementSystem(self.bot)
        
        user_achievements = achievements.get_user_achievements(message.guild.id, message.author.id)
        
        if not user_achievements:
            embed = discord.Embed(
                title="🎯 Your Achievements",
                description="No achievements yet! Keep being active to earn some.",
                color=discord.Color.gold()
            )
        else:
            embed = discord.Embed(
                title=f"🎯 {message.author.display_name}'s Achievements",
                description=f"**{len(user_achievements)} achievements earned**",
                color=discord.Color.gold()
            )
            
            # Group by category
            by_category = {}
            for ach in user_achievements:
                cat = ach.get("category", "other")
                if cat not in by_category:
                    by_category[cat] = []
                by_category[cat].append(ach)
            
            for category, achs in by_category.items():
                ach_list = "\n".join([f"{a['icon']} **{a['name']}** - {a['description']}" for a in achs[:5]])
                if ach_list:
                    embed.add_field(
                        name=f"{category.title()} ({len(achs)})",
                        value=ach_list,
                        inline=False
                    )
        
        await message.channel.send(embed=embed)
        return True

    async def handle_list_titles(self, message: discord.Message) -> bool:
        """Handle !titles command"""
        from modules.achievements import AchievementSystem
        achievements = AchievementSystem(self.bot)
        
        user_titles = achievements.get_user_titles(message.guild.id, message.author.id)
        active_title = achievements.get_active_title(message.guild.id, message.author.id)
        
        if not user_titles:
            embed = discord.Embed(
                title="🎖️ Your Titles",
                description="No titles yet! Keep being active to unlock titles.",
                color=discord.Color.gold()
            )
        else:
            active_name = f"{active_title['icon']} {active_title['name']}" if active_title else "None"
            
            embed = discord.Embed(
                title=f"🎖️ {message.author.display_name}'s Titles",
                description=f"Active: **{active_name}**\n" +
                           f"Total unlocked: **{len(user_titles)}**",
                color=discord.Color.gold()
            )
            
            titles_list = "\n".join([f"{t['icon']} **{t['name']}**" for t in user_titles])
            embed.add_field(name="Unlocked Titles", value=titles_list, inline=False)
        
        await message.channel.send(embed=embed)
        return True

    async def handle_set_title(self, message: discord.Message) -> bool:
        """Handle !settitle command"""
        from modules.achievements import AchievementSystem
        achievements = AchievementSystem(self.bot)
        
        # Parse command - get title from message content
        content = message.content.split(" ", 1)
        title_input = content[1].strip() if len(content) > 1 else None
        
        if not title_input:
            user_titles = achievements.get_user_titles(message.guild.id, message.author.id)
            
            if not user_titles:
                await message.channel.send("You don't have any titles yet!")
                return True
            
            titles_list = "\n".join([f"• {t['icon']} **{t['name']}**" for t in user_titles])
            await message.channel.send(f"Available titles:\n{titles_list}\n\nUse `!settitle <name>` to set one.")
            return True
        
        # Find matching title
        user_titles = achievements.get_user_titles(message.guild.id, message.author.id)
        
        matched_title = None
        for t in user_titles:
            if title_input.lower() in t['name'].lower() or title_input.lower() == t['id'].lower():
                matched_title = t
                break
        
        if not matched_title:
            await message.channel.send(f"No title matching '{title_input}' found. Use `!titles` to see your titles.")
            return True
        
        # Set active title
        achievements.set_active_title(message.guild.id, message.author.id, matched_title['id'])
        
        await message.channel.send(f"✅ Title set to **{matched_title['icon']} {matched_title['name']}**!")
        return True

    async def handle_achievements_leaderboard(self, message: discord.Message) -> bool:
        """Handle !achievementsleaderboard command"""
        from modules.achievements import AchievementSystem
        achievements = AchievementSystem(self.bot)
        
        leaderboard = achievements.get_leaderboard(message.guild.id)
        
        if not leaderboard:
            await message.channel.send("No achievements recorded yet!")
            return True
        
        embed = discord.Embed(
            title="🏆 Achievement Leaderboard",
            description="Top achievement collectors in this server:",
            color=discord.Color.gold()
        )
        
        lb_text = "\n".join([
            f"**{entry['rank']}.** <@{entry['user_id']}> - {entry['achievements']} achievements"
            for entry in leaderboard
        ])
        
        embed.add_field(name="Rankings", value=lb_text, inline=False)
        
        await message.channel.send(embed=embed)
        return True

    async def undo_last_actions(self, interaction: discord.Interaction, count: int = 1) -> List[Tuple[str, bool]]:
        """Undo the last N action groups for the guild."""
        guild_id = interaction.guild.id
        action_logs = dm.get_guild_data(guild_id, "action_logs", [])
        
        if not action_logs:
            return [("No actions to undo", False)]
        
        to_undo = action_logs[-count:]
        remaining = action_logs[:-count]
        dm.update_guild_data(guild_id, "action_logs", remaining)
        
        results = []
        for log_entry in reversed(to_undo):
            undo_data = log_entry.get("undo_data", {})
            undo_action = undo_data.get("action")
            success = await self._execute_undo(interaction, undo_action, undo_data)
            results.append((log_entry.get("action", "unknown"), success))
        
        return results

    async def _execute_undo(self, interaction: discord.Interaction, undo_action: str, undo_data: Dict) -> bool:
        """Execute a specific undo operation."""
        try:
            guild = interaction.guild
            
            if undo_action == "delete_channel":
                channel = guild.get_channel(undo_data.get("channel_id"))
                if channel:
                    await channel.delete()
                    return True
                    
            elif undo_action == "delete_role":
                role = guild.get_role(undo_data.get("role_id"))
                if role:
                    await role.delete()
                    return True
                    
            elif undo_action == "remove_role":
                member = guild.get_member(undo_data.get("user_id"))
                role = guild.get_role(undo_data.get("role_id"))
                if member and role:
                    await member.remove_roles(role)
                    return True
                    
            elif undo_action == "delete_prefix_command":
                cmd_name = undo_data.get("cmd_name")
                cmds = dm.get_guild_data(guild.id, "custom_commands", {})
                if cmd_name in cmds:
                    previous = undo_data.get("previous_code")
                    if previous:
                        cmds[cmd_name] = previous
                    else:
                        del cmds[cmd_name]
                    dm.update_guild_data(guild.id, "custom_commands", cmds)
                    return True
                    
            elif undo_action == "delete_message":
                channel = guild.get_channel(undo_data.get("channel_id"))
                if channel:
                    try:
                        msg = await channel.fetch_message(undo_data.get("message_id"))
                        await msg.delete()
                        return True
                    except discord.NotFound:
                        pass
                        
            elif undo_action in ("undo_staff_system", "undo_economy", "undo_trigger_role"):
                return await self._undo_system_setup(guild, undo_action)
                
            return False
        except Exception as e:
            logger.error("Undo Error (%s): %s", undo_action, e)
            return False

    async def _undo_system_setup(self, guild: discord.Guild, undo_action: str) -> bool:
        """Undo a system setup by removing associated channels and commands."""
        try:
            if undo_action == "undo_staff_system":
                channels_to_remove = ["apply-staff", "apply-staff-logs"]
                cmds_to_remove = ["apply", "help staffapply"]
            elif undo_action == "undo_economy":
                channels_to_remove = ["economy", "shop"]
                cmds_to_remove = ["daily", "balance", "transfer", "shop", "help economy"]
            elif undo_action == "undo_trigger_role":
                channels_to_remove = []
                cmds_to_remove = ["triggers", "help triggers"]
            else:
                return False
            
            for ch_name in channels_to_remove:
                channel = discord.utils.get(guild.channels, name=ch_name)
                if channel:
                    await channel.delete()
            
            cmds = dm.get_guild_data(guild.id, "custom_commands", {})
            for cmd in cmds_to_remove:
                cmds.pop(cmd, None)
            dm.update_guild_data(guild.id, "custom_commands", cmds)
            
            return True
        except Exception as e:
            logger.error("System Undo Error (%s): %s", undo_action, e)
            return False

    async def handle_help_all(self, message: discord.Message) -> bool:
        """Handle !help command - shows interactive embed for all non-system commands (no help <system>)"""
        guild_id = message.guild.id
        custom_cmds = dm.get_guild_data(guild_id, "custom_commands", {})
        
        non_help_commands = {}
        for cmd_name, cmd_code in custom_cmds.items():
            if not cmd_name.startswith("help "):
                non_help_commands[cmd_name] = cmd_code
        
        if not non_help_commands:
            await message.channel.send("No custom commands yet! Use /bot to create systems.")
            return True
        
        command_groups = {}
        for cmd_name in non_help_commands.keys():
            parts = cmd_name.split()
            if len(parts) > 1:
                parent = parts[0]
                if parent not in command_groups:
                    command_groups[parent] = []
                command_groups[parent].append(cmd_name)
            else:
                if cmd_name not in command_groups:
                    command_groups[cmd_name] = []
        
        embed = discord.Embed(
            title="📋 All Custom Commands",
            description=f"Total: {len(non_help_commands)} commands\n\nClick a command to view/edit its subcommands.",
            color=discord.Color.blue()
        )
        
        parent_list = []
        for parent in sorted(command_groups.keys()):
            subcount = len(command_groups[parent])
            if subcount > 0:
                parent_list.append(f"**!{parent}** ({subcount} subcommands)")
            else:
                parent_list.append(f"**!{parent}**")
        
        embed.add_field(
            name="🎮 Commands",
            value="\n".join(parent_list[:25]),
            inline=False
        )
        if len(parent_list) > 25:
            embed.add_field(name="", value="\n".join(parent_list[25:]), inline=False)
        
        embed.set_footer(text="Click to edit • Back to close")
        
        view = CommandsListView(non_help_commands, command_groups, message.author.id)
        await message.channel.send(embed=embed, view=view)
        return True


class CommandsListView(discord.ui.View):
    """Interactive view for listing and editing all custom commands"""
    
    def __init__(self, all_commands: dict, command_groups: dict, user_id: int, parent: str = None, page: int = 0):
        super().__init__(timeout=180)
        self.all_commands = all_commands
        self.command_groups = command_groups
        self.user_id = user_id
        self.parent = parent
        self.page = page
        self.commands_per_page = 10
        
        if parent is None:
            parent_commands = [p for p in command_groups.keys() if not command_groups[p]]
            parent_commands.extend([p for p in command_groups.keys() if command_groups[p]])
            start_idx = page * self.commands_per_page
            end_idx = start_idx + self.commands_per_page
            page_commands = parent_commands[start_idx:end_idx]
            
            for cmd in page_commands:
                btn = discord.ui.Button(label=f"!{cmd}", custom_id=f"view_{cmd}", style=discord.ButtonStyle.secondary)
                btn.callback = self.create_view_callback(cmd)
                self.add_item(btn)
            
            total = len(parent_commands)
            total_pages = (total + self.commands_per_page - 1) // self.commands_per_page
            
            if page > 0:
                prev_btn = discord.ui.Button(label="◀ Prev", custom_id=f"prev_{page}", style=discord.ButtonStyle.primary)
                prev_btn.callback = self.create_parent_prev_callback(page)
                self.add_item(prev_btn)
            
            if page < total_pages - 1:
                next_btn = discord.ui.Button(label="Next ▶", custom_id=f"next_{page}", style=discord.ButtonStyle.primary)
                next_btn.callback = self.create_parent_next_callback(page)
                self.add_item(next_btn)
            
            back_btn = discord.ui.Button(label="✕ Close", custom_id="close", style=discord.ButtonStyle.danger)
            back_btn.callback = self.close_callback
            self.add_item(back_btn)
        else:
            subcommands = command_groups.get(parent, [])
            if parent not in subcommands:
                subcommands = [parent] + subcommands
            
            start_idx = page * self.commands_per_page
            end_idx = start_idx + self.commands_per_page
            page_subs = subcommands[start_idx:end_idx]
            
            for sub in page_subs:
                is_parent = (sub == parent)
                btn = discord.ui.Button(
                    label=f"!{sub}" + (" (parent)" if is_parent else ""),
                    custom_id=f"edit_{sub}",
                    style=discord.ButtonStyle.success if is_parent else discord.ButtonStyle.secondary
                )
                btn.callback = self.create_edit_callback(sub)
                self.add_item(btn)
            
            total_pages = (len(subcommands) + self.commands_per_page - 1) // self.commands_per_page
            
            if page > 0:
                prev_btn = discord.ui.Button(label="◀ Prev", custom_id=f"prev_{page}", style=discord.ButtonStyle.primary)
                prev_btn.callback = self.create_sub_prev_callback(page)
                self.add_item(prev_btn)
            
            if page < total_pages - 1:
                next_btn = discord.ui.Button(label="Next ▶", custom_id=f"next_{page}", style=discord.ButtonStyle.primary)
                next_btn.callback = self.create_sub_next_callback(page)
                self.add_item(next_btn)
            
            back_btn = discord.ui.Button(label="← Back", custom_id="back", style=discord.ButtonStyle.primary)
            back_btn.callback = self.back_callback
            self.add_item(back_btn)
    
    def create_view_callback(self, parent: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
            view = CommandsListView(self.all_commands, self.command_groups, self.user_id, parent, 0)
            subcommands = self.command_groups.get(parent, [])
            if parent not in subcommands:
                subcommands = [parent] + subcommands
            total = len(subcommands)
            
            embed = discord.Embed(
                title=f"📁 Command: !{parent}",
                description=f"Subcommands: {total}\n\nClick to edit each command's code.",
                color=discord.Color.orange()
            )
            await interaction.response.edit_message(embed=embed, view=view)
        return callback
    
    def create_edit_callback(self, cmd_name: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
            
            cmd_code = self.all_commands.get(cmd_name, "")
            
            embed = discord.Embed(
                title=f"✏️ Edit Command: !{cmd_name}",
                description=f"Current code:\n```json\n{cmd_code[:1500]}\n```",
                color=discord.Color.orange()
            )
            
            modal = EditCommandModal(cmd_name, self.user_id)
            await interaction.response.send_modal(modal)
        return callback
    
    def create_parent_prev_callback(self, page: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
            view = CommandsListView(self.all_commands, self.command_groups, self.user_id, None, page - 1)
            await interaction.response.edit_message(view=view)
        return callback
    
    def create_parent_next_callback(self, page: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
            view = CommandsListView(self.all_commands, self.command_groups, self.user_id, None, page + 1)
            await interaction.response.edit_message(view=view)
        return callback
    
    def create_sub_prev_callback(self, page: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
            view = CommandsListView(self.all_commands, self.command_groups, self.user_id, self.parent, page - 1)
            await interaction.response.edit_message(view=view)
        return callback
    
    def create_sub_next_callback(self, page: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
            view = CommandsListView(self.all_commands, self.command_groups, self.user_id, self.parent, page + 1)
            await interaction.response.edit_message(view=view)
        return callback
    
    async def back_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
        view = CommandsListView(self.all_commands, self.command_groups, self.user_id, None, 0)
        custom_cmds = self.all_commands
        command_groups = self.command_groups
        
        command_list = []
        for parent in sorted(command_groups.keys()):
            subcount = len(command_groups[parent])
            if subcount > 0:
                command_list.append(f"**!{parent}** ({subcount} subcommands)")
            else:
                command_list.append(f"**!{parent}**")
        
        embed = discord.Embed(
            title="📋 All Custom Commands",
            description=f"Total: {len(custom_cmds)} commands\n\nClick a command to view/edit its subcommands.",
            color=discord.Color.blue()
        )
        embed.add_field(name="🎮 Commands", value="\n".join(command_list[:25]), inline=False)
        
        await interaction.response.edit_message(embed=embed, view=view)
    
    async def close_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
        await interaction.response.edit_message(content="📋 Commands closed.", view=None)

    async def handle_staffpromo_status(self, message: discord.Message) -> bool:
        guild = message.guild
        member = message.author
        staff_promo = self.bot.staff_promo
        promotion_service = self.bot.promotion_service
        
        config = staff_promo._get_full_config(guild.id)
        metrics = config.get("metrics", staff_promo._default_metrics)
        
        score = promotion_service._compute_score(guild.id, member.id, member, metrics)
        tiers = config.get("tiers", staff_promo._default_tiers)
        
        current_tier = "None"
        for tier in tiers:
            rid = config.get("roles_by_tier", {}).get(tier["name"])
            if rid and any(r.id == rid for r in member.roles):
                current_tier = tier["name"]
                break
        
        embed = discord.Embed(title="📊 Your Staff Promotion Status", color=discord.Color.blue())
        embed.add_field(name="Current Role", value=current_tier, inline=True)
        embed.add_field(name="Score", value=f"{score*100:.1f}%", inline=True)
        
        breakdown = []
        udata = dm.get_guild_data(guild.id, f"user_{member.id}", {})
        for metric_name, cfg in metrics.items():
            if not cfg.get("enabled", True):
                continue
            max_val = cfg.get("max", 100)
            weight = cfg.get("weight", 0)
            if metric_name == "tenure_days":
                val = (datetime.utcnow() - (member.joined_at or datetime.utcnow())).days
            elif metric_name == "achievements":
                val = len(dm.get_guild_data(guild.id, f"achievements_{member.id}", []))
            else:
                val = udata.get(metric_name, 0)
            normalized = max(0, min(1, val / max_val)) if max_val > 0 else 0
            breakdown.append(f"• {metric_name}: {val}/{max_val} ({normalized*weight*100:.1f}%)")
        
        embed.add_field(name="Score Breakdown", value="\n".join(breakdown), inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        
        await message.channel.send(embed=embed)
        return True

    async def handle_staffpromo_leaderboard(self, message: discord.Message) -> bool:
        guild = message.guild
        staff_promo = self.bot.staff_promo
        promotion_service = self.bot.promotion_service
        
        config = staff_promo._get_full_config(guild.id)
        metrics = config.get("metrics", staff_promo._default_metrics)
        
        scores = []
        for member in guild.members:
            if member.bot:
                continue
            score = promotion_service._compute_score(guild.id, member.id, member, metrics)
            scores.append((member, score))
        
        scores.sort(key=lambda x: x[1], reverse=True)
        top_10 = scores[:10]
        
        embed = discord.Embed(title="🏆 Staff Promotion Leaderboard", color=discord.Color.gold())
        
        if not top_10:
            embed.add_field(name="No data", value="No staff members evaluated yet", inline=False)
        else:
            for i, (member, score) in enumerate(top_10, 1):
                embed.add_field(name=f"#{i} {member.display_name}", value=f"Score: {score*100:.1f}%", inline=True)
        
        await message.channel.send(embed=embed)
        return True

    async def handle_staffpromo_config(self, message: discord.Message) -> bool:
        if not message.author.guild_permissions.administrator:
            await message.channel.send("❌ This command is only for administrators.")
            return True
        
        guild = message.guild
        staff_promo = self.bot.staff_promo
        config = staff_promo._get_full_config(guild.id)
        
        settings = config.get("settings", staff_promo._default_settings)
        tiers = config.get("tiers", staff_promo._default_tiers)
        
        embed = discord.Embed(title="⚙️ Staff Promo Configuration", color=discord.Color.orange())
        
        embed.add_field(name="Auto Promote", value=str(settings.get("auto_promote", True)), inline=True)
        embed.add_field(name="Auto Demote", value=str(settings.get("auto_demote", False)), inline=True)
        embed.add_field(name="Min Tenure", value=f"{settings.get('min_tenure_hours', 72)} hours", inline=True)
        embed.add_field(name="Cooldown", value=f"{settings.get('promotion_cooldown_hours', 24)} hours", inline=True)
        
        tiers_text = "\n".join([f"• {t['name']}: {int(t['threshold']*100)}%" for t in tiers])
        embed.add_field(name="Tiers", value=tiers_text or "None", inline=False)
        
        await message.channel.send(embed=embed)
        return True

    async def handle_staffpromo_progress(self, message: discord.Message) -> bool:
        guild = message.guild
        member = message.author
        staff_promo = self.bot.staff_promo
        promotion_service = self.bot.promotion_service
        
        config = staff_promo._get_full_config(guild.id)
        metrics = config.get("metrics", staff_promo._default_metrics)
        tiers = config.get("tiers", staff_promo._default_tiers)
        role_ids = dict(config.get("roles_by_tier", {}))
        
        for tier in tiers:
            tier_name = tier.get("name")
            if tier_name not in role_ids or not role_ids[tier_name]:
                role_name = tier.get("role_name")
                if role_name:
                    r = discord.utils.find(lambda x: x.name == role_name, guild.roles)
                    if r:
                        role_ids[tier_name] = r.id
        
        score = promotion_service._compute_score(guild.id, member.id, member, metrics)
        current_index = staff_promo._get_current_tier_index(member, tiers, role_ids)
        
        embed = discord.Embed(title="📈 Your Promotion Progress", color=discord.Color.blue())
        embed.add_field(name="Current Score", value=f"{score*100:.1f}%", inline=True)
        
        if current_index < len(tiers) - 1:
            next_tier = tiers[current_index + 1]
            next_threshold = next_tier.get("threshold", 0)
            percent_away = (next_threshold - score) * 100
            
            embed.add_field(name="Next Tier", value=next_tier.get("name"), inline=True)
            embed.add_field(name="Progress", value=f"{percent_away:.1f}% away", inline=True)
            
            progress_bar = "█" * int(score * 10) + "░" * (10 - int(score * 10))
            embed.add_field(name="Progress Bar", value=f"`{progress_bar}` {score*100:.0f}%", inline=False)
            
            if percent_away <= 5:
                embed.add_field(name="🎯 Almost there!", value="You're very close to your next promotion!", inline=False)
        else:
            embed.add_field(name="Status", value="You've reached the highest tier!", inline=True)
        
        embed.set_thumbnail(url=member.display_avatar.url)
        await message.channel.send(embed=embed)
        return True

    async def handle_staffpromo_promote(self, message: discord.Message) -> bool:
        if not message.author.guild_permissions.administrator:
            await message.channel.send("❌ This command is only for administrators.")
            return True
        
        guild = message.guild
        staff_promo = self.bot.staff_promo
        
        parts = message.content.split()
        if len(parts) < 4:
            await message.channel.send("Usage: `!staffpromo promote @user <tier>`")
            return True
        
        try:
            user_id = int(parts[2].strip("<@!>"))
            target_member = await guild.fetch_member(user_id)
        except:
            await message.channel.send("❌ Could not find user. Use `@user` format.")
            return True
        
        tier_name = " ".join(parts[3:])
        
        config = staff_promo._get_full_config(guild.id)
        success, result = await staff_promo.manual_promote(guild, target_member, tier_name, config)
        
        if success:
            await message.channel.send(f"✅ {target_member.mention} {result}")
        else:
            await message.channel.send(f"❌ {result}")
        return True

    async def handle_staffpromo_demote(self, message: discord.Message) -> bool:
        if not message.author.guild_permissions.administrator:
            await message.channel.send("❌ This command is only for administrators.")
            return True
        
        guild = message.guild
        staff_promo = self.bot.staff_promo
        
        parts = message.content.split()
        if len(parts) < 4:
            await message.channel.send("Usage: `!staffpromo demote @user <tier>`\nUse `none` to remove all staff roles.")
            return True
        
        try:
            user_id = int(parts[2].strip("<@!>"))
            target_member = await guild.fetch_member(user_id)
        except:
            await message.channel.send("❌ Could not find user. Use `@user` format.")
            return True
        
        tier_name = " ".join(parts[3:])
        
        config = staff_promo._get_full_config(guild.id)
        success, result = await staff_promo.manual_demote(guild, target_member, tier_name, config)
        
        if success:
            await message.channel.send(f"✅ {target_member.mention} {result}")
        else:
            await message.channel.send(f"❌ {result}")
        return True

    async def handle_staffpromo_exclude(self, message: discord.Message) -> bool:
        if not message.author.guild_permissions.administrator:
            await message.channel.send("❌ This command is only for administrators.")
            return True
        
        guild = message.guild
        staff_promo = self.bot.staff_promo
        
        parts = message.content.split()
        if len(parts) < 4:
            await message.channel.send("Usage: `!staffpromo exclude add @user` or `!staffpromo exclude remove @user`")
            return True
        
        action = parts[2].lower()
        if action not in ["add", "remove"]:
            await message.channel.send("Usage: `!staffpromo exclude add @user` or `!staffpromo exclude remove @user`")
            return True
        
        try:
            user_id = int(parts[3].strip("<@!>"))
            target_member = await guild.fetch_member(user_id)
        except:
            await message.channel.send("❌ Could not find user. Use `@user` format.")
            return True
        
        config = staff_promo._get_full_config(guild.id)
        settings = config.get("settings", staff_promo._default_settings)
        excluded = settings.get("excluded_users", [])
        
        if action == "add":
            if user_id not in excluded:
                excluded.append(user_id)
                await message.channel.send(f"✅ {target_member.mention} added to exclusion list.")
            else:
                await message.channel.send(f"ℹ️ {target_member.mention} is already excluded.")
        else:
            if user_id in excluded:
                excluded.remove(user_id)
                await message.channel.send(f"✅ {target_member.mention} removed from exclusion list.")
            else:
                await message.channel.send(f"ℹ️ {target_member.mention} is not in the exclusion list.")
        
        settings["excluded_users"] = excluded
        config["settings"] = settings
        dm.update_guild_data(guild.id, "staff_promo_config", config)
        
        return True

class TierManagementView(discord.ui.View):
    def __init__(self, guild: discord.Guild, staff_promo, config: dict):
        super().__init__(timeout=300)  # 5 minute timeout
        self.guild = guild
        self.staff_promo = staff_promo
        self.config = config
    
    @discord.ui.button(label="Add Tier", style=discord.ButtonStyle.green, emoji="➕")
    async def add_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddTierModal(self.guild, self.staff_promo, self.config))
    
    @discord.ui.button(label="Edit Tier", style=discord.ButtonStyle.blue, emoji="✏️")
    async def edit_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditTierModal(self.guild, self.staff_promo, self.config))
    
    @discord.ui.button(label="Remove Tier", style=discord.ButtonStyle.red, emoji="🗑️")
    async def remove_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RemoveTierModal(self.guild, self.staff_promo, self.config))

class AddTierModal(discord.ui.Modal, title="Add Promotion Tier"):
    def __init__(self, guild: discord.Guild, staff_promo, config: dict):
        super().__init__()
        self.guild = guild
        self.staff_promo = staff_promo
        self.config = config
    
    name = discord.ui.TextInput(label="Tier Name", placeholder="e.g., Senior Moderator", required=True)
    threshold = discord.ui.TextInput(label="Threshold (%)", placeholder="e.g., 75 for 75%", required=True)
    role = discord.ui.TextInput(label="Role Name (Optional)", placeholder="Exact role name", required=False)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            threshold_val = float(self.threshold.value) / 100
            if threshold_val < 0 or threshold_val > 1:
                await interaction.response.send_message("❌ Threshold must be between 0 and 100", ephemeral=True)
                return
            
            tiers = self.config.get("tiers", self.staff_promo._default_tiers)
            new_tier = {
                "name": self.name.value,
                "threshold": threshold_val,
                "role_name": self.role.value if self.role.value else None
            }
            tiers.append(new_tier)
            self.config["tiers"] = tiers
            
            # Update the data manager
            from data_manager import dm
            dm.update_guild_data(self.guild.id, "staff_promo_config", self.config)
            
            await interaction.response.send_message(f"✅ Added tier **{self.name.value}** with threshold {self.threshold.value}%", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid threshold value", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error adding tier: {str(e)}", ephemeral=True)

class EditTierModal(discord.ui.Modal, title="Edit Promotion Tier"):
    def __init__(self, guild: discord.Guild, staff_promo, config: dict):
        super().__init__()
        self.guild = guild
        self.staff_promo = staff_promo
        self.config = config
    
    tier_select = discord.ui.TextInput(
        label="Tier Name to Edit", 
        placeholder="Enter exact tier name to edit", 
        required=True
    )
    new_name = discord.ui.TextInput(label="New Tier Name (Optional)", placeholder="Leave blank to keep current", required=False)
    new_threshold = discord.ui.TextInput(label="New Threshold (%) (Optional)", placeholder="Leave blank to keep current", required=False)
    new_role = discord.ui.TextInput(label="New Role Name (Optional)", placeholder="Leave blank to keep current", required=False)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            tiers = self.config.get("tiers", self.staff_promo._default_tiers)
            tier_to_edit = None
            for tier in tiers:
                if tier.get("name", "").lower() == self.tier_select.value.lower():
                    tier_to_edit = tier
                    break
            
            if not tier_to_edit:
                await interaction.response.send_message(f"❌ Tier '{self.tier_select.value}' not found", ephemeral=True)
                return
            
            # Update fields if provided
            if self.new_name.value:
                tier_to_edit["name"] = self.new_name.value
            if self.new_threshold.value:
                threshold_val = float(self.new_threshold.value) / 100
                if threshold_val < 0 or threshold_val > 1:
                    await interaction.response.send_message("❌ Threshold must be between 0 and 100", ephemeral=True)
                    return
                tier_to_edit["threshold"] = threshold_val
            if self.new_role.value is not None:  # Allow empty string to remove role
                tier_to_edit["role_name"] = self.new_role.value if self.new_role.value else None
            
            self.config["tiers"] = tiers
            
            # Update the data manager
            from data_manager import dm
            dm.update_guild_data(self.guild.id, "staff_promo_config", self.config)
            
            await interaction.response.send_message(f"✅ Updated tier **{self.tier_select.value}**", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid threshold value", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error editing tier: {str(e)}", ephemeral=True)

class RemoveTierModal(discord.ui.Modal, title="Remove Promotion Tier"):
    def __init__(self, guild: discord.Guild, staff_promo, config: dict):
        super().__init__()
        self.guild = guild
        self.staff_promo = staff_promo
        self.config = config
    
    tier_select = discord.ui.TextInput(
        label="Tier Name to Remove", 
        placeholder="Enter exact tier name to remove", 
        required=True
    )
    confirm = discord.ui.TextInput(
        label="Type 'CONFIRM' to delete", 
        placeholder="This action cannot be undone", 
        required=True
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            if self.confirm.value != "CONFIRM":
                await interaction.response.send_message("❌ Confirmation failed. Type 'CONFIRM' to delete.", ephemeral=True)
                return
            
            tiers = self.config.get("tiers", self.staff_promo._default_tiers)
            tier_to_remove = None
            for i, tier in enumerate(tiers):
                if tier.get("name", "").lower() == self.tier_select.value.lower():
                    tier_to_remove = i
                    break
            
            if tier_to_remove is None:
                await interaction.response.send_message(f"❌ Tier '{self.tier_select.value}' not found", ephemeral=True)
                return
            
            removed_tier = tiers.pop(tier_to_remove)
            self.config["tiers"] = tiers
            
            # Update the data manager
            from data_manager import dm
            dm.update_guild_data(self.guild.id, "staff_promo_config", self.config)
            
            await interaction.response.send_message(f"✅ Removed tier **{removed_tier.get('name')}**", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error removing tier: {str(e)}", ephemeral=True)

    async def handle_staffpromo_tiers(self, message: discord.Message) -> bool:
        """Handle !staffpromo tiers command - Interactive tier management"""
        if not message.author.guild_permissions.administrator:
            await message.channel.send("❌ This command is only for administrators.")
            return True
        
        guild = message.guild
        staff_promo = self.bot.staff_promo
        config = staff_promo._get_full_config(guild.id)
        tiers = config.get("tiers", staff_promo._default_tiers)
        role_ids = config.get("roles_by_tier", {})
        
        # Create embed showing current tiers
        embed = discord.Embed(
            title="⚙️ Promotion Tiers Management",
            description="Manage promotion tiers for this server",
            color=discord.Color.blue()
        )
        
        if tiers:
            tiers_text = ""
            for tier in sorted(tiers, key=lambda t: t.get("threshold", 0)):
                name = tier.get("name", "Unknown")
                threshold = int(tier.get("threshold", 0) * 100)
                role_id = role_ids.get(name)
                role_mention = f"<@&{role_id}>" if role_id and guild.get_role(role_id) else "Not set"
                tiers_text += f"**{name}**: {threshold}% → {role_mention}\n"
            embed.add_field(name="Current Tiers", value=tiers_text or "None", inline=False)
        else:
            embed.add_field(name="Current Tiers", value="No tiers configured", inline=False)
        
        # Create view with buttons
        view = TierManagementView(guild, staff_promo, config)
        await message.channel.send(embed=embed, view=view)
        return True

    async def handle_staffpromo_roles(self, message: discord.Message) -> bool:
        if not message.author.guild_permissions.administrator:
            await message.channel.send("❌ This command is only for administrators.")
            return True
        
        guild = message.guild
        staff_promo = self.bot.staff_promo
        
        parts = message.content.split()
        if len(parts) < 5:
            await message.channel.send(
                "Usage: `!staffpromo roles add <tier> @role` or `!staffpromo roles remove <tier> @role`\n"
                "Example: `!staffpromo roles add Moderator @Moderator`"
            )
            return True
        
        action = parts[2].lower()
        if action not in ["add", "remove", "list"]:
            await message.channel.send("Use: `add`, `remove`, or `list`")
            return True
        
        if action == "list":
            config = staff_promo._get_full_config(guild.id)
            role_ids = config.get("roles_by_tier", {})
            tiers = config.get("tiers", staff_promo._default_tiers)
            
            embed = discord.Embed(title="🔗 Role Mappings", color=discord.Color.orange())
            for tier in tiers:
                tier_name = tier.get("name")
                rid = role_ids.get(tier_name)
                if rid:
                    role = guild.get_role(rid)
                    role_mention = role.mention if role else f"Role ID: {rid}"
                else:
                    role_mention = "Not set"
                embed.add_field(name=tier_name, value=role_mention, inline=True)
            
            await message.channel.send(embed=embed)
            return True
        
        tier_name = parts[3]
        
        try:
            role_id = int(parts[4].strip("<@&>"))
            target_role = guild.get_role(role_id)
        except:
            await message.channel.send("❌ Could not find role. Use `@role` format.")
            return True
        
        config = staff_promo._get_full_config(guild.id)
        role_ids = config.get("roles_by_tier", {})
        
        tiers = config.get("tiers", staff_promo._default_tiers)
        valid_tiers = [t.get("name").lower() for t in tiers]
        
        if tier_name.lower() not in valid_tiers:
            valid_list = ", ".join([t.get("name") for t in tiers])
            await message.channel.send(f"❌ Invalid tier. Valid tiers: {valid_list}")
            return True
        
        if action == "add":
            role_ids[tier_name] = target_role.id
            await message.channel.send(f"✅ Mapped **{tier_name}** to {target_role.mention}")
        else:
            if tier_name in role_ids:
                del role_ids[tier_name]
            await message.channel.send(f"✅ Removed mapping for **{tier_name}**")
        
        config["roles_by_tier"] = role_ids
        dm.update_guild_data(guild.id, "staff_promo_config", config)
        
        return True

    async def handle_staffpromo_review(self, message: discord.Message) -> bool:
        guild = message.guild
        staff_promo = self.bot.staff_promo
        
        parts = message.content.split()
        
        if message.author.guild_permissions.administrator:
            if len(parts) >= 3:
                action = parts[2].lower()
                if action in ["approve", "reject"]:
                    return await self.handle_promotion_decision(message, action, parts)
            
            config = staff_promo._get_full_config(guild.id)
            pending = config.get("pending_reviews", [])
            
            if not pending:
                await message.channel.send("ℹ️ No pending promotion reviews.")
                return True
            
            embed = discord.Embed(title="📋 Pending Promotion Reviews", color=discord.Color.yellow())
            for review in pending:
                user_id = review.get("user_id")
                tier_name = review.get("tier_name")
                score = review.get("score", 0)
                embed.add_field(name=f"User ID: {user_id}", value=f"Tier: {tier_name}, Score: {score*100:.1f}%", inline=False)
            
            embed.add_field(name="Actions", value="`!staffpromo approve @user` or `!staffpromo reject @user`", inline=False)
            await message.channel.send(embed=embed)
            return True
        else:
            config = staff_promo._get_full_config(guild.id)
            pending = config.get("pending_reviews", [])
            
            user_pending = [r for r in pending if r.get("user_id") == message.author.id]
            if user_pending:
                for review in user_pending:
                    await message.channel.send(f"📋 Your promotion to **{review.get('tier_name')}** is pending review. Score: {review.get('score', 0)*100:.1f}%")
            else:
                await message.channel.send("ℹ️ You have no pending reviews.")
            return True

    async def handle_promotion_decision(self, message: discord.Message, decision: str, parts: list) -> bool:
        guild = message.guild
        staff_promo = self.bot.staff_promo
        
        if len(parts) < 3:
            await message.channel.send(f"Usage: `!staffpromo {decision} @user`")
            return True
        
        try:
            user_id = int(parts[2].strip("<@!>"))
            target_member = await guild.fetch_member(user_id)
        except:
            await message.channel.send("❌ Could not find user. Use `@user` format.")
            return True
        
        config = staff_promo._get_full_config(guild.id)
        pending = config.get("pending_reviews", [])
        
        review_to_remove = None
        target_tier_name = None
        for review in pending:
            if review.get("user_id") == user_id:
                review_to_remove = review
                target_tier_name = review.get("tier_name")
                break
        
        if not review_to_remove:
            await message.channel.send(f"❌ No pending review found for that user.")
            return True
        
        pending = [r for r in pending if r.get("user_id") != user_id]
        config["pending_reviews"] = pending
        dm.update_guild_data(guild.id, "staff_promo_config", config)
        
        if decision == "approve":
            tiers = config.get("tiers", staff_promo._default_tiers)
            role_ids = dict(config.get("roles_by_tier", {}))
            
            for tier in tiers:
                tier_name = tier.get("name")
                if tier_name not in role_ids or not role_ids[tier_name]:
                    role_name = tier.get("role_name")
                    if role_name:
                        r = discord.utils.find(lambda x: x.name == role_name, guild.roles)
                        if r:
                            role_ids[tier_name] = r.id
            
            tier = next((t for t in tiers if t.get("name") == target_tier_name), None)
            if tier:
                current_index = staff_promo._get_current_tier_index(target_member, tiers, role_ids)
                settings = config.get("settings", staff_promo._default_settings)
                await staff_promo._promote_member(guild, target_member, tier, tiers, role_ids, current_index, settings, config)
                await message.channel.send(f"✅ Approved! {target_member.mention} promoted to **{target_tier_name}**")
        else:
            try:
                await target_member.send(f"❌ Your promotion to **{target_tier_name}** was rejected.")
            except:
                pass
            await message.channel.send(f"❌ Rejected promotion for {target_member.mention}")
        
        return True

    async def handle_staffpromo_requirements(self, message: discord.Message) -> bool:
        guild = message.guild
        staff_promo = self.bot.staff_promo
        
        config = staff_promo._get_full_config(guild.id)
        requirements = config.get("tier_requirements", staff_promo._default_tier_requirements)
        tiers = config.get("tiers", staff_promo._default_tiers)
        
        embed = discord.Embed(title="📋 Tier Requirements", color=discord.Color.blue())
        
        for tier in tiers:
            tier_name = tier.get("name")
            tier_reqs = requirements.get(tier_name, {})
            
            if tier_reqs:
                req_text = "\n".join([f"• {k}: {v}" for k, v in tier_reqs.items()])
            else:
                req_text = "No requirements"
            
            embed.add_field(name=f"{tier_name} ({int(tier.get('threshold', 0)*100)}%)", value=req_text, inline=False)
        
        await message.channel.send(embed=embed)
        return True

    async def handle_staffpromo_bonuses(self, message: discord.Message) -> bool:
        guild = message.guild
        staff_promo = self.bot.staff_promo
        
        config = staff_promo._get_full_config(guild.id)
        bonuses = config.get("achievement_bonuses", staff_promo._default_achievement_bonuses)
        
        embed = discord.Embed(title="🏆 Achievement Score Bonuses", color=discord.Color.gold())
        
        total_bonus = 1.0
        for ach_name, multiplier in bonuses.items():
            bonus_pct = (multiplier - 1) * 100
            embed.add_field(name=ach_name, value=f"+{bonus_pct:.0f}% score multiplier", inline=True)
            total_bonus += (multiplier - 1)
        
        embed.add_field(name="Total Max Bonus", value=f"{((total_bonus - 1) * 100):.0f}%", inline=False)
        
        await message.channel.send(embed=embed)
        return True
