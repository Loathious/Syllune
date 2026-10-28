# Product identity shared by the engine and release tooling.
# The storage/package names stay stable to preserve existing recordings.
# A public repository is configured at release time, never guessed at runtime.

import json
from pathlib import Path

APP_NAME = 'Syllune'
VERSION = '0.24.1'


def repository() -> str:
    """Read only the bundled owner/repo identifier; no arbitrary update hosts."""
    import re
    config = Path(__file__).with_name('release.json')
    try:
        value = json.loads(config.read_text(encoding='utf-8')).get('repository', '')
    except (OSError, ValueError, TypeError):
        return ''
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value) else ''


def releases_url() -> str:
    value = repository()
    return f'https://github.com/{value}/releases' if value else ''
