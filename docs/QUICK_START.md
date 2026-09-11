# CuckooTrade — Infra Setup (simple deployment)

> **This is the `simplify_deployment` branch.** It describes the infrastructure that
> actually serves cuckootrade.com: one small EC2 instance. The EKS/ArgoCD build lives on
> `main` and is kept as a portfolio piece — `main`'s copy of this file still documents
> the cluster, and that is correct for that branch. See [CLAUDE.md](../CLAUDE.md) for the
> branch model.

The whole system, end to end:

- **Terraform** (`infra/`) → a VPC with one public subnet, one `t3.micro`, an Elastic IP,
  two Route53 A records, the ECR repos, and the IAM role GitHub Actions assumes via OIDC.
- **GitHub Actions** → on every push to `simplify_deployment` touching `api/` or
  `frontend/`, builds the changed image and pushes it to ECR as both `:<commit-sha>` and
  `:latest`.
- **The instance** → a systemd timer runs `deploy/update.sh` every five minutes: pull the
  deploy branch, pull `:latest` from ECR, `docker compose up -d`. That is the entire
  deployment system.
- **Caddy** → terminates TLS with certificates it obtains from Let's Encrypt itself,
  serves `/api/*` to the api container and everything else to the frontend container.

Day to day you don't touch any of it: push to `simplify_deployment`, and the change is
live within about five minutes.

## Why it looks like this

It used to be EKS, and the bill didn't match the traffic — roughly two human visitors a
day against a control plane, a NAT gateway and a load balancer that each cost more per
month than the entire stack does now.

| | Before (EKS) | Now |
|---|---|---|
| EKS control plane | ~$73 | — |
| Worker node (`t3.medium`) | ~$30 | — |
| NAT gateway | ~$33 | — (public subnet, no NAT) |
| ALB | ~$16 | — (Caddy on the box) |
| `t3.micro` instance | — | ~$7.60 |
| EBS | ~$1.60 | ~$1.00 |
| Elastic IP (attached) | — | $0 |
| Route53 + ECR + S3 logs | ~$2 | ~$0.60 |
| **Approx. monthly** | **~$155** | **~$9** |

On-demand us-east-1 list prices, excluding data transfer and the domain registration.

What was given up, honestly:

- **Durable access logs.** The ALB wrote to S3, which survived anything. Caddy writes to
  `/var/log/caddy` on the instance's own disk with the same 90-day retention. Lose the
  box, lose the history.
- **Redundancy.** There was never much — one node, one replica pair — but now a reboot is
  visible downtime rather than a rescheduled pod. At this traffic level that is a fair
  trade.
- **Zero-downtime rollouts.** `docker compose up -d` replaces a container in place. A
  deploy is a couple of seconds of connection refused.

## Prerequisites

Installed locally: Terraform >= 1.9, AWS CLI v2, Docker (only for building by hand),
`gh` (only for triggering CI manually).

An SSH key pair, if you don't already have one you want to use:

```bash
ssh-keygen -t ed25519 -C cuckootrade -f ~/.ssh/cuckootrade
```

`infra/terraform.tfvars` (gitignored — never commit this):

```hcl
aws_access_key    = "..."
aws_secret_key    = "..."
ssh_allowed_cidrs = ["x.x.x.x/32"]                     # curl https://checkip.amazonaws.com
ssh_public_key    = "ssh-ed25519 AAAA... cuckootrade"  # contents of the .pub file
```

There is deliberately no ACME contact address here. Caddy registers an anonymous
Let's Encrypt account, which issues and renews the same certificates; the only thing
given up is that Let's Encrypt has no address to warn if renewal ever breaks. To add
one, put a global options block at the top of `deploy/Caddyfile` and push — no rebuild
needed, the box picks up `deploy/` changes on its next tick:

```
{
    email you@example.com
}
```

Only the public half of the key goes in here, so Terraform never holds a private key in
state. If your home IP changes later, update `ssh_allowed_cidrs` and re-apply — you lose
SSH until you do, but nothing else breaks: the site keeps serving and the update timer
keeps deploying, because neither needs you logged in.

## 0. Decommission the EKS stack — do this first

**This is the step that actually saves the money.** Gutting the repo changes nothing on
the AWS bill; the cluster bills until it is destroyed.

It also has to happen *before* step 1, because both stacks declare the same ECR
repository names and the same GitHub OIDC provider, and whichever is created second
collides with the first. Expect the site to be down for the half hour in between — at two
visitors a day, that is the cheapest part of this migration.

The EKS Terraform state is a local, gitignored `terraform.tfstate` in `terraform/`. It is
untracked, so it is still sitting there even though this branch removed the `.tf` files
next to it. Switch to `main` to get those files back:

```bash
git checkout main
```

Then, **in this order**:

```bash
# 1. Stop ArgoCD managing the app. This step is the one that matters.
kubectl delete -f argocd/root-app.yaml

# 2. Now the Ingress will stay deleted
kubectl delete ingress --all
kubectl get ingress          # wait until empty, ~1 min

# 3. Everything else
cd terraform
terraform destroy
```

**Why the ArgoCD delete comes first:** `argocd/root-app.yaml` sets `selfHeal: true`, so
deleting the Ingress by hand just makes ArgoCD re-apply it from git within seconds, and
the load balancer controller obediently builds a *brand new* ALB that Terraform has never
heard of. You cannot win that race by hand — the desired state lives in git and the
reconciler always wins. Deleting the Application first removes the reconciler from the
picture.

Expect 15–20 minutes. Don't interrupt it: a partial destroy can remove the NAT gateway
while nodes are still running, which strands the kubelets and leaves namespaces stuck
`Terminating`.

If a destroy already failed that way and left an orphaned load balancer behind:

```bash
aws elbv2 describe-load-balancers --query 'LoadBalancers[].LoadBalancerArn' --output text
aws elbv2 delete-load-balancer --load-balancer-arn <arn>
cd terraform && terraform destroy
```

Two leftovers Terraform won't clean up, because it never created them:

- **ExternalDNS's A records.** It ran with `policy=upsert-only`, which never deletes, so
  its alias records for the apex and `www` outlive the cluster. Step 1 sets
  `allow_overwrite = true` and takes them over, so you can ignore these.
- **ExternalDNS's TXT ownership records.** Harmless, but they will sit in the zone
  forever. Delete anything carrying a `heritage=external-dns` value:

  ```bash
  aws route53 list-resource-record-sets --hosted-zone-id <zone-id> \
    --query "ResourceRecordSets[?Type=='TXT']"
  ```

Finally, come back:

```bash
git checkout simplify_deployment
```

## 1. Provision

**Note the directory: `infra/`, not `terraform/`.** On this branch `terraform/` holds
only the spent EKS state and no `.tf` files at all, so running `terraform apply` in there
gets you `Error: No configuration files`. That separation is deliberate — it's what keeps
the two stacks' state files from colliding.

```bash
cd infra          # NOT terraform/
terraform init
terraform apply
```

You need `infra/terraform.tfvars` first — see [Prerequisites](#prerequisites). It is a
different variable set from the EKS stack's: same two AWS keys, but `public_access_cidrs`
becomes `ssh_allowed_cidrs`, and `ssh_public_key` is new.

Takes about two minutes — there is no control plane to wait for any more. Note the
outputs: `public_ip`, `ssh`, `site_url`, `github_actions_role_arn`.

If `github_actions_role_arn` or `ecr_registry` disagree with the values hardcoded in
`.github/workflows/deploy.yml`, update the workflow. That only happens if you change AWS
accounts or `var.name`.

## 2. Seed the first images

Step 0 destroyed the ECR repos, and `force_delete = true` means the images went with
them. Step 1 created empty ones. Until something is pushed, the instance's update timer
fails every five minutes with a pull error — harmless and self-correcting, it starts
working the moment images exist.

```bash
gh workflow run deploy.yml --ref simplify_deployment
gh run watch
```

A manual run always builds *both* services, since there is no diff to path-filter on.

## 3. Verify

Within five minutes of the images landing:

```bash
curl -I https://cuckootrade.com
curl -s https://cuckootrade.com/api/health
```

The first HTTPS request against a fresh box can take a few extra seconds while Caddy
completes the ACME handshake. If the name doesn't resolve at all yet, DNS is still
propagating — the records are new and carry a 300s TTL.

To watch the box converge:

```bash
ssh ec2-user@<public_ip> 'journalctl -u cuckootrade-update -f'
```

## Deploying changes

App code lands on `main` first — that is still the canonical branch for the product —
then comes here to ship:

```bash
git checkout simplify_deployment
git merge main
git push origin simplify_deployment
```

Or commit directly to this branch for deploy-only changes. Either way:

1. `.github/workflows/deploy.yml` fires, only for the service you actually touched. A
   frontend-only commit never rebuilds the api.
2. It assumes an AWS role via OIDC (no stored keys), builds the image, and pushes it to
   ECR as `:<commit-sha>` and `:latest`.
3. Within five minutes the instance's timer pulls `:latest` and `docker compose up -d`
   replaces the container.

There is no bump commit and no manifest rewrite any more — the moving `:latest` tag is
what carries the release. That is why the ECR repos are `MUTABLE` on this branch where
`main` had them `IMMUTABLE`.

Changes under `deploy/` — the Caddyfile, the compose file, the update script itself —
need no build at all. The instance pulls those straight from git on the same tick.

**git is authoritative on the box.** `update.sh` runs `git reset --hard` every five
minutes, so anything you edit under `/opt/cuckootrade` by hand is reverted on the next
tick. That is the selfHeal half of what ArgoCD used to do. The one exception is
`deploy/.env`, which is untracked and left alone.

### If a deploy doesn't land

```bash
gh run list --workflow deploy.yml --limit 5        # did CI pass?

ssh ec2-user@<ip>
systemctl status cuckootrade-update                # did the timer run, did it fail?
journalctl -u cuckootrade-update -n 50             # why
cd /opt/cuckootrade/deploy && docker compose ps    # what is actually up
docker compose logs --tail 50 api
```

**`Could not assume role with OIDC: Not authorized to perform sts:AssumeRoleWithWebIdentity`**
means the token's `sub` claim doesn't match the role's trust policy — almost always
because the branch name changed. `var.deploy_branch` in `infra/variables.tf`, the
`on.push.branches` filter in the workflow, and the branch checked out in
`/opt/cuckootrade` all have to agree. If you renamed or transferred the repo instead,
re-read the immutable subject and re-apply:

```bash
gh api repos/OWNER/NAME/actions/oidc/customization/sub -q .sub_claim_prefix
```

### Rolling back

Every build's commit-SHA tag is still in ECR and nothing ever overwrites one. Pin it:

```bash
ssh ec2-user@<ip>
cd /opt/cuckootrade/deploy
echo 'IMAGE_TAG=<sha>' >> .env
docker compose up -d
```

`.env` is untracked, so the timer won't undo this — which also means the pin is permanent
until you remove the line and let `:latest` take over again. Rolling back is the easy
half; remember to unpin.

## Operating the box

```bash
ssh ec2-user@<public_ip>

cd /opt/cuckootrade/deploy
docker compose ps                    # what is running
docker compose logs -f api           # one line per request, health checks excluded
docker compose logs -f caddy         # TLS and proxy errors
docker compose restart api           # without waiting for a timer tick

systemctl start cuckootrade-update   # force a reconcile now
systemctl list-timers                # when the next one fires

free -h                              # swap in steady use means t3.micro is undersized
df -h /                              # image churn; the timer prunes dangling layers
```

To rebuild the machine from scratch — an ordinary, supported operation, because nothing
on it is precious:

```bash
cd infra
terraform apply -replace=aws_instance.this
```

It reinstalls, re-clones and re-pulls in about two minutes. The Elastic IP and the DNS
records don't move. The one real cost is that Caddy re-issues its certificate, and Let's
Encrypt allows 5 identical certificates per week — so don't do this a dozen times in an
afternoon.

## Seeing who's actually using it

Still no analytics vendor and no tracking script: the audiences that matter — CI
pipelines, coding agents, curl — never execute JavaScript, so a page tracker would be
blind to exactly the traffic worth counting. Usage comes from Caddy's access log, the
successor to the ALB's S3 logs, with the same 90-day retention.

```bash
ssh ec2-user@<ip>
sudo ls /var/log/caddy/              # access.log plus rotated .gz files
```

It is JSON, one object per request, so `jq` does what `awk` did against the ALB format:

```bash
cd /var/log/caddy

# unique visitors in the current log
sudo jq -r '.request.client_ip' access.log | sort -u | wc -l

# busiest addresses -- a caller appearing across several days is your repeat traffic
sudo jq -r '.request.client_ip' access.log | sort | uniq -c | sort -rn | head -20

# what symbols people ask for -- the actual product-usage signal
sudo jq -r '.request.uri' access.log | grep -o 'symbols\?=[^& ]*' | sed 's/.*=//' \
  | tr ',' '\n' | sort | uniq -c | sort -rn

# who is calling, minus the vulnerability scanners
sudo jq -r '.request.headers["User-Agent"][0] // "-"' access.log \
  | grep -v 'wp-\|xmlrpc' | sort | uniq -c | sort -rn

# endpoints, query strings stripped
sudo jq -r '.request.uri' access.log | sed 's/?.*//' | sort | uniq -c | sort -rn
```

**Discount the site's own traffic.** `frontend/src/ribbon.js` opens an SSE stream for its
nine ticker symbols on *every* page load, and `/playground` requests all eight magic
tickers — so `CUCKOO`, `SPY`, `CRASH`, `MOON`, `AAPL`, `NVDA`, `TSLA`, `PENNY`, `CHOPPY`
counts are mostly the landing page calling itself. Outside usage is the residue:
unfamiliar symbols, lowercase input, and non-browser user agents (`python-httpx`, `curl`,
`okhttp`, custom agent strings).

To keep anything long-term, copy it off the box. This is the one place the old S3 bucket
was genuinely better:

```bash
scp "ec2-user@<ip>:/var/log/caddy/access.log*" ./logs/
```

## Tearing everything down

```bash
cd infra
terraform destroy
```

Two minutes, and no ordering traps — there is no reconciler fighting you and nothing
creates AWS resources out from under Terraform. The ECR repos are `force_delete = true`,
so their images go with them.

The one thing worth checking afterwards is the **Elastic IP**. Attached to a running
instance it is free; left allocated with nothing attached it bills by the hour.
`terraform destroy` releases it, but if you ever terminate the instance by hand without
destroying, that address is the thing that quietly keeps charging you:

```bash
aws ec2 describe-addresses --query 'Addresses[?AssociationId==`null`]'
```

## Repo layout

```
infra/        all infrastructure — flat, no modules: network, instance, registry, cicd, dns
deploy/       what runs on the box — compose file, Caddyfile, and the update script
.github/      build-and-push workflow (no deploy step; the box polls)
api/          FastAPI backend + Dockerfile
frontend/     static frontend + nginx Dockerfile
```

Not on this branch, by design: `terraform/`, `k8s/`, `argocd/`. Those are on `main`.
