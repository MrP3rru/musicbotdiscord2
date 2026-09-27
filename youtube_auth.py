"""Read Render's cookie secret without modifying or logging the source file."""
from contextlib import contextmanager
from pathlib import Path
import os
import tempfile

DEFAULT_COOKIE_FILE = '/etc/secrets/youtube-cookies.txt'


@contextmanager
def cookie_arguments():
    source = Path(os.environ.get('YOUTUBE_COOKIES_FILE') or DEFAULT_COOKIE_FILE)
    if not source.exists() and not os.environ.get('YOUTUBE_COOKIES_FILE'):
        yield []
        return
    try:
        with source.open('rb') as stream:
            raw = stream.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError()
        content = raw.decode('utf-8-sig')
        if not content.startswith(('# Netscape HTTP Cookie File', '# HTTP Cookie File')):
            raise ValueError()
        selected = []
        for line in content.splitlines()[1:]:
            if not line or (line.startswith('#') and not line.startswith('#HttpOnly_')):
                continue
            fields = line.removeprefix('#HttpOnly_').split('\t')
            if len(fields) != 7 or fields[1] not in {'TRUE', 'FALSE'} or fields[3] not in {'TRUE', 'FALSE'}:
                raise ValueError()
            if fields[4] and not fields[4].isdigit():
                raise ValueError()
            domain = fields[0].lstrip('.').lower()
            if domain == 'youtube.com' or domain.endswith('.youtube.com'):
                selected.append(line)
        if not selected:
            raise ValueError()
    except (OSError, UnicodeError, ValueError):
        raise ValueError('Nie można odczytać cookies YouTube. Sprawdź Secret File i format Netscape. [YT_COOKIES]') from None
    # yt-dlp writes its cookie jar on exit; Render's mounted secret is read-only.
    with tempfile.TemporaryDirectory(prefix='music-yt-') as directory:
        destination = Path(directory) / 'cookies.txt'
        with destination.open('x', encoding='utf-8', newline='\n') as stream:
            os.chmod(destination, 0o600)
            stream.write('# Netscape HTTP Cookie File\n' + '\n'.join(selected) + '\n')
        yield ['--cookies', str(destination)]
