# Practical Lab 9: Automated EC2 Deployment with GitHub Actions

**Goal:** Automatically deploy newly published container images to EC2 after CI succeeds, verify health, and roll back if deployment fails.

This lab uses:

- GitHub Actions environments and secrets
- GitHub OIDC authentication to AWS
- A temporary security-group rule for the GitHub runner
- SSH host-key verification
- Commit-specific Docker image tags
- Atomic configuration updates
- Automated health checks
- Automated rollback

**Time:** Approximately 2–3 hours.

> GitHub-hosted runner IP addresses are dynamic. Do **not** solve this by permanently opening SSH port 22 to `0.0.0.0/0`. Instead, the workflow will temporarily authorize only the current runner’s `/32` IP, deploy, and immediately remove the rule.

## Deployment pipeline

```text
Merge into main
      │
      ▼
CI workflow
      │ success
      ▼
Publish images
      │ success
      ▼
Deploy workflow
      │
      ├── Authenticate to AWS using OIDC
      ├── Discover runner public IP
      ├── Temporarily allow runner-IP:22
      ├── Connect to EC2 using verified SSH key
      ├── Pull commit-specific images
      ├── Recreate containers
      ├── Run health checks
      ├── Roll back if unhealthy
      └── Remove temporary SSH rule
```

---

# Part 1: Verify prerequisites

Your EC2 instance must still be running.

Set the local variables from the previous lab:

```bash
export AWS_PROFILE=devops-lab
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""
```

Set your instance and security-group identifiers if your previous shell was closed:

```bash
INSTANCE_ID="$(
  aws ec2 describe-instances \
    --filters \
      Name=tag:Project,Values=devops-journey \
      Name=instance-state-name,Values=running \
    --query 'Reservations[0].Instances[0].InstanceId' \
    --output text
)"

SG_ID="$(
  aws ec2 describe-instances \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].SecurityGroups[0].GroupId' \
    --output text
)"

PUBLIC_IP="$(
  aws ec2 describe-instances \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text
)"

echo "Instance:       $INSTANCE_ID"
echo "Security group: $SG_ID"
echo "Public IP:      $PUBLIC_IP"
```

Set the SSH key path using the key created previously:

```bash
KEY_PATH="$HOME/.ssh/YOUR_KEY_NAME.pem"
```

Verify SSH:

```bash
ssh \
  -i "$KEY_PATH" \
  -o IdentitiesOnly=yes \
  "ubuntu@$PUBLIC_IP" \
  'hostname && docker version --format "{{.Server.Version}}"'
```

Verify the application:

```bash
curl --fail "http://$PUBLIC_IP/nginx-health"
curl --fail "http://$PUBLIC_IP/api/health"
```

---

# Part 2: Create a GitHub production environment

Open the repository:

```bash
cd ~/devops-journey/01-linux-git
```

Create the GitHub environment:

```bash
gh api \
  --method PUT \
  repos/{owner}/{repo}/environments/production
```

Verify:

```bash
gh api repos/{owner}/{repo}/environments/production
```

A GitHub environment provides a deployment boundary for production secrets and variables.

If your repository plan supports deployment protection rules, configure:

1. Open repository **Settings**.
2. Open **Environments**.
3. Select `production`.
4. Add required reviewers.
5. Optionally prevent self-review.
6. Limit deployment branches to `main`.

With required reviewers, GitHub pauses before making production secrets available.

---

# Part 3: Record and verify the EC2 SSH host key

An SSH private key authenticates the workflow to EC2. The server’s host key authenticates EC2 to the workflow.

Without host-key verification, a workflow could connect to an attacker impersonating your server.

Use your already verified SSH connection to retrieve the EC2 host key:

```bash
ssh \
  -i "$KEY_PATH" \
  -o IdentitiesOnly=yes \
  "ubuntu@$PUBLIC_IP" \
  'sudo cat /etc/ssh/ssh_host_ed25519_key.pub' \
  > /tmp/ec2-host-key.pub
```

Inspect its fingerprint:

```bash
ssh-keygen -lf /tmp/ec2-host-key.pub
```

Create a `known_hosts` entry:

```bash
awk \
  -v host="$PUBLIC_IP" \
  '{print host, $1, $2}' \
  /tmp/ec2-host-key.pub \
  > /tmp/ec2-known-hosts
```

Inspect:

```bash
cat /tmp/ec2-known-hosts
```

It should resemble:

```text
203.0.113.10 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...
```

Store the connection information as environment secrets:

```bash
gh secret set EC2_HOST \
  --env production \
  --body "$PUBLIC_IP"
```

```bash
gh secret set EC2_USER \
  --env production \
  --body "ubuntu"
```

```bash
gh secret set EC2_SSH_KEY \
  --env production \
  < "$KEY_PATH"
```

```bash
gh secret set EC2_KNOWN_HOSTS \
  --env production \
  < /tmp/ec2-known-hosts
```

Delete temporary copies:

```bash
rm /tmp/ec2-host-key.pub /tmp/ec2-known-hosts
```

List secret names:

```bash
gh secret list --env production
```

GitHub displays names but never returns secret values.

> If EC2 receives a new public IP after being stopped and started, update both `EC2_HOST` and `EC2_KNOWN_HOSTS`.

---

# Part 4: Configure GitHub OIDC in AWS

The workflow needs AWS permission to add and remove one temporary SSH rule.

Do not create permanent AWS access keys for GitHub Actions. Use OpenID Connect so GitHub receives short-lived AWS credentials.

## 4.1 Add GitHub as an AWS identity provider

In the AWS Console:

1. Open **IAM**.
2. Open **Identity providers**.
3. Select **Add provider**.
4. Choose **OpenID Connect**.
5. Use provider URL:

   ```text
   https://token.actions.githubusercontent.com
   ```

6. Use audience:

   ```text
   sts.amazonaws.com
   ```

7. Add the provider.

If the provider already exists, do not create a duplicate.

Check using AWS CLI:

```bash
aws iam list-open-id-connect-providers
```

---

# Part 5: Create the deployment IAM role

Get your AWS account ID:

```bash
ACCOUNT_ID="$(
  aws sts get-caller-identity \
    --query Account \
    --output text
)"

echo "$ACCOUNT_ID"
```

Get the exact GitHub repository name:

```bash
OWNER_REPO="$(
  gh repo view \
    --json nameWithOwner \
    --jq '.nameWithOwner'
)"

echo "$OWNER_REPO"
```

Create a trust-policy file:

```bash
nano /tmp/github-deploy-trust.json
```

Add this, replacing `OWNER/REPOSITORY`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::AWS_ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
          "token.actions.githubusercontent.com:sub": "repo:OWNER/REPOSITORY:environment:production"
        }
      }
    }
  ]
}
```

Replace `AWS_ACCOUNT_ID`, `OWNER`, and `REPOSITORY` with your values.

For example:

```text
repo:alice/devops-journey:environment:production
```

Create the role:

```bash
ROLE_NAME="GitHubActionsDevOpsJourneyDeploy"

aws iam create-role \
  --role-name "$ROLE_NAME" \
  --description "GitHub Actions deployment role for DevOps Journey" \
  --assume-role-policy-document file:///tmp/github-deploy-trust.json
```

## Create a least-privilege permission policy

Create:

```bash
nano /tmp/github-deploy-permissions.json
```

Add this, replacing the region, account ID, and security-group ID:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ManageTemporaryDeploymentSshRule",
      "Effect": "Allow",
      "Action": [
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:RevokeSecurityGroupIngress"
      ],
      "Resource": "arn:aws:ec2:AWS_REGION:AWS_ACCOUNT_ID:security-group/SECURITY_GROUP_ID"
    },
    {
      "Sid": "DescribeSecurityGroups",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeSecurityGroupRules"
      ],
      "Resource": "*"
    }
  ]
}
```

Attach it as an inline role policy:

```bash
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name ManageDeploymentSshRule \
  --policy-document file:///tmp/github-deploy-permissions.json
```

Get the role ARN:

```bash
ROLE_ARN="$(
  aws iam get-role \
    --role-name "$ROLE_NAME" \
    --query 'Role.Arn' \
    --output text
)"

echo "$ROLE_ARN"
```

Delete temporary policy files:

```bash
rm \
  /tmp/github-deploy-trust.json \
  /tmp/github-deploy-permissions.json
```

---

# Part 6: Store deployment variables

Unlike secrets, these values are identifiers rather than credentials.

Store them as production environment variables:

```bash
gh variable set AWS_DEPLOY_ROLE_ARN \
  --env production \
  --body "$ROLE_ARN"
```

```bash
gh variable set AWS_REGION \
  --env production \
  --body "$AWS_REGION"
```

```bash
gh variable set EC2_SECURITY_GROUP_ID \
  --env production \
  --body "$SG_ID"
```

List them:

```bash
gh variable list --env production
```

The production environment should now contain:

## Secrets

```text
EC2_HOST
EC2_USER
EC2_SSH_KEY
EC2_KNOWN_HOSTS
```

## Variables

```text
AWS_DEPLOY_ROLE_ARN
AWS_REGION
EC2_SECURITY_GROUP_ID
```

---

# Part 7: Create the rollback-capable deployment script

Create a branch:

```bash
git switch main
git pull origin main
git switch -c ci/automate-ec2-deployment
```

Create the scripts directory:

```bash
mkdir -p scripts
```

Create:

```bash
nano scripts/deploy-ec2.sh
```

Add:

```bash
#!/usr/bin/env bash

set -Eeuo pipefail
umask 077

IMAGE_OWNER="${1:?Usage: deploy-ec2.sh IMAGE_OWNER IMAGE_TAG [DEPLOY_DIR] [SOURCE_COMPOSE]}"
IMAGE_TAG="${2:?Usage: deploy-ec2.sh IMAGE_OWNER IMAGE_TAG [DEPLOY_DIR] [SOURCE_COMPOSE]}"
DEPLOY_DIR="${3:-/opt/devops-journey}"
SOURCE_COMPOSE="${4:-/tmp/compose.prod.yaml}"

if [[ ! "$IMAGE_OWNER" =~ ^[a-z0-9][a-z0-9-]*$ ]]; then
    echo "Invalid image owner: $IMAGE_OWNER" >&2
    exit 2
fi

if [[ ! "$IMAGE_TAG" =~ ^(latest|sha-[0-9a-f]{7,40}|v?[0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
    echo "Invalid image tag: $IMAGE_TAG" >&2
    exit 2
fi

if [[ ! -f "$SOURCE_COMPOSE" ]]; then
    echo "Compose source file does not exist: $SOURCE_COMPOSE" >&2
    exit 2
fi

mkdir -p "$DEPLOY_DIR"

COMPOSE_FILE="$DEPLOY_DIR/compose.prod.yaml"
ENV_FILE="$DEPLOY_DIR/.env"
BACKUP_DIR="$(mktemp -d "$DEPLOY_DIR/.rollback.XXXXXX")"

HAD_COMPOSE=false
HAD_ENV=false

if [[ -f "$COMPOSE_FILE" ]]; then
    cp "$COMPOSE_FILE" "$BACKUP_DIR/compose.prod.yaml"
    HAD_COMPOSE=true
fi

if [[ -f "$ENV_FILE" ]]; then
    cp "$ENV_FILE" "$BACKUP_DIR/env"
    HAD_ENV=true
fi

rollback() {
    original_status=$?

    trap - ERR
    set +e

    echo
    echo "Deployment failed. Starting rollback."

    if [[ -f "$COMPOSE_FILE" && -f "$ENV_FILE" ]]; then
        (
            cd "$DEPLOY_DIR"
            docker compose -f compose.prod.yaml down
        )
    fi

    if [[ "$HAD_COMPOSE" == true ]]; then
        cp "$BACKUP_DIR/compose.prod.yaml" "$COMPOSE_FILE"
    else
        rm -f "$COMPOSE_FILE"
    fi

    if [[ "$HAD_ENV" == true ]]; then
        cp "$BACKUP_DIR/env" "$ENV_FILE"
        chmod 600 "$ENV_FILE"
    else
        rm -f "$ENV_FILE"
    fi

    if [[ "$HAD_COMPOSE" == true && "$HAD_ENV" == true ]]; then
        echo "Restoring previous deployment."

        (
            cd "$DEPLOY_DIR"

            docker compose -f compose.prod.yaml pull || true

            docker compose -f compose.prod.yaml up \
                --detach \
                --wait \
                --wait-timeout 120

            docker compose -f compose.prod.yaml ps
        )
    else
        echo "No complete previous deployment was available."
    fi

    rm -rf "$BACKUP_DIR"

    echo "Rollback finished."
    exit "$original_status"
}

trap rollback ERR

install -m 0644 "$SOURCE_COMPOSE" "$COMPOSE_FILE"

NEW_ENV="$DEPLOY_DIR/.env.new"

printf '%s\n' \
    "WEB_IMAGE=ghcr.io/$IMAGE_OWNER/devops-journey-web:$IMAGE_TAG" \
    "API_IMAGE=ghcr.io/$IMAGE_OWNER/devops-journey-api:$IMAGE_TAG" \
    "WEB_PORT=80" \
    > "$NEW_ENV"

chmod 600 "$NEW_ENV"
mv "$NEW_ENV" "$ENV_FILE"

cd "$DEPLOY_DIR"

echo "Validating Compose configuration."
docker compose -f compose.prod.yaml config --quiet

echo "Pulling deployment images."
docker compose -f compose.prod.yaml pull

echo "Applying deployment."
docker compose -f compose.prod.yaml up \
    --detach \
    --wait \
    --wait-timeout 120

echo "Testing Nginx health."
curl \
    --fail \
    --show-error \
    --silent \
    --retry 10 \
    --retry-delay 3 \
    --retry-all-errors \
    http://localhost/nginx-health

echo "Testing API health."
curl \
    --fail \
    --show-error \
    --silent \
    --retry 10 \
    --retry-delay 3 \
    --retry-all-errors \
    http://localhost/api/health

echo "Deployment services:"
docker compose -f compose.prod.yaml ps

printf '%s %s\n' \
    "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
    "$IMAGE_TAG" \
    > "$DEPLOY_DIR/LAST_DEPLOYMENT"

trap - ERR
rm -rf "$BACKUP_DIR"

echo
echo "Deployment succeeded: $IMAGE_TAG"
```

Make it executable:

```bash
chmod 755 scripts/deploy-ec2.sh
```

Validate its Bash syntax:

```bash
bash -n scripts/deploy-ec2.sh
```

---

# Part 8: Understand the rollback process

The script creates temporary backups of:

```text
/opt/devops-journey/compose.prod.yaml
/opt/devops-journey/.env
```

Deployment sequence:

```text
Back up current configuration
        │
        ▼
Install new configuration
        │
        ▼
Pull new images
        │
        ▼
Recreate containers
        │
        ▼
Wait for health checks
        │
        ├── healthy → success
        │
        └── failure
               │
               ▼
        Stop failed deployment
               │
               ▼
        Restore previous files
               │
               ▼
        Start previous images
```

The named Docker volume is not deleted, so API data survives deployments and rollbacks.

---

# Part 9: Create the deployment workflow

Create:

```bash
nano .github/workflows/deploy-ec2.yml
```

Add:

```yaml
name: Deploy to EC2

on:
  workflow_run:
    workflows:
      - Publish container images
    types:
      - completed
    branches:
      - main

  workflow_dispatch:
    inputs:
      image_tag:
        description: Image tag to deploy, such as latest or sha-a1b2c3d4e5f6
        required: true
        default: latest
        type: string

permissions:
  contents: read
  id-token: write

concurrency:
  group: production-ec2
  cancel-in-progress: false

jobs:
  deploy:
    name: Deploy production
    if: >-
      github.event_name == 'workflow_dispatch' ||
      github.event.workflow_run.conclusion == 'success'

    runs-on: ubuntu-latest
    timeout-minutes: 20
    environment: production

    env:
      EC2_HOST: ${{ secrets.EC2_HOST }}
      EC2_USER: ${{ secrets.EC2_USER }}
      AWS_REGION: ${{ vars.AWS_REGION }}
      SECURITY_GROUP_ID: ${{ vars.EC2_SECURITY_GROUP_ID }}

    steps:
      - name: Check out deployment configuration
        uses: actions/checkout@v7
        with:
          ref: ${{ github.event.workflow_run.head_sha || github.sha }}

      - name: Select image tag
        id: release
        shell: bash
        env:
          AUTOMATIC_SHA: ${{ github.event.workflow_run.head_sha }}
          MANUAL_TAG: ${{ inputs.image_tag }}
        run: |
          set -euo pipefail

          owner="${GITHUB_REPOSITORY_OWNER,,}"

          if [[ "$GITHUB_EVENT_NAME" == "workflow_run" ]]; then
            if [[ ! "$AUTOMATIC_SHA" =~ ^[0-9a-f]{40}$ ]]; then
              echo "Invalid workflow commit SHA." >&2
              exit 1
            fi

            image_tag="sha-${AUTOMATIC_SHA:0:12}"
          else
            image_tag="$MANUAL_TAG"
          fi

          if [[ ! "$image_tag" =~ ^(latest|sha-[0-9a-f]{7,40}|v?[0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
            echo "Invalid image tag: $image_tag" >&2
            exit 1
          fi

          echo "owner=$owner" >> "$GITHUB_OUTPUT"
          echo "image_tag=$image_tag" >> "$GITHUB_OUTPUT"

          echo "Deploying image tag: $image_tag"

      - name: Configure temporary AWS credentials
        uses: aws-actions/configure-aws-credentials@v6
        with:
          role-to-assume: ${{ vars.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}
          role-session-name: github-ec2-deploy-${{ github.run_id }}

      - name: Discover runner public IP
        id: runner
        shell: bash
        run: |
          set -euo pipefail

          runner_ip="$(
            curl \
              --fail \
              --silent \
              --show-error \
              --ipv4 \
              https://checkip.amazonaws.com |
            tr -d '\n'
          )"

          if [[ ! "$runner_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "Invalid runner IP: $runner_ip" >&2
            exit 1
          fi

          echo "ip=$runner_ip" >> "$GITHUB_OUTPUT"
          echo "cidr=$runner_ip/32" >> "$GITHUB_OUTPUT"

      - name: Temporarily authorize runner SSH
        id: ssh-rule
        shell: bash
        run: |
          set -euo pipefail

          rule_id="$(
            aws ec2 authorize-security-group-ingress \
              --group-id "$SECURITY_GROUP_ID" \
              --ip-permissions \
                "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=${{ steps.runner.outputs.cidr }},Description='GitHub Actions deployment $GITHUB_RUN_ID'}]" \
              --query 'SecurityGroupRules[0].SecurityGroupRuleId' \
              --output text
          )"

          if [[ -z "$rule_id" || "$rule_id" == "None" ]]; then
            echo "AWS did not return a security-group rule ID." >&2
            exit 1
          fi

          echo "rule_id=$rule_id" >> "$GITHUB_OUTPUT"
          echo "Created temporary rule: $rule_id"

      - name: Configure SSH
        shell: bash
        env:
          SSH_PRIVATE_KEY: ${{ secrets.EC2_SSH_KEY }}
          SSH_KNOWN_HOSTS: ${{ secrets.EC2_KNOWN_HOSTS }}
        run: |
          set -euo pipefail

          install -m 0700 -d "$HOME/.ssh"

          printf '%s\n' "$SSH_PRIVATE_KEY" \
            > "$HOME/.ssh/ec2_key"

          chmod 600 "$HOME/.ssh/ec2_key"

          printf '%s\n' "$SSH_KNOWN_HOSTS" \
            > "$HOME/.ssh/known_hosts"

          chmod 600 "$HOME/.ssh/known_hosts"

      - name: Test SSH connection
        shell: bash
        run: |
          set -euo pipefail

          ssh \
            -i "$HOME/.ssh/ec2_key" \
            -o BatchMode=yes \
            -o ConnectTimeout=15 \
            -o IdentitiesOnly=yes \
            -o StrictHostKeyChecking=yes \
            "$EC2_USER@$EC2_HOST" \
            'echo "Connected to $(hostname)"'

      - name: Upload deployment files
        shell: bash
        run: |
          set -euo pipefail

          scp \
            -i "$HOME/.ssh/ec2_key" \
            -o BatchMode=yes \
            -o ConnectTimeout=15 \
            -o IdentitiesOnly=yes \
            -o StrictHostKeyChecking=yes \
            compose.prod.yaml \
            scripts/deploy-ec2.sh \
            "$EC2_USER@$EC2_HOST:/tmp/"

      - name: Deploy release
        shell: bash
        env:
          IMAGE_OWNER: ${{ steps.release.outputs.owner }}
          IMAGE_TAG: ${{ steps.release.outputs.image_tag }}
        run: |
          set -euo pipefail

          ssh \
            -i "$HOME/.ssh/ec2_key" \
            -o BatchMode=yes \
            -o ConnectTimeout=15 \
            -o IdentitiesOnly=yes \
            -o StrictHostKeyChecking=yes \
            "$EC2_USER@$EC2_HOST" \
            "chmod 755 /tmp/deploy-ec2.sh &&
             /tmp/deploy-ec2.sh \
               '$IMAGE_OWNER' \
               '$IMAGE_TAG' \
               /opt/devops-journey \
               /tmp/compose.prod.yaml"

      - name: Verify public endpoints
        shell: bash
        run: |
          set -euo pipefail

          curl \
            --fail \
            --show-error \
            --silent \
            --retry 10 \
            --retry-delay 3 \
            --retry-all-errors \
            "http://$EC2_HOST/nginx-health"

          curl \
            --fail \
            --show-error \
            --silent \
            --retry 10 \
            --retry-delay 3 \
            --retry-all-errors \
            "http://$EC2_HOST/api/health"

      - name: Add deployment summary
        shell: bash
        run: |
          {
            echo "## EC2 deployment"
            echo
            echo "- Environment: \`production\`"
            echo "- Image tag: \`${{ steps.release.outputs.image_tag }}\`"
            echo "- Instance: \`$EC2_HOST\`"
            echo "- Nginx health: passed"
            echo "- API health: passed"
          } >> "$GITHUB_STEP_SUMMARY"

      - name: Remove temporary SSH rule
        if: always() && steps.ssh-rule.outputs.rule_id != ''
        shell: bash
        run: |
          aws ec2 revoke-security-group-ingress \
            --group-id "$SECURITY_GROUP_ID" \
            --security-group-rule-ids \
              "${{ steps.ssh-rule.outputs.rule_id }}"

      - name: Remove local SSH material
        if: always()
        shell: bash
        run: |
          rm -f \
            "$HOME/.ssh/ec2_key" \
            "$HOME/.ssh/known_hosts"
```

The AWS credentials action uses the current major line:

```yaml
aws-actions/configure-aws-credentials@v6
```

---

# Part 10: Review the security model

## No permanent AWS key

```yaml
permissions:
  id-token: write
```

GitHub requests an OIDC token. AWS validates it and issues short-lived role credentials.

## Repository and environment restriction

The IAM trust policy permits only this subject:

```text
repo:OWNER/REPOSITORY:environment:production
```

A workflow from another repository or environment cannot assume the role.

## Temporary SSH exposure

The workflow authorizes:

```text
runner-public-IP/32 → TCP 22
```

It records the exact AWS security-group rule ID and revokes that specific rule in an `always()` step.

Your own SSH rule is not removed.

## Strict server identity verification

```text
StrictHostKeyChecking=yes
```

The workflow refuses to connect if the EC2 server presents a different SSH host key.

## Serialized deployments

```yaml
concurrency:
  group: production-ec2
  cancel-in-progress: false
```

Only one production deployment runs at a time, and a running deployment is not cancelled halfway through.

---

# Part 11: Validate locally

Check shell syntax:

```bash
bash -n scripts/deploy-ec2.sh
```

Validate Compose:

```bash
OWNER="$(gh api user --jq '.login' | tr '[:upper:]' '[:lower:]')"
SHORT_SHA="$(git rev-parse --short=12 HEAD)"
```

```bash
WEB_IMAGE="ghcr.io/$OWNER/devops-journey-web:sha-$SHORT_SHA" \
API_IMAGE="ghcr.io/$OWNER/devops-journey-api:sha-$SHORT_SHA" \
WEB_PORT=80 \
docker compose -f compose.prod.yaml config --quiet
```

Review changes:

```bash
git status
git diff
```

---

# Part 12: Commit and create a pull request

Stage:

```bash
git add \
  scripts/deploy-ec2.sh \
  .github/workflows/deploy-ec2.yml
```

Commit:

```bash
git commit -m "ci: automate EC2 deployment with rollback"
```

Push:

```bash
git push -u origin ci/automate-ec2-deployment
```

Create the PR:

```bash
gh pr create \
  --base main \
  --head ci/automate-ec2-deployment \
  --title "Automate EC2 deployment with rollback" \
  --body "$(cat <<'EOF'
## Summary

- Deploys successful GHCR images to EC2
- Uses GitHub OIDC instead of permanent AWS credentials
- Temporarily authorizes only the GitHub runner's SSH IP
- Verifies the EC2 SSH host key
- Uses commit-specific container tags
- Runs internal and external health checks
- Automatically restores the previous deployment on failure
- Removes temporary SSH access after every run

## Test plan

- [x] Deployment script passes Bash syntax validation
- [x] Compose configuration is valid
- [ ] GitHub Actions assumes the AWS role
- [ ] Temporary SSH rule is created and removed
- [ ] EC2 health checks pass
- [ ] Invalid image deployment rolls back successfully
EOF
)"
```

Wait for CI:

```bash
gh pr checks --watch
```

---

# Part 13: Merge and watch the full pipeline

Merge:

```bash
gh pr merge --squash --delete-branch
```

Synchronize:

```bash
git switch main
git pull origin main
```

The complete pipeline should now run:

```text
CI
  → Publish container images
      → Deploy to EC2
```

List deployment runs:

```bash
gh run list \
  --workflow deploy-ec2.yml \
  --limit 5
```

Watch the deployment:

```bash
DEPLOY_RUN_ID="$(
  gh run list \
    --workflow deploy-ec2.yml \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId'
)"

gh run watch "$DEPLOY_RUN_ID"
```

Inspect the run:

```bash
gh run view "$DEPLOY_RUN_ID"
```

Open it in the browser:

```bash
gh run view "$DEPLOY_RUN_ID" --web
```

---

# Part 14: Verify the deployment

Check public endpoints:

```bash
curl --fail "http://$PUBLIC_IP/nginx-health"
curl --fail "http://$PUBLIC_IP/api/health"
curl --fail "http://$PUBLIC_IP/api/info"
```

Connect to EC2:

```bash
ssh -i "$KEY_PATH" "ubuntu@$PUBLIC_IP"
```

Inspect the deployment:

```bash
cd /opt/devops-journey
```

```bash
cat LAST_DEPLOYMENT
```

```bash
docker compose -f compose.prod.yaml ps
docker compose -f compose.prod.yaml images
```

Inspect deployed image values:

```bash
grep IMAGE .env
```

The image tag should resemble:

```text
sha-a1b2c3d4e5f6
```

Exit:

```bash
exit
```

---

# Part 15: Verify temporary SSH cleanup

From your local machine:

```bash
aws ec2 describe-security-group-rules \
  --filters Name=group-id,Values="$SG_ID" \
  --query 'SecurityGroupRules[].{ID:SecurityGroupRuleId,Port:FromPort,CIDR:CidrIpv4,Description:Description}' \
  --output table
```

You should see:

- Your personal `/32` SSH rule
- Public HTTP port 80
- No leftover rule with a GitHub Actions deployment description

If a failed workflow leaves a rule behind, delete that exact rule:

```bash
aws ec2 revoke-security-group-ingress \
  --group-id "$SG_ID" \
  --security-group-rule-ids RULE_ID
```

---

# Part 16: Test automated rollback

Find the currently deployed tag:

```bash
ssh \
  -i "$KEY_PATH" \
  "ubuntu@$PUBLIC_IP" \
  "grep IMAGE /opt/devops-journey/.env"
```

Trigger an invalid image deployment:

```bash
gh workflow run deploy-ec2.yml \
  --ref main \
  -f image_tag=sha-000000000000
```

Find and watch the run:

```bash
sleep 3
```

```bash
FAILED_RUN_ID="$(
  gh run list \
    --workflow deploy-ec2.yml \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId'
)"

gh run watch "$FAILED_RUN_ID"
```

The workflow should fail because the image tag does not exist.

Inspect logs:

```bash
gh run view "$FAILED_RUN_ID" --log-failed
```

Look for:

```text
Deployment failed. Starting rollback.
Restoring previous deployment.
Rollback finished.
```

Verify the application stayed available:

```bash
curl --fail "http://$PUBLIC_IP/nginx-health"
curl --fail "http://$PUBLIC_IP/api/health"
```

Verify the old images were restored:

```bash
ssh \
  -i "$KEY_PATH" \
  "ubuntu@$PUBLIC_IP" \
  "grep IMAGE /opt/devops-journey/.env"
```

Verify the temporary SSH rule was removed:

```bash
aws ec2 describe-security-group-rules \
  --filters Name=group-id,Values="$SG_ID" \
  --query 'SecurityGroupRules[?contains(Description, `GitHub Actions`)]'
```

Expected:

```json
[]
```

---

# Part 17: Perform a real manual rollback

Find recent published commit tags in GitHub Packages or Git history:

```bash
git log --oneline -10
```

Select a previously published commit SHA:

```bash
PREVIOUS_TAG="sha-PREVIOUS_12_CHARACTER_SHA"
```

Start a manual deployment:

```bash
gh workflow run deploy-ec2.yml \
  --ref main \
  -f image_tag="$PREVIOUS_TAG"
```

Watch it:

```bash
ROLLBACK_RUN_ID="$(
  gh run list \
    --workflow deploy-ec2.yml \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId'
)"

gh run watch "$ROLLBACK_RUN_ID"
```

Verify:

```bash
curl --fail "http://$PUBLIC_IP/nginx-health"
curl --fail "http://$PUBLIC_IP/api/health"
```

This deploys a previous release without rebuilding it.

---

# Troubleshooting

## OIDC assumption fails

Typical error:

```text
Not authorized to perform sts:AssumeRoleWithWebIdentity
```

Check:

- The OIDC provider exists.
- Audience is `sts.amazonaws.com`.
- Repository owner and name are exact.
- Trust policy uses `environment:production`.
- The workflow job contains `environment: production`.
- `AWS_DEPLOY_ROLE_ARN` points to the correct role.

## AWS authorization fails

If creating the temporary SSH rule fails:

```bash
aws iam get-role-policy \
  --role-name GitHubActionsDevOpsJourneyDeploy \
  --policy-name ManageDeploymentSshRule
```

Confirm the policy references the correct:

- Region
- Account ID
- Security-group ID

## SSH host-key verification fails

Do not disable strict checking. Instead, verify whether:

- EC2 was replaced.
- The public IP changed.
- The operating system was reinstalled.
- The stored `EC2_KNOWN_HOSTS` entry is outdated.

Retrieve and verify the new key through a trusted connection, then update the secret.

## SSH connection times out

Check whether the temporary rule exists while the deployment is running:

```bash
aws ec2 describe-security-group-rules \
  --filters Name=group-id,Values="$SG_ID" \
  --output table
```

Also verify:

- The instance is running.
- SSH service is active.
- `EC2_HOST` is the current public IP.
- The subnet route reaches an internet gateway.

## Deployment passes internally but fails publicly

Check:

```bash
aws ec2 describe-security-groups \
  --group-ids "$SG_ID"
```

Port 80 must be allowed from the intended clients.

On EC2:

```bash
sudo ss -lntp | grep ':80'
```

---

# Final verification

Run:

```bash
gh secret list --env production
gh variable list --env production
```

```bash
gh run list --workflow ci.yml --limit 3
gh run list --workflow publish-images.yml --limit 3
gh run list --workflow deploy-ec2.yml --limit 3
```

```bash
curl --fail "http://$PUBLIC_IP/nginx-health"
curl --fail "http://$PUBLIC_IP/api/health"
```

```bash
aws ec2 describe-security-group-rules \
  --filters Name=group-id,Values="$SG_ID" \
  --query 'SecurityGroupRules[].{Port:FromPort,CIDR:CidrIpv4,Description:Description}' \
  --output table
```

Success means:

- GitHub uses OIDC rather than permanent AWS keys.
- The AWS role trusts only the production environment in your repository.
- The role can modify only the deployment security group.
- The GitHub runner receives temporary SSH access.
- The exact temporary rule is removed after every run.
- SSH server identity is strictly verified.
- Deployment uses commit-specific images.
- Health checks run internally and publicly.
- Invalid deployments restore the previous images.
- Production deployments cannot overlap.
- Manual rollback to an older image works.

## Skills completed

You have now practised:

- GitHub environments
- Environment-scoped secrets and variables
- GitHub OIDC federation with AWS
- Temporary AWS credentials
- Least-privilege IAM trust and permission policies
- Dynamic security-group rules
- Secure SSH host-key verification
- Automated container deployment
- Deployment health checks
- Atomic configuration updates
- Automated rollback
- Deployment concurrency control
- Manual release rollback

**Next lab:** AWS S3—create a private bucket, configure an EC2 IAM role without access keys, upload application backups, enable encryption and versioning, and automate backup retention.

Official references:

- [GitHub Actions OIDC with AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
- [AWS IAM OIDC federation](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc.html)
- [GitHub Actions AWS credentials action](https://github.com/aws-actions/configure-aws-credentials)