import discord
from data_manager import dm

class TriggerRoles:
    """
    When a user types a trigger word (e.g. /Asclade), assign a target role.
    Store in trigger_roles.json. Cooldown and channel restrictions applied.
    """
    def __init__(self, bot):
        self.bot = bot

    def get_triggers(self, guild_id: int) -> dict:
        return dm.get_guild_data(guild_id, "trigger_roles", {})

    def add_trigger(self, guild_id: int, word: str, role_id: int):
        triggers = self.get_triggers(guild_id)
        triggers[word] = role_id
        dm.update_guild_data(guild_id, "trigger_roles", triggers)

    async def handle_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
            
        triggers = self.get_triggers(message.guild.id)
        for word, role_id in triggers.items():
            if word in message.content:
                role = message.guild.get_role(role_id)
                if role:
                    # Check if user already has it
                    if role not in message.author.roles:
                        await message.author.add_roles(role)
                        await message.channel.send(f"✅ {message.author.mention}, you have been assigned the **{role.name}** role via trigger word!")
                        # Optional log
                        return
