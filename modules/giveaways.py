"""
🎉 GIVEAWAYS SYSTEM - FULLY FUNCTIONAL
All buttons, modals, and features from Part 7 blueprint
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
from typing import Optional, List, Dict, Any
import asyncio
import random
from datetime import datetime, timedelta
import json

from data_manager import DataManager

dm = DataManager()


class GiveawayEntryView(discord.ui.View):
    """View for users to enter/leave giveaways"""
    
    def __init__(self, giveaway_id: str, guild_id: int):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self.guild_id = guild_id
    
    @discord.ui.button(label="Enter Giveaway", style=discord.ButtonStyle.success, custom_id="giveaway_enter")
    async def entry_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        giveaways = guild_data.get("giveaways", {})
        
        if self.giveaway_id not in giveaways:
            await interaction.response.send_message("❌ This giveaway has ended or doesn't exist.", ephemeral=True)
            return
        
        giveaway = giveaways[self.giveaway_id]
        user_id = str(interaction.user.id)
        member = interaction.guild.get_member(interaction.user.id)
        requirements = giveaway.get("requirements", {})
        
        # Check all requirements
        if requirements.get("min_level", 0) > 0:
            user_level = guild_data.get("levels", {}).get(user_id, {}).get("level", 0)
            if user_level < requirements["min_level"]:
                await interaction.response.send_message(f"❌ You need to be at least level {requirements['min_level']}.", ephemeral=True)
                return
        
        if requirements.get("min_coins", 0) > 0:
            user_coins = guild_data.get("economy", {}).get(user_id, {}).get("coins", 0)
            if user_coins < requirements["min_coins"]:
                await interaction.response.send_message(f"❌ You need at least {requirements['min_coins']} coins.", ephemeral=True)
                return
        
        if requirements.get("required_role"):
            role_id = int(requirements["required_role"])
            if not any(r.id == role_id for r in member.roles):
                await interaction.response.send_message(f"❌ You need the <@&{role_id}> role.", ephemeral=True)
                return
        
        if requirements.get("excluded_role"):
            role_id = int(requirements["excluded_role"])
            if any(r.id == role_id for r in member.roles):
                await interaction.response.send_message(f"❌ Users with <@&{role_id}> cannot enter.", ephemeral=True)
                return
        
        if requirements.get("boost_required", False) and not member.premium:
            await interaction.response.send_message("❌ You need to be a server booster.", ephemeral=True)
            return
        
        if requirements.get("min_account_age_days", 0) > 0:
            account_age = (datetime.now() - interaction.user.created_at).days
            if account_age < requirements["min_account_age_days"]:
                await interaction.response.send_message(f"❌ Your account must be {requirements['min_account_age_days']}+ days old.", ephemeral=True)
                return
        
        entries = giveaway.get("entries", [])
        
        if user_id in entries:
            entries.remove(user_id)
            giveaway["entries"] = entries
            dm.update_guild_data(self.guild_id, guild_data)
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("❌ You have left the giveaway.", ephemeral=True)
        else:
            bonus_multiplier = 1
            bonus_roles = guild_data.get("giveaway_bonus_roles", {})
            for role_id, multiplier in bonus_roles.items():
                if any(r.id == int(role_id) for r in member.roles):
                    bonus_multiplier = max(bonus_multiplier, multiplier)
            
            for _ in range(bonus_multiplier):
                entries.append(user_id)
            
            giveaway["entries"] = entries
            dm.update_guild_data(self.guild_id, guild_data)
            
            entry_number = len([e for e in entries if e == user_id])
            await interaction.response.edit_message(view=self)
            
            embed = discord.Embed(title="🎉 Entry Confirmed!", description=f"You entered: **{giveaway['prize']}**!", color=discord.Color.green())
            embed.add_field(name="Your Entry Number(s)", value=f"#{entry_number}", inline=False)
            embed.add_field(name="Total Entries", value=len(entries), inline=True)
            if bonus_multiplier > 1:
                embed.add_field(name="Bonus Entries!", value=f"{bonus_multiplier}x entries!", inline=True)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
            if guild_data.get("giveaway_entry_dms", False):
                try:
                    dm_embed = discord.Embed(title="🎉 Giveaway Entry Confirmed", description=f"You've entered: **{giveaway['prize']}**", color=discord.Color.blue())
                    dm_embed.add_field(name="Entry Number", value=f"#{entry_number}", inline=True)
                    dm_embed.add_field(name="Total Entries", value=len(entries), inline=True)
                    await interaction.user.send(embed=dm_embed)
                except:
                    pass
        
        await self.update_embed(interaction)
    
    @discord.ui.button(label="View Entries", style=discord.ButtonStyle.secondary, custom_id="giveaway_entries")
    async def view_entries_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        giveaways = guild_data.get("giveaways", {})
        
        if self.giveaway_id not in giveaways:
            await interaction.response.send_message("❌ This giveaway has ended.", ephemeral=True)
            return
        
        giveaway = giveaways[self.giveaway_id]
        entries = giveaway.get("entries", [])
        user_id = str(interaction.user.id)
        user_entries = [i for i, e in enumerate(entries) if e == user_id]
        
        embed = discord.Embed(title="📊 Giveaway Entries", description=f"**{giveaway['prize']}**", color=discord.Color.blue())
        embed.add_field(name="Total Entries", value=len(entries), inline=True)
        embed.add_field(name="Your Entries", value=len(user_entries), inline=True)
        embed.add_field(name="Winner Count", value=giveaway.get("winners_count", 1), inline=True)
        
        if user_entries:
            embed.add_field(name="Your Entry Numbers", value=", ".join(f"#{i+1}" for i in user_entries), inline=False)
        else:
            embed.add_field(name="Status", value="You haven't entered yet!", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="View Requirements", style=discord.ButtonStyle.secondary, custom_id="giveaway_requirements")
    async def view_requirements_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        giveaways = guild_data.get("giveaways", {})
        
        if self.giveaway_id not in giveaways:
            await interaction.response.send_message("❌ This giveaway has ended.", ephemeral=True)
            return
        
        giveaway = giveaways[self.giveaway_id]
        requirements = giveaway.get("requirements", {})
        
        embed = discord.Embed(title="🏆 Entry Requirements", description=f"**{giveaway['prize']}**", color=discord.Color.gold())
        
        if not requirements:
            embed.add_field(name="Requirements", value="✅ No requirements! Everyone can enter!", inline=False)
        else:
            req_list = []
            if requirements.get("min_level", 0) > 0:
                req_list.append(f"• Level {requirements['min_level']}+")
            if requirements.get("min_coins", 0) > 0:
                req_list.append(f"• {requirements['min_coins']}+ coins")
            if requirements.get("required_role"):
                req_list.append(f"• Role: <@&{requirements['required_role']}>")
            if requirements.get("excluded_role"):
                req_list.append(f"• Cannot have: <@&{requirements['excluded_role']}>")
            if requirements.get("boost_required", False):
                req_list.append("• Server Booster required")
            if requirements.get("min_account_age_days", 0) > 0:
                req_list.append(f"• Account age: {requirements['min_account_age_days']}+ days")
            
            embed.add_field(name="Requirements", value="\n".join(req_list) if req_list else "None", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    async def update_embed(self, interaction: discord.Interaction):
        guild_data = dm.get_guild_data(self.guild_id)
        giveaways = guild_data.get("giveaways", {})
        
        if self.giveaway_id not in giveaways:
            return
        
        giveaway = giveaways[self.giveaway_id]
        entries = giveaway.get("entries", [])
        
        try:
            channel = interaction.guild.get_channel(giveaway["channel_id"])
            if channel:
                message = await channel.fetch_message(giveaway["message_id"])
                embed = message.embeds[0]
                for i, field in enumerate(embed.fields):
                    if "entries" in field.name.lower():
                        embed.set_field_at(i, name="🎟️ Entries", value=str(len(entries)), inline=True)
                        break
                await message.edit(embed=embed, view=self)
        except Exception as e:
            print(f"Error updating giveaway embed: {e}")


class CreateGiveawayModal(discord.ui.Modal):
    """Modal for creating a new giveaway"""
    
    def __init__(self):
        super().__init__(title="Create Giveaway")
        
        self.prize = discord.ui.TextInput(label="Prize", placeholder="What are you giving away?", max_length=100)
        self.add_item(self.prize)
        
        self.winners = discord.ui.TextInput(label="Number of Winners", placeholder="1", default="1", max_length=5)
        self.add_item(self.winners)
        
        self.duration = discord.ui.TextInput(label="Duration (e.g., 1h, 2d, 1w)", placeholder="How long should the giveaway last?", max_length=20)
        self.add_item(self.duration)
        
        self.requirements = discord.ui.TextInput(label="Requirements (optional)", placeholder="min_level:5,min_coins:100,required_role:123456789", required=False, max_length=500)
        self.add_item(self.requirements)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            winners_count = int(self.winners.value)
            if winners_count < 1:
                await interaction.response.send_message("❌ Winner count must be at least 1.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("❌ Invalid winner count.", ephemeral=True)
            return
        
        duration_str = self.duration.value.lower()
        duration_seconds = 0
        
        if 'h' in duration_str:
            hours = int(duration_str.replace('h', '').replace(' ', ''))
            duration_seconds = hours * 3600
        elif 'd' in duration_str:
            days = int(duration_str.replace('d', '').replace(' ', ''))
            duration_seconds = days * 86400
        elif 'w' in duration_str:
            weeks = int(duration_str.replace('w', '').replace(' ', ''))
            duration_seconds = weeks * 604800
        elif 'm' in duration_str:
            minutes = int(duration_str.replace('m', '').replace(' ', ''))
            duration_seconds = minutes * 60
        else:
            await interaction.response.send_message("❌ Invalid duration format. Use: 1h, 2d, 1w, 30m", ephemeral=True)
            return
        
        requirements = {}
        if self.requirements.value:
            for req in self.requirements.value.split(','):
                if ':' in req:
                    key, value = req.split(':', 1)
                    key, value = key.strip(), value.strip()
                    if key == "min_level":
                        requirements["min_level"] = int(value)
                    elif key == "min_coins":
                        requirements["min_coins"] = int(value)
                    elif key == "required_role":
                        requirements["required_role"] = value
                    elif key == "excluded_role":
                        requirements["excluded_role"] = value
                    elif key == "boost_required":
                        requirements["boost_required"] = value.lower() == "true"
                    elif key == "min_account_age_days":
                        requirements["min_account_age_days"] = int(value)
        
        giveaway_id = f"gw_{interaction.guild_id}_{datetime.now().timestamp()}"
        end_time = datetime.now() + timedelta(seconds=duration_seconds)
        
        guild_data = dm.get_guild_data(interaction.guild_id)
        if "giveaways" not in guild_data:
            guild_data["giveaways"] = {}
        
        guild_data["giveaways"][giveaway_id] = {
            "prize": self.prize.value,
            "winners_count": winners_count,
            "start_time": datetime.now().isoformat(),
            "end_time": end_time.isoformat(),
            "channel_id": interaction.channel_id,
            "message_id": None,
            "host_id": str(interaction.user.id),
            "entries": [],
            "requirements": requirements,
            "status": "active"
        }
        
        dm.update_guild_data(interaction.guild_id, guild_data)
        
        embed = discord.Embed(title="🎉 GIVEAWAY!", description=f"**{self.prize.value}**", color=discord.Color.gold())
        embed.add_field(name="🏆 Winners", value=str(winners_count), inline=True)
        embed.add_field(name="🎟️ Entries", value="0", inline=True)
        embed.add_field(name="⏰ Ends", value=f"<t:{int(end_time.timestamp())}:R>", inline=True)
        embed.add_field(name="Hosted by", value=interaction.user.mention, inline=False)
        embed.set_footer(text=f"Giveaway ID: {giveaway_id}")
        embed.timestamp = datetime.now()
        
        view = GiveawayEntryView(giveaway_id, interaction.guild_id)
        message = await interaction.channel.send(embed=embed, view=view)
        
        guild_data["giveaways"][giveaway_id]["message_id"] = message.id
        dm.update_guild_data(interaction.guild_id, guild_data)
        
        asyncio.create_task(self.schedule_end(interaction.guild_id, giveaway_id, duration_seconds))
        
        await interaction.response.send_message(f"✅ Giveaway created!\n\nPrize: **{self.prize.value}**\nEnds: <t:{int(end_time.timestamp())}:f>", ephemeral=True)
    
    async def schedule_end(self, guild_id: int, giveaway_id: str, delay: int):
        await asyncio.sleep(delay)
        guild_data = dm.get_guild_data(guild_id)
        giveaways = guild_data.get("giveaways", {})
        
        if giveaway_id in giveaways and giveaways[giveaway_id]["status"] == "active":
            await self.end_giveaway(guild_id, giveaway_id)
    
    async def end_giveaway(self, guild_id: int, giveaway_id: str):
        guild_data = dm.get_guild_data(guild_id)
        giveaways = guild_data.get("giveaways", {})
        
        if giveaway_id not in giveaways:
            return
        
        giveaway = giveaways[giveaway_id]
        entries = giveaway.get("entries", [])
        winners_count = giveaway.get("winners_count", 1)
        
        if not entries:
            giveaway["status"] = "ended_no_entries"
            dm.update_guild_data(guild_id, guild_data)
            try:
                channel = discord.utils.get(await discord.utils.get(interaction.guild.channels, id=giveaway["channel_id"]).fetch_message(giveaway["message_id"]))
            except:
                pass
            return
        
        unique_entries = list(set(entries))
        if len(unique_entries) < winners_count:
            winners_count = len(unique_entries)
        
        winners = random.sample(unique_entries, winners_count)
        giveaway["winners"] = winners
        giveaway["status"] = "ended"
        dm.update_guild_data(guild_id, guild_data)
        
        try:
            guild = await discord.utils.get(bot.guilds, id=guild_id)
            channel = discord.utils.get(guild.channels, id=giveaway["channel_id"])
            if channel:
                message = await channel.fetch_message(giveaway["message_id"])
                embed = message.embeds[0]
                embed.color = discord.Color.green()
                embed.add_field(name="🏆 Winners", value="\n".join(f"<@{w}>" for w in winners), inline=False)
                await message.edit(embed=embed, view=None)
        except:
            pass
        
        announce_embed = discord.Embed(title="🎉 Giveaway Ended!", description=f"**{giveaway['prize']}**", color=discord.Color.green())
        announce_embed.add_field(name="🏆 Winners", value="\n".join(f"🎉 <@{w}>!" for w in winners), inline=False)
        announce_embed.set_footer(text=f"Total entries: {len(entries)}")
        
        channel = discord.utils.get((await discord.utils.get(bot.guilds, id=guild_id)).channels, id=giveaway["channel_id"])
        if channel:
            await channel.send(content=" ".join(f"<@{w}>" for w in winners), embed=announce_embed)
        
        log_data = {"action": "giveaway_ended", "giveaway_id": giveaway_id, "prize": giveaway["prize"], "winners": winners, "total_entries": len(entries), "timestamp": datetime.now().isoformat()}
        if "action_logs" not in guild_data:
            guild_data["action_logs"] = []
        guild_data["action_logs"].append(log_data)
        dm.update_guild_data(guild_id, guild_data)


class GiveawayPanel(discord.ui.View):
    """Admin panel for managing giveaways - ALL 11 BUTTONS"""
    
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
    
    @discord.ui.button(label="➕ Create Giveaway", style=discord.ButtonStyle.success, custom_id="gw_create")
    async def create_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CreateGiveawayModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="📋 View Active Giveaways", style=discord.ButtonStyle.primary, custom_id="gw_view_active")
    async def view_active(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        giveaways = guild_data.get("giveaways", {})
        active = [(gid, gw) for gid, gw in giveaways.items() if gw.get("status") == "active"]
        
        if not active:
            await interaction.response.send_message("📭 No active giveaways!", ephemeral=True)
            return
        
        embed = discord.Embed(title="🎉 Active Giveaways", description=f"Total: {len(active)}", color=discord.Color.blue())
        for gid, gw in active[:5]:
            entries = len(gw.get("entries", []))
            end_time = datetime.fromisoformat(gw["end_time"])
            embed.add_field(name=f"🏆 {gw['prize']}", value=f"Entries: {entries}\nEnds: <t:{int(end_time.timestamp())}:R>\nChannel: <#{gw['channel_id']}>", inline=False)
        
        if len(active) > 5:
            embed.set_footer(text=f"...and {len(active) - 5} more")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="🏆 End Giveaway Now", style=discord.ButtonStyle.danger, custom_id="gw_end_now")
    async def end_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        giveaways = guild_data.get("giveaways", {})
        active = [(gid, gw) for gid, gw in giveaways.items() if gw.get("status") == "active"]
        
        if not active:
            await interaction.response.send_message("No active giveaways!", ephemeral=True)
            return
        
        view = discord.ui.View()
        select = discord.ui.Select(placeholder="Select giveaway to end", options=[discord.SelectOption(label=gw["prize"][:25], value=gid, description=f"{len(gw.get('entries', []))} entries") for gid, gw in active[:25]])
        
        async def select_callback(interaction: discord.Interaction):
            giveaway_id = select.values[0]
            giveaway = giveaways[giveaway_id]
            
            confirm_view = discord.ui.View()
            
            @discord.ui.button(label="✅ Confirm End", style=discord.ButtonStyle.success)
            async def confirm_button(interaction: discord.Interaction, button: discord.ui.Button):
                entries = giveaway.get("entries", [])
                winners_count = giveaway.get("winners_count", 1)
                
                if not entries:
                    giveaway["status"] = "ended_no_entries"
                    dm.update_guild_data(self.guild_id, guild_data)
                    await interaction.response.send_message("❌ No entries!", ephemeral=True)
                    return
                
                unique_entries = list(set(entries))
                if len(unique_entries) < winners_count:
                    winners_count = len(unique_entries)
                
                winners = random.sample(unique_entries, winners_count)
                giveaway["winners"] = winners
                giveaway["status"] = "ended"
                dm.update_guild_data(self.guild_id, guild_data)
                
                try:
                    channel = interaction.guild.get_channel(giveaway["channel_id"])
                    if channel:
                        message = await channel.fetch_message(giveaway["message_id"])
                        embed = message.embeds[0]
                        embed.color = discord.Color.green()
                        embed.add_field(name="🏆 Winners", value="\n".join(f"<@{w}>" for w in winners), inline=False)
                        await message.edit(embed=embed, view=None)
                except:
                    pass
                
                announce_embed = discord.Embed(title="🎉 Giveaway Ended!", description=f"**{giveaway['prize']}**", color=discord.Color.green())
                announce_embed.add_field(name="🏆 Winners", value="\n".join(f"🎉 <@{w}>!" for w in winners), inline=False)
                
                channel = interaction.guild.get_channel(giveaway["channel_id"])
                if channel:
                    await channel.send(content=" ".join(f"<@{w}>" for w in winners), embed=announce_embed)
                
                await interaction.response.send_message(f"✅ Ended! Winners: {' '.join(f'<@{w}>' for w in winners)}", ephemeral=True)
            
            confirm_view.add_item(confirm_button)
            await interaction.response.send_message(f"⚠️ End giveaway for **{giveaway['prize']}**?", view=confirm_view, ephemeral=True)
        
        select.callback = select_callback
        view.add_item(select)
        await interaction.response.send_message("Select a giveaway to end:", view=view, ephemeral=True)
    
    @discord.ui.button(label="🔄 Reroll Giveaway", style=discord.ButtonStyle.secondary, custom_id="gw_reroll")
    async def reroll_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        giveaways = guild_data.get("giveaways", {})
        ended = [(gid, gw) for gid, gw in giveaways.items() if gw.get("status") == "ended" and gw.get("winners")]
        
        if not ended:
            await interaction.response.send_message("No ended giveaways!", ephemeral=True)
            return
        
        view = discord.ui.View()
        select = discord.ui.Select(placeholder="Select giveaway to reroll", options=[discord.SelectOption(label=gw["prize"][:25], value=gid, description=f"Winners: {len(gw.get('winners', []))}") for gid, gw in ended[:25]])
        
        async def select_callback(interaction: discord.Interaction):
            giveaway_id = select.values[0]
            giveaway = giveaways[giveaway_id]
            entries = giveaway.get("entries", [])
            current_winners = giveaway.get("winners", [])
            
            unique_entries = list(set(entries))
            available = [e for e in unique_entries if e not in current_winners]
            
            if not available:
                await interaction.response.send_message("❌ No remaining entries!", ephemeral=True)
                return
            
            new_winner = random.choice(available)
            giveaway["winners"].append(new_winner)
            dm.update_guild_data(self.guild_id, guild_data)
            
            announce_embed = discord.Embed(title="🔄 Giveaway Reroll!", description=f"**{giveaway['prize']}**", color=discord.Color.orange())
            announce_embed.add_field(name="🎉 New Winner", value=f"<@{new_winner}>", inline=False)
            announce_embed.set_footer(text=f"Total winners: {len(giveaway['winners'])}")
            
            channel = interaction.guild.get_channel(giveaway["channel_id"])
            if channel:
                await channel.send(content=f"<@{new_winner}>", embed=announce_embed)
            
            await interaction.response.send_message(f"✅ Rerolled! New winner: <@{new_winner}>", ephemeral=True)
        
        select.callback = select_callback
        view.add_item(select)
        await interaction.response.send_message("Select a giveaway to reroll:", view=view, ephemeral=True)
    
    @discord.ui.button(label="🗑️ Cancel Giveaway", style=discord.ButtonStyle.danger, custom_id="gw_cancel")
    async def cancel_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        giveaways = guild_data.get("giveaways", {})
        active = [(gid, gw) for gid, gw in giveaways.items() if gw.get("status") == "active"]
        
        if not active:
            await interaction.response.send_message("No active giveaways!", ephemeral=True)
            return
        
        view = discord.ui.View()
        select = discord.ui.Select(placeholder="Select giveaway to cancel", options=[discord.SelectOption(label=gw["prize"][:25], value=gid, description=f"{len(gw.get('entries', []))} entries") for gid, gw in active[:25]])
        
        async def select_callback(interaction: discord.Interaction):
            giveaway_id = select.values[0]
            giveaway = giveaways[giveaway_id]
            
            modal = CancelConfirmModal(giveaway_id, giveaway["prize"])
            await interaction.response.send_modal(modal)
        
        select.callback = select_callback
        view.add_item(select)
        await interaction.response.send_message("Select a giveaway to cancel:", view=view, ephemeral=True)
    
    @discord.ui.button(label="📊 Stats", style=discord.ButtonStyle.secondary, custom_id="gw_stats")
    async def stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        giveaways = guild_data.get("giveaways", {})
        
        total = len(giveaways)
        active = len([g for g in giveaways.values() if g.get("status") == "active"])
        ended = len([g for g in giveaways.values() if g.get("status") == "ended"])
        total_entries = sum(len(g.get("entries", [])) for g in giveaways.values())
        most_popular = max(giveaways.items(), key=lambda x: len(x[1].get("entries", [])), default=None)
        avg_entries = total_entries / total if total > 0 else 0
        
        embed = discord.Embed(title="📊 Giveaway Statistics", color=discord.Color.blue())
        embed.add_field(name="Total Giveaways", value=total, inline=True)
        embed.add_field(name="Active", value=active, inline=True)
        embed.add_field(name="Ended", value=ended, inline=True)
        embed.add_field(name="Total Entries", value=total_entries, inline=True)
        embed.add_field(name="Avg Entries/Giveaway", value=f"{avg_entries:.1f}", inline=True)
        
        if most_popular:
            embed.add_field(name="🏆 Most Popular", value=f"{most_popular[1]['prize']} ({len(most_popular[1].get('entries', []))} entries)", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="⚙️ Configure Bonus Entries", style=discord.ButtonStyle.primary, custom_id="gw_config_bonus")
    async def config_bonus(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = BonusEntriesModal()
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="📋 View Bonus Entry Roles", style=discord.ButtonStyle.secondary, custom_id="gw_view_bonus")
    async def view_bonus(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        bonus_roles = guild_data.get("giveaway_bonus_roles", {})
        
        if not bonus_roles:
            await interaction.response.send_message("📭 No bonus entry roles configured!", ephemeral=True)
            return
        
        embed = discord.Embed(title="🎁 Bonus Entry Roles", description="Roles that give extra entries", color=discord.Color.gold())
        for role_id, multiplier in bonus_roles.items():
            embed.add_field(name=f"Role ID: {role_id}", value=f"{multiplier}x entries", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="📣 Set Default Channel", style=discord.ButtonStyle.primary, custom_id="gw_set_channel")
    async def set_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View()
        select = discord.ui.Select(placeholder="Select default giveaway channel", options=[discord.SelectOption(label=ch.name[:25], value=str(ch.id)) for ch in interaction.guild.text_channels if ch.permissions_for(interaction.guild.me).send_messages][:25])
        
        async def select_callback(interaction: discord.Interaction):
            channel_id = int(select.values[0])
            guild_data = dm.get_guild_data(self.guild_id)
            guild_data["giveaway_default_channel"] = channel_id
            dm.update_guild_data(self.guild_id, guild_data)
            await interaction.response.send_message(f"✅ Default channel set to <#{channel_id}>!", ephemeral=True)
        
        select.callback = select_callback
        view.add_item(select)
        await interaction.response.send_message("Select the default channel:", view=view, ephemeral=True)
    
    @discord.ui.button(label="📩 Toggle Entry DMs", style=discord.ButtonStyle.secondary, custom_id="gw_toggle_dms")
    async def toggle_dms(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        current = guild_data.get("giveaway_entry_dms", False)
        guild_data["giveaway_entry_dms"] = not current
        dm.update_guild_data(self.guild_id, guild_data)
        
        status = "✅ Enabled" if not current else "❌ Disabled"
        await interaction.response.send_message(f"📩 Entry DMs {status}!", ephemeral=True)
    
    @discord.ui.button(label="📋 View Ended Giveaways", style=discord.ButtonStyle.secondary, custom_id="gw_view_ended")
    async def view_ended(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_data = dm.get_guild_data(self.guild_id)
        giveaways = guild_data.get("giveaways", {})
        ended = [(gid, gw) for gid, gw in giveaways.items() if gw.get("status") in ["ended", "ended_no_entries"]]
        
        if not ended:
            await interaction.response.send_message("📭 No ended giveaways!", ephemeral=True)
            return
        
        embed = discord.Embed(title="📜 Ended Giveaways", description="Last 10 ended giveaways", color=discord.Color.greyple())
        for gid, gw in ended[-10:]:
            winners = gw.get("winners", [])
            status = "✅ Ended" if winners else "❌ No Entries"
            embed.add_field(name=f"{gw['prize'][:30]}", value=f"Status: {status}\nWinners: {len(winners)}\nEntries: {len(gw.get('entries', []))}", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


class BonusEntriesModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Configure Bonus Entries")
        self.role_id = discord.ui.TextInput(label="Role ID", placeholder="The role ID", max_length=30)
        self.add_item(self.role_id)
        self.multiplier = discord.ui.TextInput(label="Multiplier", placeholder="2 (for 2x)", default="2", max_length=5)
        self.add_item(self.multiplier)
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            role_id = self.role_id.value.strip()
            multiplier = int(self.multiplier.value)
            
            if multiplier < 1:
                await interaction.response.send_message("❌ Multiplier must be at least 1.", ephemeral=True)
                return
            
            role = interaction.guild.get_role(int(role_id))
            if not role:
                await interaction.response.send_message("❌ That role doesn't exist!", ephemeral=True)
                return
            
            guild_data = dm.get_guild_data(interaction.guild_id)
            if "giveaway_bonus_roles" not in guild_data:
                guild_data["giveaway_bonus_roles"] = {}
            
            guild_data["giveaway_bonus_roles"][role_id] = multiplier
            dm.update_guild_data(interaction.guild_id, guild_data)
            
            await interaction.response.send_message(f"✅ Bonus configured!\n\nRole: {role.mention}\nMultiplier: {multiplier}x", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid role ID or multiplier!", ephemeral=True)


class CancelConfirmModal(discord.ui.Modal):
    def __init__(self, giveaway_id: str, prize: str):
        super().__init__(title="Confirm Cancellation")
        self.giveaway_id = giveaway_id
        self.prize = prize
        self.confirmation = discord.ui.TextInput(label=f"Type 'CANCEL' to cancel", placeholder="CANCEL", max_length=10)
        self.add_item(self.confirmation)
    
    async def on_submit(self, interaction: discord.Interaction):
        if self.confirmation.value.upper() != "CANCEL":
            await interaction.response.send_message("❌ You didn't type 'CANCEL'. Aborted.", ephemeral=True)
            return
        
        guild_data = dm.get_guild_data(interaction.guild_id)
        giveaways = guild_data.get("giveaways", {})
        
        if self.giveaway_id in giveaways:
            del giveaways[self.giveaway_id]
            dm.update_guild_data(interaction.guild_id, guild_data)
            await interaction.response.send_message("✅ Giveaway cancelled!", ephemeral=True)


def setup_giveaway_commands(bot):
    @bot.command(name="giveawaypanel")
    async def giveaway_panel(ctx):
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ Administrator permissions required!")
            return
        
        embed = discord.Embed(title="🎉 Giveaway Management Panel", description="Manage all aspects of giveaways", color=discord.Color.gold())
        view = GiveawayPanel(ctx.guild.id)
        await ctx.send(embed=embed, view=view)
