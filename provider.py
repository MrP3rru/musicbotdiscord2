"""Run the PO provider on loopback, and reap it when the bot exits."""
import asyncio
import contextlib
import logging
import os

from aiohttp import ClientSession, ClientTimeout, ClientError

log = logging.getLogger('music.provider')


@contextlib.asynccontextmanager
async def token_provider():
    script = os.environ.get('POT_SERVER_SCRIPT', '/opt/pot/build/main.js')
    # The provider does not need the Discord or deployment credentials.
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {'PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT', 'SYSTEMDRIVE', 'TEMP', 'TMP', 'LANG'}}
    process = await asyncio.create_subprocess_exec(
        'node', '--max-old-space-size=128', script, '--host', '127.0.0.1', '--port', '4416',
        env=environment, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        async with ClientSession(timeout=ClientTimeout(total=1)) as session:
            for _ in range(40):
                if process.returncode is not None:
                    raise RuntimeError(f'Generator PO zakończył pracę przy starcie (kod {process.returncode}).')
                try:
                    async with session.get('http://127.0.0.1:4416/ping') as response:
                        data = await response.json()
                        if response.status == 200 and data.get('version') == '2.0.0':
                            break
                except (ClientError, asyncio.TimeoutError, ValueError):
                    pass
                await asyncio.sleep(0.25)
            else:
                raise RuntimeError('Generator PO nie odpowiada na porcie lokalnym 4416.')
        log.info('Generator PO 2.0.0 gotowy (tylko localhost).')
        yield process
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
