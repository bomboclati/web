import discord
import json
import asyncio
import time
from datetime import datetime, timezone
import datetime as dt
from typing import List, Dict, Any, Tuple, Optional
from data_manager import dm
from logger import logger
from utils.deduplicator import deduplicator

# Color name to integer mapping for embeds
COLOR_MAP = {
    "gold": 0xFFD700,
    "yellow": 0xFFD700,
    "blue": 0x3498DB,
    "red": 0xE74C3C,
    "green": 0x2ECC71,
    "purple": 0x9B59B6,
    "orange": 0xE67E22,
    "pink": 0xFF69B4,
    "white": 0xFFFFFF,
    "black": 0x000000,
    "gray": 0x95A5A6,
    "grey": 0x95A5A6,
    "cyan": 0x1ABC9C,
    "magenta": 0xE91E63,
    "brown": 0x795548,
    "navy": 0x34495E,
    "lime": 0xCDDC39,
    "teal": 0x008080,
}

def parse_color(color_val):
    """Convert color value to valid Discord embed color."""
    if isinstance(color_val, int):
        return color_val
    if isinstance(color_val, str):
        # Try color name first
        color_lower = color_val.lower()
        if color_lower in COLOR_MAP:
            return COLOR_MAP[color_lower]
        # Try hex string
        try:
            if color_val.startswith("#"):
                return int(color_val[1:], 16)
            return int(color_val, 16)
        except ValueError:
            pass
    # Default fallback
    return 0x3498DB

COMMAND_SCHEMA = {
    "type": "object",
    "properties": {
        "command_type": {
            "type": "string",
            "enum": ["application_status", "appeal_status", "help_embed", "simple", "economy_daily", "economy_balance", "achievements", "titles", "leaderboard", "staffpromo_status", "staffpromo_leaderboard", "staffpromo_progress", "staffpromo_tiers", "staffpromo_roles", "staffpromo_review", "list_triggers", "set_title", "achievements_leaderboard", "help_all"]
        },
        "content": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "parameters": {"type": "object"}
                },
                "required": ["name"]
            }
        }
    },
    "required": ["command_type"]
}

def validate_command_json(data: dict) -> Tuple[bool, str]:
    """Validate custom command JSON against schema. Returns (valid, error_message)."""
    if not isinstance(data, dict):
        return False, "Command must be a JSON object"
    
    command_type = data.get("command_type")
    if command_type is None:
        return False, "Missing required field: command_type"
    
    allowed_types = COMMAND_SCHEMA["properties"]["command_type"]["enum"]
    if command_type not in allowed_types:
        return False, f"Invalid command_type: {command_type}. Allowed: {allowed_types}"
    
    if "actions" in data:
        if not isinstance(data["actions"], list):
            return False, "actions must be an array"
        for i, action in enumerate(data["actions"]):
            if not isinstance(action, dict):
                return False, f"Action {i} must be an object"
            if "name" not in action:
                return False, f"Action {i} missing required field: name"
            if action["name"] not in ActionHandler.ALLOWED_ACTIONS:
                return False, f"Disallowed action in command: {action['name']}"
    
    return True, ""

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
            data = json.loads(new_code)
        except json.JSONDecodeError:
            await interaction.response.send_message("❌ Invalid JSON! Please enter valid JSON.", ephemeral=True)
            return
        
        valid, error_msg = validate_command_json(data)
        if not valid:
            await interaction.response.send_message(f"❌ {error_msg}", ephemeral=True)
            return
        
        custom_cmds = dm.get_guild_data(interaction.guild.id, "custom_commands", {})
        custom_cmds[self.cmd_name] = new_code
        dm.update_guild_data(interaction.guild.id, "custom_commands", custom_cmds)
        
        await interaction.response.edit_message(content=f"? Command `!{self.cmd_name}` updated!", view=None)

class ActionHandler:
    ALLOWED_ACTIONS = {
        "send_message", "send_embed", "add_role", "remove_role",
        "create_channel", "delete_channel", "create_role", "delete_role",
        "create_category", "edit_channel", "edit_role", "assign_role", "remove_role",
        "create_prefix_command", "delete_prefix_command", "setup_welcome",
        "setup_logging", "setup_verification", "setup_economy", "setup_leveling",
        "setup_tickets", "setup_applications", "setup_appeals", "setup_moderation",
        "send_dm", "create_invite", "schedule_ai_action", "ping",
        "kick_user", "ban_user", "timeout_user",
        "delete_role", "delete_channel", "announce", "poll", "give_points", "remove_points", "warn_user",
        "create_verify_system", "create_tickets_system", "create_applications_system", "create_appeals_system",
        "create_welcome_system", "create_staff_system", "create_leveling_system", "create_economy_system",
        "mute_user", "unmute_user", "deafen_user", "set_nickname", "slowmode", "lock_channel", "unlock_channel",
        "send_message", "reply_message", "add_reaction", "edit_channel_name", "edit_role_name",
        "change_role_color", "move_channel", "clone_channel", "create_thread", "pin_message", "unpin_message",
        "set_topic", "delete_messages"
    }
    
    def __init__(self, bot):
        self.bot = bot
        self._action_log = []
        self._setup_id = None
        self._artifacts = []
        self._guild_context = None
    
    def set_guild_context(self, guild):
        """Set the guild context for help and other commands"""
        self._guild_context = guild

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
            
            if name not in self.ALLOWED_ACTIONS:
                logger.warning("Blocked disallowed action: %s", name)
                results.append((name, False))
                return {"results": results, "rolled_back": [], "failed_at": i, "failed_action": name, "error": f"Action not allowed: {name}", "success": False}
            
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
                elif success is False:
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

        if not guild.me.guild_permissions.manage_channels:
            logger.error("Bot lacks manage_channels permission in guild %s", guild.id)
            return False, None

        # Check for duplicate channel creation using deduplicator
        dedup_key = f"channel_{guild.id}_{name}"
        if not deduplicator.should_send(dedup_key, interval=5):
            logger.info("Channel '%s' creation skipped (duplicate request)", name)
            return True, None

        existing = discord.utils.get(guild.channels, name=name)
        if existing:
            logger.info("Channel '%s' already exists, skipping creation", name)
            return True, None

        category = None
        if category_name:
            category = discord.utils.get(guild.categories, name=category_name)
            if not category:
                # Also deduplicate category creation
                cat_dedup_key = f"category_{guild.id}_{category_name}"
                if deduplicator.should_send(cat_dedup_key, interval=5):
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
        # Ordered most-specific first to prevent substring false matches
        channel_rules = [
            # Most specific rules first
            ("apply-public", {"allowed": [], "denied": []}),
            ("bot-logs", {"allowed": ["Moderator", "Admin"], "denied": ["@everyone"]}),
            ("ticket-queue", {"allowed": ["Moderator", "Support"], "denied": ["@everyone"]}),
            
            # Staff/Admin channels - only staff can see
            ("staff", {"allowed": ["Moderator", "Admin", "Administrator"], "denied": ["@everyone"]}),
            ("modmail", {"allowed": ["Moderator", "Admin", "Administrator"], "denied": ["@everyone"]}),
            ("admin", {"allowed": ["Administrator", "Admin"], "denied": ["@everyone"]}),
            ("logs", {"allowed": ["Moderator", "Admin", "Administrator"], "denied": ["@everyone"]}),
            
            # Applications - hidden from regular users until they apply
            ("applications", {"allowed": ["Moderator", "Admin", "Administrator"], "denied": ["@everyone"]}),
            ("apply", {"allowed": ["Moderator", "Admin"], "denied": ["@everyone"]}),
            
            # Verification - new users need to verify
            ("verify", {"allowed": [], "denied": []}),
            
            # General channels - everyone can see
            ("general", {"allowed": [], "denied": []}),
            ("chat", {"allowed": [], "denied": []}),
            ("talk", {"allowed": [], "denied": []}),
            
            # Public channels - everyone can see
            ("announcements", {"allowed": [], "denied": []}),
            ("rules", {"allowed": [], "denied": []}),
            ("welcome", {"allowed": [], "denied": []}),
            ("suggestions", {"allowed": [], "denied": []}),
            
            # Support channels
            ("tickets", {"allowed": ["Moderator", "Support"], "denied": ["@everyone"]}),
            
            # Media channels
            ("media", {"allowed": [], "denied": []}),
            ("art", {"allowed": [], "denied": []}),
            ("gaming", {"allowed": [], "denied": []}),
            ("vc", {"allowed": [], "denied": []}),
            
            # Voice channels - everyone can join
            ("voice", {"allowed": [], "denied": []}),
            ("lounge", {"allowed": [], "denied": []}),
        ]
        
        # Find matching rule (most specific first)
        for keyword, perms in channel_rules:
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
            title=f"📢 {channel_name}",
            description=guide["description"],
            color=discord.Color.blue()
        )
        
        cmd_list = "\n".join([f". {cmd}" for cmd in guide["commands"]])
        embed.add_field(name="Available Commands", value=cmd_list, inline=False)
        
        await channel.send(embed=embed)

    async def action_create_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        guild = interaction.guild
        name = params.get("name")

        if not guild.me.guild_permissions.manage_roles:
            logger.error("Bot lacks manage_roles permission in guild %s", guild.id)
            return False, None

        # Check for duplicate role creation using deduplicator
        dedup_key = f"role_{guild.id}_{name}"
        if not deduplicator.should_send(dedup_key, interval=5):
            logger.info("Role '%s' creation skipped (duplicate request)", name)
            return True, None

        existing = discord.utils.get(guild.roles, name=name)
        if existing:
            logger.info("Role '%s' already exists, skipping creation", name)
            return True, None

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
        
        description = f"Custom role: {role_name}"
        for key, desc in guide_content.items():
            if key in name_lower:
                description = desc
                break
        
        embed = discord.Embed(
            title=f"🎭 Role: {role_name}",
            description=description,
            color=discord.Color.blue()
        )
        
        perm_list = [f"View Channels", "Send Messages"] if role_perms.get("view_channel") else []
        if role_perms.get("moderate_members"): perm_list.append("Moderate Members")
        if role_perms.get("kick_members"): perm_list.append("Kick Members")
        if role_perms.get("ban_members"): perm_list.append("Ban Members")
        if role_perms.get("manage_channels"): perm_list.append("Manage Channels")
        
        if perm_list:
            embed.add_field(name="Permissions", value="\n".join([f"? {p}" for p in perm_list]), inline=False)
        
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def action_assign_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Assign a role to a user. Supports lookup by name OR id for both role and user."""
        guild = interaction.guild
        
        # --- Resolve role (by id first, then by name) ---
        role = None
        role_id = params.get("role_id")
        role_name = params.get("role_name") or params.get("name")
        if role_id:
            try:
                role = guild.get_role(int(role_id))
            except (TypeError, ValueError):
                role = None
        if not role and role_name:
            role = discord.utils.find(lambda r: r.name.lower() == str(role_name).lower(), guild.roles)
        if not role and role_name:
            role = discord.utils.find(lambda r: str(role_name).lower() in r.name.lower(), guild.roles)
        
        # --- Resolve member (by id first, then by name/mention) ---
        member = None
        user_id = params.get("user_id") or params.get("user")
        username = params.get("username") or params.get("user_name")
        if user_id:
            try:
                uid = int(str(user_id).strip().lstrip("<@!").rstrip(">"))
                member = guild.get_member(uid) or await guild.fetch_member(uid)
            except (TypeError, ValueError, discord.NotFound, discord.HTTPException):
                member = None
        if not member and username:
            search = str(username).lstrip("@").lower()
            member = discord.utils.find(
                lambda m: m.name.lower() == search or m.display_name.lower() == search,
                guild.members
            )
        
        if not role:
            logger.error("assign_role: could not find role. role_id=%s role_name=%s", role_id, role_name)
            return False, None
        if not member:
            logger.error("assign_role: could not find member. user_id=%s username=%s", user_id, username)
            return False, None
        
        await member.add_roles(role)
        logger.info("Assigned role %s to %s", role.name, member.display_name)
        return True, {"action": "remove_role", "user_id": member.id, "role_id": role.id}


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
        color = parse_color(params.get("color", 0x3498db))
        buttons = params.get("buttons", [])  # List of {"label": ..., "type": ..., "style": ...}
        fields = params.get("fields", [])

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) or interaction.channel
        embed = discord.Embed(title=title, description=description, color=color)

        for field in fields:
            embed.add_field(name=field.get("name", ""), value=field.get("value", ""), inline=field.get("inline", False))

        view = None
        if buttons:
            from modules.auto_setup import VerifyButton, AcceptRulesButton, CreateTicketButton, ApplyStaffButton, SuggestionButton
            view = discord.ui.View(timeout=None)
            for btn_def in buttons:
                label = btn_def.get("label", "Click")
                btn_type = btn_def.get("type", "custom")
                style_str = btn_def.get("style", "primary")
                style_map = {"primary": discord.ButtonStyle.primary, "success": discord.ButtonStyle.success, "danger": discord.ButtonStyle.danger, "secondary": discord.ButtonStyle.secondary}
                style = style_map.get(style_str, discord.ButtonStyle.primary)

                if btn_type == "verify":
                    role = discord.utils.get(interaction.guild.roles, name="Verified") or discord.utils.get(interaction.guild.roles, name="Member")
                    sub_view = VerifyButton(interaction.guild.id, role.id if role else 0)
                    for item in sub_view.children:
                        view.add_item(item)
                elif btn_type == "ticket":
                    sub_view = CreateTicketButton(interaction.guild.id, channel.id)
                    for item in sub_view.children:
                        view.add_item(item)
                elif btn_type == "apply_staff":
                    sub_view = ApplyStaffButton(interaction.guild.id)
                    for item in sub_view.children:
                        view.add_item(item)
                elif btn_type == "accept_rules":
                    sub_view = AcceptRulesButton(interaction.guild.id)
                    for item in sub_view.children:
                        view.add_item(item)
                elif btn_type == "suggestion":
                    sub_view = SuggestionButton(interaction.guild.id)
                    for item in sub_view.children:
                        view.add_item(item)
                else:
                    # Generic button with a response message
                    response_msg = btn_def.get("response", f"You clicked **{label}**!")
                    btn = discord.ui.Button(label=label, style=style)
                    async def make_callback(msg):
                        async def callback(it: discord.Interaction):
                            await it.response.send_message(msg, ephemeral=True)
                        return callback
                    btn.callback = await make_callback(response_msg)
                    view.add_item(btn)

        msg = await channel.send(embed=embed, view=view)
        return True, {"action": "delete_message", "channel_id": channel.id, "message_id": msg.id}

    async def action_send_dm(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Sends a DM to a user with deduplication."""
        user_id = params.get("user_id")
        username = params.get("username")
        content = params.get("content")
        embed_data = params.get("embed")
        
        # Support both user_id and username
        if not user_id and username:
            # Strip @ prefix if present
            if username.startswith("@"):
                username = username[1:]
            
            # Try by name first
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                # Try by nick
                member = discord.utils.get(interaction.guild.members, nick=username)
            if not member:
                # Try by display name
                member = discord.utils.get(interaction.guild.members, display_name=username)
            if member:
                user_id = member.id
        
        if not user_id and username:
            # Try fetching by ID if username looks like a number
            try:
                user_id = int(username)
            except ValueError:
                pass
        
        if not user_id:
            return False, None
            
        # Deduplication check
        dedup_key = f"dm_{user_id}_{hash(content or '')}_{hash(str(embed_data) if embed_data else '')}"
        if not deduplicator.should_send(dedup_key):
            logger.info(f"Deduplicated DM to user {user_id}")
            return True, None # Still return true as we "handled" it by skipping

        user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        if not user:
            return False, None
            
        embed = None
        if embed_data:
            embed = discord.Embed(
                title=embed_data.get("title"),
                description=embed_data.get("description"),
                color=parse_color(embed_data.get("color", "blue"))
            )
            for field in embed_data.get("fields", []):
                embed.add_field(name=field.get("name"), value=field.get("value"), inline=field.get("inline", False))

        try:
            await user.send(content=content, embed=embed)
            return True, None # Rollback for DM is hard, usually not needed for simple notifications
        except discord.Forbidden:
            logger.warning(f"Failed to send DM to {user_id}: DMs disabled")
            return False, None
        except Exception as e:
            logger.error(f"Error sending DM: {e}")
            return False, None

    async def action_ping(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Pings a user and shows their latency/online status."""
        user_id = params.get("user_id")
        username = params.get("username")
        
        if not user_id and not username:
            return False, None
        
        if username:
            # Strip @ prefix if present
            if username.startswith("@"):
                username = username[1:]
            
            # Try by name first
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                # Try by nick
                member = discord.utils.get(interaction.guild.members, nick=username)
            if not member:
                # Try by display name
                member = discord.utils.get(interaction.guild.members, display_name=username)
            if member:
                user_id = member.id
        
        if not user_id and username:
            # Try fetching by ID if username looks like a number
            try:
                user_id = int(username)
            except ValueError:
                pass
        
        if not user_id:
            return False, None
        
        member = interaction.guild.get_member(user_id) if user_id else None
        if not member:
            return False, None
        
        latency = round(self.bot.latency * 1000, 1) if self.bot.latency else 0
        status_emoji = str(member.status).replace("online", "\\U0001f7e2").replace("idle", "\\U0001f7e1").replace("dnd", "\\U0001f7e0").replace("offline", "\\U0001f507")
        status_text = f"Status: {member.status}"
        
        embed = discord.Embed(
            title=f"@ {member.display_name}",
            description=f"{status_text}\nBot Latency: {latency}ms\nJoined: {member.joined_at.strftime('%Y-%m-%d') if member.joined_at else 'Unknown'}",
            color=member.color or discord.Color.blurple()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        
        await interaction.channel.send(f"{member.mention}", embed=embed, delete_after=30)
        return True, None

    async def action_post_documentation(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Posts a comprehensive, multi-section documentation embed for a newly created system."""
        channel_name = params.get("channel")
        channel = discord.utils.get(interaction.guild.channels, name=channel_name) or interaction.channel
        
        title = params.get("title", "System Documentation")
        description = params.get("description", "")
        sections = params.get("sections", [])
        footer = params.get("footer", "")
        color = parse_color(params.get("color", 0x5865F2))
        
        embed = discord.Embed(title=title, description=description, color=color)
        
        for section in sections:
            section_title = section.get("title", "")
            section_content = section.get("content", "")
            if section_title and section_content:
                embed.add_field(name=section_title, value=section_content, inline=False)
        
        if footer:
            embed.set_footer(text=footer)
        
        embed.timestamp = datetime.now(timezone.utc)
        
        msg = await channel.send(embed=embed)
        return True, {"action": "delete_message", "channel_id": channel.id, "message_id": msg.id}

    # --- Specialized Systems ---

    async def action_setup_staff_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        from modules.staff_system import StaffSystem
        system = StaffSystem(self.bot)
        result = await system.setup(interaction, params)
        return bool(result) if result is not None else True, {"action": "undo_staff_system", "guild_id": interaction.guild.id}

    async def action_setup_economy(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        from modules.economy import Economy
        system = Economy(self.bot)
        result = await system.setup(interaction, params)
        return bool(result) if result is not None else True, {"action": "undo_economy", "guild_id": interaction.guild.id}

    async def action_setup_trigger_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        from modules.trigger_roles import TriggerRoles
        system = TriggerRoles(self.bot)
        result = await system.setup(interaction, params)
        return bool(result) if result is not None else True, {"action": "undo_trigger_role", "guild_id": interaction.guild.id}

    # --- Setup System Actions (Auto-Setup with Buttons) ---
    
    async def action_setup_verification(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup verification system with button embed."""
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_verification(interaction, params)
        return result, {"action": "undo_verification", "guild_id": interaction.guild.id}
    
    async def action_setup_tickets(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup ticket system with button embed."""
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_tickets(interaction, params)
        return result, {"action": "undo_tickets", "guild_id": interaction.guild.id}
    
    async def action_setup_applications(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup applications system with button embed."""
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_applications(interaction, params)
        return result, {"action": "undo_applications", "guild_id": interaction.guild.id}
    
    async def action_setup_appeals(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup appeals system with button embed."""
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_appeals(interaction, params)
        return result, {"action": "undo_appeals", "guild_id": interaction.guild.id}
    
    async def action_setup_moderation(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup moderation logging system."""
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_moderation(interaction, params)
        return result, {"action": "undo_moderation", "guild_id": interaction.guild.id}
    
    async def action_setup_logging(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup server logging system."""
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_logging(interaction, params)
        return result, {"action": "undo_logging", "guild_id": interaction.guild.id}
    
    async def action_setup_leveling(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup leveling/XP system."""
        from modules.gamification import Gamification
        system = Gamification(self.bot)
        result = await system.setup(interaction, params)
        return result, {"action": "undo_leveling", "guild_id": interaction.guild.id}

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

    async def action_create_invite(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Creates an invite link for a channel or the server."""
        channel = params.get("channel")
        max_uses = params.get("max_uses", 0)
        max_age = params.get("max_age", 86400)  # 24 hours by default
        temporary = params.get("temporary", False)
        
        target_channel = None
        if channel:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel)
            if not target_channel:
                target_channel = interaction.channel
        else:
            target_channel = interaction.channel
        
        try:
            invite = await target_channel.create_invite(
                max_uses=max_uses if max_uses else None,
                max_age=max_age if max_age else None,
                temporary=temporary
            )
            return True, {"invite_url": invite.url, "channel_id": target_channel.id}
        except discord.Forbidden:
            return False, {"error": "Missing permission to create invite"}
        except Exception as e:
            logger.error(f"Error creating invite: {e}")
            return False, None

    async def action_kick_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Kicks a user from the server."""
        user_id = params.get("user_id")
        username = params.get("username")
        reason = params.get("reason", "Kicked via bot command")
        
        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, display_name=username)
            if member:
                user_id = member.id
        
        if not user_id:
            return False, None
        
        member = interaction.guild.get_member(user_id)
        if not member:
            return False, None
        
        try:
            await member.kick(reason=reason)
            return True, {"user_id": user_id, "action": "kick"}
        except discord.Forbidden:
            return False, {"error": "Missing permission to kick"}
        except Exception as e:
            logger.error(f"Error kicking user: {e}")
            return False, None

    async def action_ban_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Bans a user from the server."""
        user_id = params.get("user_id")
        username = params.get("username")
        reason = params.get("reason", "Banned via bot command")
        delete_days = params.get("delete_messages_days", 0)
        
        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, display_name=username)
            if member:
                user_id = member.id
        
        if not user_id:
            return False, None
        
        member = interaction.guild.get_member(user_id)
        if not member:
            # Try to fetch user
            try:
                member = await interaction.guild.fetch_member(user_id)
            except:
                pass
        
        try:
            if member:
                await member.ban(reason=reason, delete_message_days=delete_days)
            else:
                await interaction.guild.ban(discord.Object(user_id), reason=reason, delete_message_days=delete_days)
            return True, {"user_id": user_id, "action": "ban"}
        except discord.Forbidden:
            return False, {"error": "Missing permission to ban"}
        except Exception as e:
            logger.error(f"Error banning user: {e}")
            return False, None

    async def action_timeout_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Times out a user (modifies their communication timeout)."""
        user_id = params.get("user_id")
        username = params.get("username")
        duration = params.get("duration", 600)  # seconds, default 10 minutes
        reason = params.get("reason", "Timed out via bot command")
        
        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, display_name=username)
            if member:
                user_id = member.id
        
        if not user_id:
            return False, None
        
        member = interaction.guild.get_member(user_id)
        if not member:
            return False, None
        
        try:
            import datetime
            timeout_until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration)
            await member.timeout(timeout_until, reason=reason)
            return True, {"user_id": user_id, "duration": duration, "action": "timeout"}
        except discord.Forbidden:
            return False, {"error": "Missing permission to timeout"}
        except Exception as e:
            logger.error(f"Error timeout user: {e}")
            return False, None

    async def action_delete_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Deletes a role from the server."""
        role_name = params.get("role_name")
        
        if not role_name:
            return False, None
        
        role = discord.utils.get(interaction.guild.roles, name=role_name)
        if not role:
            return False, None
        
        try:
            await role.delete()
            return True, {"role_name": role_name}
        except discord.Forbidden:
            return False, {"error": "Missing permission to delete role"}
        except Exception as e:
            logger.error(f"Error deleting role: {e}")
            return False, None

    async def action_delete_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Deletes a channel from the server."""
        channel_name = params.get("channel_name") or params.get("name")
        
        if not channel_name:
            return False, None
        
        channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not channel:
            return False, None
        
        try:
            await channel.delete()
            return True, {"channel_name": channel_name}
        except discord.Forbidden:
            return False, {"error": "Missing permission to delete channel"}
        except Exception as e:
            logger.error(f"Error deleting channel: {e}")
            return False, None

    async def action_announce(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Makes an announcement in a channel."""
        channel_name = params.get("channel") or params.get("channel_name")
        title = params.get("title", "Announcement")
        content = params.get("content", "")
        color = params.get("color", "#3498db")
        
        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel
        
        embed = discord.Embed(
            title=title,
            description=content,
            color=parse_color(color)
        )
        
        try:
            await target_channel.send(embed=embed)
            return True, {"channel_id": target_channel.id}
        except Exception as e:
            logger.error(f"Error sending announcement: {e}")
            return False, None

    async def action_poll(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Creates a poll with options."""
        channel_name = params.get("channel") or params.get("channel_name")
        question = params.get("question", "Poll")
        options = params.get("options", [])  # list of option strings
        duration = params.get("duration", 300)  # seconds
        
        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel
        
        if not options:
            options = ["Yes", "No"]
        
        options_text = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(options)])
        embed = discord.Embed(
            title=f"? {question}",
            description=options_text,
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f"Poll ends in {duration//60} minutes")
        
        try:
            msg = await target_channel.send(embed=embed)
            for i in range(len(options)):
                await msg.add_reaction(f"{i+1}\u20e3")
            return True, {"message_id": msg.id, "options": options}
        except Exception as e:
            logger.error(f"Error creating poll: {e}")
            return False, None

    async def action_give_points(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Gives economy points to a user."""
        user_id = params.get("user_id")
        username = params.get("username")
        points = params.get("points", 100)
        
        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if member:
                user_id = member.id
        
        if not user_id:
            return False, None
        
        try:
            from modules.economy import economy
            if economy:
                economy.add_balance(user_id, points)
            return True, {"user_id": user_id, "points": points, "action": "give_points"}
        except Exception as e:
            logger.error(f"Error giving points: {e}")
            return False, None

    async def action_remove_points(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Removes economy points from a user."""
        user_id = params.get("user_id")
        username = params.get("username")
        points = params.get("points", 100)
        
        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if member:
                user_id = member.id
        
        if not user_id:
            return False, None
        
        try:
            from modules.economy import economy
            if economy:
                economy.add_balance(user_id, -points)
            return True, {"user_id": user_id, "points": points, "action": "remove_points"}
        except Exception as e:
            logger.error(f"Error removing points: {e}")
            return False, None

    async def action_warn_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Warns a user (moderation)."""
        user_id = params.get("user_id")
        username = params.get("username")
        reason = params.get("reason", "Warning issued")
        
        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if member:
                user_id = member.id
        
        if not user_id:
            return False, None
        
        member = interaction.guild.get_member(user_id)
        if not member:
            return False, None
        
        try:
            dm_channel = member.dm_channel if hasattr(member, 'dm_channel') else None
            if not dm_channel:
                dm_channel = await member.create_dm()
            
            embed = discord.Embed(
                title="? You have been warned",
                description=f"**Reason:** {reason}\n\nPlease follow the server rules.",
                color=discord.Color.orange()
            )
            await dm_channel.send(embed=embed)
            
            # Log warning
            guild_id = interaction.guild.id
            from data_manager import dm
            warnings = dm.get_guild_data(guild_id, "warnings", {})
            if str(user_id) not in warnings:
                warnings[str(user_id)] = []
            warnings[str(user_id)].append({
                "reason": reason,
                "timestamp": time.time(),
                "moderator": interaction.user.id
            })
            dm.update_guild_data(guild_id, "warnings", warnings)
            
            return True, {"user_id": user_id, "reason": reason}
        except Exception as e:
            logger.error(f"Error warning user: {e}")
            return False, None

    async def action_mute_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Server mutes a user (voice mute)."""
        user_id = params.get("user_id")
        username = params.get("username")
        reason = params.get("reason", "Muted via bot")
        
        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if member:
                user_id = member.id
        
        if not user_id:
            return False, None
        
        member = interaction.guild.get_member(user_id)
        if not member:
            return False, None
        
        try:
            await member.edit(mute=True, reason=reason)
            return True, {"user_id": user_id, "action": "mute"}
        except Exception as e:
            logger.error(f"Error muting user: {e}")
            return False, None

    async def action_unmute_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Removes server mute from a user."""
        user_id = params.get("user_id")
        username = params.get("username")
        
        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if member:
                user_id = member.id
        
        if not user_id:
            return False, None
        
        member = interaction.guild.get_member(user_id)
        if not member:
            return False, None
        
        try:
            await member.edit(mute=False)
            return True, {"user_id": user_id, "action": "unmute"}
        except Exception as e:
            logger.error(f"Error unmuting user: {e}")
            return False, None

    async def action_deafen_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Voice deafens a user."""
        user_id = params.get("user_id")
        username = params.get("username")
        reason = params.get("reason", "Deafened via bot")
        
        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if member:
                user_id = member.id
        
        if not user_id:
            return False, None
        
        member = interaction.guild.get_member(user_id)
        if not member:
            return False, None
        
        try:
            await member.edit(deafen=True, reason=reason)
            return True, {"user_id": user_id, "action": "deafen"}
        except Exception as e:
            logger.error(f"Error deafening user: {e}")
            return False, None

    async def action_set_nickname(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Sets a user's server nickname."""
        user_id = params.get("user_id")
        username = params.get("username")
        nickname = params.get("nickname", "")
        
        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username)
            if not member:
                member = discord.utils.get(interaction.guild.members, nick=username)
            if member:
                user_id = member.id
        
        if not user_id:
            return False, None
        
        member = interaction.guild.get_member(user_id)
        if not member:
            return False, None
        
        try:
            await member.edit(nick=nickname)
            return True, {"user_id": user_id, "nickname": nickname}
        except Exception as e:
            logger.error(f"Error setting nickname: {e}")
            return False, None

    async def action_slowmode(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Sets slowmode for a channel."""
        channel_name = params.get("channel") or params.get("channel_name")
        delay = params.get("delay", 5)  # seconds
        
        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel
        
        try:
            await target_channel.edit(slowmode_delay=delay)
            return True, {"channel_id": target_channel.id, "delay": delay}
        except Exception as e:
            logger.error(f"Error setting slowmode: {e}")
            return False, None

    async def action_lock_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Locks a channel (removes @everyone permission to send."""
        channel_name = params.get("channel") or params.get("channel_name")
        
        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel
        
        everyone_role = interaction.guild.default_role
        
        try:
            overwrite = discord.PermissionOverwrite(send_messages=False)
            await target_channel.set_permissions(everyone_role, overwrite=overwrite)
            return True, {"channel_id": target_channel.id, "action": "lock"}
        except Exception as e:
            logger.error(f"Error locking channel: {e}")
            return False, None

    async def action_unlock_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Unlocks a channel (restores @everyone permission to send."""
        channel_name = params.get("channel") or params.get("channel_name")
        
        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel
        
        everyone_role = interaction.guild.default_role
        
        try:
            overwrite = discord.PermissionOverwrite(send_messages=None)
            await target_channel.set_permissions(everyone_role, overwrite=overwrite)
            return True, {"channel_id": target_channel.id, "action": "unlock"}
        except Exception as e:
            logger.error(f"Error unlocking channel: {e}")
            return False, None

    async def action_send_message(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Sends a simple text message to a channel."""
        channel_name = params.get("channel") or params.get("channel_name")
        content = params.get("content", "")
        
        if not content:
            return False, None
        
        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel
        
        try:
            await target_channel.send(content)
            return True, {"channel_id": target_channel.id}
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False, None

    async def action_reply_message(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Replies to a message."""
        channel_name = params.get("channel") or params.get("channel_name")
        message_id = params.get("message_id")
        content = params.get("content", "")
        
        if not content:
            return False, None
        
        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel
        
        try:
            msg = await target_channel.fetch_message(message_id) if message_id else None
            if msg:
                await msg.reply(content)
            else:
                await target_channel.send(content)
            return True, {"message_id": message_id}
        except Exception as e:
            logger.error(f"Error replying: {e}")
            return False, None

    async def action_add_reaction(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Adds emoji reaction to a message."""
        channel_name = params.get("channel") or params.get("channel_name")
        message_id = params.get("message_id")
        emoji = params.get("emoji", "")
        
        if not emoji:
            return False, None
        
        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel
        
        try:
            msg = await target_channel.fetch_message(message_id) if message_id else None
            if msg:
                await msg.add_reaction(emoji)
            return True, {"emoji": emoji}
        except Exception as e:
            logger.error(f"Error adding reaction: {e}")
            return False, None

    async def action_edit_channel_name(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Renames a channel."""
        channel_name = params.get("channel_name")
        new_name = params.get("new_name") or params.get("name")
        
        if not new_name:
            return False, None
        
        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            return False, None
        
        try:
            await channel.edit(name=new_name)
            return True, {"new_name": new_name}
        except Exception as e:
            logger.error(f"Error editing channel: {e}")
            return False, None

    async def action_edit_role_name(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Renames a role."""
        role_name = params.get("role_name")
        new_name = params.get("new_name") or params.get("name")
        
        if not new_name:
            return False, None
        
        role = discord.utils.get(interaction.guild.roles, name=role_name) if role_name else None
        if not role:
            return False, None
        
        try:
            await role.edit(name=new_name)
            return True, {"new_name": new_name}
        except Exception as e:
            logger.error(f"Error editing role: {e}")
            return False, None

    async def action_change_role_color(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Changes role color."""
        role_name = params.get("role_name")
        color = params.get("color", "#99AAB5")
        
        if not role_name:
            return False, None
        
        role = discord.utils.get(interaction.guild.roles, name=role_name)
        if not role:
            return False, None
        
        try:
            await role.edit(color=parse_color(color))
            return True, {"role_name": role_name, "color": color}
        except Exception as e:
            logger.error(f"Error changing role color: {e}")
            return False, None

    async def action_move_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Moves channel to a category."""
        channel_name = params.get("channel_name")
        category_name = params.get("category")
        
        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            return False, None
        
        category = discord.utils.get(interaction.guild.categories, name=category_name) if category_name else None
        
        try:
            await channel.edit(category=category)
            return True, {"channel": channel.name}
        except Exception as e:
            logger.error(f"Error moving channel: {e}")
            return False, None

    async def action_clone_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Duplicates a channel."""
        channel_name = params.get("channel_name")
        
        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            return False, None
        
        try:
            new_channel = await channel.clone(name=f"{channel.name}-copy")
            await new_channel.edit(position=channel.position + 1)
            return True, {"new_channel": new_channel.name}
        except Exception as e:
            logger.error(f"Error cloning channel: {e}")
            return False, None

    async def action_create_thread(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Creates a thread in a channel."""
        channel_name = params.get("channel") or params.get("channel_name")
        name = params.get("name", "new-thread")
        message = params.get("message", "Thread created")
        
        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            return False, None
        
        try:
            msg = await channel.send(content=message)
            thread = await msg.create_thread(name=name)
            return True, {"thread": thread.name}
        except Exception as e:
            logger.error(f"Error creating thread: {e}")
            return False, None

    async def action_pin_message(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Pins a message."""
        channel_name = params.get("channel") or params.get("channel_name")
        message_id = params.get("message_id")
        
        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            return False, None
        
        try:
            msg = await channel.fetch_message(message_id) if message_id else None
            if msg:
                await msg.pin()
            return True, {"message_id": message_id}
        except Exception as e:
            logger.error(f"Error pinning message: {e}")
            return False, None

    async def action_set_topic(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Sets channel topic/description."""
        channel_name = params.get("channel") or params.get("channel_name")
        topic = params.get("topic", "")
        
        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            return False, None
        
        try:
            await channel.edit(topic=topic)
            return True, {"topic": topic}
        except Exception as e:
            logger.error(f"Error setting topic: {e}")
            return False, None

    # --- Execution Logic ---

    _custom_cmd_cooldowns = {}  # class-level: (guild_id, user_id, cmd_name) -> timestamp
    _custom_cmd_cooldown_seconds = 3
    
    async def execute_custom_command(self, message: discord.Message, code: str, cmd_name: str = None):
        """
        Executes a custom '!' command's stored code.
        Can be a simple string, a list of actions, or a special command object.
        Includes error prevention based on learned patterns.
        """
        guild_id = message.guild.id
        user_id = message.author.id
        cmd_data_obj = None
        
        cooldown_key = (guild_id, user_id, cmd_name)
        now = time.time()
        if cooldown_key in self._custom_cmd_cooldowns:
            remaining = self._custom_cmd_cooldown_seconds - (now - self._custom_cmd_cooldowns[cooldown_key])
            if remaining > 0:
                await message.channel.send(f"⏳ Command on cooldown. Wait {int(remaining)}s.", delete_after=2)
                return None
        self._custom_cmd_cooldowns[cooldown_key] = now
        
        try:
            data = json.loads(code)
            cmd_data_obj = data
            
            valid, error_msg = validate_command_json(data)
            if not valid:
                await message.channel.send(f"❌ {error_msg}")
                return False
            
            if isinstance(data, list):
                # We'd need a way to pass 'message' context to execute_sequence
                # For now, just acknowledge the command
                await message.channel.send("Command executed (action list).")
                return True
            
            # Handle special command objects
            elif isinstance(data, dict):
                command_type = data.get("command_type")

                # Handle action-style JSON: {"action": "...", "parameters": {...}}
                # or {"actions": [{"name": "...", "parameters": {...}}]}
                if not command_type and (data.get("action") or data.get("actions")):
                    await message.channel.send(f"⚙️ Running **!{cmd_name}**...")
                    # Build a fake interaction-like context using message
                    class _FakeInteraction:
                        def __init__(self, msg):
                            self.guild = msg.guild
                            self.channel = msg.channel
                            self.user = msg.author
                            self.response = self
                            self._responded = False
                        async def send_message(self, content=None, embed=None, ephemeral=False):
                            await message.channel.send(content=content, embed=embed)
                        async def defer(self, ephemeral=False):
                            pass
                        async def followup_send(self, *a, **kw):
                            await message.channel.send(*a, **kw)
                        async def edit_message(self, *a, **kw):
                            pass
                    fake = _FakeInteraction(message)
                    actions = data.get("actions", [])
                    if not actions and data.get("action"):
                        actions = [{"name": data["action"], "parameters": data.get("parameters", {})}]
                    result = await self.execute_sequence(fake, actions)
                    if result["success"]:
                        await message.channel.send(f"✅ **!{cmd_name}** completed!")
                    else:
                        await message.channel.send(f"❌ **!{cmd_name}** failed: {result.get('error', 'Unknown error')}")
                    return True

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
            except Exception as e:
                logger.debug("Error reading stored command prevention for %s: %s", cmd_name, e)
        
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
            last_date = dt.datetime.fromisoformat(last_time)
            if (dt.datetime.now() - last_date).days < 1:
                await message.channel.send("Daily reward already claimed today!")
                return True
        
        reward = 100
        economy.add_coins(guild_id, user_id, reward)
        last_daily[str(user_id)] = str(dt.datetime.now())
        dm.update_guild_data(guild_id, "last_daily", last_daily)
        
        await message.channel.send(f"🎉 {message.author.mention} claimed **{reward} coins**!")
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
                title="🏆 Your Achievements",
                description="No achievements yet! Keep being active to earn some.",
                color=discord.Color.gold()
            )
        else:
            embed = discord.Embed(
                title=f"🏆 {message.author.display_name}'s Achievements",
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
                title="👑 Your Titles",
                description="No titles yet! Keep being active to unlock titles.",
                color=discord.Color.gold()
            )
        else:
            active_name = f"{active_title['icon']} {active_title['name']}" if active_title else "None"
            
            embed = discord.Embed(
                title=f"👑 {message.author.display_name}'s Titles",
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
            
            titles_list = "\n".join([f". {t['icon']} **{t['name']}**" for t in user_titles])
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
            name="📋 Commands",
            value="\n".join(parent_list[:25]),
            inline=False
        )
        if len(parent_list) > 25:
            embed.add_field(name="", value="\n".join(parent_list[25:]), inline=False)
        
        embed.set_footer(text="Click to edit . Back to close")
        
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
                prev_btn = discord.ui.Button(label="? Prev", custom_id=f"prev_{page}", style=discord.ButtonStyle.primary)
                prev_btn.callback = self.create_parent_prev_callback(page)
                self.add_item(prev_btn)
            
            if page < total_pages - 1:
                next_btn = discord.ui.Button(label="Next ?", custom_id=f"next_{page}", style=discord.ButtonStyle.primary)
                next_btn.callback = self.create_parent_next_callback(page)
                self.add_item(next_btn)
            
            back_btn = discord.ui.Button(label="? Close", custom_id="close", style=discord.ButtonStyle.danger)
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
                prev_btn = discord.ui.Button(label="? Prev", custom_id=f"prev_{page}", style=discord.ButtonStyle.primary)
                prev_btn.callback = self.create_sub_prev_callback(page)
                self.add_item(prev_btn)
            
            if page < total_pages - 1:
                next_btn = discord.ui.Button(label="Next ?", custom_id=f"next_{page}", style=discord.ButtonStyle.primary)
                next_btn.callback = self.create_sub_next_callback(page)
                self.add_item(next_btn)
            
            back_btn = discord.ui.Button(label="? Back", custom_id="back", style=discord.ButtonStyle.primary)
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
                title=f"📋 Command: !{parent}",
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
        embed.add_field(name="📋 Commands", value="\n".join(command_list[:25]), inline=False)
        
        await interaction.response.edit_message(embed=embed, view=view)
    
    async def close_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Not your session.", ephemeral=True)
        await interaction.response.edit_message(content="?? Commands closed.", view=None)

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
        
        embed = discord.Embed(title="?? Your Staff Promotion Status", color=discord.Color.blue())
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
                val = (discord.utils.utcnow() - (member.joined_at or discord.utils.utcnow())).days
            elif metric_name == "achievements":
                val = len(dm.get_guild_data(guild.id, f"achievements_{member.id}", []))
            else:
                val = udata.get(metric_name, 0)
            normalized = max(0, min(1, val / max_val)) if max_val > 0 else 0
            breakdown.append(f". {metric_name}: {val}/{max_val} ({normalized*weight*100:.1f}%)")
        
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
        
        embed = discord.Embed(title="?? Staff Promotion Leaderboard", color=discord.Color.gold())
        
        if not top_10:
            embed.add_field(name="No data", value="No staff members evaluated yet", inline=False)
        else:
            for i, (member, score) in enumerate(top_10, 1):
                embed.add_field(name=f"#{i} {member.display_name}", value=f"Score: {score*100:.1f}%", inline=True)
        
        await message.channel.send(embed=embed)
        return True

    async def handle_staffpromo_config(self, message: discord.Message) -> bool:
        if not message.author.guild_permissions.administrator:
            await message.channel.send("? This command is only for administrators.")
            return True
        
        guild = message.guild
        staff_promo = self.bot.staff_promo
        config = staff_promo._get_full_config(guild.id)
        
        settings = config.get("settings", staff_promo._default_settings)
        tiers = config.get("tiers", staff_promo._default_tiers)
        
        embed = discord.Embed(title="?? Staff Promo Configuration", color=discord.Color.orange())
        
        embed.add_field(name="Auto Promote", value=str(settings.get("auto_promote", True)), inline=True)
        embed.add_field(name="Auto Demote", value=str(settings.get("auto_demote", False)), inline=True)
        embed.add_field(name="Min Tenure", value=f"{settings.get('min_tenure_hours', 72)} hours", inline=True)
        embed.add_field(name="Cooldown", value=f"{settings.get('promotion_cooldown_hours', 24)} hours", inline=True)
        
        tiers_text = "\n".join([f". {t['name']}: {int(t['threshold']*100)}%" for t in tiers])
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
        
        embed = discord.Embed(title="?? Your Promotion Progress", color=discord.Color.blue())
        embed.add_field(name="Current Score", value=f"{score*100:.1f}%", inline=True)
        
        if current_index < len(tiers) - 1:
            next_tier = tiers[current_index + 1]
            next_threshold = next_tier.get("threshold", 0)
            percent_away = (next_threshold - score) * 100
            
            embed.add_field(name="Next Tier", value=next_tier.get("name"), inline=True)
            embed.add_field(name="Progress", value=f"{percent_away:.1f}% away", inline=True)
            
            progress_bar = "?" * int(score * 10) + "?" * (10 - int(score * 10))
            embed.add_field(name="Progress Bar", value=f"`{progress_bar}` {score*100:.0f}%", inline=False)
            
            if percent_away <= 5:
                embed.add_field(name="?? Almost there!", value="You're very close to your next promotion!", inline=False)
        else:
            embed.add_field(name="Status", value="You've reached the highest tier!", inline=True)
        
        embed.set_thumbnail(url=member.display_avatar.url)
        await message.channel.send(embed=embed)
        return True

    async def handle_staffpromo_promote(self, message: discord.Message) -> bool:
        if not message.author.guild_permissions.administrator:
            await message.channel.send("? This command is only for administrators.")
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
        except (ValueError, IndexError, discord.NotFound) as e:
            logger.debug("User lookup failed in promote: %s", e)
            await message.channel.send("? Could not find user. Use `@user` format.")
            return True
        
        tier_name = " ".join(parts[3:])
        
        config = staff_promo._get_full_config(guild.id)
        success, result = await staff_promo.manual_promote(guild, target_member, tier_name, config)
        
        if success:
            await message.channel.send(f"? {target_member.mention} {result}")
        else:
            await message.channel.send(f"? {result}")
        return True

    async def handle_staffpromo_demote(self, message: discord.Message) -> bool:
        if not message.author.guild_permissions.administrator:
            await message.channel.send("? This command is only for administrators.")
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
        except (ValueError, IndexError, discord.NotFound) as e:
            logger.debug("User lookup failed in demote: %s", e)
            await message.channel.send("? Could not find user. Use `@user` format.")
            return True
        
        tier_name = " ".join(parts[3:])
        
        config = staff_promo._get_full_config(guild.id)
        success, result = await staff_promo.manual_demote(guild, target_member, tier_name, config)
        
        if success:
            await message.channel.send(f"? {target_member.mention} {result}")
        else:
            await message.channel.send(f"? {result}")
        return True

    async def handle_staffpromo_exclude(self, message: discord.Message) -> bool:
        if not message.author.guild_permissions.administrator:
            await message.channel.send("? This command is only for administrators.")
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
        except (ValueError, IndexError, discord.NotFound) as e:
            logger.debug("User lookup failed in exclude: %s", e)
            await message.channel.send("? Could not find user. Use `@user` format.")
            return True
        
        config = staff_promo._get_full_config(guild.id)
        settings = config.get("settings", staff_promo._default_settings)
        excluded = settings.get("excluded_users", [])
        
        if action == "add":
            if user_id not in excluded:
                excluded.append(user_id)
                await message.channel.send(f"? {target_member.mention} added to exclusion list.")
            else:
                await message.channel.send(f"?? {target_member.mention} is already excluded.")
        else:
            if user_id in excluded:
                excluded.remove(user_id)
                await message.channel.send(f"? {target_member.mention} removed from exclusion list.")
            else:
                await message.channel.send(f"?? {target_member.mention} is not in the exclusion list.")
        
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
    
    @discord.ui.button(label="Add Tier", style=discord.ButtonStyle.success, emoji="?")
    async def add_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddTierModal(self.guild, self.staff_promo, self.config))
    
    @discord.ui.button(label="Edit Tier", style=discord.ButtonStyle.primary, emoji="??")
    async def edit_tier(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditTierModal(self.guild, self.staff_promo, self.config))
    
    @discord.ui.button(label="Remove Tier", style=discord.ButtonStyle.danger, emoji="???")
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
                await interaction.response.send_message("? Threshold must be between 0 and 100", ephemeral=True)
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
            
            await interaction.response.send_message(f"? Added tier **{self.name.value}** with threshold {self.threshold.value}%", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("? Invalid threshold value", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"? Error adding tier: {str(e)}", ephemeral=True)

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
                await interaction.response.send_message(f"? Tier '{self.tier_select.value}' not found", ephemeral=True)
                return
            
            # Update fields if provided
            if self.new_name.value:
                tier_to_edit["name"] = self.new_name.value
            if self.new_threshold.value:
                threshold_val = float(self.new_threshold.value) / 100
                if threshold_val < 0 or threshold_val > 1:
                    await interaction.response.send_message("? Threshold must be between 0 and 100", ephemeral=True)
                    return
                tier_to_edit["threshold"] = threshold_val
            if self.new_role.value is not None:  # Allow empty string to remove role
                tier_to_edit["role_name"] = self.new_role.value if self.new_role.value else None
            
            self.config["tiers"] = tiers
            
            # Update the data manager
            from data_manager import dm
            dm.update_guild_data(self.guild.id, "staff_promo_config", self.config)
            
            await interaction.response.send_message(f"? Updated tier **{self.tier_select.value}**", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("? Invalid threshold value", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"? Error editing tier: {str(e)}", ephemeral=True)

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
                await interaction.response.send_message("? Confirmation failed. Type 'CONFIRM' to delete.", ephemeral=True)
                return
            
            tiers = self.config.get("tiers", self.staff_promo._default_tiers)
            tier_to_remove = None
            for i, tier in enumerate(tiers):
                if tier.get("name", "").lower() == self.tier_select.value.lower():
                    tier_to_remove = i
                    break
            
            if tier_to_remove is None:
                await interaction.response.send_message(f"? Tier '{self.tier_select.value}' not found", ephemeral=True)
                return
            
            removed_tier = tiers.pop(tier_to_remove)
            self.config["tiers"] = tiers
            
            # Update the data manager
            from data_manager import dm
            dm.update_guild_data(self.guild.id, "staff_promo_config", self.config)
            
            await interaction.response.send_message(f"? Removed tier **{removed_tier.get('name')}**", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"? Error removing tier: {str(e)}", ephemeral=True)

    async def handle_staffpromo_tiers(self, message: discord.Message) -> bool:
        """Handle !staffpromo tiers command - Interactive tier management"""
        if not message.author.guild_permissions.administrator:
            await message.channel.send("? This command is only for administrators.")
            return True
        
        guild = message.guild
        staff_promo = self.bot.staff_promo
        config = staff_promo._get_full_config(guild.id)
        tiers = config.get("tiers", staff_promo._default_tiers)
        role_ids = config.get("roles_by_tier", {})
        
        # Create embed showing current tiers
        embed = discord.Embed(
            title="?? Promotion Tiers Management",
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
                tiers_text += f"**{name}**: {threshold}% ? {role_mention}\n"
            embed.add_field(name="Current Tiers", value=tiers_text or "None", inline=False)
        else:
            embed.add_field(name="Current Tiers", value="No tiers configured", inline=False)
        
        # Create view with buttons
        view = TierManagementView(guild, staff_promo, config)
        await message.channel.send(embed=embed, view=view)
        return True

    async def handle_staffpromo_roles(self, message: discord.Message) -> bool:
        if not message.author.guild_permissions.administrator:
            await message.channel.send("? This command is only for administrators.")
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
            
            embed = discord.Embed(title="?? Role Mappings", color=discord.Color.orange())
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
        except (ValueError, IndexError, discord.NotFound) as e:
            logger.debug("Role lookup failed: %s", e)
            await message.channel.send("? Could not find role. Use `@role` format.")
            return True
        
        config = staff_promo._get_full_config(guild.id)
        role_ids = config.get("roles_by_tier", {})
        
        tiers = config.get("tiers", staff_promo._default_tiers)
        valid_tiers = [t.get("name").lower() for t in tiers]
        
        if tier_name.lower() not in valid_tiers:
            valid_list = ", ".join([t.get("name") for t in tiers])
            await message.channel.send(f"? Invalid tier. Valid tiers: {valid_list}")
            return True
        
        if action == "add":
            role_ids[tier_name] = target_role.id
            await message.channel.send(f"? Mapped **{tier_name}** to {target_role.mention}")
        else:
            if tier_name in role_ids:
                del role_ids[tier_name]
            await message.channel.send(f"? Removed mapping for **{tier_name}**")
        
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
                await message.channel.send("?? No pending promotion reviews.")
                return True
            
            embed = discord.Embed(title="?? Pending Promotion Reviews", color=discord.Color.yellow())
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
                    await message.channel.send(f"?? Your promotion to **{review.get('tier_name')}** is pending review. Score: {review.get('score', 0)*100:.1f}%")
            else:
                await message.channel.send("?? You have no pending reviews.")
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
        except (ValueError, IndexError, discord.NotFound) as e:
            logger.debug("User lookup failed in promotion decision: %s", e)
            await message.channel.send("? Could not find user. Use `@user` format.")
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
            await message.channel.send(f"? No pending review found for that user.")
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
                await message.channel.send(f"? Approved! {target_member.mention} promoted to **{target_tier_name}**")
        else:
            try:
                await target_member.send(f"? Your promotion to **{target_tier_name}** was rejected.")
            except Exception as e:
                logger.debug("Could not DM user about rejected promotion: %s", e)
            await message.channel.send(f"? Rejected promotion for {target_member.mention}")
        
        return True

    async def handle_staffpromo_requirements(self, message: discord.Message) -> bool:
        guild = message.guild
        staff_promo = self.bot.staff_promo
        
        config = staff_promo._get_full_config(guild.id)
        requirements = config.get("tier_requirements", staff_promo._default_tier_requirements)
        tiers = config.get("tiers", staff_promo._default_tiers)
        
        embed = discord.Embed(title="?? Tier Requirements", color=discord.Color.blue())
        
        for tier in tiers:
            tier_name = tier.get("name")
            tier_reqs = requirements.get(tier_name, {})
            
            if tier_reqs:
                req_text = "\n".join([f". {k}: {v}" for k, v in tier_reqs.items()])
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
        
        embed = discord.Embed(title="?? Achievement Score Bonuses", color=discord.Color.gold())
        
        total_bonus = 1.0
        for ach_name, multiplier in bonuses.items():
            bonus_pct = (multiplier - 1) * 100
            embed.add_field(name=ach_name, value=f"+{bonus_pct:.0f}% score multiplier", inline=True)
            total_bonus += (multiplier - 1)
        
        embed.add_field(name="Total Max Bonus", value=f"{((total_bonus - 1) * 100):.0f}%", inline=False)
        
        await message.channel.send(embed=embed)
        return True
