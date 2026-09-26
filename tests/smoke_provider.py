"""Manual network smoke test; does not connect to Discord or download the song."""
import asyncio
import logging
import os
from pathlib import Path
import sys
from aiohttp import ClientSession, ClientTimeout

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot import extract
from provider import token_provider


async def main():
    os.environ.setdefault('POT_SERVER_SCRIPT', str(Path('.venv/bgutil/server/build/main.js').resolve()))
    async with token_provider():
        info, duration = await extract('https://www.youtube.com/watch?v=XJPAk9ctb-Q')
        print(f'Audio resolved: format={info.get("format_id")} duration={duration}s')
        async with ClientSession(timeout=ClientTimeout(total=15)) as session:
            async with session.get(info['url'], headers={**info.get('http_headers', {}),
                                                       'Range': 'bytes=0-4095'}) as response:
                if response.status >= 400:
                    raise RuntimeError(f'Audio stream refused: HTTP {response.status} (URL omitted)')
                chunk = await response.content.read(4096)
                if not chunk:
                    raise RuntimeError('Audio stream is empty')
                print(f'Audio stream responds: HTTP {response.status}, received {len(chunk)} bytes')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
