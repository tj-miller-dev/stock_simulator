# One VPC, one public subnet, one instance in it.
#
# What's gone versus the EKS stack: three private subnets, three public subnets,
# and the NAT gateway that gave the private ones egress. The NAT alone was ~$33/mo
# -- more than this entire stack now costs -- and it existed only because worker
# nodes sat in private subnets. A single public instance reaches the internet
# through the internet gateway directly, which is free.

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true

  tags = {
    Name = "${var.name}-vpc"
  }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = var.name
  }
}

resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.this.id
  availability_zone = data.aws_availability_zones.available.names[0]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 0)

  # The instance needs working egress the moment it boots -- user_data installs
  # docker and clones the repo before the Elastic IP below has been associated.
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.name}-public"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = {
    Name = "${var.name}-public"
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "instance" {
  name        = "${var.name}-instance"
  description = "Public web traffic, plus SSH from the operator"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = var.name
  }
}

# 80 stays open alongside 443 for two reasons: Caddy answers the Let's Encrypt
# HTTP-01 challenge on it, and it serves the redirect to HTTPS. Closing it breaks
# certificate renewal silently, ~60 days after you'd have stopped watching.
resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.instance.id
  description       = "HTTP -- ACME challenge and the redirect to HTTPS"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.instance.id
  description       = "HTTPS"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

# count, not for_each, because ssh_allowed_cidrs is sensitive: a for_each key
# becomes part of the resource address and would print the address in plan output
# and state listings. An index key keeps it out. The cost is that removing an
# entry from the middle of the list shifts the ones after it and recreates those
# rules, which for a one- or two-entry list of home IPs is free.
resource "aws_vpc_security_group_ingress_rule" "ssh" {
  count = length(var.ssh_allowed_cidrs)

  security_group_id = aws_security_group.instance.id
  description       = "SSH from the operator"
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = var.ssh_allowed_cidrs[count.index]
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.instance.id
  description       = "Outbound to ECR, GitHub, and the ACME certificate authority"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
