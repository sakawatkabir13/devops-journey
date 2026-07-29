# Practical Lab 6: Continuous Integration with GitHub Actions

**Goal:** Create a GitHub Actions workflow that automatically:

1. Validates Python syntax.
2. Runs API unit tests.
3. Validates `compose.yaml`.
4. Builds and starts the containers.
5. Tests Nginx configuration.
6. Runs end-to-end HTTP tests.
7. Captures container logs when something fails.

**Time:** Approximately 60–90 minutes.

> **CI** automatically validates every change.  
> **CD** automatically publishes or deploys changes after CI passes.  
> This lab focuses on CI; the next lab will add image publishing as the first CD stage.

## CI pipeline

```text
git push / pull request
          │
          ▼
┌─────────────────────────┐
│ GitHub Actions runner   │
├─────────────────────────┤
│ 1. Check out code       │
│ 2. Validate Python      │
│ 3. Run unit tests       │
│ 4. Validate Compose     │
│ 5. Build containers     │
│ 6. Start application    │
│ 7. Test Nginx           │
│ 8. Test HTTP endpoints  │
│ 9. Collect logs         │
└─────────────────────────┘
          │
          ▼
       Pass or fail
```

---

# Part 1: Prepare a CI branch

Open the project:

```bash
cd ~/devops-journey/01-linux-git
```

Ensure the previous lab is complete:

```bash
docker compose up -d
docker compose ps
git status
```

Synchronize `main`:

```bash
git switch main
git pull origin main
```

Create a CI branch:

```bash
git switch -c ci/add-github-actions
```

---

# Part 2: Create an API unit test

Create:

```bash
nano api/test_server.py
```

Add:

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class CounterTests(unittest.TestCase):
    def test_counter_starts_at_one(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            counter_file = Path(temporary_directory) / "counter.txt"

            with patch.object(server, "COUNTER_FILE", counter_file):
                result = server.increment_counter()

            self.assertEqual(result, 1)
            self.assertEqual(counter_file.read_text(), "1")

    def test_counter_increments_existing_value(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            counter_file = Path(temporary_directory) / "counter.txt"
            counter_file.write_text("1")

            with patch.object(server, "COUNTER_FILE", counter_file):
                result = server.increment_counter()

            self.assertEqual(result, 2)
            self.assertEqual(counter_file.read_text(), "2")

    def test_invalid_counter_is_reset(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            counter_file = Path(temporary_directory) / "counter.txt"
            counter_file.write_text("invalid")

            with patch.object(server, "COUNTER_FILE", counter_file):
                result = server.increment_counter()

            self.assertEqual(result, 1)
            self.assertEqual(counter_file.read_text(), "1")


if __name__ == "__main__":
    unittest.main()
```

This test isolates counter data in a temporary directory rather than using Docker’s `/data` directory.

---

# Part 3: Run the tests locally

Check Python syntax:

```bash
python3 -m compileall -q api
```

Run the tests:

```bash
python3 -m unittest discover \
  -s api \
  -p 'test_*.py' \
  -v
```

Expected:

```text
test_counter_increments_existing_value ... ok
test_counter_starts_at_one ... ok
test_invalid_counter_is_reset ... ok

Ran 3 tests
OK
```

If `import server` fails, ensure you are running the command from the repository root and that both files are inside `api/`.

---

# Part 4: Validate the application locally

Validate Compose:

```bash
docker compose config >/dev/null &&
echo "Compose configuration is valid"
```

Rebuild and start everything:

```bash
docker compose up -d --build --wait
```

If your Compose version does not recognize `--wait`, use:

```bash
docker compose up -d --build
```

and then check manually:

```bash
docker compose ps
```

Test Nginx:

```bash
docker compose exec web nginx -t
```

Run HTTP tests:

```bash
curl --fail http://localhost:8080/nginx-health
curl --fail http://localhost:8080/api/health
curl --fail http://localhost:8080/api/info
```

All local checks should pass before adding CI.

---

# Part 5: Create the GitHub Actions workflow

GitHub Actions workflow files must be stored in:

```text
.github/workflows/
```

Create the directory:

```bash
mkdir -p .github/workflows
```

Create the workflow:

```bash
nano .github/workflows/ci.yml
```

Add:

```yaml
name: CI

run-name: CI for ${{ github.ref_name }}

on:
  push:
    branches:
      - main

  pull_request:
    branches:
      - main

  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  validate:
    name: Validate and unit test
    runs-on: ubuntu-latest
    timeout-minutes: 5

    steps:
      - name: Check out repository
        uses: actions/checkout@v7

      - name: Set up Python
        uses: actions/setup-python@v7
        with:
          python-version: "3.12"

      - name: Check Python syntax
        run: python -m compileall -q api

      - name: Run unit tests
        run: |
          python -m unittest discover \
            -s api \
            -p 'test_*.py' \
            -v

      - name: Validate Docker Compose
        run: docker compose config --quiet

  integration:
    name: Container integration test
    needs: validate
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Check out repository
        uses: actions/checkout@v7

      - name: Build and start application
        run: |
          docker compose up \
            --detach \
            --build \
            --wait \
            --wait-timeout 60

      - name: Show service status
        run: docker compose ps

      - name: Test Nginx configuration
        run: docker compose exec -T web nginx -t

      - name: Test health endpoints
        run: |
          set -euo pipefail

          curl \
            --fail \
            --show-error \
            --silent \
            --retry 5 \
            --retry-delay 2 \
            --retry-all-errors \
            http://localhost:8080/nginx-health

          curl \
            --fail \
            --show-error \
            --silent \
            --retry 5 \
            --retry-delay 2 \
            --retry-all-errors \
            http://localhost:8080/api/health

      - name: Test API response
        run: |
          set -euo pipefail

          response="$(
            curl \
              --fail \
              --show-error \
              --silent \
              http://localhost:8080/api/info
          )"

          echo "$response"

          echo "$response" |
            grep --fixed-strings '"message": "Hello from the Docker API"'

      - name: Test security headers
        run: |
          set -euo pipefail

          headers="$(
            curl \
              --fail \
              --show-error \
              --silent \
              --head \
              http://localhost:8080
          )"

          echo "$headers"

          echo "$headers" |
            grep --ignore-case '^x-content-type-options: nosniff'

          echo "$headers" |
            grep --ignore-case '^x-frame-options: DENY'

          echo "$headers" |
            grep --ignore-case '^content-security-policy:'

      - name: Test custom 404 page
        run: |
          set -euo pipefail

          status="$(
            curl \
              --silent \
              --output /tmp/not-found.html \
              --write-out '%{http_code}' \
              http://localhost:8080/does-not-exist
          )"

          cat /tmp/not-found.html

          test "$status" = "404"
          grep --fixed-strings '<h1>404</h1>' /tmp/not-found.html

      - name: Show container logs
        if: always()
        run: |
          docker compose logs --no-color || true

      - name: Remove test environment
        if: always()
        run: |
          docker compose down --volumes --remove-orphans
```

The current major versions used here are:

```yaml
actions/checkout@v7
actions/setup-python@v7
```

---

# Part 6: Understand the workflow

## Triggers

```yaml
on:
  push:
    branches:
      - main
  pull_request:
    branches:
      - main
  workflow_dispatch:
```

The workflow runs when:

- Code is pushed directly to `main`.
- A pull request targets `main`.
- You start it manually from GitHub.

## Minimal permissions

```yaml
permissions:
  contents: read
```

The workflow can read the repository but cannot modify it.

Following the principle of least privilege reduces the impact of a compromised dependency or script.

## Concurrency

```yaml
concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

If you push several commits quickly, GitHub cancels older CI runs for the same branch.

## Job dependency

```yaml
needs: validate
```

The integration job starts only after validation and unit tests pass.

```text
validate ──success──► integration
         └─failure──► stop
```

## Always-run cleanup

```yaml
if: always()
```

Logs and cleanup execute even when an earlier test fails.

This is important because the logs usually explain the failure.

## Non-interactive Docker execution

```bash
docker compose exec -T web nginx -t
```

`-T` disables pseudo-terminal allocation. CI systems do not normally provide interactive terminals.

---

# Part 7: Review the workflow locally

Display the workflow:

```bash
less .github/workflows/ci.yml
```

Press `q` to exit.

Check Git status:

```bash
git status
```

Review the change:

```bash
git diff
```

Run all local validations one more time:

```bash
python3 -m compileall -q api
```

```bash
python3 -m unittest discover \
  -s api \
  -p 'test_*.py' \
  -v
```

```bash
docker compose config --quiet
```

```bash
docker compose up -d --build --wait
```

```bash
docker compose exec -T web nginx -t
```

```bash
curl --fail http://localhost:8080/nginx-health
curl --fail http://localhost:8080/api/health
```

---

# Part 8: Commit and push the workflow

Stage the files:

```bash
git add api/test_server.py .github/workflows/ci.yml
```

Commit:

```bash
git commit -m "ci: add automated validation and integration tests"
```

Push:

```bash
git push -u origin ci/add-github-actions
```

Pushing the branch alone does not trigger this workflow because the `push` trigger is restricted to `main`. Creating a pull request will trigger it.

---

# Part 9: Create the pull request

Create a PR:

```bash
gh pr create \
  --base main \
  --head ci/add-github-actions \
  --title "Add GitHub Actions CI pipeline" \
  --body "$(cat <<'EOF'
## Summary

- Adds Python unit tests
- Validates Python syntax and Docker Compose
- Builds the multi-container application
- Tests Nginx configuration
- Tests health endpoints, API responses and security headers
- Captures container logs on failure

## Test plan

- [x] Unit tests pass locally
- [x] Compose configuration is valid
- [x] Containers become healthy
- [x] Nginx configuration is valid
- [x] End-to-end HTTP tests pass
EOF
)"
```

Open the PR:

```bash
gh pr view --web
```

---

# Part 10: Monitor CI

Watch the pull-request checks:

```bash
gh pr checks --watch
```

Expected checks:

```text
Validate and unit test       pass
Container integration test   pass
```

List recent workflow runs:

```bash
gh run list --branch ci/add-github-actions --limit 5
```

View the current run:

```bash
gh run view
```

If GitHub asks you to select a run, choose the latest one.

Open the workflow in your browser:

```bash
gh run view --web
```

---

# Part 11: Diagnose a failure

If CI fails, view failed logs:

```bash
gh run view --log-failed
```

You can also find the run ID:

```bash
gh run list --branch ci/add-github-actions --limit 5
```

Then inspect a specific run:

```bash
gh run view RUN_ID
```

Display only failed logs:

```bash
gh run view RUN_ID --log-failed
```

Typical failures:

| Failure | Likely cause |
|---|---|
| Python test fails | Application behavior and expected result differ |
| `docker compose config` fails | YAML indentation or Compose syntax error |
| Container is unhealthy | Health check, startup, or application problem |
| `nginx -t` fails | Nginx syntax or directive-context error |
| `curl: connection refused` | Container did not start or port was not published |
| API returns `502` | Nginx cannot reach the API |
| Header test fails | Security header missing or overridden in a location block |
| Cleanup fails | An earlier service never started; use `|| true` for diagnostic cleanup |

The correct workflow is:

```text
Read error → reproduce locally → fix → commit → push → watch CI
```

Do not repeatedly change random lines and push without understanding the failure.

---

# Part 12: Perform a controlled CI failure drill

This exercise demonstrates why CI is valuable.

Edit the test:

```bash
nano api/test_server.py
```

Temporarily change:

```python
self.assertEqual(result, 2)
```

in `test_counter_increments_existing_value` to:

```python
self.assertEqual(result, 999)
```

Commit and push the intentionally incorrect test:

```bash
git add api/test_server.py
git commit -m "test: demonstrate CI failure"
git push
```

Watch CI:

```bash
gh pr checks --watch
```

The unit-test job should fail, and the integration job should not run because it depends on validation.

Inspect the error:

```bash
gh run view --log-failed
```

Restore the correct expectation:

```python
self.assertEqual(result, 2)
```

Commit and push the correction:

```bash
git add api/test_server.py
git commit -m "test: restore correct counter expectation"
git push
```

Watch the new run:

```bash
gh pr checks --watch
```

Both jobs should now pass.

This demonstrates the **fail-fast** principle:

```text
Cheap validation fails first
        │
        └── expensive integration tests never start
```

---

# Part 13: Merge only when CI is green

Check the PR:

```bash
gh pr checks
```

When every required check passes, merge using squash:

```bash
gh pr merge --squash --delete-branch
```

Synchronize your local repository:

```bash
git switch main
git pull origin main
```

The push to `main` triggers CI again.

Watch the `main` workflow:

```bash
gh run list --branch main --limit 3
```

Watch the latest run:

```bash
gh run watch
```

Verify its conclusion:

```bash
gh run view
```

---

# Part 14: Add a CI status badge

Get your repository name:

```bash
gh repo view --json nameWithOwner --jq '.nameWithOwner'
```

The badge format is:

```markdown
[![CI](https://github.com/USERNAME/devops-journey/actions/workflows/ci.yml/badge.svg)](https://github.com/USERNAME/devops-journey/actions/workflows/ci.yml)
```

Create a documentation branch:

```bash
git switch -c docs/add-ci-badge
```

Edit:

```bash
nano README.md
```

Place the badge immediately below the main heading, replacing `USERNAME` with your GitHub username.

Commit and push:

```bash
git add README.md
git commit -m "docs: add CI status badge"
git push -u origin docs/add-ci-badge
```

Create and monitor the PR:

```bash
gh pr create \
  --base main \
  --title "Add CI status badge" \
  --body "Displays the current GitHub Actions CI status in the README."

gh pr checks --watch
```

Merge after CI passes:

```bash
gh pr merge --squash --delete-branch
git switch main
git pull origin main
```

---

# Part 15: Optional branch protection

After CI works reliably, protect `main` from untested changes.

In GitHub:

1. Open your repository.
2. Select **Settings**.
3. Open **Rules**, then **Rulesets**.
4. Create a branch ruleset targeting `main`.
5. Enable:
   - Require a pull request before merging
   - Require status checks to pass
   - Require branches to be up to date before merging
6. Select these checks:
   - `Validate and unit test`
   - `Container integration test`
7. Save and activate the ruleset.

The desired workflow becomes:

```text
Feature branch
      │
      ▼
Pull request
      │
      ▼
Required CI checks
      │
      ├── failure → block merge
      │
      └── success → allow merge
```

Availability of some ruleset features can depend on repository visibility and GitHub plan.

---

# Final verification

Run locally:

```bash
cd ~/devops-journey/01-linux-git

git switch main
git status

python3 -m compileall -q api

python3 -m unittest discover \
  -s api \
  -p 'test_*.py' \
  -v

docker compose config --quiet
docker compose up -d --build --wait
docker compose exec -T web nginx -t

curl --fail http://localhost:8080/nginx-health
curl --fail http://localhost:8080/api/health
```

Check GitHub:

```bash
gh workflow list
gh run list --branch main --limit 5
gh run view
```

Success means:

- Python unit tests pass locally and in GitHub Actions.
- Compose configuration is valid.
- CI builds and starts both containers.
- Nginx configuration testing passes.
- End-to-end HTTP tests pass.
- Failed workflows preserve diagnostic logs.
- Pull requests show CI checks.
- `main` contains the workflow.
- The latest `main` workflow is green.

## Skills completed

You have now practised:

- Understanding CI versus CD
- Writing GitHub Actions workflows
- Configuring workflow triggers
- Restricting workflow permissions
- Cancelling obsolete workflow runs
- Creating dependent jobs
- Running Python unit tests
- Testing multi-container applications in CI
- Running Nginx tests non-interactively
- Performing end-to-end HTTP tests
- Collecting logs after failures
- Diagnosing failed workflows
- Blocking integration tests after validation failures
- Merging only after CI passes

**Next lab:** Container registry and continuous delivery—use GitHub Actions to build versioned images, tag them with commit identifiers, and publish them securely to GitHub Container Registry.