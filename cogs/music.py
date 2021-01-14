import datetime
import math
import re

import discord
import traceback
import humanize
import wavelink
import spotify
from discord.ext import commands
from .utils import paginator, db, checks

from .utils.objects import Player, AutoPlayer, Track, SpotifyTrack


RURL = re.compile(r'https?:\/\/(?:www\.)?.+')
ALBUM_URL = re.compile(r'^(?:https?://(?:open\.)?spotify\.com|spotify)([/:])album\1([a-zA-Z0-9]+)')
ARTIST_URL = re.compile(r'^(?:https?://(?:open\.)?spotify\.com|spotify)([/:])artist\1([a-zA-Z0-9]+)')
PLAYLIST_URL = re.compile(r'(?:https?://(?:open\.)?spotify\.com(?:/user/[a-zA-Z0-9_]+)?|spotify)([/:])playlist\1([a-zA-Z0-9]+)')
TRACK_URL = re.compile(r'^(?:https?://(?:open\.)?spotify\.com|spotify)([/:])track\1([a-zA-Z0-9]+)')

class MusicChannels(db.Table, table_name='music_channels'):
    guild_id = db.Column(db.Integer(big=True), primary_key=True)
    channel_ids = db.Column(db.Array(db.Integer(big=True)))
    djs = db.Column(db.Array(db.Integer(big=True)))

class MusicLoopers(db.Table, table_name='music_loopers'):
    guild_id = db.Column(db.Integer(big=True), primary_key=True)
    vc_id = db.Column(db.Integer(big=True))
    tc_id = db.Column(db.Integer(big=True))
    playlist = db.Column(db.String)
    enabled = db.Column(db.Boolean, default=False)

class MusicError(commands.CommandError):
    pass

class SpotifyList:

    def __init__(self, name, tracks):
        self.name = name
        self.tracks = tracks

def check_no_automusic():
    def predicate(ctx):
        if not isinstance(ctx.player, AutoPlayer):
            return True
        raise MusicError('You may not use this while in automusic mode.')
    return commands.check(predicate)

def check_in_voice():
    def predicate(ctx):
        if not ctx.player.is_connected:
            raise MusicError('I am not currently connected to voice!')

        if ctx.author.voice is None or ctx.author.voice.channel != ctx.guild.me.voice.channel:
            # this block is necessary because it sometimes throws an AttributeError in checks during `filter_commands`
            # in cases of fucked up permissions, mostly in mute roles
            try:
                channel = ctx.guild.me.voice.channel
            except AttributeError:
                return False
            raise MusicError(f"You must be connected to {ctx.guild.me.voice.channel.mention} to control music!");   
        return True
    return commands.check(predicate)

class Music(commands.Cog):
    """All the tunes \U0001f3b5
    
    Spotify is buggy for some reason and I don't know enough Java to debug.
    If it doesn't work just don't use it for the time being.
    """
    def __init__(self, bot):
        self.bot = bot

        if not hasattr(self.bot, 'wavelink'):
            self.bot.wavelink = wavelink.Client(bot=bot)
        if not hasattr(self.bot, 'cached_always_play'):
            self.bot.cached_always_play = self.always_plays = {}
            self.bot.loop.create_task(self.load_always_play())
        else:
            self.always_plays = self.bot.cached_always_play
        
        self.bot.loop.create_task(self.__init_nodes__())

    async def __init_nodes__(self):
        await self.bot.wait_until_ready()

        nodes = {
            'MAIN': {
                'host': self.bot.config.lavalink_ip,
                'port': 2333,
                'rest_uri': f'http://{self.bot.config.lavalink_ip}:2333',
                'password': self.bot.config.lavalink_password,
                'identifier': 'MAIN',
                'region': 'europe',
            }
        }

        for n in nodes.values():
            node = await self.bot.wavelink.initiate_node(**n)
            node.set_hook(self.event_hook)

    def event_hook(self, event):
        """Our event hook. Dispatched when an event occurs on our Node."""
        if isinstance(event, wavelink.TrackEnd):
            event.player.next_event.set()
        elif isinstance(event, wavelink.TrackException):
            print(event.error)

    def required(self, player, invoked_with):
        """Calculate required votes."""
        channel = self.bot.get_channel(int(player.channel_id))
        if invoked_with == 'stop':
            if len(channel.members) - 1 == 2:
                return 2

        return math.ceil((len(channel.members) - 1) / 2.5)

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if ctx.command.qualified_name == self.musicchannel.qualified_name:
            return True
        channels = await self.bot.pool.fetchval("SELECT channel_ids FROM music_channels WHERE guild_id = $1", ctx.guild.id)
        if not channels:
            return True

        if ctx.channel.id in channels:
            return True
        raise commands.CheckFailure("This isn't a music channel!")

    async def has_perms(self, ctx, **perms):
        """Check whether a member has the given permissions."""
        try:
            player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        except:
            return False
        if await self.bot.is_owner(ctx.author):
            return True
        try:
            if player.dj is None:
                player.dj = ctx.author
            if ctx.author.id == player.dj.id:
                return True
        except:
            pass
        
        guild_djs = await self.bot.pool.fetchval("SELECT djs FROM music_channels WHERE guild_id = $1;", ctx.guild.id)
        if guild_djs is None:
            guild_djs = []
        for _id in guild_djs:
            if ctx.author._roles.has(_id):
                return True

        ch = ctx.channel
        permissions = ch.permissions_for(ctx.author)

        missing = [perm for perm, value in perms.items() if getattr(permissions, perm, None) != value]

        if not missing:
            return True

        return False

    async def vote_check(self, ctx, command: str):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        vcc = len(self.bot.get_channel(int(player.channel_id)).members) - 1
        votes = getattr(player, command + 's', None)

        if vcc < 3 and not ctx.invoked_with == 'stop':
            votes.clear()
            return True
        else:
            votes.add(ctx.author.id)

            if len(votes) >= self.required(player, ctx.invoked_with):
                votes.clear()
                return True
        return False

    async def do_vote(self, ctx, player, command: str):
        attr = getattr(player, command + 's', None)
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if ctx.author.id in attr:
            await ctx.send(f'{ctx.author.mention}, you have already voted to {command}!', delete_after=15)
        elif await self.vote_check(ctx, command):
            await ctx.send(f'Vote request for {command} passed!', delete_after=20)
            to_do = getattr(self, f'do_{command}')
            await to_do(ctx)
        else:
            await ctx.send(f'{ctx.author.mention}, has voted to {command} the song!'
                           f' **{self.required(player, ctx.invoked_with) - len(attr)}** more votes needed!',
                           delete_after=45)

    @commands.group(invoke_without_command=True)
    @check_no_automusic()
    async def musicchannel(self, ctx):
        """
        view the channels you can use music commands in!
        """
        channels = await self.bot.pool.fetchval("SELECT channel_ids FROM music_channels WHERE guild_id = $1", ctx.guild.id)
        if not channels:
            return await ctx.send("Music commands can be used in any channel!")

        channels = [self.bot.get_channel(x) for x in channels if self.bot.get_channel(x) is not None]
        channels = [x.mention for x in channels if x.permissions_for(ctx.author).read_messages]
        e = ctx.embed_invis(title="Music Channels")
        fmt = "\n> " + "\n> ".join(channels)
        e.description = f"Music commands can be used in any of the following channels!\n{fmt}"
        await ctx.send(embed=e)

    @musicchannel.command()
    @checks.is_admin()
    @check_no_automusic()
    async def add(self, ctx, channel: discord.TextChannel):
        """
        Add a channel to the whitelisted channels for music commands.
        You need administrator permissions to use this command.
        """
        channels = await self.bot.pool.fetchval("SELECT channel_ids FROM music_channels WHERE guild_id = $1", ctx.guild.id)
        if channel.id not in channels:
            channels.append(channel.id)
        await ctx.db.execute("""INSERT INTO music_channels (guild_id, channel_ids) VALUES ($1, $2)
                                ON CONFLICT (guild_id) DO UPDATE SET channel_ids = $2 WHERE guild_id = $1;""", ctx.guild.id, channels)
        await ctx.send(f"Added {channel.mention} to whitelisted channels")

    @musicchannel.command()
    @checks.is_admin()
    @check_no_automusic()
    async def remove(self, ctx, channel: discord.TextChannel): # TODO: put this in !queue subgroup?
        """
        removes a channel from the whitelisted channels for music commands.
        You need administrator permissions to use this command.
        """
        channels = await self.bot.pool.fetchval("SELECT channel_ids FROM music_channels WHERE guild_id = $1", ctx.guild.id)
        exists = channel.id in channels
        if not exists:
            return await ctx.send("That channel is not whitelisted.")
        channels.remove(channel.id)
        await self.bot.pool.execute("UPDATE music_channels SET channel_ids = $2 WHERE guild_id = $1", ctx.guild.id, channels)
        await ctx.send(f"Removed {channel.mention} from whitelisted channels")

    @commands.command(name='connect', aliases=['c'])
    @check_no_automusic()
    async def connect_(self, ctx, *, channel: discord.VoiceChannel=None):
        """Connect to voice.

        If no channel is provided, then it will try to connect to the voice channel you are connected to.
        """
        if channel is None:
            try:
                channel = ctx.author.voice.channel
            except AttributeError:
                raise MusicError('No channel to join. Either specify a valid channel, or join one.')

        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if player.is_connected and ctx.guild.me.voice is not None:
            if ctx.author.voice.channel == ctx.guild.me.voice.channel:
                return

        await player.connect(channel.id)
        player.controller_channel_id = ctx.channel.id

    @commands.command()
    @check_no_automusic()
    async def play(self, ctx, *, query: str):
        """Queue a song or playlist for playback.

        Can be a youtube link or a song name.

        (Spotify has bugs I can't fix right now, so if it doesn't work just don't use it for a while.)
        """
        await self.play_(ctx, query)

    @commands.command()
    @checks.is_admin()
    @check_no_automusic()
    async def playnext(self, ctx, *, query):
        """Queue a song or playlist to be played next.
        Can be a youtube link or a song name.

        You must have administrator permissions to use this.
        """
        await self.play_(ctx, query, appendleft=True)

    async def play_(self, ctx, query, appendleft=False):
        await ctx.trigger_typing()
        await ctx.invoke(self.connect_)
        query = query.strip('<>')
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected:
            return await ctx.send('Not connected to voice. Please join a voice channel to play music.')

        if not player.dj:
            player.dj = ctx.author
        
        tracks, data = await self._find_tracks(ctx, player, query)
        if tracks is None:
            return
        if isinstance(tracks, list):
            for i in tracks:
                if appendleft:
                    player.queue.putleft(i)
                else:
                    player.queue.put_nowait(i)
            await ctx.send(f'```ini\nAdded the playlist {data.data["playlistInfo"]["name"]}'
                           f' with {len(data.tracks)} songs to the queue.\n```')

        elif isinstance(tracks, SpotifyList):
            for i in tracks.tracks:
                if appendleft:
                    player.queue.putleft(i)
                else:
                    player.queue.put_nowait(i)
            await ctx.send(f'```ini\nAdded {tracks.name}'
                           f' with {len(tracks.tracks)} songs to the queue.\n```')
        else:
            if appendleft:
                player.queue.putleft(Track(tracks.id, tracks.info, ctx=ctx))
            else:
                player.queue.put_nowait(Track(tracks.id, tracks.info, ctx=ctx))
            await ctx.send(f'```ini\nAdded {tracks.title} to the Queue\n```', delete_after=15)

    async def get_album_tracks(self, id, ctx):
        album = await self.spotify.get_album(id)
        tracks = await album.get_all_tracks()
        return self.return_tracks(album.name, tracks, ctx)

    async def get_artist_tracks(self, id, ctx):
        artist = await self.spotify.get_artist(id)
        tracks = await artist.top_tracks()
        return self.return_tracks(artist.name, tracks, ctx)

    async def get_playlist_tracks(self, id, ctx):
        playlist = spotify.Playlist(self.spotify, await self.spotify.http.get_playlist(id))
        tracks = await playlist.get_all_tracks()
        return self.return_tracks(playlist.name, tracks, ctx)

    def return_tracks(self, name, tracks, ctx):
        to_return = []
        for track in tracks:
            try:
                thumb = track.images[0].url
            except (IndexError, AttributeError):
                thumb = None
            to_return.append(SpotifyTrack(track.name, track.artists, ctx=ctx, requester=ctx.author))
        return SpotifyList(name, to_return)

    async def get_spotify_track(self, id, ctx):
        track = await self.spotify.get_track(id)
        base = SpotifyTrack(track.name, track.artists, ctx=ctx, requester=ctx.author)
        return await base.find_wavelink_track()

    async def _find_tracks(self, ctx, player, query):
        if not RURL.match(query):
            query = f'ytsearch:{query}'
        
        if ALBUM_URL.match(query):
            tracks = await self.get_album_tracks(ALBUM_URL.match(query).group(2), ctx)
        elif ARTIST_URL.match(query):
            tracks = await self.get_artist_tracks(ALBUM_URL.match(query).group(2), ctx)
        elif PLAYLIST_URL.match(query):
            tracks = await self.get_playlist_tracks(PLAYLIST_URL.match(query).group(2), ctx)
        elif TRACK_URL.match(query):
            tracks = await self.get_spotify_track(TRACK_URL.match(query).group(2), ctx)
        else:
            try:
                tracks = await self.bot.wavelink.get_tracks(query)
            except KeyError:
                tracks = None

        if not tracks:
            await ctx.send('No songs were found with that query. Please try again.')
            return None, None

        if isinstance(tracks, wavelink.TrackPlaylist):
            return [Track(t.id, t.info, ctx=ctx) for t in tracks.tracks], tracks

        elif isinstance(tracks, SpotifyList):
            return tracks, None
        
        else:
            track = tracks[0]
            return Track(track.id, track.info, ctx=ctx), tracks

    @commands.command(name='np', aliases=['current', 'currentsong'])
    @commands.cooldown(2, 15, commands.BucketType.user)
    async def now_playing(self, ctx):
        """
        Show the Current Song
        """
        if not ctx.player or not ctx.player.is_connected or not ctx.player.is_playing:
            return await ctx.send("Nothing is currently playing")

        await ctx.player.now_playing(channel=ctx.channel)

    @commands.command(name='pause')
    @check_no_automusic()
    @check_in_voice()
    async def pause_(self, ctx):
        """
        Pause the currently playing song.
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        if not player:
            return

        if player.paused:
            return

        if await self.has_perms(ctx, manage_guild=True):
            await ctx.send(f'{ctx.author.mention} has paused the song as an admin or DJ.', delete_after=25)
            return await self.do_pause(ctx)

        await self.do_vote(ctx, player, 'pause')

    async def do_pause(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        player.paused = True
        await player.set_pause(True)

    @commands.command(name='resume')
    @check_no_automusic()
    @check_in_voice()
    async def resume_(self, ctx):
        """
        Resume a currently paused song.
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.paused:
            return

        if await self.has_perms(ctx, manage_guild=True):
            await ctx.send(f'{ctx.author.mention} has resumed the song as an admin or DJ.', delete_after=25)
            return await self.do_resume(ctx)

        await self.do_vote(ctx, player, 'resume')

    async def do_resume(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        await player.set_pause(False)

    @commands.command(name='skip', aliases=['next'])
    @commands.cooldown(5, 10, commands.BucketType.user)
    @check_in_voice()
    @check_no_automusic()
    async def skip_(self, ctx):
        """Skip the current song.
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)


        if await self.has_perms(ctx, manage_guild=True):
            await ctx.send(f'{ctx.author.mention} has skipped the song as an admin or DJ.', delete_after=25)
            return await self.do_skip(ctx)

        if not player.current:
            return await ctx.send("Nothing is currently playing")

        if player.current.requester.id == ctx.author.id:
            await ctx.send(f'The requester {ctx.author.mention} has skipped the song.')
            return await self.do_skip(ctx)

        await self.do_vote(ctx, player, 'skip')

    async def do_skip(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        await player.stop()

    @commands.command(name='stop', aliases=['dc', 'disconnect', 'shoo', 'begone'])
    @commands.cooldown(3, 30, commands.BucketType.guild)
    @check_in_voice()
    @check_no_automusic()
    async def stop_(self, ctx):
        """Stop the player, disconnect and clear the queue.
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)


        if await self.has_perms(ctx, manage_guild=True):
            await ctx.send(f'{ctx.author.mention} has stopped the player as an admin or DJ.', delete_after=25)
            return await self.do_stop(ctx)

        await self.do_vote(ctx, player, 'stop')

    async def do_stop(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        await player.destroy_controller()
        await player.destroy()

    @commands.command(name='volume', aliases=['vol'])
    @commands.cooldown(1, 2, commands.BucketType.guild)
    @check_in_voice()
    async def volume_(self, ctx, *, value: int):
        """Change the player volume.
        Parameters
        ------------
        value: [Required]
            The volume level you would like to set. This can be a number between 1 and 100.
        Examples
        ----------
        <prefix>volume <value>
        {ctx.prefix}volume 50
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not 0 < value < 101:
            return await ctx.send('Please enter a value between 1 and 100.')

        if not await self.has_perms(ctx, manage_guild=True):
            if (len(ctx.author.voice.channel.members) - 1) > 2:
                return

        await player.set_volume(value)
        await ctx.send(f'Set the volume to **{value}**%', delete_after=7)


    @commands.command(name='queue', aliases=['q', 'que'])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def queue_(self, ctx):
        """Retrieve a list of currently queued songs.
        Examples
        ----------
        <prefix>queue
        {ctx.prefix}queue
        {ctx.prefix}q
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected:
            return await ctx.send('I am not currently connected to voice!')

        upcoming = player.entries

        if not upcoming:
            return await ctx.send('```\nNo more songs in the Queue!\n```', delete_after=15)

        if isinstance(player, Player):
            pages = paginator.Pages(ctx, entries=[f"`{song}` - {song.author} - requested by: {song.requester}" for song
                                              in upcoming], embed_color=0x36393E, title="Upcoming Songs")
        else:
            pages = paginator.Pages(ctx, entries=[f"`{song}` - {song.author}" for song
                                              in upcoming], embed_color=0x36393E, title="Upcoming Songs")

        await pages.paginate()

    @commands.command(name='shuffle', aliases=['mix'])
    @commands.cooldown(2, 10, commands.BucketType.user)
    @check_in_voice()
    @check_no_automusic()
    async def shuffle_(self, ctx):
        """Shuffle the current queue.
        Examples
        ----------
        <prefix>shuffle
            {ctx.prefix}shuffle
            {ctx.prefix}mix
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if len(player.entries) < 3:
            return await ctx.send('Please add more songs to the queue before trying to shuffle.', delete_after=10)

        if await self.has_perms(ctx, manage_guild=True):
            await ctx.send(f'{ctx.author.mention} has shuffled the playlist as an admin or DJ.', delete_after=25)
            return await self.do_shuffle(ctx)

        await self.do_vote(ctx, player, 'shuffle')

    async def do_shuffle(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        player.shuffle()

        player.update = True

    @commands.command(name='repeat')
    async def repeat_(self, ctx):
        """Repeat the currently playing song.
        Examples
        ----------
        <prefix>repeat
            {ctx.prefix}repeat
        """

        if not ctx.player.is_connected:
            return await ctx.send('I am not currently connected to voice!')

        if ctx.author.voice is None or ctx.author.voice.channel != ctx.guild.me.voice.channel:
            return await ctx.send(f"You must be connected to {ctx.guild.me.voice.channel.mention} to control music!")

        if await self.has_perms(ctx, manage_guild=True):
            await ctx.send(f'{ctx.author.mention} has repeated the song as an admin or DJ.', delete_after=25)
            return await self.do_repeat(ctx)

        await self.do_vote(ctx, ctx.player, 'repeat')

    async def do_repeat(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        player.repeat()

    @commands.command(name='vol_up', hidden=True)
    @check_in_voice()
    async def volume_up(self, ctx):
        """
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        vol = int(math.ceil((player.volume + 10) / 10)) * 10

        if vol > 100:
            vol = 100
            await ctx.send('Maximum volume reached', delete_after=7)

        await player.set_volume(vol)
        player.update = True

    @commands.command(name='vol_down', hidden=True)
    @check_in_voice()
    async def volume_down(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        vol = int(math.ceil((player.volume - 10) / 10)) * 10

        if vol < 0:
            vol = 0
            await ctx.send('Player is currently muted', delete_after=10)

        await player.set_volume(vol)
        player.update = True

    @commands.command(name='seteq', aliases=['eq'])
    @check_no_automusic()
    @check_in_voice()
    async def set_eq(self, ctx, *, eq: str):
        """
        set the music EQ!
        Available EQ
        -------------
        - Flat
        - Boost
        - Metal
        - Piano
        """
        player = ctx.player

        if eq.upper() not in player.equalizers:
            return await ctx.send(f'`{eq}` - Is not a valid equalizer!\nTry Flat, Boost, Metal, Piano.')

        await player.set_eq(player.equalizers[eq.upper()])
        await ctx.send(f'The player Equalizer was set to - {eq.capitalize()}')

    @commands.command()
    @check_no_automusic()
    @check_in_voice()
    async def controller(self, ctx):
        """
        gives you a fancy music controller
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        if not player.current:
            return await ctx.send("nothing is currently playing")

        await player.invoke_controller()

    @commands.command()
    @check_no_automusic()
    async def history(self, ctx):
        """
        Shows the song history of the **current session**
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected:
            return await ctx.send('I am not currently connected to voice!')

        if not player.queue.history:
            return await ctx.send("No history!")

        pages = paginator.Pages(ctx, entries=[f"[{track}]({track.uri} \"{track.title}\")"
                                              f" - {track.author} {'| Requested by'+str(track.requester) if not isinstance(player, AutoPlayer) else ''}" for track in
                                              reversed(player.queue.history)],
                                embed_color=0x36393E,
                                title="Music History",
                                per_page=5
                                )
        await pages.paginate()

    @commands.command(aliases=["wavelink", "wl", "ll"])
    async def musicinfo(self, ctx):
        """Retrieve various Node/Server/Player information."""
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        node = player.node

        used = humanize.naturalsize(node.stats.memory_used)
        total = humanize.naturalsize(node.stats.memory_allocated)
        free = humanize.naturalsize(node.stats.memory_free)
        cpu = node.stats.cpu_cores

        fmt = f'**WaveLink:** `{wavelink.__version__}`\n\n' \
              f'Connected to `{len(self.bot.wavelink.nodes)}` nodes.\n' \
              f'Best available Node `{self.bot.wavelink.get_best_node().__repr__()}`\n' \
              f'`{len(self.bot.wavelink.players)}` players are distributed on nodes.\n' \
              f'`{node.stats.players}` players are distributed on server.\n' \
              f'`{node.stats.playing_players}` players are playing on server.\n\n' \
              f'Server Memory: `{used}/{total}` | `({free} free)`\n' \
              f'Server Cores: `{cpu}`\n\n' \
              f'Server Uptime: `{datetime.timedelta(milliseconds=node.stats.uptime)}`'
        await ctx.send(fmt)

    async def load_always_play(self):
        plays = await self.bot.pool.fetch("SELECT guild_id, vc_id, tc_id, playlist FROM music_loopers WHERE enabled = true;")

        for gid, vcid, tcid, pl in plays:
            self.always_plays[gid] = (vcid, pl, tcid)

    @commands.Cog.listener("on_voice_state_update")
    async def check_users(self, member, *args):
        if member.bot:
            return

        if not self.bot.is_ready() or member.guild.id not in self.always_plays:
            return

        player = self.bot.wavelink.get_player(member.guild.id, cls=AutoPlayer)
        if not isinstance(player, AutoPlayer):
            await player.destroy()
            player = self.bot.wavelink.get_player(member.guild.id, cls=AutoPlayer)

        player.controller_channel_id = self.always_plays[member.guild.id][2]

        if not player.is_connected:
            try:
                await player.connect(self.always_plays[member.guild.id][0])
                await self.run_playlist(player, member.guild)

            except discord.Forbidden:
                return

            except discord.HTTPException:
                # channel not found?
                return  # TODO: maybe remove the mode here?

        if len(member.guild.me.voice.channel.members) == 1:
            if not player.paused:
                await player.set_pause(True)

        else:
            if player.paused:
                await player.set_pause(False)

    async def run_playlist(self, player: AutoPlayer, guild, playlist=None):
        player.autoplay = True
        playlist = playlist or self.always_plays[guild.id][2]
        try:
            tracks = await self.bot.wavelink.get_tracks(playlist) #type: wavelink.TrackPlaylist
        except Exception as e:
            tracks = None
        if not tracks:
            try:
                await self.bot.get_channel(self.always_plays[guild.id][1]).send(
                    f"Failed to load playlist at <{self.always_plays[guild.id][2]}")
            finally:
                return

        player.assign_playlist(tracks.tracks, tracks.data)

    @commands.group(name="247", invoke_without_command=True)
    @checks.is_admin()
    async def twofortyseven(self, ctx, voice_channel: discord.VoiceChannel, text_channel: discord.TextChannel, playlist: str):
        """No Spotify support here... yet"""
        tracks = await self.bot.wavelink.get_tracks(playlist)

        if not tracks:
            return await ctx.send("Invalid playlist")

        await self.bot.pool.execute("INSERT INTO music_loopers VALUES ($1,$2,$3,$4,$5) ON CONFLICT (guild_id) DO UPDATE "
                                  "SET playlist=$4, vc_id=$2, tc_id=$3 WHERE music_loopers.guild_id=$1;", ctx.guild.id,
                                  voice_channel.id, text_channel.id, playlist, False)

        await ctx.send(f"Your 24/7 music has been set up. To enable it, run `{ctx.prefix}247 enabled`")

    @twofortyseven.command("enable")
    @checks.is_admin()
    async def tfs_enable(self, ctx):
        await self.tfs_runner(ctx, True)

    @twofortyseven.command("disable")
    @checks.is_admin()
    async def tfs_disable(self, ctx):
        await self.tfs_runner(ctx, False)

    async def tfs_runner(self, ctx, state: bool):
        found = await self.bot.pool.fetchrow("UPDATE music_loopers SET enabled=$1 WHERE guild_id = $2 RETURNING guild_id, vc_id, tc_id, playlist;",
                                        state, ctx.guild.id)

        if not found:
            return await ctx.send("Please set up 24/7 music first")

        else:
            await ctx.send(f"{'Enabled' if state else 'Disabled'} 24/7 music")

        if state:
            self.always_plays[ctx.guild.id] = found['vc_id'], found['tc_id'], found['playlist']

        else:
            self.always_plays.pop(ctx.guild.id, None)

        try:
            await ctx.player.destroy()
        except:
            pass


def setup(bot):
    bot.add_cog(Music(bot))
