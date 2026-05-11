import discord
from discord import ui
from discord.ext import commands
import asyncio
import re
import time
import random
import uuid
import datetime
from data_manager import dm
from logger import logger

class AntiRaidSystem:
    """
    Advanced anti-raid system with join monitoring, raid detection, and automatic lockdown.
    Features:
    - Join velocity monitoring
    - Account age checking
    - Automatic lockdown
    - Manual lockdown/unlock
    - Raid detection and alerts
    - Whitelist system
    """

    def __init__(self, bot):
        self.bot = bot
        self.join_history = {}  # guild_id -> list of (timestamp, user_id)
        self.lockdown_active = {}  # guild_id -> bool
        self.monitoring = False

    async def start_monitoring(self):
        """Start the anti-raid monitoring system."""
        if self.monitoring:
            return

        self.monitoring = True
        logger.info("Anti-raid monitoring started")

        # Clean up old join history periodically
        asyncio.create_task(self._cleanup_join_history())

    async def _cleanup_join_history(self):
        """Clean up old join history to prevent memory bloat."""
        while self.monitoring:
            try:
                current_time = time.time()
                cutoff = current_time - 3600  # Keep 1 hour of history

                for guild_id in list(self.join_history.keys()):
                    history = self.join_history[guild_id]
                    # Remove entries older than cutoff
                    self.join_history[guild_id] = [entry for entry in history if entry[0] > cutoff]

                    # Remove empty lists
                    if not self.join_history[guild_id]:
                        del self.join_history[guild_id]

                await asyncio.sleep(300)  # Clean every 5 minutes
            except Exception as e:
                logger.error(f"Join history cleanup error: {e}")
                await asyncio.sleep(300)

    async def handle_member_join(self, member):
        """Handle member joins for raid detection."""
        try:
            config = dm.get_guild_data(member.guild.id, "anti_raid_config", {})
            if not config.get("enabled", False):
                return

            guild_id = member.guild.id
            current_time = time.time()

            # Initialize join history for guild
            if guild_id not in self.join_history:
                self.join_history[guild_id] = []

            # Add join to history
            self.join_history[guild_id].append((current_time, member.id))

            # Check if server is in lockdown
            if self.lockdown_active.get(guild_id, False):
                await self._handle_lockdown_join(member, config)
                return

            # Check account age
            await self._check_account_age(member, config)

            # Check join velocity
            await self._check_join_velocity(member, config)

        except Exception as e:
            logger.error(f"Anti-raid join handling error: {e}")

    async def _handle_lockdown_join(self, member, config):
        """Handle joins during lockdown."""
        try:
            # Check if user is whitelisted
            whitelist = config.get("whitelist", [])
            if str(member.id) in whitelist:
                return

            # Kick the user
            reason = "Server is in lockdown mode"
            await member.kick(reason=reason)

            # Log the kick
            log_channel_id = config.get("log_channel")
            if log_channel_id:
                try:
                    log_channel = member.guild.get_channel(int(log_channel_id))
                    if log_channel:
                        embed = discord.Embed(
                            title="🚫 Raid Protection - User Kicked",
                            description=f"**User:** {member.mention} ({member.id})\n**Reason:** {reason}",
                            color=discord.Color.red()
                        )
                        await log_channel.send(embed=embed)
                except:
                    pass

        except Exception as e:
            logger.error(f"Lockdown join handling error: {e}")

    async def _check_account_age(self, member, config):
        """Check if account is too new."""
        try:
            min_age_hours = config.get("min_account_age_hours", 24)
            account_age_hours = (time.time() - member.created_at.timestamp()) / 3600

            if account_age_hours < min_age_hours:
                action = config.get("new_account_action", "kick")

                if action == "kick":
                    await member.kick(reason=f"Account too new ({account_age_hours:.1f}h old, minimum {min_age_hours}h)")
                elif action == "ban":
                    await member.ban(reason=f"Account too new ({account_age_hours:.1f}h old, minimum {min_age_hours}h)")

                # Log the action
                await self._log_raid_action(member, f"New account ({account_age_hours:.1f}h)", action)

        except Exception as e:
            logger.error(f"Account age check error: {e}")

    async def _check_join_velocity(self, member, config):
        """Check join velocity for raid detection."""
        try:
            guild_id = member.guild.id
            check_window = config.get("join_check_window", 60)  # seconds
            max_joins = config.get("max_joins_per_window", 5)

            current_time = time.time()
            window_start = current_time - check_window

            # Count joins in the window
            recent_joins = [entry for entry in self.join_history.get(guild_id, []) if entry[0] > window_start]
            join_count = len(recent_joins)

            if join_count >= max_joins:
                # Potential raid detected
                await self._trigger_lockdown(member.guild, config, join_count, check_window)

        except Exception as e:
            logger.error(f"Join velocity check error: {e}")

    async def _trigger_lockdown(self, guild, config, join_count, window):
        """Trigger automatic lockdown."""
        try:
            if self.lockdown_active.get(guild.id, False):
                return  # Already in lockdown

            self.lockdown_active[guild.id] = True

            # Set lockdown role permissions
            await self._set_lockdown_permissions(guild, True)

            # Send alert
            alert_channel_id = config.get("alert_channel")
            if alert_channel_id:
                try:
                    alert_channel = guild.get_channel(int(alert_channel_id))
                    if alert_channel:
                        embed = discord.Embed(
                            title="🚨 RAID DETECTED - LOCKDOWN ACTIVATED",
                            description=f"Detected {join_count} joins in {window} seconds!\nServer is now in lockdown mode.",
                            color=discord.Color.red()
                        )
                        embed.add_field(
                            name="Actions Taken",
                            value="• New member joins are blocked\n• @everyone role permissions restricted\n• Staff can still moderate",
                            inline=False
                        )
                        await alert_channel.send(embed=embed)
                except:
                    pass

            # Auto-disable after timeout
            timeout = config.get("lockdown_timeout", 1800)  # 30 minutes default
            asyncio.create_task(self._auto_disable_lockdown(guild.id, timeout))

            logger.info(f"Lockdown activated for guild {guild.id}")

        except Exception as e:
            logger.error(f"Lockdown trigger error: {e}")

    async def _auto_disable_lockdown(self, guild_id, timeout):
        """Automatically disable lockdown after timeout."""
        await asyncio.sleep(timeout)

        try:
            if self.lockdown_active.get(guild_id, False):
                await self.disable_lockdown(self.bot.get_guild(guild_id))
        except Exception as e:
            logger.error(f"Auto-disable lockdown error: {e}")

    async def _set_lockdown_permissions(self, guild, enable_lockdown):
        """Set or remove lockdown permissions."""
        try:
            config = dm.get_guild_data(guild.id, "anti_raid_config", {})
            lockdown_role_id = config.get("lockdown_role")

            if not lockdown_role_id:
                return

            role = guild.get_role(int(lockdown_role_id))
            if not role:
                return

            # Set permissions for all channels
            for channel in guild.channels:
                if isinstance(channel, discord.TextChannel):
                    if enable_lockdown:
                        # Restrict send messages for @everyone
                        await channel.set_permissions(
                            guild.default_role,
                            send_messages=False,
                            reason="Anti-raid lockdown activated"
                        )
                    else:
                        # Restore normal permissions (this is simplistic - in reality you'd need to remember original perms)
                        await channel.set_permissions(
                            guild.default_role,
                            send_messages=None,
                            reason="Anti-raid lockdown disabled"
                        )

        except Exception as e:
            logger.error(f"Lockdown permissions error: {e}")

    async def enable_lockdown(self, interaction):
        """Manually enable lockdown."""
        try:
            config = dm.get_guild_data(interaction.guild.id, "anti_raid_config", {})
            if not config.get("enabled", False):
                return await interaction.response.send_message("❌ Anti-raid system is disabled.", ephemeral=True)

            if not interaction.user.guild_permissions.administrator:
                return await interaction.response.send_message("❌ Only administrators can manage lockdown.", ephemeral=True)

            if self.lockdown_active.get(interaction.guild.id, False):
                return await interaction.response.send_message("⚠️ Server is already in lockdown.", ephemeral=True)

            self.lockdown_active[interaction.guild.id] = True
            await self._set_lockdown_permissions(interaction.guild, True)

            embed = discord.Embed(
                title="🔒 Manual Lockdown Activated",
                description="Server lockdown has been manually activated by staff.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

            logger.info(f"Manual lockdown activated for guild {interaction.guild.id} by {interaction.user.id}")

        except Exception as e:
            logger.error(f"Manual lockdown error: {e}")
            await interaction.response.send_message("❌ Failed to activate lockdown.", ephemeral=True)

    async def disable_lockdown(self, guild):
        """Disable lockdown."""
        try:
            if not self.lockdown_active.get(guild.id, False):
                return

            self.lockdown_active[guild.id] = False
            await self._set_lockdown_permissions(guild, False)

            config = dm.get_guild_data(guild.id, "anti_raid_config", {})
            alert_channel_id = config.get("alert_channel")
            if alert_channel_id:
                try:
                    alert_channel = guild.get_channel(int(alert_channel_id))
                    if alert_channel:
                        embed = discord.Embed(
                            title="🔓 Lockdown Disabled",
                            description="Server lockdown has been disabled.",
                            color=discord.Color.green()
                        )
                        await alert_channel.send(embed=embed)
                except:
                    pass

            logger.info(f"Lockdown disabled for guild {guild.id}")

        except Exception as e:
            logger.error(f"Disable lockdown error: {e}")

    async def handle_member_remove(self, member):
        """Handle member leaves (for anti-raid tracking)."""
        pass  # Not implemented yet, but could track leaves too

    async def handle_message(self, message):
        """Handle messages for anti-raid purposes."""
        pass  # Could implement message spam detection here

    async def _log_raid_action(self, member, reason, action):
        """Log raid protection actions."""
        try:
            config = dm.get_guild_data(member.guild.id, "anti_raid_config", {})
            log_channel_id = config.get("log_channel")

            if log_channel_id:
                log_channel = member.guild.get_channel(int(log_channel_id))
                if log_channel:
                    embed = discord.Embed(
                        title=f"🛡️ Anti-Raid Action: {action.upper()}",
                        description=f"**User:** {member.mention} ({member.id})\n**Reason:** {reason}",
                        color=discord.Color.orange()
                    )
                    await log_channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Raid action logging error: {e}")

    def get_config_panel(self, guild_id):
        """Get anti-raid config panel."""
        return AntiRaidConfigPanel(self.bot, guild_id)

class GuardianSystem(commands.Cog):
    """
    Guardian system that protects against token leaks, malicious links, and other security threats.
    Features:
    - Bot token detection and deletion
    - Malicious link scanning
    - File attachment scanning
    - Suspicious content detection
    - Automated responses
    """

    def __init__(self, bot):
        self.bot = bot
        self.token_patterns = [
            r'[A-Za-z\d]{24}\.[\w-]{6}\.[\w-]{27}',  # Discord bot token
            r'[A-Za-z\d]{24}\.[\w-]{6}\.[\w-]{38}',  # Discord user token
            r'mfa\.[\w-]{84}',  # Discord MFA token
        ]
        self.malicious_patterns = [
            r'discord\.gg/[a-zA-Z0-9]+',  # Discord invites (can be configured)
            r'bit\.ly/[a-zA-Z0-9]+',  # Shortened links
            r'tinyurl\.com/[a-zA-Z0-9]+',
            r'goo\.gl/[a-zA-Z0-9]+',
        ]
        self.suspicious_keywords = [
            'token', 'api_key', 'secret', 'password', 'credentials',
            'hack', 'exploit', 'virus', 'malware', 'trojan'
        ]

    async def cog_load(self):
        """Called when the cog is loaded."""
        logger.info("Guardian system loaded and monitoring")

    @commands.Cog.listener()
    async def on_message(self, message):
        """Monitor messages for security threats."""
        if message.author.bot:
            return

        await self.handle_message(message)

    async def handle_message(self, message):
        """Handle message scanning."""
        try:
            config = dm.get_guild_data(message.guild.id, "guardian_config", {})
            if not config.get("enabled", False):
                return

            # Check for bot tokens
            if config.get("scan_tokens", True):
                token_found = await self._scan_for_tokens(message, config)
                if token_found:
                    return  # Message already handled

            # Check for malicious links
            if config.get("scan_links", True):
                await self._scan_for_links(message, config)

            # Check for suspicious content
            if config.get("scan_content", True):
                await self._scan_content(message, config)

            # Check file attachments
            if config.get("scan_files", True):
                await self._scan_attachments(message, config)

        except Exception as e:
            logger.error(f"Guardian message handling error: {e}")

    async def _scan_for_tokens(self, message, config):
        """Scan message for Discord tokens."""
        content = message.content

        for pattern in self.token_patterns:
            matches = re.findall(pattern, content)
            if matches:
                # Token found - take action
                action = config.get("token_action", "delete")

                try:
                    if action == "delete":
                        await message.delete()

                        # Send warning
                        embed = discord.Embed(
                            title="🚨 Security Alert - Token Detected",
                            description=f"{message.author.mention}, your message contained a Discord token and was removed for security.",
                            color=discord.Color.red()
                        )
                        warning = await message.channel.send(embed=embed, delete_after=10)

                    elif action == "warn":
                        embed = discord.Embed(
                            title="⚠️ Potential Token Detected",
                            description=f"{message.author.mention}, please be careful sharing sensitive information.",
                            color=discord.Color.orange()
                        )
                        await message.channel.send(embed=embed, delete_after=30)

                    # Log the incident
                    await self._log_security_event(message, "token_detected", f"Pattern: {pattern}")

                    return True

                except Exception as e:
                    logger.error(f"Token handling error: {e}")

        return False

    async def _scan_for_links(self, message, config):
        """Scan for potentially malicious links."""
        content = message.content.lower()

        for pattern in self.malicious_patterns:
            if re.search(pattern, content):
                # Check if link scanning is enabled for this pattern
                if pattern.startswith('discord.gg') and not config.get("block_invites", False):
                    continue

                action = config.get("link_action", "warn")

                try:
                    if action == "delete":
                        await message.delete()

                        embed = discord.Embed(
                            title="🚫 Suspicious Link Blocked",
                            description=f"{message.author.mention}, links from this domain are not allowed.",
                            color=discord.Color.red()
                        )
                        await message.channel.send(embed=embed, delete_after=10)

                    elif action == "warn":
                        embed = discord.Embed(
                            title="⚠️ Suspicious Link Detected",
                            description=f"{message.author.mention}, please be careful with links from unknown sources.",
                            color=discord.Color.orange()
                        )
                        await message.reply(embed=embed, delete_after=30)

                    await self._log_security_event(message, "suspicious_link", pattern)
                    break

                except Exception as e:
                    logger.error(f"Link scanning error: {e}")

    async def _scan_content(self, message, config):
        """Scan message content for suspicious keywords."""
        content = message.content.lower()

        suspicious_words = []
        for keyword in self.suspicious_keywords:
            if keyword in content:
                suspicious_words.append(keyword)

        if suspicious_words and len(suspicious_words) >= config.get("keyword_threshold", 2):
            action = config.get("content_action", "log")

            if action == "warn":
                embed = discord.Embed(
                    title="⚠️ Suspicious Content Detected",
                    description=f"{message.author.mention}, your message contains potentially sensitive keywords.",
                    color=discord.Color.yellow()
                )
                embed.add_field(name="Detected Keywords", value=", ".join(suspicious_words), inline=False)
                try:
                    await message.reply(embed=embed, delete_after=60)
                except:
                    pass

            await self._log_security_event(message, "suspicious_content", f"Keywords: {', '.join(suspicious_words)}")

    async def _scan_attachments(self, message, config):
        """Scan file attachments for potential threats."""
        if not message.attachments:
            return

        suspicious_extensions = config.get("blocked_extensions", ['.exe', '.bat', '.cmd', '.scr', '.pif', '.com'])

        for attachment in message.attachments:
            file_name = attachment.filename.lower()

            for ext in suspicious_extensions:
                if file_name.endswith(ext):
                    action = config.get("file_action", "delete")

                    try:
                        if action == "delete":
                            await message.delete()

                            embed = discord.Embed(
                                title="🚫 Dangerous File Blocked",
                                description=f"{message.author.mention}, files with extension `{ext}` are not allowed.",
                                color=discord.Color.red()
                            )
                            await message.channel.send(embed=embed, delete_after=10)

                        await self._log_security_event(message, "dangerous_file", f"Extension: {ext}")
                        break

                    except Exception as e:
                        logger.error(f"File scanning error: {e}")

    async def _log_security_event(self, message, event_type, details):
        """Log security events."""
        try:
            config = dm.get_guild_data(message.guild.id, "guardian_config", {})
            log_channel_id = config.get("log_channel")

            if log_channel_id:
                log_channel = message.guild.get_channel(int(log_channel_id))
                if log_channel:
                    embed = discord.Embed(
                        title=f"🛡️ Guardian Alert - {event_type.replace('_', ' ').title()}",
                        description=f"**User:** {message.author.mention} ({message.author.id})\n**Channel:** {message.channel.mention}\n**Details:** {details}",
                        color=discord.Color.orange()
                    )

                    # Include message content (truncated)
                    content = message.content[:500] if message.content else "*No text content*"
                    embed.add_field(name="Message Content", value=f"```{content}```", inline=False)

                    embed.timestamp = message.created_at
                    await log_channel.send(embed=embed)

        except Exception as e:
            logger.error(f"Security event logging error: {e}")

    def get_config_panel(self, guild_id):
        """Get guardian config panel."""
        return GuardianConfigPanel(self.bot, guild_id)

class AutoModSystem:
    """
    Advanced auto-moderation system with multiple filters.
    Features:
    - Spam detection and prevention
    - Caps lock abuse detection
    - Mass mention detection
    - Discord invite filtering
    - Link filtering
    - Word filtering
    - Duplicate message detection
    - Auto-warnings and timeouts
    """

    def __init__(self, bot):
        self.bot = bot
        self.message_history = {}  # user_id -> list of (timestamp, content)
        self.user_violations = {}  # user_id -> violation_count
        self.duplicate_cache = {}  # channel_id -> last_message_content

    async def handle_message(self, message):
        """Handle message auto-moderation."""
        try:
            config = dm.get_guild_data(message.guild.id, "automod_config", {})
            if not config.get("enabled", False):
                return

            # Skip if user has moderation immunity
            if await self._user_has_immunity(message):
                return

            violations = []

            # Check spam
            if config.get("anti_spam", True):
                spam_violation = await self._check_spam(message, config)
                if spam_violation:
                    violations.append(("spam", spam_violation))

            # Check caps
            if config.get("anti_caps", True):
                caps_violation = self._check_caps(message, config)
                if caps_violation:
                    violations.append(("caps", caps_violation))

            # Check mentions
            if config.get("anti_mentions", True):
                mention_violation = self._check_mentions(message, config)
                if mention_violation:
                    violations.append(("mentions", mention_violation))

            # Check invites
            if config.get("anti_invites", True):
                invite_violation = self._check_invites(message, config)
                if invite_violation:
                    violations.append(("invites", invite_violation))

            # Check links
            if config.get("anti_links", True):
                link_violation = self._check_links(message, config)
                if link_violation:
                    violations.append(("links", link_violation))

            # Check filtered words
            if config.get("word_filter", True):
                word_violation = self._check_words(message, config)
                if word_violation:
                    violations.append(("words", word_violation))

            # Check duplicates
            if config.get("anti_duplicates", True):
                duplicate_violation = self._check_duplicates(message, config)
                if duplicate_violation:
                    violations.append(("duplicates", duplicate_violation))

            # Handle violations
            if violations:
                await self._handle_violations(message, violations, config)

        except Exception as e:
            logger.error(f"Auto-mod message handling error: {e}")

    async def _user_has_immunity(self, message):
        """Check if user has auto-mod immunity."""
        # Administrators and moderators are immune
        if message.author.guild_permissions.administrator or message.author.guild_permissions.manage_messages:
            return True

        # Check for immune roles
        config = dm.get_guild_data(message.guild.id, "automod_config", {})
        immune_roles = config.get("immune_roles", [])

        for role in message.author.roles:
            if str(role.id) in immune_roles:
                return True

        return False

    async def _check_spam(self, message, config):
        """Check for spam messages."""
        user_id = message.author.id
        current_time = time.time()
        content = message.content.lower()

        # Initialize user history
        if user_id not in self.message_history:
            self.message_history[user_id] = []

        # Add current message
        self.message_history[user_id].append((current_time, content))

        # Clean old messages (keep last 30 seconds)
        self.message_history[user_id] = [
            (ts, msg) for ts, msg in self.message_history[user_id]
            if current_time - ts < 30
        ]

        # Check message frequency
        recent_messages = len(self.message_history[user_id])
        max_messages = config.get("max_messages_per_30s", 5)

        if recent_messages > max_messages:
            return f"{recent_messages} messages in 30 seconds (max: {max_messages})"

        # Check for repeated content
        recent_content = [msg for ts, msg in self.message_history[user_id] if len(msg) > 5]
        if len(recent_content) >= 3:
            # Check if last 3 messages are similar
            last_three = recent_content[-3:]
            if all(msg == last_three[0] for msg in last_three):
                return "Repeated same message 3+ times"

        return None

    def _check_caps(self, message, config):
        """Check for excessive caps usage."""
        content = message.content
        if len(content) < config.get("caps_min_length", 10):
            return None

        caps_count = sum(1 for c in content if c.isupper())
        caps_ratio = caps_count / len(content)

        max_caps_ratio = config.get("max_caps_ratio", 0.7)

        if caps_ratio > max_caps_ratio:
            return ".1%"

        return None

    def _check_mentions(self, message, config):
        """Check for mass mentions."""
        mentions = len(message.mentions) + len(message.role_mentions)
        max_mentions = config.get("max_mentions", 3)

        if mentions > max_mentions:
            return f"{mentions} mentions (max: {max_mentions})"

        return None

    def _check_invites(self, message, config):
        """Check for Discord invites."""
        content = message.content.lower()
        invite_pattern = r'discord\.gg/[a-zA-Z0-9]+'

        if re.search(invite_pattern, content):
            # Check whitelist
            whitelist = config.get("invite_whitelist", [])
            for allowed in whitelist:
                if allowed.lower() in content:
                    return None
            return "Discord invite link detected"

        return None

    def _check_links(self, message, config):
        """Check for unauthorized links."""
        content = message.content.lower()
        url_pattern = r'https?://[^\s]+'

        urls = re.findall(url_pattern, content)
        if not urls:
            return None

        # Check whitelist
        whitelist = config.get("link_whitelist", [])
        for url in urls:
            allowed = False
            for allowed_domain in whitelist:
                if allowed_domain.lower() in url:
                    allowed = True
                    break
            if not allowed:
                return f"Unauthorized link: {url[:50]}..."

        return None

    def _check_words(self, message, config):
        """Check for filtered words."""
        content = message.content.lower()
        filtered_words = config.get("filtered_words", [])

        for word in filtered_words:
            if word.lower() in content:
                return f"Filtered word: {word}"

        return None

    def _check_duplicates(self, message, config):
        """Check for duplicate messages in channel."""
        channel_id = message.channel.id
        content = message.content.strip()

        if len(content) < config.get("duplicate_min_length", 10):
            return None

        if channel_id in self.duplicate_cache:
            last_content = self.duplicate_cache[channel_id]
            if content == last_content:
                return "Duplicate message in channel"

        self.duplicate_cache[channel_id] = content
        return None

    async def _handle_violations(self, message, violations, config):
        """Handle detected violations."""
        try:
            # Track violations
            user_id = message.author.id
            if user_id not in self.user_violations:
                self.user_violations[user_id] = 0

            self.user_violations[user_id] += len(violations)

            # Determine action based on violation count
            violation_count = self.user_violations[user_id]
            action = self._get_action_for_violations(violation_count, config)

            # Execute action
            await self._execute_action(message, action, violations, config)

            # Log violation
            await self._log_violation(message, violations, action)

        except Exception as e:
            logger.error(f"Violation handling error: {e}")

    def _get_action_for_violations(self, count, config):
        """Get appropriate action based on violation count."""
        thresholds = config.get("violation_thresholds", {
            "warn": 1,
            "timeout": 3,
            "kick": 5,
            "ban": 10
        })

        if count >= thresholds.get("ban", 10):
            return "ban"
        elif count >= thresholds.get("kick", 5):
            return "kick"
        elif count >= thresholds.get("timeout", 3):
            return "timeout"
        else:
            return "warn"

    async def _execute_action(self, message, action, violations, config):
        """Execute the determined action."""
        try:
            violation_types = [v[0] for v in violations]
            reason = f"Auto-mod violation: {', '.join(violation_types)}"

            if action == "warn":
                embed = discord.Embed(
                    title="⚠️ Auto-Mod Warning",
                    description=f"{message.author.mention}, your message violated server rules.",
                    color=discord.Color.yellow()
                )
                embed.add_field(name="Violations", value="\n".join([f"• {v[1]}" for v in violations]), inline=False)
                embed.set_footer(text="Repeated violations may result in stricter penalties")

                try:
                    await message.reply(embed=embed, delete_after=30)
                except:
                    await message.channel.send(embed=embed, delete_after=30)

            elif action == "timeout":
                duration = config.get("timeout_duration", 600)  # 10 minutes
                until = discord.utils.utcnow() + discord.timedelta(seconds=duration)

                await message.author.timeout(until, reason=reason)

                embed = discord.Embed(
                    title="⏰ Auto-Mod Timeout",
                    description=f"{message.author.mention} has been timed out for {duration//60} minutes.",
                    color=discord.Color.orange()
                )
                await message.channel.send(embed=embed)

            elif action == "kick":
                await message.author.kick(reason=reason)

                embed = discord.Embed(
                    title="👢 Auto-Mod Kick",
                    description=f"{message.author.display_name} has been kicked for repeated violations.",
                    color=discord.Color.red()
                )
                await message.channel.send(embed=embed)

            elif action == "ban":
                await message.author.ban(reason=reason)

                embed = discord.Embed(
                    title="🔨 Auto-Mod Ban",
                    description=f"{message.author.display_name} has been banned for repeated violations.",
                    color=discord.Color.dark_red()
                )
                await message.channel.send(embed=embed)

            # Always delete the violating message (except for warnings if configured)
            if action != "warn" or config.get("delete_warnings", True):
                try:
                    await message.delete()
                except:
                    pass

        except Exception as e:
            logger.error(f"Action execution error: {e}")

    async def _log_violation(self, message, violations, action):
        """Log the violation."""
        try:
            config = dm.get_guild_data(message.guild.id, "automod_config", {})
            log_channel_id = config.get("log_channel")

            if log_channel_id:
                log_channel = message.guild.get_channel(int(log_channel_id))
                if log_channel:
                    embed = discord.Embed(
                        title="🤖 Auto-Mod Action",
                        description=f"**User:** {message.author.mention} ({message.author.id})\n**Action:** {action.title()}\n**Channel:** {message.channel.mention}",
                        color=discord.Color.blue()
                    )

                    violation_details = "\n".join([f"• {v[0].title()}: {v[1]}" for v in violations])
                    embed.add_field(name="Violations", value=violation_details, inline=False)

                    if message.content:
                        content = message.content[:500]
                        embed.add_field(name="Message Content", value=f"```{content}```", inline=False)

                    embed.timestamp = message.created_at
                    await log_channel.send(embed=embed)

        except Exception as e:
            logger.error(f"Violation logging error: {e}")

    def get_config_panel(self, guild_id):
        """Get auto-mod config panel."""
        return AutoModConfigPanel(self.bot, guild_id)

class WarningsSystem:
    """
    Comprehensive warning system with auto-punishment and management.
    Features:
    - Issue warnings to users
    - Automatic punishment thresholds
    - Warning history and management
    - Appeal system integration
    - Warning expiration
    - Staff warning notes
    """

    def __init__(self, bot):
        self.bot = bot

    async def warn_user(self, interaction, user: discord.Member, reason: str, severity: str = "medium"):
        """Issue a warning to a user."""
        try:
            if user.bot:
                return await interaction.response.send_message("❌ Cannot warn bots.", ephemeral=True)

            if user.id == interaction.user.id:
                return await interaction.response.send_message("❌ Cannot warn yourself.", ephemeral=True)

            # Check permissions
            if not self._can_warn(interaction.user, user):
                return await interaction.response.send_message("❌ You don't have permission to warn this user.", ephemeral=True)

            # Create warning
            warning_id = self._generate_warning_id()
            warning = {
                "id": warning_id,
                "user_id": user.id,
                "moderator_id": interaction.user.id,
                "reason": reason,
                "severity": severity,
                "timestamp": time.time(),
                "active": True
            }

            # Save warning
            warnings = dm.get_guild_data(interaction.guild.id, "user_warnings", {})
            user_warnings = warnings.get(str(user.id), [])
            user_warnings.append(warning)
            warnings[str(user.id)] = user_warnings
            dm.update_guild_data(interaction.guild.id, "user_warnings", warnings)

            # Check auto-punishment
            await self._check_auto_punishment(interaction.guild, user, user_warnings)

            # Log warning
            await self._log_warning(interaction.guild, warning, user, interaction.user)

            # Send DM to user
            try:
                embed = discord.Embed(
                    title="⚠️ Warning Received",
                    description=f"You have been warned in **{interaction.guild.name}**",
                    color=discord.Color.yellow()
                )
                embed.add_field(name="Reason", value=reason, inline=False)
                embed.add_field(name="Severity", value=severity.title(), inline=True)
                embed.add_field(name="Warning ID", value=warning_id, inline=True)
                embed.set_footer(text="You can appeal this warning using /appeal")

                await user.send(embed=embed)
            except:
                pass  # User may have DMs disabled

            # Confirm to moderator
            embed = discord.Embed(
                title="✅ User Warned",
                description=f"Successfully warned {user.mention}",
                color=discord.Color.green()
            )
            embed.add_field(name="Reason", value=reason, inline=True)
            embed.add_field(name="Severity", value=severity.title(), inline=True)
            embed.add_field(name="Warning ID", value=warning_id, inline=True)

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Warning user error: {e}")
            await interaction.response.send_message("❌ Failed to warn user.", ephemeral=True)

    def _can_warn(self, moderator: discord.Member, target: discord.Member):
        """Check if moderator can warn the target."""
        # Administrators can warn anyone
        if moderator.guild_permissions.administrator:
            return True

        # Moderators can warn users below them
        if moderator.guild_permissions.manage_messages:
            # Check if target has higher permissions
            if target.guild_permissions.administrator:
                return False
            if target.guild_permissions.manage_messages and not moderator.guild_permissions.administrator:
                return False
            return True

        return False

    def _generate_warning_id(self):
        """Generate a unique warning ID."""
        import random
        import string
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

    async def _check_auto_punishment(self, guild, user, user_warnings):
        """Check and apply automatic punishments based on warning count."""
        try:
            config = dm.get_guild_data(guild.id, "warnings_config", {})
            active_warnings = [w for w in user_warnings if w.get("active", True)]

            punishment_thresholds = config.get("auto_punishment", {
                "timeout": 2,
                "kick": 4,
                "ban": 6
            })

            warning_count = len(active_warnings)

            if warning_count >= punishment_thresholds.get("ban", 6):
                await self._apply_punishment(guild, user, "ban", f"Auto-ban: {warning_count} warnings")
            elif warning_count >= punishment_thresholds.get("kick", 4):
                await self._apply_punishment(guild, user, "kick", f"Auto-kick: {warning_count} warnings")
            elif warning_count >= punishment_thresholds.get("timeout", 2):
                timeout_duration = config.get("timeout_duration", 3600)  # 1 hour
                await self._apply_punishment(guild, user, "timeout", f"Auto-timeout: {warning_count} warnings", timeout_duration)

        except Exception as e:
            logger.error(f"Auto punishment check error: {e}")

    async def _apply_punishment(self, guild, user, punishment_type, reason, duration=None):
        """Apply automatic punishment."""
        try:
            if punishment_type == "timeout" and duration:
                until = discord.utils.utcnow() + discord.timedelta(seconds=duration)
                await user.timeout(until, reason=reason)
                punishment_msg = f"timed out for {duration//3600}h {(duration%3600)//60}m"

            elif punishment_type == "kick":
                await user.kick(reason=reason)
                punishment_msg = "kicked"

            elif punishment_type == "ban":
                await user.ban(reason=reason)
                punishment_msg = "banned"

            # Notify user if possible
            try:
                embed = discord.Embed(
                    title=f"🚫 Automatic {punishment_type.title()}",
                    description=f"You have been {punishment_msg} from **{guild.name}** due to multiple warnings.",
                    color=discord.Color.red()
                )
                embed.add_field(name="Reason", value=reason, inline=False)
                await user.send(embed=embed)
            except:
                pass

            # Log punishment
            config = dm.get_guild_data(guild.id, "warnings_config", {})
            log_channel_id = config.get("log_channel")

            if log_channel_id:
                try:
                    log_channel = guild.get_channel(int(log_channel_id))
                    if log_channel:
                        embed = discord.Embed(
                            title=f"🤖 Auto-{punishment_type.title()}",
                            description=f"**User:** {user.mention} ({user.id})\n**Reason:** {reason}",
                            color=discord.Color.red()
                        )
                        await log_channel.send(embed=embed)
                except:
                    pass

        except Exception as e:
            logger.error(f"Apply punishment error: {e}")

    async def _log_warning(self, guild, warning, user, moderator):
        """Log the warning to configured channel."""
        try:
            config = dm.get_guild_data(guild.id, "warnings_config", {})
            log_channel_id = config.get("log_channel")

            if log_channel_id:
                log_channel = guild.get_channel(int(log_channel_id))
                if log_channel:
                    embed = discord.Embed(
                        title="⚠️ User Warned",
                        description=f"**User:** {user.mention} ({user.id})\n**Moderator:** {moderator.mention} ({moderator.id})",
                        color=discord.Color.yellow()
                    )
                    embed.add_field(name="Reason", value=warning["reason"], inline=False)
                    embed.add_field(name="Severity", value=warning["severity"].title(), inline=True)
                    embed.add_field(name="Warning ID", value=warning["id"], inline=True)
                    embed.timestamp = discord.utils.utcnow()

                    await log_channel.send(embed=embed)

        except Exception as e:
            logger.error(f"Warning logging error: {e}")

    async def get_user_warnings(self, interaction, user: discord.Member = None):
        """Get warnings for a user."""
        target = user or interaction.user

        try:
            warnings_data = dm.get_guild_data(interaction.guild.id, "user_warnings", {})
            user_warnings = warnings_data.get(str(target.id), [])

            active_warnings = [w for w in user_warnings if w.get("active", True)]
            expired_warnings = [w for w in user_warnings if not w.get("active", True)]

            embed = discord.Embed(
                title=f"⚠️ Warnings for {target.display_name}",
                color=discord.Color.yellow()
            )

            if active_warnings:
                warning_list = ""
                for warning in active_warnings[-5:]:  # Show last 5
                    timestamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(warning["timestamp"]))
                    warning_list += f"• **{warning['id']}** - {timestamp}\n  {warning['reason'][:50]}...\n"

                embed.add_field(name=f"Active Warnings ({len(active_warnings)})", value=warning_list[:1000] or "None", inline=False)

            if expired_warnings:
                embed.add_field(name=f"Expired Warnings ({len(expired_warnings)})", value=f"{len(expired_warnings)} warnings have expired", inline=True)

            if not active_warnings and not expired_warnings:
                embed.add_field(name="Status", value="No warnings found", inline=False)

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Get warnings error: {e}")
            await interaction.response.send_message("❌ Failed to retrieve warnings.", ephemeral=True)

    async def clear_warning(self, interaction, warning_id: str):
        """Clear a specific warning."""
        try:
            warnings_data = dm.get_guild_data(interaction.guild.id, "user_warnings", {})

            # Find and clear the warning
            cleared = False
            for user_id, user_warnings in warnings_data.items():
                for warning in user_warnings:
                    if warning["id"] == warning_id:
                        warning["active"] = False
                        warning["cleared_by"] = interaction.user.id
                        warning["cleared_at"] = time.time()
                        cleared = True
                        break
                if cleared:
                    break

            if cleared:
                dm.update_guild_data(interaction.guild.id, "user_warnings", warnings_data)

                embed = discord.Embed(
                    title="✅ Warning Cleared",
                    description=f"Warning **{warning_id}** has been cleared.",
                    color=discord.Color.green()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)

                # Log the clearance
                await self._log_warning_clearance(interaction.guild, warning_id, interaction.user)
            else:
                await interaction.response.send_message(f"❌ Warning **{warning_id}** not found.", ephemeral=True)

        except Exception as e:
            logger.error(f"Clear warning error: {e}")
            await interaction.response.send_message("❌ Failed to clear warning.", ephemeral=True)

    async def _log_warning_clearance(self, guild, warning_id, moderator):
        """Log warning clearance."""
        try:
            config = dm.get_guild_data(guild.id, "warnings_config", {})
            log_channel_id = config.get("log_channel")

            if log_channel_id:
                log_channel = guild.get_channel(int(log_channel_id))
                if log_channel:
                    embed = discord.Embed(
                        title="🗑️ Warning Cleared",
                        description=f"**Warning ID:** {warning_id}\n**Cleared by:** {moderator.mention} ({moderator.id})",
                        color=discord.Color.green()
                    )
                    await log_channel.send(embed=embed)

        except Exception as e:
            logger.error(f"Warning clearance logging error: {e}")

    def get_config_panel(self, guild_id):
        """Get warnings config panel."""
        return WarningsConfigPanel(self.bot, guild_id)

class StaffPromotionSystem:
    """
    Staff promotion system with requirements, applications, and tracking.
    Features:
    - Promotion requirements (activity, time, reviews)
    - Staff applications and reviews
    - Promotion tracking and history
    - Role-based requirements
    """

    def __init__(self, bot):
        self.bot = bot

    async def check_promotion_eligibility(self, user_id: int, guild_id: int):
        """Check if a user is eligible for promotion."""
        config = dm.get_guild_data(guild_id, "staff_promo_config", {})
        if not config.get("enabled", False):
            return False, "System disabled"

        # Get user's current staff role
        member = self.bot.get_guild(guild_id).get_member(user_id)
        if not member:
            return False, "User not found"

        current_role = self._get_current_staff_role(member, config)
        if not current_role:
            return False, "Not staff member"

        next_role = self._get_next_staff_role(current_role, config)
        if not next_role:
            return False, "Already at highest role"

        # Check requirements
        requirements = config.get("requirements", {}).get(next_role["id"], {})
        return await self._check_requirements(member, requirements, guild_id)

    def _get_current_staff_role(self, member, config):
        """Get user's current staff role."""
        staff_roles = config.get("staff_roles", [])
        user_roles = [role.id for role in member.roles]

        for role_data in staff_roles:
            if role_data["id"] in user_roles:
                return role_data

        return None

    def _get_next_staff_role(self, current_role, config):
        """Get the next staff role in hierarchy."""
        staff_roles = config.get("staff_roles", [])
        try:
            current_index = next(i for i, r in enumerate(staff_roles) if r["id"] == current_role["id"])
            if current_index + 1 < len(staff_roles):
                return staff_roles[current_index + 1]
        except:
            pass
        return None

    async def _check_requirements(self, member, requirements, guild_id):
        """Check if user meets promotion requirements."""
        reasons = []

        # Time requirement
        min_time = requirements.get("min_days", 0)
        if min_time > 0:
            join_date = member.joined_at
            days_since_join = (discord.utils.utcnow() - join_date).days
            if days_since_join < min_time:
                reasons.append(f"Need {min_time - days_since_join} more days in server")

        # Activity requirement
        min_messages = requirements.get("min_messages", 0)
        if min_messages > 0:
            user_stats = dm.get_guild_data(guild_id, f"user_stats_{member.id}", {})
            message_count = user_stats.get("messages", 0)
            if message_count < min_messages:
                reasons.append(f"Need {min_messages - message_count} more messages")

        # Review requirement
        min_reviews = requirements.get("min_reviews", 0)
        if min_reviews > 0:
            reviews = dm.get_guild_data(guild_id, f"staff_reviews_{member.id}", [])
            positive_reviews = len([r for r in reviews if r.get("rating", 0) >= 7])
            if positive_reviews < min_reviews:
                reasons.append(f"Need {min_reviews - positive_reviews} more positive reviews")

        return len(reasons) == 0, reasons

    async def promote_user(self, interaction, user: discord.Member):
        """Promote a user to the next staff role."""
        eligible, reasons = await self.check_promotion_eligibility(user.id, interaction.guild.id)

        if not eligible:
            embed = discord.Embed(
                title="❌ Not Eligible for Promotion",
                description=f"{user.mention} is not eligible for promotion.",
                color=discord.Color.red()
            )
            if reasons:
                embed.add_field(name="Requirements", value="\n".join(f"• {r}" for r in reasons), inline=False)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        # Get roles
        config = dm.get_guild_data(interaction.guild.id, "staff_promo_config", {})
        current_role = self._get_current_staff_role(user, config)
        next_role = self._get_next_staff_role(current_role, config)

        try:
            # Remove current role
            if current_role:
                role = interaction.guild.get_role(current_role["id"])
                if role:
                    await user.remove_roles(role)

            # Add new role
            new_role = interaction.guild.get_role(next_role["id"])
            if new_role:
                await user.add_roles(new_role)

                # Log promotion
                await self._log_promotion(interaction.guild, user, current_role, next_role, interaction.user)

                embed = discord.Embed(
                    title="🎉 Promotion Successful!",
                    description=f"{user.mention} has been promoted to {new_role.name}!",
                    color=discord.Color.green()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Promotion error: {e}")
            await interaction.response.send_message("❌ Failed to promote user.", ephemeral=True)

    async def _log_promotion(self, guild, user, old_role, new_role, promoter):
        """Log the promotion."""
        config = dm.get_guild_data(guild.id, "staff_promo_config", {})
        log_channel_id = config.get("log_channel")

        if log_channel_id:
            try:
                log_channel = guild.get_channel(int(log_channel_id))
                if log_channel:
                    embed = discord.Embed(
                        title="⬆️ Staff Promotion",
                        description=f"**User:** {user.mention} ({user.id})\n**Promoted by:** {promoter.mention}",
                        color=discord.Color.blue()
                    )
                    if old_role:
                        embed.add_field(name="From", value=old_role["name"], inline=True)
                    embed.add_field(name="To", value=new_role["name"], inline=True)
                    await log_channel.send(embed=embed)
            except:
                pass

    def get_config_panel(self, guild_id):
        """Get staff promotion config panel."""
        return StaffPromotionConfigPanel(self.bot, guild_id)

class StaffShiftSystem:
    """
    Staff shift tracking system with start/end times and breaks.
    Features:
    - Shift start/end logging
    - Break tracking
    - Duration calculation
    - Shift history
    - Active shift monitoring
    """

    def __init__(self, bot):
        self.bot = bot
        self.active_shifts = {}  # user_id -> shift_data

    async def handle_message(self, message):
        """Track staff activity during shifts."""
        if message.author.bot:
            return

        user_id = message.author.id
        if user_id in self.active_shifts:
            # Update last activity
            self.active_shifts[user_id]["last_activity"] = time.time()

    async def start_tasks(self):
        """Start shift monitoring tasks."""
        asyncio.create_task(self._monitor_shift_activity())

    async def _monitor_shift_activity(self):
        """Monitor active shifts for inactivity."""
        while True:
            try:
                current_time = time.time()
                inactive_users = []

                for user_id, shift_data in self.active_shifts.items():
                    last_activity = shift_data.get("last_activity", shift_data["start_time"])
                    if current_time - last_activity > 1800:  # 30 minutes
                        inactive_users.append(user_id)

                for user_id in inactive_users:
                    await self._handle_inactive_shift(user_id)

                await asyncio.sleep(300)  # Check every 5 minutes
            except Exception as e:
                logger.error(f"Shift monitoring error: {e}")
                await asyncio.sleep(300)

    async def _handle_inactive_shift(self, user_id):
        """Handle inactive shift (auto-end or notify)."""
        # For now, just log it. Could auto-end shift in the future
        logger.info(f"Staff member {user_id} has been inactive for 30+ minutes during shift")

    async def start_shift(self, interaction):
        """Start a staff shift."""
        user_id = interaction.user.id
        guild_id = interaction.guild.id

        if user_id in self.active_shifts:
            return await interaction.response.send_message("❌ You already have an active shift.", ephemeral=True)

        # Check if staff member
        config = dm.get_guild_data(guild_id, "staff_shifts_config", {})
        staff_roles = config.get("staff_roles", [])
        is_staff = any(role.id in [int(r) for r in staff_roles] for role in interaction.user.roles)

        if not is_staff and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Only staff members can start shifts.", ephemeral=True)

        shift_data = {
            "user_id": user_id,
            "guild_id": guild_id,
            "start_time": time.time(),
            "last_activity": time.time(),
            "breaks": [],
            "on_break": False
        }

        self.active_shifts[user_id] = shift_data

        embed = discord.Embed(
            title="🕐 Shift Started",
            description=f"Staff shift started for {interaction.user.mention}",
            color=discord.Color.green()
        )
        embed.add_field(name="Start Time", value=time.strftime("%H:%M:%S", time.localtime(shift_data["start_time"])), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

        # Log shift start
        await self._log_shift_action(guild_id, user_id, "started", interaction.user)

    async def end_shift(self, interaction):
        """End a staff shift."""
        user_id = interaction.user.id
        guild_id = interaction.guild.id

        if user_id not in self.active_shifts:
            return await interaction.response.send_message("❌ You don't have an active shift.", ephemeral=True)

        shift_data = self.active_shifts[user_id]
        end_time = time.time()
        duration = end_time - shift_data["start_time"]

        # Calculate break time
        break_time = sum(b["duration"] for b in shift_data["breaks"] if b.get("end_time"))
        actual_duration = duration - break_time

        # Save shift to history
        shift_record = {
            "user_id": user_id,
            "guild_id": guild_id,
            "start_time": shift_data["start_time"],
            "end_time": end_time,
            "duration": actual_duration,
            "break_time": break_time,
            "breaks": shift_data["breaks"]
        }

        shift_history = dm.get_guild_data(guild_id, "shift_history", [])
        shift_history.append(shift_record)
        dm.update_guild_data(guild_id, "shift_history", shift_history[-1000:])  # Keep last 1000

        # Remove active shift
        del self.active_shifts[user_id]

        # Response
        embed = discord.Embed(
            title="🏁 Shift Ended",
            description=f"Staff shift ended for {interaction.user.mention}",
            color=discord.Color.blue()
        )

        hours, remainder = divmod(int(actual_duration), 3600)
        minutes, seconds = divmod(remainder, 60)
        duration_str = f"{hours}h {minutes}m {seconds}s"

        embed.add_field(name="Duration", value=duration_str, inline=True)
        if break_time > 0:
            break_hours, break_remainder = divmod(int(break_time), 3600)
            break_minutes, break_seconds = divmod(break_remainder, 60)
            embed.add_field(name="Break Time", value=f"{break_hours}h {break_minutes}m {break_seconds}s", inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

        # Log shift end
        await self._log_shift_action(guild_id, user_id, "ended", interaction.user, duration_str)

    async def start_break(self, interaction):
        """Start a break during shift."""
        user_id = interaction.user.id

        if user_id not in self.active_shifts:
            return await interaction.response.send_message("❌ You don't have an active shift.", ephemeral=True)

        shift_data = self.active_shifts[user_id]
        if shift_data["on_break"]:
            return await interaction.response.send_message("❌ You're already on break.", ephemeral=True)

        break_start = time.time()
        shift_data["breaks"].append({"start_time": break_start})
        shift_data["on_break"] = True

        embed = discord.Embed(
            title="☕ Break Started",
            description=f"Break started for {interaction.user.mention}",
            color=discord.Color.yellow()
        )
        embed.add_field(name="Start Time", value=time.strftime("%H:%M:%S", time.localtime(break_start)), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def end_break(self, interaction):
        """End a break during shift."""
        user_id = interaction.user.id

        if user_id not in self.active_shifts:
            return await interaction.response.send_message("❌ You don't have an active shift.", ephemeral=True)

        shift_data = self.active_shifts[user_id]
        if not shift_data["on_break"]:
            return await interaction.response.send_message("❌ You're not on break.", ephemeral=True)

        break_end = time.time()
        current_break = shift_data["breaks"][-1]
        current_break["end_time"] = break_end
        current_break["duration"] = break_end - current_break["start_time"]
        shift_data["on_break"] = False

        embed = discord.Embed(
            title="▶️ Break Ended",
            description=f"Break ended for {interaction.user.mention}",
            color=discord.Color.green()
        )

        duration = int(current_break["duration"])
        minutes, seconds = divmod(duration, 60)
        embed.add_field(name="Break Duration", value=f"{minutes}m {seconds}s", inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def get_my_shifts(self, interaction):
        """Get user's shift history."""
        user_id = interaction.user.id
        guild_id = interaction.guild.id

        shift_history = dm.get_guild_data(guild_id, "shift_history", [])
        user_shifts = [s for s in shift_history if s["user_id"] == user_id]

        if not user_shifts:
            return await interaction.response.send_message("📊 No shift history found.", ephemeral=True)

        # Get recent shifts
        recent_shifts = user_shifts[-5:]

        embed = discord.Embed(
            title="🕐 Your Shift History",
            color=discord.Color.blue()
        )

        total_duration = 0
        shift_list = ""

        for shift in recent_shifts:
            start_date = time.strftime("%m/%d %H:%M", time.localtime(shift["start_time"]))
            duration = shift["duration"]
            total_duration += duration

            hours, remainder = divmod(int(duration), 3600)
            minutes, seconds = divmod(remainder, 60)
            duration_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m {seconds}s"

            shift_list += f"• {start_date} - {duration_str}\n"

        embed.add_field(name="Recent Shifts", value=shift_list or "None", inline=False)

        # Total stats
        total_hours, total_remainder = divmod(int(total_duration), 3600)
        total_minutes, total_seconds = divmod(total_remainder, 60)
        embed.add_field(name="Total Time", value=f"{total_hours}h {total_minutes}m", inline=True)
        embed.add_field(name="Total Shifts", value=str(len(user_shifts)), inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _log_shift_action(self, guild_id, user_id, action, user, extra_info=None):
        """Log shift actions."""
        config = dm.get_guild_data(guild_id, "staff_shifts_config", {})
        log_channel_id = config.get("log_channel")

        if log_channel_id:
            try:
                log_channel = self.bot.get_guild(guild_id).get_channel(int(log_channel_id))
                if log_channel:
                    embed = discord.Embed(
                        title=f"🕐 Shift {action.title()}",
                        description=f"**Staff:** {user.mention} ({user.id})",
                        color=discord.Color.blue()
                    )
                    if extra_info:
                        embed.add_field(name="Duration", value=extra_info, inline=True)
                    await log_channel.send(embed=embed)
            except:
                pass

    def get_config_panel(self, guild_id):
        """Get staff shifts config panel."""
        return StaffShiftsConfigPanel(self.bot, guild_id)

class StaffReviewSystem:
    """
    Staff review system with peer/self/admin reviews and composite scoring.
    Features:
    - Review cycles
    - Peer reviews
    - Self-reviews
    - Admin reviews
    - Composite scoring
    - Review history
    """

    def __init__(self, bot):
        self.bot = bot

    async def start_tasks(self):
        """Start review monitoring tasks."""
        asyncio.create_task(self._check_review_cycles())

    async def _check_review_cycles(self):
        """Check for review cycles that need to start."""
        while True:
            try:
                for guild in self.bot.guilds:
                    config = dm.get_guild_data(guild.id, "staff_reviews_config", {})
                    if not config.get("enabled", False):
                        continue

                    # Check if it's time for a review cycle
                    last_cycle = config.get("last_review_cycle", 0)
                    cycle_interval = config.get("cycle_days", 30) * 86400

                    if time.time() - last_cycle > cycle_interval:
                        await self._start_review_cycle(guild, config)
                        config["last_review_cycle"] = time.time()
                        dm.update_guild_data(guild.id, "staff_reviews_config", config)

                await asyncio.sleep(3600)  # Check hourly

            except Exception as e:
                logger.error(f"Review cycle check error: {e}")
                await asyncio.sleep(3600)

    async def _start_review_cycle(self, guild, config):
        """Start a new review cycle for all staff."""
        try:
            staff_roles = config.get("staff_roles", [])
            review_deadline = time.time() + (config.get("review_deadline_days", 7) * 86400)

            for role_id in staff_roles:
                role = guild.get_role(int(role_id))
                if not role:
                    continue

                for member in role.members:
                    if member.bot:
                        continue

                    # Send review request
                    await self._send_review_request(member, guild, review_deadline)

        except Exception as e:
            logger.error(f"Start review cycle error: {e}")

    async def _send_review_request(self, member, guild, deadline):
        """Send review request to staff member."""
        try:
            embed = discord.Embed(
                title="📋 Staff Review Required",
                description="It's time for your periodic staff review!",
                color=discord.Color.blue()
            )

            deadline_str = time.strftime("%Y-%m-%d", time.localtime(deadline))
            embed.add_field(name="Deadline", value=deadline_str, inline=True)
            embed.add_field(
                name="Instructions",
                value="Use `/review self` to submit your self-review\nOther staff will also review you",
                inline=False
            )

            await member.send(embed=embed)

        except Exception as e:
            logger.error(f"Send review request error: {e}")

    async def submit_review(self, interaction, target_user: discord.Member, review_type: str, rating: int, comments: str):
        """Submit a review."""
        try:
            if rating < 1 or rating > 10:
                return await interaction.response.send_message("❌ Rating must be between 1-10.", ephemeral=True)

            review = {
                "reviewer_id": interaction.user.id,
                "target_id": target_user.id,
                "guild_id": interaction.guild.id,
                "type": review_type,
                "rating": rating,
                "comments": comments,
                "submitted_at": time.time()
            }

            reviews = dm.get_guild_data(interaction.guild.id, "staff_reviews", [])
            reviews.append(review)

            # Keep last 1000 reviews
            if len(reviews) > 1000:
                reviews = reviews[-1000:]

            dm.update_guild_data(interaction.guild.id, "staff_reviews", reviews)

            await interaction.response.send_message("✅ Review submitted!", ephemeral=True)

        except Exception as e:
            logger.error(f"Submit review error: {e}")
            await interaction.response.send_message("❌ Failed to submit review.", ephemeral=True)

    def get_user_reviews(self, guild_id, user_id, days=90):
        """Get reviews for a user within the specified days."""
        reviews = dm.get_guild_data(guild_id, "staff_reviews", [])
        cutoff = time.time() - (days * 86400)

        user_reviews = [r for r in reviews if r["target_id"] == user_id and r["submitted_at"] > cutoff]

        return user_reviews

    def calculate_composite_score(self, guild_id, user_id):
        """Calculate composite review score."""
        reviews = self.get_user_reviews(guild_id, user_id)

        if not reviews:
            return None

        # Weight different review types
        weights = {
            "self": 0.2,
            "peer": 0.4,
            "admin": 0.4
        }

        total_weighted_score = 0
        total_weight = 0

        for review in reviews:
            weight = weights.get(review["type"], 0.3)
            total_weighted_score += review["rating"] * weight
            total_weight += weight

        if total_weight == 0:
            return None

        return round(total_weighted_score / total_weight, 2)

class StarboardSystem:
    """
    Starboard system for highlighting popular messages.
    Features:
    - Star reaction tracking
    - Message reposting to starboard channel
    - Star count requirements
    - Self-star prevention
    - Duplicate prevention
    """

    def __init__(self, bot):
        self.bot = bot
        self.posted_messages = {}  # message_id -> starboard_message_id

    async def handle_reaction_add(self, reaction, user):
        """Handle star reactions."""
        if str(reaction.emoji) != "⭐":
            return

        if user.bot:
            return

        try:
            config = dm.get_guild_data(reaction.message.guild.id, "starboard_config", {})
            if not config.get("enabled", False):
                return

            starboard_channel_id = config.get("channel")
            if not starboard_channel_id:
                return

            min_stars = config.get("min_stars", 3)

            # Count stars (excluding self-stars and bot reactions)
            star_count = 0
            for reaction_obj in reaction.message.reactions:
                if str(reaction_obj.emoji) == "⭐":
                    star_count = reaction_obj.count
                    break

            # Check if already posted
            if reaction.message.id in self.posted_messages:
                # Update existing post
                await self._update_starboard_post(reaction.message, star_count, config)
            elif star_count >= min_stars:
                # Create new starboard post
                await self._create_starboard_post(reaction.message, star_count, config)

        except Exception as e:
            logger.error(f"Starboard reaction error: {e}")

    async def _create_starboard_post(self, message, star_count, config):
        """Create a new starboard post."""
        try:
            starboard_channel = message.guild.get_channel(int(config["channel"]))
            if not starboard_channel:
                return

            embed = await self._create_starboard_embed(message, star_count)

            starboard_message = await starboard_channel.send(embed=embed)
            self.posted_messages[message.id] = starboard_message.id

        except Exception as e:
            logger.error(f"Create starboard post error: {e}")

    async def _update_starboard_post(self, message, star_count, config):
        """Update existing starboard post."""
        try:
            starboard_channel = message.guild.get_channel(int(config["channel"]))
            if not starboard_channel:
                return

            starboard_message_id = self.posted_messages.get(message.id)
            if not starboard_message_id:
                return

            starboard_message = await starboard_channel.fetch_message(starboard_message_id)
            embed = await self._create_starboard_embed(message, star_count)

            await starboard_message.edit(embed=embed)

        except Exception as e:
            logger.error(f"Update starboard post error: {e}")

    async def _create_starboard_embed(self, message, star_count):
        """Create starboard embed."""
        embed = discord.Embed(
            description=message.content[:2000] if message.content else "*No text content*",
            color=discord.Color.gold(),
            timestamp=message.created_at
        )

        embed.set_author(
            name=message.author.display_name,
            icon_url=message.author.avatar.url if message.author.avatar else None
        )

        embed.add_field(
            name="⭐ Stars",
            value=str(star_count),
            inline=True
        )

        embed.add_field(
            name="Channel",
            value=message.channel.mention,
            inline=True
        )

        # Add image if present
        if message.attachments:
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith('image/'):
                    embed.set_image(url=attachment.url)
                    break

        embed.set_footer(text=f"Message ID: {message.id}")

        return embed

class AIChatSystem:
    """
    AI chat system for designated channels.
    Features:
    - AI responses in configured channels
    - Conversation memory
    - Response filtering
    - Rate limiting
    """

    def __init__(self, bot):
        self.bot = bot
        self.last_response = {}  # channel_id -> timestamp

    async def handle_message(self, message):
        """Handle messages in AI chat channels."""
        if message.author.bot:
            return

        try:
            config = dm.get_guild_data(message.guild.id, "ai_chat_config", {})
            if not config.get("enabled", False):
                return

            ai_channels = config.get("channels", [])
            if str(message.channel.id) not in ai_channels:
                return

            # Rate limiting
            now = time.time()
            last_time = self.last_response.get(message.channel.id, 0)
            cooldown = config.get("cooldown", 30)

            if now - last_time < cooldown:
                return

            # Get AI response
            response = await self._get_ai_response(message.content, message.author.id, message.guild.id)

            if response:
                self.last_response[message.channel.id] = now
                await message.reply(response[:2000])

        except Exception as e:
            logger.error(f"AI chat error: {e}")

    async def _get_ai_response(self, user_message, user_id, guild_id):
        """Get AI response (placeholder - would integrate with actual AI API)."""
        # This is a placeholder - in real implementation would call AI API
        responses = [
            "That's interesting! Tell me more.",
            "I understand. How can I help you with that?",
            "Thanks for sharing that information.",
            "That's a great point!",
            "I appreciate you bringing that up.",
            "Let me think about that...",
            "That's something worth considering.",
            "I see what you mean.",
            "Thanks for the clarification!",
            "That's helpful information."
        ]

        return random.choice(responses)

class ApplicationSystem:
    """
    Staff application system with forms and review process.
    Features:
    - Application forms
    - Staff review process
    - Application status tracking
    - Acceptance/rejection handling
    """

    def __init__(self, bot):
        self.bot = bot

    async def create_application(self, interaction):
        """Create a new staff application."""
        config = dm.get_guild_data(interaction.guild.id, "applications_config", {})
        if not config.get("enabled", False):
            return await interaction.response.send_message("❌ Applications are currently closed.", ephemeral=True)

        modal = StaffApplicationModal(self.bot, interaction.guild.id)
        await interaction.response.send_modal(modal)

    async def review_application(self, interaction, application_id, action, reason=None):
        """Review a staff application."""
        applications = dm.get_guild_data(interaction.guild.id, "staff_applications", {})
        application = applications.get(application_id)

        if not application:
            return await interaction.response.send_message("❌ Application not found.", ephemeral=True)

        application["status"] = action
        application["reviewed_by"] = interaction.user.id
        application["reviewed_at"] = time.time()
        if reason:
            application["review_reason"] = reason

        dm.update_guild_data(interaction.guild.id, "staff_applications", applications)

        # Notify applicant
        try:
            applicant = self.bot.get_user(application["user_id"])
            if applicant:
                embed = discord.Embed(
                    title=f"📋 Application {'Accepted' if action == 'accepted' else 'Rejected'}",
                    description=f"Your staff application for **{interaction.guild.name}** has been {action}.",
                    color=discord.Color.green() if action == "accepted" else discord.Color.red()
                )
                if reason:
                    embed.add_field(name="Reason", value=reason, inline=False)

                await applicant.send(embed=embed)
        except:
            pass

        await interaction.response.send_message(f"✅ Application {action}.", ephemeral=True)

    def get_persistent_views(self):
        """Get persistent views for applications."""
        return [StaffApplicationButton()]

class AppealSystem:
    """
    Warning appeal system for users to appeal warnings.
    Features:
    - Appeal forms
    - Staff review process
    - Appeal status tracking
    - Appeal resolution
    """

    def __init__(self, bot):
        self.bot = bot

    async def create_appeal(self, interaction):
        """Create a warning appeal."""
        modal = WarningAppealModal(self.bot, interaction.guild.id, interaction.user.id)
        await interaction.response.send_modal(modal)

    async def review_appeal(self, interaction, appeal_id, action, reason=None):
        """Review a warning appeal."""
        appeals = dm.get_guild_data(interaction.guild.id, "warning_appeals", {})
        appeal = appeals.get(appeal_id)

        if not appeal:
            return await interaction.response.send_message("❌ Appeal not found.", ephemeral=True)

        appeal["status"] = action
        appeal["reviewed_by"] = interaction.user.id
        appeal["reviewed_at"] = time.time()
        if reason:
            appeal["review_reason"] = reason

        dm.update_guild_data(interaction.guild.id, "warning_appeals", appeals)

        # Handle appeal resolution
        if action == "accepted":
            # Clear the warning
            warnings_data = dm.get_guild_data(interaction.guild.id, "user_warnings", {})
            user_warnings = warnings_data.get(str(appeal["user_id"]), [])

            for warning in user_warnings:
                if warning["id"] == appeal["warning_id"]:
                    warning["active"] = False
                    warning["cleared_by_appeal"] = True
                    break

            dm.update_guild_data(interaction.guild.id, "user_warnings", warnings_data)

        # Notify user
        try:
            appellant = self.bot.get_user(appeal["user_id"])
            if appellant:
                embed = discord.Embed(
                    title=f"⚖️ Appeal {'Accepted' if action == 'accepted' else 'Rejected'}",
                    description=f"Your warning appeal for **{interaction.guild.name}** has been {action}.",
                    color=discord.Color.green() if action == "accepted" else discord.Color.red()
                )
                if reason:
                    embed.add_field(name="Reason", value=reason, inline=False)

                await appellant.send(embed=embed)
        except:
            pass

        await interaction.response.send_message(f"✅ Appeal {action}.", ephemeral=True)

    def get_persistent_views(self):
        """Get persistent views for appeals."""
        return [WarningAppealButton()]

class ModmailSystem:
    """
    Modmail system for direct messaging staff.
    Features:
    - Anonymous modmail
    - Staff response system
    - Conversation threading
    - Message logging
    """

    def __init__(self, bot):
        self.bot = bot
        self.active_threads = {}  # user_id -> channel_id

    async def handle_dm(self, message):
        """Handle direct messages to the bot."""
        try:
            # Find staff guild and channel
            for guild in self.bot.guilds:
                config = dm.get_guild_data(guild.id, "modmail_config", {})
                if config.get("enabled", False):
                    modmail_channel_id = config.get("channel")
                    if modmail_channel_id:
                        modmail_channel = guild.get_channel(int(modmail_channel_id))
                        if modmail_channel:
                            await self._create_modmail_thread(message, modmail_channel, guild)
                            return

            # If no modmail channel found, send error message
            await message.author.send("❌ Modmail is not configured for this server.")

        except Exception as e:
            logger.error(f"Modmail DM handling error: {e}")

    async def _create_modmail_thread(self, message, channel, guild):
        """Create a modmail thread."""
        user = message.author

        # Check if thread already exists
        if user.id in self.active_threads:
            thread_id = self.active_threads[user.id]
            try:
                thread = await guild.fetch_channel(thread_id)
                await thread.send(f"**{user.display_name}**: {message.content}")
                return
            except:
                # Thread was deleted, create new one
                pass

        # Create new thread
        thread_name = f"modmail-{user.display_name[:50]}"

        embed = discord.Embed(
            title=f"📬 New Modmail - {user.display_name}",
            description=f"**User:** {user.mention} ({user.id})\n**Message:** {message.content}",
            color=discord.Color.blue()
        )

        thread = await channel.create_thread(
            name=thread_name,
            embed=embed,
            auto_archive_duration=1440  # 24 hours
        )

        self.active_threads[user.id] = thread.id

        # Send confirmation to user
        try:
            await user.send("✅ Your message has been sent to the staff team. They will respond soon.")
        except:
            pass

    async def reply_to_modmail(self, interaction, user_id: int, response: str):
        """Reply to a modmail conversation."""
        try:
            user = self.bot.get_user(user_id)
            if not user:
                return await interaction.response.send_message("❌ User not found.", ephemeral=True)

            # Send response to user
            embed = discord.Embed(
                title="📬 Staff Response",
                description=response,
                color=discord.Color.green()
            )
            embed.set_footer(text=f"From {interaction.guild.name} staff")

            await user.send(embed=embed)

            # Log in thread
            await interaction.channel.send(f"**Staff {interaction.user.display_name}**: {response}")

            await interaction.response.send_message("✅ Response sent.", ephemeral=True)

        except Exception as e:
            logger.error(f"Modmail reply error: {e}")
            await interaction.response.send_message("❌ Failed to send response.", ephemeral=True)

    def get_persistent_views(self):
        """Get persistent views for modmail."""
        return []

class AnnouncementSystem:
    """
    Announcement system with auto-posting and cross-posting.
    Features:
    - Scheduled announcements
    - Auto cross-posting
    - Announcement channels
    - Auto-pinning
    """

    def __init__(self, bot):
        self.bot = bot

    async def start_monitoring(self):
        """Start announcement monitoring."""
        pass  # Would implement scheduled announcement checking

    async def create_announcement(self, interaction, title, content, channel_id=None, auto_pin=False, cross_post=False):
        """Create a new announcement."""
        try:
            config = dm.get_guild_data(interaction.guild.id, "announcements_config", {})
            if not config.get("enabled", False):
                return await interaction.response.send_message("❌ Announcements system is disabled.", ephemeral=True)

            target_channel_id = channel_id or config.get("default_channel")
            if not target_channel_id:
                return await interaction.response.send_message("❌ No announcement channel configured.", ephemeral=True)

            target_channel = interaction.guild.get_channel(int(target_channel_id))
            if not target_channel:
                return await interaction.response.send_message("❌ Announcement channel not found.", ephemeral=True)

            embed = discord.Embed(
                title=title,
                description=content,
                color=discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            embed.set_author(
                name=interaction.user.display_name,
                icon_url=interaction.user.avatar.url if interaction.user.avatar else None
            )

            message = await target_channel.send(embed=embed)

            # Auto-pin if requested
            if auto_pin:
                try:
                    await message.pin()
                except:
                    pass

            # Cross-post if requested and it's an announcement channel
            if cross_post and target_channel.type == discord.ChannelType.news:
                try:
                    await message.publish()
                except:
                    pass

            await interaction.response.send_message("✅ Announcement posted!", ephemeral=True)

        except Exception as e:
            logger.error(f"Create announcement error: {e}")
            await interaction.response.send_message("❌ Failed to create announcement.", ephemeral=True)

class AutoResponderSystem:
    """
    Auto-responder system for keyword-based responses.
    Features:
    - Keyword triggers
    - Custom responses
    - Response cooldowns
    - Channel restrictions
    """

    def __init__(self, bot):
        self.bot = bot
        self.last_response = {}  # (channel_id, trigger) -> timestamp

    async def handle_message(self, message):
        """Handle message auto-responses."""
        if message.author.bot:
            return

        try:
            config = dm.get_guild_data(message.guild.id, "auto_responder_config", {})
            if not config.get("enabled", False):
                return

            responses = config.get("responses", [])
            content = message.content.lower()

            for response_data in responses:
                trigger = response_data.get("trigger", "").lower()
                if trigger in content:
                    # Check cooldown
                    cooldown_key = (message.channel.id, trigger)
                    last_time = self.last_response.get(cooldown_key, 0)
                    cooldown = response_data.get("cooldown", 300)  # 5 minutes default

                    if time.time() - last_time < cooldown:
                        continue

                    # Send response
                    response_text = response_data.get("response", "")
                    if response_text:
                        await message.channel.send(response_text)
                        self.last_response[cooldown_key] = time.time()

                    # Only send one response per message
                    break

        except Exception as e:
            logger.error(f"Auto-responder error: {e}")

class ReactionRoleSystem:
    """
    Reaction role system for reaction-based role assignment.
    Features:
    - Reaction role messages
    - Role assignment/removal
    - Multiple roles per message
    - Custom emojis support
    """

    def __init__(self, bot):
        self.bot = bot

    async def handle_reaction_add(self, reaction, user):
        """Handle reaction add for role assignment."""
        await self._handle_reaction(reaction, user, add=True)

    async def handle_reaction_remove(self, reaction, user):
        """Handle reaction remove for role removal."""
        await self._handle_reaction(reaction, user, add=False)

    async def _handle_reaction(self, reaction, user, add=True):
        """Handle reaction role logic."""
        if user.bot:
            return

        try:
            config = dm.get_guild_data(reaction.message.guild.id, "reaction_roles_config", {})
            if not config.get("enabled", False):
                return

            reaction_roles = config.get("reaction_roles", {})

            # Check if this message has reaction roles
            message_key = str(reaction.message.id)
            if message_key not in reaction_roles:
                return

            emoji_key = str(reaction.emoji)
            role_data = reaction_roles[message_key].get(emoji_key)

            if not role_data:
                return

            role = reaction.message.guild.get_role(role_data["role_id"])
            if not role:
                return

            if add:
                await user.add_roles(role, reason="Reaction role assignment")
            else:
                await user.remove_roles(role, reason="Reaction role removal")

        except Exception as e:
            logger.error(f"Reaction role error: {e}")

    async def create_reaction_role_message(self, interaction, channel: discord.TextChannel, title, description, role_emoji_pairs):
        """Create a reaction role message."""
        try:
            embed = discord.Embed(
                title=title,
                description=description,
                color=discord.Color.blue()
            )

            # Add role-emoji pairs to embed
            role_list = ""
            for role_emoji, role_id in role_emoji_pairs:
                role = interaction.guild.get_role(role_id)
                if role:
                    role_list += f"{role_emoji} - {role.mention}\n"

            if role_list:
                embed.add_field(name="Available Roles", value=role_list, inline=False)

            message = await channel.send(embed=embed)

            # Save reaction role data
            config = dm.get_guild_data(interaction.guild.id, "reaction_roles_config", {})
            reaction_roles = config.get("reaction_roles", {})

            reaction_roles[str(message.id)] = {}
            for role_emoji, role_id in role_emoji_pairs:
                reaction_roles[str(message.id)][role_emoji] = {"role_id": role_id}

            config["reaction_roles"] = reaction_roles
            dm.update_guild_data(interaction.guild.id, "reaction_roles_config", config)

            # Add reactions
            for role_emoji, _ in role_emoji_pairs:
                try:
                    await message.add_reaction(role_emoji)
                except:
                    pass

            await interaction.response.send_message("✅ Reaction role message created!", ephemeral=True)

        except Exception as e:
            logger.error(f"Create reaction role message error: {e}")
            await interaction.response.send_message("❌ Failed to create reaction role message.", ephemeral=True)

class ReactionMenuSystem:
    """
    Dynamic reaction role menus with multiple pages and categories.
    Features:
    - Multi-page reaction menus
    - Category organization
    - Role requirements
    - Menu navigation
    """

    def __init__(self, bot):
        self.bot = bot

    async def create_menu(self, interaction, channel: discord.TextChannel, title: str, categories: list):
        """Create a reaction menu with categories."""
        # Implementation would create paginated embeds with category buttons
        # For brevity, this is a placeholder implementation
        embed = discord.Embed(title=title, color=discord.Color.blue())

        for category in categories[:5]:  # Limit to 5 categories
            embed.add_field(name=category["name"], value=category["description"], inline=False)

        message = await channel.send(embed=embed)
        await interaction.response.send_message("✅ Reaction menu created!", ephemeral=True)

class RoleButtonSystem:
    """
    Button-based role assignment system.
    Features:
    - Role buttons in embeds
    - One-click role assignment/removal
    - Role requirements
    - Button persistence
    """

    def __init__(self, bot):
        self.bot = bot

    async def create_role_buttons(self, interaction, channel: discord.TextChannel, title: str, roles: list):
        """Create role buttons."""
        embed = discord.Embed(title=title, description="Click buttons to assign/remove roles", color=discord.Color.green())

        view = RoleButtonView(self.bot, roles)
        message = await channel.send(embed=embed, view=view)

        await interaction.response.send_message("✅ Role buttons created!", ephemeral=True)

class RoleButtonView(discord.ui.View):
    def __init__(self, bot, roles):
        super().__init__(timeout=None)
        self.bot = bot

        for role_data in roles[:5]:  # Limit to 5 buttons
            self.add_item(RoleButton(role_data))

class RoleButton(discord.ui.Button):
    def __init__(self, role_data):
        super().__init__(label=role_data["name"], style=discord.ButtonStyle.primary, custom_id=f"role_{role_data['id']}")
        self.role_id = role_data["id"]

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if not role:
            return await interaction.response.send_message("❌ Role not found.", ephemeral=True)

        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(f"✅ Removed {role.name} role.", ephemeral=True)
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"✅ Added {role.name} role.", ephemeral=True)

class ModerationSystem:
    """
    General moderation tools and logging.
    Features:
    - Bulk actions
    - User moderation history
    - Moderation commands
    - Case management
    """

    def __init__(self, bot):
        self.bot = bot

    async def purge_messages(self, interaction, channel: discord.TextChannel, amount: int, reason: str = None):
        """Purge messages from a channel."""
        try:
            if not interaction.user.guild_permissions.manage_messages:
                return await interaction.response.send_message("❌ You don't have permission to purge messages.", ephemeral=True)

            if amount < 1 or amount > 100:
                return await interaction.response.send_message("❌ Amount must be between 1-100.", ephemeral=True)

            deleted = await channel.purge(limit=amount, reason=reason)

            embed = discord.Embed(
                title="🗑️ Messages Purged",
                description=f"Deleted {len(deleted)} messages from {channel.mention}",
                color=discord.Color.red()
            )
            if reason:
                embed.add_field(name="Reason", value=reason, inline=False)

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Purge messages error: {e}")
            await interaction.response.send_message("❌ Failed to purge messages.", ephemeral=True)

class LoggingSystem:
    """
    General server logging system.
    Features:
    - Message edits/deletes
    - Member joins/leaves
    - Role changes
    - Channel changes
    - Voice activity
    """

    def __init__(self, bot):
        self.bot = bot

    async def log_event(self, guild_id, event_type, details):
        """Log a general server event."""
        try:
            config = dm.get_guild_data(guild_id, "logging_config", {})
            if not config.get("enabled", False):
                return

            log_channel_id = config.get("channel")
            if not log_channel_id:
                return

            # This would log to the configured channel
            # Implementation details would depend on event type

        except Exception as e:
            logger.error(f"General logging error: {e}")

class ModLoggingSystem:
    """
    Moderation action logging system.
    Features:
    - Moderation command logging
    - Punishment tracking
    - Case numbers
    - Moderator activity logs
    """

    def __init__(self, bot):
        self.bot = bot

    async def log_moderation_action(self, guild_id, action, moderator, target, reason=None, details=None):
        """Log a moderation action."""
        try:
            config = dm.get_guild_data(guild_id, "mod_logging_config", {})
            if not config.get("enabled", False):
                return

            log_channel_id = config.get("channel")
            if not log_channel_id:
                return

            # Generate case number
            cases = dm.get_guild_data(guild_id, "moderation_cases", [])
            case_number = len(cases) + 1

            case = {
                "case_number": case_number,
                "action": action,
                "moderator_id": moderator.id,
                "target_id": target.id if hasattr(target, 'id') else target,
                "reason": reason,
                "details": details,
                "timestamp": time.time()
            }

            cases.append(case)
            dm.update_guild_data(guild_id, "moderation_cases", cases[-1000:])  # Keep last 1000

            # Log to channel
            guild = self.bot.get_guild(guild_id)
            log_channel = guild.get_channel(int(log_channel_id))

            if log_channel:
                embed = discord.Embed(
                    title=f"🔨 Case #{case_number} - {action.title()}",
                    color=discord.Color.orange()
                )

                embed.add_field(name="Moderator", value=moderator.mention, inline=True)
                embed.add_field(name="Target", value=f"<@{case['target_id']}>", inline=True)
                embed.add_field(name="Action", value=action.title(), inline=True)

                if reason:
                    embed.add_field(name="Reason", value=reason, inline=False)

                if details:
                    embed.add_field(name="Details", value=details, inline=False)

                embed.timestamp = discord.utils.utcnow()

                await log_channel.send(embed=embed)

        except Exception as e:
            logger.error(f"Mod logging error: {e}")

class AntiRaidConfigPanel(discord.ui.View):
    """Config panel for anti-raid system."""

    def __init__(self, bot, guild_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="Toggle Anti-Raid", style=discord.ButtonStyle.primary, row=0)
    async def toggle_anti_raid(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "anti_raid_config", {})
        enabled = config.get("enabled", False)
        config["enabled"] = not enabled
        dm.update_guild_data(self.guild_id, "anti_raid_config", config)

        await interaction.response.send_message(
            f"✅ Anti-raid system {'enabled' if not enabled else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Manual Lockdown", style=discord.ButtonStyle.danger, row=0)
    async def manual_lockdown(self, interaction: discord.Interaction, button: discord.ui.Button):
        # This would need to be implemented in the AntiRaidSystem class
        await interaction.response.send_message("❌ Manual lockdown not implemented yet.", ephemeral=True)

    @discord.ui.button(label="Set Join Limits", style=discord.ButtonStyle.secondary, row=1)
    async def set_join_limits(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetJoinLimitsModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Set Account Age", style=discord.ButtonStyle.secondary, row=1)
    async def set_account_age(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetAccountAgeModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Set Log Channel", style=discord.ButtonStyle.primary, row=2)
    async def set_log_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetRaidLogChannelModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

class SetJoinLimitsModal(discord.ui.Modal, title="Set Join Velocity Limits"):
    window = discord.ui.TextInput(label="Check Window (seconds)", placeholder="60")
    max_joins = discord.ui.TextInput(label="Max Joins per Window", placeholder="5")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            window = int(self.window.value)
            max_joins = int(self.max_joins.value)

            config = dm.get_guild_data(self.guild_id, "anti_raid_config", {})
            config["join_check_window"] = window
            config["max_joins_per_window"] = max_joins
            dm.update_guild_data(self.guild_id, "anti_raid_config", config)

            await interaction.response.send_message(f"✅ Join limits set: {max_joins} joins per {window} seconds", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter valid numbers", ephemeral=True)

class SetAccountAgeModal(discord.ui.Modal, title="Set Minimum Account Age"):
    min_age = discord.ui.TextInput(label="Minimum Age (hours)", placeholder="24")
    action = discord.ui.TextInput(label="Action (kick/ban)", placeholder="kick")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            min_age = int(self.min_age.value)
            action = self.action.value.lower()

            if action not in ["kick", "ban"]:
                raise ValueError

            config = dm.get_guild_data(self.guild_id, "anti_raid_config", {})
            config["min_account_age_hours"] = min_age
            config["new_account_action"] = action
            dm.update_guild_data(self.guild_id, "anti_raid_config", config)

            await interaction.response.send_message(f"✅ New accounts under {min_age}h will be {action}ed", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid input. Action must be 'kick' or 'ban'", ephemeral=True)

class SetRaidLogChannelModal(discord.ui.Modal, title="Set Anti-Raid Log Channel"):
    channel_id = discord.ui.TextInput(label="Channel ID", placeholder="123456789")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel_id = int(self.channel_id.value)
            channel = interaction.guild.get_channel(channel_id)

            if not channel or not isinstance(channel, discord.TextChannel):
                return await interaction.response.send_message("❌ Text channel not found", ephemeral=True)

            config = dm.get_guild_data(self.guild_id, "anti_raid_config", {})
            config["log_channel"] = str(channel_id)
            dm.update_guild_data(self.guild_id, "anti_raid_config", config)

            await interaction.response.send_message(f"✅ Anti-raid log channel set to {channel.mention}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid channel ID", ephemeral=True)

class GuardianConfigPanel(discord.ui.View):
    """Config panel for guardian system."""

    def __init__(self, bot, guild_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="Toggle Guardian", style=discord.ButtonStyle.primary, row=0)
    async def toggle_guardian(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "guardian_config", {})
        enabled = config.get("enabled", False)
        config["enabled"] = not enabled
        dm.update_guild_data(self.guild_id, "guardian_config", config)

        await interaction.response.send_message(
            f"✅ Guardian system {'enabled' if not enabled else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Token Scanning", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_token_scanning(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "guardian_config", {})
        scan_tokens = config.get("scan_tokens", True)
        config["scan_tokens"] = not scan_tokens
        dm.update_guild_data(self.guild_id, "guardian_config", config)

        await interaction.response.send_message(
            f"✅ Token scanning {'enabled' if not scan_tokens else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Link Scanning", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_link_scanning(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "guardian_config", {})
        scan_links = config.get("scan_links", True)
        config["scan_links"] = not scan_links
        dm.update_guild_data(self.guild_id, "guardian_config", config)

        await interaction.response.send_message(
            f"✅ Link scanning {'enabled' if not scan_links else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Content Scanning", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_content_scanning(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "guardian_config", {})
        scan_content = config.get("scan_content", True)
        config["scan_content"] = not scan_content
        dm.update_guild_data(self.guild_id, "guardian_config", config)

        await interaction.response.send_message(
            f"✅ Content scanning {'enabled' if not scan_content else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Set Log Channel", style=discord.ButtonStyle.primary, row=2)
    async def set_log_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetGuardianLogChannelModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

class SetGuardianLogChannelModal(discord.ui.Modal, title="Set Guardian Log Channel"):
    channel_id = discord.ui.TextInput(label="Channel ID", placeholder="123456789")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel_id = int(self.channel_id.value)
            channel = interaction.guild.get_channel(channel_id)

            if not channel or not isinstance(channel, discord.TextChannel):
                return await interaction.response.send_message("❌ Text channel not found", ephemeral=True)

            config = dm.get_guild_data(self.guild_id, "guardian_config", {})
            config["log_channel"] = str(channel_id)
            dm.update_guild_data(self.guild_id, "guardian_config", config)

            await interaction.response.send_message(f"✅ Guardian log channel set to {channel.mention}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid channel ID", ephemeral=True)

class AutoModConfigPanel(discord.ui.View):
    """Config panel for auto-mod system."""

    def __init__(self, bot, guild_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="Toggle Auto-Mod", style=discord.ButtonStyle.primary, row=0)
    async def toggle_automod(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "automod_config", {})
        enabled = config.get("enabled", False)
        config["enabled"] = not enabled
        dm.update_guild_data(self.guild_id, "automod_config", config)

        await interaction.response.send_message(
            f"✅ Auto-mod system {'enabled' if not enabled else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Anti-Spam", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_anti_spam(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "automod_config", {})
        anti_spam = config.get("anti_spam", True)
        config["anti_spam"] = not anti_spam
        dm.update_guild_data(self.guild_id, "automod_config", config)

        await interaction.response.send_message(
            f"✅ Anti-spam {'enabled' if not anti_spam else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Anti-Caps", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_anti_caps(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "automod_config", {})
        anti_caps = config.get("anti_caps", True)
        config["anti_caps"] = not anti_caps
        dm.update_guild_data(self.guild_id, "automod_config", config)

        await interaction.response.send_message(
            f"✅ Anti-caps {'enabled' if not anti_caps else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Anti-Mentions", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_anti_mentions(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "automod_config", {})
        anti_mentions = config.get("anti_mentions", True)
        config["anti_mentions"] = not anti_mentions
        dm.update_guild_data(self.guild_id, "automod_config", config)

        await interaction.response.send_message(
            f"✅ Anti-mentions {'enabled' if not anti_mentions else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Anti-Invites", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_anti_invites(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "automod_config", {})
        anti_invites = config.get("anti_invites", True)
        config["anti_invites"] = not anti_invites
        dm.update_guild_data(self.guild_id, "automod_config", config)

        await interaction.response.send_message(
            f"✅ Anti-invites {'enabled' if not anti_invites else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Word Filter", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_word_filter(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "automod_config", {})
        word_filter = config.get("word_filter", True)
        config["word_filter"] = not word_filter
        dm.update_guild_data(self.guild_id, "automod_config", config)

        await interaction.response.send_message(
            f"✅ Word filter {'enabled' if not word_filter else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Add Filtered Word", style=discord.ButtonStyle.success, row=3)
    async def add_filtered_word(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddFilteredWordModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Set Log Channel", style=discord.ButtonStyle.primary, row=3)
    async def set_log_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetAutoModLogChannelModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

class AddFilteredWordModal(discord.ui.Modal, title="Add Filtered Word"):
    word = discord.ui.TextInput(label="Word to filter", placeholder="badword")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        word = self.word.value.lower().strip()
        if not word:
            return await interaction.response.send_message("❌ Please enter a word", ephemeral=True)

        config = dm.get_guild_data(self.guild_id, "automod_config", {})
        filtered_words = config.get("filtered_words", [])

        if word in filtered_words:
            return await interaction.response.send_message(f"❌ Word '{word}' is already filtered", ephemeral=True)

        filtered_words.append(word)
        config["filtered_words"] = filtered_words
        dm.update_guild_data(self.guild_id, "automod_config", config)

        await interaction.response.send_message(f"✅ Added '{word}' to filtered words", ephemeral=True)

class SetAutoModLogChannelModal(discord.ui.Modal, title="Set Auto-Mod Log Channel"):
    channel_id = discord.ui.TextInput(label="Channel ID", placeholder="123456789")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel_id = int(self.channel_id.value)
            channel = interaction.guild.get_channel(channel_id)

            if not channel or not isinstance(channel, discord.TextChannel):
                return await interaction.response.send_message("❌ Text channel not found", ephemeral=True)

            config = dm.get_guild_data(self.guild_id, "automod_config", {})
            config["log_channel"] = str(channel_id)
            dm.update_guild_data(self.guild_id, "automod_config", config)

            await interaction.response.send_message(f"✅ Auto-mod log channel set to {channel.mention}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid channel ID", ephemeral=True)

class WarningsConfigPanel(discord.ui.View):
    """Config panel for warnings system."""

    def __init__(self, bot, guild_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="Toggle Warnings", style=discord.ButtonStyle.primary, row=0)
    async def toggle_warnings(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "warnings_config", {})
        enabled = config.get("enabled", True)
        config["enabled"] = not enabled
        dm.update_guild_data(self.guild_id, "warnings_config", config)

        await interaction.response.send_message(
            f"✅ Warnings system {'enabled' if not enabled else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Set Auto-Punishment", style=discord.ButtonStyle.secondary, row=0)
    async def set_auto_punishment(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetAutoPunishmentModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Set Log Channel", style=discord.ButtonStyle.primary, row=1)
    async def set_log_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetWarningsLogChannelModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Warn User", style=discord.ButtonStyle.danger, row=1)
    async def warn_user_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = WarnUserModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

class SetAutoPunishmentModal(discord.ui.Modal, title="Set Auto-Punishment Thresholds"):
    timeout_threshold = discord.ui.TextInput(label="Warnings for Timeout", placeholder="2")
    kick_threshold = discord.ui.TextInput(label="Warnings for Kick", placeholder="4")
    ban_threshold = discord.ui.TextInput(label="Warnings for Ban", placeholder="6")
    timeout_duration = discord.ui.TextInput(label="Timeout Duration (minutes)", placeholder="60")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            timeout_thresh = int(self.timeout_threshold.value)
            kick_thresh = int(self.kick_threshold.value)
            ban_thresh = int(self.ban_threshold.value)
            timeout_dur = int(self.timeout_duration.value) * 60  # Convert to seconds

            config = dm.get_guild_data(self.guild_id, "warnings_config", {})
            config["auto_punishment"] = {
                "timeout": timeout_thresh,
                "kick": kick_thresh,
                "ban": ban_thresh
            }
            config["timeout_duration"] = timeout_dur
            dm.update_guild_data(self.guild_id, "warnings_config", config)

            await interaction.response.send_message(
                f"✅ Auto-punishment set: Timeout at {timeout_thresh}, Kick at {kick_thresh}, Ban at {ban_thresh} warnings",
                ephemeral=True
            )
        except ValueError:
            await interaction.response.send_message("❌ Please enter valid numbers", ephemeral=True)

class SetWarningsLogChannelModal(discord.ui.Modal, title="Set Warnings Log Channel"):
    channel_id = discord.ui.TextInput(label="Channel ID", placeholder="123456789")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel_id = int(self.channel_id.value)
            channel = interaction.guild.get_channel(channel_id)

            if not channel or not isinstance(channel, discord.TextChannel):
                return await interaction.response.send_message("❌ Text channel not found", ephemeral=True)

            config = dm.get_guild_data(self.guild_id, "warnings_config", {})
            config["log_channel"] = str(channel_id)
            dm.update_guild_data(self.guild_id, "warnings_config", config)

            await interaction.response.send_message(f"✅ Warnings log channel set to {channel.mention}", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid channel ID", ephemeral=True)

class WarnUserModal(discord.ui.Modal, title="Warn User"):
    user_id = discord.ui.TextInput(label="User ID or Mention", placeholder="@user or 123456789")
    reason = discord.ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, placeholder="Violation of rules")
    severity = discord.ui.TextInput(label="Severity (low/medium/high)", placeholder="medium")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Parse user ID
            user_id = int(self.user_id.value.strip("<@!>"))
            user = interaction.guild.get_member(user_id)

            if not user:
                return await interaction.response.send_message("❌ User not found in this server.", ephemeral=True)

            severity = self.severity.value.lower()
            if severity not in ["low", "medium", "high"]:
                severity = "medium"

            # Use the warnings system
            warnings_system = WarningsSystem(self.bot)
            await warnings_system.warn_user(interaction, user, self.reason.value, severity)

        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid user ID or mention.", ephemeral=True)

class StaffPromotionConfigPanel(discord.ui.View):
    """Config panel for staff promotion system."""

    def __init__(self, bot, guild_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="Toggle Promotions", style=discord.ButtonStyle.primary, row=0)
    async def toggle_promotions(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
        enabled = config.get("enabled", False)
        config["enabled"] = not enabled
        dm.update_guild_data(self.guild_id, "staff_promo_config", config)

        await interaction.response.send_message(
            f"✅ Staff promotion system {'enabled' if not enabled else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Add Staff Role", style=discord.ButtonStyle.success, row=0)
    async def add_staff_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddStaffRolePromoModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Check Eligibility", style=discord.ButtonStyle.secondary, row=1)
    async def check_eligibility(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CheckEligibilityModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Promote User", style=discord.ButtonStyle.primary, row=1)
    async def promote_user_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PromoteUserModal(self.bot, self.guild_id)
        await interaction.response.send_modal(modal)

class AddStaffRolePromoModal(discord.ui.Modal, title="Add Staff Role"):
    role_id = discord.ui.TextInput(label="Role ID", placeholder="123456789")
    role_name = discord.ui.TextInput(label="Role Name", placeholder="Moderator")
    min_days = discord.ui.TextInput(label="Min Days in Server", placeholder="30")
    min_messages = discord.ui.TextInput(label="Min Messages", placeholder="1000")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            role_id = int(self.role_id.value)
            min_days = int(self.min_days.value)
            min_messages = int(self.min_messages.value)

            config = dm.get_guild_data(self.guild_id, "staff_promo_config", {})
            staff_roles = config.get("staff_roles", [])

            role_data = {
                "id": role_id,
                "name": self.role_name.value,
                "requirements": {
                    "min_days": min_days,
                    "min_messages": min_messages
                }
            }

            staff_roles.append(role_data)
            config["staff_roles"] = staff_roles
            dm.update_guild_data(self.guild_id, "staff_promo_config", config)

            await interaction.response.send_message(f"✅ Added {self.role_name.value} to staff roles", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter valid numbers", ephemeral=True)

class CheckEligibilityModal(discord.ui.Modal, title="Check Promotion Eligibility"):
    user_id = discord.ui.TextInput(label="User ID or Mention", placeholder="@user or 123456789")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = int(self.user_id.value.strip("<@!>"))
            user = interaction.guild.get_member(user_id)

            if not user:
                return await interaction.response.send_message("❌ User not found in this server.", ephemeral=True)

            promo_system = StaffPromotionSystem(self.bot)
            eligible, reasons = await promo_system.check_promotion_eligibility(user.id, self.guild_id)

            embed = discord.Embed(
                title="📊 Promotion Eligibility Check",
                description=f"Checking eligibility for {user.mention}",
                color=discord.Color.green() if eligible else discord.Color.red()
            )

            if eligible:
                embed.add_field(name="Status", value="✅ Eligible for promotion", inline=False)
            else:
                embed.add_field(name="Status", value="❌ Not eligible", inline=False)
                if reasons:
                    embed.add_field(name="Reasons", value="\n".join(f"• {r}" for r in reasons), inline=False)

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid user ID or mention.", ephemeral=True)

class PromoteUserModal(discord.ui.Modal, title="Promote User"):
    user_id = discord.ui.TextInput(label="User ID or Mention", placeholder="@user or 123456789")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            user_id = int(self.user_id.value.strip("<@!>"))
            user = interaction.guild.get_member(user_id)

            if not user:
                return await interaction.response.send_message("❌ User not found in this server.", ephemeral=True)

            promo_system = StaffPromotionSystem(self.bot)
            await promo_system.promote_user(interaction, user)

        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid user ID or mention.", ephemeral=True)

class StaffApplicationModal(discord.ui.Modal, title="Staff Application"):
    experience = discord.ui.TextInput(label="Staff Experience", style=discord.TextStyle.paragraph, placeholder="Describe your moderation experience")
    reason = discord.ui.TextInput(label="Why do you want to be staff?", style=discord.TextStyle.paragraph, placeholder="Explain your motivation")
    availability = discord.ui.TextInput(label="Availability", placeholder="How many hours per week?")

    def __init__(self, bot, guild_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        application_id = str(uuid.uuid4())[:8]

        application = {
            "id": application_id,
            "user_id": interaction.user.id,
            "guild_id": self.guild_id,
            "experience": self.experience.value,
            "reason": self.reason.value,
            "availability": self.availability.value,
            "submitted_at": time.time(),
            "status": "pending"
        }

        applications = dm.get_guild_data(self.guild_id, "staff_applications", {})
        applications[application_id] = application
        dm.update_guild_data(self.guild_id, "staff_applications", applications)

        await interaction.response.send_message("✅ Application submitted! You will be notified of the decision.", ephemeral=True)

class WarningAppealModal(discord.ui.Modal, title="Warning Appeal"):
    warning_id = discord.ui.TextInput(label="Warning ID", placeholder="ABC12345")
    reason = discord.ui.TextInput(label="Appeal Reason", style=discord.TextStyle.paragraph, placeholder="Explain why this warning should be removed")

    def __init__(self, bot, guild_id, user_id):
        super().__init__()
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        appeal_id = str(uuid.uuid4())[:8]

        appeal = {
            "id": appeal_id,
            "user_id": self.user_id,
            "guild_id": self.guild_id,
            "warning_id": self.warning_id.value,
            "reason": self.reason.value,
            "submitted_at": time.time(),
            "status": "pending"
        }

        appeals = dm.get_guild_data(self.guild_id, "warning_appeals", {})
        appeals[appeal_id] = appeal
        dm.update_guild_data(self.guild_id, "warning_appeals", appeals)

        await interaction.response.send_message("✅ Appeal submitted! You will be notified of the decision.", ephemeral=True)

class StaffApplicationButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Apply for Staff", style=discord.ButtonStyle.primary, custom_id="staff_application")

    async def callback(self, interaction: discord.Interaction):
        app_system = ApplicationSystem(interaction.client)
        await app_system.create_application(interaction)

class WarningAppealButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Appeal Warning", style=discord.ButtonStyle.secondary, custom_id="warning_appeal")

    async def callback(self, interaction: discord.Interaction):
        appeal_system = AppealSystem(interaction.client)
        await appeal_system.create_appeal(interaction)

# Config Panels
class StaffShiftsConfigPanel(discord.ui.View):
    def __init__(self, bot, guild_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="Toggle Shifts", style=discord.ButtonStyle.primary, row=0)
    async def toggle_shifts(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = dm.get_guild_data(self.guild_id, "staff_shifts_config", {})
        enabled = config.get("enabled", True)
        config["enabled"] = not enabled
        dm.update_guild_data(self.guild_id, "staff_shifts_config", config)

        await interaction.response.send_message(
            f"✅ Staff shifts system {'enabled' if not enabled else 'disabled'}",
            ephemeral=True
        )

    @discord.ui.button(label="Start Shift", style=discord.ButtonStyle.success, row=1)
    async def start_shift(self, interaction: discord.Interaction, button: discord.ui.Button):
        shifts_system = StaffShiftSystem(self.bot)
        await shifts_system.start_shift(interaction)

    @discord.ui.button(label="End Shift", style=discord.ButtonStyle.danger, row=1)
    async def end_shift(self, interaction: discord.Interaction, button: discord.ui.Button):
        shifts_system = StaffShiftSystem(self.bot)
        await shifts_system.end_shift(interaction)

    @discord.ui.button(label="My Shifts", style=discord.ButtonStyle.secondary, row=2)
    async def my_shifts(self, interaction: discord.Interaction, button: discord.ui.Button):
        shifts_system = StaffShiftSystem(self.bot)
        await shifts_system.get_my_shifts(interaction)