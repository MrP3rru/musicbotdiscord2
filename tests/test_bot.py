import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from limits import youtube_url, track_duration, DAILY_SECONDS, listener_stop_reason
from types import SimpleNamespace
from bot import MusicBot, register, extract


class LimitsTests(unittest.TestCase):
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
        self.assertEqual({c.name for c in bot.tree.get_commands()}, {'play', 'skip', 'stop', 'queue'})
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
            with self.assertRaises(asyncio.TimeoutError):
                await extract('https://www.youtube.com/watch?v=abcdefghijk')
        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()
