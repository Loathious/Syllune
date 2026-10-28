# Set the single owned GitHub release destination in both bundled runtimes.
# This does not create a repository, authenticate, or publish any content.
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configure(value):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value) or value.lower() == 'drajb/whisper-local':
        raise ValueError('Use the dedicated Syllune owner/repo, not the original upstream project')
    for relative in ('overlay/brand.json', 'src/whisper_key/release.json'):
        target = ROOT / relative
        data = json.loads(target.read_text(encoding='utf-8'))
        data['repository'] = value
        target.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    configure(sys.argv[1])
