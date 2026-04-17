def _resolve_channel(self, channel_identifier):
    """Resolve a channel from the guild by name, ID, or mention format."""
    guild = getattr(self, 'guild', None)
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
    return None