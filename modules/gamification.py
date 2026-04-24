"""
🎮 GAMIFICATION SYSTEM - FULLY FUNCTIONAL
All buttons, modals, and features from Part 7 blueprint
"""

import discord
from discord.ext import commands
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import random

from data_manager import DataManager

dm = DataManager()


class GamificationPanel(discord.ui.View):
    """Admin panel for gamification - ALL 15 BUTTONS"""
    
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
    
    @discord.ui.button(label="📋 View Active Challenges", style=discord.ButtonStyle.primary, custom_id="gam_view_challenges")
    async def view_challenges(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        challenges = guild_data.get("challenges", {})
        
        embed = discord.Embed(title="📋 Active Challenges", color=discord.Color.blue())
        
        daily = challenges.get("daily", [])
        weekly = challenges.get("weekly", [])
        monthly = challenges.get("monthly", [])
        
        if daily:
            embed.add_field(name="📅 Daily Challenges", value="\n".join(f"• {c['name']}: {c.get('completions', 0)} completions" for c in daily[:3]), inline=False)
        if weekly:
            embed.add_field(name="📆 Weekly Challenges", value="\n".join(f"• {c['name']}: {c.get('completions', 0)} completions" for c in weekly[:3]), inline=False)
        if monthly:
            embed.add_field(name="📊 Monthly Tournament", value="\n".join(f"• {c['name']}" for c in monthly[:3]), inline=False)
        
        if not any([daily, weekly, monthly]):
            embed.description = "No active challenges!"
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="🔄 Regenerate Daily", style=discord.ButtonStyle.secondary, custom_id="gam_regenerate_daily")
    async def regenerate_daily(self, interaction: discord.Interaction, button: discord.ui.Button):
        confirm_view = discord.ui.View()
        
        @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success)
        async def confirm(interaction: discord.Interaction, button: discord.ui.Button):
            guild_data = dm.get_guild_data(self.guild_id)
            
            # Generate new daily challenges
            challenge_templates = [
                {"name": "Send 20 messages", "goal_type": "messages", "goal_value": 20, "reward_xp": 100, "reward_coins": 50},
                {"name": "Earn 100 coins", "goal_type": "coins_earn", "goal_value": 100, "reward_xp": 50, "reward_coins": 25},
                {"name": "React to 5 messages", "goal_type": "reactions", "goal_value": 5, "reward_xp": 75, "reward_coins": 30},
                {"name": "Spend 10 min in voice", "goal_type": "voice_minutes", "goal_value": 10, "reward_xp": 80, "reward_coins": 40},
                {"name": "Use 3 commands", "goal_type": "commands", "goal_value": 3, "reward_xp": 60, "reward_coins": 25},
            ]
            
            new_daily = random.sample(challenge_templates, 3)
            for c in new_daily:
                c["completions"] = 0
                c["expires"] = (datetime.now() + timedelta(days=1)).isoformat()
            
            if "challenges" not in guild_data:
                guild_data["challenges"] = {}
            guild_data["challenges"]["daily"] = new_daily
            dm.update_guild_data(self.guild_id, guild_data)
            
            await interaction.response.send_message("✅ New daily challenges generated!", ephemeral=True)
        
        confirm_view.add_item(confirm)
        await interaction.response.send_message("⚠️ This will replace current daily challenges. Continue?", view=confirm_view, ephemeral=True)
    
    @discord.ui.button(label="➕ Create Custom Challenge", style=discord.ButtonStyle.success, custom_id="gam_create_challenge")
    async def create_challenge(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CreateChallengeModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🗑️ Remove Challenge", style=discord.ButtonStyle.danger, custom_id="gam_remove_challenge")
    async def remove_challenge(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        challenges = guild_data.get("challenges", {})
        
        all_challenges = []
        for ctype, clist in challenges.items():
            for i, c in enumerate(clist):
                all_challenges.append((ctype, i, c["name"]))
        
        if not all_challenges:
            await interaction.response.send_message("No challenges to remove!", ephemeral=True)
            return
        
        view = discord.ui.View()
        select = discord.ui.Select(placeholder="Select challenge to remove", options=[discord.SelectOption(label=name[:25], value=f"{ctype}:{i}") for ctype, i, name in all_challenges][:25])
        
        async def select_callback(interaction: discord.Interaction):
            ctype, idx = select.values[0].split(":")
            guild_data["challenges"][ctype].pop(int(idx))
            dm.update_guild_data(self.guild_id, guild_data)
            await interaction.response.send_message("✅ Challenge removed!", ephemeral=True)
        
        select.callback = select_callback
        view.add_item(select)
        await interaction.response.send_message("Select a challenge to remove:", view=view, ephemeral=True)
    
    @discord.ui.button(label="📊 Engagement Stats", style=discord.ButtonStyle.secondary, custom_id="gam_engagement_stats")
    async def engagement_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        challenges = guild_data.get("challenges", {})
        
        total_completions = sum(c.get("completions", 0) for clist in challenges.values() for c in clist)
        
        # Find most popular
        most_popular = None
        max_completions = 0
        for clist in challenges.values():
            for c in clist:
                if c.get("completions", 0) > max_completions:
                    max_completions = c.get("completions", 0)
                    most_popular = c["name"]
        
        # Streak leaders
        streaks = guild_data.get("activity_streaks", {})
        top_streak = max(streaks.items(), key=lambda x: x[1].get("current", 0), default=(None, {"current": 0}))
        
        embed = discord.Embed(title="📊 Engagement Statistics", color=discord.Color.blue())
        embed.add_field(name="Total Completions Today", value=total_completions, inline=True)
        embed.add_field(name="Most Popular Challenge", value=most_popular or "N/A", inline=True)
        embed.add_field(name="Top Streak Leader", value=f"<@{top_streak[0]}> ({top_streak[1].get('current', 0)} days)" if top_streak[0] else "None", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="🏆 Season Leaderboard", style=discord.ButtonStyle.primary, custom_id="gam_season_leaderboard")
    async def season_leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        season_data = guild_data.get("season_data", {})
        points = season_data.get("points", {})
        
        sorted_users = sorted(points.items(), key=lambda x: x[1], reverse=True)[:10]
        
        embed = discord.Embed(title="🏆 Season Leaderboard", description="Current monthly tournament rankings", color=discord.Color.gold())
        
        for i, (user_id, pts) in enumerate(sorted_users, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            embed.add_field(name=f"{medal} <@{user_id}>", value=f"{pts} points", inline=True)
        
        if not sorted_users:
            embed.description = "No participants yet!"
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="🎯 Set Prestige Level", style=discord.ButtonStyle.secondary, custom_id="gam_set_prestige")
    async def set_prestige(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PrestigeLevelModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="⚙️ Configure Ranking Titles", style=discord.ButtonStyle.primary, custom_id="gam_config_titles")
    async def config_titles(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RankingTitlesModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🎉 Launch Seasonal Event", style=discord.ButtonStyle.success, custom_id="gam_launch_event")
    async def launch_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = LaunchEventModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🏁 End Seasonal Event", style=discord.ButtonStyle.danger, custom_id="gam_end_event")
    async def end_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        events = guild_data.get("seasonal_events", {})
        
        active_events = [(eid, e) for eid, e in events.items() if e.get("active", False)]
        
        if not active_events:
            await interaction.response.send_message("No active events!", ephemeral=True)
            return
        
        view = discord.ui.View()
        select = discord.ui.Select(placeholder="Select event to end", options=[discord.SelectOption(label=e["name"][:25], value=eid) for eid, e in active_events][:25])
        
        async def select_callback(interaction: discord.Interaction):
            event_id = select.values[0]
            events[event_id]["active"] = False
            events[event_id]["end_time"] = datetime.now().isoformat()
            dm.update_guild_data(self.guild_id, guild_data)
            await interaction.response.send_message(f"✅ Event **{events[event_id]['name']}** ended!", ephemeral=True)
        
        select.callback = select_callback
        view.add_item(select)
        await interaction.response.send_message("Select an event to end:", view=view, ephemeral=True)
    
    @discord.ui.button(label="🎰 Configure Mini-Games", style=discord.ButtonStyle.secondary, custom_id="gam_config_minigames")
    async def config_minigames(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = MinigamesConfigModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="🔢 Toggle Streak System", style=discord.ButtonStyle.primary, custom_id="gam_toggle_streak")
    async def toggle_streak(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        current = guild_data.get("streak_system_enabled", True)
        guild_data["streak_system_enabled"] = not current
        dm.update_guild_data(self.guild_id, guild_data)
        
        status = "✅ Enabled" if not current else "❌ Disabled"
        await interaction.response.send_message(f"🔢 Streak System {status}!", ephemeral=True)
    
    @discord.ui.button(label="📣 Set Leaderboard Channel", style=discord.ButtonStyle.primary, custom_id="gam_set_leaderboard_channel")
    async def set_leaderboard_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View()
        select = discord.ui.Select(placeholder="Select leaderboard channel", options=[discord.SelectOption(label=ch.name[:25], value=str(ch.id)) for ch in interaction.guild.text_channels][:25])
        
        async def select_callback(interaction: discord.Interaction):
            channel_id = int(select.values[0])
            guild_data = dm.get_guild_data(self.guild_id)
            guild_data["leaderboard_channel"] = channel_id
            dm.update_guild_data(self.guild_id, guild_data)
            await interaction.response.send_message(f"✅ Leaderboard channel set to <#{channel_id}>!", ephemeral=True)
        
        select.callback = select_callback
        view.add_item(select)
        await interaction.response.send_message("Select the leaderboard channel:", view=view, ephemeral=True)
    
    @discord.ui.button(label="🔄 Update Leaderboard Now", style=discord.ButtonStyle.secondary, custom_id="gam_update_leaderboard")
    async def update_leaderboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        channel_id = guild_data.get("leaderboard_channel")
        
        if not channel_id:
            await interaction.response.send_message("❌ No leaderboard channel set!", ephemeral=True)
            return
        
        # Get economy data for leaderboard
        economy = guild_data.get("economy", {})
        sorted_users = sorted(economy.items(), key=lambda x: x[1].get("coins", 0), reverse=True)[:10]
        
        embed = discord.Embed(title="🏆 Server Leaderboard", description="Top 10 richest members", color=discord.Color.gold())
        
        for i, (user_id, data) in enumerate(sorted_users, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            embed.add_field(name=f"{medal} <@{user_id}>", value=f"{data.get('coins', 0)} coins", inline=True)
        
        channel = interaction.guild.get_channel(channel_id)
        if channel:
            try:
                # Delete old leaderboard message if exists
                async for msg in channel.history(limit=10):
                    if msg.author == interaction.guild.me and msg.embeds and "Leaderboard" in msg.embeds[0].title:
                        await msg.delete()
                        break
            except:
                pass
            
            await channel.send(embed=embed)
        
        await interaction.response.send_message("✅ Leaderboard updated!", ephemeral=True)
    
    @discord.ui.button(label="🏅 Manage Titles", style=discord.ButtonStyle.primary, custom_id="gam_manage_titles")
    async def manage_titles(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        titles = guild_data.get("ranking_titles", {})
        
        embed = discord.Embed(title="🏅 Ranking Titles", description="Titles assigned based on level ranges", color=discord.Color.blue())
        
        if not titles:
            embed.add_field(name="Default Titles", value="Level 1-9: Newcomer\nLevel 10-24: Regular\nLevel 25-49: Veteran\nLevel 50-99: Elite\nLevel 100+: Legend", inline=False)
        else:
            for level_range, title in sorted(titles.items()):
                embed.add_field(name=level_range, value=title, inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


class CreateChallengeModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Create Custom Challenge")
        self.name = discord.ui.TextInput(label="Name", placeholder="Challenge name", max_length=50)
        self.add_item(self.name)
        self.description = discord.ui.TextInput(label="Description", placeholder="What needs to be done?", max_length=200)
        self.add_item(self.description)
        self.goal_type = discord.ui.TextInput(label="Goal Type", placeholder="messages/coins/reactions/voice_minutes", max_length=30)
        self.add_item(self.goal_type)
        self.goal_value = discord.ui.TextInput(label="Goal Value", placeholder="Target number", max_length=10)
        self.add_item(self.goal_value)
        self.reward_xp = discord.ui.TextInput(label="XP Reward", placeholder="XP given on completion", default="100", max_length=10)
        self.add_item(self.reward_xp)
        self.reward_coins = discord.ui.TextInput(label="Coin Reward", placeholder="Coins given on completion", default="50", max_length=10)
        self.add_item(self.reward_coins)
        self.duration = discord.ui.TextInput(label="Duration (hours)", placeholder="How long is this challenge available?", default="24", max_length=10)
        self.add_item(self.duration)
    
    async def on_submit(self, interaction: discord.Interaction):
        guild_data = dm.get_guild_data(interaction.guild_id)
        
        if "challenges" not in guild_data:
            guild_data["challenges"] = {"custom": []}
        elif "custom" not in guild_data["challenges"]:
            guild_data["challenges"]["custom"] = []
        
        guild_data["challenges"]["custom"].append({
            "name": self.name.value,
            "description": self.description.value,
            "goal_type": self.goal_type.value,
            "goal_value": int(self.goal_value.value),
            "reward_xp": int(self.reward_xp.value),
            "reward_coins": int(self.reward_coins.value),
            "duration_hours": int(self.duration.value),
            "completions": 0,
            "expires": (datetime.now() + timedelta(hours=int(self.duration.value))).isoformat()
        })
        
        dm.update_guild_data(interaction.guild_id, guild_data)
        await interaction.response.send_message(f"✅ Challenge created: **{self.name.value}**!", ephemeral=True)


class PrestigeLevelModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Set Prestige Level")
        self.level = discord.ui.TextInput(label="Prestige Level", placeholder="Level required to prestige", default="100", max_length=10)
        self.add_item(self.level)
    
    async def on_submit(self, interaction: discord.Interaction):
        guild_data = dm.get_guild_data(interaction.guild_id)
        guild_data["prestige_level"] = int(self.level.value)
        dm.update_guild_data(interaction.guild_id, guild_data)
        await interaction.response.send_message(f"✅ Prestige level set to {self.level.value}!", ephemeral=True)


class RankingTitlesModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Configure Ranking Titles")
        self.titles = discord.ui.TextInput(
            label="Titles (format: level_range:title)",
            placeholder="1-9:Newcomer,10-24:Regular,25-49:Veteran,50-99:Elite,100+:Legend",
            default="1-9:Newcomer,10-24:Regular,25-49:Veteran,50-99:Elite,100+:Legend",
            max_length=500
        )
        self.add_item(self.titles)
    
    async def on_submit(self, interaction: discord.Interaction):
        guild_data = dm.get_guild_data(interaction.guild_id)
        titles = {}
        
        for pair in self.titles.value.split(","):
            if ":" in pair:
                range_str, title = pair.split(":", 1)
                titles[range_str.strip()] = title.strip()
        
        guild_data["ranking_titles"] = titles
        dm.update_guild_data(interaction.guild_id, guild_data)
        await interaction.response.send_message("✅ Ranking titles configured!", ephemeral=True)


class LaunchEventModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Launch Seasonal Event")
        self.name = discord.ui.TextInput(label="Event Name", placeholder="Summer Event 2024", max_length=50)
        self.add_item(self.name)
        self.description = discord.ui.TextInput(label="Description", placeholder="What's special about this event?", max_length=200)
        self.add_item(self.description)
        self.xp_multiplier = discord.ui.TextInput(label="XP Multiplier", placeholder="1.5 for 1.5x XP", default="1.5", max_length=10)
        self.add_item(self.xp_multiplier)
        self.duration_days = discord.ui.TextInput(label="Duration (days)", placeholder="How many days?", default="7", max_length=10)
        self.add_item(self.duration_days)
        self.achievement_name = discord.ui.TextInput(label="Exclusive Achievement", placeholder="Name of exclusive achievement", max_length=50)
        self.add_item(self.achievement_name)
    
    async def on_submit(self, interaction: discord.Interaction):
        guild_data = dm.get_guild_data(interaction.guild_id)
        
        if "seasonal_events" not in guild_data:
            guild_data["seasonal_events"] = {}
        
        event_id = f"event_{datetime.now().timestamp()}"
        guild_data["seasonal_events"][event_id] = {
            "name": self.name.value,
            "description": self.description.value,
            "xp_multiplier": float(self.xp_multiplier.value),
            "start_time": datetime.now().isoformat(),
            "end_time": (datetime.now() + timedelta(days=int(self.duration_days.value))).isoformat(),
            "active": True,
            "exclusive_achievement": self.achievement_name.value
        }
        
        dm.update_guild_data(interaction.guild_id, guild_data)
        await interaction.response.send_message(f"🎉 Event **{self.name.value}** launched! Ends in {self.duration_days.value} days!", ephemeral=True)


class MinigamesConfigModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Configure Mini-Games")
        self.min_payout = discord.ui.TextInput(label="Min Payout", placeholder="Minimum coins won", default="10", max_length=10)
        self.add_item(self.min_payout)
        self.max_payout = discord.ui.TextInput(label="Max Payout", placeholder="Maximum coins won", default="100", max_length=10)
        self.add_item(self.max_payout)
        self.cooldown = discord.ui.TextInput(label="Cooldown (seconds)", placeholder="Seconds between plays", default="30", max_length=10)
        self.add_item(self.cooldown)
    
    async def on_submit(self, interaction: discord.Interaction):
        guild_data = dm.get_guild_data(interaction.guild_id)
        guild_data["minigames_config"] = {
            "min_payout": int(self.min_payout.value),
            "max_payout": int(self.max_payout.value),
            "cooldown_seconds": int(self.cooldown.value)
        }
        dm.update_guild_data(interaction.guild_id, guild_data)
        await interaction.response.send_message("✅ Mini-games configured!", ephemeral=True)


def setup_gamification_commands(bot):
    @bot.command(name="gamificationpanel")
    async def gamification_panel(ctx):
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Administrator permissions required!")
            return
        
        embed = discord.Embed(title="🎮 Gamification Management Panel", description="Manage challenges, events, and engagement features", color=discord.Color.blue())
        view = GamificationPanel(ctx.guild.id)
        await ctx.send(embed=embed, view=view)
