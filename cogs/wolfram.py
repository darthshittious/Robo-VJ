from typing import Mapping

from discord.ext import commands
from lxml import etree
from bot import RoboVJ

class Wolfram(commands.Cog):
    chars: Mapping[int, str] = {0xF74C: ' d', 0xF74D: 'e', 0xF74E: 'i', 0xF7D9: ' = '}

    def __init__(self, bot):
        self.bot = bot
        self.url: str = "http://api.wolframalpha.com/v2/query"
        self.key: str = self.bot.config.wolfram_api_key

    @commands.command(aliases=['wolf'])
    async def wolfram(self, ctx, *, inp):
        """Query Wolfram|Alpha."""
        async with ctx.typing():
            params = {'input': inp, 'appid': self.key, 'format': 'plaintext'}
            async with self.bot.session.get(self.url, params=params) as resp:
                text = await resp.text()
            root = etree.fromstring(text.encode(), etree.XMLParser())
            interpret = root.xpath(
                '//pod[@title="Input interpretation"]' '/subpod/plaintext/text()'
            )
            if not interpret:
                interpret = root.xpath(
                    '//pod[@title="Input"]' '/subpod/plaintext/text()'
                )
            if not interpret:
                interpret = ['']
            interpret = interpret[0]
            try:
                result = root.xpath(
                    '//pod[not(starts-with(@title, "Input"))]'
                    '/subpod/plaintext/text()'
                )[0]
            except IndexError:
                result = 'No results found.'
            if interpret:
                result = f'> {interpret}\n{result}'
            result = result.translate(self.chars)
        await ctx.send(result)


def setup(bot):
    bot.add_cog(Wolfram(bot))
