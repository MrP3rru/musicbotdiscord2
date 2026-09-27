"""Run the PO provider on loopback, and reap it when the bot exits."""
import asyncio
import contextlib
import logging
import os

from aiohttp import ClientSession, ClientTimeout, ClientError

log = logging.getLogger('music.provider')


async def _provider_log(stream):
    """Forward only bounded provider diagnostics to Render's ordinary logs."""
    lines = 0
    async for raw_line in stream:
        lines += 1
        if lines <= 50:
            log.info('PO: %s', raw_line.decode('utf-8', errors='replace').rstrip())
        elif lines == 51:
            log.warning('PO: dalsze komunikaty generatora zostały wyciszone.')


@contextlib.asynccontextmanager
async def token_provider():
    script = os.environ.get('POT_SERVER_SCRIPT', '/opt/pot/build/main.js')
    # The provider does not need the Discord or deployment credentials.
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {'PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT', 'SYSTEMDRIVE', 'TEMP', 'TMP', 'LANG'}}
    process = await asyncio.create_subprocess_exec(
        'node', '--max-old-space-size=128', script, '--host', '127.0.0.1', '--port', '4416',
        env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    output_task = asyncio.create_task(_provider_log(process.stdout))
    try:
        async with ClientSession(timeout=ClientTimeout(total=1)) as session:
            # First import of canvas/BgUtils can be slow on a cold free instance.
            for _ in range(180):
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
                raise RuntimeError('Generator PO nie odpowiada po 45 sekundach. Sprawdź wpisy „PO:” w logach Render.')
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
        output_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await output_task
