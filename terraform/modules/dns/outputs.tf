output "certificate_arn" {
  value = aws_acm_certificate_validation.this.certificate_arn
}

output "hosted_zone_id" {
  value = data.aws_route53_zone.this.zone_id
}

output "name_servers" {
  value = data.aws_route53_zone.this.name_servers
}
