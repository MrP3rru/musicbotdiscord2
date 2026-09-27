import asyncio
import contextlib
import datetime as dt
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
from collections import deque

import discord
from discord import app_commands
from aiohttp import web
from provider import token_provider
from youtube_auth import cookie_arguments

from limits import (MAX_TRACK, MAX_QUEUE, DAILY_SECONDS, IDLE_SECONDS, AUDIO_BITRATE,
                    youtube_url, track_duration, listener_stop_reason, playlist_url, PLAYLIST_SCAN_LIMIT)

log = logging.getLogger('music')


def stream_options(info):
    options = '-nostdin -rw_timeout 15000000'
    for header, flag in (('User-Agent', '-user_agent'), ('Referer', '-referer')):
        value = info.get('http_headers', {}).get(header)
        if value and '\r' not in value and '\n' not in value:
            options += f' {flag} {shlex.quote(value)}'
    return options


def safe_diagnostic(value):
    for key in ('DISCORD_TOKEN', 'RENDER_API_KEY', 'GH_TOKEN', 'GITHUB_TOKEN'):
        secret = os.environ.get(key)
        if secret:
            value = value.replace(secret, '[secret]')
    value = re.sub(r'https?://\S+', '[url]', value)
    value = re.sub(r'(?i)(authorization|cookie|token|password)\s*[:=]\s*\S+', r'\1=[redacted]', value)
    value = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', value)
    return value[-1600:]


def youtube_failure(detail, stage):
    lower = detail.lower().replace('’', "'")
    if 'not a bot' in lower or 'confirm you' in lower:
        code, message = 'YT_LOGIN', 'YouTube żąda potwierdzenia, że użytkownik nie jest botem. Serwer nie uzyskał dostępu.'
    elif '429' in lower or 'too many requests' in lower:
        code, message = 'YT_RATE_LIMIT', 'YouTube ograniczył liczbę żądań z serwera. Nie ponawiaj teraz komendy.'
    elif '403' in lower or 'forbidden' in lower:
        code, message = 'YT_FORBIDDEN', 'YouTube odrzucił dostęp do materiału (HTTP 403).'
    elif 'requested format is not available' in lower:
        code, message = 'YT_FORMAT', 'YouTube nie udostępnił obsługiwanego formatu audio.'
    elif any(word in lower for word in ('javascript runtime', 'challenge solving', 'n challenge', 'signature solving')):
        code, message = 'YT_JS', 'Nie udało się przetworzyć odtwarzacza YouTube; wymagana jest korekta ekstraktora.'
    elif any(word in lower for word in ('private video', 'video unavailable', 'not available', 'age-restricted', 'sign in')):
        code, message = 'YT_UNAVAILABLE', 'Materiał jest niedostępny lub wymaga zalogowania.'
    else:
        code, message = 'YT_EXTRACT', 'Nie udało się odczytać danych YouTube. Szczegóły zapisano w logach Render.'
    return f'{message} [{stage}/{code}]'


async def ytdlp_json(target, *options):
    with cookie_arguments() as auth_args:
        if auth_args:
            # Let yt-dlp select its authenticated client set after loading the jar.
            options = tuple('youtube:player_client=default' if option == 'youtube:player_client=mweb'
                            else option for option in options)
        return await _ytdlp_json(target, options, auth_args)


async def _ytdlp_json(target, options, auth_args):
    stage = 'search' if target.startswith('ytsearch') else ('playlist' if '--flat-playlist' in options else 'audio')
    log.info('YouTube stage=%s session_file=%s client=%s', stage,
             'loaded' if auth_args else 'absent',
             'mweb' if 'youtube:player_client=mweb' in options else 'default')
    proc = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'yt_dlp', '--ignore-config',
        '--js-runtimes', 'node',
        '--skip-download', '--dump-single-json',
        '--socket-timeout', '10', '--retries', '1', '--extractor-retries', '1',
        '--no-cache-dir', *auth_args, *options, '--', target,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        try:
            output, error = await asyncio.wait_for(proc.communicate(), 60 if stage == 'audio' else 40)
        except asyncio.TimeoutError:
            log.warning('YouTube stage=%s code=TIMEOUT', stage)
            raise ValueError(f'Przekroczono czas oczekiwania na YouTube. [{stage}/YT_TIMEOUT]') from None
        if proc.returncode:
            detail = (error or b'').decode('utf-8', errors='replace')
            message = youtube_failure(detail, stage)
            if auth_args:
                log.warning('YouTube stage=%s exit=%s auth=cookies error=%s', stage, proc.returncode, message)
            else:
                log.warning('YouTube stage=%s exit=%s details=%s', stage, proc.returncode, safe_diagnostic(detail))
            raise ValueError(message)
        return json.loads(output)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


def play_input(value):
    value = value.strip()
    if not value or len(value) > 200:
        raise ValueError('Podaj nazwę utworu lub link (maksymalnie 200 znaków).')
    if '://' in value or value.startswith(('www.', 'youtu.be/')):
        return playlist_url(value) or youtube_url(value)
    return value


def candidates(data, seen=()):
    results = []
    for entry in data.get('entries') or []:
        if not entry or entry.get('id') in seen:
            continue
        try:
            track_duration(entry)
            url = youtube_url('https://youtu.be/' + (entry.get('id') or ''))
        except ValueError:
            continue
        results.append((entry, url))
    return results


async def resolve(query):
    if query.startswith('https://'):
        return youtube_url(query)
    data = await ytdlp_json('ytsearch5:' + query, '--flat-playlist', '--playlist-end', '5')
    matches = candidates(data)
    if not matches:
        raise ValueError('Nie znaleziono pasującego utworu do 5 minut.')
    return max(matches, key=lambda item: item[0].get('view_count') or 0)[1]


async def recommendation(video_id, seen):
    url = youtube_url('https://youtu.be/' + video_id)
    data = await ytdlp_json(url + '&list=RD' + video_id, '--yes-playlist',
                             '--flat-playlist', '--playlist-end', '10')
    matches = candidates(data, seen)
    return matches[0][1] if matches else None


async def playlist_tracks(url):
    data = await ytdlp_json(playlist_url(url), '--yes-playlist', '--flat-playlist',
                             '--playlist-end', str(PLAYLIST_SCAN_LIMIT))
    entries = list(data.get('entries') or [])[:PLAYLIST_SCAN_LIMIT]
    # Preserve playlist order, but do not enqueue duplicate videos.
    matches = candidates({'entries': entries})
    urls = list(dict.fromkeys(item[1] for item in matches))
    return urls, len(entries)


async def extract(url):
    options = ('--no-playlist', '--extractor-args',
               'youtubepot-bgutilhttp:base_url=http://127.0.0.1:4416',
               '-f', 'bestaudio[abr<=80]/worstaudio')
    try:
        info = await ytdlp_json(url, *options, '--extractor-args', 'youtube:player_client=mweb')
    except ValueError as exc:
        if '[audio/YT_FORMAT]' not in str(exc):
            raise
        log.info('Brak formatu mweb; jedna próba standardowym klientem z generatorem PO.')
        info = await ytdlp_json(url, *options)
    duration = track_duration(info)
    if not info.get('url', '').startswith('https://'):
        raise ValueError('Brak obsługiwanego strumienia audio.')
    return info, duration


class PlaybackControls(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=MAX_TRACK + 60)
        self.bot = bot
        self.message = None

    async def retire(self):
        for button in self.children:
            button.disabled = True
        self.stop()
        if self.message:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(view=self)

    async def action(self, interaction, stop):
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.bot.lock:
                self.bot.check(interaction)
                if self.bot.controls is not self or not self.bot.voice or not self.bot.voice.is_playing():
                    raise ValueError('Ten panel dotyczy zakończonego utworu.')
                if stop:
                    await self.bot.stop()
                    text = 'Zatrzymano muzykę i wyczyszczono kolejkę.'
                else:
                    self.bot.skipped = True
                    self.bot.voice.stop()
                    text = 'Pominięto utwór.'
            await interaction.followup.send(text, ephemeral=True)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)

    @discord.ui.button(label='Pomiń', emoji='⏭', style=discord.ButtonStyle.primary)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.action(interaction, stop=False)

    @discord.ui.button(label='Zatrzymaj', emoji='⏹', style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.action(interaction, stop=True)


class MusicBot(discord.Client):
    def __init__(self, guild_id):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.tree = app_commands.CommandTree(self)
        self.guild_id = guild_id
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
        self.autoplay = True
        self.seen = deque(maxlen=100)
        self.skipped = False
        self.controls = None

    async def setup_hook(self):
        self.tree.copy_global_to(guild=discord.Object(id=self.guild_id))
        await self.tree.sync(guild=discord.Object(id=self.guild_id))
        self.monitor = asyncio.create_task(self.watch())

    def check(self, interaction):
        if interaction.guild_id != self.guild_id:
            raise ValueError('Bot działa tylko na skonfigurowanym serwerze.')
        state = getattr(interaction.user, 'voice', None)
        if not state or not isinstance(state.channel, discord.VoiceChannel):
            raise ValueError('Wejdź na zwykły kanał głosowy na tym serwerze.')
        if self.voice and self.voice.channel.id != state.channel.id:
            raise ValueError('Bot jest już na innym kanale. Dołącz do niego lub poczekaj na rozłączenie.')
        return state.channel

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
                               'Po powrocie i odciszeniu użyj /play — muzyka nie wznowi się sama.')
            return True
        return False

    async def on_voice_state_update(self, member, before, after):
        if member.guild.id != self.guild_id:
            return
        if not self.voice or not any(channel and channel.id == self.voice.channel.id
                                     for channel in (before.channel, after.channel)):
            return
        async with self.lock:
            await self.stop_if_unattended()

    def budget(self):
        today = dt.datetime.now(dt.timezone.utc).date().isoformat()
        if today != self.day:
            self.day, self.used = today, 0
        return DAILY_SECONDS - self.used

    async def stop(self):
        self.autoplay = False
        self.seen.clear()
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
                controls = None
                completed = False
                self.skipped = False
                try:
                    if self.budget() < MAX_TRACK + 1:
                        raise ValueError('Wyczerpano dzisiejszy limit odtwarzania.')
                    if playlist_url(url):
                        urls, scanned = await playlist_tracks(url)
                        # Recheck available space after network I/O: commands can add items meanwhile.
                        selected = urls[:1 + max(0, MAX_QUEUE - len(self.queue))]
                        if not selected:
                            raise ValueError('W pierwszych 10 pozycjach playlisty brak dostępnych utworów do 5 minut.')
                        self.queue.extendleft(reversed([(item, text_channel) for item in selected[1:]]))
                        url = selected[0]
                        await self.say(text_channel, f'Playlista: dodano {len(selected)} utworów, '
                                       f'pominięto {scanned - len(selected)} z {scanned} sprawdzonych. '
                                       f'Sprawdzam maksymalnie {PLAYLIST_SCAN_LIMIT} pierwszych pozycji; '
                                       'dalsza część playlisty nie jest importowana.')
                    url = await resolve(url)
                    info, duration = await extract(url)
                    if not self.voice or not self.voice.is_connected():
                        raise ValueError('Utracono połączenie z kanałem głosowym.')
                    if listener_stop_reason(self.voice.channel.members):
                        self.queue.clear()
                        raise ValueError('Brak aktywnych słuchaczy. Po odciszeniu uruchom /play ponownie.')
                    self.used += duration  # Reserve full duration, also for skipped tracks.
                    self.seen.append(info['id'])
                    self.current = discord.utils.escape_markdown(info.get('title', 'Utwór')[:150])
                    source = discord.FFmpegOpusAudio(
                        info['url'], bitrate=AUDIO_BITRATE, codec='libopus',
                        before_options=stream_options(info),
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
                    controls = PlaybackControls(self)
                    self.controls = controls
                    with contextlib.suppress(discord.HTTPException):
                        controls.message = await text_channel.send(
                            f'🎵 **Teraz gra:** {self.current}\n'
                            f'<{youtube_url("https://youtu.be/" + info["id"])}>\n'
                            f'Czas: {duration // 60}:{duration % 60:02d} • Opus {AUDIO_BITRATE} kb/s',
                            view=controls)
                    await asyncio.wait_for(done.wait(), duration + 15)
                    if errors:
                        raise ValueError('Odtwarzanie zostało przerwane.')
                    completed = not self.skipped
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
                    if controls:
                        if self.controls is controls:
                            self.controls = None
                        await controls.retire()
                if completed and self.autoplay and not self.queue and self.budget() >= MAX_TRACK + 1:
                    self.current = 'Szukanie kolejnego utworu w miksie YouTube…'
                    try:
                        next_url = await recommendation(info['id'], self.seen)
                        if (next_url and self.autoplay and not self.queue and self.voice
                                and self.voice.is_connected()
                                and not listener_stop_reason(self.voice.channel.members)):
                            self.queue.append((next_url, text_channel))
                        elif not next_url and self.autoplay and not self.queue:
                            await self.say(text_channel, 'Brak kolejnej propozycji w miksie YouTube. Użyj /play.')
                    except Exception as exc:
                        log.warning('Recommendations failed: %s', type(exc).__name__)
                        await self.say(text_channel, 'Miks YouTube jest niedostępny. Dodaj następny utwór przez /play.')
                    finally:
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
    @bot.tree.command(name='play', description='Podaj nazwę utworu, link do filmu lub playlisty YouTube.')
    @app_commands.describe(utwor='Np. reto ua, link do filmu lub playlisty YouTube')
    async def play(interaction: discord.Interaction, utwor: str):
        await interaction.response.defer(ephemeral=True)
        try:
            bot.check(interaction)
            url = play_input(utwor)
            async with bot.lock:
                channel = bot.check(interaction)
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
                    bot.autoplay = True
                bot.queue.append((url, interaction.channel))
                bot.text_channel = interaction.channel
                bot.empty_since = None
                if not bot.worker or bot.worker.done():
                    bot.worker = asyncio.create_task(bot.play_queue())
            await interaction.followup.send('Dodano utwór. Sprawdzę wyniki, długość i dostępność przed odtworzeniem.', ephemeral=True)
        except Exception as exc:
            log.warning('Play request failed: %s', type(exc).__name__)
            message = str(exc) if isinstance(exc, ValueError) else 'Nie udało się połączyć. Sprawdź uprawnienia bota i konfigurację kanału.'
            await interaction.followup.send(message, ephemeral=True)

    @bot.tree.command(name='skip', description='Pomiń odtwarzany utwór.')
    async def skip(interaction: discord.Interaction):
        try:
            bot.check(interaction)
            if bot.voice and bot.voice.is_playing():
                bot.skipped = True
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
                bot.check(interaction)
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
                     f'Autoplay: {"włączony" if bot.autoplay else "wyłączony"}',
                     *[f'{i}. {discord.utils.escape_markdown(item[0])}' for i, item in enumerate(bot.queue, 1)]]
            message = '\n'.join(lines)
        except ValueError as exc:
            message = str(exc)
        await interaction.response.send_message(message, ephemeral=True)

    @bot.tree.command(name='autoplay', description='Włącz lub wyłącz kolejne utwory z miksu YouTube.')
    async def autoplay(interaction: discord.Interaction, wlacz: bool):
        try:
            bot.check(interaction)
            bot.autoplay = wlacz
            message = 'Autoplay włączony.' if wlacz else 'Autoplay wyłączony; bieżący utwór i ręczna kolejka pozostają.'
        except ValueError as exc:
            message = str(exc)
        await interaction.response.send_message(message, ephemeral=True)


async def main():
    token = os.environ.get('DISCORD_TOKEN')
    if not token or not os.environ.get('GUILD_ID'):
        raise SystemExit('Ustaw DISCORD_TOKEN i GUILD_ID w Environment na Render.')
    bot = MusicBot(int(os.environ['GUILD_ID']))
    register(bot)
    app = web.Application()
    async def health(request):
        return web.json_response({'status': 'ok', 'discord_ready': bot.is_ready()})
    app.router.add_get('/health', health)
    app.router.add_get('/', health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', '10000'))).start()
    try:
        async with token_provider() as provider, bot:
            playback = asyncio.create_task(bot.start(token))
            provider_exit = asyncio.create_task(provider.wait())
            try:
                done, _ = await asyncio.wait((playback, provider_exit), return_when=asyncio.FIRST_COMPLETED)
                if provider_exit in done:
                    raise RuntimeError('Generator PO zatrzymał się. Usługa wymaga ponownego uruchomienia.')
                await playback
            finally:
                playback.cancel()
                provider_exit.cancel()
                await asyncio.gather(playback, provider_exit, return_exceptions=True)
    finally:
        await runner.cleanup()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    asyncio.run(main())
