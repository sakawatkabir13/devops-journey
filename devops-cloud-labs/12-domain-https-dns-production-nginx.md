# Practical Lab 12: Domain, HTTPS, DNS, and Production Nginx

**Goal:** Give your application a stable IP address and domain name, terminate TLS with host-level Nginx, redirect HTTP to HTTPS, and automate certificate renewal.

**Time:** Approximately 2–3 hours.

## Final architecture

```text
User
 │
 │ https://app.example.com:443
 ▼
Route 53 DNS
 │
 ▼
EC2 Elastic IP
 │
 ▼
AWS security group
 │ ports 80 and 443
 ▼
Host Nginx
 │
 │ TLS termination
 │ HTTP → HTTPS redirect
 │ reverse proxy
 ▼
127.0.0.1:8080
 │
 ▼
Docker Nginx
 │
 ├── Static website
 └── /api/* → API container
```

The Docker web port will be bound only to `127.0.0.1`, so it cannot be reached directly from the internet.

> **Prerequisite:** You need a domain name you control. Route 53 hosted zones, domain registration, Elastic IP/public IPv4 usage, and DNS queries can generate charges.

---

# Part 1: Set the environment

On your local Ubuntu machine:

```bash
export AWS_PROFILE=devops-lab
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""
```

Set your domain values:

```bash
ROOT_DOMAIN="example.com"
APP_DOMAIN="devops.${ROOT_DOMAIN}"
CERT_EMAIL="your-email@example.com"
```

Replace `example.com` and the email address with your real values.

Rediscover EC2:

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

OLD_PUBLIC_IP="$(
  aws ec2 describe-instances \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text
)"

echo "Instance:      $INSTANCE_ID"
echo "Security group: $SG_ID"
echo "Current IP:     $OLD_PUBLIC_IP"
echo "Domain:         $APP_DOMAIN"
```

---

# Part 2: Save the EC2 host key before changing IP

Associating an Elastic IP replaces the instance’s current public IP.

Retrieve the existing, already trusted host key:

```bash
ssh \
  -i "$KEY_PATH" \
  -o IdentitiesOnly=yes \
  "ubuntu@$OLD_PUBLIC_IP" \
  'sudo cat /etc/ssh/ssh_host_ed25519_key.pub' \
  > /tmp/ec2-ed25519-host-key.pub
```

Verify its fingerprint:

```bash
ssh-keygen -lf /tmp/ec2-ed25519-host-key.pub
```

Keep this file temporarily. It will be used to create a verified `known_hosts` entry for the Elastic IP.

---

# Part 3: Allocate an Elastic IP

Check whether the instance already has an Elastic IP:

```bash
aws ec2 describe-addresses \
  --filters Name=instance-id,Values="$INSTANCE_ID" \
  --query 'Addresses[].{IP:PublicIp,Allocation:AllocationId,Association:AssociationId}' \
  --output table
```

If no address is returned, allocate one:

```bash
ALLOCATION_ID="$(
  aws ec2 allocate-address \
    --domain vpc \
    --tag-specifications \
      'ResourceType=elastic-ip,Tags=[
        {Key=Name,Value=devops-journey},
        {Key=Project,Value=devops-journey},
        {Key=Environment,Value=production}
      ]' \
    --query AllocationId \
    --output text
)"

echo "$ALLOCATION_ID"
```

Associate it:

```bash
EIP_ASSOCIATION_ID="$(
  aws ec2 associate-address \
    --instance-id "$INSTANCE_ID" \
    --allocation-id "$ALLOCATION_ID" \
    --query AssociationId \
    --output text
)"

echo "$EIP_ASSOCIATION_ID"
```

Get the stable address:

```bash
ELASTIC_IP="$(
  aws ec2 describe-addresses \
    --allocation-ids "$ALLOCATION_ID" \
    --query 'Addresses[0].PublicIp' \
    --output text
)"

PUBLIC_IP="$ELASTIC_IP"

echo "Elastic IP: $PUBLIC_IP"
```

> Do not leave an Elastic IP unattached. Public IPv4 addresses can incur charges, including when reserved but unused.

---

# Part 4: Update SSH verification

Build a verified host entry using the host key collected before the IP change:

```bash
awk \
  -v host="$PUBLIC_IP" \
  '{print host, $1, $2}' \
  /tmp/ec2-ed25519-host-key.pub \
  > /tmp/ec2-known-hosts
```

Verify the fingerprint is unchanged:

```bash
ssh-keygen -lf /tmp/ec2-ed25519-host-key.pub
```

Install the entry locally:

```bash
ssh-keygen -R "$PUBLIC_IP" 2>/dev/null || true
cat /tmp/ec2-known-hosts >> ~/.ssh/known_hosts
```

Test strict verification:

```bash
ssh \
  -i "$KEY_PATH" \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  "ubuntu@$PUBLIC_IP" \
  'hostname'
```

Update the GitHub production secrets:

```bash
cd ~/devops-journey/01-linux-git
```

```bash
gh secret set EC2_HOST \
  --env production \
  --body "$PUBLIC_IP"
```

```bash
gh secret set EC2_KNOWN_HOSTS \
  --env production \
  < /tmp/ec2-known-hosts
```

Delete temporary files:

```bash
rm \
  /tmp/ec2-ed25519-host-key.pub \
  /tmp/ec2-known-hosts
```

---

# Part 5: Open HTTPS in the security group

Port 80 must remain open for:

- HTTP-to-HTTPS redirects
- Let’s Encrypt HTTP validation
- Certificate renewal validation

Authorize HTTPS:

```bash
aws ec2 authorize-security-group-ingress \
  --group-id "$SG_ID" \
  --ip-permissions \
  "IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0,Description='Public HTTPS'}]"
```

If AWS reports a duplicate rule, verify the existing rule rather than adding another.

Inspect:

```bash
aws ec2 describe-security-groups \
  --group-ids "$SG_ID" \
  --query 'SecurityGroups[0].IpPermissions[].{
    Protocol:IpProtocol,
    From:FromPort,
    To:ToPort,
    IPv4:IpRanges[].CidrIp
  }' \
  --output table
```

Public ports should now be:

| Port | Purpose |
|---|---|
| `80` | Redirect and certificate validation |
| `443` | HTTPS |
| `22` | SSH, restricted to authorized `/32` addresses |

---

# Part 6: Configure DNS in Route 53

## Find the hosted zone

```bash
ZONE_ID="$(
  aws route53 list-hosted-zones-by-name \
    --dns-name "${ROOT_DOMAIN}." \
    --query "HostedZones[?Name=='${ROOT_DOMAIN}.'] | [0].Id" \
    --output text
)"

ZONE_ID="${ZONE_ID##*/}"

echo "Hosted zone: $ZONE_ID"
```

If this returns `None`, create or import a Route 53 hosted zone first.

When the domain is registered elsewhere, update the registrar’s nameservers to the NS records assigned to the Route 53 hosted zone.

Inspect nameservers:

```bash
aws route53 list-resource-record-sets \
  --hosted-zone-id "$ZONE_ID" \
  --query 'ResourceRecordSets[?Type==`NS`]' \
  --output table
```

## Create the A record

Generate the change:

```bash
export APP_DOMAIN PUBLIC_IP
```

```bash
python3 - <<'PY' > /tmp/route53-change.json
import json
import os

domain = os.environ["APP_DOMAIN"]
public_ip = os.environ["PUBLIC_IP"]

change = {
    "Comment": "Point DevOps Journey application to EC2",
    "Changes": [
        {
            "Action": "UPSERT",
            "ResourceRecordSet": {
                "Name": domain,
                "Type": "A",
                "TTL": 300,
                "ResourceRecords": [
                    {
                        "Value": public_ip
                    }
                ],
            },
        }
    ],
}

print(json.dumps(change, indent=2))
PY
```

Apply:

```bash
CHANGE_ID="$(
  aws route53 change-resource-record-sets \
    --hosted-zone-id "$ZONE_ID" \
    --change-batch file:///tmp/route53-change.json \
    --query ChangeInfo.Id \
    --output text
)"

echo "$CHANGE_ID"
```

Wait:

```bash
aws route53 wait resource-record-sets-changed \
  --id "$CHANGE_ID"
```

Delete the temporary file:

```bash
rm /tmp/route53-change.json
```

---

# Part 7: Verify DNS

Install DNS utilities if necessary:

```bash
sudo apt update
sudo apt install -y dnsutils
```

Query your default resolver:

```bash
dig +short "$APP_DOMAIN"
```

Query public resolvers:

```bash
dig +short "$APP_DOMAIN" @1.1.1.1
dig +short "$APP_DOMAIN" @8.8.8.8
```

Expected:

```text
YOUR_ELASTIC_IP
```

Verify with Route 53:

```bash
aws route53 test-dns-answer \
  --hosted-zone-id "$ZONE_ID" \
  --record-name "$APP_DOMAIN" \
  --record-type A
```

Do not request a certificate until public DNS resolves to the correct EC2 address.

---

# Part 8: Prepare the production networking change

The desired design is:

```text
Host Nginx: 0.0.0.0:80 and 0.0.0.0:443
Docker Nginx: 127.0.0.1:8080
```

Create a branch:

```bash
cd ~/devops-journey/01-linux-git
git switch main
git pull origin main
git switch -c feat/add-https-edge-proxy
```

## Make the bind address configurable

Edit:

```bash
nano compose.prod.yaml
```

Change the web port configuration to:

```yaml
ports:
  - "${WEB_BIND_ADDRESS:-0.0.0.0}:${WEB_PORT:-8080}:80"
```

This results in:

```text
Local development default:
0.0.0.0:8080 → container:80

EC2 production:
127.0.0.1:8080 → container:80
```

## Update automated deployment

Edit:

```bash
nano scripts/deploy-ec2.sh
```

Find the environment-file creation:

```bash
printf '%s\n' \
    "WEB_IMAGE=ghcr.io/$IMAGE_OWNER/devops-journey-web:$IMAGE_TAG" \
    "API_IMAGE=ghcr.io/$IMAGE_OWNER/devops-journey-api:$IMAGE_TAG" \
    "WEB_PORT=80" \
    > "$NEW_ENV"
```

Replace it with:

```bash
printf '%s\n' \
    "WEB_IMAGE=ghcr.io/$IMAGE_OWNER/devops-journey-web:$IMAGE_TAG" \
    "API_IMAGE=ghcr.io/$IMAGE_OWNER/devops-journey-api:$IMAGE_TAG" \
    "WEB_BIND_ADDRESS=127.0.0.1" \
    "WEB_PORT=8080" \
    > "$NEW_ENV"
```

Validate:

```bash
bash -n scripts/deploy-ec2.sh
```

Validate Compose:

```bash
WEB_IMAGE=devops-journey-web:local \
API_IMAGE=devops-journey-api:local \
WEB_BIND_ADDRESS=127.0.0.1 \
WEB_PORT=8080 \
docker compose -f compose.prod.yaml config
```

---

# Part 9: Preserve the original client IP

Once host Nginx proxies to Docker Nginx, the Docker container sees the Docker gateway as the connection source unless real-IP processing is configured.

Edit:

```bash
nano nginx/default.conf
```

Add this before the `limit_req_zone` directive:

```nginx
map $http_x_forwarded_proto $original_scheme {
    default $http_x_forwarded_proto;
    ""      $scheme;
}
```

Inside the `server` block, near the top, add:

```nginx
# Trust proxy traffic from the private Docker network.
# The Docker web port is bound only to 127.0.0.1 on the host.
set_real_ip_from 172.16.0.0/12;
real_ip_header X-Forwarded-For;
real_ip_recursive on;
```

Find:

```nginx
proxy_set_header X-Forwarded-Proto $scheme;
```

Replace it with:

```nginx
proxy_set_header X-Forwarded-Proto $original_scheme;
```

This preserves:

- Original client IP for logging
- Per-client API rate limiting
- Original HTTPS scheme for the API

---

# Part 10: Update deployment verification to use HTTPS

Create a GitHub production variable:

```bash
gh variable set APP_URL \
  --env production \
  --body "https://$APP_DOMAIN"
```

Edit:

```bash
nano .github/workflows/deploy-ec2.yml
```

In the job-level `env` section, add:

```yaml
APP_URL: ${{ vars.APP_URL }}
```

Find the public verification commands:

```yaml
"http://$EC2_HOST/nginx-health"
```

and:

```yaml
"http://$EC2_HOST/api/health"
```

Replace them with:

```yaml
"$APP_URL/nginx-health"
```

and:

```yaml
"$APP_URL/api/health"
```

The deployment workflow will now verify:

- DNS resolution
- TLS certificate validity
- Host Nginx
- Docker Nginx
- API health

Do not merge yet. First, establish HTTPS manually.

---

# Part 11: Copy the updated Compose file to EC2

Copy the uncommitted but validated file:

```bash
scp \
  -i "$KEY_PATH" \
  compose.prod.yaml \
  "ubuntu@$PUBLIC_IP:/tmp/compose.prod.yaml"
```

Connect:

```bash
ssh -i "$KEY_PATH" "ubuntu@$PUBLIC_IP"
```

Enter the project:

```bash
cd /opt/devops-journey
```

Back up the current files:

```bash
cp compose.prod.yaml compose.prod.yaml.before-https
cp .env .env.before-https
```

Install the new Compose file:

```bash
cp /tmp/compose.prod.yaml compose.prod.yaml
```

Edit:

```bash
nano .env
```

It should contain:

```text
WEB_IMAGE=ghcr.io/YOUR_USERNAME/devops-journey-web:sha-YOUR_SHA
API_IMAGE=ghcr.io/YOUR_USERNAME/devops-journey-api:sha-YOUR_SHA
WEB_BIND_ADDRESS=127.0.0.1
WEB_PORT=8080
```

Preserve your existing image values. Change only the bind address and port.

---

# Part 12: Install host-level Nginx

Stop the Docker web container briefly to release port 80:

```bash
docker compose -f compose.prod.yaml stop web
```

Install host Nginx:

```bash
sudo apt update
sudo apt install -y nginx
```

Start the Docker application on loopback port 8080:

```bash
docker compose -f compose.prod.yaml up \
  --detach \
  --wait
```

Verify the Docker service locally:

```bash
curl --fail http://127.0.0.1:8080/nginx-health
curl --fail http://127.0.0.1:8080/api/health
```

Confirm it is bound only to loopback:

```bash
sudo ss -lntp | grep ':8080'
```

Expected structure:

```text
127.0.0.1:8080
```

You should not see:

```text
0.0.0.0:8080
```

---

# Part 13: Configure host Nginx as the edge proxy

Create:

```bash
sudo nano /etc/nginx/sites-available/devops-journey
```

Add, replacing `devops.example.com`:

```nginx
server {
    listen 80;
    listen [::]:80;

    server_name devops.example.com;

    server_tokens off;

    access_log /var/log/nginx/devops-journey-access.log;
    error_log /var/log/nginx/devops-journey-error.log warn;

    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;

        proxy_connect_timeout 3s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Request-ID $request_id;
    }
}
```

Disable the default site:

```bash
sudo rm -f /etc/nginx/sites-enabled/default
```

Enable the application:

```bash
sudo ln -s \
  /etc/nginx/sites-available/devops-journey \
  /etc/nginx/sites-enabled/devops-journey
```

Test:

```bash
sudo nginx -t
```

Expected:

```text
syntax is ok
test is successful
```

Reload:

```bash
sudo systemctl reload nginx
```

Verify:

```bash
curl --fail \
  --header "Host: $APP_DOMAIN" \
  http://127.0.0.1/nginx-health
```

From your local machine, HTTP should work:

```bash
exit
```

```bash
curl -i "http://$APP_DOMAIN/nginx-health"
```

Do not continue until this returns `200 OK`.

---

# Part 14: Install Certbot

Reconnect:

```bash
ssh -i "$KEY_PATH" "ubuntu@$PUBLIC_IP"
```

Ensure Snap is available:

```bash
sudo apt update
sudo apt install -y snapd
sudo snap install core
sudo snap refresh core
```

If an old operating-system Certbot package is installed, remove it:

```bash
sudo apt remove -y certbot 2>/dev/null || true
```

Install Certbot:

```bash
sudo snap install --classic certbot
```

Create the command link if necessary:

```bash
if [[ ! -e /usr/bin/certbot ]]; then
    sudo ln -s /snap/bin/certbot /usr/bin/certbot
fi
```

Verify:

```bash
certbot --version
```

---

# Part 15: Obtain the TLS certificate

Verify DNS one more time:

```bash
getent hosts "$APP_DOMAIN"
```

Request and install the certificate:

```bash
sudo certbot \
  --nginx \
  --non-interactive \
  --agree-tos \
  --no-eff-email \
  --redirect \
  --email "$CERT_EMAIL" \
  --domains "$APP_DOMAIN"
```

Certbot should:

1. Contact Let’s Encrypt.
2. Prove domain control through port 80.
3. Download the certificate.
4. Configure Nginx to listen on port 443.
5. Configure HTTP-to-HTTPS redirect.
6. Reload Nginx.

> Certificates are recorded in public certificate-transparency logs. Do not use a hostname that you consider secret.

---

# Part 16: Inspect the TLS configuration

Test:

```bash
sudo nginx -t
```

Reload:

```bash
sudo systemctl reload nginx
```

Inspect the final site configuration:

```bash
sudo nginx -T |
  less
```

Press `q` to exit.

List certificates:

```bash
sudo certbot certificates
```

Expected certificate path:

```text
/etc/letsencrypt/live/YOUR_DOMAIN/fullchain.pem
```

Test HTTPS locally:

```bash
curl --fail "https://$APP_DOMAIN/nginx-health"
curl --fail "https://$APP_DOMAIN/api/health"
```

---

# Part 17: Add a cautious HSTS policy

Only add HSTS after HTTPS works correctly.

Edit:

```bash
sudo nano /etc/nginx/sites-available/devops-journey
```

Inside the HTTPS `server` block created by Certbot, add:

```nginx
add_header Strict-Transport-Security "max-age=86400" always;
```

This starts with a one-day policy.

Do not add `includeSubDomains` or `preload` until every relevant subdomain supports permanent HTTPS.

Test and reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

---

# Part 18: Test HTTP redirect and HTTPS

From your local machine:

```bash
exit
```

Test HTTP:

```bash
curl -I "http://$APP_DOMAIN/"
```

Expected:

```text
HTTP/1.1 301 Moved Permanently
```

or another permanent redirect status pointing to:

```text
https://YOUR_DOMAIN/
```

Test HTTPS:

```bash
curl -I "https://$APP_DOMAIN/"
```

Expected:

```text
HTTP/2 200
```

or:

```text
HTTP/1.1 200 OK
```

Test health endpoints:

```bash
curl --fail "https://$APP_DOMAIN/nginx-health"
curl --fail "https://$APP_DOMAIN/api/health"
curl --fail "https://$APP_DOMAIN/api/info"
```

Test the certificate:

```bash
openssl s_client \
  -connect "${APP_DOMAIN}:443" \
  -servername "$APP_DOMAIN" \
  </dev/null
```

Look for:

```text
Verify return code: 0 (ok)
```

---

# Part 19: Verify security headers

Run:

```bash
curl -I "https://$APP_DOMAIN/"
```

Look for:

```text
Strict-Transport-Security: max-age=86400
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Content-Security-Policy: ...
Referrer-Policy: strict-origin-when-cross-origin
```

The HSTS header comes from host Nginx. The other headers come from Docker Nginx and pass through the proxy.

---

# Part 20: Test certificate renewal

Run on EC2:

```bash
ssh -i "$KEY_PATH" "ubuntu@$PUBLIC_IP"
```

Test renewal safely:

```bash
sudo certbot renew --dry-run
```

Expected:

```text
Congratulations, all simulated renewals succeeded
```

Inspect Certbot’s renewal mechanism:

```bash
systemctl list-timers |
  grep -i certbot || true
```

Snap may manage renewal through its own systemd timers. The important verification is that `certbot renew --dry-run` succeeds.

Certbot renews certificates before expiration and reloads Nginx when required.

---

# Part 21: Verify public exposure

On EC2:

```bash
sudo ss -lntp
```

Expected:

```text
0.0.0.0:80
0.0.0.0:443
127.0.0.1:8080
```

The API port should not appear publicly:

```bash
sudo ss -lntp | grep ':5000' || echo "API port is not published"
```

From your local computer, direct Docker access should fail:

```bash
curl \
  --connect-timeout 3 \
  "http://$PUBLIC_IP:8080/nginx-health"
```

The public HTTPS path should work:

```bash
curl --fail \
  "https://$APP_DOMAIN/nginx-health"
```

---

# Part 22: Commit the permanent configuration

Return to your local repository:

```bash
cd ~/devops-journey/01-linux-git
```

Validate:

```bash
bash -n scripts/deploy-ec2.sh
```

```bash
WEB_IMAGE=devops-journey-web:local \
API_IMAGE=devops-journey-api:local \
WEB_BIND_ADDRESS=127.0.0.1 \
WEB_PORT=8080 \
docker compose -f compose.prod.yaml config --quiet
```

Review:

```bash
git status
git diff
```

Stage:

```bash
git add \
  compose.prod.yaml \
  nginx/default.conf \
  scripts/deploy-ec2.sh \
  .github/workflows/deploy-ec2.yml
```

Commit:

```bash
git commit -m "feat: add HTTPS edge-proxy deployment"
```

Push:

```bash
git push -u origin feat/add-https-edge-proxy
```

Create the PR:

```bash
gh pr create \
  --base main \
  --title "Add HTTPS edge-proxy deployment" \
  --body "$(cat <<'EOF'
## Summary

- Binds Docker Nginx only to localhost
- Uses host Nginx as the public TLS edge proxy
- Preserves original client IP and HTTPS scheme
- Updates automated deployment for the loopback port
- Verifies production through the HTTPS domain
- Prevents direct internet access to Docker port 8080

## Test plan

- [x] DNS resolves to the EC2 Elastic IP
- [x] HTTP redirects to HTTPS
- [x] TLS certificate validates
- [x] Nginx and API health checks pass over HTTPS
- [x] Docker port is bound only to 127.0.0.1
- [x] Certificate renewal dry run succeeds
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

The pipeline should run:

```text
CI
 → publish images
   → deploy to EC2
     → verify https://YOUR_DOMAIN
```

Watch deployment:

```bash
gh run list \
  --workflow deploy-ec2.yml \
  --limit 3
```

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

---

# Part 23: Update CloudWatch validation

The existing EC2 health monitor checks the complete local proxy path through host Nginx:

```text
http://localhost/nginx-health
http://localhost/api/health
```

This tests:

- Host Nginx
- Docker Nginx
- API

The GitHub deployment workflow tests the external HTTPS path:

```text
https://YOUR_DOMAIN/nginx-health
https://YOUR_DOMAIN/api/health
```

This additionally tests:

- DNS
- Elastic IP
- Security-group port 443
- TLS certificate
- Public routing

Together, they provide internal and external validation.

---

# Troubleshooting

## DNS does not resolve

Check Route 53:

```bash
aws route53 list-resource-record-sets \
  --hosted-zone-id "$ZONE_ID" \
  --query "ResourceRecordSets[?Name=='${APP_DOMAIN}.']"
```

Check nameserver delegation:

```bash
dig NS "$ROOT_DOMAIN" +short
```

Compare with the Route 53 hosted-zone NS records.

## Certbot reports authorization failure

Verify:

- DNS resolves to the correct Elastic IP.
- Port 80 is open.
- Host Nginx is running.
- The domain responds over HTTP.
- No proxy or firewall intercepts `/.well-known/acme-challenge/`.

Test:

```bash
curl -I "http://$APP_DOMAIN/"
```

## Host Nginx reports `502 Bad Gateway`

Check Docker:

```bash
curl -i http://127.0.0.1:8080/nginx-health
```

```bash
cd /opt/devops-journey
docker compose -f compose.prod.yaml ps
docker compose -f compose.prod.yaml logs --tail=50
```

Check host logs:

```bash
sudo tail -n 50 \
  /var/log/nginx/devops-journey-error.log
```

## Port 80 is already in use

Inspect:

```bash
sudo ss -lntp | grep ':80'
```

Docker should use `127.0.0.1:8080`; host Nginx should own public ports 80 and 443.

## HTTPS works but rate limiting affects all users together

Ensure Docker Nginx has:

```nginx
set_real_ip_from 172.16.0.0/12;
real_ip_header X-Forwarded-For;
real_ip_recursive on;
```

Confirm host Nginx sends:

```nginx
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
```

## Deployment workflow fails SSH after Elastic IP change

Update:

```text
EC2_HOST
EC2_KNOWN_HOSTS
```

in the GitHub `production` environment.

Do not disable `StrictHostKeyChecking`.

---

# Cleanup

If ending the lab:

## Remove DNS record

Create a Route 53 `DELETE` change using the same record name, type, TTL, and IP value.

## Revoke HTTPS

```bash
aws ec2 revoke-security-group-ingress \
  --group-id "$SG_ID" \
  --protocol tcp \
  --port 443 \
  --cidr 0.0.0.0/0
```

## Remove the certificate and host Nginx

On EC2:

```bash
sudo certbot delete \
  --cert-name "$APP_DOMAIN"
```

```bash
sudo rm -f \
  /etc/nginx/sites-enabled/devops-journey \
  /etc/nginx/sites-available/devops-journey
```

## Release the Elastic IP

First disassociate:

```bash
aws ec2 disassociate-address \
  --association-id "$EIP_ASSOCIATION_ID"
```

Then release:

```bash
aws ec2 release-address \
  --allocation-id "$ALLOCATION_ID"
```

Do not release the Elastic IP while DNS still points to it.

---

# Final verification

```bash
dig +short "$APP_DOMAIN"
```

```bash
curl -I "http://$APP_DOMAIN/"
```

```bash
curl -I "https://$APP_DOMAIN/"
```

```bash
curl --fail "https://$APP_DOMAIN/nginx-health"
curl --fail "https://$APP_DOMAIN/api/health"
```

```bash
openssl s_client \
  -connect "${APP_DOMAIN}:443" \
  -servername "$APP_DOMAIN" \
  </dev/null 2>/dev/null |
  grep 'Verify return code'
```

On EC2:

```bash
sudo nginx -t
sudo certbot renew --dry-run
sudo ss -lntp

cd /opt/devops-journey
docker compose -f compose.prod.yaml ps
```

Success means:

- The domain resolves to a stable Elastic IP.
- Port 80 redirects to HTTPS.
- Port 443 presents a trusted certificate.
- Certificate hostname validation succeeds.
- Renewal dry-run succeeds.
- Host Nginx terminates TLS.
- Docker Nginx is bound only to `127.0.0.1:8080`.
- The API port is not public.
- Original client IP information is preserved.
- CI/CD verifies the HTTPS production URL.
- CloudWatch continues monitoring the full application path.

## Skills completed

You have now practised:

- Elastic IP allocation and association
- Route 53 hosted zones and A records
- DNS propagation and troubleshooting
- Host-level Nginx reverse proxying
- TLS termination
- Let’s Encrypt certificates
- Certbot Nginx integration
- HTTP-to-HTTPS redirection
- HSTS rollout
- Certificate renewal testing
- Loopback-only Docker port publishing
- Proxy client-IP preservation
- HTTPS-aware deployment verification

**Next lab:** Final capstone and disaster-recovery exercise—make an application change, send it through the full Git/PR/CI/CD pipeline, deliberately break production, diagnose it from CloudWatch, roll back, restore from S3, and produce a professional project README.