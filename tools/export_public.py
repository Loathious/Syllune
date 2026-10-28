# Copy the files that belong in the public Syllune repository into a new
# folder, without this checkout's Git history, then audit them for personal
# data. The audit is supporting evidence for a manual review, not a guarantee.
# Usage: python tools/export_public.py <new-empty-directory>
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ---- What is left out ---------------------------------------------------------

# Assistant instructions, private planning notes and scratch output.
EXCLUDED_PREFIXES = ('.temp/', '.claude/', 'docs/superpowers/')
EXCLUDED_NAMES = ('CLAUDE.md', 'AGENTS.md')
# A manual renderer harness tied to one checkout's folder layout.
EXCLUDED_FILES = ('overlay/tests/pill-browser-check.js',)

# ---- What the audit looks for -------------------------------------------------

# Credential shapes, plus the identity of whoever runs the export: read from
# this machine, so the tool itself never carries anyone's personal details.
CREDENTIAL_PATTERNS = {
    'GitHub token': re.compile(r'\b(ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}'),
    'Hugging Face token': re.compile(r'\bhf_[A-Za-z0-9]{30,}'),
    'API key': re.compile(r'\bsk-[A-Za-z0-9_-]{20,}'),
    'private key': re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
}


def _git_config(key):
    result = subprocess.run(['git', 'config', key], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip()


# Each part of the local account name, the Git name and the Git email (not a
# GitHub noreply address, which is meant to be public) becomes a search term.
def personal_patterns():
    terms = {os.environ.get('USERNAME', ''), os.environ.get('USER', ''), Path.home().name}
    terms.update(_git_config('user.name').split())
    email = _git_config('user.email')
    if email and not email.endswith('users.noreply.github.com'):
        terms.add(email)
        terms.add(email.split('@')[-1])
    terms = sorted(t for t in terms if len(t) >= 4)
    patterns = dict(CREDENTIAL_PATTERNS)
    for term in terms:
        patterns[f'personal term #{terms.index(term) + 1}'] = re.compile(re.escape(term), re.I)
    return patterns
# Personal recordings and logs never belong in the repository.
FORBIDDEN_FILES = re.compile(r'(transcripts\.jsonl|app\.log|user_settings\.yaml|dictation_audio/|meetings?/.*\.(wav|flac|ogg))$', re.I)


def outgoing_files():
    listed = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                            cwd=ROOT, check=True, capture_output=True).stdout.decode('utf-8')
    for relative in sorted(set(filter(None, listed.split('\0')))):
        if (relative.startswith(EXCLUDED_PREFIXES) or Path(relative).name in EXCLUDED_NAMES
                or relative in EXCLUDED_FILES):
            continue
        if (ROOT / relative).is_file():          # skips files deleted in the working tree
            yield relative


def audit(folder: Path):
    findings = []
    patterns = personal_patterns()
    for path in sorted(p for p in folder.rglob('*') if p.is_file() and '.git' not in p.parts):
        relative = path.relative_to(folder).as_posix()
        if FORBIDDEN_FILES.search(relative):
            findings.append((relative, 0, 'personal data file'))
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue                              # binary assets: reviewed by hand
        for number, line in enumerate(text.splitlines(), 1):
            for label, pattern in patterns.items():
                if pattern.search(line):
                    findings.append((relative, number, label))
    return findings


def main(target):
    target = Path(target).resolve()
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f'{target} must be a new or empty folder')
    count = 0
    for relative in outgoing_files():
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
        count += 1
    findings = audit(target)
    print(f'Exported {count} files to {target}')
    for relative, number, label in findings:
        print(f'  {label}: {relative}:{number}')
    print(f'{len(findings)} audit findings' if findings else 'Audit: no findings')
    return 1 if findings else 0


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__ or 'Usage: python tools/export_public.py <new-empty-directory>')
    sys.exit(main(sys.argv[1]))
