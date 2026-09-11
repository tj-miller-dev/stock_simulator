# Route53 registrar auto-creates a public hosted zone when you register a
# domain through it -- don't create a second one, just look it up.
data "aws_route53_zone" "this" {
  name         = var.domain_name
  private_zone = false
}

# Plain A records straight at the Elastic IP. Gone with the ALB: the ACM
# certificate (Caddy gets its own from Let's Encrypt), the alias records that
# pointed at the load balancer, and ExternalDNS -- the in-cluster controller
# that existed only because Terraform never knew the ALB's hostname. Terraform
# knows this address, so the indirection isn't needed.
#
# allow_overwrite matters on the first apply: ExternalDNS ran with
# `policy=upsert-only`, which never deletes, so its alias records for this
# domain outlive the cluster and would otherwise collide here.
resource "aws_route53_record" "apex" {
  zone_id         = data.aws_route53_zone.this.zone_id
  name            = var.domain_name
  type            = "A"
  ttl             = 300
  records         = [aws_eip.this.public_ip]
  allow_overwrite = true
}

resource "aws_route53_record" "www" {
  zone_id         = data.aws_route53_zone.this.zone_id
  name            = "www.${var.domain_name}"
  type            = "A"
  ttl             = 300
  records         = [aws_eip.this.public_ip]
  allow_overwrite = true
}
