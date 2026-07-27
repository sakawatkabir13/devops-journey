# Linux Notes
whoami
pwd
uname -a
lsb_release -a
cat /etc/lsb-release

## Commands Learned
git --version
docker --version
nginx -v

mkdir -p ~/devops-journey/01-linux-git
cd ~/devops-journey/01-linux-git
pwd

mkdir website notes

find . -maxdepth 2 -type d

git config --global user.name
git config --global user.email
git config --global --list

git init
git branch -M main
git status
printf '# DevOps Journey\n\nMy hands-on DevOps and cloud learning project.\n' > README.md
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

find . -maxdepth 2 -type f
less website/index.html

Working directory → Staging area → Repository
      edit           git add       git commit

git add README.md website/index.html
git status
git diff --staged
git commit -m "feat: create initial DevOps project"
git log --oneline
printf '\n## Goal\n\nDeploy this website using Docker, Nginx, GitHub Actions and AWS.\n' >> README.md
git diff
git add README.md
git commit -m "docs: add project goal"
git log --oneline --decorate
