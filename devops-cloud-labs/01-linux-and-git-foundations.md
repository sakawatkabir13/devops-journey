# What each technology does

| Technology | Short description |
|---|---|
| **Git** | Tracks changes to source code. It lets you create versions, work on branches, collaborate, and restore earlier code. |
| **CI/CD** | Automatically tests, builds, and deploys code. **GitHub Actions** runs these workflows when you push code or open a pull request. |
| **Docker** | Packages an application and its dependencies into portable **containers**, so it behaves consistently across computers and servers. |
| **AWS EC2** | Virtual Linux servers in the AWS cloud. You can use EC2 to host websites, APIs, databases, and Docker containers. |
| **AWS S3** | Cloud object storage for files such as images, backups, logs, build artifacts, and static websites. |
| **Linux/Ubuntu** | The operating system commonly used on cloud servers. You need to understand files, permissions, processes, packages, networking, and services. |
| **Nginx** | A high-performance web server and reverse proxy. It can serve static sites, forward traffic to applications, provide HTTPS, and load-balance requests. |

## How they work together

```text
Developer
    │
    │ git push
    ▼
GitHub Repository
    │
    ▼
GitHub Actions
    │ test → build → Docker image → deploy
    ▼
AWS EC2 Ubuntu Server
    │
    ├── Nginx
    │      └── Dockerized application
    │
    └── S3 for files, artifacts and backups
```

# Your practical learning journey

We will build one connected project instead of studying isolated commands:

> **A Dockerized website deployed to an Ubuntu EC2 server, exposed through Nginx, automatically tested and deployed using GitHub Actions, with files or backups stored in S3.**

Recommended order:

1. **Linux foundations** — terminal, files, permissions, processes, networking
2. **Git fundamentals** — repositories, commits, branches, merging
3. **GitHub** — remote repositories, pull requests, collaboration
4. **Docker** — images, containers, volumes, networks, Compose
5. **Nginx** — static hosting and reverse proxying
6. **GitHub Actions** — automated testing and Docker builds
7. **AWS EC2** — launch and secure an Ubuntu server
8. **AWS S3** — buckets, permissions, uploads, backups
9. **Final CI/CD project** — automatically deploy from GitHub to EC2

---

# Practical Lab 1: Linux and Git foundations

**Goal:** Create your first DevOps project, track it with Git, and make two commits.

**Time:** Approximately 30–45 minutes.

Open your Ubuntu terminal.

## Step 1: Examine your Linux environment

Run each command separately:

```bash
whoami
pwd
uname -a
lsb_release -a
```

Meaning:

- `whoami` — shows your current user
- `pwd` — shows your current directory
- `uname -a` — shows kernel/system information
- `lsb_release -a` — shows the Ubuntu version

Now check the tools we will need:

```bash
git --version
docker --version
nginx -v
```

It is normal if Docker or Nginx is not installed yet. We will install them in later labs.

## Step 2: Create the course workspace

```bash
mkdir -p ~/devops-journey/01-linux-git
cd ~/devops-journey/01-linux-git
pwd
```

Expected final path:

```text
/home/YOUR_USERNAME/devops-journey/01-linux-git
```

Create some directories:

```bash
mkdir website notes
```

Display them:

```bash
find . -maxdepth 2 -type d
```

You should see something similar to:

```text
.
./website
./notes
```

## Step 3: Configure Git

Check whether Git already has your identity:

```bash
git config --global user.name
git config --global user.email
```

If either command returns nothing, configure it using your details:

```bash
git config --global user.name "Your Name"
git config --global user.email "your-email@example.com"
```

Verify:

```bash
git config --global --list
```

Your Git email should eventually match the email used for your GitHub account.

## Step 4: Initialize your first repository

Make sure you are in the lab directory:

```bash
cd ~/devops-journey/01-linux-git
```

Initialize Git:

```bash
git init
git branch -M main
```

Check the repository:

```bash
git status
```

You should see that you are on the `main` branch with no commits.

## Step 5: Create project files

Create a README:

```bash
printf '# DevOps Journey\n\nMy hands-on DevOps and cloud learning project.\n' > README.md
```

Create a simple webpage:

```bash
printf '%s\n' \
'<!doctype html>' \
'<html lang="en">' \
'<head>' \
'  <meta charset="UTF-8">' \
'  <title>DevOps Journey</title>' \
'</head>' \
'<body>' \
'  <h1>My DevOps Journey</h1>' \
'  <p>This website will eventually run in Docker on AWS.</p>' \
'</body>' \
'</html>' > website/index.html
```

Inspect the files:

```bash
find . -maxdepth 2 -type f
```

Read the webpage:

```bash
less website/index.html
```

Press `q` to exit `less`.

## Step 6: Understand Git’s three stages

Git uses this basic flow:

```text
Working directory → Staging area → Repository
      edit           git add       git commit
```

Check untracked files:

```bash
git status
```

Stage them:

```bash
git add README.md website/index.html
```

Check the difference between working and staged files:

```bash
git status
git diff --staged
```

Create your first commit:

```bash
git commit -m "feat: create initial DevOps project"
```

Verify it:

```bash
git log --oneline
```

You should see something similar to:

```text
a1b2c3d (HEAD -> main) feat: create initial DevOps project
```

Your commit identifier will be different.

## Step 7: Make a second change

Add a paragraph to the README:

```bash
printf '\n## Goal\n\nDeploy this website using Docker, Nginx, GitHub Actions and AWS.\n' >> README.md
```

See what changed:

```bash
git diff
```

Commit the change:

```bash
git add README.md
git commit -m "docs: add project goal"
```

Review your history:

```bash
git log --oneline --decorate
```

You should now have two commits.

## Step 8: Complete the challenge

Without copying exact commands from above:

1. Create `notes/linux.md`.
2. Add these headings:

```markdown
# Linux Notes

## Commands Learned

## Questions
```

3. Check the file with `git status`.
4. Stage it.
5. Commit it using this message:

```text
docs: add Linux learning notes
```

## Final verification

Run:

```bash
cd ~/devops-journey/01-linux-git
git status
git log --oneline --decorate
find . -maxdepth 2 -type f
```

Success means:

- `git status` says the working tree is clean.
- Git history contains **three commits**.
- The repository contains:
  - `README.md`
  - `website/index.html`
  - `notes/linux.md`

## Concepts you have now practised

- Navigating Linux directories
- Creating directories and files
- Reading file contents
- Initializing a Git repository
- Understanding working, staging, and committed states
- Creating meaningful commits
- Inspecting Git history

The next practical lab is **Git branches and GitHub**: create a feature branch, merge it into `main`, create a GitHub repository, and push the project.