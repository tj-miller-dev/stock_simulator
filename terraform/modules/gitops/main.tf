resource "kubernetes_namespace" "argocd" {
  metadata {
    name = var.namespace
  }
}

resource "helm_release" "argocd" {
  name       = "argocd"
  repository = "https://argoproj.github.io/argo-helm"
  chart      = "argo-cd"
  version    = var.chart_version
  namespace  = kubernetes_namespace.argocd.metadata[0].name

  # Bootstrap install -- ArgoCD manages everything else in the cluster from
  # here on via GitOps. Trimmed to the four components that actually do the
  # work, so the whole control plane fits on a single t3.medium with room to
  # spare. Re-enabling any of these is a one-line change.
  values = [yamlencode({
    # SSO/OIDC login (GitHub, Google, ...) for the ArgoCD *UI*. We sign in
    # with the local admin account from argocd-initial-admin-secret instead.
    # Unrelated to ArgoCD reading this repo -- repo-server clones that
    # itself, and would still need no help here if the repo went private.
    dex = { enabled = false }

    # Generates many Applications from git/list/matrix generators. There is
    # exactly one Application in this cluster (argocd/root-app.yaml), and it
    # is hand-written, so there is nothing to generate.
    applicationSet = { enabled = false }

    # Slack/email/webhook alerts on sync events. Never configured.
    notifications = { enabled = false }

    # The chart ships every component with empty `resources`, which leaves
    # them all BestEffort -- the first pods the kubelet evicts under memory
    # pressure, despite being the ones that cause it during a resync.
    # Requests but no limits (Burstable) so the controller and repo-server
    # can still spike while rendering manifests without being OOMKilled.
    controller = { resources = { requests = { cpu = "250m", memory = "512Mi" } } }
    repoServer = { resources = { requests = { cpu = "100m", memory = "256Mi" } } }
    server     = { resources = { requests = { cpu = "50m", memory = "128Mi" } } }
    redis      = { resources = { requests = { cpu = "50m", memory = "64Mi" } } }
  })]
}
