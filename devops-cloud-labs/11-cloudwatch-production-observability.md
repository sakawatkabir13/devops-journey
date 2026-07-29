# Practical Lab 11: Production Observability with CloudWatch

**Goal:** Monitor the EC2 server, Docker containers, website, API, memory, disk, and logs using AWS CloudWatch.

You will configure:

- CloudWatch Agent
- System and Docker logs
- Memory and disk metrics
- A custom website-health metric
- SNS email notifications
- CloudWatch alarms
- A monitoring dashboard
- Failure simulation and recovery testing

**Time:** Approximately 2–3 hours.

> **Cost warning:** Custom metrics, log ingestion, log storage, dashboards, alarms, and SNS usage may generate charges. This lab sets log retention to 14 days to control storage growth.

## Observability architecture

```text
EC2 instance
   │
   ├── CloudWatch Agent
   │      ├── Memory metrics
   │      ├── Disk metrics
   │      ├── System logs
   │      └── Docker logs
   │
   └── Health-check timer
          ├── Nginx health
          ├── API health
          └── Running container count
                    │
                    ▼
               CloudWatch
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
       Metrics     Logs     Alarms
                              │
                              ▼
                         SNS email
```

---

# Part 1: Verify AWS and EC2

On your local Ubuntu machine:

```bash
export AWS_PROFILE=devops-lab
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""
```

Rediscover the instance:

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

Verify:

```bash
curl --fail "http://$PUBLIC_IP/nginx-health"
curl --fail "http://$PUBLIC_IP/api/health"
```

---

# Part 2: Add CloudWatch permissions to the EC2 role

Get the AWS account ID:

```bash
ACCOUNT_ID="$(
  aws sts get-caller-identity \
    --query Account \
    --output text
)"
```

The EC2 role created in the S3 lab was:

```bash
EC2_ROLE_NAME="DevOpsJourneyEC2S3BackupRole"
```

Generate a least-privilege CloudWatch policy:

```bash
export ACCOUNT_ID AWS_REGION
```

```bash
python3 - <<'PY' > /tmp/ec2-cloudwatch-policy.json
import json
import os

account_id = os.environ["ACCOUNT_ID"]
region = os.environ["AWS_REGION"]

policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "PublishProjectMetrics",
            "Effect": "Allow",
            "Action": [
                "cloudwatch:PutMetricData",
            ],
            "Resource": "*",
            "Condition": {
                "StringEquals": {
                    "cloudwatch:namespace": "DevOpsJourney"
                }
            },
        },
        {
            "Sid": "WriteProjectLogs",
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogStream",
                "logs:PutLogEvents",
            ],
            "Resource": (
                f"arn:aws:logs:{region}:{account_id}:"
                "log-group:/devops-journey/*:*"
            ),
        },
        {
            "Sid": "DescribeCloudWatchLogs",
            "Effect": "Allow",
            "Action": [
                "logs:DescribeLogGroups",
                "logs:DescribeLogStreams",
            ],
            "Resource": "*",
        },
        {
            "Sid": "ReadEC2MetadataForMetrics",
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeTags",
                "ec2:DescribeVolumes",
            ],
            "Resource": "*",
        },
    ],
}

print(json.dumps(policy, indent=2))
PY
```

Review:

```bash
less /tmp/ec2-cloudwatch-policy.json
```

Apply:

```bash
aws iam put-role-policy \
  --role-name "$EC2_ROLE_NAME" \
  --policy-name DevOpsJourneyCloudWatchAccess \
  --policy-document file:///tmp/ec2-cloudwatch-policy.json
```

Delete the temporary policy:

```bash
rm /tmp/ec2-cloudwatch-policy.json
```

Verify:

```bash
aws iam get-role-policy \
  --role-name "$EC2_ROLE_NAME" \
  --policy-name DevOpsJourneyCloudWatchAccess
```

The role can publish only to the `DevOpsJourney` metric namespace and `/devops-journey/*` log groups.

---

# Part 3: Create CloudWatch log groups

Create two log groups:

```bash
aws logs create-log-group \
  --log-group-name /devops-journey/system \
  2>/dev/null || true
```

```bash
aws logs create-log-group \
  --log-group-name /devops-journey/docker \
  2>/dev/null || true
```

Set 14-day retention:

```bash
aws logs put-retention-policy \
  --log-group-name /devops-journey/system \
  --retention-in-days 14
```

```bash
aws logs put-retention-policy \
  --log-group-name /devops-journey/docker \
  --retention-in-days 14
```

Tag the log groups:

```bash
aws logs tag-log-group \
  --log-group-name /devops-journey/system \
  --tags Project=devops-journey,Environment=production
```

```bash
aws logs tag-log-group \
  --log-group-name /devops-journey/docker \
  --tags Project=devops-journey,Environment=production
```

Verify:

```bash
aws logs describe-log-groups \
  --log-group-name-prefix /devops-journey \
  --query 'logGroups[].{Name:logGroupName,Retention:retentionInDays,Bytes:storedBytes}' \
  --output table
```

---

# Part 4: Create the CloudWatch Agent configuration

Open the repository:

```bash
cd ~/devops-journey/01-linux-git
git switch main
git pull origin main
git switch -c feat/add-cloudwatch-monitoring
```

Create directories:

```bash
mkdir -p deploy/cloudwatch deploy/systemd
```

Create:

```bash
nano deploy/cloudwatch/amazon-cloudwatch-agent.json
```

Add:

```json
{
  "agent": {
    "metrics_collection_interval": 60,
    "run_as_user": "root"
  },
  "metrics": {
    "namespace": "DevOpsJourney",
    "append_dimensions": {
      "InstanceId": "${aws:InstanceId}",
      "InstanceType": "${aws:InstanceType}"
    },
    "aggregation_dimensions": [
      [
        "InstanceId"
      ]
    ],
    "metrics_collected": {
      "mem": {
        "measurement": [
          "mem_used_percent",
          "mem_available"
        ],
        "metrics_collection_interval": 60
      },
      "swap": {
        "measurement": [
          "swap_used_percent"
        ],
        "metrics_collection_interval": 60
      },
      "disk": {
        "measurement": [
          "used_percent",
          "free",
          "inodes_free"
        ],
        "resources": [
          "/"
        ],
        "drop_device": true,
        "metrics_collection_interval": 60
      },
      "netstat": {
        "measurement": [
          "tcp_established",
          "tcp_time_wait"
        ],
        "metrics_collection_interval": 60
      }
    }
  },
  "logs": {
    "force_flush_interval": 5,
    "logs_collected": {
      "files": {
        "collect_list": [
          {
            "file_path": "/var/log/syslog",
            "log_group_name": "/devops-journey/system",
            "log_stream_name": "{instance_id}/syslog",
            "timezone": "UTC"
          },
          {
            "file_path": "/var/lib/docker/containers/*/*.log",
            "log_group_name": "/devops-journey/docker",
            "log_stream_name": "{instance_id}/docker",
            "timezone": "UTC"
          }
        ]
      }
    }
  }
}
```

## Metrics collected

| Metric | Purpose |
|---|---|
| `mem_used_percent` | Memory utilization |
| `mem_available` | Available RAM |
| `swap_used_percent` | Swap utilization |
| `disk_used_percent` | Root filesystem utilization |
| `disk_free` | Available disk space |
| `disk_inodes_free` | Available filesystem inodes |
| `netstat_tcp_established` | Established TCP connections |
| `netstat_tcp_time_wait` | Connections waiting to close |

EC2 sends CPU, network, and status-check metrics automatically. The agent adds operating-system metrics that EC2 does not provide by default.

---

# Part 5: Create a custom health-metric script

Create:

```bash
nano scripts/publish-health-metrics.sh
```

Add:

```bash
#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/devops-journey}"
EXPECTED_CONTAINERS="${EXPECTED_CONTAINERS:-2}"
NAMESPACE="${CLOUDWATCH_NAMESPACE:-DevOpsJourney}"

get_instance_id() {
    local token

    token="$(
        curl \
            --fail \
            --silent \
            --show-error \
            --request PUT \
            --header "X-aws-ec2-metadata-token-ttl-seconds: 60" \
            http://169.254.169.254/latest/api/token
    )"

    curl \
        --fail \
        --silent \
        --show-error \
        --header "X-aws-ec2-metadata-token: $token" \
        http://169.254.169.254/latest/meta-data/instance-id
}

INSTANCE_ID="$(get_instance_id)"
WEBSITE_HEALTHY=0
RUNNING_CONTAINERS=0

if curl \
    --fail \
    --silent \
    --show-error \
    --max-time 10 \
    http://localhost/nginx-health \
    >/dev/null &&
   curl \
    --fail \
    --silent \
    --show-error \
    --max-time 10 \
    http://localhost/api/health \
    >/dev/null; then
    WEBSITE_HEALTHY=1
fi

if [[ -f "$PROJECT_DIR/compose.prod.yaml" ]]; then
    RUNNING_CONTAINERS="$(
        cd "$PROJECT_DIR"

        docker compose \
            -f compose.prod.yaml \
            ps \
            --status running \
            --services |
        wc -l |
        tr -d ' '
    )"
fi

if [[ "$RUNNING_CONTAINERS" -lt "$EXPECTED_CONTAINERS" ]]; then
    WEBSITE_HEALTHY=0
fi

aws cloudwatch put-metric-data \
    --namespace "$NAMESPACE" \
    --metric-data \
        "MetricName=WebsiteHealthy,Dimensions=[{Name=InstanceId,Value=$INSTANCE_ID}],Value=$WEBSITE_HEALTHY,Unit=Count" \
        "MetricName=RunningContainers,Dimensions=[{Name=InstanceId,Value=$INSTANCE_ID}],Value=$RUNNING_CONTAINERS,Unit=Count"

if [[ "$WEBSITE_HEALTHY" -eq 1 ]]; then
    logger \
        --tag devops-journey-health \
        "Health check passed: containers=$RUNNING_CONTAINERS"

    echo "Healthy: containers=$RUNNING_CONTAINERS"
else
    logger \
        --priority user.err \
        --tag devops-journey-health \
        "Health check failed: containers=$RUNNING_CONTAINERS"

    echo "Unhealthy: containers=$RUNNING_CONTAINERS" >&2
    exit 1
fi
```

Make it executable:

```bash
chmod 755 scripts/publish-health-metrics.sh
```

Check syntax:

```bash
bash -n scripts/publish-health-metrics.sh
```

---

# Part 6: Create the health-monitor systemd service

Create:

```bash
nano deploy/systemd/devops-journey-health.service
```

Add:

```ini
[Unit]
Description=Publish DevOps Journey health metrics
Wants=network-online.target
After=network-online.target docker.service
Requires=docker.service

[Service]
Type=oneshot
User=ubuntu
Group=ubuntu
SupplementaryGroups=docker

Environment=PROJECT_DIR=/opt/devops-journey
Environment=EXPECTED_CONTAINERS=2
Environment=CLOUDWATCH_NAMESPACE=DevOpsJourney
Environment=AWS_REGION=us-east-1
Environment=AWS_DEFAULT_REGION=us-east-1
Environment=AWS_PAGER=

ExecStart=/usr/local/bin/devops-journey-health

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadOnlyPaths=/opt/devops-journey
```

Create the timer:

```bash
nano deploy/systemd/devops-journey-health.timer
```

Add:

```ini
[Unit]
Description=Check DevOps Journey health every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=60s
AccuracySec=10s
Persistent=true

[Install]
WantedBy=timers.target
```

---

# Part 7: Commit the monitoring configuration

Review:

```bash
git status
git diff
```

Validate JSON:

```bash
python3 -m json.tool \
  deploy/cloudwatch/amazon-cloudwatch-agent.json \
  >/dev/null &&
echo "CloudWatch Agent JSON is valid"
```

Validate Bash:

```bash
bash -n scripts/publish-health-metrics.sh
```

Stage:

```bash
git add \
  deploy/cloudwatch/amazon-cloudwatch-agent.json \
  deploy/systemd/devops-journey-health.service \
  deploy/systemd/devops-journey-health.timer \
  scripts/publish-health-metrics.sh
```

Commit:

```bash
git commit -m "feat: add CloudWatch observability"
```

Push:

```bash
git push -u origin feat/add-cloudwatch-monitoring
```

Create a pull request:

```bash
gh pr create \
  --base main \
  --title "Add CloudWatch observability" \
  --body "$(cat <<'EOF'
## Summary

- Collects memory, disk and network metrics
- Ships system and Docker logs to CloudWatch
- Publishes website and container health metrics
- Adds a minute-by-minute systemd health timer
- Uses the EC2 IAM role instead of AWS access keys

## Test plan

- [x] CloudWatch Agent configuration is valid JSON
- [x] Health script passes Bash syntax validation
- [ ] CloudWatch Agent starts on EC2
- [ ] Custom metrics appear in CloudWatch
- [ ] System and Docker logs appear in CloudWatch Logs
- [ ] Failure simulation changes the alarm state
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

# Part 8: Copy monitoring files to EC2

From your local machine:

```bash
scp \
  -i "$KEY_PATH" \
  deploy/cloudwatch/amazon-cloudwatch-agent.json \
  "ubuntu@$PUBLIC_IP:/tmp/amazon-cloudwatch-agent.json"
```

```bash
scp \
  -i "$KEY_PATH" \
  scripts/publish-health-metrics.sh \
  "ubuntu@$PUBLIC_IP:/tmp/devops-journey-health"
```

```bash
scp \
  -i "$KEY_PATH" \
  deploy/systemd/devops-journey-health.service \
  deploy/systemd/devops-journey-health.timer \
  "ubuntu@$PUBLIC_IP:/tmp/"
```

Connect:

```bash
ssh -i "$KEY_PATH" "ubuntu@$PUBLIC_IP"
```

---

# Part 9: Install the CloudWatch Agent

On EC2, detect the package architecture:

```bash
case "$(dpkg --print-architecture)" in
  amd64)
    CW_ARCH="amd64"
    ;;
  arm64)
    CW_ARCH="arm64"
    ;;
  *)
    echo "Unsupported architecture: $(dpkg --print-architecture)"
    ;;
esac

echo "$CW_ARCH"
```

Download:

```bash
curl \
  "https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/${CW_ARCH}/latest/amazon-cloudwatch-agent.deb" \
  -o /tmp/amazon-cloudwatch-agent.deb
```

Install:

```bash
sudo dpkg -i /tmp/amazon-cloudwatch-agent.deb
```

Remove the package:

```bash
rm /tmp/amazon-cloudwatch-agent.deb
```

Verify:

```bash
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a status
```

It may report `stopped` before configuration is loaded.

---

# Part 10: Install and start the agent configuration

Install the configuration:

```bash
sudo install \
  -m 0644 \
  /tmp/amazon-cloudwatch-agent.json \
  /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
```

Load, validate, and start it:

```bash
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config \
  -m ec2 \
  -s \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
```

Check status:

```bash
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a status
```

Expected status:

```text
running
```

Check the systemd service:

```bash
sudo systemctl status \
  amazon-cloudwatch-agent \
  --no-pager
```

Inspect agent logs:

```bash
sudo tail -n 50 \
  /opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log
```

Look for permission errors or invalid configuration.

---

# Part 11: Install the health monitor

Install the script:

```bash
sudo install \
  -m 0755 \
  /tmp/devops-journey-health \
  /usr/local/bin/devops-journey-health
```

Install the systemd units:

```bash
sudo install \
  -m 0644 \
  /tmp/devops-journey-health.service \
  /etc/systemd/system/devops-journey-health.service
```

```bash
sudo install \
  -m 0644 \
  /tmp/devops-journey-health.timer \
  /etc/systemd/system/devops-journey-health.timer
```

Reload:

```bash
sudo systemctl daemon-reload
```

Run the check manually:

```bash
sudo -u ubuntu \
  /usr/local/bin/devops-journey-health
```

Expected:

```text
Healthy: containers=2
```

Enable the timer:

```bash
sudo systemctl enable --now \
  devops-journey-health.timer
```

Check:

```bash
systemctl list-timers \
  devops-journey-health.timer
```

Inspect logs:

```bash
sudo journalctl \
  -u devops-journey-health.service \
  --since "10 minutes ago" \
  --no-pager
```

---

# Part 12: Verify metrics in CloudWatch

Wait a few minutes for metrics to arrive.

From your local machine:

```bash
exit
```

List custom metrics:

```bash
aws cloudwatch list-metrics \
  --namespace DevOpsJourney \
  --dimensions Name=InstanceId,Value="$INSTANCE_ID" \
  --query 'Metrics[].{Metric:MetricName,Dimensions:Dimensions}' \
  --output table
```

Expected metric names include:

```text
WebsiteHealthy
RunningContainers
mem_used_percent
mem_available
disk_used_percent
disk_free
disk_inodes_free
swap_used_percent
netstat_tcp_established
netstat_tcp_time_wait
```

Get recent website-health values:

```bash
aws cloudwatch get-metric-statistics \
  --namespace DevOpsJourney \
  --metric-name WebsiteHealthy \
  --dimensions Name=InstanceId,Value="$INSTANCE_ID" \
  --start-time "$(date -u -d '15 minutes ago' +'%Y-%m-%dT%H:%M:%SZ')" \
  --end-time "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
  --period 60 \
  --statistics Minimum Maximum Average \
  --output table
```

A healthy application publishes:

```text
WebsiteHealthy = 1
RunningContainers = 2
```

---

# Part 13: Verify logs in CloudWatch

List streams:

```bash
aws logs describe-log-streams \
  --log-group-name /devops-journey/system \
  --order-by LastEventTime \
  --descending \
  --limit 10 \
  --output table
```

```bash
aws logs describe-log-streams \
  --log-group-name /devops-journey/docker \
  --order-by LastEventTime \
  --descending \
  --limit 10 \
  --output table
```

Query recent system logs:

```bash
START_TIME="$(date -d '15 minutes ago' +%s)"
END_TIME="$(date +%s)"
```

```bash
QUERY_ID="$(
  aws logs start-query \
    --log-group-name /devops-journey/system \
    --start-time "$START_TIME" \
    --end-time "$END_TIME" \
    --query-string '
      fields @timestamp, @message
      | filter @message like /devops-journey-health/
      | sort @timestamp desc
      | limit 20
    ' \
    --query queryId \
    --output text
)"

echo "$QUERY_ID"
```

Wait briefly:

```bash
sleep 5
```

Retrieve:

```bash
aws logs get-query-results \
  --query-id "$QUERY_ID"
```

---

# Part 14: Create an SNS alert topic

Create the topic:

```bash
TOPIC_ARN="$(
  aws sns create-topic \
    --name devops-journey-alerts \
    --tags \
      Key=Project,Value=devops-journey \
      Key=Environment,Value=production \
    --query TopicArn \
    --output text
)"

echo "$TOPIC_ARN"
```

Subscribe your email address:

```bash
ALERT_EMAIL="your-email@example.com"
```

```bash
aws sns subscribe \
  --topic-arn "$TOPIC_ARN" \
  --protocol email \
  --notification-endpoint "$ALERT_EMAIL"
```

AWS sends a confirmation message. Open it and select **Confirm subscription**.

Verify:

```bash
aws sns list-subscriptions-by-topic \
  --topic-arn "$TOPIC_ARN" \
  --output table
```

The subscription ARN should no longer say `PendingConfirmation`.

---

# Part 15: Create a website-health alarm

Create an alarm that triggers when health is `0` or when metrics disappear.

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name DevOpsJourney-Website-Unhealthy \
  --alarm-description "Website or API health check failed" \
  --namespace DevOpsJourney \
  --metric-name WebsiteHealthy \
  --dimensions Name=InstanceId,Value="$INSTANCE_ID" \
  --statistic Minimum \
  --period 60 \
  --evaluation-periods 2 \
  --datapoints-to-alarm 2 \
  --threshold 1 \
  --comparison-operator LessThanThreshold \
  --treat-missing-data breaching \
  --alarm-actions "$TOPIC_ARN" \
  --ok-actions "$TOPIC_ARN" \
  --tags \
    Key=Project,Value=devops-journey \
    Key=Environment,Value=production
```

This alarm enters `ALARM` when:

- Health is `0` for two consecutive periods.
- The health timer stops publishing data.

---

# Part 16: Create a container-count alarm

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name DevOpsJourney-Container-Count-Low \
  --alarm-description "Fewer than two application containers are running" \
  --namespace DevOpsJourney \
  --metric-name RunningContainers \
  --dimensions Name=InstanceId,Value="$INSTANCE_ID" \
  --statistic Minimum \
  --period 60 \
  --evaluation-periods 2 \
  --datapoints-to-alarm 2 \
  --threshold 2 \
  --comparison-operator LessThanThreshold \
  --treat-missing-data breaching \
  --alarm-actions "$TOPIC_ARN" \
  --ok-actions "$TOPIC_ARN"
```

---

# Part 17: Create an EC2 status-check alarm

EC2 provides this metric without the CloudWatch Agent:

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name DevOpsJourney-EC2-Status-Check-Failed \
  --alarm-description "EC2 system or instance status check failed" \
  --namespace AWS/EC2 \
  --metric-name StatusCheckFailed \
  --dimensions Name=InstanceId,Value="$INSTANCE_ID" \
  --statistic Maximum \
  --period 60 \
  --evaluation-periods 2 \
  --datapoints-to-alarm 2 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data missing \
  --alarm-actions "$TOPIC_ARN" \
  --ok-actions "$TOPIC_ARN"
```

---

# Part 18: Create a high-CPU alarm

Basic EC2 monitoring generally provides CPU metrics at five-minute granularity unless detailed monitoring is enabled.

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name DevOpsJourney-High-CPU \
  --alarm-description "EC2 CPU remained above 80 percent" \
  --namespace AWS/EC2 \
  --metric-name CPUUtilization \
  --dimensions Name=InstanceId,Value="$INSTANCE_ID" \
  --statistic Average \
  --period 300 \
  --evaluation-periods 2 \
  --datapoints-to-alarm 2 \
  --threshold 80 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching \
  --alarm-actions "$TOPIC_ARN" \
  --ok-actions "$TOPIC_ARN"
```

---

# Part 19: Create a high-memory alarm

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name DevOpsJourney-High-Memory \
  --alarm-description "Memory utilization remained above 85 percent" \
  --namespace DevOpsJourney \
  --metric-name mem_used_percent \
  --dimensions Name=InstanceId,Value="$INSTANCE_ID" \
  --statistic Average \
  --period 300 \
  --evaluation-periods 2 \
  --datapoints-to-alarm 2 \
  --threshold 85 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data breaching \
  --alarm-actions "$TOPIC_ARN" \
  --ok-actions "$TOPIC_ARN"
```

List alarms:

```bash
aws cloudwatch describe-alarms \
  --alarm-name-prefix DevOpsJourney \
  --query 'MetricAlarms[].{Name:AlarmName,State:StateValue,Metric:MetricName,Reason:StateReason}' \
  --output table
```

New alarms can initially report `INSUFFICIENT_DATA` while CloudWatch waits for metrics.

---

# Part 20: Test SNS notification delivery

Manually set the website alarm state:

```bash
aws cloudwatch set-alarm-state \
  --alarm-name DevOpsJourney-Website-Unhealthy \
  --state-value ALARM \
  --state-reason "Manual SNS notification test"
```

You should receive an email notification.

Return it to OK:

```bash
aws cloudwatch set-alarm-state \
  --alarm-name DevOpsJourney-Website-Unhealthy \
  --state-value OK \
  --state-reason "Manual notification test completed"
```

The next real metric evaluation may also update the state automatically.

---

# Part 21: Create a CloudWatch dashboard

Generate the dashboard definition:

```bash
export INSTANCE_ID AWS_REGION
```

```bash
python3 - <<'PY' > /tmp/devops-dashboard.json
import json
import os

instance_id = os.environ["INSTANCE_ID"]
region = os.environ["AWS_REGION"]

dashboard = {
    "widgets": [
        {
            "type": "text",
            "x": 0,
            "y": 0,
            "width": 24,
            "height": 2,
            "properties": {
                "markdown": (
                    "# DevOps Journey Production\n"
                    f"Instance: `{instance_id}` · Region: `{region}`"
                )
            },
        },
        {
            "type": "metric",
            "x": 0,
            "y": 2,
            "width": 12,
            "height": 6,
            "properties": {
                "title": "Application health",
                "region": region,
                "period": 60,
                "stat": "Minimum",
                "yAxis": {
                    "left": {
                        "min": 0,
                        "max": 1
                    }
                },
                "metrics": [
                    [
                        "DevOpsJourney",
                        "WebsiteHealthy",
                        "InstanceId",
                        instance_id
                    ]
                ],
            },
        },
        {
            "type": "metric",
            "x": 12,
            "y": 2,
            "width": 12,
            "height": 6,
            "properties": {
                "title": "Running containers",
                "region": region,
                "period": 60,
                "stat": "Minimum",
                "metrics": [
                    [
                        "DevOpsJourney",
                        "RunningContainers",
                        "InstanceId",
                        instance_id
                    ]
                ],
            },
        },
        {
            "type": "metric",
            "x": 0,
            "y": 8,
            "width": 12,
            "height": 6,
            "properties": {
                "title": "CPU utilization",
                "region": region,
                "period": 300,
                "stat": "Average",
                "metrics": [
                    [
                        "AWS/EC2",
                        "CPUUtilization",
                        "InstanceId",
                        instance_id
                    ]
                ],
            },
        },
        {
            "type": "metric",
            "x": 12,
            "y": 8,
            "width": 12,
            "height": 6,
            "properties": {
                "title": "Memory utilization",
                "region": region,
                "period": 60,
                "stat": "Average",
                "metrics": [
                    [
                        "DevOpsJourney",
                        "mem_used_percent",
                        "InstanceId",
                        instance_id
                    ]
                ],
            },
        },
        {
            "type": "log",
            "x": 0,
            "y": 14,
            "width": 24,
            "height": 8,
            "properties": {
                "title": "Recent Docker logs",
                "region": region,
                "view": "table",
                "query": (
                    "SOURCE '/devops-journey/docker' "
                    "| fields @timestamp, @message "
                    "| sort @timestamp desc "
                    "| limit 50"
                ),
            },
        },
    ]
}

print(json.dumps(dashboard))
PY
```

Create the dashboard:

```bash
aws cloudwatch put-dashboard \
  --dashboard-name DevOpsJourney \
  --dashboard-body file:///tmp/devops-dashboard.json
```

Delete the temporary file:

```bash
rm /tmp/devops-dashboard.json
```

Open the CloudWatch Console, select **Dashboards**, then open `DevOpsJourney`.

---

# Part 22: Perform a real failure test

Connect to EC2:

```bash
ssh -i "$KEY_PATH" "ubuntu@$PUBLIC_IP"
```

Stop the API:

```bash
cd /opt/devops-journey
docker compose -f compose.prod.yaml stop api
```

Verify the failure:

```bash
curl -i http://localhost/api/health
```

Nginx should return `502 Bad Gateway`.

Run the monitor immediately instead of waiting:

```bash
sudo systemctl start devops-journey-health.service
```

The service should fail and publish:

```text
WebsiteHealthy = 0
RunningContainers = 1
```

Inspect:

```bash
sudo systemctl status \
  devops-journey-health.service \
  --no-pager
```

Wait for CloudWatch evaluation, then check from your local machine:

```bash
exit
```

```bash
aws cloudwatch describe-alarms \
  --alarm-names \
    DevOpsJourney-Website-Unhealthy \
    DevOpsJourney-Container-Count-Low \
  --query 'MetricAlarms[].{Name:AlarmName,State:StateValue,Reason:StateReason}' \
  --output table
```

You should receive an alarm notification after the configured evaluation periods.

---

# Part 23: Recover the application

Reconnect:

```bash
ssh -i "$KEY_PATH" "ubuntu@$PUBLIC_IP"
```

Start the API:

```bash
cd /opt/devops-journey
docker compose -f compose.prod.yaml start api
```

Wait for health:

```bash
docker compose -f compose.prod.yaml ps
```

Test:

```bash
curl --fail http://localhost/nginx-health
curl --fail http://localhost/api/health
```

Publish recovery immediately:

```bash
sudo systemctl start devops-journey-health.service
```

The monitor should publish:

```text
WebsiteHealthy = 1
RunningContainers = 2
```

CloudWatch should eventually return the alarms to `OK` and send recovery notifications.

---

# Part 24: Useful operational commands

## EC2 application

```bash
cd /opt/devops-journey
docker compose -f compose.prod.yaml ps
docker compose -f compose.prod.yaml logs --tail=100
```

## Health monitor

```bash
systemctl status devops-journey-health.timer
sudo systemctl start devops-journey-health.service
sudo journalctl -u devops-journey-health.service --no-pager
```

## CloudWatch Agent

```bash
sudo systemctl status amazon-cloudwatch-agent --no-pager
```

```bash
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a status
```

```bash
sudo tail -n 100 \
  /opt/aws/amazon-cloudwatch-agent/logs/amazon-cloudwatch-agent.log
```

## AWS alarms

```bash
aws cloudwatch describe-alarms \
  --alarm-name-prefix DevOpsJourney \
  --output table
```

## Log groups

```bash
aws logs describe-log-groups \
  --log-group-name-prefix /devops-journey \
  --output table
```

---

# Cleanup

If continuing, retain these resources.

If stopping the project:

```bash
aws cloudwatch delete-alarms \
  --alarm-names \
    DevOpsJourney-Website-Unhealthy \
    DevOpsJourney-Container-Count-Low \
    DevOpsJourney-EC2-Status-Check-Failed \
    DevOpsJourney-High-CPU \
    DevOpsJourney-High-Memory
```

Delete the dashboard:

```bash
aws cloudwatch delete-dashboards \
  --dashboard-names DevOpsJourney
```

Delete log groups:

```bash
aws logs delete-log-group \
  --log-group-name /devops-journey/system
```

```bash
aws logs delete-log-group \
  --log-group-name /devops-journey/docker
```

Delete the SNS topic:

```bash
aws sns delete-topic \
  --topic-arn "$TOPIC_ARN"
```

Remove the inline role policy:

```bash
aws iam delete-role-policy \
  --role-name DevOpsJourneyEC2S3BackupRole \
  --policy-name DevOpsJourneyCloudWatchAccess
```

On EC2:

```bash
sudo systemctl disable --now devops-journey-health.timer
sudo systemctl disable --now amazon-cloudwatch-agent
```

---

# Final verification

```bash
aws cloudwatch list-metrics \
  --namespace DevOpsJourney \
  --dimensions Name=InstanceId,Value="$INSTANCE_ID" \
  --output table
```

```bash
aws cloudwatch describe-alarms \
  --alarm-name-prefix DevOpsJourney \
  --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}' \
  --output table
```

```bash
aws logs describe-log-groups \
  --log-group-name-prefix /devops-journey \
  --output table
```

```bash
curl --fail "http://$PUBLIC_IP/nginx-health"
curl --fail "http://$PUBLIC_IP/api/health"
```

Success means:

- EC2 publishes metrics without access keys.
- Memory and disk metrics appear in CloudWatch.
- System and Docker logs reach CloudWatch Logs.
- Log retention is limited to 14 days.
- Website health is published every minute.
- Missing metrics are treated as a failure.
- SNS notifications are confirmed.
- CPU, memory, status, container, and website alarms exist.
- The dashboard displays production health.
- Stopping the API triggers an alarm.
- Restarting the API returns alarms to `OK`.

## Skills completed

You have now practised:

- Infrastructure and application observability
- CloudWatch Agent installation
- Custom CloudWatch namespaces
- Host memory and disk monitoring
- Docker log collection
- Custom application-health metrics
- IMDSv2 metadata access
- CloudWatch Logs Insights
- SNS notifications
- CloudWatch alarms
- Missing-data alarm behavior
- CloudWatch dashboards
- Failure simulation
- Incident recovery verification

**Next lab:** HTTPS and production networking—attach a domain name, configure DNS, obtain a TLS certificate, redirect HTTP to HTTPS, add automatic certificate renewal, and update health monitoring for secure endpoints.

Official reference:

- [Install and configure the CloudWatch Agent](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Install-CloudWatch-Agent.html)