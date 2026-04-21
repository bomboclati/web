import discord
import json
import asyncio
import time
import difflib
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
            style=discord.TextStyle.paragraph,
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
        "create_channel", "create_shop_channel", "delete_channel", "create_role", "delete_role",
        "create_category", "edit_channel", "edit_role", "assign_role",
        "assign_role_by_name", "create_prefix_command", "create_command", "make_command", "add_command", "new_command", "delete_prefix_command",
        "setup_welcome", "setup_logging", "setup_verification", "setup_economy", "setup_leveling",
        "setup_tickets", "setup_applications", "setup_appeals", "setup_moderation", "setup_staff_system",
        "send_dm", "create_invite", "schedule_ai_action", "ping",
        "kick_user", "ban_user", "timeout_user", "softban_user",
        "announce", "poll", "give_points", "remove_points", "warn_user",
        "create_verify_system", "create_tickets_system", "create_applications_system", "create_appeals_system",
        "create_welcome_system", "create_staff_system", "create_leveling_system", "create_economy_system",
        "mute_user", "unmute_user", "deafen_user", "set_nickname", "slowmode", "lock_channel", "unlock_channel",
        "reply_message", "add_reaction", "edit_channel_name", "edit_role_name",
        "change_role_color", "move_channel", "clone_channel", "create_thread", "pin_message", "unpin_message",
        "set_topic", "delete_messages", "remove_reaction", "delete_message", "bulk_delete_messages",
        "create_role_with_permissions", "edit_channel_permissions", "create_voice_channel", "create_text_channel",
        "create_category_channel", "edit_channel_bitrate", "edit_channel_user_limit", "follow_announcement_channel",
        "create_scheduled_event", "allow_channel_permission", "deny_channel_permission",
        "deny_all_channels_for_role", "allow_all_channels_for_role", "deny_category_for_role",
        "make_channel_private", "make_category_private", "clear_reactions", "edit_guild",
        "analyze_server_state", "extract_online_users", "send_notification", "create_task", "update_profile",
        # Server Query Actions
        "query_server_info", "query_channels", "query_roles", "query_members", "query_member_details",
        "query_economy_leaderboard", "query_xp_leaderboard", "query_pending_applications",
        "query_active_shifts", "query_recent_messages",
        # System connection actions
        "connect_systems",
        # System move actions
        "move_system",
        # Additional actions from action_catalog
        "post_documentation", "setup_trigger_role"
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

    async def _auto_document_system(self, guild_id, system_type):
        """Auto-document a system by creating missing ! commands with fallback responses."""
        try:
            # Load system commands mapping
            import os
            mapping_file = os.path.join(os.path.dirname(__file__), "system_commands.json")
            if not os.path.exists(mapping_file):
                logger.warning(f"system_commands.json not found at {mapping_file}")
                return

            with open(mapping_file, "r") as f:
                system_commands = json.load(f)

            if system_type not in system_commands:
                logger.info(f"No commands defined for system_type: {system_type}")
                return

            commands = system_commands[system_type]
            custom_cmds = dm.get_guild_data(guild_id, "custom_commands", {})

            # Create missing commands
            created_commands = []
            for cmd_name, cmd_data in commands.items():
                if cmd_name not in custom_cmds:
                    # Create command with fallback response
                    custom_cmds[cmd_name] = cmd_data
                    created_commands.append(cmd_name)
                    logger.info(f"Created missing command: !{cmd_name} for system: {system_type}")

            # Create help command for the system
            help_cmd_name = f"help {system_type}"
            if help_cmd_name not in custom_cmds:
                custom_cmds[help_cmd_name] = {
                    "command_type": "simple",
                    "content": f"Help for {system_type} system. Use !help {system_type} for more info."
                }
                created_commands.append(help_cmd_name)
                logger.info(f"Created help command: !{help_cmd_name}")

            if created_commands:
                dm.update_guild_data(guild_id, "custom_commands", custom_cmds)

                # Send help embed in dedicated channel
                guild = self.bot.get_guild(guild_id)
                if guild:
                    # Find or create help channel
                    help_channel = None
                    for channel in guild.text_channels:
                        if channel.name in ["help", "commands", "bot-help"]:
                            help_channel = channel
                            break
                    if not help_channel:
                        # Try to create one
                        try:
                            help_channel = await guild.create_text_channel("help")
                        except discord.Forbidden:
                            logger.warning("Cannot create help channel due to permissions")
                            return

                    if help_channel:
                        embed = discord.Embed(
                            title=f"{system_type.capitalize()} System Setup Complete",
                            description=f"Created the following commands: {', '.join(f'!{c}' for c in created_commands)}\n\nUse `!help {system_type}` for more info.",
                            color=0x00ff00
                        )
                        await help_channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Error in _auto_document_system: {e}")

    def _normalize_params(self, params, mappings):
        """Normalize parameters using aliases and defaults."""
        normalized = {}
        for expected, aliases in mappings.items():
            value = None
            for alias in aliases:
                if alias in params:
                    value = params[alias]
                    break
            # Apply defaults if missing
            if value is None:
                if expected == "channel_name":
                    value = "new-channel"
                elif expected == "role_name":
                    value = "new-role"
                elif expected == "duration":
                    value = 60
                elif expected == "reason":
                    value = "No reason provided"
                elif expected == "content":
                    value = ""
                elif expected == "color":
                    value = "#3498db"
            # Special handling
            if expected == "user_id":
                if isinstance(value, str):
                    import re
                    # Extract from mention
                    match = re.match(r'<@!?(\d+)>', value)
                    if match:
                        value = int(match.group(1))
                    elif value.isdigit():
                        value = int(value)
            elif expected == "duration":
                if params.get("minutes") and not params.get("duration"):
                    value = params["minutes"] * 60
            elif expected == "color":
                if params.get("colour"):
                    value = params["colour"]
                elif params.get("hex_color"):
                    value = params["hex_color"]
            normalized[expected] = value
        return normalized

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
                error_msg = f"Action not allowed: {name}"
                results.append((name, False))
                self._record_failure(guild_id, name, error_msg)
                return {"results": results, "rolled_back": [], "failed_at": i, "failed_action": name, "error": error_msg, "success": False}

            try:
                success, undo_data = await self.dispatch(interaction, name, params)

                if success:
                    results.append((name, success))

                    if undo_data:
                        self._action_log.append({
                            "action": name,
                            "undo_data": undo_data,
                            "guild_id": guild_id,
                            "user_id": user_id,
                            "timestamp": time.time()
                        })
                else:
                    # Failure
                    if undo_data and isinstance(undo_data, dict) and "error" in undo_data:
                        error_msg = undo_data["error"]
                    else:
                        error_msg = f"Action returned failure: {name}"
                    logger.error("Action Error (%s): %s", name, error_msg)
                    results.append((name, False))

                    self._record_failure(guild_id, name, error_msg)

                    await interaction.followup.send(error_msg, ephemeral=True)

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
            except Exception as e:
                error_msg = str(e)
                logger.error("Action Error (%s): %s", name, error_msg)
                results.append((name, False))

                self._record_failure(guild_id, name, error_msg)

                await interaction.followup.send(error_msg, ephemeral=True)

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

    def _normalize_action_name(self, name: str) -> str:
        import re
        original_name = name
        name = name.lower()
        name = name.replace(' ', '_')
        typo_map = {'creat': 'create', 'chanel': 'channel', 'assing': 'assign', 'remve': 'remove', 'delet': 'delete', 'membr': 'member', 'nmae': 'name', 'mesage': 'message', 'embded': 'embed', 'reson': 'reason', 'duraton': 'duration'}
        for typo, correct in typo_map.items():
            name = name.replace(typo, correct)
        phrasing_map = {'send_embed': 'embed_send', 'embed_send': 'send_embed', 'dm_user': 'send_dm', 'send_dm': 'dm_user', 'ban_member': 'ban_user', 'ban_user': 'ban_member', 'kick_member': 'kick_user', 'kick_user': 'kick_member'}
        if name in phrasing_map:
            name = phrasing_map[name]
        name = name.rstrip('.,!?;:')
        name = re.sub(r'_+', '_', name)
        name = name.strip('_')
        if name in self.ALLOWED_ACTIONS:
            return name
        variations = []
        if name.endswith('_user'):
            variations.append(name[:-5])
        elif name.endswith('_member'):
            variations.append(name[:-7])
        else:
            variations.append(name + '_user')
            variations.append(name + '_member')
        for var in variations:
            if var in self.ALLOWED_ACTIONS:
                return var
        return original_name

    def _fuzzy_match(self, target: str, candidates: List[str], cutoff: float = 0.6) -> Optional[str]:
        """Find the best fuzzy match for target in candidates using difflib."""
        if not target or not candidates:
            return None

        target_lower = target.lower()
        matches = difflib.get_close_matches(target_lower, [c.lower() for c in candidates], n=1, cutoff=cutoff)
        if matches:
            # Find the original case candidate
            for candidate in candidates:
                if candidate.lower() == matches[0]:
                    return candidate
        return None

    async def dispatch(self, interaction: discord.Interaction, name: str, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Routes action names to specific methods with permission enforcement. Returns (success, undo_data)."""
        name = self._normalize_action_name(name)

        # --- PERMISSION ENFORCEMENT ---
        # Critical Security: Ensure the initiating user has Administrator permission
        # for sensitive actions. This prevents exploitation via AI or custom commands.

        # Read-only actions that don't require admin
        read_only_actions = {
            "analyze_server_state", "query_server_info", "query_channels",
            "query_roles", "query_members", "query_member_details",
            "query_economy_leaderboard", "query_xp_leaderboard",
            "query_pending_applications", "query_active_shifts",
            "query_recent_messages", "send_message", "reply_message",
            "add_reaction", "send_notification"
        }

        # If action is not read-only and user is not admin, block it.
        if name not in read_only_actions and not interaction.user.guild_permissions.administrator:
            # Special case: allow the bot itself (e.g. for scheduled tasks or system connections)
            if interaction.user.id != self.bot.user.id:
                logger.warning(
                    "Blocked sensitive action '%s' triggered by non-admin user %s (%d) in guild %d",
                    name, interaction.user, interaction.user.id, interaction.guild.id
                )
                return False, {"error": f"You do not have Administrator permission to execute the '{name}' action."}

        method_name = f"action_{name}"
        if hasattr(self, method_name):
            method = getattr(self, method_name)
            return await method(interaction, params)
        else:
            logger.warning("Unknown action: %s", name)
            return False, None

    # --- Meta / Planning Actions ---

    async def action_analyze_server_state(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Read-only no-op. The AI uses this as a planning checkpoint before executing real actions.
        Logs current guild state to help with debugging; always returns success so the action
        sequence continues to the actual changes."""
        guild = interaction.guild
        channels = [c.name for c in guild.text_channels] + [c.name for c in guild.voice_channels]
        categories = [c.name for c in guild.categories]
        roles = [r.name for r in guild.roles if r.name != "@everyone"]
        logger.info(
            "[analyze_server_state] guild=%s | categories=%s | channels=%s | roles=%s | members=%d",
            guild.name, categories, channels, roles, guild.member_count
        )
        return True, None

    # --- Basic Actions ---

    async def action_create_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        guild = interaction.guild
        name = self._get_param(params, "channel_name", "name", "channel", default="new-channel")
        channel_type = self._get_param(params, "channel_type", "type", default="text")
        category_name = self._get_param(params, "category_name", "category")
        private = self._get_param(params, "private", default=False)
        allowed_roles = self._get_param(params, "allowed_roles", default=[])
        denied_roles = self._get_param(params, "denied_roles", default=[])

        # If private=true, always deny @everyone view_channel
        if private and "@everyone" not in denied_roles:
            denied_roles = list(denied_roles) + ["@everyone"]

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

    async def action_create_shop_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        guild = interaction.guild
        name = self._get_param(params, "channel_name", "name", default="shop")
        channel_type = self._get_param(params, "channel_type", "type", default="text")
        category_name = self._get_param(params, "category_name", "category", default="shop")
        private = self._get_param(params, "private", default=False)
        allowed_roles = self._get_param(params, "allowed_roles", default=[])
        denied_roles = self._get_param(params, "denied_roles", default=[])

        # If private=true, always deny @everyone view_channel
        if private and "@everyone" not in denied_roles:
            denied_roles = list(denied_roles) + ["@everyone"]

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
        logger.info("Created shop channel: %s", channel.name)
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
            ("shop", {"allowed": [], "denied": []}),

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
        """Set view permissions for roles without wiping existing overwrites"""
        from discord import PermissionOverwrite

        for role_name in allowed_roles:
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                existing = channel.overwrites_for(role)
                existing.view_channel = True
                existing.send_messages = True
                await channel.set_permissions(role, overwrite=existing)

        for role_name in denied_roles:
            if role_name == "@everyone":
                role = guild.default_role
            else:
                role = discord.utils.get(guild.roles, name=role_name)
            if role:
                existing = channel.overwrites_for(role)
                existing.view_channel = False
                await channel.set_permissions(role, overwrite=existing)

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
            },
            "shop": {
                "description": "Buy and sell items in our server shop!",
                "commands": ["!shop", "!buy <item>", "!sell <item>"]
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
        name = self._get_param(params, "role_name", "name")

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

        color = self._get_param(params, "color", "colour", "hex_color", default="#3498db")
        color_hex = color.replace("#", "")
        color = discord.Color(int(color_hex, 16))

        # Auto-detect role permissions
        role_perms = self._detect_role_permissions(name)

        permissions = discord.Permissions(
            view_channel=role_perms.get("view_channel", True),
            send_messages=role_perms.get("send_messages", True),
            read_message_history=role_perms.get("read_message_history", True),
            attach_files=role_perms.get("attach_files", True),
            embed_links=role_perms.get("embed_links", True),
            add_reactions=role_perms.get("add_reactions", True),
            use_external_emojis=role_perms.get("use_external_emojis", True),
            use_application_commands=role_perms.get("use_application_commands", True),
            connect=role_perms.get("connect", True),
            speak=role_perms.get("speak", True),
            manage_channels=role_perms.get("manage_channels", False),
            manage_roles=role_perms.get("manage_roles", False),
            kick_members=role_perms.get("kick_members", False),
            ban_members=role_perms.get("ban_members", False),
            moderate_members=role_perms.get("moderate_members", False),
            manage_messages=role_perms.get("manage_messages", False),
            mention_everyone=role_perms.get("mention_everyone", False),
            mute_members=role_perms.get("mute_members", False),
            move_members=role_perms.get("move_members", False),
            create_instant_invite=role_perms.get("create_instant_invite", True),
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
        return True, {"action": "delete_role", "role_id": role.id, "role_name": role.name}

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

    async def _resolve_role(self, guild: discord.Guild, **kwargs) -> Optional[discord.Role]:
        """Robustly resolve a role by ID or name from kwargs.

        Changes:
        - Updated to use **kwargs for flexibility in parameter names.
        - Extracts role_id from 'role_id', 'id', or 'role' (as int).
        - Extracts role_name from 'role_name' or 'name'.
        - Logs a warning if neither role_id nor role_name is provided.
        - Supports various input formats for role identification.
        """
        # Extract role_id from possible keys, prioritizing role_id, then id, then role
        role_id = kwargs.get('role_id') or kwargs.get('id') or kwargs.get('role')
        # Extract role_name from possible keys, prioritizing role_name, then name
        role_name = kwargs.get('role_name') or kwargs.get('name')

        if role_id:
            try:
                # Handle both int and string IDs, including mentions
                rid = int(str(role_id).strip().lstrip("<@&").rstrip(">"))
                role = guild.get_role(rid)
                if role: return role
            except (ValueError, TypeError):
                pass

        if role_name:
            name = str(role_name).strip()
            if name == "@everyone":
                return guild.default_role
            name_lower = name.lower()
            # Try exact match first
            role = discord.utils.find(lambda r: r.name.lower() == name_lower, guild.roles)
            if role: return role
            # Try partial match
            role = discord.utils.find(lambda r: name_lower in r.name.lower(), guild.roles)
            if role: return role

        # Log warning if neither role_id nor role_name was provided
        if not role_id and not role_name:
            logger.warning("_resolve_role: no role_id or role_name provided in kwargs")

        return None

    async def _resolve_member(self, guild: discord.Guild, **kwargs) -> Optional[discord.Member]:
        """Robustly resolve a member by ID or name from kwargs."""
        user_id = kwargs.get('user_id')
        username = kwargs.get('username')

        if user_id:
            try:
                uid = int(str(user_id).strip().lstrip("<@!").rstrip(">"))
                member = guild.get_member(uid)
                if not member:
                    try:
                        member = await guild.fetch_member(uid)
                    except (discord.NotFound, discord.HTTPException):
                        pass
                if member: return member
            except (ValueError, TypeError):
                pass

        if username:
            name = str(username).strip().lstrip("@").lower()
            # Try exact match on name or display name
            member = discord.utils.find(lambda m: m.name.lower() == name or m.display_name.lower() == name, guild.members)
            if member: return member

            # Try query members for those not in cache
            try:
                results = await guild.query_members(query=name, limit=5)
                if results:
                    return results[0]
            except Exception:
                pass

        return None

    async def _find_existing_resources(self, guild: discord.Guild, system_type: str) -> dict:
        """Scan existing channels, roles, and categories for partial matches based on system_type keywords.

        Returns dict with 'channel', 'role', 'category' (or None), and 'reasoning'.
        """
        # Keywords for each system type
        keywords = {
            "verification": {
                "channels": ["verify", "verification", "welcome", "rules"],
                "roles": ["verified", "member", "guest"]
            },
            "tickets": {
                "channels": ["ticket", "support", "help"],
                "roles": ["support", "helper", "staff"]
            },
            "appeals": {
                "channels": ["appeal", "ban-appeal", "modmail"],
                "roles": ["appeal", "moderator"]
            },
            "applications": {
                "channels": ["apply", "application", "staff-app"],
                "roles": ["applicant", "staff"]
            },
            "economy": {
                "channels": ["shop", "economy", "market"],
                "roles": ["economy", "shopkeeper"]
            },
            "leveling": {
                "channels": ["level", "xp", "rank", "leaderboard"],
                "roles": ["level", "veteran", "rank"]
            },
            "welcome": {
                "channels": ["welcome", "introductions", "greeting"],
                "roles": ["welcome", "new"]
            }
        }

        if system_type not in keywords:
            return {"channel": None, "role": None, "category": None, "reasoning": f"Unknown system_type: {system_type}"}

        sys_keywords = keywords[system_type]
        found_channel = None
        found_role = None
        found_category = None
        reasoning_parts = []

        # Helper function to find best match
        def find_best_match(items, keyword_list):
            """Find best match: exact > partial, shorter name > longer, higher position > lower."""
            matches = []
            for item in items:
                item_name_lower = item.name.lower()
                for keyword in keyword_list:
                    keyword_lower = keyword.lower()
                    # Check exact match
                    if item_name_lower == keyword_lower:
                        matches.append((item, 3, len(item.name), -getattr(item, 'position', 0)))
                    # Check partial match
                    elif keyword_lower in item_name_lower:
                        matches.append((item, 2, len(item.name), -getattr(item, 'position', 0)))

            if matches:
                # Sort by priority (exact > partial), then shorter name, then higher position
                matches.sort(key=lambda x: (-x[1], x[2], x[3]))
                return matches[0][0]
            return None

        # Find channel
        if sys_keywords.get("channels"):
            found_channel = find_best_match(guild.channels, sys_keywords["channels"])
            if found_channel:
                reasoning_parts.append(f"Found channel '{found_channel.name}'")

        # Find role
        if sys_keywords.get("roles"):
            found_role = find_best_match(guild.roles, sys_keywords["roles"])
            if found_role:
                reasoning_parts.append(f"Found role '{found_role.name}'")

        # Find category (use same keywords as channels or common category names)
        category_keywords = sys_keywords.get("channels", []) + ["community", "general", "main"]
        found_category = find_best_match(guild.categories, category_keywords)
        if found_category:
            reasoning_parts.append(f"Found category '{found_category.name}'")

        reasoning = "; ".join(reasoning_parts) if reasoning_parts else "No existing resources found"

        return {
            "channel": found_channel,
            "role": found_role,
            "category": found_category,
            "reasoning": reasoning
        }

    def _get_param(self, params: dict, *keys, default=None):
        """Returns the first value found for any key in keys."""
        for key in keys:
            if key in params:
                return params[key]
        return default

    async def action_assign_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Assign a role to one or more users. Supports multiple roles too."""
        guild = interaction.guild

        # Security check: Does the user have permission to manage roles?
        if not interaction.user.guild_permissions.manage_roles:
            return False, {"error": "User lacks 'Manage Roles' permission."}

        if not guild.me.guild_permissions.manage_roles:
            return False, {"error": "Bot lacks 'Manage Roles' permission."}

        # --- Resolve Roles ---
        roles = []
        role_ids = self._get_param(params, "role_ids", "roles", "role_list", default=[])
        if not isinstance(role_ids, list): role_ids = [role_ids]
        single_role_id = self._get_param(params, "role_id", "role", "name", "role_name")
        if single_role_id is not None:
            role_ids.append(single_role_id)

        role_names = self._get_param(params, "role_names", "names", "role_names_list", default=[])
        if not isinstance(role_names, list): role_names = [role_names]
        single_role_name = self._get_param(params, "role_name", "name", "role")
        if single_role_name is not None:
            role_names.append(single_role_name)

        for rid in role_ids:
            r = await self._resolve_role(guild, role_id=rid)
            if r and r not in roles: roles.append(r)
        for rn in role_names:
            r = await self._resolve_role(guild, role_name=rn)
            if r and r not in roles: roles.append(r)

        if not roles:
            return False, {"error": f"Could not find any roles matching: ID={role_ids}, Name={role_names}"}

        # --- Resolve Members ---
        members = []
        user_ids = self._get_param(params, "user_ids", "users", "members", "targets", "uids", default=[])
        if not isinstance(user_ids, list): user_ids = [user_ids]
        single_user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        if single_user_id is not None:
            user_ids.append(single_user_id)

        usernames = self._get_param(params, "usernames", "names", "user_names", default=[])
        if not isinstance(usernames, list): usernames = [usernames]
        single_username = self._get_param(params, "username", "user_name", "name")
        if single_username is not None:
            usernames.append(single_username)

        for uid in user_ids:
            m = await self._resolve_member(guild, user_id=uid)
            if m and m not in members: members.append(m)
        for un in usernames:
            m = await self._resolve_member(guild, username=un)
            if m and m not in members: members.append(m)

        if not members:
            return False, {"error": f"Could not find any members matching: ID={user_ids}, Name={usernames}"}

        # --- Process Assignments ---
        bot_top_role = guild.me.top_role
        success_count = 0
        undo_list = []
        errors = []

        for role in roles:
            if role.position >= bot_top_role.position:
                errors.append(f"Role '{role.name}' is higher than bot's role.")
                continue

            for member in members:
                try:
                    if role in member.roles:
                        success_count += 1
                        continue
                    await member.add_roles(role, reason=f"Assigned by {interaction.user.display_name}")
                    success_count += 1
                    undo_list.append({"action": "remove_role", "user_id": member.id, "role_id": role.id})
                except discord.Forbidden:
                    errors.append(f"Forbidden: Could not give '{role.name}' to '{member.display_name}'.")
                except Exception as e:
                    errors.append(f"Error giving '{role.name}' to '{member.display_name}': {str(e)}")

        if success_count == 0 and errors:
            return False, {"error": "; ".join(errors)}

        return True, {"action": "batch_undo", "undo_data": undo_list}

    async def action_add_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Adds a role to a user. Alias for action_assign_role."""
        return await self.action_assign_role(interaction, params)

    async def action_assign_role_by_name(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Assigns a role to a user by role name and user identifier."""
        guild = interaction.guild

        # Security check
        if not interaction.user.guild_permissions.manage_roles:
            return False, {"error": "User lacks 'Manage Roles' permission."}
        if not guild.me.guild_permissions.manage_roles:
            return False, {"error": "Bot lacks 'Manage Roles' permission."}

        role_name = self._get_param(params, "role_name", "name")
        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "user_mention")
        username = self._get_param(params, "username", "user_name")

        # Resolve role
        role = None
        if role_name:
            role = discord.utils.find(lambda r: r.name.lower() == str(role_name).lower(), guild.roles)
            if not role:
                role = discord.utils.find(lambda r: str(role_name).lower() in r.name.lower(), guild.roles)
        if not role:
            return False, {"error": f"Could not find role '{role_name}'"}

        # Resolve member
        member = None
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
        if not member:
            return False, {"error": f"Could not find member '{username or user_id}'"}

        # Check bot's role position
        if role.position >= guild.me.top_role.position:
            return False, {"error": f"Role '{role.name}' is higher than bot's role."}

        # Assign role
        try:
            if role in member.roles:
                return True, None  # Already has the role
            await member.add_roles(role, reason=f"Assigned by {interaction.user.display_name}")
            return True, {"action": "remove_role", "user_id": member.id, "role_id": role.id, "role_name": role.name}
        except discord.Forbidden:
            return False, {"error": f"Forbidden: Could not assign '{role.name}' to '{member.display_name}'"}
        except Exception as e:
            return False, {"error": f"Error assigning role: {str(e)}"}

    async def action_remove_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Removes a role from a user."""
        guild = interaction.guild

        role = await self._resolve_role(guild, **params)

        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        username = self._get_param(params, "username", "user_name", "name")
        member = await self._resolve_member(guild, user_id=user_id, username=username)

        if not role:
            logger.error("remove_role: could not find role with params: %s", params)
            return False, None
        if not member:
            logger.error("remove_role: could not find member. user_id=%s username=%s", user_id, username)
            return False, None

        try:
            await member.remove_roles(role)
            logger.info("Removed role %s from %s", role.name, member.display_name)
            return True, {"action": "add_role", "user_id": member.id, "role_id": role.id, "role_name": role.name}
        except discord.Forbidden:
            logger.error("remove_role: missing permissions")
            return False, None
        except Exception as e:
            logger.error(f"Error removing role: {e}")
            return False, None

    async def action_bulk_delete_messages(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Bulk deletes messages in a channel."""
        channel_name = params.get("channel") or params.get("channel_name")
        amount = params.get("amount", 10)

        target_channel = None
        if channel_name:
            target_channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not target_channel:
            target_channel = interaction.channel

        if not target_channel.permissions_for(interaction.guild.me).manage_messages:
            logger.error("bulk_delete_messages: missing manage_messages permission")
            return False, None

        amount = min(max(amount, 1), 100)

        try:
            deleted = []
            async for msg in target_channel.history(limit=amount + 1):
                if msg.id == interaction.id:
                    continue
                deleted.append(msg)
                if len(deleted) >= amount:
                    break

            if deleted:
                await target_channel.delete_messages(deleted)
                logger.info("Bulk deleted %d messages in %s", len(deleted), target_channel.name)

            return True, {"deleted_count": len(deleted), "channel": target_channel.name}
        except discord.Forbidden:
            logger.error("bulk_delete_messages: missing permissions")
            return False, None
        except Exception as e:
            logger.error(f"Error bulk deleting messages: {e}")
            return False, None

    async def action_delete_messages(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Bulk deletes messages. Alias for action_bulk_delete_messages."""
        return await self.action_bulk_delete_messages(interaction, params)


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

    async def action_create_command(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Alias for action_create_prefix_command."""
        return await self.action_create_prefix_command(interaction, params)

    async def action_make_command(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Alias for action_create_prefix_command."""
        return await self.action_create_prefix_command(interaction, params)

    async def action_add_command(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Alias for action_create_prefix_command."""
        return await self.action_create_prefix_command(interaction, params)

    async def action_new_command(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Alias for action_create_prefix_command."""
        return await self.action_create_prefix_command(interaction, params)

    async def action_send_embed(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        channel_name = self._get_param(params, "channel_name", "channel")
        title = self._get_param(params, "title")
        description = self._get_param(params, "description", "content", "message", "text")
        color = parse_color(self._get_param(params, "color", "colour", "hex_color", default="#3498db"))
        buttons = self._get_param(params, "buttons", default=[])
        fields = self._get_param(params, "fields", default=[])

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
        """Sends a DM to a user. Returns True even when DMs are disabled (soft failure) so the action sequence keeps going."""
        params = self._normalize_params(params, {
            "user_id": ["user_id", "user", "member_id", "target_id", "user_mention"],
            "username": ["username", "user_name"],
            "content": ["content", "message", "text"],
            "embed_data": ["embed"],
        })
        user_id = params["user_id"]
        username = params["username"]
        content = params["content"]
        embed_data = params["embed_data"]
        guild = interaction.guild

        # ── 0. Parse user_id from Discord mention format (e.g. <@!123456789> or <@123456789>) ─────
        if user_id:
            try:
                # Handle both <@! and <@ mention formats
                user_id_str = str(user_id).strip()
                if user_id_str.startswith("<@") and user_id_str.endswith(">"):
                    # Strip <@! or <@ prefix and > suffix to get the numeric ID
                    cleaned = user_id_str.lstrip("<@!").lstrip("<@").rstrip(">")
                    if cleaned.isdigit():
                        user_id = int(cleaned)
                    else:
                        user_id = None
                elif user_id_str.isdigit():
                    user_id = int(user_id_str)
                else:
                    user_id = None
            except (ValueError, TypeError):
                user_id = None

        # ── 1. Resolve user_id from username ──────────────────────────────────
        if not user_id and username:
            # Handle Discord mention format <@!123456789> or <@123456789> (extract numeric ID directly)
            if isinstance(username, str) and username.startswith("<@"):
                mention_cleaned = username.lstrip("<@!").lstrip("<@").rstrip(">")
                if mention_cleaned.isdigit():
                    user_id = int(mention_cleaned)
                    username = None  # Clear username since we got the ID directly
            # Handle plain @username format
            elif isinstance(username, str) and username.startswith("@"):
                username = username[1:]

            # If we got user_id from mention, skip the name-based lookup
            if not user_id and username:
                # a) Check in-memory guild member cache (name / nick / display_name)
                member = (
                    discord.utils.get(guild.members, name=username)
                    or discord.utils.get(guild.members, nick=username)
                    or discord.utils.get(guild.members, display_name=username)
                )

                # b) Case-insensitive fallback over cached members
                if not member:
                    lower = username.lower()
                    for m in guild.members:
                        if (m.name.lower() == lower
                                or (m.nick and m.nick.lower() == lower)
                                or m.display_name.lower() == lower):
                            member = m
                            break

                # c) Discord API member search (finds members not in cache)
                if not member:
                    try:
                        results = await guild.query_members(query=username, limit=5)
                        if results:
                            member = results[0]
                    except Exception:
                        pass

                if member:
                    user_id = member.id

        # d) username is a raw numeric ID
        if not user_id and username:
            try:
                user_id = int(str(username).strip())
            except (ValueError, TypeError):
                pass

        # e) Fallback: Try partial match search with query_members (handles various name formats)
        if not user_id and username:
            member = None  # Initialize for this fallback block
            try:
                # Try with broader search on the username for partial matching
                results = await guild.query_members(query=username, limit=100)
                username_lower = username.lower()
                for m in results:
                    # Check exact match on various name formats
                    if (m.name.lower() == username_lower
                            or (m.nick and m.nick.lower() == username_lower)
                            or m.display_name.lower() == username_lower):
                        member = m
                        break
                # If no exact match, use first partial match as fallback
                if not member and results:
                    member = results[0]
                if member:
                    user_id = member.id
            except Exception:
                pass

        # f) Last resort: extract a Discord snowflake embedded in the username string.
        #    Discord's pomelo auto-usernames look like "user<snowflake>" e.g. user1357317173470564433.
        #    We pull out any 17–20 digit number and call fetch_user() directly — no guild cache needed.
        if not user_id and username:
            import re as _re_dm
            snowflake_match = _re_dm.search(r'\b(\d{17,20})\b', str(username))
            if snowflake_match:
                try:
                    potential_id = int(snowflake_match.group(1))
                    fetched_user = await self.bot.fetch_user(potential_id)
                    if fetched_user:
                        user_id = fetched_user.id
                        logger.info(f"[send_dm] Resolved snowflake from username string: {username!r} → {user_id}")
                except Exception:
                    pass

        # ── 2. No user resolved — return failure so action sequence stops ──
        if not user_id:
            logger.warning(f"[send_dm] Could not resolve user from username={username!r}")
            try:
                await interaction.channel.send(
                    f"⚠️ Could not find user **{username}** to send them a DM.", delete_after=10
                )
            except Exception:
                pass
            return False, None

        # ── 3. Deduplication ──────────────────────────────────────────────────
        dedup_key = f"dm_{user_id}_{hash(content or '')}_{hash(str(embed_data) if embed_data else '')}"
        if not deduplicator.should_send(dedup_key, interval=3):
            logger.info(f"[send_dm] Deduplicated DM to user {user_id}")
            return True, None

        # ── 4. Fetch the User object ──────────────────────────────────────────
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        except discord.NotFound:
            logger.warning(f"[send_dm] User {user_id} not found on Discord")
            return False, None
        except Exception as e:
            logger.error(f"[send_dm] Error fetching user {user_id}: {e}")
            return False, None

        if not user:
            return False, None

        # ── 5. Build embed ────────────────────────────────────────────────────
        embed = None
        if embed_data:
            embed = discord.Embed(
                title=embed_data.get("title"),
                description=embed_data.get("description"),
                color=parse_color(embed_data.get("color", "blue"))
            )
            for field in embed_data.get("fields", []):
                embed.add_field(
                    name=field.get("name"),
                    value=field.get("value"),
                    inline=field.get("inline", False)
                )

        # ── 6. Send the DM ────────────────────────────────────────────────────
        try:
            await user.send(content=content, embed=embed)
            logger.info(f"[send_dm] DM sent to {user} ({user_id})")
            return True, None
        except discord.Forbidden:
            # User has DMs disabled — NOT a soft pass - this is a genuine failure
            logger.warning(f"[send_dm] {user} ({user_id}) has DMs disabled")
            try:
                await interaction.channel.send(
                    f"⚠️ Could not DM **{user.display_name}** — they have DMs disabled.",
                    delete_after=10
                )
            except Exception:
                pass
            return False, None
        except discord.HTTPException as e:
            logger.error(f"[send_dm] HTTP error sending DM to {user_id}: {e}")
            return False, None
        except Exception as e:
            logger.error(f"[send_dm] Unexpected error sending DM to {user_id}: {e}")
            return False, None

    async def action_ping(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Pings a user and shows their latency/online status. Resolves by username with cache + API fallback."""
        params = self._normalize_params(params, {
            "user_id": ["user_id", "user", "member_id", "target_id", "user_mention"],
            "username": ["username", "user_name"],
        })
        user_id = params["user_id"]
        username = params["username"]
        guild = interaction.guild

        # ── Resolve member from username ──────────────────────────────────────
        member = None

        if not user_id and not username:
            try:
                await interaction.channel.send("⚠️ No user specified to ping.", delete_after=8)
            except Exception:
                pass
            return True, None

        if username:
            # Handle Discord mention format <@!123456789> or <@123456789> (extract numeric ID directly)
            if isinstance(username, str) and username.startswith("<@"):
                mention_cleaned = username.lstrip("<@!").lstrip("<@").rstrip(">")
                if mention_cleaned.isdigit():
                    user_id = int(mention_cleaned)
                    username = None  # Clear username since we got the ID directly
            # Handle plain @username format
            elif isinstance(username, str) and username.startswith("@"):
                username = username[1:]

            # If we have user_id from mention, skip name-based lookup
            if not user_id and username:
                # a) Exact cache match
                member = (
                    discord.utils.get(guild.members, name=username)
                    or discord.utils.get(guild.members, nick=username)
                    or discord.utils.get(guild.members, display_name=username)
                )

                # b) Case-insensitive cache scan
                if not member:
                    lower = username.lower()
                    for m in guild.members:
                        if (m.name.lower() == lower
                                or (m.nick and m.nick.lower() == lower)
                                or m.display_name.lower() == lower):
                            member = m
                            break

                # c) Discord API member search (finds members not in cache)
                if not member:
                    try:
                        results = await guild.query_members(query=username, limit=5)
                        if results:
                            member = results[0]
                    except Exception:
                        pass

                # d) Numeric ID passed as username string
                if not member:
                    try:
                        user_id = int(str(username).strip())
                    except (ValueError, TypeError):
                        pass

            # e) Fallback: Try broader search with query_members for partial matches
            if not member and username:
                try:
                    results = await guild.query_members(query=username, limit=100)
                    username_lower = username.lower()
                    for m in results:
                        if (m.name.lower() == username_lower
                                or (m.nick and m.nick.lower() == username_lower)
                                or m.display_name.lower() == username_lower):
                            member = m
                            break
                    if not member and results:
                        member = results[0]
                except Exception:
                    pass

        # e) Lookup by user_id if we have one but no member yet
        if not member and user_id:
            member = guild.get_member(int(user_id))
            if not member:
                try:
                    member = await guild.fetch_member(int(user_id))
                except Exception:
                    pass

        # f) Last resort: extract a Discord snowflake embedded in the username string.
        #    Discord's pomelo auto-usernames look like "user<snowflake>" e.g. user1357317173470564433.
        if not member and not user_id and username:
            import re as _re_ping
            snowflake_match = _re_ping.search(r'\b(\d{17,20})\b', str(username))
            if snowflake_match:
                try:
                    potential_id = int(snowflake_match.group(1))
                    member = guild.get_member(potential_id)
                    if not member:
                        member = await guild.fetch_member(potential_id)
                    if member:
                        logger.info(f"[ping] Resolved snowflake from username string: {username!r} → {member.id}")
                except Exception:
                    pass

        # ── Member not found — soft pass ──────────────────────────────────────
        if not member:
            logger.warning(f"[ping] Could not find member: username={username!r} user_id={user_id!r}")
            try:
                await interaction.channel.send(
                    f"⚠️ Could not find member **{username or user_id}** to ping.", delete_after=8
                )
            except Exception:
                pass
            return True, None

        # ── Build and send the ping embed ─────────────────────────────────────
        latency = round(self.bot.latency * 1000, 1) if self.bot.latency else 0

        status_map = {
            "online": "U0001f7e2 Online",
            "idle": "U0001f7e1 Idle",
            "dnd": "U0001f7e0 Do Not Disturb",
            "offline": "⚫ Offline",
        }
        status_text = status_map.get(str(member.status), str(member.status).title())
        joined = member.joined_at.strftime("%Y-%m-%d") if member.joined_at else "Unknown"

        embed = discord.Embed(
            title=f"📣 {member.display_name}",
            description=f"{status_text}\nBot Latency: {latency}ms\nJoined: {joined}",
            color=member.color if member.color != discord.Color.default() else discord.Color.blurple()
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        try:
            await interaction.channel.send(f"{member.mention}", embed=embed, delete_after=30)
        except Exception as e:
            logger.error(f"[ping] Error sending ping embed: {e}")
            return False, None

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
        success = bool(result) if result is not None else True
        if success:
            await self._auto_document_system(interaction.guild.id, "staff_system")
        return success, {"action": "undo_staff_system", "guild_id": interaction.guild.id}

    async def action_setup_economy(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        guild = interaction.guild
        existing = await self._find_existing_resources(guild, "economy")

        if existing["channel"]:
            # Reuse existing resources
            logger.info(f"Reusing existing economy resources: channel '{existing['channel'].name}' - {existing['reasoning']}")
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
            await self._auto_document_system(guild.id, "economy")
            return True, {"action": "undo_economy", "guild_id": guild.id}

        # No existing resources found, create new ones
        from modules.economy import Economy
        system = Economy(self.bot)
        result = await system.setup(interaction, params)
        success = bool(result) if result is not None else True
        if success:
            await self._auto_document_system(guild.id, "economy")
        return success, {"action": "undo_economy", "guild_id": guild.id}

    async def action_setup_trigger_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        from modules.trigger_roles import TriggerRoles
        system = TriggerRoles(self.bot)
        result = await system.setup(interaction, params)
        return bool(result) if result is not None else True, {"action": "undo_trigger_role", "guild_id": interaction.guild.id}

    # --- Setup System Actions (Auto-Setup with Buttons) ---

    async def action_setup_verification(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup verification system with button embed."""
        guild = interaction.guild
        existing = await self._find_existing_resources(guild, "verification")

        if existing["channel"] and existing["role"]:
            # Reuse existing resources
            logger.info(f"Reusing existing verification resources: channel '{existing['channel'].name}', role '{existing['role'].name}' - {existing['reasoning']}")
            dm.update_guild_data(guild.id, "verify_channel", existing["channel"].id)
            dm.update_guild_data(guild.id, "verify_role", existing["role"].id)
            await self._auto_document_system(guild.id, "verification")
            return True, {"action": "undo_verification", "guild_id": guild.id}

        # No existing resources found, create new ones
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_verification(interaction, params)
        if result:
            await self._auto_document_system(guild.id, "verification")
        return result, {"action": "undo_verification", "guild_id": guild.id}

    async def action_setup_tickets(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup ticket system with button embed."""
        guild = interaction.guild
        existing = await self._find_existing_resources(guild, "tickets")

        if existing["channel"] and existing["role"]:
            # Reuse existing resources
            logger.info(f"Reusing existing ticket resources: channel '{existing['channel'].name}', role '{existing['role'].name}' - {existing['reasoning']}")
            dm.update_guild_data(guild.id, "tickets_channel", existing["channel"].id)
            # Store ticket config
            tickets_config = {
                "enabled": True,
                "categories": ["General", "Support", "Billing", "Other"],
                "support_role": existing["role"].id,
                "category": existing.get("category").id if existing.get("category") else None
            }
            dm.update_guild_data(guild.id, "tickets_config", tickets_config)
            await self._auto_document_system(guild.id, "tickets")
            return True, {"action": "undo_tickets", "guild_id": guild.id}

        # No existing resources found, create new ones
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_tickets(interaction, params)
        if result:
            await self._auto_document_system(guild.id, "tickets")
        return result, {"action": "undo_tickets", "guild_id": guild.id}

    async def action_setup_applications(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup applications system with button embed."""
        guild = interaction.guild
        existing = await self._find_existing_resources(guild, "applications")

        if existing["channel"] and existing["role"]:
            # Reuse existing resources
            logger.info(f"Reusing existing application resources: channel '{existing['channel'].name}', role '{existing['role'].name}' - {existing['reasoning']}")
            dm.update_guild_data(guild.id, "applications_channel", existing["channel"].id)
            await self._auto_document_system(guild.id, "applications")
            return True, {"action": "undo_applications", "guild_id": guild.id}

        # No existing resources found, create new ones
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_applications(interaction, params)
        if result:
            await self._auto_document_system(guild.id, "applications")
        return result, {"action": "undo_applications", "guild_id": guild.id}

    async def action_setup_appeals(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup appeals system with button embed."""
        guild = interaction.guild
        existing = await self._find_existing_resources(guild, "appeals")

        if existing["channel"] and existing["role"]:
            # Reuse existing resources
            logger.info(f"Reusing existing appeal resources: channel '{existing['channel'].name}', role '{existing['role'].name}' - {existing['reasoning']}")
            dm.update_guild_data(guild.id, "appeals_channel", existing["channel"].id)
            await self._auto_document_system(guild.id, "appeals")
            return True, {"action": "undo_appeals", "guild_id": guild.id}

        # No existing resources found, create new ones
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_appeals(interaction, params)
        if result:
            await self._auto_document_system(guild.id, "appeals")
        return result, {"action": "undo_appeals", "guild_id": guild.id}

    async def action_setup_moderation(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup moderation logging system."""
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_moderation(interaction, params)
        if result:
            await self._auto_document_system(interaction.guild.id, "moderation")
        return result, {"action": "undo_moderation", "guild_id": interaction.guild.id}

    async def action_setup_logging(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup server logging system."""
        from modules.auto_setup import AutoSetup
        setup = AutoSetup(self.bot)
        result = await setup.setup_logging(interaction, params)
        return result, {"action": "undo_logging", "guild_id": interaction.guild.id}

    async def action_setup_leveling(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup leveling/XP system."""
        guild = interaction.guild
        existing = await self._find_existing_resources(guild, "leveling")

        if existing["channel"]:
            # Reuse existing resources
            logger.info(f"Reusing existing leveling resources: channel '{existing['channel'].name}' - {existing['reasoning']}")
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
                if not guild.roles or not discord.utils.get(guild.roles, name=role_name):
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
            await self._auto_document_system(guild.id, "leveling")
            return True, {"action": "undo_leveling", "guild_id": guild.id}

        # No existing resources found, create new ones
        from modules.gamification import Gamification
        system = Gamification(self.bot)
        result = await system.setup(interaction, params)
        if result:
            await self._auto_document_system(guild.id, "leveling")
        return result, {"action": "undo_leveling", "guild_id": guild.id}

    async def action_setup_welcome(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Setup welcome/leave message system."""
        guild = interaction.guild
        existing = await self._find_existing_resources(guild, "welcome")

        if existing["channel"]:
            # Reuse existing resources
            logger.info(f"Reusing existing welcome resources: channel '{existing['channel'].name}' - {existing['reasoning']}")
            dm.update_guild_data(guild.id, "welcome_channel", existing["channel"].id)
            await self._auto_document_system(guild.id, "welcome")
            return True, {"action": "undo_welcome", "guild_id": guild.id}

        # No existing resources found, create new ones
        from modules.welcome_leave import WelcomeLeaveSystem
        system = WelcomeLeaveSystem(self.bot)
        result = await system.setup(interaction, params)
        success = bool(result) if result is not None else True
        if success:
            await self._auto_document_system(guild.id, "welcome")
        return success, {"action": "undo_welcome", "guild_id": guild.id}

    # --- Aliases: create_*_system → setup_* (AI may use either name) ---

    async def action_create_verify_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        return await self.action_setup_verification(interaction, params)

    async def action_create_tickets_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        return await self.action_setup_tickets(interaction, params)

    async def action_create_applications_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        return await self.action_setup_applications(interaction, params)

    async def action_create_appeals_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        return await self.action_setup_appeals(interaction, params)

    async def action_create_welcome_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        return await self.action_setup_welcome(interaction, params)

    async def action_create_staff_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        return await self.action_setup_staff_system(interaction, params)

    async def action_create_leveling_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        return await self.action_setup_leveling(interaction, params)

    async def action_create_economy_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        return await self.action_setup_economy(interaction, params)

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

    async def action_connect_systems(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Connect two systems so that when trigger_event happens in source_system, action is performed on target_system."""
        guild_id = interaction.guild.id
        
        source_system = params.get("source_system")
        trigger_event = params.get("trigger_event")
        target_system = params.get("target_system")
        action = params.get("action")
        parameters = params.get("parameters", {})
        
        if not all([source_system, trigger_event, target_system, action]):
            return False, {"error": "Missing required parameters: source_system, trigger_event, target_system, action"}
        
        # Load existing connections
        connections = dm.load_json("system_connections", default={})
        guild_connections = connections.get(str(guild_id), [])
        
        # Create connection object
        connection = {
            "source_system": source_system,
            "trigger_event": trigger_event,
            "target_system": target_system,
            "action": action,
            "parameters": parameters
        }
        
        # Add to connections
        guild_connections.append(connection)
        connections[str(guild_id)] = guild_connections
        dm.save_json("system_connections", connections)
        
        return True, {"connection": connection}

    async def action_move_system(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Move a system to a different channel."""
        guild = interaction.guild
        guild_id = guild.id
        
        system = params.get("system")
        new_channel_name = params.get("new_channel_name")
        
        if not system or not new_channel_name:
            return False, {"error": "Missing required parameters: system, new_channel_name"}
        
        # Get current system configuration
        system_config_key = f"{system}_channel"
        current_channel_id = dm.get_guild_data(guild_id, system_config_key)
        
        if not current_channel_id:
            return False, {"error": f"No {system} channel found to move"}
        
        # Find the new channel
        new_channel = discord.utils.get(guild.text_channels, name=new_channel_name.lstrip('#'))
        if not new_channel:
            # Try to create it if it doesn't exist
            try:
                new_channel = await guild.create_text_channel(new_channel_name.lstrip('#'))
            except Exception as e:
                return False, {"error": f"Could not find or create channel '{new_channel_name}': {str(e)}"}
        
        # Update the system configuration
        dm.update_guild_data(guild_id, system_config_key, new_channel.id)
        
        # Also update any related configuration (like verification_role for verification system)
        if system == "verification":
            # For verification, we might also want to update the verification message location
            pass
        
        return True, {
            "system": system,
            "old_channel_id": current_channel_id,
            "new_channel_id": new_channel.id,
            "new_channel_name": new_channel.name
        }

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
        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        username = self._get_param(params, "username", "user_name")
        reason = self._get_param(params, "reason", "cause", "note", default="No reason provided")

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

    async def action_mute_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        reason = self._get_param(params, "reason", "cause", "note", default="No reason provided")
        member = interaction.guild.get_member(user_id) if user_id else None
        if not member:
            return False, {"error": "User not found"}
        try:
            await member.edit(mute=True, reason=reason)
            return True, {"message": f"Muted {member.display_name}"}
        except Exception as e:
            return False, {"error": str(e)}

    async def action_unmute_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        member = interaction.guild.get_member(user_id) if user_id else None
        if not member:
            return False, {"error": "User not found"}
        try:
            await member.edit(mute=False)
            return True, {"message": f"Unmuted {member.display_name}"}
        except Exception as e:
            return False, {"error": str(e)}

    async def action_ban_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Bans a user from the server."""
        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        username = self._get_param(params, "username", "user_name")
        reason = self._get_param(params, "reason", "cause", "note", default="No reason provided")
        delete_days = self._get_param(params, "delete_days", "delete_messages_days", default=0)

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
            try:
                member = await interaction.guild.fetch_member(user_id)
            except (discord.NotFound, discord.HTTPException, Exception) as e:
                logger.debug("Could not fetch member for ban: %s", e)

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
        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        username = self._get_param(params, "username", "user_name")
        duration = self._get_param(params, "duration", "seconds", "time", "minutes", default=60)
        reason = self._get_param(params, "reason", "cause", "note", default="No reason provided")

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
        guild = interaction.guild

        if not guild.me.guild_permissions.manage_channels:
            return False, {"error": "Bot lacks manage_channels permission"}

        # Get parameters, accepting alternatives
        channel_name = self._get_param(params, "channel_name", "channel_mention", "name", "channel")
        channel_id = self._get_param(params, "channel_id", "id")

        channel = None

        # Resolve by ID if provided
        if channel_id:
            try:
                cid = int(str(channel_id).strip().lstrip("<#").rstrip(">"))
                channel = guild.get_channel(cid)
            except (ValueError, TypeError):
                pass

        # If not resolved by ID, try by name
        if not channel and channel_name:
            name = str(channel_name).strip().lstrip("#")  # Strip leading #

            # Exact match first
            channel = discord.utils.get(guild.channels, name=name)
            if not channel:
                # Fuzzy match: case-insensitive partial
                name_lower = name.lower()
                channel = discord.utils.find(lambda c: name_lower in c.name.lower(), guild.channels)

        if not channel:
            error_name = channel_name or channel_id or "unknown"
            return False, {"error": f"Channel '{error_name}' not found"}

        try:
            await channel.delete()
            return True, {"channel_id": channel.id, "channel_name": channel.name}
        except discord.Forbidden:
            return False, {"error": "Missing permission to delete channel"}
        except Exception as e:
            logger.error(f"Error deleting channel: {e}")
            return False, {"error": f"Failed to delete channel: {e}"}

    async def action_announce(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Makes an announcement in a channel."""
        channel_name = self._get_param(params, "channel_name", "channel")
        title = self._get_param(params, "title")
        content = self._get_param(params, "content", "message", "text")
        color = self._get_param(params, "color", "colour", "hex_color", default="#3498db")

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
        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        username = self._get_param(params, "username", "user_name")
        points = self._get_param(params, "points", default=100)

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
        user_id = self._get_param(params, "user_id", "user", "member_id", "target_id", "uid")
        username = self._get_param(params, "username", "user_name")
        points = self._get_param(params, "points", default=100)

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

    async def action_delete_prefix_command(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        cmd_name = params.get("cmd_name")
        if not cmd_name:
            return False, {"error": "Missing cmd_name"}
        guild_id = interaction.guild.id
        commands = dm.get_guild_data(guild_id, "custom_commands", {})
        if cmd_name in commands:
            del commands[cmd_name]
            dm.update_guild_data(guild_id, "custom_commands", commands)
            return True, {"message": f"Deleted command !{cmd_name}"}
        return False, {"error": f"Command !{cmd_name} not found"}

    async def action_unpin_message(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        message_id = params.get("message_id")
        channel_name = params.get("channel_name") or params.get("channel")
        channel = self._resolve_channel(channel_name) if channel_name else interaction.channel
        if not channel:
            return False, {"error": "Channel not found"}
        try:
            msg = await channel.fetch_message(message_id)
            await msg.unpin()
            return True, {"message": "Message unpinned"}
        except Exception as e:
            return False, {"error": str(e)}

    async def action_extract_online_users(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        status_filter = params.get("status", "online")
        online_members = [m.display_name for m in interaction.guild.members if str(m.status) == status_filter and not m.bot]
        return True, {"members": online_members, "count": len(online_members)}

    async def action_send_notification(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        channel_id = params.get("channel")
        message = params.get("message")
        channel = self._resolve_channel(channel_id)
        if not channel:
            return False, {"error": "Channel not found"}
        await channel.send(f"?? **Notification:** {message}")
        return True, {"message": "Notification sent"}

    async def action_create_task(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        name = params.get("name")
        cron = params.get("cron")
        handler = params.get("handler")
        self.bot.scheduler.add_ai_task(name, interaction.guild.id, cron, handler, {})
        return True, {"message": f"Task {name} scheduled"}

    async def action_update_profile(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        user_id = params.get("user_id")
        field = params.get("field")
        value = params.get("value")
        # Simplified profile update for demonstration
        profiles = dm.get_guild_data(interaction.guild.id, "user_profiles", {})
        if str(user_id) not in profiles:
            profiles[str(user_id)] = {}
        profiles[str(user_id)][field] = value
        dm.update_guild_data(interaction.guild.id, "user_profiles", profiles)
        return True, {"message": f"Updated {field} for user {user_id}"}

    async def action_softban_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Bans then immediately unbans a user to clear their messages."""
        user_id = params.get("user_id")
        username = params.get("username")
        days = params.get("delete_messages_days", 7)

        member = await self._resolve_member(interaction.guild, user_id=user_id, username=username)
        if not member:
            return False, {"error": "User not found"}

        try:
            # discord.py 2.0+ uses delete_message_seconds
            await member.ban(reason="Softban (clear messages)", delete_message_seconds=days * 86400)
            await interaction.guild.unban(member, reason="Softban completion")
            return True, None
        except Exception as e:
            return False, {"error": str(e)}

    async def action_clear_reactions(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Clears all reactions from a message."""
        message_id = params.get("message_id")
        channel_name = params.get("channel")
        channel = self._resolve_channel(channel_name) if channel_name else interaction.channel
        if not channel: return False, {"error": "Channel not found"}

        try:
            msg = await channel.fetch_message(message_id)
            await msg.clear_reactions()
            return True, None
        except Exception as e:
            return False, {"error": str(e)}

    async def action_edit_guild(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Edits server settings (name, description, etc.)."""
        guild = interaction.guild
        try:
            edit_kwargs = {}
            if "name" in params: edit_kwargs["name"] = params["name"]
            if "description" in params: edit_kwargs["description"] = params["description"]

            if edit_kwargs:
                await guild.edit(**edit_kwargs)
            return True, None
        except Exception as e:
            return False, {"error": str(e)}

    # --- Server Query Implementation ---

    async def _send_query_result(self, interaction: discord.Interaction, title: str, description: str, fields: List[Dict] = None):
        embed = discord.Embed(title=title, description=description[:4000], color=discord.Color.blue())
        if fields:
            for f in fields[:25]:
                embed.add_field(name=f["name"], value=f["value"], inline=f.get("inline", True))
        await interaction.channel.send(embed=embed)

    async def action_query_server_info(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        data = await self.bot.server_query.query_server_info(interaction.guild.id)
        if "error" in data: return False, data

        fields = [
            {"name": "Members", "value": f"{data['member_count']} ({data['online_count']} online)"},
            {"name": "Channels", "value": str(data['channel_count'])},
            {"name": "Roles", "value": str(data['role_count'])},
            {"name": "Owner", "value": data['owner']},
            {"name": "Created", "value": data['created_at'][:10]}
        ]
        await self._send_query_result(interaction, f"Server Info: {data['name']}", data.get("description", ""), fields)
        return True, None

    async def action_query_channels(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        channels = await self.bot.server_query.query_channels(interaction.guild.id, params.get("type"))
        text = "\n".join([f"• {c['name']} ({c['type']})" for c in channels[:30]])
        await self._send_query_result(interaction, "Server Channels", text or "No channels found")
        return True, None

    async def action_query_roles(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        roles = await self.bot.server_query.query_roles(interaction.guild.id)
        text = "\n".join([f"• {r['name']} (ID: {r['id']})" for r in roles if r['name'] != "@everyone"][:30])
        await self._send_query_result(interaction, "Server Roles", text or "No roles found")
        return True, None

    async def action_query_members(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        members = await self.bot.server_query.query_members(interaction.guild.id, params.get("query"), params.get("limit", 20))
        text = "\n".join([f"• {m['name']} ({m['status']})" for m in members])
        await self._send_query_result(interaction, "Members Search", text or "No members found")
        return True, None

    async def action_query_member_details(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        uid = params.get("user_id")
        if not uid: return False, {"error": "Missing user_id"}
        details = await self.bot.server_query.query_member_details(interaction.guild.id, int(uid))
        if not details: return False, {"error": "User not found"}

        fields = [
            {"name": "Status", "value": details["status"]},
            {"name": "Top Role", "value": details["top_role"]},
            {"name": "Joined", "value": details["joined_at"][:10] if details["joined_at"] else "N/A"},
            {"name": "Roles", "value": ", ".join(details["roles"][:10])}
        ]
        await self._send_query_result(interaction, f"User Details: {details['name']}", "", fields)
        return True, None

    async def action_query_economy_leaderboard(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        lb = await self.bot.server_query.query_economy_leaderboard(interaction.guild.id, params.get("limit", 10))
        text = "\n".join([f"**{i+1}.** {e['name']} - {e['coins']} coins" for i, e in enumerate(lb)])
        await self._send_query_result(interaction, "Economy Leaderboard", text or "No data")
        return True, None

    async def action_query_xp_leaderboard(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        lb = await self.bot.server_query.query_xp_leaderboard(interaction.guild.id, params.get("limit", 10))
        text = "\n".join([f"**{i+1}.** {e['name']} - Level {e['level']} ({e['xp']} XP)" for i, e in enumerate(lb)])
        await self._send_query_result(interaction, "Leveling Leaderboard", text or "No data")
        return True, None

    async def action_query_pending_applications(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        apps = await self.bot.server_query.query_pending_applications(interaction.guild.id)
        text = "\n".join([f"• {a['username']} (Applied: {a['applied_at']})" for a in apps])
        await self._send_query_result(interaction, "Pending Staff Applications", text or "No pending applications")
        return True, None

    async def action_query_active_shifts(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        shifts = await self.bot.server_query.query_active_shifts(interaction.guild.id)
        text = "\n".join([f"• {s['username']} (Started: {s['start_time']})" for s in shifts])
        await self._send_query_result(interaction, "Active Staff Shifts", text or "No active shifts")
        return True, None

    async def action_query_recent_messages(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        cid = params.get("channel_id")
        if not cid: return False, {"error": "Missing channel_id"}
        msgs = await self.bot.server_query.query_recent_messages(int(cid), params.get("limit", 10))
        text = "\n".join([f"**{m['author']}:** {m['content'][:100]}" for m in msgs])
        await self._send_query_result(interaction, "Recent Messages", text or "No messages found")
        return True, None
    async def action_warn_user(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Warns a user (moderation)."""
        params = self._normalize_params(params, {
            "user_id": ["user_id", "user", "member_id", "target_id", "user_mention"],
            "username": ["username", "user_name"],
            "reason": ["reason", "cause", "note"],
        })
        user_id = params["user_id"]
        username = params["username"]
        reason = params["reason"]
        guild_id = interaction.guild.id

        if not user_id and username:
            member = discord.utils.get(interaction.guild.members, name=username) or \
                     discord.utils.get(interaction.guild.members, nick=username) or \
                     discord.utils.get(interaction.guild.members, display_name=username)
            if member:
                user_id = member.id

        if not user_id:
            return False, None

        member = interaction.guild.get_member(user_id)
        if not member:
            try:
                member = await interaction.guild.fetch_member(user_id)
            except discord.NotFound:
                return False, None

        try:
            if hasattr(self.bot, 'moderation'):
                history = self.bot.moderation.get_user_history(guild_id, user_id)
                history.warnings += 1
                history.violation_count += 1
                history.last_violation = time.time()
                self.bot.moderation.save_user_history(guild_id, user_id, history)

            logger.info("Issued warning to %s in guild %d: %s", member.display_name, guild_id, reason)

            try:
                embed = discord.Embed(title="⚠️ Warning Issued", color=discord.Color.gold())
                embed.add_field(name="Server", value=interaction.guild.name)
                embed.add_field(name="Reason", value=reason)
                embed.timestamp = dt.datetime.now(dt.timezone.utc)
                await member.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

            return True, {"user_id": user_id, "action": "warn", "reason": reason}
        except Exception as e:
            logger.error(f"Error warning user: {e}")
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
        params = self._normalize_params(params, {
            "channel_name": ["channel", "channel_name"],
            "content": ["content", "message", "text"],
        })
        channel_name = params["channel_name"]
        content = params["content"]

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

    async def action_remove_reaction(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Removes emoji reaction from a message."""
        channel_name = params.get("channel") or params.get("channel_name")
        message_id = params.get("message_id")
        emoji = params.get("emoji", "")

        if not emoji:
            return False, None

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            return False, None

        try:
            msg = await channel.fetch_message(message_id) if message_id else None
            if msg:
                await msg.remove_reaction(emoji)
            return True, {"emoji": emoji}
        except Exception as e:
            logger.error(f"Error removing reaction: {e}")
            return False, None

    async def action_delete_message(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Deletes a specific message."""
        channel_name = params.get("channel") or params.get("channel_name")
        message_id = params.get("message_id")

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else interaction.channel
        if not channel:
            return False, None

        try:
            msg = await channel.fetch_message(message_id) if message_id else None
            if msg:
                await msg.delete()
            return True, {"message_id": message_id}
        except Exception as e:
            logger.error(f"Error deleting message: {e}")
            return False, None

    async def action_create_voice_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Creates a voice channel."""
        name = params.get("name", "Voice Channel")
        category = params.get("category")

        category_obj = discord.utils.get(interaction.guild.categories, name=category) if category else None

        try:
            channel = await interaction.guild.create_voice_channel(name, category=category_obj)
            return True, {"channel": channel.name}
        except Exception as e:
            logger.error(f"Error creating voice channel: {e}")
            return False, None

    async def action_create_text_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Creates a text channel."""
        name = params.get("name", "text-channel")
        category = params.get("category")

        category_obj = discord.utils.get(interaction.guild.categories, name=category) if category else None

        try:
            channel = await interaction.guild.create_text_channel(name, category=category_obj)
            return True, {"channel": channel.name}
        except Exception as e:
            logger.error(f"Error creating text channel: {e}")
            return False, None

    async def action_create_category_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Creates a category with optional privacy settings."""
        guild = interaction.guild
        name = params.get("name", "New Category")
        private = params.get("private", False)
        allowed_roles = params.get("allowed_roles", [])
        denied_roles = params.get("denied_roles", [])

        if private and "@everyone" not in denied_roles:
            denied_roles = list(denied_roles) + ["@everyone"]

        try:
            category = await guild.create_category(name)

            if denied_roles or allowed_roles:
                await self._set_channel_permissions(category, guild, allowed_roles, denied_roles)
                for child in category.channels:
                    await self._set_channel_permissions(child, guild, allowed_roles, denied_roles)

            self._track_artifact("category", category.id, category.name)
            return True, {"category": category.name}
        except Exception as e:
            logger.error(f"Error creating category: {e}")
            return False, None

    async def action_create_category(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Alias for create_category_channel — supports name, private, allowed_roles, denied_roles."""
        return await self.action_create_category_channel(interaction, params)

    async def action_edit_channel_bitrate(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Sets voice channel bitrate."""
        channel_name = params.get("channel_name")
        bitrate = params.get("bitrate", 128000)

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else None
        if not channel or not channel.voice:
            return False, None

        try:
            await channel.edit(bitrate=bitrate)
            return True, {"bitrate": bitrate}
        except Exception as e:
            logger.error(f"Error editing bitrate: {e}")
            return False, None

    async def action_edit_channel_user_limit(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Sets voice channel user limit."""
        channel_name = params.get("channel_name")
        user_limit = params.get("user_limit", 0)

        channel = discord.utils.get(interaction.guild.channels, name=channel_name) if channel_name else None
        if not channel or not channel.voice:
            return False, None

        try:
            await channel.edit(user_limit=user_limit)
            return True, {"user_limit": user_limit}
        except Exception as e:
            logger.error(f"Error editing user limit: {e}")
            return False, None

    async def action_follow_announcement_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Follows an announcement channel."""
        source = params.get("source_channel")
        target = params.get("target_channel")

        source_channel = discord.utils.get(interaction.guild.channels, name=source) if source else None
        target_channel = discord.utils.get(interaction.guild.channels, name=target) if target else None

        if not source_channel or not target_channel:
            return False, None

        try:
            await source_channel.followers.append(target_channel)
            return True, {"source": source, "target": target}
        except Exception as e:
            logger.error(f"Error following channel: {e}")
            return False, None

    async def action_create_scheduled_event(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Creates a scheduled event."""
        name = params.get("name", "Event")
        description = params.get("description", "")
        start_time = params.get("start_time")  # ISO format
        end_time = params.get("end_time")
        location = params.get("location", "Voice Channel")

        import datetime
        try:
            start = datetime.datetime.fromisoformat(start_time) if start_time else datetime.datetime.utcnow() + datetime.timedelta(hours=1)
            end = datetime.datetime.fromisoformat(end_time) if end_time else start + datetime.timedelta(hours=1)

            event = await interaction.guild.create_scheduled_event(
                name=name,
                description=description,
                start_time=start,
                end_time=end,
                location=location
            )
            return True, {"event": event.name}
        except Exception as e:
            logger.error(f"Error creating event: {e}")
            return False, None



    async def _merge_channel_permission(self, channel, role, **kwargs):
        """Merge permission changes into existing overwrites instead of replacing them."""
        existing = channel.overwrites_for(role)
        for perm_name, perm_value in kwargs.items():
            setattr(existing, perm_name, perm_value)
        await channel.set_permissions(role, overwrite=existing)

    async def action_allow_channel_permission(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Allows a permission for a role in a channel (merges with existing overwrites)."""

        # Improved parameter resolution - accept alternative parameter names
        channel_ident = None
        for key in ["channel", "channel_name", "channel_id"]:
            if key in params and params[key]:
                channel_ident = params[key]
                break

        role_ident = None
        for key in ["role", "role_name", "role_id"]:
            if key in params and params[key]:
                role_ident = params[key]
                break

        permission = None
        for key in ["permission", "perm", "permission_name"]:
            if key in params and params[key]:
                permission = params[key]
                break

        if not permission:
            permission = "send_messages"

        if not channel_ident:
            logger.error("allow_channel_permission: No channel identifier provided")
            return False, {"error": "Channel identifier is required. Use 'channel', 'channel_name', or 'channel_id'."}

        if not role_ident:
            logger.error("allow_channel_permission: No role identifier provided")
            return False, {"error": "Role identifier is required. Use 'role', 'role_name', or 'role_id'."}

        # Resolve channel with fuzzy matching
        channel = None
        if isinstance(channel_ident, int) or (isinstance(channel_ident, str) and channel_ident.isdigit()):
            # Try as ID first
            try:
                cid = int(channel_ident)
                channel = interaction.guild.get_channel(cid)
                if channel:
                    logger.info(f"allow_channel_permission: Resolved channel by ID '{channel_ident}' -> '{channel.name}'")
            except (ValueError, TypeError):
                pass

        if not channel and isinstance(channel_ident, str):
            # Try exact match first
            channel = discord.utils.get(interaction.guild.channels, name=channel_ident)
            if channel:
                logger.info(f"allow_channel_permission: Resolved channel by exact match '{channel_ident}' -> '{channel.name}'")
            else:
                # Try fuzzy matching
                channel_names = [c.name for c in interaction.guild.channels]
                fuzzy_match = self._fuzzy_match(channel_ident, channel_names)
                if fuzzy_match:
                    channel = discord.utils.get(interaction.guild.channels, name=fuzzy_match)
                    logger.info(f"allow_channel_permission: Resolved channel by fuzzy match '{channel_ident}' -> '{channel.name}'")
                else:
                    logger.error(f"allow_channel_permission: Channel '{channel_ident}' not found, available: {channel_names[:5]}...")
                    return False, {"error": f"Channel '{channel_ident}' not found. Available channels include: {', '.join(channel_names[:5])}"}

        if not channel:
            logger.error(f"allow_channel_permission: Could not resolve channel '{channel_ident}'")
            return False, {"error": f"Channel '{channel_ident}' not found"}

        # Resolve role with fuzzy matching
        role = None
        if isinstance(role_ident, int) or (isinstance(role_ident, str) and role_ident.isdigit()):
            # Try as ID first
            try:
                rid = int(role_ident)
                role = interaction.guild.get_role(rid)
                if role:
                    logger.info(f"allow_channel_permission: Resolved role by ID '{role_ident}' -> '{role.name}'")
            except (ValueError, TypeError):
                pass

        if not role:
            # Use the existing _resolve_role method which handles names well
            role = await self._resolve_role(interaction.guild, **{k: role_ident for k in ["role_name", "name"]})
            if role:
                logger.info(f"allow_channel_permission: Resolved role '{role_ident}' -> '{role.name}'")
            else:
                # Try fuzzy matching for role names
                role_names = [r.name for r in interaction.guild.roles]
                fuzzy_match = self._fuzzy_match(role_ident, role_names)
                if fuzzy_match:
                    role = discord.utils.get(interaction.guild.roles, name=fuzzy_match)
                    logger.info(f"allow_channel_permission: Resolved role by fuzzy match '{role_ident}' -> '{role.name}'")
                else:
                    logger.error(f"allow_channel_permission: Role '{role_ident}' not found, available: {role_names[:5]}...")
                    return False, {"error": f"Role '{role_ident}' not found. Available roles include: {', '.join(role_names[:5])}"}

        if not role:
            logger.error(f"allow_channel_permission: Could not resolve role '{role_ident}'")
            return False, {"error": f"Role '{role_ident}' not found"}

        # Check idempotency - if permission is already allowed, return success
        existing_overwrites = channel.overwrites_for(role)
        perm_names = {
            "send_messages": "send_messages",
            "read_messages": "read_messages",
            "view_channel": "view_channel",
            "connect": "connect",
            "speak": "speak",
            "mute_members": "mute_members",
            "deafen_members": "deafen_members",
            "move_members": "move_members",
            "manage_messages": "manage_messages",
            "manage_channels": "manage_channels",
            "attach_files": "attach_files",
            "embed_links": "embed_links",
            "add_reactions": "add_reactions",
            "use_external_emojis": "use_external_emojis",
            "manage_permissions": "manage_permissions",
            "create_instant_invite": "create_instant_invite",
            "mention_everyone": "mention_everyone",
            "manage_webhooks": "manage_webhooks",
            "read_message_history": "read_message_history",
            "use_application_commands": "use_application_commands",
            "stream": "stream",
            "use_voice_activation": "use_voice_activation",
        }
        perm_attr = perm_names.get(permission, permission)

        if hasattr(existing_overwrites, perm_attr) and getattr(existing_overwrites, perm_attr) is True:
            logger.info(f"allow_channel_permission: Permission '{permission}' already allowed for role '{role.name}' in channel '{channel.name}'")
            return True, {"role": role.name, "permission": permission, "already_allowed": True}

        try:
            await self._merge_channel_permission(channel, role, **{perm_attr: True})
            logger.info(f"allow_channel_permission: Successfully allowed permission '{permission}' for role '{role.name}' in channel '{channel.name}'")
            return True, {"role": role.name, "permission": permission}
        except Exception as e:
            logger.error(f"Error allowing permission: {e}")
            return False, {"error": f"Failed to allow permission: {str(e)}"}

        channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        role = await self._resolve_role(interaction.guild, role_name=role_name)

        if not channel or not role:
            logger.error(f"allow_channel_permission: channel='{channel_name}' found={channel is not None}, role='{role_name}' found={role is not None}")
            return False, None

        try:
            perm_names = {
                "send_messages": "send_messages",
                "read_messages": "read_messages",
                "view_channel": "view_channel",
                "connect": "connect",
                "speak": "speak",
                "mute_members": "mute_members",
                "deafen_members": "deafen_members",
                "move_members": "move_members",
                "manage_messages": "manage_messages",
                "manage_channels": "manage_channels",
                "attach_files": "attach_files",
                "embed_links": "embed_links",
                "add_reactions": "add_reactions",
                "use_external_emojis": "use_external_emojis",
                "manage_permissions": "manage_permissions",
                "create_instant_invite": "create_instant_invite",
                "mention_everyone": "mention_everyone",
                "manage_webhooks": "manage_webhooks",
                "read_message_history": "read_message_history",
                "use_application_commands": "use_application_commands",
                "stream": "stream",
                "use_voice_activation": "use_voice_activation",
            }
            perm_attr = perm_names.get(permission, permission)
            await self._merge_channel_permission(channel, role, **{perm_attr: True})
            return True, {"role": role_name, "permission": permission}
        except Exception as e:
            logger.error(f"Error allowing permission: {e}")
            return False, None

    async def action_deny_channel_permission(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Denies a permission for a role in a channel (merges with existing overwrites)."""
        channel_name = params.get("channel") or params.get("channel_name")
        role_name = params.get("role_name")
        permission = params.get("permission", "send_messages")

        if not channel_name or not role_name:
            return False, None

        channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        role = await self._resolve_role(interaction.guild, role_name=role_name)

        if not channel or not role:
            logger.error(f"deny_channel_permission: channel='{channel_name}' found={channel is not None}, role='{role_name}' found={role is not None}")
            return False, None

        try:
            perm_names = {
                "send_messages": "send_messages",
                "read_messages": "read_messages",
                "view_channel": "view_channel",
                "connect": "connect",
                "speak": "speak",
                "mute_members": "mute_members",
                "deafen_members": "deafen_members",
                "move_members": "move_members",
                "manage_messages": "manage_messages",
                "manage_channels": "manage_channels",
                "attach_files": "attach_files",
                "embed_links": "embed_links",
                "add_reactions": "add_reactions",
                "use_external_emojis": "use_external_emojis",
                "manage_permissions": "manage_permissions",
                "read_message_history": "read_message_history",
            }
            perm_attr = perm_names.get(permission, permission)
            await self._merge_channel_permission(channel, role, **{perm_attr: False})
            return True, {"role": role_name, "permission": permission}
        except Exception as e:
            logger.error(f"Error denying permission: {e}")
            return False, None

    async def action_deny_all_channels_for_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Denies a role from viewing ALL channels (text, voice, and categories) in the server."""
        role_name = params.get("role_name")

        if not role_name:
            return False, None

        role = await self._resolve_role(interaction.guild, role_name=role_name)
        if not role:
            return False, None

        channels_updated = 0

        try:
            all_channels = list(interaction.guild.categories) + list(interaction.guild.text_channels) + list(interaction.guild.voice_channels)
            for channel in all_channels:
                try:
                    await self._merge_channel_permission(channel, role, view_channel=False, send_messages=False)
                    channels_updated += 1
                except Exception:
                    pass

            return True, {"channels_updated": channels_updated, "role": role_name}
        except Exception as e:
            logger.error(f"Error denying all channels: {e}")
            return False, None

    async def action_allow_all_channels_for_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Allows a role to view ALL channels (text, voice, and categories) in the server."""
        role_name = params.get("role_name")

        if not role_name:
            return False, None

        role = await self._resolve_role(interaction.guild, role_name=role_name)
        if not role:
            return False, None

        channels_updated = 0

        try:
            all_channels = list(interaction.guild.categories) + list(interaction.guild.text_channels) + list(interaction.guild.voice_channels)
            for channel in all_channels:
                try:
                    await self._merge_channel_permission(channel, role, view_channel=True, send_messages=True)
                    channels_updated += 1
                except Exception:
                    pass

            return True, {"channels_updated": channels_updated, "role": role_name}
        except Exception as e:
            logger.error(f"Error allowing all channels: {e}")
            return False, None

    async def action_deny_category_for_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Denies a role in a category AND all its child channels."""
        category_name = params.get("category_name") or params.get("category")
        role_name = params.get("role_name")

        if not category_name or not role_name:
            return False, None

        category = discord.utils.get(interaction.guild.categories, name=category_name)
        role = await self._resolve_role(interaction.guild, role_name=role_name)

        if not category or not role:
            return False, None

        channels_updated = 0

        try:
            await self._merge_channel_permission(category, role, view_channel=False, send_messages=False)
            channels_updated += 1

            for channel in category.channels:
                try:
                    await self._merge_channel_permission(channel, role, view_channel=False, send_messages=False)
                    channels_updated += 1
                except Exception:
                    pass

            return True, {"channels_updated": channels_updated, "category": category_name, "role": role_name}
        except Exception as e:
            logger.error(f"Error denying category: {e}")
            return False, None

    async def action_make_channel_private(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Makes an existing channel private: denies @everyone view_channel and allows specified roles."""
        guild = interaction.guild
        channel_name = params.get("channel") or params.get("channel_name")
        allowed_roles = params.get("allowed_roles", [])

        if not channel_name:
            try:
                await interaction.channel.send("⚠️ No channel name specified for make_channel_private.", delete_after=10)
            except Exception:
                pass
            return False, None

        # Case-insensitive channel lookup
        channel = discord.utils.get(guild.channels, name=channel_name)
        if not channel:
            lower = channel_name.lower()
            channel = next((c for c in guild.channels if c.name.lower() == lower), None)
        if not channel:
            logger.error(f"make_channel_private: '{channel_name}' not found")
            try:
                await interaction.channel.send(
                    f"⚠️ Could not find channel **{channel_name}**.", delete_after=10
                )
            except Exception:
                pass
            return False, None

        try:
            # Deny @everyone
            await self._merge_channel_permission(channel, guild.default_role, view_channel=False, send_messages=False)

            # Allow each specified role
            for role_name in allowed_roles:
                role = await self._resolve_role(guild, role_name=role_name)
                if role:
                    await self._merge_channel_permission(channel, role, view_channel=True, send_messages=True, read_message_history=True)

            logger.info(f"Made channel '{channel.name}' private. Allowed roles: {allowed_roles}")
            return True, {"channel": channel.name, "allowed_roles": allowed_roles}
        except discord.Forbidden:
            logger.error(f"make_channel_private: bot lacks Manage Channels permission in guild {guild.id}")
            try:
                await interaction.channel.send(
                    "⚠️ I don't have permission to manage channel permissions. "
                    "Please grant me the **Manage Channels** permission.", delete_after=15
                )
            except Exception:
                pass
            return False, None
        except Exception as e:
            logger.error(f"make_channel_private: unexpected error: {e}", exc_info=True)
            try:
                await interaction.channel.send(f"⚠️ Error making channel private: {e}", delete_after=10)
            except Exception:
                pass
            return False, None

    async def action_make_category_private(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Makes an existing category private: denies @everyone and allows specified roles on the category and all child channels."""
        guild = interaction.guild
        category_name = params.get("category") or params.get("category_name")
        allowed_roles = params.get("allowed_roles", [])

        if not category_name:
            try:
                await interaction.channel.send("⚠️ No category name specified for make_category_private.", delete_after=10)
            except Exception:
                pass
            return False, None

        # Case-insensitive category lookup
        category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            lower = category_name.lower()
            category = next((c for c in guild.categories if c.name.lower() == lower), None)
        if not category:
            available = ", ".join(f"**{c.name}**" for c in guild.categories) or "none found"
            logger.error(f"make_category_private: '{category_name}' not found. Available: {[c.name for c in guild.categories]}")
            try:
                await interaction.channel.send(
                    f"⚠️ Could not find category **{category_name}**. "
                    f"Available categories: {available}", delete_after=15
                )
            except Exception:
                pass
            return False, None

        try:
            channels_updated = 0

            # Deny @everyone on the category itself
            await self._merge_channel_permission(category, guild.default_role, view_channel=False, send_messages=False)
            channels_updated += 1

            # Allow each specified role on the category
            for role_name in allowed_roles:
                role = await self._resolve_role(guild, role_name=role_name)
                if role:
                    await self._merge_channel_permission(category, role, view_channel=True, send_messages=True, read_message_history=True)

            # Do the same for all child channels
            for child in category.channels:
                try:
                    await self._merge_channel_permission(child, guild.default_role, view_channel=False, send_messages=False)
                    channels_updated += 1
                    for role_name in allowed_roles:
                        role = await self._resolve_role(guild, role_name=role_name)
                        if role:
                            await self._merge_channel_permission(child, role, view_channel=True, send_messages=True, read_message_history=True)
                except Exception:
                    pass

            logger.info(f"Made category '{category.name}' private. Updated {channels_updated} channels. Allowed: {allowed_roles}")
            return True, {"category": category.name, "channels_updated": channels_updated, "allowed_roles": allowed_roles}
        except discord.Forbidden:
            logger.error(f"make_category_private: bot lacks Manage Channels permission in guild {guild.id}")
            try:
                await interaction.channel.send(
                    "⚠️ I don't have permission to manage channel permissions. "
                    "Please grant me the **Manage Channels** permission.", delete_after=15
                )
            except Exception:
                pass
            return False, None
        except Exception as e:
            logger.error(f"make_category_private: unexpected error: {e}", exc_info=True)
            try:
                await interaction.channel.send(f"⚠️ Error making category private: {e}", delete_after=10)
            except Exception:
                pass
            return False, None

    async def action_create_role_with_permissions(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Creates a role with specific permissions."""
        guild = interaction.guild
        name = params.get("name")

        if not name:
            return False, None

        if not guild.me.guild_permissions.manage_roles:
            logger.error("Bot lacks manage_roles permission in guild %s", guild.id)
            return False, None

        existing = discord.utils.get(guild.roles, name=name)
        if existing:
            logger.info("Role '%s' already exists, skipping creation", name)
            return True, None

        color_hex = params.get("color", "#99AAB5").replace("#", "")
        color = discord.Color(int(color_hex, 16))
        hoist = params.get("hoist", True)
        mentionable = params.get("mentionable", False)

        perm_params = params.get("permissions", {})
        # Compatibility mapping
        if "use_slash_commands" in perm_params:
            perm_params["use_application_commands"] = perm_params.pop("use_slash_commands")
        permissions = discord.Permissions(
            view_channel=perm_params.get("view_channel", True),
            send_messages=perm_params.get("send_messages", True),
            read_messages=perm_params.get("read_messages", True),
            manage_channels=perm_params.get("manage_channels", False),
            manage_roles=perm_params.get("manage_roles", False),
            kick_members=perm_params.get("kick_members", False),
            ban_members=perm_params.get("ban_members", False),
            moderate_members=perm_params.get("moderate_members", False),
            manage_messages=perm_params.get("manage_messages", False),
            mention_everyone=perm_params.get("mention_everyone", False),
            attach_files=perm_params.get("attach_files", True),
            embed_links=perm_params.get("embed_links", True),
            add_reactions=perm_params.get("add_reactions", True),
            use_external_emojis=perm_params.get("use_external_emojis", True),
            connect=perm_params.get("connect", True),
            speak=perm_params.get("speak", True),
            read_message_history=perm_params.get("read_message_history", True),
            use_application_commands=perm_params.get("use_application_commands", True),
            administrator=perm_params.get("administrator", False),
            manage_guild=perm_params.get("manage_guild", False),
            mute_members=perm_params.get("mute_members", False),
            deafen_members=perm_params.get("deafen_members", False),
            move_members=perm_params.get("move_members", False),
            manage_webhooks=perm_params.get("manage_webhooks", False),
            manage_expressions=perm_params.get("manage_expressions", False),
            create_instant_invite=perm_params.get("create_instant_invite", True),
        )

        role = await guild.create_role(
            name=name,
            color=color,
            permissions=permissions,
            hoist=hoist,
            mentionable=mentionable,
            reason="AI Action - create_role_with_permissions"
        )

        self._track_artifact("role", role.id, role.name)
        logger.info("Created role with permissions: %s", role.name)
        return True, {"action": "delete_role", "role_id": role.id}

    async def action_edit_channel_permissions(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Edit permissions for a role on a specific channel (merges with existing overwrites)."""
        channel_name = params.get("channel") or params.get("channel_name")
        role_name = params.get("role_name")
        permissions = params.get("permissions", {})

        if not channel_name or not role_name:
            return False, None

        channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        role = await self._resolve_role(interaction.guild, role_name=role_name)

        if not channel or not role:
            return False, None

        try:
            existing = channel.overwrites_for(role)
            for perm_name, perm_value in permissions.items():
                if isinstance(perm_value, bool) or perm_value is None:
                    setattr(existing, perm_name, perm_value)
            await channel.set_permissions(role, overwrite=existing)
            return True, {"channel": channel_name, "role": role_name, "permissions": permissions}
        except Exception as e:
            logger.error(f"Error editing channel permissions: {e}")
            return False, None

    async def action_edit_channel(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Edit channel properties (name, topic, slowmode, category, nsfw, position)."""
        channel_name = params.get("channel_name") or params.get("name")

        if not channel_name:
            return False, None

        channel = discord.utils.get(interaction.guild.channels, name=channel_name)
        if not channel:
            return False, None

        try:
            edit_kwargs = {}
            if "new_name" in params:
                edit_kwargs["name"] = params["new_name"]
            if "topic" in params:
                edit_kwargs["topic"] = params["topic"]
            if "slowmode_delay" in params:
                edit_kwargs["slowmode_delay"] = params["slowmode_delay"]
            if "nsfw" in params:
                edit_kwargs["nsfw"] = params["nsfw"]
            if "position" in params:
                edit_kwargs["position"] = params["position"]
            if "category" in params:
                cat = discord.utils.get(interaction.guild.categories, name=params["category"])
                if cat:
                    edit_kwargs["category"] = cat

            if edit_kwargs:
                await channel.edit(**edit_kwargs)
            return True, {"channel": channel_name}
        except Exception as e:
            logger.error(f"Error editing channel: {e}")
            return False, None

    async def action_edit_role(self, interaction: discord.Interaction, params: Dict[str, Any]) -> Tuple[bool, Optional[Dict]]:
        """Edit role properties (name, color, hoist, mentionable, permissions)."""
        role_name = params.get("role_name") or params.get("name")

        if not role_name:
            return False, None

        role = discord.utils.get(interaction.guild.roles, name=role_name)
        if not role:
            return False, None

        try:
            edit_kwargs = {}
            if "new_name" in params:
                edit_kwargs["name"] = params["new_name"]
            if "color" in params:
                edit_kwargs["color"] = parse_color(params["color"])
            if "hoist" in params:
                edit_kwargs["hoist"] = params["hoist"]
            if "mentionable" in params:
                edit_kwargs["mentionable"] = params["mentionable"]
            if "permissions" in params:
                perm_params = params["permissions"]
                current_perms = role.permissions
                perm_value = current_perms.value
                new_perms = discord.Permissions(perm_value)
                for perm_name, perm_val in perm_params.items():
                    if hasattr(new_perms, perm_name):
                        setattr(new_perms, perm_name, perm_val)
                edit_kwargs["permissions"] = new_perms

            if edit_kwargs:
                await role.edit(**edit_kwargs, reason="AI Action - edit_role")
            return True, {"role": role_name}
        except Exception as e:
            logger.error(f"Error editing role: {e}")
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

            elif undo_action == "batch_undo":
                results = []
                for sub_undo in undo_data.get("undo_data", []):
                    res = await self._execute_undo(interaction, sub_undo.get("action"), sub_undo)
                    results.append(res)
                return all(results)

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

    async def handle_help_system(self, message: discord.Message, system: str) -> bool:
        """Handle !help <system> command - shows help for a specific system."""
        guild_id = message.guild.id
        custom_cmds = dm.get_guild_data(guild_id, "custom_commands", {})

        system_commands = {k: v for k, v in custom_cmds.items() if k.startswith(system)}
        if not system_commands:
            await message.channel.send(f"No commands found for system '{system}'. Try !help for all commands.")
            return True

        embed = discord.Embed(
            title=f"📋 {system.capitalize()} System Commands",
            description=f"Commands for the {system} system:",
            color=discord.Color.green()
        )

        cmd_list = []
        for cmd_name in sorted(system_commands.keys()):
            cmd_list.append(f"**!{cmd_name}**")

        embed.add_field(
            name="Commands",
            value="\n".join(cmd_list),
            inline=False
        )

        await message.channel.send(embed=embed)
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

    def _resolve_channel(self, channel_identifier):
        """Resolve a channel from the guild by name, ID, or mention format."""
        guild = getattr(self, 'guild', None)
        if guild is None:
            if hasattr(self, '_guild_context') and self._guild_context:
                guild = self._guild_context

        if guild is None:
            import inspect
            for frame_record in inspect.stack():
                local_self = frame_record.frame.f_locals.get('self')
                if hasattr(local_self, 'guild'):
                    guild = local_self.guild
                    break

        if guild is None:
            return None

        # Check if channel_identifier is an ID (int)
        if isinstance(channel_identifier, int):
            return guild.get_channel(channel_identifier)
        # Check if channel_identifier is a string mention (e.g. <#1234567890>)
        elif isinstance(channel_identifier, str) and channel_identifier.startswith('<#') and channel_identifier.endswith('>'):
            try:
                channel_id = int(channel_identifier[2:-1])
                return guild.get_channel(channel_id)
            except Exception:
                pass
        # Check if channel_identifier is a name
        elif isinstance(channel_identifier, str):
            for channel in guild.channels:
                if channel.name == channel_identifier or channel.name == channel_identifier.lstrip('#'):
                    return channel
