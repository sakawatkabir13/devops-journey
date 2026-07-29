# Practical Lab 10: AWS S3 Backups with an EC2 IAM Role

**Goal:** Create a secure private S3 bucket, give EC2 limited access using an IAM role, back up application data, verify integrity, and schedule automatic backups.

**Time:** Approximately 2 hours.

## Architecture

```text
EC2 instance
    │
    │ temporary IAM role credentials
    │ HTTPS
    ▼
Private S3 bucket
    │
    ├── Server-side encryption
    ├── Versioning
    ├── Public access blocked
    ├── TLS-only bucket policy
    └── Lifecycle retention
```

No AWS access keys will be placed on the EC2 server.

> **Cost warning:** S3 storage, requests, versioned objects, and data transfer can generate charges. Versioning keeps old copies, so lifecycle rules are important.

---

# Part 1: Verify the EC2 deployment

Set your local AWS environment:

```bash
export AWS_PROFILE=devops-lab
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""
```

Rediscover your instance if necessary:

```bash
INSTANCE_ID="$(
  aws ec2 describe-instances \
    --filters \
      Name=tag:Project,Values=devops-journey \
      Name=instance-state-name,Values=running \
    --query 'Reservations[0].Instances[0].InstanceId' \
    --output text
)"

PUBLIC_IP="$(
  aws ec2 describe-instances \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text
)"

echo "Instance:  $INSTANCE_ID"
echo "Public IP: $PUBLIC_IP"
```

Verify the application:

```bash
curl --fail "http://$PUBLIC_IP/nginx-health"
curl --fail "http://$PUBLIC_IP/api/health"
```

---

# Part 2: Create a globally unique S3 bucket

Get your AWS account ID:

```bash
ACCOUNT_ID="$(
  aws sts get-caller-identity \
    --query Account \
    --output text
)"
```

Create a globally unique lowercase bucket name:

```bash
BUCKET="devops-journey-${ACCOUNT_ID}-${AWS_REGION}"

echo "$BUCKET"
```

S3 bucket names are globally unique across all AWS accounts, not just within your account.

Create the bucket.

For `us-east-1`:

```bash
if [[ "$AWS_REGION" == "us-east-1" ]]; then
  aws s3api create-bucket \
    --bucket "$BUCKET" \
    --region "$AWS_REGION"
else
  aws s3api create-bucket \
    --bucket "$BUCKET" \
    --region "$AWS_REGION" \
    --create-bucket-configuration \
      LocationConstraint="$AWS_REGION"
fi
```

Verify:

```bash
aws s3api get-bucket-location \
  --bucket "$BUCKET"
```

Tag the bucket:

```bash
aws s3api put-bucket-tagging \
  --bucket "$BUCKET" \
  --tagging 'TagSet=[
    {Key=Project,Value=devops-journey},
    {Key=Environment,Value=lab},
    {Key=Purpose,Value=application-backups}
  ]'
```

---

# Part 3: Block all public access

Apply all four bucket-level public-access protections:

```bash
aws s3api put-public-access-block \
  --bucket "$BUCKET" \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

Verify:

```bash
aws s3api get-public-access-block \
  --bucket "$BUCKET"
```

Expected:

```json
{
  "PublicAccessBlockConfiguration": {
    "BlockPublicAcls": true,
    "IgnorePublicAcls": true,
    "BlockPublicPolicy": true,
    "RestrictPublicBuckets": true
  }
}
```

---

# Part 4: Disable ACL-based ownership

Configure bucket-owner-enforced object ownership:

```bash
aws s3api put-bucket-ownership-controls \
  --bucket "$BUCKET" \
  --ownership-controls \
    'Rules=[{ObjectOwnership=BucketOwnerEnforced}]'
```

Verify:

```bash
aws s3api get-bucket-ownership-controls \
  --bucket "$BUCKET"
```

This disables S3 ACLs for the bucket. Access should be controlled through IAM and bucket policies instead.

---

# Part 5: Configure encryption

Amazon S3 encrypts new objects by default, but we will explicitly configure SSE-S3 so the security configuration is visible and auditable.

Run:

```bash
aws s3api put-bucket-encryption \
  --bucket "$BUCKET" \
  --server-side-encryption-configuration \
    '{
      "Rules": [
        {
          "ApplyServerSideEncryptionByDefault": {
            "SSEAlgorithm": "AES256"
          }
        }
      ]
    }'
```

Verify:

```bash
aws s3api get-bucket-encryption \
  --bucket "$BUCKET"
```

Expected algorithm:

```text
AES256
```

This is **SSE-S3**: server-side encryption using keys managed by Amazon S3.

---

# Part 6: Enable versioning

Enable versioning:

```bash
aws s3api put-bucket-versioning \
  --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled
```

Verify:

```bash
aws s3api get-bucket-versioning \
  --bucket "$BUCKET"
```

Expected:

```json
{
  "Status": "Enabled"
}
```

Versioning protects against accidental overwrites and deletes, but every retained version consumes storage.

---

# Part 7: Require encrypted HTTPS transport

Create a bucket policy that denies unencrypted HTTP connections.

Generate the policy:

```bash
export BUCKET
```

```bash
python3 - <<'PY' > /tmp/s3-tls-policy.json
import json
import os

bucket = os.environ["BUCKET"]

policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "DenyInsecureTransport",
            "Effect": "Deny",
            "Principal": "*",
            "Action": "s3:*",
            "Resource": [
                f"arn:aws:s3:::{bucket}",
                f"arn:aws:s3:::{bucket}/*",
            ],
            "Condition": {
                "Bool": {
                    "aws:SecureTransport": "false"
                }
            },
        }
    ],
}

print(json.dumps(policy, indent=2))
PY
```

Inspect:

```bash
less /tmp/s3-tls-policy.json
```

Press `q` to exit.

Apply:

```bash
aws s3api put-bucket-policy \
  --bucket "$BUCKET" \
  --policy file:///tmp/s3-tls-policy.json
```

Delete the temporary file:

```bash
rm /tmp/s3-tls-policy.json
```

Check whether AWS considers the bucket public:

```bash
aws s3api get-bucket-policy-status \
  --bucket "$BUCKET"
```

Expected:

```json
{
  "PolicyStatus": {
    "IsPublic": false
  }
}
```

---

# Part 8: Configure backup retention

Create a lifecycle configuration:

```bash
nano /tmp/s3-lifecycle.json
```

Add:

```json
{
  "Rules": [
    {
      "ID": "ExpireOldApplicationBackups",
      "Status": "Enabled",
      "Filter": {
        "Prefix": "backups/"
      },
      "Expiration": {
        "Days": 30
      },
      "NoncurrentVersionExpiration": {
        "NoncurrentDays": 30
      },
      "AbortIncompleteMultipartUpload": {
        "DaysAfterInitiation": 7
      }
    }
  ]
}
```

Apply:

```bash
aws s3api put-bucket-lifecycle-configuration \
  --bucket "$BUCKET" \
  --lifecycle-configuration file:///tmp/s3-lifecycle.json
```

Verify:

```bash
aws s3api get-bucket-lifecycle-configuration \
  --bucket "$BUCKET"
```

Delete the temporary file:

```bash
rm /tmp/s3-lifecycle.json
```

The policy:

- Expires backup objects after 30 days.
- Expires noncurrent versions after 30 days.
- Aborts incomplete multipart uploads after seven days.

Adjust retention according to your recovery and compliance requirements.

---

# Part 9: Create a least-privilege EC2 IAM role

The EC2 server needs access only to the `backups/` prefix in this bucket.

## 9.1 Create the EC2 trust policy

Create:

```bash
nano /tmp/ec2-s3-trust.json
```

Add:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ec2.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

Create the role:

```bash
S3_ROLE_NAME="DevOpsJourneyEC2S3BackupRole"

aws iam create-role \
  --role-name "$S3_ROLE_NAME" \
  --description "Allows DevOps Journey EC2 to store and restore S3 backups" \
  --assume-role-policy-document file:///tmp/ec2-s3-trust.json
```

## 9.2 Create the S3 permission policy

Export the bucket variable:

```bash
export BUCKET
```

Generate a bucket-specific policy:

```bash
python3 - <<'PY' > /tmp/ec2-s3-permissions.json
import json
import os

bucket = os.environ["BUCKET"]

policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "ReadBucketMetadata",
            "Effect": "Allow",
            "Action": [
                "s3:GetBucketLocation",
            ],
            "Resource": f"arn:aws:s3:::{bucket}",
        },
        {
            "Sid": "ListBackupObjects",
            "Effect": "Allow",
            "Action": [
                "s3:ListBucket",
                "s3:ListBucketVersions",
            ],
            "Resource": f"arn:aws:s3:::{bucket}",
            "Condition": {
                "StringLike": {
                    "s3:prefix": [
                        "backups",
                        "backups/*",
                    ]
                }
            },
        },
        {
            "Sid": "ReadAndWriteBackupObjects",
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:GetObjectVersion",
                "s3:PutObject",
            ],
            "Resource": f"arn:aws:s3:::{bucket}/backups/*",
        },
    ],
}

print(json.dumps(policy, indent=2))
PY
```

Review:

```bash
less /tmp/ec2-s3-permissions.json
```

Apply as an inline role policy:

```bash
aws iam put-role-policy \
  --role-name "$S3_ROLE_NAME" \
  --policy-name DevOpsJourneyS3BackupAccess \
  --policy-document file:///tmp/ec2-s3-permissions.json
```

Delete temporary files:

```bash
rm \
  /tmp/ec2-s3-trust.json \
  /tmp/ec2-s3-permissions.json
```

Notice that the role does **not** have:

- Access to other buckets
- Access outside `backups/`
- Permission to change bucket policies
- Permission to make objects public
- Permission to delete objects manually
- General AWS administrator access

Lifecycle management, not the EC2 role, removes expired backups.

---

# Part 10: Create and attach an instance profile

EC2 receives IAM roles through an instance profile.

Create one:

```bash
INSTANCE_PROFILE_NAME="DevOpsJourneyEC2S3BackupProfile"

aws iam create-instance-profile \
  --instance-profile-name "$INSTANCE_PROFILE_NAME"
```

Add the role:

```bash
aws iam add-role-to-instance-profile \
  --instance-profile-name "$INSTANCE_PROFILE_NAME" \
  --role-name "$S3_ROLE_NAME"
```

Check whether the EC2 instance already has an instance profile:

```bash
aws ec2 describe-iam-instance-profile-associations \
  --filters Name=instance-id,Values="$INSTANCE_ID" \
  --query 'IamInstanceProfileAssociations[].{Association:AssociationId,State:State,Profile:IamInstanceProfile.Arn}' \
  --output table
```

If no association exists, attach the profile:

```bash
ASSOCIATION_ID="$(
  aws ec2 associate-iam-instance-profile \
    --instance-id "$INSTANCE_ID" \
    --iam-instance-profile Name="$INSTANCE_PROFILE_NAME" \
    --query 'IamInstanceProfileAssociation.AssociationId' \
    --output text
)"

echo "$ASSOCIATION_ID"
```

If an instance profile already exists, inspect it instead of replacing it blindly. An EC2 instance can have only one associated instance profile; in that case, add the S3 permissions to the existing role or deliberately replace the profile after reviewing its purpose.

Verify:

```bash
aws ec2 describe-iam-instance-profile-associations \
  --filters Name=instance-id,Values="$INSTANCE_ID" \
  --output table
```

IAM changes can take a short time to propagate.

---

# Part 11: Install AWS CLI on EC2

Connect:

```bash
ssh -i "$KEY_PATH" "ubuntu@$PUBLIC_IP"
```

Check:

```bash
aws --version
```

If AWS CLI v2 is not installed:

```bash
sudo apt update
sudo apt install -y curl unzip
```

```bash
case "$(uname -m)" in
  x86_64)
    AWSCLI_ARCH="x86_64"
    ;;
  aarch64|arm64)
    AWSCLI_ARCH="aarch64"
    ;;
  *)
    echo "Unsupported architecture: $(uname -m)"
    ;;
esac
```

```bash
curl \
  "https://awscli.amazonaws.com/awscli-exe-linux-${AWSCLI_ARCH}.zip" \
  -o /tmp/awscliv2.zip
```

```bash
rm -rf /tmp/aws
unzip -q /tmp/awscliv2.zip -d /tmp
sudo /tmp/aws/install
rm -rf /tmp/aws /tmp/awscliv2.zip
```

Verify:

```bash
aws --version
```

Do **not** run `aws configure` on EC2.

Set the region:

```bash
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""
```

Verify the instance role:

```bash
aws sts get-caller-identity
```

The ARN should reference:

```text
DevOpsJourneyEC2S3BackupRole
```

The AWS CLI automatically retrieves temporary credentials from EC2 instance metadata and refreshes them.

---

# Part 12: Verify EC2 role permissions

On EC2, set the bucket:

```bash
BUCKET="YOUR_BUCKET_NAME"
```

Check its location:

```bash
aws s3api get-bucket-location \
  --bucket "$BUCKET"
```

List the allowed backup prefix:

```bash
aws s3 ls "s3://$BUCKET/backups/"
```

This may return nothing because no backups exist yet.

Test access outside the allowed prefix:

```bash
printf 'permission test\n' > /tmp/permission-test.txt
```

```bash
aws s3 cp \
  /tmp/permission-test.txt \
  "s3://$BUCKET/not-allowed/test.txt"
```

Expected:

```text
AccessDenied
```

Test the allowed prefix:

```bash
aws s3 cp \
  /tmp/permission-test.txt \
  "s3://$BUCKET/backups/permission-test.txt" \
  --sse AES256
```

Expected: upload succeeds.

Inspect it:

```bash
aws s3api head-object \
  --bucket "$BUCKET" \
  --key backups/permission-test.txt
```

Look for:

```json
"ServerSideEncryption": "AES256"
```

Remove the local temporary file:

```bash
rm /tmp/permission-test.txt
```

The EC2 role intentionally cannot delete the uploaded S3 object.

---

# Part 13: Create the backup script

Return to your local machine:

```bash
exit
```

Open your repository:

```bash
cd ~/devops-journey/01-linux-git
git switch main
git pull origin main
git switch -c feat/add-s3-backups
```

Create:

```bash
nano scripts/backup-to-s3.sh
```

Add:

```bash
#!/usr/bin/env bash

set -Eeuo pipefail
umask 077

BUCKET="${1:-${S3_BUCKET:?Provide the bucket as argument 1 or S3_BUCKET}}"
PROJECT_DIR="${PROJECT_DIR:-/opt/devops-journey}"

if [[ ! "$BUCKET" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]]; then
    echo "Invalid S3 bucket name: $BUCKET" >&2
    exit 2
fi

if [[ ! -f "$PROJECT_DIR/compose.prod.yaml" ]]; then
    echo "Compose file not found in $PROJECT_DIR" >&2
    exit 2
fi

TIMESTAMP="$(date -u +'%Y%m%dT%H%M%SZ')"
DATE_PATH="$(date -u +'%Y/%m/%d')"
SERVER_NAME="$(hostname -s)"

WORK_DIR="$(mktemp -d)"
ARCHIVE="/tmp/devops-journey-${TIMESTAMP}.tar.gz"
CHECKSUM_FILE="${ARCHIVE}.sha256"

cleanup() {
    rm -rf "$WORK_DIR"
    rm -f "$ARCHIVE" "$CHECKSUM_FILE"
}

trap cleanup EXIT

cd "$PROJECT_DIR"

echo "Backing up API volume."

docker compose -f compose.prod.yaml exec -T api \
    tar -czf - -C /data . \
    > "$WORK_DIR/api-data.tar.gz"

echo "Backing up deployment metadata."

cp compose.prod.yaml "$WORK_DIR/compose.prod.yaml"

if [[ -f LAST_DEPLOYMENT ]]; then
    cp LAST_DEPLOYMENT "$WORK_DIR/LAST_DEPLOYMENT"
fi

(
    cd "$WORK_DIR"

    find . \
        -maxdepth 1 \
        -type f \
        ! -name SHA256SUMS \
        -printf '%f\n' \
        -exec sha256sum {} \; \
        > SHA256SUMS
)

tar -czf "$ARCHIVE" -C "$WORK_DIR" .

ARCHIVE_NAME="$(basename "$ARCHIVE")"
CHECKSUM_NAME="$(basename "$CHECKSUM_FILE")"

sha256sum "$ARCHIVE" |
    sed "s|$ARCHIVE|$ARCHIVE_NAME|" \
    > "$CHECKSUM_FILE"

S3_KEY="backups/${SERVER_NAME}/${DATE_PATH}/${ARCHIVE_NAME}"
S3_CHECKSUM_KEY="${S3_KEY}.sha256"

echo "Uploading s3://$BUCKET/$S3_KEY"

aws s3 cp \
    "$ARCHIVE" \
    "s3://$BUCKET/$S3_KEY" \
    --sse AES256 \
    --only-show-errors

aws s3 cp \
    "$CHECKSUM_FILE" \
    "s3://$BUCKET/$S3_CHECKSUM_KEY" \
    --sse AES256 \
    --only-show-errors

echo "Verifying uploaded object."

aws s3api head-object \
    --bucket "$BUCKET" \
    --key "$S3_KEY" \
    --query '{
        Size:ContentLength,
        Encryption:ServerSideEncryption,
        VersionId:VersionId,
        Modified:LastModified
    }'

echo
echo "Backup completed:"
echo "s3://$BUCKET/$S3_KEY"
echo "s3://$BUCKET/$S3_CHECKSUM_KEY"
```

Make it executable:

```bash
chmod 755 scripts/backup-to-s3.sh
```

Check syntax:

```bash
bash -n scripts/backup-to-s3.sh
```

> The script deliberately excludes `.env` because deployment environment files may later contain secrets.

---

# Part 14: Commit and merge the backup script

Stage:

```bash
git add scripts/backup-to-s3.sh
```

Commit:

```bash
git commit -m "feat: add encrypted S3 backup script"
```

Push:

```bash
git push -u origin feat/add-s3-backups
```

Create the PR:

```bash
gh pr create \
  --base main \
  --title "Add encrypted S3 backup script" \
  --body "$(cat <<'EOF'
## Summary

- Archives API volume data
- Includes deployment metadata without copying environment secrets
- Generates SHA-256 checksums
- Uploads backups to a private S3 prefix
- Requests SSE-S3 encryption
- Verifies uploaded object metadata

## Test plan

- [x] Bash syntax validation passes
- [ ] EC2 role can upload to backups/
- [ ] EC2 role cannot upload outside backups/
- [ ] Archive checksum verification passes
EOF
)"
```

Wait for CI:

```bash
gh pr checks --watch
```

Merge:

```bash
gh pr merge --squash --delete-branch
git switch main
git pull origin main
```

---

# Part 15: Install and run the backup script

Copy it to EC2:

```bash
scp \
  -i "$KEY_PATH" \
  scripts/backup-to-s3.sh \
  "ubuntu@$PUBLIC_IP:/tmp/backup-to-s3.sh"
```

Connect:

```bash
ssh -i "$KEY_PATH" "ubuntu@$PUBLIC_IP"
```

Install:

```bash
sudo install \
  -m 0755 \
  /tmp/backup-to-s3.sh \
  /usr/local/bin/devops-journey-backup
```

Set the bucket:

```bash
BUCKET="YOUR_BUCKET_NAME"
```

Run a backup:

```bash
S3_BUCKET="$BUCKET" \
  /usr/local/bin/devops-journey-backup
```

You should receive two S3 locations:

```text
s3://BUCKET/backups/HOST/YYYY/MM/DD/devops-journey-TIMESTAMP.tar.gz
s3://BUCKET/backups/HOST/YYYY/MM/DD/devops-journey-TIMESTAMP.tar.gz.sha256
```

List backups:

```bash
aws s3 ls \
  "s3://$BUCKET/backups/" \
  --recursive
```

---

# Part 16: Verify a backup

Copy the archive key from the script output:

```bash
BACKUP_KEY="backups/HOST/YYYY/MM/DD/devops-journey-TIMESTAMP.tar.gz"
```

Create a verification directory:

```bash
rm -rf /tmp/devops-restore-test
mkdir -p /tmp/devops-restore-test
cd /tmp/devops-restore-test
```

Download the archive:

```bash
ARCHIVE_NAME="$(basename "$BACKUP_KEY")"
```

```bash
aws s3 cp \
  "s3://$BUCKET/$BACKUP_KEY" \
  "$ARCHIVE_NAME"
```

Download its checksum:

```bash
aws s3 cp \
  "s3://$BUCKET/${BACKUP_KEY}.sha256" \
  "${ARCHIVE_NAME}.sha256"
```

Verify the complete archive:

```bash
sha256sum -c "${ARCHIVE_NAME}.sha256"
```

Expected:

```text
devops-journey-TIMESTAMP.tar.gz: OK
```

Extract:

```bash
mkdir extracted
tar -xzf "$ARCHIVE_NAME" -C extracted
```

Inspect:

```bash
find extracted -maxdepth 2 -type f -print
```

Expected files include:

```text
api-data.tar.gz
compose.prod.yaml
LAST_DEPLOYMENT
SHA256SUMS
```

Verify internal files:

```bash
cd extracted
sha256sum -c SHA256SUMS
```

Every file should report:

```text
OK
```

A backup is not proven until you can download, inspect, and verify it.

---

# Part 17: Perform an optional restore test

> This step modifies the API volume. Perform it only after verifying the backup.

Record the current API counter:

```bash
curl http://localhost/api/info
curl http://localhost/api/info
```

Enter the project:

```bash
cd /opt/devops-journey
```

Stop the application:

```bash
docker compose -f compose.prod.yaml stop web api
```

Clear and restore the API volume:

```bash
docker compose -f compose.prod.yaml run \
  --rm \
  --no-deps \
  -T \
  api \
  sh -c '
    find /data -mindepth 1 -maxdepth 1 -exec rm -rf {} +
    tar -xzf - -C /data
  ' < /tmp/devops-restore-test/extracted/api-data.tar.gz
```

Restart:

```bash
docker compose -f compose.prod.yaml up \
  --detach \
  --wait
```

Test:

```bash
curl --fail http://localhost/nginx-health
curl --fail http://localhost/api/health
curl http://localhost/api/info
```

The API counter should reflect the value captured in the backup.

---

# Part 18: Schedule daily backups with systemd

Create an environment file:

```bash
sudo nano /etc/devops-journey-backup
```

Add:

```text
S3_BUCKET=YOUR_BUCKET_NAME
PROJECT_DIR=/opt/devops-journey
AWS_REGION=us-east-1
AWS_DEFAULT_REGION=us-east-1
AWS_PAGER=
```

Protect it:

```bash
sudo chmod 600 /etc/devops-journey-backup
sudo chown root:root /etc/devops-journey-backup
```

Create the service:

```bash
sudo nano /etc/systemd/system/devops-journey-backup.service
```

Add:

```ini
[Unit]
Description=Back up DevOps Journey data to Amazon S3
Wants=network-online.target
After=network-online.target docker.service
Requires=docker.service

[Service]
Type=oneshot
User=ubuntu
Group=ubuntu
SupplementaryGroups=docker
EnvironmentFile=/etc/devops-journey-backup
ExecStart=/usr/local/bin/devops-journey-backup

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/tmp
ReadOnlyPaths=/opt/devops-journey
```

Create the timer:

```bash
sudo nano /etc/systemd/system/devops-journey-backup.timer
```

Add:

```ini
[Unit]
Description=Run DevOps Journey backup daily

[Timer]
OnCalendar=*-*-* 02:00:00 UTC
Persistent=true
RandomizedDelaySec=10m

[Install]
WantedBy=timers.target
```

The timer runs daily around 02:00 UTC. `RandomizedDelaySec` avoids every server starting backup work at exactly the same second.

Reload systemd:

```bash
sudo systemctl daemon-reload
```

Enable the timer:

```bash
sudo systemctl enable --now devops-journey-backup.timer
```

Inspect:

```bash
systemctl list-timers \
  devops-journey-backup.timer
```

---

# Part 19: Test the scheduled service now

Start a manual systemd run:

```bash
sudo systemctl start devops-journey-backup.service
```

Check status:

```bash
sudo systemctl status \
  devops-journey-backup.service \
  --no-pager
```

View logs:

```bash
sudo journalctl \
  -u devops-journey-backup.service \
  --since "10 minutes ago" \
  --no-pager
```

List S3 backups:

```bash
aws s3 ls \
  "s3://$BUCKET/backups/" \
  --recursive
```

---

# Part 20: Practise S3 versioning

Create a local demonstration file:

```bash
printf 'version one\n' > /tmp/versioning-demo.txt
```

Upload:

```bash
aws s3 cp \
  /tmp/versioning-demo.txt \
  "s3://$BUCKET/backups/versioning-demo.txt" \
  --sse AES256
```

Overwrite it:

```bash
printf 'version two\n' > /tmp/versioning-demo.txt
```

```bash
aws s3 cp \
  /tmp/versioning-demo.txt \
  "s3://$BUCKET/backups/versioning-demo.txt" \
  --sse AES256
```

List versions:

```bash
aws s3api list-object-versions \
  --bucket "$BUCKET" \
  --prefix backups/versioning-demo.txt \
  --query 'Versions[].{VersionId:VersionId,Latest:IsLatest,Modified:LastModified,Size:Size}' \
  --output table
```

You should see two versions with different version IDs.

Download a specific older version:

```bash
aws s3api get-object \
  --bucket "$BUCKET" \
  --key backups/versioning-demo.txt \
  --version-id OLD_VERSION_ID \
  /tmp/old-version.txt
```

Inspect:

```bash
cat /tmp/old-version.txt
```

Expected:

```text
version one
```

---

# Part 21: Monitor S3 security settings

From your local machine, run:

```bash
aws s3api get-public-access-block \
  --bucket "$BUCKET"
```

```bash
aws s3api get-bucket-policy-status \
  --bucket "$BUCKET"
```

```bash
aws s3api get-bucket-encryption \
  --bucket "$BUCKET"
```

```bash
aws s3api get-bucket-versioning \
  --bucket "$BUCKET"
```

```bash
aws s3api get-bucket-lifecycle-configuration \
  --bucket "$BUCKET"
```

```bash
aws s3api get-bucket-ownership-controls \
  --bucket "$BUCKET"
```

Desired state:

| Setting | Required value |
|---|---|
| Public access block | All four values `true` |
| Public bucket status | `false` |
| Encryption | `AES256` |
| Versioning | `Enabled` |
| Object ownership | `BucketOwnerEnforced` |
| Lifecycle | Enabled for `backups/` |
| Insecure transport | Explicitly denied |

---

# Troubleshooting

## EC2 reports `Unable to locate credentials`

Check the instance-profile association:

```bash
aws ec2 describe-iam-instance-profile-associations \
  --filters Name=instance-id,Values="$INSTANCE_ID"
```

On EC2:

```bash
aws sts get-caller-identity
```

IAM propagation can take a short time.

## `AccessDenied` on `backups/`

Check:

- The bucket name is exact.
- The role policy references the correct bucket.
- The object key starts with `backups/`.
- EC2 is using the expected role.

Inspect the role policy:

```bash
aws iam get-role-policy \
  --role-name DevOpsJourneyEC2S3BackupRole \
  --policy-name DevOpsJourneyS3BackupAccess
```

## Backup service cannot access Docker

Check the service groups:

```bash
systemctl show \
  devops-journey-backup.service \
  --property=User,Group,SupplementaryGroups
```

Confirm the Docker socket:

```bash
stat /var/run/docker.sock
```

## Systemd hardening blocks the script

Inspect logs:

```bash
sudo journalctl \
  -u devops-journey-backup.service \
  --no-pager
```

The script should write temporary files only to `/tmp` and read application files from `/opt/devops-journey`.

## S3 storage keeps growing

Versioning retains old objects and delete markers. Verify lifecycle configuration and remember that lifecycle expiration is asynchronous rather than immediate.

---

# Cleanup

If you are continuing to the next lab, retain the bucket and IAM role.

If you are ending the project:

1. Disable the timer on EC2:

   ```bash
   sudo systemctl disable --now devops-journey-backup.timer
   ```

2. In the S3 Console:
   - Select the bucket.
   - Choose **Empty**.
   - Confirm permanent deletion of current objects, versions, and delete markers.
   - Delete the bucket.

3. Disassociate the instance profile:

   ```bash
   aws ec2 disassociate-iam-instance-profile \
     --association-id "$ASSOCIATION_ID"
   ```

4. Remove the role from the instance profile:

   ```bash
   aws iam remove-role-from-instance-profile \
     --instance-profile-name DevOpsJourneyEC2S3BackupProfile \
     --role-name DevOpsJourneyEC2S3BackupRole
   ```

5. Delete the instance profile:

   ```bash
   aws iam delete-instance-profile \
     --instance-profile-name DevOpsJourneyEC2S3BackupProfile
   ```

6. Delete the inline role policy:

   ```bash
   aws iam delete-role-policy \
     --role-name DevOpsJourneyEC2S3BackupRole \
     --policy-name DevOpsJourneyS3BackupAccess
   ```

7. Delete the role:

   ```bash
   aws iam delete-role \
     --role-name DevOpsJourneyEC2S3BackupRole
   ```

Do not use only `aws s3 rm --recursive` for final cleanup of a versioned bucket; noncurrent versions and delete markers can remain.

---

# Final verification

On EC2:

```bash
aws sts get-caller-identity
```

```bash
systemctl list-timers devops-journey-backup.timer
```

```bash
sudo systemctl start devops-journey-backup.service
```

```bash
sudo journalctl \
  -u devops-journey-backup.service \
  --since "10 minutes ago" \
  --no-pager
```

```bash
aws s3 ls \
  "s3://$BUCKET/backups/" \
  --recursive
```

From your local machine:

```bash
aws s3api get-public-access-block --bucket "$BUCKET"
aws s3api get-bucket-policy-status --bucket "$BUCKET"
aws s3api get-bucket-encryption --bucket "$BUCKET"
aws s3api get-bucket-versioning --bucket "$BUCKET"
aws s3api get-bucket-lifecycle-configuration --bucket "$BUCKET"
```

Success means:

- The bucket is private.
- Public access is blocked.
- ACLs are disabled.
- Objects are encrypted.
- Versioning is enabled.
- Insecure HTTP transport is denied.
- Lifecycle retention is configured.
- EC2 uses an IAM role rather than access keys.
- EC2 can access only the intended backup prefix.
- Backups include integrity checksums.
- Downloaded archives pass checksum verification.
- A restore procedure has been tested.
- Daily backups are scheduled through systemd.

## Skills completed

You have now practised:

- Creating and securing S3 buckets
- S3 server-side encryption
- S3 versioning
- Lifecycle retention
- Public-access blocking
- TLS-only bucket policies
- Bucket-owner-enforced object ownership
- EC2 IAM roles and instance profiles
- Temporary workload credentials
- Prefix-scoped IAM permissions
- Docker-volume backups
- SHA-256 integrity verification
- Backup restoration
- Systemd services and timers
- S3 version recovery

**Next lab:** Production observability—send application and system metrics to CloudWatch, create alarms, monitor Docker health, and receive notifications when EC2 or the website fails.

Official reference:

- [Amazon S3 security best practices](https://docs.aws.amazon.com/AmazonS3/latest/userguide/security-best-practices.html)