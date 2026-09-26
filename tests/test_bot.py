import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from limits import youtube_url, track_duration, DAILY_SECONDS, listener_stop_reason, playlist_url
from types import SimpleNamespace
from bot import (MusicBot, register, extract, resolve, recommendation, play_input, playlist_tracks,
                 youtube_failure, safe_diagnostic, ytdlp_json, stream_options)
from bot import PlaybackControls


class LimitsTests(unittest.TestCase):
    def test_stream_options(self):
        self.assertEqual(stream_options({}), '-nostdin -rw_timeout 15000000')
        opts = stream_options({'http_headers': {'User-Agent': 'TestAgent', 'Referer': 'https://youtube.com'}})
        self.assertIn('-user_agent TestAgent', opts)
        self.assertIn('-referer https://youtube.com', opts)
        bad_opts = stream_options({'http_headers': {'User-Agent': 'Evil\nAgent'}})
        self.assertNotIn('-user_agent', bad_opts)

    def test_diagnostic_classification_and_redaction(self):
        self.assertIn('audio/YT_LOGIN', youtube_failure("Sign in to confirm you're not a bot", 'audio'))
        self.assertIn('YT_FORMAT', youtube_failure('Requested format is not available', 'audio'))
        self.assertIn('YT_RATE_LIMIT', youtube_failure('HTTP Error 429: Too Many Requests', 'search'))
        with patch.dict('os.environ', {'DISCORD_TOKEN': 'test-secret-value'}):
            detail = safe_diagnostic('test-secret-value https://host.example/?key=secret cookie=private')
        self.assertNotIn('test-secret-value', detail)
        self.assertNotIn('host.example', detail)
        self.assertNotIn('private', detail)

    def test_playlist_input_and_single_video(self):
        expected = 'https://www.youtube.com/playlist?list=PLabc123'
        self.assertEqual(play_input('https://www.youtube.com/playlist?list=PLabc123'), expected)
        self.assertEqual(play_input('https://youtu.be/abcdefghijk?list=PLabc123'), expected)
        self.assertEqual(play_input('https://youtu.be/abcdefghijk'), 'https://www.youtube.com/watch?v=abcdefghijk')
        self.assertIsNone(playlist_url('reto ua'))
        for url in ['https://evil.com/playlist?list=PLabc', 'https://youtube.com/playlist?list=',
                    'https://youtube.com/playlist?list=PLabc&list=PLdef']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                play_input(url)

    def test_listener_states(self):
        def member(bot=False, **states):
            voice = dict(self_mute=False, mute=False, self_deaf=False, deaf=False)
            voice.update(states)
            return SimpleNamespace(bot=bot, voice=SimpleNamespace(**voice))
        self.assertIsNotNone(listener_stop_reason([]))
        self.assertIsNotNone(listener_stop_reason([member(bot=True)]))
        self.assertIsNone(listener_stop_reason([member(), member(self_mute=True)]))
        for flag in ['self_mute', 'mute', 'self_deaf', 'deaf']:
            with self.subTest(flag=flag):
                self.assertIsNotNone(listener_stop_reason([member(**{flag: True}), member(bot=True)]))
        self.assertIsNotNone(listener_stop_reason([member(self_mute=True), member(deaf=True)]))

    def test_urls(self):
        self.assertEqual(youtube_url('https://youtu.be/abcdefghijk?t=20'),
                         'https://www.youtube.com/watch?v=abcdefghijk')
        for url in ['http://youtu.be/abcdefghijk', 'https://evil.com/abcdefghijk',
                    'https://youtube.com/watch?v=abcdefghijk&list=abc',
                    'https://youtube.com@localhost/watch?v=abcdefghijk',
                    'https://youtube.com:443/watch?v=abcdefghijk']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                youtube_url(url)

    def test_duration(self):
        self.assertEqual(track_duration({'duration': 300}), 300)
        for info in [{'duration': 301}, {'duration': None}, {'duration': 10, 'is_live': True},
                     {'duration': 10, 'live_status': 'is_upcoming'}, {'duration': -1}]:
            with self.subTest(info=info), self.assertRaises(ValueError):
                track_duration(info)


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def test_playback_buttons_skip_stop_and_reject_stale(self):
        from unittest.mock import Mock
        bot = Mock()
        bot.lock = asyncio.Lock()
        bot.stop = AsyncMock()
        view = PlaybackControls(bot)
        self.assertEqual([button.label for button in view.children], ['Pomiń', 'Zatrzymaj'])
        bot.controls = view
        interaction = AsyncMock()
        await view.action(interaction, stop=False)
        bot.voice.stop.assert_called_once()
        self.assertTrue(bot.skipped)
        await view.action(interaction, stop=True)
        bot.stop.assert_awaited_once()
        bot.controls = None
        bot.voice.stop.reset_mock()
        await view.action(interaction, stop=False)
        bot.voice.stop.assert_not_called()
        self.assertIn('zakończonego', interaction.followup.send.call_args.args[0])
        await view.retire()
        self.assertTrue(all(button.disabled for button in view.children))

    async def test_buttons_require_same_voice_channel(self):
        from unittest.mock import Mock
        bot = Mock()
        bot.lock = asyncio.Lock()
        bot.check.side_effect = ValueError('Dołącz do kanału bota.')
        view = PlaybackControls(bot)
        interaction = AsyncMock()
        await view.action(interaction, stop=False)
        bot.voice.stop.assert_not_called()
        self.assertIn('Dołącz', interaction.followup.send.call_args.args[0])
        view.stop()

    async def test_extract_uses_mweb_and_local_po_provider(self):
        info = {'url': 'https://example.com/audio', 'duration': 100}
        with patch('bot.ytdlp_json', AsyncMock(return_value=info)) as fetch:
            await extract('https://www.youtube.com/watch?v=abcdefghijk')
        self.assertIn('youtube:player_client=mweb', fetch.call_args.args)
        self.assertIn('youtubepot-bgutilhttp:base_url=http://127.0.0.1:4416', fetch.call_args.args)

    async def test_format_fallback_is_bounded_and_login_is_not_retried(self):
        info = {'url': 'https://example.com/audio', 'duration': 100}
        with patch('bot.ytdlp_json', AsyncMock(side_effect=[ValueError('[audio/YT_FORMAT]'), info])) as fetch:
            self.assertEqual((await extract('url'))[1], 100)
            self.assertEqual(fetch.await_count, 2)
            self.assertNotIn('youtube:player_client=mweb', fetch.call_args.args)
        with patch('bot.ytdlp_json', AsyncMock(side_effect=ValueError('[audio/YT_LOGIN]'))) as fetch:
            with self.assertRaises(ValueError):
                await extract('url')
            self.assertEqual(fetch.await_count, 1)

    async def test_failed_extractor_reports_stage(self):
        from unittest.mock import Mock
        proc = Mock(returncode=1)
        proc.communicate = AsyncMock(return_value=(b'', b"ERROR: Sign in to confirm you're not a bot"))
        with patch('bot.asyncio.create_subprocess_exec', AsyncMock(return_value=proc)):
            with self.assertRaisesRegex(ValueError, 'search/YT_LOGIN'):
                await ytdlp_json('ytsearch5:reto ua', '--flat-playlist')

    async def test_playlist_bounded_order_and_filtering(self):
        data = {'entries': [
            {'id': 'aaaaaaaaaaa', 'duration': 200},
            {'id': 'bbbbbbbbbbb', 'duration': 600},
            {'id': 'aaaaaaaaaaa', 'duration': 200},
            {'id': 'ccccccccccc', 'duration': 150}, None]}
        with patch('bot.ytdlp_json', AsyncMock(return_value=data)) as fetch:
            urls, count = await playlist_tracks('https://www.youtube.com/playlist?list=PLabc')
        self.assertEqual(count, 5)
        self.assertEqual(urls, ['https://www.youtube.com/watch?v=aaaaaaaaaaa',
                               'https://www.youtube.com/watch?v=ccccccccccc'])
        self.assertEqual(fetch.call_args.args[-2:], ('--playlist-end', '10'))

    async def test_playlist_expansion_respects_existing_queue_capacity(self):
        from unittest.mock import Mock
        bot = MusicBot(123)
        bot.voice = Mock()
        bot.voice.channel.members = [SimpleNamespace(bot=False, voice=SimpleNamespace(
            self_mute=False, mute=False, self_deaf=False, deaf=False))]
        bot.voice.play.side_effect = lambda source, after: after(None)
        bot.autoplay = False
        channel = AsyncMock()
        bot.queue.extend([('https://www.youtube.com/playlist?list=PLabc', channel), ('manual', channel)])
        info = {'id': 'aaaaaaaaaaa', 'url': 'https://example.com/audio', 'title': 'Test'}
        async def extraction(value):
            self.assertLessEqual(len(bot.queue), 3)
            return info, 100
        with patch('bot.playlist_tracks', AsyncMock(return_value=(['one', 'two', 'three', 'four'], 4))), \
             patch('bot.resolve', AsyncMock(side_effect=lambda value: value)), \
             patch('bot.extract', AsyncMock(side_effect=extraction)) as extracts, \
             patch('bot.discord.FFmpegOpusAudio', Mock()):
            await bot.play_queue()
        self.assertEqual([call.args[0] for call in extracts.await_args_list], ['one', 'two', 'three', 'manual'])
        bot.voice = None
        await bot.close()

    async def test_finished_track_queues_mix_once_and_stops_on_error(self):
        from unittest.mock import Mock
        bot = MusicBot(123)
        bot.voice = Mock()
        bot.voice.channel.members = [SimpleNamespace(bot=False, voice=SimpleNamespace(
            self_mute=False, mute=False, self_deaf=False, deaf=False))]
        bot.voice.play.side_effect = lambda source, after: after(None)
        channel = AsyncMock()
        bot.queue.append(('first', channel))
        info = {'id': 'aaaaaaaaaaa', 'url': 'https://example.com/audio', 'title': 'Test'}
        with patch('bot.resolve', AsyncMock(side_effect=lambda value: value)), \
             patch('bot.extract', AsyncMock(side_effect=[(info, 100), ValueError('unavailable')])), \
             patch('bot.recommendation', AsyncMock(return_value='next')) as recommend, \
             patch('bot.discord.FFmpegOpusAudio', Mock()):
            await bot.play_queue()
        recommend.assert_awaited_once()
        self.assertFalse(bot.queue)
        self.assertIsNone(bot.current)
        self.assertEqual(bot.used, 100)
        bot.voice = None
        await bot.close()

    async def test_manual_queue_has_priority_and_disabled_autoplay_stops(self):
        from unittest.mock import Mock
        bot = MusicBot(123)
        bot.voice = Mock()
        bot.voice.channel.members = [SimpleNamespace(bot=False, voice=SimpleNamespace(
            self_mute=False, mute=False, self_deaf=False, deaf=False))]
        bot.voice.play.side_effect = lambda source, after: after(None)
        channel = AsyncMock()
        bot.queue.extend([('first', channel), ('second', channel)])
        info = {'id': 'aaaaaaaaaaa', 'url': 'https://example.com/audio', 'title': 'Test'}
        async def extraction(value):
            if value == 'second':
                bot.autoplay = False
            return info, 100
        with patch('bot.resolve', AsyncMock(side_effect=lambda value: value)), \
             patch('bot.extract', AsyncMock(side_effect=extraction)) as extract_mock, \
             patch('bot.recommendation', AsyncMock()) as recommend, \
             patch('bot.discord.FFmpegOpusAudio', Mock()):
            await bot.play_queue()
        self.assertEqual([call.args[0] for call in extract_mock.await_args_list], ['first', 'second'])
        recommend.assert_not_awaited()
        bot.voice = None
        await bot.close()

    async def test_search_selects_popular_eligible_result(self):
        data = {'entries': [
            {'id': 'aaaaaaaaaaa', 'duration': 200, 'view_count': 100},
            {'id': 'bbbbbbbbbbb', 'duration': 220, 'view_count': 1000},
            {'id': 'ccccccccccc', 'duration': 600, 'view_count': 10000}]}
        with patch('bot.ytdlp_json', AsyncMock(return_value=data)) as fetch:
            self.assertEqual(await resolve(play_input('reto ua')), 'https://www.youtube.com/watch?v=bbbbbbbbbbb')
            self.assertEqual(fetch.call_args.args[0], 'ytsearch5:reto ua')

    async def test_mix_excludes_history_and_preserves_order(self):
        data = {'entries': [
            {'id': 'aaaaaaaaaaa', 'duration': 200},
            {'id': 'bbbbbbbbbbb', 'duration': 220},
            {'id': 'ccccccccccc', 'duration': 150}]}
        with patch('bot.ytdlp_json', AsyncMock(return_value=data)):
            self.assertEqual(await recommendation('aaaaaaaaaaa', ['aaaaaaaaaaa']),
                             'https://www.youtube.com/watch?v=bbbbbbbbbbb')
            self.assertIsNone(await recommendation('aaaaaaaaaaa', ['aaaaaaaaaaa', 'bbbbbbbbbbb', 'ccccccccccc']))

    async def test_dynamic_channel_and_cross_channel_controls(self):
        from unittest.mock import Mock
        import discord
        bot = MusicBot(123)
        channel = Mock(spec=discord.VoiceChannel)
        channel.id = 456
        interaction = SimpleNamespace(guild_id=123, user=SimpleNamespace(
            voice=SimpleNamespace(channel=channel)))
        self.assertIs(bot.check(interaction), channel)
        bot.voice = SimpleNamespace(channel=SimpleNamespace(id=789))
        with self.assertRaises(ValueError):
            bot.check(interaction)
        bot.voice = None
        channel.id = 789
        self.assertIs(bot.check(interaction), channel)
        interaction.user.voice = None
        with self.assertRaises(ValueError):
            bot.check(interaction)
        await bot.close()

    async def test_auto_stop_clears_and_does_not_resume(self):
        from unittest.mock import Mock
        bot = MusicBot(123)
        bot.voice = AsyncMock()
        bot.voice.stop = Mock()
        bot.voice.channel.members = [SimpleNamespace(bot=False, voice=SimpleNamespace(
            self_mute=True, mute=False, self_deaf=False, deaf=False))]
        bot.text_channel = AsyncMock()
        text_channel = bot.text_channel
        voice = bot.voice
        bot.queue.append(('url', None))
        bot.worker = asyncio.create_task(asyncio.sleep(100))
        self.assertTrue(await bot.stop_if_unattended())
        voice.disconnect.assert_awaited_once_with(force=True)
        text_channel.send.assert_awaited_once()
        self.assertFalse(bot.queue)
        self.assertIsNone(bot.voice)
        self.assertIsNone(bot.worker)
        self.assertFalse(await bot.stop_if_unattended())
        await bot.close()

    async def test_active_listener_prevents_auto_stop(self):
        bot = MusicBot(123)
        bot.voice = SimpleNamespace(channel=SimpleNamespace(members=[SimpleNamespace(
            bot=False, voice=SimpleNamespace(self_mute=False, mute=False, self_deaf=False, deaf=False))]))
        self.assertFalse(await bot.stop_if_unattended())
        bot.voice = None
        await bot.close()

    async def test_commands_and_budget(self):
        bot = MusicBot(123)
        register(bot)
        self.assertEqual({c.name for c in bot.tree.get_commands()}, {'play', 'skip', 'stop', 'queue', 'autoplay'})
        self.assertEqual(bot.budget(), DAILY_SECONDS)
        bot.used = 100
        self.assertEqual(bot.budget(), DAILY_SECONDS - 100)
        bot.day = '2000-01-01'
        self.assertEqual(bot.budget(), DAILY_SECONDS)
        await bot.close()

    async def test_stop_cancels_worker_and_disconnects(self):
        bot = MusicBot(123)
        bot.voice = AsyncMock()
        from unittest.mock import Mock
        bot.voice.stop = Mock()
        voice = bot.voice
        bot.queue.append(('url', None))
        bot.worker = asyncio.create_task(asyncio.sleep(100))
        await bot.stop()
        voice.disconnect.assert_awaited_once_with(force=True)
        self.assertFalse(bot.queue)
        self.assertIsNone(bot.worker)
        self.assertIsNone(bot.voice)
        await bot.close()

    async def test_extract_timeout_kills_process(self):
        from unittest.mock import Mock
        proc = Mock(returncode=None)
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        proc.wait = AsyncMock()
        with patch('bot.asyncio.create_subprocess_exec', AsyncMock(return_value=proc)):
            with self.assertRaisesRegex(ValueError, 'YT_TIMEOUT'):
                await extract('https://www.youtube.com/watch?v=abcdefghijk')
        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()
