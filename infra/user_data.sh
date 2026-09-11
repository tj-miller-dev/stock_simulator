#!/bin/bash
# Fired once, on first boot. Everything durable it sets up is either in the git
# clone at /opt/cuckootrade or in the systemd timer that keeps that clone and the
# running containers current -- so rebuilding this box is `terraform apply` after
# a taint, not a restore from anywhere.
#
# Changing this file replaces the instance (user_data_replace_on_change), because
# a running box that no longer matches its own boot script is exactly the drift
# this whole setup exists to avoid.
set -euxo pipefail

# Terraform associates the Elastic IP moments after the instance reaches
# `running`, which swaps out the auto-assigned public address and drops any TCP
# connection open at that instant -- quite possibly a dnf transaction or the git
# clone below. Retrying is what makes that a non-event instead of a box that
# boots into a half-installed state.
retry() {
  local n=0
  until "$@"; do
    n=$((n + 1))
    if [ "$n" -ge 10 ]; then
      echo "giving up after $n attempts: $*" >&2
      return 1
    fi
    echo "attempt $n failed, retrying in 15s: $*" >&2
    sleep 15
  done
}

# 1 GiB serves this workload comfortably but has no slack for a burst -- a docker
# pull unpacking layers, or numpy allocating on first import. Swap turns "the
# OOM killer reaped uvicorn mid-deploy" into "that took a few extra seconds".
if [ ! -f /swapfile ]; then
  dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

retry dnf update -y
retry dnf install -y docker git amazon-ecr-credential-helper

# The compose plugin isn't in the AL2023 repos, so it's installed straight from
# upstream as a docker CLI plugin. Deliberately unpinned: a 404 on a version that
# has aged out would fail the boot, and there is nothing here that depends on a
# specific compose release. Pin it if that tradeoff ever inverts.
mkdir -p /usr/libexec/docker/cli-plugins
retry curl -fsSL -o /usr/libexec/docker/cli-plugins/docker-compose \
  https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64
chmod +x /usr/libexec/docker/cli-plugins/docker-compose

systemctl enable --now docker
usermod -aG docker ec2-user

# ECR auth with no `docker login` and no stored password: the credential helper
# mints a token from the instance profile on every pull, so nothing expires at
# the 12-hour mark and there's no scheduled re-login to forget about.
mkdir -p /root/.docker /home/ec2-user/.docker
printf '{"credsStore":"ecr-login"}\n' > /root/.docker/config.json
cp /root/.docker/config.json /home/ec2-user/.docker/config.json
chown -R ec2-user:ec2-user /home/ec2-user/.docker

# git is the source of truth for the compose file and the Caddyfile, exactly as
# it was for the k8s manifests. The repo is public, so the box needs no
# credentials to read it.
if [ ! -d /opt/cuckootrade/.git ]; then
  retry git clone --branch ${deploy_branch} --single-branch ${repo_url} /opt/cuckootrade
fi

# The handful of values Terraform knows and git can't. Untracked and gitignored,
# so the update script's `git reset --hard` leaves it alone; docker compose reads
# it automatically because it sits next to the compose file.
cat > /opt/cuckootrade/deploy/.env <<'ENVEOF'
ECR_REGISTRY=${ecr_registry}
DOMAIN=${domain_name}
ACME_EMAIL=${acme_email}
ENVEOF

# Caddy writes access logs here through a bind mount, so they outlive any
# container replacement. This is the successor to the ALB's S3 log bucket -- see
# the note in docs/QUICK_START.md about what that trade costs.
mkdir -p /var/log/caddy

# The updater runs out of the git clone rather than being copied to /usr/local/bin,
# so `git push` updates the deployment script itself along with everything else.
# Invoked via bash explicitly: the execute bit doesn't survive a clone from a
# repo authored on Windows.
cat > /etc/systemd/system/cuckootrade-update.service <<'UNITEOF'
[Unit]
Description=Reconcile CuckooTrade with git and ECR
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash /opt/cuckootrade/deploy/update.sh
UNITEOF

cat > /etc/systemd/system/cuckootrade-update.timer <<'UNITEOF'
[Unit]
Description=Reconcile CuckooTrade every 5 minutes

[Timer]
OnBootSec=30s
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
UNITEOF

systemctl daemon-reload
systemctl enable --now cuckootrade-update.timer
