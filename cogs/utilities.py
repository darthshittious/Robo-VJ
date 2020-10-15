"""
Utitlities cog for Discord bot
"""
import json
import datetime
import asyncpg
import asyncio
import re
import discord
from discord.ext import commands, tasks
from typing import Optional, Tuple

def check_util_perms(ctx):
    """
    Checks if user has "Manage server" permissions
    """
    if ctx.guild is None:
        return True
    return ctx.author.guild_permissions.manage_guild

def q_channel_protection(ctx):
    """
    Prevents question channel from being wiped by anyone other than owner
    """
    if ctx.guild is None:
        return True
    if ctx.guild.id not in ctx.bot.qchannels.keys():
        return True
    if ctx.channel.id == ctx.bot.qchannels[ctx.guild.id] and ctx.author == ctx.guild.owner:
        return True
    if ctx.channel.id != ctx.bot.qchannels[ctx.guild.id]:
        return True
    return False

class Utilities(commands.Cog):
    """Utilities for your server. Require manage server permissions to use"""
    def __init__(self, bot):
        self.bot = bot
        self.guild_state= {}

    @commands.command()
    @commands.guild_only()
    @commands.check(check_util_perms)
    @commands.check(q_channel_protection)
    async def wipe(self, ctx, limit):
        """
        Wipes a number of messages from current channel.
        (Upto 200 messages in the past 14 days.)
        """
        if limit.lower() == "all":
            limit = None
        else:
            try:
                limit = int(limit)
            except ValueError:
                await ctx.send("Please enter a valid number of messages to wipe (Add 2 to your originally intended number). Enter `all` to wipe the entire channel. (Upto 100 messages in the past 14 days.)")
                return
        if ctx.channel == self.bot.get_channel(self.bot.qchannels.get(ctx.guild.id)):
            msg = await ctx.send(f"React with \U00002705 to confirm wiping question channel.\nReact with \U0000274c to cancel.")
            await msg.add_reaction('\U00002705')
            await msg.add_reaction('\U0000274c')
            def check(reaction, user):
                return user == ctx.author and str(reaction.emoji) in ['\U00002705', '\U0000274c'] and reaction.message == msg
            try:
                reaction, user = await self.bot.wait_for('reaction_add', timeout=30.0, check=check)
            except asyncio.TimeoutError:
                await ctx.send("Timeout! Cancelling operation...", delete_after=20.0)
                await msg.delete()
                await ctx.message.delete()
            else:
                if str(reaction.emoji) == '\U0000274c':
                    await ctx.send("Cancelling...", delete_after=20.0)
                    await msg.delete()
                    await ctx.message.delete()
                elif str(reaction.emoji) == '\U00002705':
                    await msg.delete()
                    await ctx.message.delete()
                    await ctx.channel.purge(limit=limit)
        else:
            await ctx.message.delete()
            await ctx.channel.purge(limit=limit)


    @wipe.error
    async def wipe_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.message.delete()
            await ctx.send("Please enter a number of messages to wipe (Accomodate this message into your count). Enter `all` to wipe the entire channel. (Upto 100 messages in the past 14 days.)")


    @commands.command()
    @commands.guild_only()
    @commands.check(check_util_perms)
    async def prefix(self, ctx, pfx=None):
        """
        Change the prefix of the bot in this server, or return the current prefix if none specified.
        """
        #with open('readonly/prefixes.json', 'r') as file:
        #    prefixes = json.load(file)
        if pfx is None:
            await ctx.send(f"My prefix in this server is `{self.bot.prefixes.get(ctx.guild.id, '!')}`")
            return
        self.bot.prefixes[ctx.guild.id] = pfx
        await ctx.send(f"My prefix in this server is now `{pfx}`")
        test = await self.bot.db.fetchrow(f"SELECT prefix FROM servers WHERE guild_id = {ctx.guild.id}")
        connection = await self.bot.db.acquire()
        async with connection.transaction():
            if test:
                await self.bot.db.execute("UPDATE servers SET prefix = $1 WHERE guild_id = $2", pfx, ctx.guild.id)
            else:
                await self.bot.db.execute("INSERT INTO servers (guild_id, prefix) VALUES ($1, $2)", ctx.guild.id, pfx)
        await self.bot.db.release(connection)

        #prefixes[str(ctx.guild.id)] = pfx

        #with open('readonly/prefixes.json', 'w') as file:
        #    json.dump(prefixes, file, indent=4)



    @commands.command()
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def purgeroles(self, ctx, *, args):
        """Clears all roles of a particular member, or all members of a particular role.


            Syntax: purgeroles @member1 @member2
                    # or
                    purgeroles @member1 @member2 @role
                    # or
                    purgeroles @role"""
        members = []
        role = None
        for arg in args.split():
            if len(id := re.findall('<@!?([0-9]+)>', arg)) == 1:
                members.append(ctx.guild.get_member(int(id[0])))
            elif len(id := re.findall('<@&([0-9]+)>', arg)) == 1:
                role = ctx.guild.get_role(int(id[0]))
                break

        if role:
            if len(members) == 0:
                members = role.members
            for member in members:
                if role in member.roles:
                    try:
                        await member.remove_roles(role)
                    except discord.Forbidden:
                        await ctx.send(content=f"Cannot remove role {role.mention}. It is a higher or equal role to the bot.", delete_after=30.0)
                        return

                else:
                    await ctx.send(content=f"{member.mention} does not have the role {role.mention}. Ignoring...", delete_after=30.0)
                await asyncio.sleep(1)
        else:
            for member in members:
                await asyncio.sleep(1)
                if len(member.roles) > 3:
                    try:
                        await member.edit(roles=[])
                    except discord.Forbidden:
                        await ctx.send(f"{member.mention} has one or more roles higher than the bot. Please specify which roles to remove.", delete_after=30.0)
                else:
                    for role in member.roles[1:]:
                        await asyncio.sleep(1)
                        try:
                            await member.remove_roles(role)
                        except discord.Forbidden:
                            await ctx.send(content=f"Cannot remove role {role.mention}. Attempting to remove other roles...", delete_after=30.0)
                            continue
        if approved := discord.utils.get(ctx.guild.roles, name="Approved"): # For use in my server, and others if need be
            for member in members:
                await member.add_roles(approved)
                await asyncio.sleep(1)
        await ctx.send('Roles removed successfully')

    @purgeroles.error
    async def purgeroles_error(self, ctx, error):
        await ctx.send(error, delete_after=30.0)
        await ctx.send("Use `!help purgeroles` to get proper invocation syntax.", delete_after=30.0)

class OwnerOnly(commands.Cog, name="Server Owner Commands"):
    """Commands only guild owner can call"""
    def __init__(self, bot):
        self.bot = bot

    @commands.command(hidden=True)
    @commands.guild_only()
    async def approve(self, ctx, member:discord.Member):
        """Grants role Approved to the user"""
        if ctx.author == ctx.guild.owner or await self.bot.is_owner(ctx.author):
            if not discord.utils.get(ctx.guild.roles, name="Approved"):
                await ctx.send("Create role named 'Approved' and try again.")
                return
            if discord.utils.get(member.roles, name="Approved"):
                await ctx.send(f"{member.mention} is already approved.", delete_after=15.0)
                await ctx.message.delete()
                return
            try:
                await member.add_roles(discord.utils.get(ctx.guild.roles, name="Approved"))
            except discord.Forbidden:
                await ctx.send(f"I do not have permissions to approve members in `{ctx.message.guild.name}`. Make sure I have a role higher up than `Approved`")
                return
            await ctx.message.delete()
            embed = discord.Embed(title=f"Approved {member}", colour=discord.Colour.green(), timestamp=datetime.datetime.utcnow())
            embed.set_author(name=ctx.author, icon_url=ctx.author.avatar_url, url=f"https://discordapp.com/users/{ctx.author.id}")
            embed.set_footer(text=ctx.guild.name, icon_url=ctx.guild.icon_url)
            await ctx.guild.system_channel.send(embed=embed)

    @commands.command(hidden=True)
    @commands.guild_only()
    async def unapprove(self, ctx, member: discord.Member):
        """Revokes approval for a member"""
        if not ctx.author == ctx.guild.owner and not await self.bot.is_owner(ctx.author):
            return
        if not discord.utils.get(member.roles, name="Approved"):
            await ctx.send(f"{member.mention} has not been approved.", delete_after=15.0)
            await ctx.message.delete()
            return
        try:
            await member.remove_roles(discord.utils.get(member.roles, name="Approved"))
        except discord.Forbidden:
            await ctx.send("I do not have permissions to remove the `Approved` role. Does this member have higher roles than me?")
            return
        await ctx.message.delete()
        embed=discord.Embed(title=f"Revoked approval for {member}.", colour=0xFF0000, timestamp=datetime.datetime.utcnow())
        embed.set_author(name=ctx.author, icon_url=ctx.author.avatar_url, url=f"https://discordapp.com/users/{ctx.author.id}")
        embed.set_footer(text=ctx.guild.name, icon_url=ctx.guild.icon_url)
        await ctx.guild.system_channel.send(embed=embed)


    @commands.command()
    @commands.guild_only()
    async def prune(self, ctx, days:int, *roles):
        """
        Kick all members who haven't logged on in a certain nember of days, with optional roles.
        If a member has any roles that are not provided, they won't be kicked.
        The estimate provided does not take these roles into consideration.
        """
        if not ctx.author == ctx.guild.owner and not ctx.bot.is_owner(ctx.author):
            return
        if roles:
            rolelist = []
            for arg in roles:
                try:
                    role = await commands.RoleConverter().convert(ctx, arg)
                except commands.BadArgument:
                    return await ctx.send(f"Invalid role: {arg}")
                else:
                    rolelist.append(role)
            roles = rolelist
        estimate = await ctx.guild.estimate_pruned_members(days=days)
        msg = await ctx.send(f"After this operation, approximately {estimate} members will be pruned.\nReact with \U00002705 to confirm wiping question channel.\nReact with \U0000274c to cancel.")
        await msg.add_reaction('\U00002705')
        await msg.add_reaction('\U0000274c')
        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ['\U00002705', '\U0000274c'] and reaction.message == msg
        try:
            reaction, user = await self.bot.wait_for('reaction_add', timeout=30.0, check=check)
        except asyncio.TimeoutError:
            await ctx.send("Timeout! Cancelling operation...", delete_after=20.0)
            await msg.delete()
            await ctx.message.delete()
        else:
            if str(reaction.emoji) == '\U0000274c':
                await ctx.send("Cancelling...", delete_after=20.0)
                await msg.delete()
                await ctx.message.delete()
            elif str(reaction.emoji) == '\U00002705':
                await msg.delete()
                await ctx.message.delete()
                count = await ctx.guild.prune_members(days=days, roles=roles)
                await ctx.send(f"Operation complete. {count} members were pruned.")

    @commands.command()
    @commands.guild_only()
    async def qchannel(self, ctx):
        if not ctx.author == ctx.guild.owner and not await ctx.bot.is_owner(ctx.author):
            return
        channel = ctx.channel
        self.bot.qchannels[ctx.guild.id] = channel.id
        await ctx.send(f"{channel.mention} is marked as the questions channel and only the owner can wipe it.")

        test = await self.bot.db.fetchrow(f"SELECT qchannel FROM servers WHERE guild_id = {ctx.guild.id}")
        connection = await self.bot.db.acquire()
        async with connection.transaction():
            if test:
                await self.bot.db.execute(f"UPDATE servers SET qchannel = {channel.id} WHERE guild_id = {ctx.guild.id}")
            else:
                await self.bot.db.execute(f"""INSERT INTO servers (guild_id, qchannel) VALUES ({ctx.guild.id}, {channel.id})""")
        await self.bot.db.release(connection)

    @commands.command()
    @commands.guild_only()
    async def pchannel(self, ctx):
        if not ctx.author == ctx.guild.owner and not await ctx.bot.is_owner(ctx.author):
            return
        channel = ctx.channel
        self.bot.pchannels[ctx.guild.id] = channel.id
        await ctx.send(f"{channel.mention} is now the channel where pounces will appear.")

        test = await self.bot.db.fetchrow(f"SELECT pchannel FROM servers WHERE guild_id = {ctx.guild.id}")
        connection = await self.bot.db.acquire()
        async with connection.transaction():
            if test:
                await self.bot.db.execute(f"UPDATE servers SET pchannel = {channel.id} WHERE guild_id = {ctx.guild.id}")
            else:
                await self.bot.db.execute(f"""INSERT INTO servers (guild_id, pchannel) VALUES ({ctx.guild.id}, {channel.id})""")
        await self.bot.db.release(connection)

def setup(bot):
    bot.add_cog(OwnerOnly(bot))
    bot.add_cog(Utilities(bot))
