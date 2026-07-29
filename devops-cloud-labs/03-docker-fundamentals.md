# Practical Lab 3: Docker Fundamentals

**Goal:** Package your website as a Docker image, run it as a container, inspect it, and publish the Docker changes through GitHub.

**Time:** Approximately 60–90 minutes.

## Core concepts

| Term | Meaning |
|---|---|
| **Dockerfile** | Instructions used to build an image. |
| **Image** | A read-only application template containing code and dependencies. |
| **Container** | A running instance of an image. |
| **Registry** | A service such as Docker Hub that stores images. |
| **Port mapping** | Connects a port on your machine to a port inside a container. |
| **Volume** | Persistent or shared storage attached to a container. |
| **Build context** | Files Docker can access while building an image. |

The workflow is:

```text
Dockerfile
    │ docker build
    ▼
Docker image
    │ docker run
    ▼
Running container
    │
    └── localhost:8080 → container port 80 → Nginx website
```

---

# Part 1: Check Docker

Run:

```bash
docker --version
docker compose version
sudo systemctl status docker --no-pager
```

If Docker is installed and active, skip to **Part 3**.

---

# Part 2: Install Docker Engine on Ubuntu

These steps use Docker’s official Ubuntu package repository.

## 2.1 Add Docker’s repository

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
```

Download Docker’s signing key:

```bash
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
```

Make it readable:

```bash
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

Add the repository:

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

Update package information:

```bash
sudo apt update
```

> If this reports that your Ubuntu release has no Docker repository, stop rather than substituting a different Ubuntu codename. Keep the exact error for troubleshooting.

## 2.2 Install Docker

```bash
sudo apt install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin
```

Start and enable Docker:

```bash
sudo systemctl enable --now docker
```

Verify the service:

```bash
sudo systemctl status docker --no-pager
```

Press `q` if the command opens a pager.

## 2.3 Run the test container

```bash
sudo docker run --rm hello-world
```

Docker will:

1. Look for the `hello-world` image locally.
2. Download it if necessary.
3. Create a container.
4. run the container.
5. Print a confirmation.
6. Remove the container because of `--rm`.

---

# Part 3: Optionally run Docker without `sudo`

By default, Docker operations may require `sudo`.

You can add yourself to the `docker` group:

```bash
sudo usermod -aG docker "$USER"
```

Log out of Ubuntu completely and log back in. Then verify:

```bash
groups
docker run --rm hello-world
```

> **Security warning:** Membership in the `docker` group effectively grants root-level control over the machine. On shared or security-sensitive systems, use `sudo` or investigate Docker rootless mode instead.

The remaining examples use `docker` without `sudo`. If you did not configure group access, prefix Docker commands with `sudo`.

---

# Part 4: Prepare a Docker feature branch

Open your project:

```bash
cd ~/devops-journey/01-linux-git
```

Synchronize `main`:

```bash
git switch main
git pull origin main
git status
```

Create a feature branch:

```bash
git switch -c feature/dockerize-website
```

Verify:

```bash
git branch --show-current
```

Expected:

```text
feature/dockerize-website
```

---

# Part 5: Write the Dockerfile

At the repository root, create a file named exactly `Dockerfile`:

```bash
nano Dockerfile
```

Add:

```dockerfile
FROM nginx:alpine

COPY website/ /usr/share/nginx/html/

EXPOSE 80
```

Save with **Ctrl+O**, press **Enter**, and exit with **Ctrl+X**.

## Understand each instruction

```dockerfile
FROM nginx:alpine
```

Uses the lightweight Alpine-based Nginx image as the starting point.

```dockerfile
COPY website/ /usr/share/nginx/html/
```

Copies your website into Nginx’s default document directory.

```dockerfile
EXPOSE 80
```

Documents that the application listens on TCP port 80.

> `EXPOSE` does not publish the port to your computer. Port publishing happens with `docker run -p`.

---

# Part 6: Create `.dockerignore`

Docker sends the build context to its build engine. Files that are not required should be excluded.

Create:

```bash
nano .dockerignore
```

Add:

```text
.git
.github
notes
README.md
```

Save and exit.

Check your new files:

```bash
git status
```

Expected untracked files:

```text
.dockerignore
Dockerfile
```

---

# Part 7: Build the Docker image

Run from the repository root:

```bash
docker build -t devops-journey:v1 .
```

Breakdown:

```text
docker build            Build an image
-t devops-journey:v1    Name and tag the image
.                       Use the current directory as the build context
```

List local images:

```bash
docker image ls
```

You should see an image named:

```text
devops-journey   v1
```

Inspect its build history:

```bash
docker image history devops-journey:v1
```

---

# Part 8: Run the website container

Make sure port 8080 is not already occupied:

```bash
sudo ss -lntp | grep ':8080' || echo "Port 8080 is available"
```

Run the container:

```bash
docker run -d \
  --name devops-site \
  -p 8080:80 \
  devops-journey:v1
```

Breakdown:

| Option | Meaning |
|---|---|
| `-d` | Run in the background |
| `--name devops-site` | Give the container a readable name |
| `-p 8080:80` | Map host port 8080 to container port 80 |
| `devops-journey:v1` | Image to run |

The port mapping is:

```text
Your computer                 Docker container
localhost:8080  ────────────► Nginx port 80
```

Check running containers:

```bash
docker ps
```

Test with `curl`:

```bash
curl -I http://localhost:8080
```

Expected status:

```text
HTTP/1.1 200 OK
```

Open in your browser:

```text
http://localhost:8080
```

---

# Part 9: Inspect the container

## View logs

```bash
docker logs devops-site
```

Refresh the website in your browser and run the logs command again:

```bash
docker logs devops-site
```

You should see an Nginx access-log entry for your browser request.

Follow logs in real time:

```bash
docker logs -f devops-site
```

Refresh the browser, observe the new request, and press **Ctrl+C** to stop following logs. This does not stop the container.

## Inspect configuration

```bash
docker inspect devops-site
```

The output is large JSON. Extract only useful fields:

```bash
docker inspect \
  --format 'Name={{.Name}} Image={{.Config.Image}} IP={{.NetworkSettings.IPAddress}}' \
  devops-site
```

## Run a command inside the container

```bash
docker exec devops-site nginx -v
```

List the website files inside the container:

```bash
docker exec devops-site ls -la /usr/share/nginx/html
```

Read the container’s operating-system information:

```bash
docker exec devops-site cat /etc/os-release
```

This demonstrates that the container has its own filesystem and userspace.

---

# Part 10: Understand container persistence

Create a temporary file inside the running container:

```bash
docker exec devops-site \
  sh -c 'echo "Created inside the running container" > /usr/share/nginx/html/temp.txt'
```

Request it:

```bash
curl http://localhost:8080/temp.txt
```

Expected:

```text
Created inside the running container
```

Now remove the container:

```bash
docker rm -f devops-site
```

Create a new container from the same image:

```bash
docker run -d \
  --name devops-site \
  -p 8080:80 \
  devops-journey:v1
```

Try requesting the temporary file again:

```bash
curl -i http://localhost:8080/temp.txt
```

You should receive `404 Not Found`.

## Why did it disappear?

The temporary change was made to one container, not to the original image. When that container was deleted, its writable layer was deleted too.

```text
Image: devops-journey:v1
         │
         ├── Container A + temp.txt → deleted
         │
         └── Container B → clean copy of original image
```

Persistent data should normally be stored in:

- Docker volumes
- Bind mounts
- Databases
- External storage such as AWS S3

---

# Part 11: Modify and rebuild the application

Edit the website:

```bash
nano website/index.html
```

Add this inside the `.container` element:

```html
<p>Version 2: Now running inside a Docker container.</p>
```

Save and exit.

Rebuild with a new tag:

```bash
docker build -t devops-journey:v2 .
```

List both versions:

```bash
docker image ls devops-journey
```

You should now have:

```text
devops-journey   v2
devops-journey   v1
```

The existing container still runs `v1`. Images do not automatically update running containers.

Replace it:

```bash
docker rm -f devops-site
```

Run version 2:

```bash
docker run -d \
  --name devops-site \
  -p 8080:80 \
  devops-journey:v2
```

Verify:

```bash
curl http://localhost:8080
docker inspect --format 'Image={{.Config.Image}}' devops-site
```

Expected image:

```text
Image=devops-journey:v2
```

---

# Part 12: Practise the container lifecycle

Stop the container:

```bash
docker stop devops-site
```

Check all containers, including stopped ones:

```bash
docker ps -a
```

Start it again:

```bash
docker start devops-site
```

Confirm it responds:

```bash
curl -I http://localhost:8080
```

Restart it:

```bash
docker restart devops-site
```

Remove it:

```bash
docker rm -f devops-site
```

Run it one final time with automatic cleanup when stopped:

```bash
docker run -d \
  --rm \
  --name devops-site \
  -p 8080:80 \
  devops-journey:v2
```

---

# Part 13: Commit and create a pull request

Review your changes:

```bash
git status
git diff
```

Stage them:

```bash
git add Dockerfile .dockerignore website/index.html
```

Inspect the staged change:

```bash
git diff --staged
```

Commit:

```bash
git commit -m "feat: containerize website with Nginx"
```

Push the branch:

```bash
git push -u origin feature/dockerize-website
```

Create a pull request:

```bash
gh pr create \
  --base main \
  --head feature/dockerize-website \
  --title "Containerize website with Docker" \
  --body "Adds an Nginx-based Docker image for serving the static website."
```

Open it:

```bash
gh pr view --web
```

Merge it after reviewing the changed files:

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

1. Build the image using a new tag:

   ```text
   devops-journey:challenge
   ```

2. Run it with:
   - Container name: `devops-challenge`
   - Host port: `8081`
   - Container port: `80`

3. Confirm that both endpoints work:

   ```text
   http://localhost:8080
   http://localhost:8081
   ```

4. View the challenge container’s logs.
5. Run `nginx -v` inside it.
6. Stop and remove only the challenge container.
7. Confirm the original `devops-site` container remains running.

---

# Final verification

Run:

```bash
docker version
docker compose version
docker image ls devops-journey
docker ps
curl -I http://localhost:8080
git status
git log --oneline --decorate -5
```

Success means:

- Docker Engine is running.
- The `devops-journey` images exist.
- `devops-site` is running.
- `http://localhost:8080` returns `200 OK`.
- The Docker files are committed to Git.
- The pull request is merged into `main`.

## Skills completed

You have now practised:

- Installing Docker Engine
- Understanding images versus containers
- Writing a `Dockerfile`
- Controlling the Docker build context
- Building and tagging images
- Publishing container ports
- Reading container logs
- Executing commands inside containers
- Understanding ephemeral container storage
- Rebuilding and replacing an application
- Managing the container lifecycle

**Next lab:** Docker Compose, bind mounts, container networking, health checks, and running a multi-container application.

Official references:

- [Install Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Docker Linux post-installation steps](https://docs.docker.com/engine/install/linux-postinstall/)