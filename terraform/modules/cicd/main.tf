# GitHub's OIDC identity provider. Lets a workflow run in this repo trade its
# short-lived Actions token for AWS credentials -- no access keys sitting in
# GitHub secrets, nothing to rotate.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]

  # AWS validates GitHub's certificate against its own trusted CA list and
  # effectively ignores these, but the argument is still required. Both of
  # GitHub's published intermediates are listed so a rotation on their end
  # doesn't break an apply.
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd",
  ]
}

data "aws_iam_policy_document" "assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Scoped to pushes on one branch of one repo. A workflow triggered on any
    # other branch -- or in a fork, or by a pull_request event -- presents a
    # different `sub` claim and is refused. Widen this deliberately, not by
    # reaching for a wildcard.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:ref:refs/heads/${var.deploy_branch}"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "${var.cluster_name}-github-actions"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

data "aws_iam_policy_document" "ecr_push" {
  # GetAuthorizationToken is account-wide by definition -- ECR does not let it
  # be scoped down to individual repositories.
  statement {
    sid       = "GetAuthorizationToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  # Everything that actually touches image data is pinned to the two repos the
  # registry module creates. Note there is no ecr:BatchDeleteImage here: CI
  # pushes new tags, it never removes old ones. The lifecycle policy handles
  # expiry, so a compromised workflow token can't wipe the registry.
  statement {
    sid    = "PushImages"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = var.ecr_repository_arns
  }
}

resource "aws_iam_role_policy" "ecr_push" {
  name   = "ecr-push"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.ecr_push.json
}
