"""
Suggestions System - Full implementation for Discord bot
Handles suggestion submission, voting, staff review, and complete admin panel
"""

import discord
from discord import ui, app_commands
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict
import time

from data_manager import dm


class SuggestionModal(ui.Modal, title="Submit a Suggestion"):
    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

        # Get categories from config
        config = dm.get_guild_data(guild_id, "suggestions_config", {})
        categories = config.get('categories', ['Feature', 'Bug', 'Content', 'Other'])
        
        self.title_input = ui.TextInput(
            label="Suggestion Title",
            style=discord.TextStyle.short,
            placeholder="A short descriptive title...",
            required=True,
            max_length=100
        )
        
        self.description_input = ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            placeholder="Describe your suggestion in detail...",
            required=True,
            max_length=2000
        )
        
        self.add_item(self.title_input)
        self.add_item(self.description_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        config = dm.get_guild_data(self.guild_id, "suggestions_config", {})
        guild_data = dm.get_guild_data(self.guild_id)

        # Check cooldown
        cooldown_minutes = config.get('cooldown_minutes', 30)
        user_suggestions = dm.get_guild_data(self.guild_id, 'suggestions_by_user', {})
        user_id_str = str(interaction.user.id)

        if user_id_str in user_suggestions:
            last_submission = user_suggestions[user_id_str].get('last_submission')
            if last_submission:
                last_time = datetime.fromisoformat(last_submission)
                cooldown_end = last_time + timedelta(minutes=cooldown_minutes)
                if datetime.now(timezone.utc) < cooldown_end:
                    time_left = (cooldown_end - datetime.now(timezone.utc)).seconds // 60
                    await interaction.response.send_message(
                        f"⏱️ Please wait **{time_left} more minutes** before submitting another suggestion.",
                        ephemeral=True
                    )
                    return

        # Generate suggestion ID
        suggestions = dm.get_guild_data(self.guild_id, 'suggestions', [])
        suggestion_id = len(suggestions) + 1

        # Create suggestion data
        suggestion_data = {
            'id': suggestion_id,
            'user_id': interaction.user.id,
            'username': interaction.user.name,
            'title': self.title_input.value,
            'description': self.description_input.value,
            'category': 'Other',  # Will be set via select
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'status': 'pending',
            'upvotes': [],
            'downvotes': [],
            'comments': []
        }

        # Save suggestion
        suggestions.append(suggestion_data)
        dm.update_guild_data(self.guild_id, 'suggestions', suggestions)

        # Update user tracking
        if user_id_str not in user_suggestions:
            user_suggestions[user_id_str] = {}
        user_suggestions[user_id_str]['last_submission'] = datetime.utcnow().isoformat()
        user_suggestions[user_id_str]['count'] = user_suggestions[user_id_str].get('count', 0) + 1
        dm.update_guild_data(self.guild_id, 'suggestions_by_user', user_suggestions)

        # Post to suggestions channel
        suggestions_channel_id = config.get('suggestions_channel_id')
        if suggestions_channel_id:
            channel = interaction.client.get_channel(suggestions_channel_id)
            if channel:
                embed = discord.Embed(
                    title=f"💡 {self.title_input.value}",
                    description=self.description_input.value,
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                embed.set_author(name=interaction.user.name, icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
                embed.add_field(name="📝 Category", value=suggestion_data['category'], inline=True)
                embed.add_field(name="🔢 ID", value=f"#{suggestion_id}", inline=True)
                embed.add_field(name="👤 Author", value=interaction.user.mention, inline=True)
                embed.set_footer(text=f"Suggestion ID: {suggestion_id}")

                view = SuggestionVoteView(suggestion_id, self.guild_id)
                msg = await channel.send(embed=embed, view=view)

                # Store message reference
                suggestion_data['message_id'] = msg.id
                suggestion_data['channel_id'] = channel.id
                dm.update_guild_data(self.guild_id, 'suggestions', suggestions)

        # Send DM
        send_dms = config.get('submitter_dms_enabled', True)
        if send_dms:
            try:
                await interaction.user.send(
                    f"✅ **Your suggestion has been submitted!**\n\n"
                    f"**Title:** {self.title_input.value}\n"
                    f"**ID:** #{suggestion_id}\n\n"
                    f"Staff will review it soon. You'll be notified of any updates."
                )
            except:
                pass

        # Log action
        action_log = guild_data.get('action_logs', [])
        action_log.append({
            'action': 'suggestion_submitted',
            'user_id': interaction.user.id,
            'username': interaction.user.name,
            'suggestion_id': suggestion_id,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'details': f"Suggestion: {self.title_input.value[:100]}"
        })
        guild_data['action_logs'] = action_log[-1000:]
        dm.update_guild_data(self.guild_id, guild_data)
        
        await interaction.response.send_message(
            f"✅ Your suggestion **#{suggestion_id}** has been submitted!",
            ephemeral=True
        )


class SuggestionVoteView(ui.View):
    def __init__(self, suggestion_id: int, guild_id: int):
        super().__init__(timeout=None)
        self.suggestion_id = suggestion_id
        self.guild_id = guild_id

    def _get_suggestion(self, suggestions: list) -> Optional[dict]:
        for s in suggestions:
            if s['id'] == self.suggestion_id:
                return s
        return None

    @ui.button(label="Upvote", style=discord.ButtonStyle.success, emoji="✅", custom_id="suggestion_upvote")
    async def upvote(self, interaction: discord.Interaction, button: ui.Button):
        suggestions = dm.get_guild_data(self.guild_id, 'suggestions', [])
        suggestion = self._get_suggestion(suggestions)
        
        if not suggestion:
            await interaction.response.send_message("❌ Suggestion not found.", ephemeral=True)
            return
        
        user_id = interaction.user.id
        user_id_str = str(user_id)
        
        # Toggle upvote
        if user_id in suggestion['upvotes']:
            suggestion['upvotes'].remove(user_id)
        else:
            suggestion['upvotes'].append(user_id)
            if user_id in suggestion.get('downvotes', []):
                suggestion['downvotes'].remove(user_id)

        dm.update_guild_data(self.guild_id, 'suggestions', suggestions)
        
        # Update embed
        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            upvote_count = len(suggestion['upvotes'])
            downvote_count = len(suggestion.get('downvotes', []))
            
            # Update fields or description to show vote counts
            for field in embed.fields:
                if field.name == "📊 Votes":
                    embed.set_field_at(
                        embed.fields.index(field),
                        name="📊 Votes",
                        value=f"✅ {upvote_count} | ❌ {downvote_count}",
                        inline=True
                    )
                    break
            else:
                embed.add_field(name="📊 Votes", value=f"✅ {upvote_count} | ❌ {downvote_count}", inline=True)
            
            await interaction.message.edit(embed=embed)
        
        await interaction.response.send_message(
            f"✅ Upvoted! Total: {len(suggestion['upvotes'])}",
            ephemeral=True
        )
    
    @ui.button(label="Downvote", style=discord.ButtonStyle.danger, emoji="❌", custom_id="suggestion_downvote")
    async def downvote(self, interaction: discord.Interaction, button: ui.Button):
        suggestions = dm.get_guild_data(self.guild_id, 'suggestions', [])
        suggestion = self._get_suggestion(suggestions)
        
        if not suggestion:
            await interaction.response.send_message("❌ Suggestion not found.", ephemeral=True)
            return
        
        user_id = interaction.user.id
        
        # Toggle downvote
        if user_id in suggestion.get('downvotes', []):
            suggestion['downvotes'].remove(user_id)
        else:
            if 'downvotes' not in suggestion:
                suggestion['downvotes'] = []
            suggestion['downvotes'].append(user_id)
            if user_id in suggestion['upvotes']:
                suggestion['upvotes'].remove(user_id)

        dm.update_guild_data(self.guild_id, 'suggestions', suggestions)
        
        # Update embed
        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            upvote_count = len(suggestion['upvotes'])
            downvote_count = len(suggestion.get('downvotes', []))
            
            for field in embed.fields:
                if field.name == "📊 Votes":
                    embed.set_field_at(
                        embed.fields.index(field),
                        name="📊 Votes",
                        value=f"✅ {upvote_count} | ❌ {downvote_count}",
                        inline=True
                    )
                    break
            else:
                embed.add_field(name="📊 Votes", value=f"✅ {upvote_count} | ❌ {downvote_count}", inline=True)
            
            await interaction.message.edit(embed=embed)
        
        await interaction.response.send_message(
            f"❌ Downvoted! Total: {len(suggestion['downvotes'])}",
            ephemeral=True
        )
    
    @ui.button(label="Results", style=discord.ButtonStyle.secondary, emoji="📊", custom_id="suggestion_results")
    async def results(self, interaction: discord.Interaction, button: ui.Button):
        suggestions = dm.get_guild_data(self.guild_id, 'suggestions', [])
        suggestion = self._get_suggestion(suggestions)
        
        if not suggestion:
            await interaction.response.send_message("❌ Suggestion not found.", ephemeral=True)
            return
        
        upvotes = len(suggestion['upvotes'])
        downvotes = len(suggestion.get('downvotes', []))
        total = upvotes + downvotes
        approval = (upvotes / total * 100) if total > 0 else 50
        
        embed = discord.Embed(
            title=f"📊 Results for #{self.suggestion_id}: {suggestion['title']}",
            color=discord.Color.blue()
        )
        embed.add_field(name="✅ Upvotes", value=str(upvotes), inline=True)
        embed.add_field(name="❌ Downvotes", value=str(downvotes), inline=True)
        embed.add_field(name="📈 Approval", value=f"{approval:.1f}%", inline=True)
        embed.add_field(name="👥 Total Votes", value=str(total), inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


class SuggestionReviewView(ui.View):
    """Staff review buttons for suggestions in review channel"""
    def __init__(self, suggestion_id: int, guild_id: int):
        super().__init__(timeout=None)
        self.suggestion_id = suggestion_id
        self.guild_id = guild_id

    def _get_suggestion(self, suggestions: list) -> Optional[dict]:
        for s in suggestions:
            if s['id'] == self.suggestion_id:
                return s
        return None

    @ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅", custom_id="suggestion_approve")
    async def approve(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need Manage Messages permission.", ephemeral=True)
            return

        config = dm.get_guild_data(self.guild_id, "suggestions_config", {})
        suggestions = dm.get_guild_data(self.guild_id, 'suggestions', [])
        suggestion = self._get_suggestion(suggestions)

        if not suggestion:
            await interaction.response.send_message("❌ Suggestion not found.", ephemeral=True)
            return

        suggestion['status'] = 'approved'
        suggestion['reviewed_by'] = interaction.user.id
        suggestion['review_timestamp'] = datetime.utcnow().isoformat()
        dm.update_guild_data(self.guild_id, 'suggestions', suggestions)
        
        # Update original embed
        suggestions_channel_id = config.get('suggestions_channel_id')
        if suggestions_channel_id:
            channel = interaction.client.get_channel(suggestions_channel_id)
            if channel:
                try:
                    msg = await channel.fetch_message(suggestion.get('message_id'))
                    if msg and msg.embeds:
                        embed = msg.embeds[0]
                        embed.color = discord.Color.green()
                        original_title = embed.title
                        if "✅ APPROVED" not in original_title:
                            embed.title = f"✅ APPROVED - {original_title}"
                        await msg.edit(embed=embed, view=None)
                except:
                    pass
        
        # DM submitter
        send_dms = guild_data.get('suggestions_send_dms', True)
        if send_dms:
            approval_dm = guild_data.get('suggestions_approval_dm',
                "🎉 **Your suggestion has been approved!**\n\nThank you for contributing to {server}. "
                "Our team will work on implementing it.")
            approval_dm = approval_dm.replace('{server}', interaction.guild.name)
            
            try:
                user = await interaction.client.fetch_user(suggestion['user_id'])
                await user.send(approval_dm)
            except:
                pass
        
        # Log
        action_log = guild_data.get('action_logs', [])
        action_log.append({
            'action': 'suggestion_approved',
            'moderator_id': interaction.user.id,
            'suggestion_id': self.suggestion_id,
            'timestamp': datetime.utcnow().isoformat()
        })
        guild_data['action_logs'] = action_log[-1000:]
        dm.update_guild_data(self.guild_id, guild_data)
        
        await interaction.response.send_message("✅ Suggestion approved!", ephemeral=True)
    
    @ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌", custom_id="suggestion_deny")
    async def deny(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need Manage Messages permission.", ephemeral=True)
            return
        
        await interaction.response.send_modal(DenySuggestionModal(self.suggestion_id, self.guild_id))
    
    @ui.button(label="In Progress", style=discord.ButtonStyle.primary, emoji="🚧", custom_id="suggestion_progress")
    async def in_progress(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need Manage Messages permission.", ephemeral=True)
            return

        config = dm.get_guild_data(self.guild_id, "suggestions_config", {})
        suggestions = dm.get_guild_data(self.guild_id, 'suggestions', [])
        suggestion = self._get_suggestion(suggestions)
        
        if not suggestion:
            await interaction.response.send_message("❌ Suggestion not found.", ephemeral=True)
            return
        
        suggestion['status'] = 'in_progress'
        dm.update_guild_data(self.guild_id, 'suggestions', suggestions)

        # Update embed
        suggestions_channel_id = config.get('suggestions_channel_id')
        if suggestions_channel_id:
            channel = interaction.client.get_channel(suggestions_channel_id)
            if channel:
                try:
                    msg = await channel.fetch_message(suggestion.get('message_id'))
                    if msg and msg.embeds:
                        embed = msg.embeds[0]
                        embed.color = discord.Color.gold()
                        original_title = embed.title
                        if "🚧 IN PROGRESS" not in original_title:
                            embed.title = f"🚧 IN PROGRESS - {original_title}"
                        await msg.edit(embed=embed)
                except:
                    pass
        
        # DM
        send_dms = guild_data.get('suggestions_send_dms', True)
        if send_dms:
            try:
                user = await interaction.client.fetch_user(suggestion['user_id'])
                await user.send(f"🚧 **Your suggestion is now in progress!**\n\nThe team is working on implementing your idea.")
            except:
                pass
        
        await interaction.response.send_message("🚧 Marked as in progress.", ephemeral=True)
    
    @ui.button(label="Completed", style=discord.ButtonStyle.primary, emoji="✅", custom_id="suggestion_completed")
    async def completed(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need Manage Messages permission.", ephemeral=True)
            return

        config = dm.get_guild_data(self.guild_id, "suggestions_config", {})
        suggestions = dm.get_guild_data(self.guild_id, 'suggestions', [])
        suggestion = self._get_suggestion(suggestions)
        
        if not suggestion:
            await interaction.response.send_message("❌ Suggestion not found.", ephemeral=True)
            return
        
        suggestion['status'] = 'completed'
        dm.update_guild_data(self.guild_id, 'suggestions', suggestions)

        # Update embed
        suggestions_channel_id = config.get('suggestions_channel_id')
        if suggestions_channel_id:
            channel = interaction.client.get_channel(suggestions_channel_id)
            if channel:
                try:
                    msg = await channel.fetch_message(suggestion.get('message_id'))
                    if msg and msg.embeds:
                        embed = msg.embeds[0]
                        embed.color = discord.Color.dark_blue()
                        original_title = embed.title
                        if "✅ COMPLETED" not in original_title:
                            embed.title = f"✅ COMPLETED - {original_title}"
                        await msg.edit(embed=embed)
                except:
                    pass
        
        # DM
        send_dms = guild_data.get('suggestions_send_dms', True)
        if send_dms:
            try:
                user = await interaction.client.fetch_user(suggestion['user_id'])
                await user.send(f"✅ **Your suggestion has been completed!**\n\nThank you for helping improve {interaction.guild.name}!")
            except:
                pass
        
        await interaction.response.send_message("✅ Marked as completed.", ephemeral=True)
    
    @ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="suggestion_delete")
    async def delete(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need Manage Messages permission.", ephemeral=True)
            return

        config = dm.get_guild_data(self.guild_id, "suggestions_config", {})
        suggestions = dm.get_guild_data(self.guild_id, 'suggestions', [])

        # Find suggestion to get message_id
        suggestion = next((s for s in suggestions if s['id'] == self.suggestion_id), None)

        # Remove from list
        suggestions = [s for s in suggestions if s['id'] != self.suggestion_id]
        dm.update_guild_data(self.guild_id, 'suggestions', suggestions)

        # Delete message
        suggestions_channel_id = config.get('suggestions_channel_id')
        if suggestions_channel_id:
            channel = interaction.client.get_channel(suggestions_channel_id)
            if channel:
                try:
                    msg = await channel.fetch_message(suggestion.get('message_id'))
                    await msg.delete()
                except:
                    pass
        
        await interaction.response.send_message("🗑️ Suggestion deleted.", ephemeral=True)
    
    @ui.button(label="Pin", style=discord.ButtonStyle.secondary, emoji="📌", custom_id="suggestion_pin")
    async def pin(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ You need Manage Messages permission.", ephemeral=True)
            return

        config = dm.get_guild_data(self.guild_id, "suggestions_config", {})
        suggestions = dm.get_guild_data(self.guild_id, 'suggestions', [])
        suggestion = self._get_suggestion(suggestions)
        
        if not suggestion:
            await interaction.response.send_message("❌ Suggestion not found.", ephemeral=True)
            return

        suggestions_channel_id = config.get('suggestions_channel_id')
        if suggestions_channel_id:
            channel = interaction.client.get_channel(suggestions_channel_id)
            if channel:
                try:
                    msg = await channel.fetch_message(suggestion.get('message_id'))
                    await msg.pin()
                except:
                    pass
        
        await interaction.response.send_message("📌 Suggestion pinned.", ephemeral=True)


class DenySuggestionModal(ui.Modal, title="Deny Suggestion"):
    def __init__(self, suggestion_id: int, guild_id: int):
        super().__init__()
        self.suggestion_id = suggestion_id
        self.guild_id = guild_id
        
        self.reason = ui.TextInput(
            label="Denial Reason",
            style=discord.TextStyle.paragraph,
            placeholder="Explain why this suggestion is being denied...",
            required=True,
            max_length=500
        )
        self.add_item(self.reason)
    
    async def on_submit(self, interaction: discord.Interaction):
        guild_data = dm.get_guild_data(self.guild_id)
        suggestion = None
        for s in guild_data.get('suggestions', []):
            if s['id'] == self.suggestion_id:
                suggestion = s
                break
        
        if not suggestion:
            await interaction.response.send_message("❌ Suggestion not found.", ephemeral=True)
            return
        
        suggestion['status'] = 'denied'
        suggestion['deny_reason'] = self.reason.value
        suggestion['reviewed_by'] = interaction.user.id
        dm.update_guild_data(self.guild_id, guild_data)
        
        # Update embed
        suggestions_channel_id = guild_data.get('suggestions_channel_id')
        if suggestions_channel_id:
            channel = interaction.client.get_channel(suggestions_channel_id)
            if channel:
                try:
                    msg = await channel.fetch_message(suggestion.get('message_id'))
                    if msg and msg.embeds:
                        embed = msg.embeds[0]
                        embed.color = discord.Color.red()
                        original_title = embed.title
                        if "❌ DENIED" not in original_title:
                            embed.title = f"❌ DENIED - {original_title}"
                        embed.add_field(name="❌ Reason", value=self.reason.value[:1024], inline=False)
                        await msg.edit(embed=embed, view=None)
                except:
                    pass
        
        # DM
        send_dms = guild_data.get('suggestions_send_dms', True)
        if send_dms:
            denial_dm = guild_data.get('suggestions_denial_dm',
                "❌ **Your suggestion has been denied.**\n\nReason: {reason}\n\n"
                "Thank you for your contribution.")
            denial_dm = denial_dm.replace('{reason}', self.reason.value)
            
            try:
                user = await interaction.client.fetch_user(suggestion['user_id'])
                await user.send(denial_dm)
            except:
                pass
        
        await interaction.response.send_message("❌ Suggestion denied.", ephemeral=True)


async def setup_suggestions_system(guild: discord.Guild):
    """Setup the complete suggestions system"""
    guild_data = dm.get_guild_data(guild.id)
    
    # Create suggestions channel
    suggestions_channel = None
    for channel in guild.text_channels:
        if "suggestion" in channel.name.lower():
            suggestions_channel = channel
            break
    
    if not suggestions_channel:
        suggestions_channel = await guild.create_text_channel(
            name="suggestions",
            topic="Submit and vote on server suggestions",
            overwrites={
                guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
        )
    
    # Create review channel
    review_channel = None
    for channel in guild.text_channels:
        if "suggestions-review" in channel.name.lower() or "suggestion-review" in channel.name.lower():
            review_channel = channel
            break
    
    if not review_channel:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        for role in guild.roles:
            if role.permissions.manage_messages:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        
        review_channel = await guild.create_text_channel(
            name="suggestions-review",
            topic="Staff only - Suggestion reviews",
            overwrites=overwrites
        )
    
    # Save config
    guild_data['suggestions_channel_id'] = suggestions_channel.id
    guild_data['suggestions_review_channel_id'] = review_channel.id
    if 'suggestions_cooldown_minutes' not in guild_data:
        guild_data['suggestions_cooldown_minutes'] = 30
    if 'suggestions_send_dms' not in guild_data:
        guild_data['suggestions_send_dms'] = True
    if 'suggestions_categories' not in guild_data:
        guild_data['suggestions_categories'] = ['Feature', 'Bug', 'Content', 'Other']
    
    dm.update_guild_data(guild.id, guild_data)
    
    # Send panel embed
    embed = discord.Embed(
        title="💡 Server Suggestions",
        description="Have an idea to improve the server? Submit it here!\n\n"
                    "**Guidelines:**\n• Be constructive and respectful\n• Search before posting to avoid duplicates\n• Vote on suggestions you care about\n\n"
                    "Click the button below to submit your suggestion.",
        color=discord.Color.blue()
    )
    
    # Use the persistent SuggestionButton view (registered globally via add_view in bot.setup_hook).
    # It opens SuggestionModal on click and survives bot restarts because it has a fixed custom_id.
    from modules.auto_setup import SuggestionButton
    view = SuggestionButton(guild_id=guild.id)

    await suggestions_channel.send(embed=embed, view=view)
    
    # Create guide channel
    guide_channel = None
    for channel in guild.text_channels:
        if "suggestions-guide" in channel.name.lower():
            guide_channel = channel
            break
    
    if not guide_channel:
        guide_channel = await guild.create_text_channel(
            name="suggestions-guide",
            overwrites={guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=False)}
        )
    
    guide_embed = discord.Embed(
        title="📖 Suggestions System Guide",
        description="Complete documentation for the suggestions system",
        color=discord.Color.blue()
    )
    guide_embed.add_field(
        name="🔹 For Members",
        value="• Use `/suggest` or click the button in #suggestions\n• Vote with ✅/❌ buttons\n• View results anytime\n• Get notified of status changes",
        inline=False
    )
    guide_embed.add_field(
        name="🔹 Commands",
        value="• `!suggest <text>` - Quick suggest\n• `!suggestionspanel` - Open admin panel\n• `!help suggestions` - Show this guide",
        inline=False
    )
    guide_embed.add_field(
        name="🔹 Staff Features",
        value="• Review in #suggestions-review\n• Approve/Deny/Mark progress\n• Configure categories & cooldowns\n• View statistics",
        inline=False
    )
    
    await guide_channel.send(embed=guide_embed)
    
    return suggestions_channel, review_channel
