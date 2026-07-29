# Practical Lab 5: Nginx Reverse Proxy, Security, Logs, and Rate Limiting

**Goal:** Turn the basic Nginx configuration into a production-style reverse proxy with:

- Custom access logs
- Security headers
- Health endpoint
- Custom error pages
- Static-file caching
- API rate limiting
- Proxy timeouts
- Configuration testing and graceful reloads

**Time:** Approximately 75–90 minutes.

## Request flow

```text
Client
  │
  │ GET /styles.css
  ├──────────────────────► Nginx serves static file
  │
  │ GET /api/info
  └──────────────────────► Nginx
                              │
                              │ proxy_pass
                              ▼
                         Python API
```

Nginx is the only service exposed to the host. The Python API remains on Docker’s internal network.

---

# Part 1: Prepare the repository

Open the project:

```bash
cd ~/devops-journey/01-linux-git
```

Make sure the previous application is running:

```bash
docker compose up -d
docker compose ps
```

Verify:

```bash
curl --fail http://localhost:8080
curl --fail http://localhost:8080/api/health
```

Synchronize Git:

```bash
git switch main
git pull origin main
git status
```

Create a feature branch:

```bash
git switch -c feature/harden-nginx
```

---

# Part 2: Move JavaScript into a separate file

A strict Content Security Policy should avoid inline JavaScript.

Create:

```bash
nano website/app.js
```

Add:

```javascript
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
```

Now edit the HTML:

```bash
nano website/index.html
```

Replace the inline `<script>...</script>` block with:

```html
<script src="app.js"></script>
```

The bottom of the file should resemble:

```html
        <h2>API status</h2>
        <p id="api-result">Contacting the API...</p>
    </main>

    <script src="app.js"></script>
</body>
</html>
```

---

# Part 3: Create a custom 404 page

Create:

```bash
nano website/404.html
```

Add:

```html
<!doctype html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Page Not Found</title>
    <link rel="stylesheet" href="/styles.css">
</head>
<body>
    <main class="container">
        <h1>404</h1>
        <p>The requested page could not be found.</p>
        <p><a href="/">Return to the homepage</a></p>
    </main>
</body>
</html>
```

Optional CSS improvement:

```bash
nano website/styles.css
```

Add:

```css
a {
    color: #38bdf8;
}

a:hover {
    color: #7dd3fc;
}
```

---

# Part 4: Configure production-style Nginx

Open:

```bash
nano nginx/default.conf
```

Replace its contents with:

```nginx
# This file is loaded inside Nginx's http context.

log_format devops
    '$remote_addr - $remote_user [$time_local] '
    '"$request" $status $body_bytes_sent '
    '"$http_referer" "$http_user_agent" '
    'request_time=$request_time '
    'upstream_addr=$upstream_addr '
    'upstream_time=$upstream_response_time '
    'request_id=$request_id';

limit_req_zone $binary_remote_addr
    zone=api_limit:10m
    rate=5r/s;

server {
    listen 80;
    server_name localhost devops.local _;

    server_tokens off;
    client_max_body_size 1m;

    root /usr/share/nginx/html;
    index index.html;

    access_log /var/log/nginx/access.log devops;
    error_log /var/log/nginx/error.log warn;

    # Security headers
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'" always;

    # Lightweight Nginx health check
    location = /nginx-health {
        access_log off;
        default_type text/plain;
        return 200 "healthy\n";
    }

    # Static website
    location / {
        try_files $uri $uri/ =404;
    }

    # Cache static assets in the browser
    location ~* \.(css|js|png|jpg|jpeg|gif|svg|ico|webp)$ {
        expires 1h;
        try_files $uri =404;
    }

    # Reverse proxy to the internal API service
    location /api/ {
        limit_req zone=api_limit burst=10 nodelay;
        limit_req_status 429;
        limit_req_log_level warn;

        proxy_pass http://api:5000/;
        proxy_http_version 1.1;

        proxy_connect_timeout 3s;
        proxy_send_timeout 10s;
        proxy_read_timeout 10s;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-ID $request_id;

        proxy_hide_header X-Powered-By;
    }

    error_page 404 /404.html;

    location = /404.html {
        internal;
    }
}
```

---

# Part 5: Understand the important configuration

## Hide the Nginx version

```nginx
server_tokens off;
```

This prevents Nginx from advertising its exact version in normal error pages and headers.

It is a minor hardening measure, not a replacement for updates.

## Limit request body size

```nginx
client_max_body_size 1m;
```

Requests larger than 1 MB receive:

```text
413 Request Entity Too Large
```

## Security headers

```nginx
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
```

These reduce risks such as MIME-type confusion and clickjacking.

The Content Security Policy restricts resources to the same origin:

```nginx
default-src 'self'
```

## Rate-limiting storage

```nginx
limit_req_zone $binary_remote_addr
    zone=api_limit:10m
    rate=5r/s;
```

This creates a shared-memory zone that tracks requests by client IP.

## Apply the rate limit

```nginx
limit_req zone=api_limit burst=10 nodelay;
```

The API permits approximately five requests per second, with a short burst allowance of ten.

Excess requests receive:

```text
429 Too Many Requests
```

## Proxy timeouts

```nginx
proxy_connect_timeout 3s;
proxy_send_timeout 10s;
proxy_read_timeout 10s;
```

Timeouts prevent Nginx from waiting indefinitely for an unhealthy upstream service.

## Forwarded headers

```nginx
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

These tell the API about the original request rather than only the Nginx-to-API connection.

---

# Part 6: Test the Nginx configuration

Before reloading Nginx, test the configuration:

```bash
docker compose exec web nginx -t
```

Expected:

```text
syntax is ok
test is successful
```

If there is an error, do not reload. Read the reported filename and line number, fix the configuration, and run `nginx -t` again.

View the complete active configuration:

```bash
docker compose exec web nginx -T
```

`nginx -T` tests the configuration and prints all loaded configuration files.

---

# Part 7: Reload Nginx gracefully

The configuration file is bind-mounted, but Nginx does not automatically reload it.

Reload without stopping the container:

```bash
docker compose exec web nginx -s reload
```

Check status:

```bash
docker compose ps
```

Check recent logs:

```bash
docker compose logs --tail=30 web
```

A graceful reload allows existing requests to finish while new worker processes use the updated configuration.

---

# Part 8: Test the Nginx health endpoint

Run:

```bash
curl -i http://localhost:8080/nginx-health
```

Expected:

```text
HTTP/1.1 200 OK
Content-Type: text/plain

healthy
```

Notice that this endpoint tests Nginx itself. It does not test the Python API.

You now have two different health checks:

```text
/nginx-health → Is Nginx responding?
/api/health   → Is the API responding through Nginx?
```

Test both:

```bash
curl --fail http://localhost:8080/nginx-health
curl --fail http://localhost:8080/api/health
```

---

# Part 9: Test security headers

Inspect the homepage response:

```bash
curl -I http://localhost:8080
```

Look for:

```text
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: camera=(), microphone=(), geolocation=()
Content-Security-Policy: ...
```

Check whether the server version is hidden:

```bash
curl -I http://localhost:8080 | grep -i server
```

You may see:

```text
Server: nginx
```

but should not see the exact Nginx version.

---

# Part 10: Test static-file caching

Inspect the stylesheet:

```bash
curl -I http://localhost:8080/styles.css
```

Look for headers resembling:

```text
Expires: ...
Cache-Control: max-age=3600
```

The browser can cache the stylesheet for approximately one hour.

Inspect the JavaScript:

```bash
curl -I http://localhost:8080/app.js
```

It should also have caching headers.

> In production, long cache durations usually require versioned filenames such as `app.a1b2c3.js`, so new deployments do not leave users with stale files.

---

# Part 11: Test the custom 404 page

Request a file that does not exist:

```bash
curl -i http://localhost:8080/does-not-exist
```

Expected:

```text
HTTP/1.1 404 Not Found
```

The body should contain:

```html
<h1>404</h1>
```

Test it in your browser:

```text
http://localhost:8080/does-not-exist
```

Try requesting the custom page directly:

```bash
curl -i http://localhost:8080/404.html
```

Because the location is marked `internal`, direct access should not normally serve it as a regular public page.

---

# Part 12: Test API reverse proxying

Run:

```bash
curl -i http://localhost:8080/api/health
```

Then:

```bash
curl -i http://localhost:8080/api/info
```

The client talks only to Nginx on port 8080:

```text
curl → localhost:8080 → Nginx → api:5000
```

Confirm that the API is still not directly exposed:

```bash
curl --connect-timeout 2 http://localhost:5000/health
```

That request should fail.

---

# Part 13: Test rate limiting

Send many API requests concurrently:

```bash
seq 1 40 |
  xargs -P20 -I{} \
    curl -s -o /dev/null -w '%{http_code}\n' \
    http://localhost:8080/api/health |
  sort |
  uniq -c
```

You should see a mixture similar to:

```text
25 200
15 429
```

The exact numbers can differ.

- `200` means the request was accepted.
- `429` means the request exceeded the rate limit.

Wait a few seconds and try normally:

```bash
curl -i http://localhost:8080/api/health
```

It should return `200` again.

Check Nginx logs:

```bash
docker compose logs --tail=50 web
```

Look for rate-limit warnings.

---

# Part 14: Test upstream failure behavior

Stop only the API:

```bash
docker compose stop api
```

The static website should still respond:

```bash
curl -I http://localhost:8080
```

The API request should fail through Nginx:

```bash
curl -i http://localhost:8080/api/health
```

Expected response:

```text
502 Bad Gateway
```

This means:

```text
Client reached Nginx successfully
           │
           └── Nginx could not reach the API
```

View the error:

```bash
docker compose logs --tail=30 web
```

Start the API again:

```bash
docker compose start api
```

Wait until it becomes healthy:

```bash
docker compose ps
```

Retest:

```bash
curl --fail http://localhost:8080/api/health
```

---

# Part 15: Examine access logs

Generate some requests:

```bash
curl http://localhost:8080 >/dev/null
curl http://localhost:8080/styles.css >/dev/null
curl http://localhost:8080/api/info >/dev/null
curl http://localhost:8080/missing >/dev/null
```

View the logs:

```bash
docker compose logs --tail=20 web
```

A custom access-log entry should contain information resembling:

```text
"GET /api/info HTTP/1.1" 200
request_time=0.002
upstream_addr=172.x.x.x:5000
upstream_time=0.002
request_id=...
```

Important fields:

| Field | Meaning |
|---|---|
| `$status` | HTTP response status |
| `$request_time` | Total time Nginx spent handling the request |
| `$upstream_addr` | API container address |
| `$upstream_response_time` | Time the API took to respond |
| `$request_id` | Identifier used to correlate logs |

For static files, `upstream_addr` and `upstream_time` will normally be empty because Nginx served them directly.

---

# Part 16: Add Nginx to the Compose health checks

Edit:

```bash
nano compose.yaml
```

Add a health check to the `web` service:

```yaml
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
    healthcheck:
      test:
        - CMD
        - wget
        - --quiet
        - --tries=1
        - --spider
        - http://localhost/nginx-health
      interval: 10s
      timeout: 3s
      retries: 3
      start_period: 5s
```

Be careful with YAML indentation.

Validate:

```bash
docker compose config
```

Apply the change:

```bash
docker compose up -d
```

Wait several seconds and check:

```bash
docker compose ps
```

Both services should report healthy:

```text
api    Up ... (healthy)
web    Up ... (healthy)
```

---

# Part 17: Review and commit

Run all validation checks:

```bash
docker compose config >/dev/null &&
docker compose exec web nginx -t &&
curl --fail http://localhost:8080/nginx-health &&
curl --fail http://localhost:8080/api/health
```

Inspect Git changes:

```bash
git status
git diff
```

Stage:

```bash
git add \
  nginx/default.conf \
  compose.yaml \
  website/index.html \
  website/app.js \
  website/404.html \
  website/styles.css
```

Commit:

```bash
git commit -m "feat: harden Nginx reverse proxy"
```

Push:

```bash
git push -u origin feature/harden-nginx
```

Create a pull request:

```bash
gh pr create \
  --base main \
  --head feature/harden-nginx \
  --title "Harden Nginx reverse proxy" \
  --body "Adds security headers, rate limiting, health checks, custom logging, static caching, proxy timeouts, and a custom 404 page."
```

Review:

```bash
gh pr view --web
```

Merge:

```bash
gh pr merge --merge --delete-branch
```

Synchronize:

```bash
git switch main
git pull origin main
```

---

# Challenge

Complete these tasks independently:

1. Add a custom maintenance page at:

   ```text
   website/maintenance.html
   ```

2. Add a temporary Nginx location:

   ```text
   /maintenance
   ```

3. Return the maintenance page with HTTP status `503`.
4. Add this header:

   ```text
   Retry-After: 300
   ```

5. Run `nginx -t`.
6. Gracefully reload Nginx.
7. Verify:

   ```bash
   curl -i http://localhost:8080/maintenance
   ```

8. Remove the temporary maintenance route after testing.
9. Test and reload Nginx again.

---

# Final verification

Run:

```bash
cd ~/devops-journey/01-linux-git

docker compose config --services
docker compose ps
docker compose exec web nginx -t

curl -I http://localhost:8080
curl -i http://localhost:8080/nginx-health
curl -i http://localhost:8080/api/health
curl -i http://localhost:8080/not-found

docker compose logs --tail=20 web

git status
git log --oneline --decorate -5
```

Success means:

- Nginx configuration syntax is valid.
- Both containers are healthy.
- Static files are served by Nginx.
- `/api/*` requests are proxied to Python.
- Security headers are present.
- Static assets have caching headers.
- Missing pages return the custom 404 page.
- Excess API traffic receives `429`.
- An unavailable API produces `502`.
- Access logs include timing and upstream details.
- Changes are merged into `main`.

## Skills completed

You have now practised:

- Nginx server blocks
- Static file serving
- Reverse proxying
- Forwarded request headers
- Configuration testing
- Graceful configuration reloads
- Access and error logs
- Security response headers
- Content Security Policy
- Static asset caching
- Rate limiting
- Proxy timeout configuration
- Custom error pages
- Nginx and application health checks
- Diagnosing `404`, `429`, and `502` responses

**Next lab:** GitHub Actions CI—automatically validate Python, Docker Compose, Nginx configuration, build container images, and run end-to-end tests on every push and pull request.