"""Create and verify an online SQLite backup, including committed WAL data."""
import argparse
from contextlib import closing
from datetime import datetime
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time


def backup_database(source, directory, *, keep=14, timeout=60):
    source = Path(source).resolve()
    directory = Path(directory).resolve()
    if keep < 1:
        raise ValueError('keep must be at least 1')
    if not source.is_file():
        raise FileNotFoundError(source)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    destination = directory / f'db.sqlite3.{timestamp}'
    fd, temporary = tempfile.mkstemp(prefix='.sqlite-backup-', suffix='.tmp', dir=directory)
    os.close(fd)
    temporary = Path(temporary)
    started = time.monotonic()

    def progress(status, remaining, total):
        if time.monotonic() - started > timeout:
            raise TimeoutError('SQLite backup timed out; existing backups were retained')

    try:
        # Read-only URI also prevents creating a DB at a misspelled source path.
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as live:
            with closing(sqlite3.connect(temporary)) as backup:
                live.backup(backup, pages=256, progress=progress, sleep=0.1)
                if backup.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                    raise RuntimeError('SQLite backup integrity check failed')
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    # Only prune recognized backups after the new backup passes verification.
    backups = sorted(
        (p for p in directory.iterdir() if p.is_file() and not p.is_symlink()
         and re.fullmatch(r'db\.sqlite3\.\d{8}_\d{6}(?:_\d{6})?', p.name)),
        key=lambda p: p.name, reverse=True,
    )
    for old in backups[keep:]:
        if old.resolve() != source:
            old.unlink()
    return destination


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=root / 'db.sqlite3')
    parser.add_argument('--output', type=Path, default=root / 'backups')
    parser.add_argument('--keep', type=int, default=14)
    args = parser.parse_args()
    result = backup_database(args.database, args.output, keep=args.keep)
    print(f'Backup verified: {result} ({result.stat().st_size} bytes)')
