#!/usr/bin/env bash
# Remove db.sqlite3 from the entire Git history.
#
# Usage:
#   chmod +x scripts/clean_git_history.sh
#   ./scripts/clean_git_history.sh
#
# After running, force-push all branches:
#   git push --force --all
#   git push --force --tags

set -euo pipefail

echo "==> Removing db.sqlite3 from Git history…"

if command -v git-filter-repo &>/dev/null; then
    git filter-repo --invert-paths --path db.sqlite3 --force
else
    echo "git-filter-repo not found. Falling back to git filter-branch."
    git filter-branch --force --index-filter \
        'git rm --cached --ignore-unmatch db.sqlite3' \
        --prune-empty --tag-name-filter cat -- --all

    echo "==> Cleaning up refs…"
    rm -rf .git/refs/original/
    git reflog expire --expire=now --all
    git gc --prune=now --aggressive
fi

echo "==> Done. Verify with:  git log --all --full-history -- db.sqlite3"
echo "==> Then force-push:   git push --force --all && git push --force --tags"
