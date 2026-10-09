#!/usr/bin/env bash
# Fetch the pinned hermes-agent checkout the test suite imports
# (agent.secret_sources.base + the conformance kit) into .deps/.
# Idempotent: re-running verifies the pin and no-ops when already correct.
set -euo pipefail

HERMES_REPO="https://github.com/nousresearch/hermes-agent.git"
HERMES_PIN="6ab390a4764050bfed7f6ecb9d88c8e1d6377547"

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dest="$root_dir/.deps/hermes-agent"

if [[ -d "$dest/.git" ]]; then
    current="$(git -C "$dest" rev-parse HEAD)"
    if [[ "$current" == "$HERMES_PIN" ]]; then
        echo "hermes-agent already at pinned commit $HERMES_PIN"
        exit 0
    fi
    echo "hermes-agent at $current; re-fetching pin $HERMES_PIN"
    git -C "$dest" fetch --depth 1 origin "$HERMES_PIN"
    git -C "$dest" checkout --detach "$HERMES_PIN"
    exit 0
fi

mkdir -p "$root_dir/.deps"
git clone --no-checkout "$HERMES_REPO" "$dest"
git -C "$dest" checkout --detach "$HERMES_PIN"
echo "hermes-agent checked out at $HERMES_PIN"
