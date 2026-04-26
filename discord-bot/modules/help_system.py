"""
help_system.py — Rich interactive !help command for Miro Bot.
Provides category buttons, per-system status, and a search modal.
"""

import discord
from discord import ui, Interaction
from data_manager import dm
import time

# ─────────────────────────── Colour palette ─────────────────────────────────
BRAND      = 0x5865F2   # Discord Blurple
GREEN      = 0x2ECC71
RED        = 0xE74C3C
GOLD       = 0xF1C40F
PURPLE     = 0x9B59B6
TEAL       = 0x1ABC9C
ORANGE     = 0xE67E22
DARK_BLUE  = 0x2C3E50

# ─────────────────────────── System catalogue ───────────────────────────────
CATEGORIES = {
    "🛡️ Security": {
        "color": RED,
        "systems": [
            ("verification",   "🛡️", "Captcha / phone gate for new members"),
            ("antiraid",       "🚨", "Mass-join detection & server lockdown"),
            ("guardian",       "⚔️", "AI threat detection (scam, token, nuke)"),
            ("automod",        "🤖", "Spam, caps, invite & link filters"),
            ("warnings",       "⚠️", "Warning system with escalation"),
            ("moderation",     "🔨", "Kick, ban, mute, timeout commands"),
            ("modlog",         "📋", "Audit log for all mod actions"),
            ("logging",        "📊", "Full server event logging"),
        ]
    },
    "💰 Economy": {
        "color": GOLD,
        "systems": [
            ("economy",        "💵", "Balance, daily, work, rob & transfer"),
            ("economyshop",    "🛒", "Item shop with role rewards"),
            ("leveling",       "🆙", "XP per message with role rewards"),
            ("levelingshop",   "🎁", "Spend XP on perks & roles"),
            ("starboard",      "⭐", "Star highlight board with rewards"),
        ]
    },
    "🎮 Gamification": {
        "color": PURPLE,
        "systems": [
            ("gamification",   "🎮", "Prestige, quests, skill trees"),
            ("giveaways",      "🎉", "Timed giveaways with role requirements"),
            ("events",         "📅", "Server event scheduling & RSVP"),
            ("tournaments",    "🏆", "Bracket-style tournament management"),
        ]
    },
    "📋 Staff": {
        "color": TEAL,
        "systems": [
            ("staffpromo",     "📈", "Auto staff promotion tiers"),
            ("staffreviews",   "📝", "Peer & admin review cycles"),
            ("staffshifts",    "🕒", "On-duty shift tracker & hours log"),
            ("staffsystem",    "👮", "Staff hierarchy & management"),
        ]
    },
    "🎫 Support": {
        "color": ORANGE,
        "systems": [
            ("tickets",        "🎫", "Support ticket system"),
            ("modmail",        "📬", "Private DM-based modmail"),
            ("appeals",        "⚖️", "Ban / mute appeal system"),
            ("applications",   "📋", "Staff application forms"),
            ("suggestions",    "💡", "Community suggestion board"),
        ]
    },
    "📣 Communication": {
        "color": BRAND,
        "systems": [
            ("welcome",        "👋", "Welcome & leave messages"),
            ("welcomedm",      "✉️", "Direct-message on join"),
            ("chatchannels",   "🧠", "AI-powered chat channels"),
            ("autoresponder",  "💬", "Keyword auto-responses"),
            ("reminders",      "⏰", "Personal & server reminders"),
            ("announcements",  "📢", "Scheduled announcements"),
        ]
    },
    "🔧 Configuration": {
        "color": DARK_BLUE,
        "systems": [
            ("reactionroles",  "🎭", "Emoji-based role assignment"),
            ("reactionmenus",  "📌", "Role picker menus"),
            ("rolebuttons",    "🔘", "Button-based role panels"),
            ("automod",        "🛡️", "AutoMod rule management"),
            ("voicesystem",    "🔊", "Voice channel management"),
        ]
    },
}

# Flat lookup: system_key → (emoji, description, category_name)
_SYSTEM_LOOKUP = {}
for _cat, _data in CATEGORIES.items():
    for _sys, _emoji, _desc in _data["systems"]:
        _SYSTEM_LOOKUP[_sys] = (_emoji, _desc, _cat)


def _status_emoji(guild_id: int, system_key: str) -> str:
    """Return ✅ / ❌ based on whether the system is enabled in guild config."""
    try:
        cfg = dm.get_guild_data(guild_id, f"{system_key}_config", {})
        enabled = cfg.get("enabled", True)
        return "✅" if enabled else "❌"
    except Exception:
        return "⬜"


def _build_main_embed(guild_id: int, bot: discord.Client = None) -> discord.Embed:
    embed = discord.Embed(
        title="📖 Miro Bot — Command Reference",
        description=(
            "**Select a category** below to explore all systems and their commands.\n"
            "Each system can be configured with `!configpanel<system>`.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━"
        ),
        color=BRAND
    )

    guild = bot.get_guild(guild_id) if bot else None
    if guild and guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    else:
        embed.set_thumbnail(url="https://cdn.discordapp.com/embed/avatars/0.png")

    for cat_name, cat_data in CATEGORIES.items():
        systems = cat_data["systems"]
        # Show first 3 systems as a taster
        preview = " · ".join(f"{e} {s.replace('_',' ').title()}" for s, e, _ in systems[:4])
        if len(systems) > 4:
            preview += f" + {len(systems)-4} more"
        embed.add_field(name=cat_name, value=preview, inline=False)

    embed.set_footer(text="Miro Bot • Use !configpanel <system> to configure any system")
    embed.timestamp = discord.utils.utcnow()
    return embed


def _build_category_embed(guild_id: int, cat_name: str) -> discord.Embed:
    cat_data = CATEGORIES[cat_name]
    embed = discord.Embed(
        title=f"{cat_name} — Systems",
        description="Use `!configpanel <system>` to open a system's configuration panel.\n━━━━━━━━━━━━━━━━━━━━━━━━━━",
        color=cat_data["color"]
    )

    for sys_key, emoji, desc in cat_data["systems"]:
        status = _status_emoji(guild_id, sys_key)
        panel_cmd = f"`!configpanel {sys_key}`"
        embed.add_field(
            name=f"{emoji} {sys_key.replace('_',' ').title()}  {status}",
            value=f"{desc}\n{panel_cmd}",
            inline=True
        )

    embed.set_footer(text=f"Category: {cat_name} • Click '⬅ Back' to return to main menu")
    return embed


def _build_search_embed(guild_id: int, query: str) -> discord.Embed:
    query_l = query.lower()

    # Search in systems
    sys_results = [
        (k, v) for k, v in _SYSTEM_LOOKUP.items()
        if query_l in k or query_l in v[1].lower() or query_l in v[2].lower()
    ]

    # Search in custom commands
    cmd_results = []
    try:
        custom_cmds = dm.get_guild_data(guild_id, "custom_commands", {})
        for cmd_name, cmd_data in custom_cmds.items():
            if query_l in cmd_name.lower():
                cmd_results.append((cmd_name, cmd_data))
    except: pass

    embed = discord.Embed(
        title=f"🔍 Search Results: \"{query}\"",
        color=BRAND
    )

    if sys_results:
        text = ""
        for sys_key, (emoji, desc, cat) in sys_results[:8]:
            status = _status_emoji(guild_id, sys_key)
            text += f"{emoji} **{sys_key.title()}** {status} — {desc}\n"
        embed.add_field(name="Systems", value=text, inline=False)

    if cmd_results:
        text = ""
        for cmd_name, _ in cmd_results[:12]:
            text += f"`!{cmd_name}` "
        embed.add_field(name="Matching Commands", value=text or "None", inline=False)

    if not sys_results and not cmd_results:
        embed.description = f"No systems or commands found matching **\"{query}\"**."

    embed.set_footer(text=f"Search scope: Global Systems & Guild Commands")
    return embed


# ─────────────────────────── Views ──────────────────────────────────────────

class _SearchModal(ui.Modal, title="Search Systems"):
    query = ui.TextInput(label="Search term", placeholder="e.g. economy, role, ban", max_length=50)

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: Interaction):
        embed = _build_search_embed(self.guild_id, self.query.value)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class HelpCategoryView(ui.View):
    """Shown when user clicks a category button from the main help menu."""

    def __init__(self, guild_id: int, cat_name: str):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.cat_name = cat_name

    @ui.button(label="🏠 Home", style=discord.ButtonStyle.secondary, custom_id="help_cat_back")
    async def back(self, interaction: Interaction, button: ui.Button):
        view = HelpMainView(interaction.guild_id)
        embed = _build_main_embed(interaction.guild_id, interaction.client)
        await interaction.response.edit_message(embed=embed, view=view)

    @ui.button(label="🔍 Search", style=discord.ButtonStyle.secondary, custom_id="help_cat_search")
    async def search(self, interaction: Interaction, button: ui.Button):
        await interaction.response.send_modal(_SearchModal(interaction.guild_id))

    @ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, custom_id="help_cat_refresh")
    async def refresh(self, interaction: Interaction, button: ui.Button):
        embed = _build_category_embed(interaction.guild_id, self.cat_name)
        await interaction.response.edit_message(embed=embed, view=self)


class HelpMainView(ui.View):
    """The main !help panel with category select buttons."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        # Dynamically add one button per category
        for idx, cat_name in enumerate(CATEGORIES.keys()):
            btn = ui.Button(
                label=cat_name,
                style=discord.ButtonStyle.primary,
                custom_id=f"help_cat_{idx}",
                row=idx // 3
            )
            # Closure to capture cat_name
            btn.callback = self._make_callback(cat_name)
            self.add_item(btn)

        # Search button always last
        search_btn = ui.Button(
            label="🔍 Search",
            style=discord.ButtonStyle.secondary,
            custom_id="help_main_search",
            row=3
        )
        search_btn.callback = self._search_callback
        self.add_item(search_btn)

    def _make_callback(self, cat_name: str):
        async def callback(interaction: Interaction):
            view = HelpCategoryView(interaction.guild_id, cat_name)
            embed = _build_category_embed(interaction.guild_id, cat_name)
            await interaction.response.edit_message(embed=embed, view=view)
        return callback

    async def _search_callback(self, interaction: Interaction):
        await interaction.response.send_modal(_SearchModal(interaction.guild_id))


async def send_help(channel: discord.TextChannel, guild_id: int, invoker: discord.Member = None, system_query: str = None):
    """Entry point called from bot.py on_message for the !help command."""
    if system_query:
        system_key = system_query.lower().replace("_", "").replace("system", "")
        if system_key in _SYSTEM_LOOKUP:
            emoji, desc, cat = _SYSTEM_LOOKUP[system_key]
            embed = discord.Embed(title=f"{emoji} System Guide: {system_key.title()}", color=CATEGORIES[cat]["color"])
            embed.description = f"**Description:** {desc}\n\n**Category:** {cat}\n**Configuration:** `!configpanel{system_key}`"

            # Add common commands for this system if they exist in help_data
            # For now, show basic info.
            embed.set_footer(text=f"Requested by {invoker.display_name if invoker else 'User'}")
            await channel.send(embed=embed)
            return

    view = HelpMainView(guild_id)
    embed = _build_main_embed(guild_id, channel.guild.me.client if hasattr(channel, "guild") else None)
    if invoker:
        embed.set_author(name=f"Requested by {invoker.display_name}", icon_url=invoker.display_avatar.url)
    await channel.send(embed=embed, view=view)
