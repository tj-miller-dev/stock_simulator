# CuckooTrade (stock_simulator)

New here? Read [docs/OVERVIEW.md](docs/OVERVIEW.md) first — what this project is, who
it's for, current state, and the settled decision log. The active build contract is
[docs/V1_SPEC.md](docs/V1_SPEC.md). Infrastructure/deploy runbook:
[docs/QUICK_START.md](docs/QUICK_START.md). The root README is the public product pitch.

## Two branches, and only one of them deploys

This repo deliberately carries two different infrastructures, and which branch you are
on changes what is true:

| Branch | What it holds | Deploys? |
|---|---|---|
| `main` | The EKS + ArgoCD + Terraform build, kept intact as the DevOps portfolio piece | **No.** Nothing on `main` is running any more. |
| `simplify_deployment` | One t3.micro running three containers behind Caddy | **Yes.** This is what serves cuckootrade.com. |

**Do not merge `simplify_deployment` into `main`.** Doing so deletes the portfolio
stack, which is the one thing on `main` worth keeping. App code flows the other way:
land it on `main`, then merge `main` into this branch to ship it.

**Pushing to `simplify_deployment` deploys to production.** CI builds the touched
service's image and pushes it to ECR as `:latest`; the instance reconciles itself
against git and ECR every five minutes. Work on feature branches; merge here to ship.

`main`'s own `docs/QUICK_START.md` still describes the cluster, and its
`.github/workflows/deploy.yml` still points at `main` — that's correct for that branch.
Don't "fix" either to match this one.
