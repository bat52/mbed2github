#/usr/bin/env python3
"""
MBED → GITHUB MIGRATION TOOL
=============================

REQUIREMENTS
-------------

System:
- git
- python3

Python packages:
- requests
- beautifulsoup4
- rich

Install (Termux / Ubuntu / WSL):
    pkg install git python python-pip   (Termux)
    sudo apt install git python3 python3-pip (Ubuntu)

    pip install requests beautifulsoup4 rich

GitHub CLI (optional but used in this script):
- gh (GitHub CLI)

Install on Termux:
    pkg install gh
OR manual install:
    https://github.com/cli/cli/releases

Authentication:
    gh auth login

Git config (recommended):
    git config --global user.name "Your Name"
    git config --global user.email "you@email.com"


USAGE
-----

Dry run:
    python mbed_to_github.py --dry-run

Real migration:
    python mbed_to_github.py


NOTES
-----

- Uses --mirror to preserve full git history (branches + tags + refs)
- Automatically discovers repositories from:
    https://os.mbed.com/users/batman52/code/
- Safe retry on failed pushes
"""

import os
import argparse
import subprocess
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from rich.progress import track

MBED_USER = "batman52"
MBED_BASE = f"https://os.mbed.com/users/{MBED_USER}/code/"
GITHUB_USER = "YOUR_GITHUB_USERNAME"
WORKDIR = "./mbed_migration"

session = requests.Session()


# ---------------------------
# CLI
# ---------------------------
def parse_args():
    parser = argparse.ArgumentParser(description="Mbed → GitHub migration tool")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migration without cloning or pushing"
    )
    return parser.parse_args()


# ---------------------------
# Discovery
# ---------------------------
def get_repo_pages():
    repos = set()
    page = 1

    while True:
        url = f"{MBED_BASE}?page={page}"
        r = session.get(url)
        if r.status_code != 200:
            break

        soup = BeautifulSoup(r.text, "html.parser")

        found = False
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if "/users/" in href and "/code/" in href:
                full = urljoin("https://os.mbed.com", href.split("?")[0])
                repos.add(full)
                found = True

        if not found:
            break

        page += 1

    return sorted(repos)


def repo_name(url):
    return url.rstrip("/").split("/")[-1]


# ---------------------------
# Git helpers
# ---------------------------
def run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)


def git_push_verified(path, remote):
    code, _, err = run(["git", "push", "--mirror", remote], cwd=path)

    if code != 0:
        return False, err.strip()

    code2, out2, _ = run(["git", "ls-remote", remote])

    if code2 != 0 or not out2.strip():
        return False, "verification failed"

    return True, "ok"


# ---------------------------
# Migration
# ---------------------------
def migrate(repo_url, dry_run=False):
    name = repo_name(repo_url)
    path = os.path.join(WORKDIR, name)
    remote = f"git@github.com:{GITHUB_USER}/{name}.git"

    print(f"\n== {name} ==")

    if dry_run:
        print(f"[DRY RUN] clone {repo_url}")
        print(f"[DRY RUN] create GitHub repo {GITHUB_USER}/{name}")
        print(f"[DRY RUN] push to {remote}")
        return True, "dry-run"

    if not os.path.exists(path):
        code, _, err = run(["git", "clone", "--mirror", repo_url, path])
        if code != 0:
            return False, f"clone failed: {err.strip()}"

    subprocess.run([
        "gh", "repo", "create",
        f"{GITHUB_USER}/{name}",
        "--private",
        "--confirm"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return git_push_verified(path, remote)


# ---------------------------
# Main
# ---------------------------
def main():
    args = parse_args()
    dry_run = args.dry_run

    os.makedirs(WORKDIR, exist_ok=True)

    repos = get_repo_pages()
    print(f"Found {len(repos)} repositories")

    success, failed, dry = [], [], []

    for repo in track(repos, description="Migrating"):
        name = repo_name(repo)

        ok, msg = migrate(repo, dry_run=dry_run)

        if dry_run:
            dry.append(name)
        elif ok:
            success.append(name)
        else:
            failed.append((name, msg))
            print(f"Retrying {name}...")

            ok2, msg2 = migrate(repo, dry_run=False)
            if ok2:
                success.append(name)
            else:
                failed.append((name, msg2))

    print("\n=== SUMMARY ===")
    print(f"Total   : {len(repos)}")
    print(f"Success : {len(success)}")
    print(f"Failed  : {len(failed)}")
    print(f"Dry run : {len(dry)}")

    if failed:
        print("\nFAILED REPOS:")
        for n, e in failed:
            print(f"- {n}: {e}")


if __name__ == "__main__":
    main()
