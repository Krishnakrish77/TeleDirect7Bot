#!/usr/bin/env sh
# Install this repo's version-controlled git hooks.
#
# Points git at scripts/hooks/ instead of the default .git/hooks/ (which is not
# version-controlled). Run once per clone:
#
#     sh scripts/install-hooks.sh
#
set -e
cd "$(dirname "$0")/.."
git config core.hooksPath scripts/hooks
chmod +x scripts/hooks/* 2>/dev/null || true
echo "Installed git hooks: core.hooksPath -> scripts/hooks"
echo "Pre-commit will lint templates. Bypass once with: git commit --no-verify"
