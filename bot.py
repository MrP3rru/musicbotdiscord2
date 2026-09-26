import asyncio
import contextlib
import datetime as dt
import json
import logging
import os
import subprocess
import sys
import time
from collections import deque

import discord
from discord import app_commands
from aiohttp import web

from limits import (MAX_TRACK, MAX_QUEUE, DAILY_SECONDS, IDLE_SECONDS, AUDIO_BITRATE,
                    youtube_url, track_duration, listener_stop_reason)

log = logging.getLogger('music')


async def extract(url):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'yt_dlp', '--ignore-config', '--no-playlist',
        '--skip-download', '--dump-single-json', '--no-warnings',
        '--socket-timeout', '10', '--retries', '1', '--extractor-retries', '1',
        '--no-cache-dir', '-f', 'bestaudio[abr<=80]/worstaudio', url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(proc.communicate(), 40)
        if proc.returncode:
            raise ValueError('YouTube nie udostępnił audio. Spróbuj innego filmu; nie ponawiam automatycznie.')
        info = json.loads(output)
        duration = track_duration(info)
        if not info.get('url', '').startswith('https://'):
            raise ValueError('Brak obsługiwanego strumienia audio.')
        return info, duration
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


class MusicBot(discord.Client):
    def __init__(self, guild_id, channel_id):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.tree = app_commands.CommandTree(self)
        self.guild_id, self.channel_id = guild_id, channel_id
        self.queue = deque()
        self.lock = asyncio.Lock()
        self.voice = None
        self.worker = None
        self.monitor = None
        self.current = None
        self.last_request = 0
        self.empty_since = None
        self.day = ''
        self.used = 0
        self.text_channel = None

    async def setup_hook(self):
        self.tree.copy_global_to(guild=discord.Object(id=self.guild_id))
        await self.tree.sync(guild=discord.Object(id=self.guild_id))
        self.monitor = asyncio.create_task(self.watch())

    def check(self, interaction):
        if interaction.guild_id != self.guild_id:
            raise ValueError('Bot działa tylko na skonfigurowanym serwerze.')
        state = getattr(interaction.user, 'voice', None)
        if not state or not state.channel or state.channel.id != self.channel_id:
            raise ValueError('Wejdź na kanał głosowy skonfigurowany dla bota.')

    async def stop_if_unattended(self):
        """Caller holds lock. Stop, destroy audio process, clear queue; never auto-resume."""
        if not self.voice:
            return False
        reason = listener_stop_reason(self.voice.channel.members)
        if reason:
            channel = self.text_channel
            await self.stop()
            if channel:
                await self.say(channel, f'⏹ {reason} Zatrzymano muzykę i wyczyszczono kolejkę. '
                               'Po powrocie i odciszeniu użyj /play z linkiem — muzyka nie wznowi się sama.')
            return True
        return False

    async def on_voice_state_update(self, member, before, after):
        if member.guild.id != self.guild_id:
            return
        if not any(channel and channel.id == self.channel_id for channel in (before.channel, after.channel)):
            return
        async with self.lock:
            await self.stop_if_unattended()

    def budget(self):
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        if today != self.day:
            self.day, self.used = today, 0
        return DAILY_SECONDS - self.used

    async def stop(self):
        self.queue.clear()
        if self.worker:
            self.worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.worker
            self.worker = None
        if self.voice:
            self.voice.stop()
            await self.voice.disconnect(force=True)
            self.voice = None
        self.current = None
        self.empty_since = None
        self.text_channel = None

    async def play_queue(self):
        try:
            while self.queue:
                url, text_channel = self.queue.popleft()
                self.current = 'Przygotowanie utworu…'
                source = None
                try:
                    if self.budget() < MAX_TRACK + 1:
                        raise ValueError('Wyczerpano dzisiejszy limit odtwarzania.')
                    info, duration = await extract(url)
                    if not self.voice or not self.voice.is_connected():
                        raise ValueError('Utracono połączenie z kanałem głosowym.')
                    if listener_stop_reason(self.voice.channel.members):
                        self.queue.clear()
                        raise ValueError('Brak aktywnych słuchaczy. Po odciszeniu uruchom /play ponownie.')
                    self.used += duration  # Reserve full duration, also for skipped tracks.
                    self.current = discord.utils.escape_markdown(info.get('title', 'Utwór')[:150])
                    source = discord.FFmpegOpusAudio(
                        info['url'], bitrate=AUDIO_BITRATE, codec='libopus',
                        before_options='-nostdin -rw_timeout 15000000',
                        options=f'-vn -threads 1 -vbr off -t {duration}',
                        stderr=subprocess.DEVNULL,
                    )
                    done = asyncio.Event()
                    loop = asyncio.get_running_loop()
                    errors = []
                    def after(error):
                        if error:
                            errors.append(type(error).__name__)
                        loop.call_soon_threadsafe(done.set)
                    self.voice.play(source, after=after)
                    await self.say(text_channel, f'▶ {self.current}')
                    await asyncio.wait_for(done.wait(), duration + 15)
                    if errors:
                        raise ValueError('Odtwarzanie zostało przerwane.')
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning('Track failed: %s', type(exc).__name__)
                    message = str(exc) if isinstance(exc, ValueError) else 'Nie udało się odtworzyć utworu.'
                    await self.say(text_channel, message)
                finally:
                    if self.voice:
                        self.voice.stop()
                    if source:
                        source.cleanup()
                    self.current = None
        finally:
            self.current = None

    async def say(self, channel, message):
        with contextlib.suppress(discord.HTTPException):
            await channel.send(message)

    async def watch(self):
        await self.wait_until_ready()
        while not self.is_closed():
            await asyncio.sleep(5)
            async with self.lock:
                if not self.voice:
                    continue
                if await self.stop_if_unattended():
                    continue
                idle = self.current is None and not self.queue
                if idle or not self.voice.is_connected():
                    if self.empty_since is None:
                        self.empty_since = time.monotonic()
                    if time.monotonic() - self.empty_since >= IDLE_SECONDS:
                        await self.stop()
                else:
                    self.empty_since = None

    async def close(self):
        if self.monitor:
            self.monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.monitor
        await self.stop()
        await super().close()


def register(bot):
    @bot.tree.command(name='play', description='Dodaj pojedynczy link YouTube (maks. 5 minut).')
    @app_commands.describe(link='Link HTTPS do filmu, bez playlisty')
    async def play(interaction: discord.Interaction, link: str):
        await interaction.response.defer(ephemeral=True)
        try:
            bot.check(interaction)
            url = youtube_url(link)
            async with bot.lock:
                channel = bot.get_channel(bot.channel_id)
                if not isinstance(channel, discord.VoiceChannel):
                    raise ValueError('VOICE_CHANNEL_ID musi wskazywać zwykły kanał głosowy.')
                reason = listener_stop_reason(channel.members)
                if reason:
                    raise ValueError(reason + ' Przynajmniej jedna osoba musi mieć włączony mikrofon i odsłuch.')
                if time.monotonic() - bot.last_request < 15:
                    raise ValueError('Odczekaj 15 sekund między dodawaniem utworów.')
                if len(bot.queue) >= MAX_QUEUE:
                    raise ValueError('Kolejka jest pełna (3 utwory).')
                if bot.budget() < MAX_TRACK + 1:
                    raise ValueError('Dzisiejszy limit odtwarzania został wyczerpany.')
                bot.last_request = time.monotonic()
                if not bot.voice or not bot.voice.is_connected():
                    if bot.voice:
                        await bot.voice.disconnect(force=True)
                    bot.voice = await channel.connect(timeout=20, reconnect=False, self_deaf=True)
                bot.queue.append((url, interaction.channel))
                bot.text_channel = interaction.channel
                bot.empty_since = None
                if not bot.worker or bot.worker.done():
                    bot.worker = asyncio.create_task(bot.play_queue())
            await interaction.followup.send('Dodano link. Sprawdzę długość i dostępność przed odtworzeniem.', ephemeral=True)
        except Exception as exc:
            log.warning('Play request failed: %s', type(exc).__name__)
            message = str(exc) if isinstance(exc, ValueError) else 'Nie udało się połączyć. Sprawdź uprawnienia bota i konfigurację kanału.'
            await interaction.followup.send(message, ephemeral=True)

    @bot.tree.command(name='skip', description='Pomiń odtwarzany utwór.')
    async def skip(interaction: discord.Interaction):
        try:
            bot.check(interaction)
            if bot.voice and bot.voice.is_playing():
                bot.voice.stop()
                message = 'Pominięto utwór.'
            else:
                message = 'Nie ma odtwarzanego utworu. Podczas przygotowania użyj /stop.'
        except ValueError as exc:
            message = str(exc)
        await interaction.response.send_message(message, ephemeral=True)

    @bot.tree.command(name='stop', description='Wyczyść kolejkę i rozłącz bota.')
    async def stop(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            bot.check(interaction)
            async with bot.lock:
                await bot.stop()
            message = 'Zatrzymano odtwarzanie i rozłączono bota.'
        except ValueError as exc:
            message = str(exc)
        await interaction.followup.send(message, ephemeral=True)

    @bot.tree.command(name='queue', description='Pokaż kolejkę i pozostały limit.')
    async def queue(interaction: discord.Interaction):
        try:
            bot.check(interaction)
            lines = [f'Teraz: {bot.current or "cisza"}', f'Pozostały limit: {bot.budget() // 60} min',
                     *[f'{i}. <{item[0]}>' for i, item in enumerate(bot.queue, 1)]]
            message = '\n'.join(lines)
        except ValueError as exc:
            message = str(exc)
        await interaction.response.send_message(message, ephemeral=True)


async def main():
    token = os.environ.get('DISCORD_TOKEN')
    if not token or not os.environ.get('GUILD_ID') or not os.environ.get('VOICE_CHANNEL_ID'):
        raise SystemExit('Ustaw DISCORD_TOKEN, GUILD_ID i VOICE_CHANNEL_ID w Environment na Render.')
    bot = MusicBot(int(os.environ['GUILD_ID']), int(os.environ['VOICE_CHANNEL_ID']))
    register(bot)
    app = web.Application()
    async def health(request):
        return web.json_response({'status': 'ok', 'discord_ready': bot.is_ready()})
    app.router.add_get('/health', health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', '10000'))).start()
    try:
        async with bot:
            await bot.start(token)
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    asyncio.run(main())
