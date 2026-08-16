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

`terraform/terraform.tfvars` (gitignored — never commit this) with your AWS credentials:

```hcl
aws_access_key = "..."
aws_secret_key = "..."
```

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
update the `image:` fields in `k8s/api.yaml` and `k8s/frontend.yaml`, and the
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

## Tearing everything down

**Delete the Ingresses first.** The ALB is created by the load balancer controller, not by
Terraform, so Terraform doesn't know to remove it. If the controller is torn down first the
ALB is orphaned, and its ENIs then block subnet and VPC deletion with `DependencyViolation`
for ~15 minutes before failing:

```bash
kubectl delete ingress --all
kubectl get ingress          # wait until empty, ~1 min

cd terraform
terraform destroy
```

Expect 15–20 minutes — the node group and control plane are genuinely slow to delete. Don't
kill it partway: an interrupted destroy can remove the NAT gateway while the nodes are still
running, which strands the kubelets and leaves namespaces stuck `Terminating` forever.

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
