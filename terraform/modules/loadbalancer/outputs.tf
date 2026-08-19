output "access_logs_bucket" {
  description = "Bucket the ALB writes access logs into. k8s/ingress.yaml hardcodes this name (ArgoCD applies that file verbatim, so it can't be interpolated) -- if this output and the annotation ever disagree, the annotation wins and log delivery breaks."
  value       = aws_s3_bucket.access_logs.bucket
}

output "access_logs_prefix" {
  description = "Key prefix under which log objects land, matching access_logs.s3.prefix in k8s/ingress.yaml"
  value       = local.access_logs_prefix
}
