import asyncio
import hashlib
from functools import partial
from pathlib import Path
from typing import Optional

from discord import app_commands
from discord.app_commands import Choice
from red_commons.logging import getLogger
from redbot.core import commands
from redbot.core.i18n import Translator, cog_i18n

from pylav import Query
from pylav.types import BotT, InteractionT
from pylav.utils import AsyncIter, PyLavContext
from pylavcogs_shared.utils import rgetattr

LOGGER = getLogger("red.3pt.PyLavLocalFiles")

_ = Translator("PyLavLocalFiles", Path(__file__))


@cog_i18n(_)
class PyLavLocalFiles(commands.Cog):
    """Play local files and folders from the owner configured location."""

    __version__ = "1.0.0.0rc0"

    def __init__(self, bot: BotT, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self._localtrack_entries: dict[str, Query] = {}
        self.ready_event = asyncio.Event()

    async def initialize(self):
        from pylav.localfiles import LocalFile

        while LocalFile._ROOT_FOLDER is None:
            await asyncio.sleep(1)
        assert LocalFile._ROOT_FOLDER is not None
        async for file in LocalFile(LocalFile._ROOT_FOLDER).files_in_tree(show_folders=True):
            self._localtrack_entries[hashlib.md5(f"{file._query.path}".encode()).hexdigest()] = file
        self.ready_event.set()

    async def cog_check(self, ctx: PyLavContext):
        return bool(self.ready_event.is_set())

    @app_commands.command(name="local")
    @app_commands.guild_only()
    async def slash_local(
        self,
        interaction: InteractionT,
        entry: str,
        recursive: Optional[bool] = False,
    ):  # sourcery no-metrics
        """Play a local file or folder, supports partial searching."""
        send = partial(interaction.followup.send, wait=True)
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        author = interaction.user

        entry = self._localtrack_entries[entry]
        entry._recursive = recursive
        player = self.lavalink.get_player(interaction.guild.id)
        if player is None:
            config = await self.lavalink.player_config_manager.get_config(interaction.guild.id)
            if (channel := interaction.guild.get_channel_or_thread(config.forced_channel_id)) is None:
                channel = rgetattr(author, "voice.channel", None)
                if not channel:
                    await send(
                        embed=await self.lavalink.construct_embed(
                            description=_("You must be in a voice channel to allow me to connect."),
                            messageable=interaction,
                        ),
                        ephemeral=True,
                    )
                    return
            if not (
                (permission := channel.permissions_for(interaction.guild.me))
                and permission.connect
                and permission.speak
            ):
                await send(
                    embed=await self.lavalink.construct_embed(
                        description=_("I don't have permission to connect or speak in {channel}.").format(
                            channel=channel.mention
                        ),
                        messageable=interaction,
                    ),
                    ephemeral=True,
                )
                return
            player = await self.lavalink.connect_player(channel=channel, requester=author)

        successful, count, failed = await self.lavalink.get_all_tracks_for_queries(
            entry, requester=author, player=player
        )
        if count:
            if count == 1:
                await player.add(requester=author.id, track=successful[0])
            else:
                await player.bulk_add(requester=author.id, tracks_and_queries=successful)
        single_track = successful[0] if successful else None
        if not (player.is_playing or player.queue.empty()):
            await player.next(requester=author)

        if count > 1:
            await send(
                embed=await self.lavalink.construct_embed(
                    description=_("{track_count} tracks enqueued.").format(track_count=count),
                    messageable=interaction,
                ),
                ephemeral=True,
            )
        elif count == 1:
            await send(
                embed=await self.lavalink.construct_embed(
                    description=_("{track} enqueued.").format(
                        track=await single_track.get_track_display_name(with_url=True)
                    ),
                    messageable=interaction,
                ),
                ephemeral=True,
            )
        else:
            await send(
                embed=await self.lavalink.construct_embed(
                    description=_("No tracks were found for your query."),
                    messageable=interaction,
                ),
                ephemeral=True,
            )

    @slash_local.autocomplete("entry")
    async def slash_local_autocomplete_entry(self, interaction: InteractionT, current: str):
        entries = []
        async for md5, folder in AsyncIter(self._localtrack_entries.items()).filter(
            lambda x: current.lower() in f"{x[1]._query.path}".lower()
        ):
            choice = Choice(
                name=await folder.query_to_string(max_length=99),
                value=md5,
            )
            entries.append(choice)
            if len(entries) == 25:
                break
        return entries
