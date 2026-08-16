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

  # Bare bootstrap install -- ArgoCD manages everything else in the cluster
  # from here on via GitOps. Add `set {}` blocks or a values file here if
  # you need to customize before first sync (e.g. how the ArgoCD API/UI
  # itself gets exposed once an ingress controller exists).
}
