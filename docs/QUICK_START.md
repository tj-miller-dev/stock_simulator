# Stock Simulator — Infra Setup

A basic app (FastAPI backend + React frontend) running in EKS with ArgoCD managing
deployments GitOps-style. Terraform provisions everything except the app itself:

- **Terraform** → VPC/subnets, EKS cluster + node group, AWS Load Balancer Controller,
  ECR repos, bare ArgoCD install, and the IAM role GitHub Actions assumes via OIDC.
- **GitHub Actions** → on every push to `main` touching `api/` or `frontend/`, builds the
  changed image, pushes it to ECR tagged with the commit SHA, and writes that tag into
  `k8s/`.
- **ArgoCD** → watches `k8s/` on GitHub and syncs Deployments/Services/Ingress into the
  cluster. That tag-bump commit from CI is what triggers the rollout.
- **AWS Load Balancer Controller** → turns the Ingress objects in `k8s/` into a real
  ALB.

Day to day you don't touch any of this: push to `main`, and the change is live in a few
minutes. See [Deploying changes](#deploying-changes).

This doc assumes you're rebuilding from nothing (e.g. after a `terraform destroy`).

## Prerequisites

Installed locally: Terraform >= 1.9, AWS CLI v2, `kubectl`, Docker.

`terraform/terraform.tfvars` (gitignored — never commit this) with your AWS credentials and your own IP:

```hcl
aws_access_key      = "..."
aws_secret_key      = "..."
public_access_cidrs = ["x.x.x.x/32"]
```

Find your current public IP with `curl https://checkip.amazonaws.com`. This restricts the EKS
API's public endpoint (what `kubectl`/`terraform apply` use from your machine) to just you —
nodes and in-cluster controllers always reach it privately, regardless of this setting. If your
IP changes later, update this and re-apply, or you'll lose `kubectl`/`terraform` access to the
cluster until you do.

## 1. Provision infrastructure

```bash
cd terraform
terraform init
terraform apply
```

Takes ~15–20 minutes (mostly the EKS control plane). Note the outputs when it finishes —
`cluster_name`, `configure_kubectl`, `ecr_repository_urls`.

## 2. Point kubectl at the new cluster

```bash
aws eks update-kubeconfig --name stock-simulator --region us-east-1
kubectl get nodes   # should show Ready
```

## 3. Seed the first images

You don't build images by hand during normal work — CI does it. But a freshly rebuilt
cluster has empty ECR repos, and the tags currently referenced in `k8s/` don't exist yet,
so pods would sit in `ImagePullBackOff`. Trigger one manual run to build both services:

```bash
gh workflow run deploy.yml --ref main
gh run watch
```

A manual run always builds *both* services (there's no diff to path-filter on), pushes
them to ECR tagged with the commit SHA, and commits those tags into `k8s/`.

<details>
<summary>Building by hand instead (rarely needed)</summary>

```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 307946643562.dkr.ecr.us-east-1.amazonaws.com

docker build --provenance=false --sbom=false -t 307946643562.dkr.ecr.us-east-1.amazonaws.com/stock-simulator-api:v0.2 ./api
docker push 307946643562.dkr.ecr.us-east-1.amazonaws.com/stock-simulator-api:v0.2
```

**On Windows PowerShell the `docker login` above fails** with `400 Bad Request`. PowerShell
5.1 injects a UTF-8 BOM when piping between two native commands, which corrupts the
password. Let `cmd.exe` own the pipe instead:

```powershell
cmd /c "aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 307946643562.dkr.ecr.us-east-1.amazonaws.com"
```

Setting `$OutputEncoding` does *not* fix it. Git Bash works fine as-is.

`--provenance=false --sbom=false` avoids a confusing but harmless error — without them
Docker also pushes a build-attestation manifest under the same tag, which ECR rejects
because the repos are `IMMUTABLE`. The CI workflow sets the same two flags.

**Tags are immutable**, so you can't re-push one. Bump the version and update the `image:`
field in `k8s/api.yaml` / `k8s/frontend.yaml` to match.

</details>

The account ID (`307946643562`) is fixed to this AWS account — it won't change across a
destroy/recreate of the same account. If you ever deploy into a *different* AWS account,
update the `image:` fields in `k8s/api.yaml` and `k8s/frontend.yaml`, the
`access_logs.s3.bucket` attribute in `k8s/ingress.yaml` (the bucket name embeds the
account ID — `terraform output alb_access_logs_bucket` prints the correct value), and the
`ECR_REGISTRY` / `role-to-assume` values in `.github/workflows/deploy.yml`.

## 4. Bootstrap ArgoCD (one-time, manual)

`argocd/root-app.yaml` is deliberately *not* managed by Terraform (avoids a chicken-and-egg
problem with the Application CRD not existing yet in the same apply). It has to be applied
by hand once per cluster:

```bash
kubectl apply -f argocd/root-app.yaml
kubectl -n argocd get applications   # watch it sync
```

From this point on, ArgoCD watches the `main` branch of this repo's `k8s/` folder and
keeps the cluster in sync automatically (`prune` + `selfHeal`) — no more manual `kubectl apply`
for app changes, just `git push`.

## 5. Find your app's URL

```bash
kubectl get ingress
```

`api-ingress` and `frontend-ingress` share a single ALB (grouped via the
`alb.ingress.kubernetes.io/group.name` annotation) — use the `ADDRESS` column value.
Give it a minute or two after first creation for DNS to propagate and for target health
checks to pass.

```
http://<address>/            → frontend
http://<address>/api/hello   → api
```

## Deploying changes

Edit code under `api/` or `frontend/`, push to `main`, done:

```bash
git commit -am "add endpoint"
git push origin main
```

What happens next, all automatic:

1. `.github/workflows/deploy.yml` fires — but only for the service you actually touched.
   A frontend-only commit never rebuilds the api.
2. It assumes an AWS role via OIDC (no stored keys), builds the image, and pushes it to
   ECR tagged with the full commit SHA.
3. It rewrites the `image:` line in `k8s/api.yaml` or `k8s/frontend.yaml` and commits that
   back to `main` as `deploy api @ 8697824`.
4. ArgoCD notices the manifest change and rolls it out. It polls every ~3 minutes, so
   allow for that, or force it with `kubectl -n argocd annotate app stock-simulator argocd.argoproj.io/refresh=hard --overwrite`.

That bump commit only touches `k8s/`, which isn't in the workflow's path filter, so it
can't retrigger the pipeline. (Pushes made with `GITHUB_TOKEN` don't start workflow runs
either, so there are two independent guards against a loop.)

Because tags are commit SHAs, `kubectl get deploy api -o jsonpath='{..image}'` tells you
the exact commit running in the cluster, and rolling back is just reverting the manifest
commit.

### If a deploy doesn't land

```bash
gh run list --workflow deploy.yml --limit 5   # did CI pass?
kubectl -n argocd get app stock-simulator     # SYNC/HEALTH status
kubectl get pods                              # ImagePullBackOff = image never pushed
```

**`Could not assume role with OIDC: Not authorized to perform sts:AssumeRoleWithWebIdentity`**
means the token's `sub` claim doesn't match the role's trust policy. GitHub issues
*immutable* subject claims that embed numeric owner and repo IDs, so the value is pinned
in `var.github_repository_immutable`. If you rename the repo, transfer it, or point this
at a different repo, re-read the real value and re-apply:

```bash
gh api repos/OWNER/NAME/actions/oidc/customization/sub -q .sub_claim_prefix
```

To see what a failing run actually presented, rather than guessing:

```bash
aws cloudtrail lookup-events --lookup-attributes \
  AttributeKey=EventName,AttributeValue=AssumeRoleWithWebIdentity \
  --max-results 3 --query 'Events[].CloudTrailEvent' --output text
```

## Seeing who's actually using it

There is no analytics vendor and no tracking script. Usage is reconstructed from request
logs, which live in two places with very different lifespans:

| Where | Contains | Lifespan |
|---|---|---|
| **S3** — `stock-simulator-alb-logs-<account>` | Every request to *both* services at the ALB: client IP, path + query string, status, user agent, referer, bytes, latency | 90 days (lifecycle rule) |
| **Pod stdout** — `kubectl logs` | Same requests, per service, human-readable | Dies with the pod — a deploy, restart, or scale-down wipes it |

The S3 copy is the one that answers "are people coming back". Pod logs are for debugging
what's happening *right now*.

> **Order matters when enabling this.** The bucket and its policy come from Terraform, but
> the switch that turns logging on is the `access_logs.s3.*` attribute in
> `k8s/ingress.yaml`, which ArgoCD applies. If that annotation reaches the cluster before
> the bucket exists, the load balancer controller fails reconciliation with
> `InvalidConfigurationRequest: Access Denied for bucket` and stops applying *any* ingress
> change until it's fixed. Run `terraform apply` first, then merge the manifest. On a
> rebuild from scratch the normal step order already does this — Terraform is step 1,
> ArgoCD is step 4.

Two things are deliberately off, and both cost money for little return here: EKS
control-plane logging to CloudWatch (see `terraform/modules/cluster/main.tf`), and any
log-shipping agent. If you ever want pod logs to outlive their pod, that's the gap to fill.

### Reading the ALB logs

ALB flushes gzipped log files to S3 every 5 minutes. At this traffic level (~1k
requests/day) the whole corpus is small enough to just pull down and grep — Athena is
real setup effort and only starts paying off at a few million rows. Sync a day and
decompress:

```bash
BUCKET=$(cd terraform && terraform output -raw alb_access_logs_bucket)
aws s3 sync "s3://$BUCKET/alb/AWSLogs/307946643562/elasticloadbalancing/us-east-1/2026/08/19/" ./alblogs/
gunzip -c ./alblogs/*.gz > day.log
```

Fields are space-separated, but the interesting ones are *quoted* and contain spaces
themselves, so splitting on whitespace only works for the leading fields. Two different
awk separators are needed: `client:port` is whitespace field **4** and `elb_status_code`
is **9**, while splitting on `"` puts the request line in **2** and the user agent in
**4**. Some recipes:

```bash
# unique visitors in this day
awk '{print $4}' day.log | cut -d: -f1 | sort -u | wc -l

# busiest addresses -- a caller appearing across several synced days is your repeat traffic
awk '{print $4}' day.log | cut -d: -f1 | sort | uniq -c | sort -rn | head -20

# what symbols people ask for -- the actual product-usage signal
grep -o 'symbols\?=[^& "]*' day.log | sed 's/.*=//' | tr ',' '\n' | sort | uniq -c | sort -rn

# who is calling, minus health checkers and WordPress vulnerability scanners
grep -v 'ELB-HealthChecker\|wp-\|xmlrpc' day.log | awk -F'"' '{print $4}' | sort | uniq -c | sort -rn

# endpoints, with the query string stripped
awk -F'"' '{print $2}' day.log | awk '{print $2}' | sed 's/?.*//' | sort | uniq -c | sort -rn
```

**Discount the site's own traffic.** `frontend/src/ribbon.js` opens an SSE stream for its
nine ticker symbols on *every* page load, and `/playground` requests all eight magic
tickers — so `CUCKOO`, `SPY`, `CRASH`, `MOON`, `AAPL`, `NVDA`, `TSLA`, `PENNY`, `CHOPPY`
counts are mostly the landing page calling itself. Outside usage is the residue:
unfamiliar symbols, lowercase input, and non-browser user agents (`python-httpx`, `curl`,
`okhttp`, custom agent strings).

If the corpus does outgrow grep, AWS publishes the Athena `CREATE EXTERNAL TABLE` DDL for
this exact log format —
[Query ALB logs with Athena](https://docs.aws.amazon.com/athena/latest/ug/application-load-balancer-logs.html).

### Reading pod logs

```bash
kubectl logs -l app=api --tail=100      # one line per request, health checks excluded
kubectl logs -l app=frontend --tail=100 # nginx combined format; client IP is the last field
```

The API line is emitted by `cuckoo_middleware` in `api/api.py`, not uvicorn — uvicorn's
own access log reports the socket peer, which behind the ALB is a load balancer ENI in
`10.0.0.0/16` and identical for every caller. The middleware reads `X-Forwarded-For`
instead (last entry — the ALB appends the address it actually saw, so earlier entries are
caller-supplied and forgeable).

## Tearing everything down

Reverse the setup steps, in order. Step 4 was the last thing you did (bootstrap ArgoCD), so
it's the first thing to undo — **before** touching the Ingress:

```bash
# undo step 4: stop ArgoCD from managing the app
kubectl delete -f argocd/root-app.yaml

# undo the Ingress: now nothing will bring it back
kubectl delete ingress --all
kubectl get ingress          # wait until empty, ~1 min

# undo step 1: everything else
cd terraform
terraform destroy
```

**The `kubectl delete -f argocd/root-app.yaml` step is the one that matters.** Without it,
deleting the Ingress by hand doesn't work, because ArgoCD puts it right back. `syncPolicy`
in `argocd/root-app.yaml` has `selfHeal: true`, so ArgoCD sees the Ingress missing from the
cluster, re-applies it from `k8s/` within seconds, and the load balancer controller obediently
builds a brand new ALB — which Terraform has no idea exists, since it never created it.
CloudTrail from one such attempt:

```
20:50:02  DeleteLoadBalancer   ← kubectl delete ingress
20:50:24  CreateLoadBalancer   ← ArgoCD re-synced, 22s later
20:50:59  DeleteLoadBalancer   ← tried again
20:51:10  CreateLoadBalancer   ← and again, 11s later
```

You can't win that race by hand — the desired state lives in git, and the reconciler always
wins. Deleting the Application first removes the reconciler from the picture, so the Ingress
deletion sticks and `kubectl get ingress` coming back empty is trustworthy again.

`kubectl delete -f argocd/root-app.yaml` just removes ArgoCD's tracking of the app — no
`resources-finalizer` is set on it, so it doesn't cascade-delete anything itself. The actual
teardown of Deployments/Services/Ingress still happens the normal way, in the step after.

Expect 15–20 minutes for `terraform destroy` — the node group and control plane are genuinely
slow to delete. Don't kill it partway: an interrupted destroy can remove the NAT gateway while
the nodes are still running, which strands the kubelets and leaves namespaces stuck
`Terminating` forever.

### Cleaning up after a destroy that already failed this way

If you're reading this after already hitting the `DependencyViolation` error, the cluster and
ArgoCD are already gone — nothing is fighting you anymore, so just delete the orphaned ALB
directly and re-run destroy:

```bash
aws elbv2 describe-load-balancers --query 'LoadBalancers[].LoadBalancerArn' --output text
aws elbv2 delete-load-balancer --load-balancer-arn <arn>

cd terraform
terraform destroy
```

Give it a minute or two after the delete for the ALB's ENIs to detach before retrying destroy.

The ECR repos are `force_delete = true`, so Terraform empties them for you — no need to
delete image tags by hand (which stopped being practical once CI started tagging by SHA).

Destroying does **not** touch anything in `argocd/root-app.yaml` or `k8s/` (they're just
files in git) — after re-provisioning, redo step 4 to re-seed ArgoCD.

## ArgoCD access

```bash
# admin password
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d

# UI
kubectl -n argocd port-forward svc/argocd-server 8080:443
# open https://localhost:8080, log in as admin
```

## Repo layout

```
terraform/    infra as code — networking, cluster, registry, loadbalancer, gitops, cicd modules
.github/      build-and-deploy workflow (builds images, bumps the tags in k8s/)
argocd/       one-time bootstrap Application (applied manually, not synced by ArgoCD itself)
k8s/          app manifests ArgoCD actually syncs — Deployments, Services, Ingress
api/          FastAPI backend + Dockerfile
frontend/     React frontend + Dockerfile
```
