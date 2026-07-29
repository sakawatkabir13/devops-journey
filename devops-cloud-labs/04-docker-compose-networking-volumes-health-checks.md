# Practical Lab 4: Docker Compose, Networking, Volumes, and Health Checks

**Goal:** Use Docker Compose to run a two-container application:

1. **Web container:** Nginx serves your website.
2. **API container:** Python returns JSON data.
3. Nginx forwards `/api/*` requests to the API over Docker’s internal network.
4. A named volume preserves API data.

**Time:** Approximately 75–90 minutes.

## Architecture

```text
Browser / curl
      │
      │ http://localhost:8080
      ▼
┌───────────────────────────┐
│ Nginx container           │
│                           │
│ /       → static website  │
│ /api/* → reverse proxy    │
└─────────────┬─────────────┘
              │ http://api:5000
              │ Docker internal network
              ▼
┌───────────────────────────┐
│ Python API container      │
│                           │
│ /health → health status   │
│ /info   → JSON + counter  │
└─────────────┬─────────────┘
              │
              ▼
       Docker named volume
          api-data
```

---

# Part 1: Stop the previous container

The previous lab may still have a container using port 8080.

Check:

```bash
docker ps
```

Remove the previous container:

```bash
docker rm -f devops-site 2>/dev/null || true
```

Confirm port 8080 is available:

```bash
sudo ss -lntp | grep ':8080' || echo "Port 8080 is available"
```

---

# Part 2: Create a feature branch

Open the project:

```bash
cd ~/devops-journey/01-linux-git
```

Synchronize `main`:

```bash
git switch main
git pull origin main
git status
```

Create a branch:

```bash
git switch -c feature/add-docker-compose
```

Create directories for the API and Nginx configuration:

```bash
mkdir -p api nginx
```

---

# Part 3: Create the Python API

Create the server:

```bash
nano api/server.py
```

Add:

```python
import json
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


COUNTER_FILE = Path("/data/request-count.txt")


def increment_counter():
    COUNTER_FILE.parent.mkdir(parents=True, exist_ok=True)

    try:
        current = int(COUNTER_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        current = 0

    current += 1
    COUNTER_FILE.write_text(str(current))
    return current


class RequestHandler(BaseHTTPRequestHandler):
    def send_json(self, status_code, payload):
        body = json.dumps(payload, indent=2).encode("utf-8")

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]

        if path == "/health":
            self.send_json(
                200,
                {
                    "status": "healthy",
                    "service": "devops-api",
                },
            )
            return

        if path == "/info":
            request_count = increment_counter()

            self.send_json(
                200,
                {
                    "message": "Hello from the Docker API",
                    "hostname": socket.gethostname(),
                    "requests": request_count,
                },
            )
            return

        self.send_json(
            404,
            {
                "error": "Not found",
                "path": path,
            },
        )


if __name__ == "__main__":
    address = ("0.0.0.0", 5000)
    server = HTTPServer(address, RequestHandler)

    print("API listening on port 5000", flush=True)
    server.serve_forever()
```

Save and exit.

## What the API does

| Endpoint | Purpose |
|---|---|
| `/health` | Reports whether the API is healthy |
| `/info` | Returns a message, hostname, and persistent request counter |
| Any other path | Returns `404 Not Found` |

---

# Part 4: Create the API Dockerfile

Create:

```bash
nano api/Dockerfile
```

Add:

```dockerfile
FROM python:3.12-alpine

WORKDIR /app

COPY server.py .

RUN mkdir -p /data

EXPOSE 5000

CMD ["python", "server.py"]
```

The API uses only Python’s standard library, so no extra Python packages are required.

---

# Part 5: Configure Nginx as a reverse proxy

Create:

```bash
nano nginx/default.conf
```

Add:

```nginx
server {
    listen 80;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://api:5000/;

        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

The important instruction is:

```nginx
proxy_pass http://api:5000/;
```

`api` is not an internet hostname. It is the API service name from Docker Compose. Docker provides internal DNS that resolves it to the API container.

The trailing slashes mean:

```text
Browser request:  /api/health
API receives:     /health
```

---

# Part 6: Update the website

Edit:

```bash
nano website/index.html
```

Replace its contents with:

```html
<!doctype html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DevOps Journey</title>
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <main class="container">
        <h1>My DevOps Journey</h1>

        <p>
            This website is running in Nginx and communicating
            with a Python API through Docker networking.
        </p>

        <h2>API status</h2>
        <p id="api-result">Contacting the API...</p>
    </main>

    <script>
        async function loadApiInformation() {
            const resultElement = document.getElementById("api-result");

            try {
                const response = await fetch("/api/info");

                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }

                const data = await response.json();

                resultElement.textContent =
                    `${data.message} | Container: ${data.hostname} | Requests: ${data.requests}`;
            } catch (error) {
                resultElement.textContent =
                    `API request failed: ${error.message}`;
            }
        }

        loadApiInformation();
    </script>
</body>
</html>
```

Save and exit.

---

# Part 7: Create the Compose file

Create the file at the repository root:

```bash
nano compose.yaml
```

Add:

```yaml
services:
  api:
    build:
      context: ./api
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
    image: nginx:alpine
    restart: unless-stopped
    ports:
      - "8080:80"
    volumes:
      - ./website:/usr/share/nginx/html:ro
      - ./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on:
      api:
        condition: service_healthy

volumes:
  api-data:
```

## Compose file explained

### Services

```yaml
services:
  api:
  web:
```

Each service becomes one or more containers.

### Build

```yaml
build:
  context: ./api
```

The API image is built from `api/Dockerfile`.

### Port publishing

```yaml
ports:
  - "8080:80"
```

This publishes Nginx to your computer:

```text
Host port 8080 → web container port 80
```

The API has no `ports` section. It is available to other containers but is not directly exposed to your computer.

### Bind mounts

```yaml
- ./website:/usr/share/nginx/html:ro
- ./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
```

Files from your host are mounted inside the Nginx container.

The `:ro` suffix means **read-only**.

### Named volume

```yaml
- api-data:/data
```

Docker manages persistent storage for the API counter.

### Health check

Compose periodically requests:

```text
http://localhost:5000/health
```

from inside the API container.

### Dependency

```yaml
depends_on:
  api:
    condition: service_healthy
```

Compose waits for the API to become healthy before starting Nginx.

---

# Part 8: Validate the Compose configuration

Run:

```bash
docker compose config
```

This parses and normalizes `compose.yaml`.

If the YAML is invalid, Docker will report a line number or configuration error. Fix all reported errors before continuing.

List the services:

```bash
docker compose config --services
```

Expected:

```text
api
web
```

---

# Part 9: Build and start the application

Build the images:

```bash
docker compose build
```

Start the application:

```bash
docker compose up -d
```

The `-d` option runs the application in the background.

Check status:

```bash
docker compose ps
```

Expected general structure:

```text
NAME                    SERVICE   STATUS
...-api-1               api       Up ... (healthy)
...-web-1               web       Up ...
```

If the API initially says `health: starting`, wait a few seconds and run:

```bash
docker compose ps
```

again.

---

# Part 10: Test the application

Test the static website:

```bash
curl -I http://localhost:8080
```

Expected:

```text
HTTP/1.1 200 OK
```

Test the API health endpoint through Nginx:

```bash
curl http://localhost:8080/api/health
```

Expected JSON:

```json
{
  "status": "healthy",
  "service": "devops-api"
}
```

Call the information endpoint:

```bash
curl http://localhost:8080/api/info
```

Run it again:

```bash
curl http://localhost:8080/api/info
```

The request counter should increase:

```json
{
  "message": "Hello from the Docker API",
  "hostname": "...",
  "requests": 2
}
```

Open the website:

```text
http://localhost:8080
```

The page should display information returned by the API.

---

# Part 11: Inspect Compose logs

View logs from all services:

```bash
docker compose logs
```

View only API logs:

```bash
docker compose logs api
```

View only Nginx logs:

```bash
docker compose logs web
```

Follow logs in real time:

```bash
docker compose logs -f
```

Refresh the browser and observe requests. Press **Ctrl+C** to stop following logs.

The containers continue running.

---

# Part 12: Explore Docker networking

List Docker networks:

```bash
docker network ls
```

Compose automatically created a network for this project.

Inspect the network name reported by:

```bash
docker compose config --networks
```

The simplest way to demonstrate internal communication is from the Nginx container:

```bash
docker compose exec web wget -qO- http://api:5000/health
```

Expected:

```json
{
  "status": "healthy",
  "service": "devops-api"
}
```

Now try to access the API directly from your Ubuntu host:

```bash
curl http://localhost:5000/health
```

This should fail because API port 5000 was not published.

That distinction is important:

```text
Inside Compose network:
    web → http://api:5000       Works

Outside Docker:
    host → http://localhost:5000   Does not work

Outside Docker through Nginx:
    host → http://localhost:8080/api/health   Works
```

---

# Part 13: Examine the health check

Get the API container ID:

```bash
docker compose ps -q api
```

Inspect its health:

```bash
docker inspect \
  --format '{{json .State.Health}}' \
  "$(docker compose ps -q api)"
```

For formatted output, if `jq` is installed:

```bash
docker inspect \
  --format '{{json .State.Health}}' \
  "$(docker compose ps -q api)" | jq
```

You should see:

```json
"Status": "healthy"
```

View individual health-check results:

```bash
docker inspect \
  --format '{{range .State.Health.Log}}{{println .End .ExitCode .Output}}{{end}}' \
  "$(docker compose ps -q api)"
```

---

# Part 14: Test bind mounts

Your website is mounted from the host into the Nginx container.

Edit the website:

```bash
nano website/index.html
```

Change:

```html
<h1>My DevOps Journey</h1>
```

to:

```html
<h1>My Docker Compose Journey</h1>
```

Save and refresh:

```text
http://localhost:8080
```

The change should appear without rebuilding or restarting the image.

Why?

```text
Host file:
./website/index.html
        │
        │ bind mount
        ▼
Container file:
/usr/share/nginx/html/index.html
```

Bind mounts are useful during development, but production deployments commonly copy application files into versioned images.

---

# Part 15: Test volume persistence

Call the API a few times:

```bash
curl http://localhost:8080/api/info
curl http://localhost:8080/api/info
curl http://localhost:8080/api/info
```

Record the current `requests` value.

Stop and remove the Compose containers:

```bash
docker compose down
```

Check:

```bash
docker compose ps
```

The containers are gone, but the named volume remains:

```bash
docker volume ls
```

Start the application again:

```bash
docker compose up -d
```

Wait for it to become healthy:

```bash
docker compose ps
```

Call the API:

```bash
curl http://localhost:8080/api/info
```

The counter should continue from its previous value because `docker compose down` did not remove the named volume.

## Delete the persistent volume

> The next command intentionally deletes this lab’s persistent counter data.

Run:

```bash
docker compose down -v
```

Start again:

```bash
docker compose up -d
```

Test:

```bash
curl http://localhost:8080/api/info
```

The request counter should restart at `1`.

The difference is:

```bash
docker compose down
```

Removes containers and networks but preserves named volumes.

```bash
docker compose down -v
```

Also removes named volumes declared by the Compose project.

---

# Part 16: Useful Compose commands

```bash
# Start or update services
docker compose up -d

# Build images
docker compose build

# Build and start
docker compose up -d --build

# Show service status
docker compose ps

# View logs
docker compose logs

# Follow logs
docker compose logs -f

# Run a command in a service
docker compose exec api sh

# Restart one service
docker compose restart api

# Stop services without removing containers
docker compose stop

# Start stopped services
docker compose start

# Remove containers and project network
docker compose down

# Remove containers, network, and volumes
docker compose down -v
```

To exit the API container shell after `docker compose exec api sh`, run:

```bash
exit
```

---

# Part 17: Commit and create a pull request

Check the changes:

```bash
git status
git diff
```

Validate Compose again before committing:

```bash
docker compose config >/dev/null && echo "Compose configuration is valid"
```

Test both endpoints:

```bash
curl --fail http://localhost:8080 >/dev/null &&
curl --fail http://localhost:8080/api/health
```

Stage the files:

```bash
git add \
  api/Dockerfile \
  api/server.py \
  nginx/default.conf \
  website/index.html \
  compose.yaml
```

Commit:

```bash
git commit -m "feat: add multi-container Compose application"
```

Push:

```bash
git push -u origin feature/add-docker-compose
```

Create a pull request:

```bash
gh pr create \
  --base main \
  --head feature/add-docker-compose \
  --title "Add Docker Compose application" \
  --body "Adds an Nginx frontend, Python API, health checks, internal networking, bind mounts, and persistent volume storage."
```

Review it:

```bash
gh pr view --web
```

Merge:

```bash
gh pr merge --merge --delete-branch
```

Synchronize locally:

```bash
git switch main
git pull origin main
```

---

# Challenge

Complete these tasks independently:

1. Add an API endpoint named `/version`.
2. Make it return:

   ```json
   {
     "application": "devops-journey",
     "version": "1.0.0"
   }
   ```

3. Rebuild only the API:

   ```bash
   docker compose build api
   ```

4. Recreate the API container:

   ```bash
   docker compose up -d api
   ```

5. Verify it through Nginx:

   ```bash
   curl http://localhost:8080/api/version
   ```

6. Check that both containers are healthy/running.
7. Commit the change on a new branch and merge it through a pull request.

---

# Final verification

Run:

```bash
cd ~/devops-journey/01-linux-git

docker compose config --services
docker compose ps
docker compose images
docker compose logs --tail=20
docker volume ls

curl --fail http://localhost:8080
curl --fail http://localhost:8080/api/health
curl --fail http://localhost:8080/api/info

git status
git log --oneline --decorate -5
```

Success means:

- Compose recognizes the `api` and `web` services.
- The API reports `healthy`.
- Nginx responds on port 8080.
- `/api/health` works through Nginx.
- API port 5000 is not publicly exposed.
- The counter persists through `docker compose down`.
- Website changes appear through the bind mount.
- All changes are merged into `main`.

## Skills completed

You have now practised:

- Defining multi-container applications
- Building services with Docker Compose
- Using Docker’s internal DNS
- Publishing only necessary ports
- Configuring Nginx as a reverse proxy
- Adding container health checks
- Using service dependencies
- Using read-only bind mounts
- Persisting data with named volumes
- Viewing multi-service logs
- Starting, stopping, rebuilding, and removing Compose projects

**Next lab:** Nginx in depth—server blocks, reverse-proxy headers, access/error logs, custom error pages, rate limiting, security headers, and configuration testing.