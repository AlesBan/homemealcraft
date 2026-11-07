#!/usr/bin/env python3
"""Deploy HomeMealCraft bot to VPS (same SSH pattern as ticktick-agent)."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HOST = os.environ.get("GRIDLAB_DEPLOY_HOST", "ssh.ales-cloud.com")
USER = os.environ.get("GRIDLAB_DEPLOY_USER", "ales")
KEY = os.environ.get(
    "GRIDLAB_SSH_KEY",
    str(Path.home() / ".ssh" / "id_ed25519"),
)
CLOUDFLARED = os.environ.get("GRIDLAB_CLOUDFLARED", r"C:\tools\cloudflared.exe")
REMOTE_DIR = "/home/ales/homemealcraft"

SYNC_ITEMS = [
    "bot",
    "app",
    "docs",
    "requirements.txt",
    "deploy/homemealcraft-bot.service",
    ".env.example",
    "README.md",
]


def ssh_opts() -> list[str]:
    proxy = f"ProxyCommand={CLOUDFLARED} access ssh --hostname %h"
    return [
        "-o",
        "BatchMode=yes",
        "-o",
        proxy,
        "-i",
        KEY,
    ]


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-env", action="store_true", help="Do not upload local .env")
    args = parser.parse_args()

    remote = f"{USER}@{HOST}"
    opts = ssh_opts()

    with tempfile.TemporaryDirectory(prefix="homemealcraft-deploy-") as tmp:
        stage = Path(tmp) / "homemealcraft"
        stage.mkdir()
        for item in SYNC_ITEMS:
            src = ROOT / item
            dst = stage / item
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not src.exists():
                print(f"skip missing {item}")
                continue
            if src.is_dir():
                shutil.copytree(
                    src,
                    dst,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
            else:
                shutil.copy2(src, dst)

        (stage / "data").mkdir(exist_ok=True)
        (stage / "data" / ".gitkeep").write_text("", encoding="utf-8")

        if not args.skip_env and (ROOT / ".env").exists():
            shutil.copy2(ROOT / ".env", stage / ".env")

        if args.dry_run:
            print("Would upload:")
            for p in stage.rglob("*"):
                if p.is_file():
                    print(" ", p.relative_to(stage))
            return

        run(["ssh", *opts, remote, f"mkdir -p {Path(REMOTE_DIR).parent.as_posix()}"])
        run(
            [
                "scp",
                *opts,
                "-r",
                str(stage),
                f"{remote}:{Path(REMOTE_DIR).parent.as_posix()}/",
            ]
        )

        remote_cmd = (
            f"cd {REMOTE_DIR} && "
            "mkdir -p data && "
            "rm -rf .venv && python3 -m venv .venv && "
            ".venv/bin/pip install -q -U pip && "
            ".venv/bin/pip install -q -r requirements.txt && "
            "sudo cp deploy/homemealcraft-bot.service /etc/systemd/system/homemealcraft-bot.service && "
            "sudo systemctl daemon-reload && "
            "sudo systemctl enable --now homemealcraft-bot.service && "
            "sudo systemctl restart homemealcraft-bot.service && "
            "systemctl is-active homemealcraft-bot.service && "
            "sudo journalctl -u homemealcraft-bot.service -n 30 --no-pager"
        )
        run(["ssh", *opts, remote, remote_cmd])


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"Deploy failed: {exc}", file=sys.stderr)
        sys.exit(exc.returncode or 1)
