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
    # different `sub` claim and is refused.
    #
    # GitHub now issues *immutable* subject claims, which splice the numeric
    # owner and repo IDs into the path:
    #
    #   repo:tj-miller-dev@204254190/stock_simulator@1335562161:ref:refs/heads/main
    #
    # rather than the older `repo:owner/name:ref:...`. The IDs survive renames,
    # so a claim can't be hijacked by someone re-registering an abandoned org or
    # repo name. Both forms are listed as exact matches: StringEquals against a
    # list is an OR, so this keeps working whichever format GitHub sends.
    #
    # Do NOT collapse these into a StringLike wildcard such as
    # `repo:tj-miller-dev*/stock_simulator*:...` -- that would also match an
    # attacker-owned `tj-miller-dev-evil/stock_simulator`, which is precisely
    # the attack the numeric IDs exist to prevent.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repository_immutable}:ref:refs/heads/${var.deploy_branch}",
        "repo:${var.github_repository}:ref:refs/heads/${var.deploy_branch}",
      ]
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
