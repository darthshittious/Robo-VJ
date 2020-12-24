import json
import zlib
from functools import partial

import aiohttp
import aiohttp.http_exceptions

to_bytes = partial(bytes, encoding='utf-8')


def _to_tio_string(couple):
    name, obj = couple[0], couple[1]
    if not obj:
        return b''
    elif type(obj) == list:
        content = ['V' + name, str(len(obj))] + obj
        return to_bytes('\x00'.join(content) + '\x00')
    else:
        return to_bytes(f'F{name}\x00{len(to_bytes(obj))}\x00{obj}\x00')


class Tio:

    def __init__(self, language: str, code: str, inputs='', compiler_flags=(), command_line_options=(), args=()):
        self.backend = 'https://tio.run/cgi-bin/run/api'
        self.json = 'https://tio.run/languages.json'

        strings = {
            'lang': [language],
            '.code.tio': code,
            '.input.tio': inputs,
            'TIO_CFLAGS': list(compiler_flags),
            'TIO_OPTIONS': list(command_line_options),
            'args': list(args)
        }

        bytes_ = b''.join(map(_to_tio_string, zip(strings.keys(), strings.values()))) + b'R'

        # This returns a DEFLATE-compressed bytestring, which is what the API requires.
        self.request = zlib.compress(bytes_, 9)[2:-4]

    async def send(self):
        async with aiohttp.ClientSession() as client_session:
            async with client_session.post(self.backend, data=self.request) as resp:
                if resp.status != 200:
                    raise aiohttp.http_exceptions.HttpProcessingError(resp.status)

                data = await resp.read()
                data = data.decode('utf-8')
                return data.replace(data[:16], '')  # Remove token
