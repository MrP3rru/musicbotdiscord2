import asyncio
import unittest

from bot import youtube_failure, diagnostic_flags
from provider import _provider_log


class DiagnosticsTests(unittest.TestCase):
    def test_final_error_overrides_earlier_403_warning(self):
        detail = ('WARNING: formats might return HTTP 403\n'
                  'ERROR: Requested format is not available')
        self.assertIn('YT_FORMAT', youtube_failure(detail, 'audio'))
        self.assertIn('YT_FORBIDDEN', youtube_failure('ERROR: HTTP Error 403: Forbidden', 'audio'))

    def test_diagnostic_flags_never_contain_values(self):
        detail = 'Unable to download API page fake-secret; cookies have likely been rotated fake-secret'
        self.assertEqual(diagnostic_flags(detail), 'cookies_expired,player_api_failed')


class ProviderLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_logs_never_reveal_tokens_or_arbitrary_data(self):
        stream = asyncio.StreamReader()
        stream.feed_data(b'Started POT server v2.0.0\n'
                         b'Generated IntegrityToken: {"integrityToken":"fake-secret"}\n'
                         b'poToken: second-secret\n'
                         b'unexpected arbitrary-cookie-value\n'
                         b'Error: third-secret\n')
        stream.feed_eof()
        with self.assertLogs('music.provider', level='INFO') as logs:
            await _provider_log(stream)
        output = '\n'.join(logs.output)
        for value in ('fake-secret', 'second-secret', 'third-secret', 'arbitrary-cookie-value'):
            self.assertNotIn(value, output)
        self.assertIn('wartość ukryta', output)
