# Commands for bot owner only.
import json
import datetime
import discord
from prettytable import PrettyTable
from discord.ext import commands
import io
import traceback
import asyncio
import inspect
import textwrap
import importlib
from contextlib import redirect_stdout
import os
import re
import sys
import copy
import time
from typing import Union, Optional



class MeOnly(commands.Cog, name="Bot owner specific commands"):
    def __init__(self, client):
        self.client = client
        self.AUTOAPPROVE = {}
        try:
            with open('assets/autoapprove_messages.json', 'r') as file:
                self.messages = json.load(file)
        except:
            open('assets/autoapprove_messages.json', 'w').close()
            self.messages = {}

        self._last_result = None

    def cleanup_code(self, content):
        """Automatically removes code blocks from the code."""
        # remove ```py\n```
        if content.startswith('```') and content.endswith('```'):
            return '\n'.join(content.split('\n')[1:-1])

        # remove `foo`
        return content.strip('` \n')

    async def cog_check(self, ctx):
        return await self.client.is_owner(ctx.author)

    def get_syntax_error(self, e):
        if e.text is None:
            return f'```py\n{e.__class__.__name__}: {e}\n```'
        return f'```py\n{e.text}{"^":>{e.offset}}\n{e.__class__.__name__}: {e}```'

    @commands.command(hidden=True)
    @commands.guild_only()
    async def autoapprove(self, ctx):
        if not ctx.author.id == 411166117084528640:
            return
        if not discord.utils.get(ctx.guild.roles, name="Approved"):
            await ctx.send("Create role named 'Approved' and try again.")
            return
        Text= "Since this server was originally created for a fundraiser, there is a system in place that only allows you full access to the server on being approved by a moderator.\n\n"
        Text += "For now, you may approve yourself by clicking on ✅.\n\nBy doing so, you agree to abide by these rules."
        await ctx.message.delete()
        message = await ctx.send(embed=discord.Embed(title='Verification', description=Text, colour = 0xFF0000))
        await message.add_reaction('✅')
        self.messages[str(ctx.guild.id)] = message.id
        with open('assets/autoapprove_messages.json', 'w') as file:
            json.dump(self.messages, file, indent=2)

    @commands.command(hidden=True)
    @commands.guild_only()
    async def pinrules(self, ctx):
        if not ctx.author.id == 411166117084528640:
            return
        with open("assets/server_rules.txt", "r") as file:
            rules = file.read().split('$')[1:]
        await ctx.message.delete()
        embed = discord.Embed(title=rules.pop(0), description=rules.pop(0), colour=0xFF0000)
        embed.set_author(name=str(ctx.author), icon_url=ctx.author.avatar_url, url=f"https://discordapp.com/users/{ctx.author.id}")
        embed.set_footer(text=ctx.guild.name, icon_url=ctx.guild.icon_url)
        for idx in range(0, len(rules), 2):
            embed.add_field(name=rules[idx], value=rules[idx + 1], inline=False)
        msg = await ctx.send(embed=embed)
        await msg.pin()

    @commands.command(hidden=True)
    @commands.guild_only()
    async def pinintro(self, ctx):
        if not ctx.author.id == 411166117084528640:
            return
        with open("assets/server_intro.txt", "r") as file:
            intro = file.read()
        await ctx.message.delete()
        msg = await ctx.send(intro)
        await msg.pin()

    @commands.command(hidden=True)
    @commands.guild_only()
    async def update(self, ctx, *args):
        if ctx.author.id != 411166117084528640:
            return
        if len(args) == 0:
            changelog == ""
        else:
            changelog = "```\n"
            for arg in args:
                changelog += f"  -  {arg}\n"
            changelog += "```"
        embed = discord.Embed(title=f"v{self.client.version} has been released.", colour = (0xFF0000), description="\n", timestamp=datetime.datetime.utcnow())
        embed.add_field(name="Changelog", value=changelog, inline=False)
        await ctx.message.delete()
        await ctx.send(embed=embed)


    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if str(payload.emoji) == "✅" and payload.guild_id and payload.user_id != self.client.user.id:
            guild = self.client.get_guild(payload.guild_id)
            member = guild.get_member(payload.user_id)
            if self.messages.get(str(payload.guild_id)) == payload.message_id:
                if not "approved" in [role.name.lower() for role in member.roles]:
                    try:
                        await member.add_roles(discord.utils.get(member.guild.roles, name="Approved"))
                    except discord.Forbidden:
                        await reaction.message.guild.owner.send(f"I do not have permissions to approve members here. Make sure I have a role higher up than `Approved`")
                        return
                channel = self.client.get_channel(payload.channel_id)
                message = await channel.fetch_message(payload.message_id)
                await message.remove_reaction('✅', member)

    @commands.command(hidden=True, aliases=['guilds', 'serverlist', 'server_list', 'guild_list', 'servers'])
    async def guildlist(self, ctx):
        if ctx.author.id != 411166117084528640:
            return
        def from_list(guild_list):
            guilds = PrettyTable()
            guilds.field_names = ["Name", "ID", "Owner", "Owner ID"]
            for guild in guild_list:
                guilds.add_row([guild.name, str(guild.id), f"{guild.owner.name}#{guild.owner.discriminator}", str(guild.owner.id)])
            return f"```{guilds}```"
        def chunks(lst, n):
            """Yield successive n-sized chunks from lst."""
            for i in range(0, len(lst), n):
                yield lst[i:i + n]
        idx = len(self.client.guilds)
        msgs = [from_list(self.client.guilds)]
        while not all([len(msg) < 2000 for msg in msgs]):
            idx //= 2
            splits = list(chunks(self.client.guilds, idx))
            msgs = [from_list(guildlist) for guildlist in splits]
        for msg in msgs:
            await ctx.send(msg)
            await asyncio.sleep(1)

    @commands.command(hidden=True, name='eval', aliases=['e', 'code', 'py', 'python', 'py3', 'python3', 'evaluate'])
    async def _eval(self, ctx, *, body: str):
        """Evaluates a code"""

        env = {
            'client': self.client,
            'ctx': ctx,
            'channel': ctx.channel,
            'author': ctx.author,
            'guild': ctx.guild,
            'message': ctx.message,
            '_': self._last_result
        }

        env.update(globals())

        body = self.cleanup_code(body)
        stdout = io.StringIO()

        to_compile = f'async def func():\n{textwrap.indent(body, "  ")}'

        try:
            exec(to_compile, env)
        except Exception as e:
            return await ctx.send(f'```py\n{e.__class__.__name__}: {e}\n```')

        func = env['func']
        try:
            with redirect_stdout(stdout):
                ret = await func()
        except Exception as e:
            value = stdout.getvalue()
            await ctx.send(f'```py\n{value}{traceback.format_exc()}\n```')
        else:
            value = stdout.getvalue()
            try:
                await ctx.message.add_reaction('\u2705')
            except:
                pass

            if ret is None:
                if value:
                    await ctx.send(f'```py\n{value}\n```')
            else:
                self._last_result = ret
                await ctx.send(f'```py\n{value}{ret}\n```')



def setup(client):
    client.add_cog(MeOnly(client))
