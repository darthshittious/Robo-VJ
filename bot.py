
__version__ = "6.0.0"
__author__ = "Varun J"

import aiohttp
import datetime
import os
import random
import json
import asyncpg
import config
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import sys
from collections import Counter, deque, defaultdict
from cogs.utils.config import Config
from cogs.utils import context, time, db
import logging
import traceback



log = logging.getLogger(__name__)
load_dotenv()

initial_extensions =  [
    'cogs.tmdb',
    'cogs.buttons',
    'cogs.change_state',
    'cogs.config',
    'cogs.dpy_help',
    'cogs.feeds',
    'cogs.funhouse',
    'cogs.me_only',
    'cogs.meta',
    'cogs.mod',
    'cogs.music',
    'cogs.my_quiz',
    'cogs.poll',
    'cogs.quiz',
    'cogs.reminder',
    'cogs.stars',
    'cogs.stats',
    'cogs.tags',
    'cogs.utilities',
    'jishaku'
]

class Servers(db.Table):
    id = db.PrimaryKeyColumn()
    guild_id = db.Column(db.Integer(big=True))
    qchannel = db.Column(db.Integer(big=True))
    pchannel = db.Column(db.Integer(big=True))
    prefixes = db.Column(db.Array(db.String))
    modlog = db.Column(db.Integer(big=True))

class NamedServers(db.Table, table_name='named_servers'):
    id = db.PrimaryKeyColumn()
    guild_id = db.Column(db.Integer(big=True))
    name = db.Column(db.String)

def _prefix_callable(bot, msg):
    user_id = bot.user.id
    base = [f'<@!{user_id}> ', f'<@{user_id}> ']
    if msg.guild is None:
        base.append('!')
        base.append('?')
    else:
        base.extend(bot.prefixes.get(msg.guild.id, ['?', '!']))
    return base

class RoboVJ(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=_prefix_callable, status=discord.Status.online, activity=discord.Activity(
                        name=f"!help", type=discord.ActivityType.listening), owner_id=411166117084528640,
                        #help_command=EmbedHelpCommand(dm_help=None),
                        help_command=commands.DefaultHelpCommand(width=150, no_category='General', dm_help=None),
                        case_insensitive=True, intents=discord.Intents.all())

        self.version = __version__
        self.prefixes = {}
        self.qchannels = {}
        self.pchannels = {}
        self.modlogs = {}
        self.blacklist = Config('blacklist.json')
        self.spam_control = commands.CooldownMapping.from_cooldown(10, 12.0, commands.BucketType.user)
        self._auto_spam_count = Counter()

        for extension in initial_extensions:
            try:
                self.load_extension(extension)
            except Exception as e:
                print(f'Failed to load extension {extension}.', file=sys.stderr)
                traceback.print_exc()
                
        self.session = aiohttp.ClientSession(loop=self.loop)

        self._prev_events = deque(maxlen=10)

        # shard_id: List[datetime.datetime]
        # shows the last attempted IDENTIFYs and RESUMEs
        self.resumes = defaultdict(list)
        self.identifies = defaultdict(list)

    def _clear_gateway_data(self):
        one_week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
        for shard_id, dates in self.identifies.items():
            to_remove = [index for index, dt in enumerate(dates) if dt < one_week_ago]
            for index in reversed(to_remove):
                del dates[index]

        for shard_id, dates in self.resumes.items():
            to_remove = [index for index, dt in enumerate(dates) if dt < one_week_ago]
            for index in reversed(to_remove):
                del dates[index]

    async def on_socket_response(self, msg):
        self._prev_events.append(msg)

    async def before_identify_hook(self, shard_id, *, initial):
        self._clear_gateway_data()
        self.identifies[shard_id].append(datetime.datetime.utcnow())
        await super().before_identify_hook(shard_id, initial=initial)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.author.send('This command cannot be used in private messages.')
        elif isinstance(error, commands.DisabledCommand):
            await ctx.author.send('Sorry. This command is disabled and cannot be used.')
        elif isinstance(error, commands.CommandInvokeError):
            original = error.original
            if not isinstance(original, discord.HTTPException):
                print(f'In {ctx.command.qualified_name}:', file=sys.stderr)
                traceback.print_tb(original.__traceback__)
                print(f'{original.__class__.__name__}: {original}', file=sys.stderr)
        elif isinstance(error, commands.ArgumentParsingError):
            await ctx.send(error)
        else:
            print("Ignoring exception in command {}:".format(ctx.command), file=sys.stderr)
            traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)

    async def add_to_blacklist(self, object_id):
        await self.blacklist.put(object_id, True)

    async def remove_from_blacklist(self, object_id):
        try:
            await self.blacklist.remove(object_id)
        except KeyError:
            pass

    @discord.utils.cached_property
    def stats_webhook(self):
        wh_id, wh_token = self.config.stat_webhook
        hook = discord.Webhook.partial(id=wh_id, token=wh_token, adapter=discord.AsyncWebhookAdapter(self.session))
        return hook

    def log_spammer(self, ctx, message, retry_after, *, autoblock=False):
        guild_name = getattr(ctx.guild, 'name', 'No Guild (DMs)')
        guild_id = getattr(ctx.guild, 'id', None)
        fmt = 'User %s (ID %s) in guild %r (ID %s) spamming, retry_after: %.2fs'
        log.warning(fmt, message.author, message.author.id, guild_name, guild_id, retry_after)
        if not autoblock:
            return

        wh = self.stats_webhook
        embed = discord.Embed(title='Auto-blocked Member', colour=0xDDA453)
        embed.add_field(name='Member', value=f'{message.author} (ID: {message.author.id})', inline=False)
        embed.add_field(name='Guild Info', value=f'{guild_name} (ID: {guild_id})', inline=False)
        embed.add_field(name='Channel Info', value=f'{message.channel} (ID: {message.channel.id}', inline=False)
        embed.timestamp = datetime.datetime.utcnow()
        return wh.send(embed=embed)

    def get_guild_prefixes(self, guild, *, local_inject=_prefix_callable):
        proxy_msg = discord.Object(id=0)
        proxy_msg.guild = guild
        return local_inject(self, proxy_msg)
    
    def get_raw_guild_prefixes(self, guild_id):
        return self.prefixes.get(guild_id, ['?', '!'])

    async def set_guild_prefixes(self, guild, prefixes):
        if len(prefixes) == 0:
            self.prefixes[guild.id] = []
            await self.db.execute("UPDATE servers SET prefixes = $1 WHERE guild_id = $2", [], guild.id)
        elif len(prefixes) > 10:
            raise RuntimeError('Cannot have more than 10 custom prefixes.')
        else:
            self.prefixes[guild.id] = sorted(set(prefixes), reverse=True)
            await self.db.execute("UPDATE servers SET prefixes = $1 WHERE guild_id = $2", sorted(set(prefixes), reverse=True), guild.id)

    async def on_ready(self):
        if not hasattr(self, 'uptime'):
            self.uptime = datetime.datetime.utcnow()

        print(f'Ready: {self.user} (ID: {self.user.id})')

    async def on_resumed(self):
        print("Bot has resumed...")
        self.resumes[None].append(datetime.datetime.utcnow())

    async def on_guild_join(self, guild):
        owner = self.get_user(self.owner_id)
        if guild.id in self.blacklist:
                await guild.leave()
        test = await self.pool.fetchrow("SELECT * FROM servers WHERE guild_id = $1", guild.id)
        if not test:
            async with self.pool.acquire() as con:
                await con.execute("INSERT INTO servers (guild_id) VALUES ($1)", guild.id)
                await con.execute("INSERT INTO named_servers (guild_id, name) VALUES ($1, $2)", guild.id, guild.name)
            await self.set_guild_prefixes(guild, ['!', '?'])

        pfx = self.get_raw_guild_prefixes(guild.id)[0]
        if guild.system_channel:
            send_here = guild.system_channel
        else:
            for channel in guild.text_channels:
                if channel.overwrites_for(guild.default_role).read_messages:
                    send_here = channel
                    break
        embed = discord.Embed(title="Thanks for adding me to your server! :blush:", colour=discord.Colour.blurple())
        embed.description = f"""Robo VJ was originally made to keep scores during online quizzes, but has since evolved to support moderation commands and some fun here and there.
        For a full list of commands, use `{pfx}help`.
        
        Be mindful of hierarchy while using commands that involve assigning or removing roles, or editing nicknames. It is advisable to give the bot the highest role in the server if you are unfamiliar with Discord hierarchy and permission flow.
        
        Some easter egg commands are not included in the help page. Others like the `utils` group have been deliberately hidden because they are reserved for the bot owner.
        
        If you have any questions, or need help with the bot, or want to report bugs or request features, [click here](https://discord.gg/rqgRyF8) to join the support server."""
        embed.set_footer(text=f"Made by {owner}", icon_url=owner.avatar_url)
        await send_here.send(embed=embed)

    async def on_guild_update(self, before, after):
        if before.name != after.name:
            await self.pool.execute("UPDATE named_servers SET name = $1 WHERE guild_id = $2", after.name, after.id)

    async def process_commands(self, message):
        ctx = await self.get_context(message, cls=context.Context)

        if ctx.command is None:
            return

        if ctx.author.id in self.blacklist:
            return

        if ctx.guild is not None and ctx.guild.id in self.blacklist:
            return

        bucket = self.spam_control.get_bucket(message)
        current = message.created_at.replace(tzinfo=datetime.timezone.utc).timestamp()
        retry_after = bucket.update_rate_limit(current)
        author_id = message.author.id
        if retry_after and author_id != self.owner_id:
            self._auto_spam_count[author_id] += 1
            if self._auto_spam_count[author_id] >= 5:
                await self.add_to_blacklist(author_id)
                del self._auto_spam_count[author_id]
                await self.log_spammer(ctx, message, retry_after, autoblock=True)
            else:
                self.log_spammer(ctx, message, retry_after)
            return
        else:
            self._auto_spam_count.pop(author_id, None)
        try:
            await self.invoke(ctx)
        finally:
            # just in case we have any outstadning DB connections
            await ctx.release()

    async def on_message(self, message):
        if message.author.bot:
            return
        if message.content.strip() in [f'<@!{self.user.id}>', f'<@{self.user.id}>']:
            prefixes = _prefix_callable(self, message)
            # we want to remove prefix #2, because it's the 2nd form of the mention
            # and to the end user, this would end up making them confused why the
            # mention is there twice
            del prefixes[1]

            e = discord.Embed(title='Prefixes', colour=discord.Colour.blurple())
            e.set_footer(text=f'{len(prefixes)} prefixes')
            e.description = '\n'.join(f'{index}. {elem}' for index, elem in enumerate(prefixes, 1))
            await message.channel.send(embed=e)
        await self.process_commands(message)

    async def close(self):
        await super().close()
        await self.session.close()

    @tasks.loop(count=1)
    async def startup(self):
        #await bot.init_db()
        #await self.wait_until_ready()
        self.owner = self.get_user(self.owner_id)
        data = await self.pool.fetch("SELECT * FROM servers")
        for record in data:
            self.prefixes[record['guild_id']] = record['prefixes']
            self.qchannels[record['guild_id']] = record['qchannel']
            self.pchannels[record['guild_id']] = record['pchannel']
            self.modlogs[record['guild_id']] = record['modlog']

        print('Data loaded')

    @startup.before_loop
    async def before_startup(self):
        await self.wait_until_ready()

    def run(self):
        self.startup.start()
        try:
            super().run(config.token, reconnect=True)
        finally:
            with open('prev_events.log', 'w', encoding='utf-8') as fp:
                for data in self._prev_events:
                    try:
                        x = json.dumps(data, ensure_ascii=True, indent=4)
                    except:
                        fp.write(f'{data}\n')
                    else:
                        fp.write(f'{x}\n')
    
    @property
    def config(self):
        return __import__('config')