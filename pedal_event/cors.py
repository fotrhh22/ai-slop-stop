"""Allow the separate Vercel origin only on this event's API paths."""
import os
from urllib.parse import urlsplit

from starlette.middleware.cors import CORSMiddleware


def event_origins():
    value = os.getenv('PEDAL_APP_ORIGIN', '').strip().rstrip('/')
    if not value:
        return []
    parsed = urlsplit(value)
    local = parsed.hostname in ('localhost', '127.0.0.1')
    if (parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path
            or parsed.query or parsed.fragment or parsed.username or parsed.password
            or (parsed.scheme != 'https' and not local)):
        raise RuntimeError('PEDAL_APP_ORIGIN must be an exact HTTPS origin, or localhost for development')
    return [value]


class EventCORS:
    def __init__(self, app):
        self.app = app
        self.event = CORSMiddleware(app, allow_origins=event_origins(),
            allow_headers=['Authorization', 'Content-Type'], allow_methods=['GET', 'POST'])

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        selected = self.event if path == '/api/pedal' or path.startswith('/api/pedal/') else self.app
        await selected(scope, receive, send)
