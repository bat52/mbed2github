#!/usr/bin/env python3
"""
MBED → GITHUB MIGRATION TOOL
=============================

REQUIREMENTS
-------------

System:
- git
- python3
- mercurial (hg)

Python packages:
- requests
- beautifulsoup4
- rich

Install (Termux / Ubuntu / WSL):
    pkg install git python python-pip   (Termux)
    sudo apt install git python3 python3-pip (Ubuntu)

    pip install requests beautifulsoup4 rich mercurial

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
    python mbed-xport.py --dry-run

Real migration:
    python mbed-xport.py


NOTES
-----

- Uses Mercurial (hg) to clone from os.mbed.com since these repos are
  Mercurial-based, then converts to Git via hg-fast-export to preserve
  full commit history.
- Automatically discovers repositories from:
    https://os.mbed.com/users/batman52/code/
- Safe retry on failed pushes
"""

import os
import sys
import argparse
import subprocess
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from rich.progress import track

WORKDIR = os.path.abspath("./mbed_migration")
FAST_EXPORT_DIR = os.path.join(WORKDIR, ".fast-export")

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
    parser.add_argument(
        "--mbed-user",
        default="batman52",
        help="MBED user to migrate from (default: batman52)"
    )
    parser.add_argument(
        "--github-user",
        default="bat52",
        help="GitHub user to migrate to (default: bat52)"
    )
    return parser.parse_args()


# ---------------------------
# Discovery
# ---------------------------
def get_repo_pages(mbed_base):
    repos = set()
    page = 1

    while True:
        url = f"{mbed_base}?page={page}"
        r = session.get(url)
        if r.status_code != 200:
            break

        soup = BeautifulSoup(r.text, "html.parser")

        found = False
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            # Match only actual repo links: /users/<user>/code/<repo-name>/
            # Exclude bare /code/, /code/?sort=..., and /account/login/...
            if "/users/" in href and "/code/" in href:
                # Extract path after /code/ to distinguish repos from nav links
                after_code = href.split("/code/", 1)[1].split("?")[0].strip("/")
                if after_code and "/" not in after_code:
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
# Shell helpers
# ---------------------------
def run(cmd, cwd=None, check=False):
    """Run a command and return CompletedProcess. If check=True, raise on error."""
    r = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(
            f"Command {' '.join(cmd)!r} failed (exit {r.returncode}):\n"
            f"  stdout: {r.stdout.strip()}\n"
            f"  stderr: {r.stderr.strip()}"
        )
    return r


# ---------------------------
# Mercurial helpers
# ---------------------------
def hg_clone(repo_url, dest_path):
    """Clone a Mercurial repo via HTTP (mbed uses Mercurial, not Git)."""
    r = run(["hg", "clone", repo_url, dest_path])
    if r.returncode != 0:
        return False, f"hg clone failed: {r.stderr.strip()}"
    return True, "ok"


def hg_to_git(hg_path, git_bare_path):
    """
    Convert an Hg repository to a Git bare repository using hg-fast-export.
    Returns (success, message).
    """
    # Create empty bare git repo
    r = run(["git", "init", "--bare", git_bare_path])
    if r.returncode != 0:
        return False, f"git init --bare failed: {r.stderr.strip()}"

    # Run hg-fast-export from the fast-export tool set
    fast_export_sh = os.path.join(FAST_EXPORT_DIR, "hg-fast-export.sh")
    r = run(
        [fast_export_sh, "-r", hg_path],
        cwd=git_bare_path
    )
    if r.returncode != 0:
        return False, f"hg-fast-export failed: {r.stderr.strip()}"

    # Update the default branch (hg-fast-export creates 'master' bookmark)
    run(["git", "branch", "-m", "master"], cwd=git_bare_path, check=False)

    return True, "ok"


def ensure_fast_export():
    """Clone or update the fast-export tool repository."""
    if os.path.exists(FAST_EXPORT_DIR):
        # Already present; do a quick fetch to stay current
        run(["git", "pull"], cwd=FAST_EXPORT_DIR, check=False)
        return True
    r = run([
        "git", "clone",
        "https://github.com/frej/fast-export.git",
        FAST_EXPORT_DIR
    ])
    if r.returncode != 0:
        return False
    return True


# ---------------------------
# Git helpers
# ---------------------------
def git_push_verified(path, remote):
    r = run(["git", "push", "--mirror", remote], cwd=path)

    if r.returncode != 0:
        return False, r.stderr.strip()

    r2 = run(["git", "ls-remote", remote])

    if r2.returncode != 0 or not r2.stdout.strip():
        return False, "verification failed"

    return True, "ok"


# ---------------------------
# Migration
# ---------------------------
def migrate(repo_url, github_user, dry_run=False):
    name = repo_name(repo_url)
    hg_url = repo_url.replace("https://", "http://")  # mbed works via http for hg
    hg_path = os.path.join(WORKDIR, name)
    git_bare_path = os.path.join(WORKDIR, f"{name}.git")
    remote = f"git@github.com:{github_user}/{name}.git"

    print(f"\n== {name} ==")

    if dry_run:
        print(f"[DRY RUN] hg clone {hg_url}")
        print(f"[DRY RUN] convert to git via hg-fast-export")
        print(f"[DRY RUN] create GitHub repo {github_user}/{name}")
        print(f"[DRY RUN] push to {remote}")
        return True, "dry-run"

    # Step 1: Clone Mercurial repo (if not already done)
    if not os.path.exists(hg_path):
        ok, msg = hg_clone(hg_url, hg_path)
        if not ok:
            return False, msg
    else:
        print("  (hg clone already exists, skipping)")

    # Step 2: Convert Hg to Git bare repository
    if not os.path.exists(git_bare_path):
        ok, msg = hg_to_git(hg_path, git_bare_path)
        if not ok:
            return False, msg
    else:
        print("  (git bare repo already exists, skipping)")

    # Step 3: Create GitHub repository
    subprocess.run([
        "gh", "repo", "create",
        f"{github_user}/{name}",
        "--private",
        "--confirm"
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Step 4: Push to GitHub
    return git_push_verified(git_bare_path, remote)


# ---------------------------
# Main
# ---------------------------
def main():
    args = parse_args()
    dry_run = args.dry_run
    mbed_user = args.mbed_user
    mbed_base = f"https://os.mbed.com/users/{mbed_user}/code/"
    github_user = args.github_user

    os.makedirs(WORKDIR, exist_ok=True)

    # Ensure fast-export tool is available
    if not dry_run:
        print("Setting up hg-fast-export...")
        if not ensure_fast_export():
            print("ERROR: failed to clone fast-export repository")
            sys.exit(1)

    repos = get_repo_pages(mbed_base)
    print(f"Found {len(repos)} repositories")

    success, failed, dry = [], [], []

    for repo in track(repos, description="Migrating"):
        name = repo_name(repo)

        ok, msg = migrate(repo, github_user, dry_run=dry_run)

        if dry_run:
            dry.append(name)
        elif ok:
            success.append(name)
        else:
            failed.append((name, msg))
            print(f"Retrying {name}...")

            ok2, msg2 = migrate(repo, github_user, dry_run=False)
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
