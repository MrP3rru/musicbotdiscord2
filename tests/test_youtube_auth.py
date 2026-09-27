import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from youtube_auth import cookie_arguments
from unittest.mock import AsyncMock, Mock
from bot import ytdlp_json


class CookieTests(unittest.TestCase):
    def test_secret_copy_is_private_filtered_and_removed(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'source.txt'
            contents = ('# Netscape HTTP Cookie File\n'
                        '#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tfake-test-session\n'
                        '.unrelated.example\tTRUE\t/\tTRUE\t0\tSID\tunrelated-secret\n')
            source.write_text(contents, encoding='utf-8')
            with patch.dict(os.environ, {'YOUTUBE_COOKIES_FILE': str(source)}):
                with cookie_arguments() as args:
                    self.assertEqual(args[0], '--cookies')
                    destination = Path(args[1])
                    self.assertNotEqual(source, destination)
                    self.assertIn('fake-test-session', destination.read_text())
                    self.assertNotIn('unrelated-secret', destination.read_text())
                    if os.name != 'nt':
                        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
                self.assertFalse(destination.exists())
            self.assertEqual(source.read_text(encoding='utf-8'), contents)


class AuthExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_anonymous_attempt_never_reads_cookie_secret(self):
        with patch('bot.cookie_arguments') as cookies, \
             patch('bot._ytdlp_json', AsyncMock(return_value={})) as extract:
            await ytdlp_json('https://www.youtube.com/watch?v=abcdefghijk', use_cookies=False)
        cookies.assert_not_called()
        self.assertEqual(extract.call_args.args[2], [])

    async def test_cookie_session_preserves_explicit_fallback_client(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'cookies.txt'
            source.write_text('# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tfake-secret\n')
            with patch.dict(os.environ, {'YOUTUBE_COOKIES_FILE': str(source)}), \
                 patch('bot._ytdlp_json', AsyncMock(return_value={})) as extract:
                await ytdlp_json('https://www.youtube.com/watch?v=abcdefghijk',
                                 '--extractor-args', 'youtube:player_client=web_safari')
            options = extract.call_args.args[1]
            self.assertIn('youtube:player_client=web_safari', options)
            self.assertNotIn('youtube:player_client=default', options)
            self.assertEqual(extract.call_args.args[2][0], '--cookies')

    async def test_subprocess_uses_copy_and_logs_no_session_data(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'cookies.txt'
            source.write_text('# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tfake-secret\n')
            proc = Mock(returncode=1)
            proc.communicate = AsyncMock(return_value=(b'', b'Unknown failure fake-secret'))
            with patch.dict(os.environ, {'YOUTUBE_COOKIES_FILE': str(source)}), \
                 patch('bot.asyncio.create_subprocess_exec', AsyncMock(return_value=proc)) as spawn, \
                 self.assertLogs('music', level='WARNING') as logs:
                with self.assertRaises(ValueError):
                    await ytdlp_json('ytsearch5:test', '--flat-playlist')
            args = spawn.call_args.args
            copied_file = Path(args[args.index('--cookies') + 1])
            self.assertNotEqual(copied_file, source)
            self.assertFalse(copied_file.exists())
            self.assertNotIn('fake-secret', '\n'.join(logs.output))

    def test_invalid_cookie_file_does_not_expose_content(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / 'bad.txt'
            source.write_text('some-secret-value', encoding='utf-8')
            with patch.dict(os.environ, {'YOUTUBE_COOKIES_FILE': str(source)}):
                with self.assertRaises(ValueError) as error:
                    with cookie_arguments():
                        self.fail('Invalid file accepted')
                self.assertIn('YT_COOKIES', str(error.exception))
                self.assertNotIn('some-secret-value', str(error.exception))

    def test_missing_explicit_file_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {'YOUTUBE_COOKIES_FILE': str(Path(root) / 'missing')}):
                with self.assertRaisesRegex(ValueError, 'YT_COOKIES'):
                    with cookie_arguments():
                        pass

    def test_optional_cookie_file_and_cleanup_on_failure(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict(os.environ, {'YOUTUBE_COOKIES_FILE': ''}), \
                 patch('youtube_auth.DEFAULT_COOKIE_FILE', str(Path(root) / 'missing')):
                with cookie_arguments() as args:
                    self.assertEqual(args, [])
            source = Path(root) / 'cookies.txt'
            source.write_text('# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tfake\n')
            with patch.dict(os.environ, {'YOUTUBE_COOKIES_FILE': str(source)}):
                with self.assertRaises(RuntimeError):
                    with cookie_arguments() as args:
                        destination = Path(args[1])
                        raise RuntimeError('simulated subprocess failure')
                self.assertFalse(destination.exists())
