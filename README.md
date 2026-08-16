# Stock Simulator — Infra Setup

A basic app (FastAPI backend + React frontend) running in EKS with ArgoCD managing
deployments GitOps-style. Terraform provisions everything except the app itself:

- **Terraform** → VPC/subnets, EKS cluster + node group, AWS Load Balancer Controller,
  ECR repos, bare ArgoCD install.
- **ArgoCD** → watches `k8s/` on GitHub and syncs Deployments/Services/Ingress into the
  cluster. It does *not* build images — that's still a manual step below.
- **AWS Load Balancer Controller** → turns the Ingress objects in `k8s/` into a real
  ALB.

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

## 3. Build and push images to ECR

```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 307946643562.dkr.ecr.us-east-1.amazonaws.com

docker build --provenance=false --sbom=false -t 307946643562.dkr.ecr.us-east-1.amazonaws.com/stock-simulator-api:v0.2 ./api
docker push 307946643562.dkr.ecr.us-east-1.amazonaws.com/stock-simulator-api:v0.2

docker build --provenance=false --sbom=false -t 307946643562.dkr.ecr.us-east-1.amazonaws.com/stock-simulator-frontend:v0.1 ./frontend
docker push 307946643562.dkr.ecr.us-east-1.amazonaws.com/stock-simulator-frontend:v0.1
```

The `--provenance=false --sbom=false` flags avoid a confusing (but harmless) error —
without them, Docker also tries to push a build-attestation manifest under the same tag,
which gets rejected because the ECR repos have `image_tag_mutability = "IMMUTABLE"`.

**Tags are immutable.** If you rebuild an image, you cannot re-push the same tag —
either delete the old one first (`aws ecr batch-delete-image --repository-name <repo> --region us-east-1 --image-ids imageTag=<tag>`)
or bump the version and update the `image:` field in `k8s/api.yaml` / `k8s/frontend.yaml` to match.

The account ID (`307946643562`) is fixed to this AWS account — it won't change across a
destroy/recreate of the same account. If you ever deploy into a *different* AWS account,
update the `image:` fields in `k8s/api.yaml` and `k8s/frontend.yaml` accordingly.

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

## Tearing everything down

```bash
cd terraform
terraform destroy
```

**This will fail if the ECR repos still contain images** — AWS blocks deleting a
non-empty repository by default. Empty them first:

```bash
aws ecr batch-delete-image --repository-name stock-simulator-api --region us-east-1 --image-ids imageTag=v0.2
aws ecr batch-delete-image --repository-name stock-simulator-frontend --region us-east-1 --image-ids imageTag=v0.1
```

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
terraform/    infra as code — networking, cluster, registry, loadbalancer, gitops modules
argocd/       one-time bootstrap Application (applied manually, not synced by ArgoCD itself)
k8s/          app manifests ArgoCD actually syncs — Deployments, Services, Ingress
api/          FastAPI backend + Dockerfile
frontend/     React frontend + Dockerfile
```
