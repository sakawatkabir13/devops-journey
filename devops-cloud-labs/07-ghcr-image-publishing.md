# Practical Lab 7: Publish Docker Images with GitHub Actions and GHCR

**Goal:** Extend your pipeline from CI into continuous delivery by automatically publishing tested Docker images to **GitHub Container Registry (GHCR)**.

You will publish:

```text
ghcr.io/YOUR_USERNAME/devops-journey-web
ghcr.io/YOUR_USERNAME/devops-journey-api
```

Each image will have:

- `latest` — most recently published `main` image
- `sha-<commit>` — immutable reference to a specific source commit
- OCI metadata labels
- Build provenance
- Software bill of materials (SBOM)

**Time:** Approximately 75–90 minutes.

## Pipeline architecture

```text
Pull request
     │
     ▼
GitHub Actions CI
     │
     │ tests pass
     ▼
Merge into main
     │
     ▼
CI runs on main
     │
     │ success
     ▼
Publish workflow
     │
     ├── Build web image
     ├── Build API image
     ├── Generate provenance/SBOM
     └── Push images to GHCR
```

> The publish workflow runs only after the `CI` workflow succeeds on `main`. Failed code should never be published.

---

# Part 1: Understand image tags and digests

A Docker image can be referenced by a tag:

```text
ghcr.io/user/devops-journey-web:latest
```

or by a digest:

```text
ghcr.io/user/devops-journey-web@sha256:abc123...
```

## Mutable tag

```text
latest
```

can point to a different image after every deployment.

## Commit tag

```text
sha-a1b2c3d4e5f6
```

identifies the image produced from a particular Git commit.

## Immutable digest

```text
sha256:...
```

identifies exact image content. The digest changes whenever the image content changes.

For production deployments, commit tags or digests are safer than relying only on `latest`.

---

# Part 2: Prepare the repository

Open the project:

```bash
cd ~/devops-journey/01-linux-git
```

Confirm CI is passing:

```bash
gh run list --branch main --limit 5
```

Synchronize Git:

```bash
git switch main
git pull origin main
git status
```

Create a branch:

```bash
git switch -c ci/publish-container-images
```

---

# Part 3: Turn the web Dockerfile into a production image

The existing development Compose configuration uses bind mounts. A production image should include its website and Nginx configuration.

Open the root Dockerfile:

```bash
nano Dockerfile
```

Replace its contents with:

```dockerfile
FROM nginx:alpine

COPY nginx/default.conf /etc/nginx/conf.d/default.conf
COPY website/ /usr/share/nginx/html/

EXPOSE 80

HEALTHCHECK \
    --interval=10s \
    --timeout=3s \
    --retries=3 \
    --start-period=5s \
    CMD wget --quiet --tries=1 --spider http://localhost/nginx-health || exit 1
```

The production image now contains:

```text
Nginx
├── Hardened Nginx configuration
├── Website HTML
├── CSS
├── JavaScript
└── Custom error page
```

It no longer depends on host bind mounts for application files.

---

# Part 4: Create a production Compose file

Development and production have different requirements:

| Development | Production |
|---|---|
| Bind mounts | Versioned images |
| Local source files | Registry images |
| Rapid editing | Reproducible deployment |
| Rebuild optional | Exact artifact deployed |

Create:

```bash
nano compose.prod.yaml
```

Add:

```yaml
services:
  api:
    image: ${API_IMAGE:?Set API_IMAGE to the API container image}
    restart: unless-stopped
    volumes:
      - api-data:/data
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')"
      interval: 10s
      timeout: 3s
      retries: 3
      start_period: 5s

  web:
    image: ${WEB_IMAGE:?Set WEB_IMAGE to the web container image}
    restart: unless-stopped
    ports:
      - "8080:80"
    depends_on:
      api:
        condition: service_healthy

volumes:
  api-data:
```

The `${VARIABLE:?message}` syntax makes Compose stop with a useful error if an image variable is missing.

---

# Part 5: Test the production images locally

Stop the development environment to release port 8080:

```bash
docker compose down
```

Build local production images:

```bash
docker build \
  --tag devops-journey-web:local \
  .
```

```bash
docker build \
  --tag devops-journey-api:local \
  ./api
```

Validate the production configuration:

```bash
WEB_IMAGE=devops-journey-web:local \
API_IMAGE=devops-journey-api:local \
docker compose -f compose.prod.yaml config
```

Start the production configuration:

```bash
WEB_IMAGE=devops-journey-web:local \
API_IMAGE=devops-journey-api:local \
docker compose -f compose.prod.yaml up \
  --detach \
  --wait
```

Check it:

```bash
WEB_IMAGE=devops-journey-web:local \
API_IMAGE=devops-journey-api:local \
docker compose -f compose.prod.yaml ps
```

Test the endpoints:

```bash
curl --fail http://localhost:8080/nginx-health
curl --fail http://localhost:8080/api/health
curl --fail http://localhost:8080/api/info
```

Open:

```text
http://localhost:8080
```

The application should work without bind mounts.

Remove the local production environment:

```bash
WEB_IMAGE=devops-journey-web:local \
API_IMAGE=devops-journey-api:local \
docker compose -f compose.prod.yaml down --volumes
```

---

# Part 6: Create the publishing workflow

Create:

```bash
nano .github/workflows/publish-images.yml
```

Add:

```yaml
name: Publish container images

on:
  workflow_run:
    workflows:
      - CI
    types:
      - completed
    branches:
      - main

  workflow_dispatch:

permissions:
  contents: read
  packages: write

concurrency:
  group: publish-${{ github.ref }}
  cancel-in-progress: false

jobs:
  publish:
    name: Build and publish images
    if: >-
      github.event_name == 'workflow_dispatch' ||
      github.event.workflow_run.conclusion == 'success'

    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      - name: Check out tested commit
        uses: actions/checkout@v7
        with:
          ref: ${{ github.event.workflow_run.head_sha || github.sha }}

      - name: Generate image information
        id: image
        shell: bash
        run: |
          set -euo pipefail

          owner="${GITHUB_REPOSITORY_OWNER,,}"
          commit_sha="$(git rev-parse HEAD)"
          short_sha="${commit_sha:0:12}"

          echo "owner=$owner" >> "$GITHUB_OUTPUT"
          echo "commit_sha=$commit_sha" >> "$GITHUB_OUTPUT"
          echo "short_sha=$short_sha" >> "$GITHUB_OUTPUT"

          echo "web=ghcr.io/$owner/devops-journey-web" \
            >> "$GITHUB_OUTPUT"

          echo "api=ghcr.io/$owner/devops-journey-api" \
            >> "$GITHUB_OUTPUT"

      - name: Log in to GitHub Container Registry
        uses: docker/login-action@v4
        with:
          registry: ghcr.io
          username: ${{ github.repository_owner }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v4

      - name: Build and publish web image
        id: build-web
        uses: docker/build-push-action@v7
        with:
          context: .
          file: ./Dockerfile
          push: true
          tags: |
            ${{ steps.image.outputs.web }}:latest
            ${{ steps.image.outputs.web }}:sha-${{ steps.image.outputs.short_sha }}
          labels: |
            org.opencontainers.image.title=DevOps Journey Web
            org.opencontainers.image.description=Nginx frontend for the DevOps Journey project
            org.opencontainers.image.source=https://github.com/${{ github.repository }}
            org.opencontainers.image.revision=${{ steps.image.outputs.commit_sha }}
          cache-from: type=gha,scope=web
          cache-to: type=gha,mode=max,scope=web
          provenance: true
          sbom: true

      - name: Build and publish API image
        id: build-api
        uses: docker/build-push-action@v7
        with:
          context: ./api
          file: ./api/Dockerfile
          push: true
          tags: |
            ${{ steps.image.outputs.api }}:latest
            ${{ steps.image.outputs.api }}:sha-${{ steps.image.outputs.short_sha }}
          labels: |
            org.opencontainers.image.title=DevOps Journey API
            org.opencontainers.image.description=Python API for the DevOps Journey project
            org.opencontainers.image.source=https://github.com/${{ github.repository }}
            org.opencontainers.image.revision=${{ steps.image.outputs.commit_sha }}
          cache-from: type=gha,scope=api
          cache-to: type=gha,mode=max,scope=api
          provenance: true
          sbom: true

      - name: Add publishing summary
        shell: bash
        run: |
          {
            echo "## Published container images"
            echo
            echo "### Web"
            echo
            echo "\`${{ steps.image.outputs.web }}:latest\`"
            echo
            echo "\`${{ steps.image.outputs.web }}:sha-${{ steps.image.outputs.short_sha }}\`"
            echo
            echo "Digest: \`${{ steps.build-web.outputs.digest }}\`"
            echo
            echo "### API"
            echo
            echo "\`${{ steps.image.outputs.api }}:latest\`"
            echo
            echo "\`${{ steps.image.outputs.api }}:sha-${{ steps.image.outputs.short_sha }}\`"
            echo
            echo "Digest: \`${{ steps.build-api.outputs.digest }}\`"
          } >> "$GITHUB_STEP_SUMMARY"
```

The Docker action major versions used here are:

```yaml
docker/login-action@v4
docker/setup-buildx-action@v4
docker/build-push-action@v7
```

---

# Part 7: Understand the workflow security

## Package-write permission

```yaml
permissions:
  contents: read
  packages: write
```

The workflow can read repository files and publish packages. It cannot modify source code, pull requests, or issues.

## Built-in token

```yaml
password: ${{ secrets.GITHUB_TOKEN }}
```

GitHub automatically creates a temporary token for each workflow run.

Advantages:

- No personal token is stored.
- It expires after the workflow.
- Its permissions are explicitly restricted.
- It is not printed in logs.

## Tested commit checkout

```yaml
ref: ${{ github.event.workflow_run.head_sha || github.sha }}
```

For automatic publishing, this checks out the exact commit tested by CI.

Without this, a workflow could accidentally build a newer, untested commit.

## Publishing condition

```yaml
if: >-
  github.event_name == 'workflow_dispatch' ||
  github.event.workflow_run.conclusion == 'success'
```

Automatic publication requires a successful CI conclusion. A manual run is also permitted.

## Why `cancel-in-progress` is false

```yaml
cancel-in-progress: false
```

A publishing operation should not normally be interrupted halfway through because a newer run starts.

---

# Part 8: Understand Buildx features

## Layer caching

```yaml
cache-from: type=gha,scope=web
cache-to: type=gha,mode=max,scope=web
```

Docker layers are cached using GitHub Actions cache storage. Later builds can reuse unchanged layers.

Separate scopes prevent web and API caches from colliding:

```text
scope=web
scope=api
```

## Provenance

```yaml
provenance: true
```

Build provenance records information about:

- Where the image was built
- Which source revision was used
- Which build process created it

## SBOM

```yaml
sbom: true
```

An SBOM—software bill of materials—describes packages and components inside the image.

These supply-chain artifacts help with auditing and vulnerability investigation.

---

# Part 9: Validate locally

Check Git changes:

```bash
git status
git diff
```

Validate the development Compose file:

```bash
docker compose config --quiet
```

Validate production Compose using local image names:

```bash
WEB_IMAGE=devops-journey-web:local \
API_IMAGE=devops-journey-api:local \
docker compose -f compose.prod.yaml config --quiet
```

Run unit tests:

```bash
python3 -m unittest discover \
  -s api \
  -p 'test_*.py' \
  -v
```

Rebuild both images:

```bash
docker build --tag devops-journey-web:local .
docker build --tag devops-journey-api:local ./api
```

---

# Part 10: Commit and create a pull request

Stage:

```bash
git add \
  Dockerfile \
  compose.prod.yaml \
  .github/workflows/publish-images.yml
```

Commit:

```bash
git commit -m "ci: publish container images to GHCR"
```

Push:

```bash
git push -u origin ci/publish-container-images
```

Create the PR:

```bash
gh pr create \
  --base main \
  --head ci/publish-container-images \
  --title "Publish container images to GHCR" \
  --body "$(cat <<'EOF'
## Summary

- Packages the production Nginx configuration into the web image
- Adds a production Compose configuration
- Publishes web and API images to GitHub Container Registry
- Adds immutable commit-based image tags
- Generates provenance and SBOM attestations
- Publishes only after CI passes on main

## Test plan

- [x] Unit tests pass locally
- [x] Development Compose configuration is valid
- [x] Production Compose configuration is valid
- [x] Production images build locally
- [x] Production application passes health checks
EOF
)"
```

Watch CI:

```bash
gh pr checks --watch
```

The publishing workflow will not publish from this pull request. It is configured to publish only after successful CI on `main`.

---

# Part 11: Merge and monitor publication

When PR checks pass:

```bash
gh pr merge --squash --delete-branch
```

Synchronize:

```bash
git switch main
git pull origin main
```

The sequence should now be:

```text
1. CI runs on main
2. CI succeeds
3. Publish container images starts
4. Web and API images are pushed
```

Monitor CI:

```bash
gh run list --workflow ci.yml --branch main --limit 3
```

Monitor publishing:

```bash
gh run list \
  --workflow publish-images.yml \
  --branch main \
  --limit 3
```

Watch the latest publishing run:

```bash
gh run watch
```

If the wrong workflow is selected, find its run ID:

```bash
gh run list --workflow publish-images.yml --limit 5
```

Then:

```bash
gh run watch RUN_ID
```

Inspect failures:

```bash
gh run view RUN_ID --log-failed
```

---

# Part 12: Find the published images

Get your GitHub username in lowercase:

```bash
OWNER="$(gh api user --jq '.login' | tr '[:upper:]' '[:lower:]')"
echo "$OWNER"
```

Your image names are:

```bash
echo "ghcr.io/$OWNER/devops-journey-web:latest"
echo "ghcr.io/$OWNER/devops-journey-api:latest"
```

Open your GitHub profile’s **Packages** section in a browser.

You should find:

```text
devops-journey-web
devops-journey-api
```

New packages may initially be private, depending on the package and repository settings.

---

# Part 13: Authenticate locally with GHCR

For public images, authentication may not be required.

For private packages, log in without printing your token:

```bash
gh auth token |
  docker login ghcr.io \
    --username "$(gh api user --jq '.login')" \
    --password-stdin
```

Expected:

```text
Login Succeeded
```

> If the GitHub CLI token lacks package access, use a GitHub personal access token with `read:packages`. Pass it through `--password-stdin`; never place a token in a Dockerfile, Compose file, Git commit, or command-line URL.

---

# Part 14: Pull and inspect the published images

Pull both images:

```bash
docker pull "ghcr.io/$OWNER/devops-journey-web:latest"
docker pull "ghcr.io/$OWNER/devops-journey-api:latest"
```

List them:

```bash
docker image ls "ghcr.io/$OWNER/devops-journey-*"
```

Inspect web image labels:

```bash
docker image inspect \
  "ghcr.io/$OWNER/devops-journey-web:latest" \
  --format '{{json .Config.Labels}}'
```

If `jq` is installed:

```bash
docker image inspect \
  "ghcr.io/$OWNER/devops-journey-web:latest" \
  --format '{{json .Config.Labels}}' | jq
```

Look for:

```text
org.opencontainers.image.source
org.opencontainers.image.revision
org.opencontainers.image.title
```

Display its digest:

```bash
docker image inspect \
  "ghcr.io/$OWNER/devops-journey-web:latest" \
  --format '{{range .RepoDigests}}{{println .}}{{end}}'
```

---

# Part 15: Run the registry images locally

Export the image references:

```bash
export WEB_IMAGE="ghcr.io/$OWNER/devops-journey-web:latest"
export API_IMAGE="ghcr.io/$OWNER/devops-journey-api:latest"
```

Make sure port 8080 is free:

```bash
docker compose down 2>/dev/null || true
```

Pull through production Compose:

```bash
docker compose -f compose.prod.yaml pull
```

Start:

```bash
docker compose -f compose.prod.yaml up \
  --detach \
  --wait
```

Check:

```bash
docker compose -f compose.prod.yaml ps
```

Test:

```bash
curl --fail http://localhost:8080/nginx-health
curl --fail http://localhost:8080/api/health
curl --fail http://localhost:8080/api/info
```

Open:

```text
http://localhost:8080
```

You are now running the exact artifacts produced by GitHub Actions—not locally rebuilt images.

---

# Part 16: Run a commit-specific image

Get the current short commit:

```bash
SHORT_SHA="$(git rev-parse --short=12 HEAD)"
echo "$SHORT_SHA"
```

Set commit-specific image references:

```bash
export WEB_IMAGE="ghcr.io/$OWNER/devops-journey-web:sha-$SHORT_SHA"
export API_IMAGE="ghcr.io/$OWNER/devops-journey-api:sha-$SHORT_SHA"
```

Pull and recreate:

```bash
docker compose -f compose.prod.yaml pull
```

```bash
docker compose -f compose.prod.yaml up \
  --detach \
  --wait
```

Verify:

```bash
docker compose -f compose.prod.yaml images
```

This is closer to a real deployment because it specifies the source revision rather than using mutable `latest` tags.

---

# Part 17: Practise rollback

Record the current application commit:

```bash
git log --oneline --decorate -5
```

Find an earlier published commit tag in GitHub Packages, then set:

```bash
export WEB_IMAGE="ghcr.io/$OWNER/devops-journey-web:sha-PREVIOUS_SHA"
export API_IMAGE="ghcr.io/$OWNER/devops-journey-api:sha-PREVIOUS_SHA"
```

Deploy:

```bash
docker compose -f compose.prod.yaml pull
docker compose -f compose.prod.yaml up --detach --wait
```

That is a basic rollback:

```text
Bad current image
       │
       ▼
Select previous commit tag
       │
       ▼
Pull previous images
       │
       ▼
Recreate containers
```

No source-code rebuild is necessary because the previous artifacts already exist in the registry.

---

# Part 18: Clean up

Stop the production application:

```bash
docker compose -f compose.prod.yaml down
```

Keep the named volume if you want to retain the request counter.

To delete the volume too:

```bash
docker compose -f compose.prod.yaml down --volumes
```

Log out of GHCR if desired:

```bash
docker logout ghcr.io
```

---

# Challenge: Add semantic version publishing

Create a tag:

```bash
git switch main
git pull origin main
git tag -a v1.0.0 -m "Release version 1.0.0"
git push origin v1.0.0
```

Extend the publishing design so release tags produce:

```text
devops-journey-web:1.0.0
devops-journey-web:1.0
devops-journey-api:1.0.0
devops-journey-api:1.0
```

Keep commit tags as well:

```text
sha-a1b2c3d4e5f6
```

Do not overwrite old semantic version tags after publishing a release.

---

# Final verification

Run:

```bash
cd ~/devops-journey/01-linux-git

git switch main
git status

gh run list --workflow ci.yml --branch main --limit 3
gh run list --workflow publish-images.yml --limit 3

docker pull "ghcr.io/$OWNER/devops-journey-web:latest"
docker pull "ghcr.io/$OWNER/devops-journey-api:latest"

export WEB_IMAGE="ghcr.io/$OWNER/devops-journey-web:latest"
export API_IMAGE="ghcr.io/$OWNER/devops-journey-api:latest"

docker compose -f compose.prod.yaml up --detach --wait
docker compose -f compose.prod.yaml ps

curl --fail http://localhost:8080/nginx-health
curl --fail http://localhost:8080/api/health
curl --fail http://localhost:8080/api/info
```

Success means:

- CI passes before publishing begins.
- Both images are available in GHCR.
- Each image has `latest` and commit-specific tags.
- OCI source and revision labels are present.
- Production Compose uses registry images.
- Both services become healthy.
- The application works without source-code bind mounts.
- You understand how to select an older image for rollback.

## Skills completed

You have now practised:

- Understanding CI versus continuous delivery
- Using GitHub Container Registry
- Publishing with the temporary `GITHUB_TOKEN`
- Applying least-privilege workflow permissions
- Building production container images
- Tagging images by source commit
- Understanding mutable tags and immutable digests
- Using Buildx caching
- Generating build provenance and SBOMs
- Separating development and production Compose files
- Deploying registry artifacts locally
- Performing image-based rollbacks

**Next lab:** AWS foundations and EC2—create a cost-controlled AWS environment, configure IAM safely, launch an Ubuntu EC2 instance, secure SSH access, install Docker, and deploy these GHCR images to the cloud.