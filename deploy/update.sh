#!/bin/bash
# The entire deployment system: what ArgoCD's automated sync and selfHeal did,
# minus the cluster. Reconcile the box with git, reconcile the containers with
# the registry. Runs every five minutes from cuckootrade-update.timer, so a push
# to the deploy branch is live within that.
#
#   journalctl -u cuckootrade-update -f   # watch it work
#   systemctl start cuckootrade-update    # don't wait for the next tick
set -euo pipefail

REPO=/opt/cuckootrade
BRANCH=$(git -C "$REPO" rev-parse --abbrev-ref HEAD)

# git wins over anything edited on the box -- the selfHeal half of what ArgoCD
# used to do. Change the Caddyfile or the compose file with a commit, not with
# vi; an edit here survives exactly until the next tick. Untracked files are
# left alone, which is what keeps .env safe.
git -C "$REPO" fetch --quiet origin "$BRANCH"
git -C "$REPO" reset --quiet --hard "origin/$BRANCH"

cd "$REPO/deploy"

# :latest moves with every CI build, so this is the step that picks up a release.
# Non-fatal on purpose: a registry hiccup shouldn't stop us from bringing back
# up whatever is already cached locally.
docker compose pull --quiet || echo "pull failed; continuing with cached images" >&2

docker compose up -d --remove-orphans

# Layers orphaned by the pull. Dangling only -- tagged images are kept, so
# rolling back to a recent SHA doesn't have to re-download it.
docker image prune -f > /dev/null
