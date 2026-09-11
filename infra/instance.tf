data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

resource "aws_key_pair" "this" {
  key_name   = var.name
  public_key = var.ssh_public_key
}

# The instance's only AWS permission is "read images from ECR". It holds no
# credentials on disk: the ECR credential helper installed in user_data trades
# this role for a registry token on every pull, so nothing expires at the
# 12-hour mark and there is no `docker login` to re-run.
resource "aws_iam_role" "instance" {
  name = "${var.name}-instance"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecr_read" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.name}-instance"
  role = aws_iam_role.instance.name
}

resource "aws_instance" "this" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.instance.id]
  key_name               = aws_key_pair.this.key_name
  iam_instance_profile   = aws_iam_instance_profile.instance.name

  # IMDSv2 required. The instance profile above is reachable from the metadata
  # endpoint, and token-less IMDSv1 is what turns an SSRF in a web app into
  # stolen role credentials.
  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_size
    encrypted   = true
  }

  user_data = templatefile("${path.module}/user_data.sh", {
    repo_url      = var.repo_url
    deploy_branch = var.deploy_branch
    ecr_registry  = local.ecr_registry
    domain_name   = var.domain_name
    acme_email    = var.acme_email
  })

  # user_data runs only on first boot, so a change to it is a change to the
  # machine image in every sense that matters -- replace the instance rather
  # than leaving a running box that no longer matches its own definition.
  user_data_replace_on_change = true

  tags = {
    Name = var.name
  }
}

# Static address so the Route53 records below survive an instance replacement
# without a DNS change. An Elastic IP attached to a *running* instance is free;
# one left dangling after you terminate the instance is not, which is the only
# way this stack can quietly start charging you again.
resource "aws_eip" "this" {
  domain = "vpc"

  tags = {
    Name = var.name
  }
}

resource "aws_eip_association" "this" {
  instance_id   = aws_instance.this.id
  allocation_id = aws_eip.this.id
}
