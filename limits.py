import re
import math
from urllib.parse import urlsplit, parse_qs

MAX_TRACK = 300
MAX_QUEUE = 3
DAILY_SECONDS = 7200
IDLE_SECONDS = 60
AUDIO_BITRATE = 48
PLAYLIST_SCAN_LIMIT = 10


def playlist_url(value):
    """Return a canonical playlist URL, or None for a non-playlist input."""
    try:
        url = urlsplit(value.strip())
        query = parse_qs(url.query, keep_blank_values=True)
        if 'list' not in query:
            return None
        if (url.scheme != 'https' or url.username or url.password or url.port
                or url.hostname not in {'youtube.com', 'www.youtube.com', 'm.youtube.com',
                                        'music.youtube.com', 'youtu.be'}):
            raise ValueError()
        ids = query['list']
        if len(ids) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{2,100}', ids[0]):
            raise ValueError()
        return 'https://www.youtube.com/playlist?list=' + ids[0]
    except ValueError:
        raise ValueError('Podaj poprawny link HTTPS do playlisty YouTube.') from None


def listener_stop_reason(members):
    humans = [member for member in members if not member.bot]
    if not humans:
        return 'Kanał jest pusty.'
    if all(member.voice is None or any((member.voice.self_mute, member.voice.mute,
                                       member.voice.self_deaf, member.voice.deaf))
           for member in humans):
        return 'Wszyscy słuchacze mają wyciszony mikrofon lub odsłuch.'
    return None


def youtube_url(value):
    """Only canonical single-video URLs; never pass arbitrary URLs to the extractor."""
    try:
        url = urlsplit(value.strip())
        if url.scheme != 'https' or url.username or url.password or url.port:
            raise ValueError()
        query = parse_qs(url.query)
        if 'list' in query:
            raise ValueError()
        if url.hostname == 'youtu.be':
            video = url.path.lstrip('/')
        elif url.hostname in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'}:
            if url.path == '/watch':
                video = query.get('v', [''])[0]
            elif url.path.startswith('/shorts/'):
                video = url.path.split('/')[2]
            else:
                raise ValueError()
        else:
            raise ValueError()
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video):
            raise ValueError()
        return 'https://www.youtube.com/watch?v=' + video
    except ValueError:
        raise ValueError('Podaj pojedynczy link HTTPS do filmu YouTube, bez playlisty.') from None


def track_duration(info):
    duration = info.get('duration')
    if info.get('is_live') or info.get('live_status') in {'is_live', 'is_upcoming', 'post_live'}:
        raise ValueError('Transmisje na żywo są wyłączone.')
    if not isinstance(duration, (float, int)) or not 0 < duration <= MAX_TRACK:
        raise ValueError('Utwór musi mieć znaną długość, maksymalnie 5 minut.')
    return math.ceil(duration)
