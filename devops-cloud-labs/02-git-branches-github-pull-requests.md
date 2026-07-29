# Practical Lab 2: Git Branches, GitHub, and Pull Requests

**Goal:** Publish your project to GitHub, develop a feature on a separate branch, and merge it through a pull request.

**Time:** Approximately 45–60 minutes.

## Concepts

- **Branch:** An independent line of development.
- **Remote:** A repository hosted somewhere else, such as GitHub.
- **Origin:** The conventional name for your main remote repository.
- **Push:** Upload local commits to a remote repository.
- **Pull:** Download and integrate remote changes.
- **Pull request (PR):** A request to review and merge one branch into another.

The workflow will be:

```text
main
  │
  └── feature/add-styling
           │
           ├── edit files
           ├── commit
           ├── push
           └── pull request → merge into main
```

---

## Part 1: Verify the previous lab

Open a terminal:

```bash
cd ~/devops-journey/01-linux-git
git status
git log --oneline --decorate
```

Before continuing, `git status` should report:

```text
nothing to commit, working tree clean
```

Confirm your branch is named `main`:

```bash
git branch --show-current
```

Expected:

```text
main
```

---

## Part 2: Install the GitHub CLI

Check whether the `gh` command is installed:

```bash
gh --version
```

If you get `command not found`, install it:

```bash
sudo apt update
sudo apt install gh
```

Verify the installation:

```bash
gh --version
```

> The GitHub CLI is not required for Git itself, but it makes repository and pull-request operations easier.

---

## Part 3: Authenticate with GitHub

You need a free account at [github.com](https://github.com).

Start authentication:

```bash
gh auth login
```

Choose:

```text
Where do you use GitHub?          GitHub.com
Preferred protocol?              HTTPS
Authenticate Git with GitHub?    Yes
How would you like to log in?     Login with a web browser
```

GitHub CLI will display a temporary code. Copy it, press Enter, and complete authentication in your browser.

Verify:

```bash
gh auth status
```

You should see that you are logged in to `github.com`.

> Never paste access tokens, passwords, AWS keys, or private SSH keys into your repository.

---

## Part 4: Create your GitHub repository

Make sure you are inside the project:

```bash
cd ~/devops-journey/01-linux-git
```

Create a public GitHub repository and push your existing commits:

```bash
gh repo create devops-journey \
  --public \
  --source=. \
  --remote=origin \
  --push
```

If you prefer a private repository, replace `--public` with `--private`.

Verify the remote:

```bash
git remote -v
```

Expected structure:

```text
origin  https://github.com/YOUR_USERNAME/devops-journey.git (fetch)
origin  https://github.com/YOUR_USERNAME/devops-journey.git (push)
```

Check branch tracking:

```bash
git branch -vv
```

Your `main` branch should track `origin/main`.

Open the repository in your browser:

```bash
gh repo view --web
```

---

# Part 5: Create a feature branch

Return to the terminal and create a branch:

```bash
git switch -c feature/add-styling
```

Verify:

```bash
git branch
```

Expected:

```text
* feature/add-styling
  main
```

The asterisk shows your current branch.

You can also run:

```bash
git status
```

---

## Part 6: Add CSS styling

Create a stylesheet:

```bash
nano website/styles.css
```

Enter:

```css
* {
    box-sizing: border-box;
}

body {
    margin: 0;
    min-height: 100vh;
    display: grid;
    place-items: center;
    font-family: Arial, sans-serif;
    background: #0f172a;
    color: #e2e8f0;
}

.container {
    width: min(90%, 700px);
    padding: 3rem;
    border: 1px solid #334155;
    border-radius: 1rem;
    background: #1e293b;
    text-align: center;
}

h1 {
    color: #38bdf8;
}

p {
    line-height: 1.6;
}
```

In Nano:

1. Press **Ctrl+O** to save.
2. Press **Enter** to confirm the filename.
3. Press **Ctrl+X** to exit.

Now edit the HTML:

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
            This website will run in Docker, behind Nginx,
            on an AWS EC2 server.
        </p>
    </main>
</body>
</html>
```

Save and exit Nano.

---

## Part 7: Inspect your changes

Check the repository:

```bash
git status
```

View changes to tracked files:

```bash
git diff
```

Notice:

- `website/index.html` appears in `git diff` because Git already tracks it.
- `website/styles.css` appears as untracked in `git status`.
- Untracked file contents are not normally displayed by `git diff`.

Stage both files:

```bash
git add website/index.html website/styles.css
```

Inspect the staged changes:

```bash
git diff --staged
```

Commit:

```bash
git commit -m "feat: add homepage styling"
```

Review the branch history:

```bash
git log --oneline --decorate --graph --all
```

---

## Part 8: Understand branch isolation

While on the feature branch, confirm the CSS file exists:

```bash
git branch --show-current
find website -maxdepth 1 -type f
```

Temporarily switch to `main`:

```bash
git switch main
```

Check the files again:

```bash
find website -maxdepth 1 -type f
```

The CSS file should not exist on `main` yet because the feature has not been merged.

Switch back:

```bash
git switch feature/add-styling
```

The CSS file should return:

```bash
find website -maxdepth 1 -type f
```

This demonstrates how branches keep work isolated.

---

## Part 9: Push the feature branch

Push the branch to GitHub:

```bash
git push -u origin feature/add-styling
```

The `-u` option establishes tracking between:

```text
Local:  feature/add-styling
Remote: origin/feature/add-styling
```

Verify:

```bash
git branch -vv
```

---

## Part 10: Create a pull request

Create the pull request from the terminal:

```bash
gh pr create \
  --base main \
  --head feature/add-styling \
  --title "Add homepage styling" \
  --body "Adds a responsive stylesheet and improves the homepage HTML structure."
```

View the PR:

```bash
gh pr view --web
```

On GitHub, examine these areas:

- **Conversation** — description and discussion
- **Commits** — commits included in the PR
- **Files changed** — exact code changes
- **Checks** — automated CI jobs; none exist yet

---

## Part 11: Merge the pull request

Return to the terminal and check the PR:

```bash
gh pr status
```

Merge it:

```bash
gh pr merge --merge --delete-branch
```

If prompted to confirm, accept.

Now synchronize your local `main` branch:

```bash
git switch main
git pull origin main
```

Verify the result:

```bash
git status
git log --oneline --decorate --graph --all
find website -maxdepth 1 -type f
```

You should now see:

```text
website/index.html
website/styles.css
```

---

# Part 12: Preview the website

You can preview the static website without Nginx.

Start a temporary Python web server:

```bash
cd ~/devops-journey/01-linux-git/website
python3 -m http.server 8000
```

Open this address in your browser:

```text
http://localhost:8000
```

Stop the server by returning to the terminal and pressing:

```text
Ctrl+C
```

This temporary server is only for local development. Later, Nginx will serve the website.

---

# Challenge

Complete this without following exact commands:

1. Create a branch named:

   ```text
   docs/add-learning-roadmap
   ```

2. Add a roadmap to `README.md` containing:
   - Linux
   - Git and GitHub
   - Docker
   - Nginx
   - GitHub Actions
   - AWS EC2
   - AWS S3

3. Commit it with:

   ```text
   docs: add learning roadmap
   ```

4. Push the branch.
5. Create a pull request.
6. Merge the pull request.
7. Update your local `main` branch.
8. Delete the local feature branch if it remains:

   ```bash
   git branch -d docs/add-learning-roadmap
   ```

---

# Final verification

Run:

```bash
cd ~/devops-journey/01-linux-git
git switch main
git status
git remote -v
git branch -a
git log --oneline --decorate --graph --all
gh pr list --state merged
```

Success means:

- Your working tree is clean.
- `origin` points to GitHub.
- The styling branch was merged.
- Your website contains `index.html` and `styles.css`.
- GitHub shows the merged pull request.
- Your local `main` matches `origin/main`.

## Skills completed

You have now practised:

- Creating and switching Git branches
- Keeping feature work separate from `main`
- Publishing a local repository to GitHub
- Configuring a Git remote
- Pushing branches
- Creating and reviewing pull requests
- Merging a PR
- Synchronizing your local repository

**Next lab:** Docker fundamentals—write a `Dockerfile`, build your first image, run the website in a container, inspect logs, and understand port mapping.