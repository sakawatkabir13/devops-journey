# Practical Lab 8: AWS Foundations and EC2 Deployment

**Goal:** Secure an AWS account, launch an Ubuntu EC2 instance, install Docker, and deploy your GHCR images to the internet.

**Time:** Approximately 90–120 minutes.

> **Cost warning:** EC2, EBS storage, public IPv4 addresses, data transfer, and other AWS resources can generate charges. Free-tier and credit eligibility varies by account. Create a budget first and terminate resources when you are not using them.

## Architecture

```text
Internet
   │
   │ HTTP port 80
   ▼
AWS Security Group
   │
   ▼
Ubuntu EC2 instance
   │
   │ Docker Compose
   ▼
┌─────────────────────────────────────┐
│ Web container                       │
│ Nginx: port 80                      │
│                                     │
│ /        → static site              │
│ /api/*  → api:5000                  │
└──────────────────┬──────────────────┘
                   │ Docker network
                   ▼
          ┌─────────────────┐
          │ API container   │
          │ Python: 5000    │
          └────────┬────────┘
                   │
                   ▼
             Docker volume
```

---

# Part 1: Secure your AWS account

Before creating resources:

1. Sign in to the AWS Console.
2. Enable **MFA** on the root account.
3. Do not create root-user access keys.
4. Do not use the root account for daily work.
5. Prefer IAM Identity Center and temporary credentials for human access.

AWS recommends federation and temporary credentials for human users rather than long-lived IAM user access keys.

## Create a budget

In the AWS Console:

1. Open **Billing and Cost Management**.
2. Open **Budgets**.
3. Select **Create budget**.
4. Create a monthly cost budget, for example `$5` or an amount appropriate for you.
5. Add email alerts at:
   - 50%
   - 80%
   - 100%

> A budget sends alerts; it does not automatically stop resources.

Also enable billing alerts and check the **Cost Explorer** regularly.

---

# Part 2: Configure AWS CLI access

## 2.1 Check for AWS CLI

```bash
aws --version
```

If AWS CLI v2 is installed, continue to authentication.

## 2.2 Install AWS CLI v2

Install prerequisites:

```bash
sudo apt update
sudo apt install -y curl unzip
```

Detect your CPU architecture:

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

echo "$AWSCLI_ARCH"
```

Download:

```bash
curl \
  "https://awscli.amazonaws.com/awscli-exe-linux-${AWSCLI_ARCH}.zip" \
  -o /tmp/awscliv2.zip
```

Extract and install:

```bash
rm -rf /tmp/aws
unzip -q /tmp/awscliv2.zip -d /tmp
sudo /tmp/aws/install
```

Verify:

```bash
aws --version
```

Clean up:

```bash
rm -rf /tmp/aws /tmp/awscliv2.zip
```

---

# Part 3: Authenticate using temporary credentials

The preferred approach is AWS IAM Identity Center.

After configuring IAM Identity Center in the AWS Console, run:

```bash
aws configure sso --profile devops-lab
```

Enter the SSO start URL, SSO region, account, role, and default AWS region when prompted.

Authenticate:

```bash
aws sso login --profile devops-lab
```

Use the profile:

```bash
export AWS_PROFILE=devops-lab
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""
```

You may select a region closer to you instead of `us-east-1`, but use one region consistently throughout the lab.

Verify your identity:

```bash
aws sts get-caller-identity
```

Expected structure:

```json
{
    "UserId": "...",
    "Account": "...",
    "Arn": "..."
}
```

Verify the configured region:

```bash
aws configure get region --profile "$AWS_PROFILE"
```

> Do not paste AWS credentials into GitHub, Dockerfiles, source code, shell-history commands, or chat messages.

---

# Part 4: Make the production port configurable

Your production Compose file currently publishes port 8080. For EC2, we want HTTP port 80 while preserving 8080 as the local default.

Open the repository:

```bash
cd ~/devops-journey/01-linux-git
git switch main
git pull origin main
git switch -c feat/configurable-web-port
```

Edit:

```bash
nano compose.prod.yaml
```

Change:

```yaml
ports:
  - "8080:80"
```

to:

```yaml
ports:
  - "${WEB_PORT:-8080}:80"
```

Validate the default:

```bash
WEB_IMAGE=devops-journey-web:local \
API_IMAGE=devops-journey-api:local \
docker compose -f compose.prod.yaml config
```

Confirm the resolved port is `8080`.

Validate the EC2 configuration:

```bash
WEB_PORT=80 \
WEB_IMAGE=devops-journey-web:local \
API_IMAGE=devops-journey-api:local \
docker compose -f compose.prod.yaml config
```

Confirm that the resolved port is `80`.

Commit:

```bash
git add compose.prod.yaml
git commit -m "feat: make production web port configurable"
git push -u origin feat/configurable-web-port
```

Create the PR:

```bash
gh pr create \
  --base main \
  --title "Make production web port configurable" \
  --body "Allows EC2 to publish HTTP on port 80 while retaining port 8080 as the local default."
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

Wait for CI and image publication on `main`:

```bash
gh run list --workflow ci.yml --branch main --limit 3
gh run list --workflow publish-images.yml --limit 3
```

---

# Part 5: Discover the AWS network

This lab uses the account’s default VPC.

Find it:

```bash
VPC_ID="$(
  aws ec2 describe-vpcs \
    --filters Name=is-default,Values=true \
    --query 'Vpcs[0].VpcId' \
    --output text
)"

echo "VPC: $VPC_ID"
```

If this prints `None`, your account does not have a default VPC. Stop here rather than launching with guessed networking configuration. You can create a default VPC from the VPC console or build a custom VPC in a later networking lab.

Find a subnet:

```bash
SUBNET_ID="$(
  aws ec2 describe-subnets \
    --filters Name=vpc-id,Values="$VPC_ID" \
    --query 'sort_by(Subnets,&AvailabilityZone)[0].SubnetId' \
    --output text
)"

echo "Subnet: $SUBNET_ID"
```

Inspect it:

```bash
aws ec2 describe-subnets \
  --subnet-ids "$SUBNET_ID" \
  --query 'Subnets[0].{SubnetId:SubnetId,AvailabilityZone:AvailabilityZone,CIDR:CidrBlock,PublicIP:MapPublicIpOnLaunch}' \
  --output table
```

---

# Part 6: Find an official Ubuntu AMI

This lab uses Ubuntu Server 24.04 LTS on x86-64.

Find the latest available image owned by Canonical:

```bash
AMI_ID="$(
  aws ec2 describe-images \
    --owners 099720109477 \
    --filters \
      "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*" \
      "Name=state,Values=available" \
      "Name=architecture,Values=x86_64" \
    --query 'sort_by(Images,&CreationDate)[-1].ImageId' \
    --output text
)"

echo "AMI: $AMI_ID"
```

Do not continue if it prints `None`.

Inspect the image:

```bash
aws ec2 describe-images \
  --image-ids "$AMI_ID" \
  --query 'Images[0].{ID:ImageId,Name:Name,Created:CreationDate,Architecture:Architecture,RootDevice:RootDeviceName}' \
  --output table
```

Save the root-device name:

```bash
ROOT_DEVICE="$(
  aws ec2 describe-images \
    --image-ids "$AMI_ID" \
    --query 'Images[0].RootDeviceName' \
    --output text
)"

echo "Root device: $ROOT_DEVICE"
```

---

# Part 7: Create an SSH key pair

Create a unique key name:

```bash
KEY_NAME="devops-journey-$(date +%Y%m%d-%H%M%S)"
KEY_PATH="$HOME/.ssh/${KEY_NAME}.pem"

echo "$KEY_NAME"
echo "$KEY_PATH"
```

Create the local SSH directory:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
```

Create an Ed25519 key pair in AWS and save the private key:

```bash
umask 077

aws ec2 create-key-pair \
  --key-name "$KEY_NAME" \
  --key-type ed25519 \
  --key-format pem \
  --query 'KeyMaterial' \
  --output text > "$KEY_PATH"
```

Set the required permissions:

```bash
chmod 400 "$KEY_PATH"
```

Verify the local file:

```bash
stat "$KEY_PATH"
```

Verify the AWS key pair:

```bash
aws ec2 describe-key-pairs \
  --key-names "$KEY_NAME" \
  --query 'KeyPairs[0].{Name:KeyName,Fingerprint:KeyFingerprint}' \
  --output table
```

> The `.pem` file is a private SSH key. Never commit it, upload it, email it, or paste it into GitHub Actions.

---

# Part 8: Create a security group

Find your current public IPv4 address:

```bash
MY_IP="$(curl -4 -fsS https://checkip.amazonaws.com | tr -d '\n')"
echo "Current public IP: $MY_IP"
```

Create a unique security-group name:

```bash
SG_NAME="devops-journey-web-$(date +%Y%m%d-%H%M%S)"
```

Create the group:

```bash
SG_ID="$(
  aws ec2 create-security-group \
    --group-name "$SG_NAME" \
    --description "DevOps Journey EC2 web server" \
    --vpc-id "$VPC_ID" \
    --query 'GroupId' \
    --output text
)"

echo "Security group: $SG_ID"
```

Tag it:

```bash
aws ec2 create-tags \
  --resources "$SG_ID" \
  --tags \
    Key=Name,Value=devops-journey-web \
    Key=Project,Value=devops-journey \
    Key=Environment,Value=lab
```

## Allow SSH only from your IP

```bash
aws ec2 authorize-security-group-ingress \
  --group-id "$SG_ID" \
  --ip-permissions \
  "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=${MY_IP}/32,Description='SSH from learner IP'}]"
```

## Allow HTTP from the internet

```bash
aws ec2 authorize-security-group-ingress \
  --group-id "$SG_ID" \
  --ip-permissions \
  "IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0,Description='Public HTTP'}]"
```

Inspect the rules:

```bash
aws ec2 describe-security-groups \
  --group-ids "$SG_ID" \
  --query 'SecurityGroups[0].IpPermissions' \
  --output json
```

The intended exposure is:

| Port | Source | Purpose |
|---|---|---|
| `22` | `YOUR_IP/32` | SSH administration |
| `80` | `0.0.0.0/0` | Public HTTP |
| `5000` | Not exposed | Internal API |
| `8080` | Not exposed | Local development only |

Do not open SSH to `0.0.0.0/0`.

---

# Part 9: Launch the EC2 instance

This lab uses `t3.micro`. It may incur charges. Verify instance pricing and account credit/free-tier eligibility before continuing.

Launch an encrypted 10 GiB `gp3` root volume:

```bash
INSTANCE_ID="$(
  aws ec2 run-instances \
    --image-id "$AMI_ID" \
    --instance-type t3.micro \
    --count 1 \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --subnet-id "$SUBNET_ID" \
    --associate-public-ip-address \
    --metadata-options HttpTokens=required,HttpEndpoint=enabled \
    --block-device-mappings \
      "DeviceName=${ROOT_DEVICE},Ebs={VolumeSize=10,VolumeType=gp3,Encrypted=true,DeleteOnTermination=true}" \
    --tag-specifications \
      'ResourceType=instance,Tags=[{Key=Name,Value=devops-journey},{Key=Project,Value=devops-journey},{Key=Environment,Value=lab}]' \
      'ResourceType=volume,Tags=[{Key=Name,Value=devops-journey-root},{Key=Project,Value=devops-journey},{Key=Environment,Value=lab}]' \
    --query 'Instances[0].InstanceId' \
    --output text
)"

echo "Instance: $INSTANCE_ID"
```

Security settings used here include:

- Encrypted EBS storage
- Root-volume deletion when the instance is terminated
- IMDSv2 tokens required
- SSH restricted to your IP
- No public access to the API container

Wait for it to run:

```bash
aws ec2 wait instance-running \
  --instance-ids "$INSTANCE_ID"
```

Wait for AWS health checks:

```bash
aws ec2 wait instance-status-ok \
  --instance-ids "$INSTANCE_ID"
```

Get the public IP:

```bash
PUBLIC_IP="$(
  aws ec2 describe-instances \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text
)"

echo "Public IP: $PUBLIC_IP"
```

Inspect the instance:

```bash
aws ec2 describe-instances \
  --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].{ID:InstanceId,State:State.Name,Type:InstanceType,PublicIP:PublicIpAddress,PrivateIP:PrivateIpAddress,AZ:Placement.AvailabilityZone}' \
  --output table
```

---

# Part 10: Connect with SSH

Connect from your Ubuntu machine:

```bash
ssh -i "$KEY_PATH" "ubuntu@$PUBLIC_IP"
```

On the first connection, SSH asks whether you trust the host key. Verify that you are connecting to the expected IP, then type:

```text
yes
```

Check the server:

```bash
whoami
hostname
pwd
cat /etc/os-release
uname -a
```

Expected user:

```text
ubuntu
```

---

# Part 11: Update the EC2 instance

Inside the SSH session:

```bash
sudo apt update
sudo apt upgrade -y
```

Check whether a reboot is required:

```bash
if [ -f /var/run/reboot-required ]; then
    cat /var/run/reboot-required
fi
```

If a reboot is required:

```bash
sudo reboot
```

Your SSH session disconnects. Wait briefly and reconnect from the local machine:

```bash
ssh -i "$KEY_PATH" "ubuntu@$PUBLIC_IP"
```

---

# Part 12: Install Docker on EC2

Inside the EC2 SSH session:

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
```

Add Docker’s signing key:

```bash
sudo curl -fsSL \
  https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc

sudo chmod a+r /etc/apt/keyrings/docker.asc
```

Add Docker’s repository:

```bash
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
```

Install Docker:

```bash
sudo apt update

sudo apt install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin
```

Enable it at boot:

```bash
sudo systemctl enable --now docker
```

Check:

```bash
sudo systemctl status docker --no-pager
sudo docker run --rm hello-world
sudo docker compose version
```

Add the Ubuntu user to the Docker group:

```bash
sudo usermod -aG docker ubuntu
```

Exit:

```bash
exit
```

Reconnect so group membership takes effect:

```bash
ssh -i "$KEY_PATH" "ubuntu@$PUBLIC_IP"
```

Verify:

```bash
groups
docker version
docker compose version
```

> Membership in the Docker group effectively grants root-level privileges.

---

# Part 13: Make GHCR images available

The simplest approach for this learning project is to make both GHCR packages public:

1. Open your GitHub profile.
2. Open **Packages**.
3. Select `devops-journey-web`.
4. Open **Package settings**.
5. Change visibility to **Public**.
6. Repeat for `devops-journey-api`.

If you keep the packages private, EC2 needs a GitHub token with `read:packages`. Do not put that token in `compose.prod.yaml`, `.env`, or Git.

---

# Part 14: Prepare deployment files locally

Run these commands on your local Ubuntu machine, not inside EC2.

Open a second terminal if your SSH session is still active:

```bash
cd ~/devops-journey/01-linux-git
git switch main
git pull origin main
```

Get your image owner and commit:

```bash
OWNER="$(gh api user --jq '.login' | tr '[:upper:]' '[:lower:]')"
SHORT_SHA="$(git rev-parse --short=12 HEAD)"

echo "Owner: $OWNER"
echo "Commit: $SHORT_SHA"
```

Create a temporary environment file:

```bash
printf '%s\n' \
  "WEB_IMAGE=ghcr.io/$OWNER/devops-journey-web:sha-$SHORT_SHA" \
  "API_IMAGE=ghcr.io/$OWNER/devops-journey-api:sha-$SHORT_SHA" \
  "WEB_PORT=80" \
  > /tmp/devops-journey.env
```

Inspect it:

```bash
cat /tmp/devops-journey.env
```

It should not contain passwords or tokens.

Copy the files to EC2:

```bash
scp \
  -i "$KEY_PATH" \
  compose.prod.yaml \
  "ubuntu@$PUBLIC_IP:/tmp/compose.prod.yaml"
```

```bash
scp \
  -i "$KEY_PATH" \
  /tmp/devops-journey.env \
  "ubuntu@$PUBLIC_IP:/tmp/devops-journey.env"
```

Delete the local temporary file:

```bash
rm /tmp/devops-journey.env
```

---

# Part 15: Deploy the application on EC2

Connect:

```bash
ssh -i "$KEY_PATH" "ubuntu@$PUBLIC_IP"
```

Create the application directory:

```bash
sudo mkdir -p /opt/devops-journey
sudo chown ubuntu:ubuntu /opt/devops-journey
```

Move the files:

```bash
mv /tmp/compose.prod.yaml /opt/devops-journey/
mv /tmp/devops-journey.env /opt/devops-journey/.env
chmod 600 /opt/devops-journey/.env
```

Enter the directory:

```bash
cd /opt/devops-journey
```

Inspect the resolved configuration:

```bash
docker compose -f compose.prod.yaml config
```

Confirm:

- The web image uses your GHCR commit tag.
- The API image uses the same commit tag.
- The published web port is `80`.
- API port 5000 is not published.

Pull the images:

```bash
docker compose -f compose.prod.yaml pull
```

Start the application:

```bash
docker compose -f compose.prod.yaml up \
  --detach \
  --wait
```

Inspect:

```bash
docker compose -f compose.prod.yaml ps
```

Expected:

```text
api    Up ... (healthy)
web    Up ... (healthy)
```

---

# Part 16: Test from inside EC2

Test Nginx:

```bash
curl --fail http://localhost/nginx-health
```

Test the API through Nginx:

```bash
curl --fail http://localhost/api/health
```

Test API information:

```bash
curl --fail http://localhost/api/info
```

Inspect logs:

```bash
docker compose -f compose.prod.yaml logs --tail=50
```

Check listening ports:

```bash
sudo ss -lntp
```

You should see port 80 listening. Port 5000 should not be exposed on the host.

---

# Part 17: Test from your local machine

Exit SSH:

```bash
exit
```

From your local terminal:

```bash
curl -i "http://$PUBLIC_IP/nginx-health"
```

Test the API:

```bash
curl -i "http://$PUBLIC_IP/api/health"
```

Test the website:

```bash
curl -I "http://$PUBLIC_IP/"
```

Open in your browser:

```text
http://YOUR_EC2_PUBLIC_IP
```

You should see your DevOps Journey website running from AWS.

> This deployment uses unencrypted HTTP. Do not send passwords, tokens, personal data, or other sensitive information through it. HTTPS will be added after introducing a domain name.

---

# Part 18: Understand the AWS network path

When you visit the public IP:

```text
Browser
   │
   │ TCP port 80
   ▼
EC2 public IPv4
   │
   ▼
Security group inbound rule
   │
   ▼
Ubuntu host port 80
   │
   ▼
Docker port mapping
   │
   ▼
Nginx container port 80
   │
   ├── static files
   └── /api/* → API container port 5000
```

Security groups are stateful. Response traffic for an allowed inbound connection is automatically permitted.

---

# Part 19: Verify restart behavior

Because Docker is enabled at boot and containers use `restart: unless-stopped`, they should return after an instance reboot.

Reboot from your local terminal:

```bash
aws ec2 reboot-instances \
  --instance-ids "$INSTANCE_ID"
```

Wait:

```bash
aws ec2 wait instance-status-ok \
  --instance-ids "$INSTANCE_ID"
```

Test:

```bash
curl --retry 10 \
  --retry-delay 3 \
  --retry-all-errors \
  --fail \
  "http://$PUBLIC_IP/nginx-health"
```

Reconnect and inspect:

```bash
ssh -i "$KEY_PATH" "ubuntu@$PUBLIC_IP"
```

```bash
cd /opt/devops-journey
docker compose -f compose.prod.yaml ps
```

---

# Part 20: Basic troubleshooting

## SSH times out

Check your current public IP:

```bash
curl -4 -fsS https://checkip.amazonaws.com
```

If it changed, update the security-group SSH rule. Never solve this by permanently opening port 22 to the internet.

Check instance status:

```bash
aws ec2 describe-instance-status \
  --instance-ids "$INSTANCE_ID" \
  --include-all-instances
```

## `docker pull` reports denied

The GHCR package is probably private or the image name/tag is incorrect.

Verify the `.env` file:

```bash
cd /opt/devops-journey
grep IMAGE .env
```

Do not display this file later if you add secrets to it.

## Website connection is refused

On EC2:

```bash
docker compose -f /opt/devops-journey/compose.prod.yaml ps
sudo ss -lntp | grep ':80'
```

Check the security group:

```bash
aws ec2 describe-security-groups \
  --group-ids "$SG_ID"
```

## Nginx returns `502 Bad Gateway`

Check the API:

```bash
cd /opt/devops-journey
docker compose -f compose.prod.yaml ps
docker compose -f compose.prod.yaml logs api
docker compose -f compose.prod.yaml logs web
```

## Containers are unhealthy

Inspect health details:

```bash
docker inspect \
  --format '{{json .State.Health}}' \
  "$(docker compose -f compose.prod.yaml ps -q api)"
```

---

# Part 21: Stop versus terminate

## Stop

```bash
aws ec2 stop-instances --instance-ids "$INSTANCE_ID"
aws ec2 wait instance-stopped --instance-ids "$INSTANCE_ID"
```

Stopping preserves the EBS volume, but storage can continue generating charges. The automatically assigned public IP will normally change after the next start.

Start later:

```bash
aws ec2 start-instances --instance-ids "$INSTANCE_ID"
aws ec2 wait instance-status-ok --instance-ids "$INSTANCE_ID"
```

Retrieve the new IP:

```bash
PUBLIC_IP="$(
  aws ec2 describe-instances \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text
)"
```

## Terminate

Termination deletes the EC2 instance and its root volume when `DeleteOnTermination` is enabled.

Use termination if you are not continuing soon:

```bash
aws ec2 terminate-instances \
  --instance-ids "$INSTANCE_ID"
```

Wait:

```bash
aws ec2 wait instance-terminated \
  --instance-ids "$INSTANCE_ID"
```

Delete the security group:

```bash
aws ec2 delete-security-group \
  --group-id "$SG_ID"
```

Delete the AWS key-pair record:

```bash
aws ec2 delete-key-pair \
  --key-name "$KEY_NAME"
```

Delete the local private key:

```bash
rm "$KEY_PATH"
```

Verify no tagged instances remain:

```bash
aws ec2 describe-instances \
  --filters \
    Name=tag:Project,Values=devops-journey \
    Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[].{ID:InstanceId,State:State.Name,PublicIP:PublicIpAddress}' \
  --output table
```

Check for unattached volumes:

```bash
aws ec2 describe-volumes \
  --filters Name=status,Values=available \
  --query 'Volumes[].{ID:VolumeId,Size:Size,Type:VolumeType,Created:CreateTime}' \
  --output table
```

---

# Final verification

From your local Ubuntu machine:

```bash
aws sts get-caller-identity
```

```bash
aws ec2 describe-instances \
  --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].{State:State.Name,PublicIP:PublicIpAddress,Type:InstanceType}' \
  --output table
```

```bash
curl --fail "http://$PUBLIC_IP/nginx-health"
curl --fail "http://$PUBLIC_IP/api/health"
curl --fail "http://$PUBLIC_IP/api/info"
```

On EC2:

```bash
cd /opt/devops-journey

docker compose -f compose.prod.yaml config
docker compose -f compose.prod.yaml ps
docker compose -f compose.prod.yaml images
docker compose -f compose.prod.yaml logs --tail=20

sudo ss -lntp
```

Success means:

- Root MFA is enabled.
- CLI access uses temporary credentials.
- A cost budget exists.
- SSH is restricted to your current public IP.
- Only HTTP port 80 is publicly exposed.
- EC2 requires IMDSv2.
- The EBS root volume is encrypted.
- Docker starts automatically.
- Both containers are healthy.
- EC2 runs commit-specific GHCR images.
- The website is reachable through the EC2 public IP.
- API port 5000 is not exposed publicly.
- You know how to terminate every created resource.

## Skills completed

You have now practised:

- AWS account and IAM security fundamentals
- AWS CLI SSO authentication
- Regions, VPCs, subnets, and AMIs
- EC2 key pairs
- Security-group ingress rules
- Launching and tagging EC2 instances
- Encrypted EBS volumes
- IMDSv2 enforcement
- SSH administration
- Installing Docker on a cloud server
- Pulling deployment artifacts from GHCR
- Deploying with production Compose
- Diagnosing cloud networking problems
- Managing AWS cost and resource cleanup

**Next lab:** Automated EC2 deployment—configure GitHub Actions to deploy successful images over SSH, store secrets safely, verify health, and automatically roll back when deployment fails.

Official references:

- [AWS CLI v2 installation](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- [AWS IAM security best practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html)
- [Docker Engine installation on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)