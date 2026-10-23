# -*- coding: utf-8 -*-
"""云卡 SSH 小工具（笑樱项目用）——避免每次重写 paramiko 样板。

用法：
    python tools/cloud_sh.py "<远端 shell 命令>"
    python tools/cloud_sh.py --file local_script.sh

口令来源优先级：环境变量 COMFY_SSH_PASS → 同目录 cloud-comfy.config.json
"""
from __future__ import annotations

import json
import os
import sys

import paramiko

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "cloud-comfy.config.json")


def load_cfg() -> dict:
    cfg: dict = {}
    if os.path.exists(CFG_PATH):
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    return {
        "host": os.environ.get("COMFY_SSH_HOST") or cfg.get("ssh_host", ""),
        "port": int(os.environ.get("COMFY_SSH_PORT") or cfg.get("ssh_port", 22)),
        "user": os.environ.get("COMFY_SSH_USER") or cfg.get("ssh_user", "root"),
        "pass": os.environ.get("COMFY_SSH_PASS") or cfg.get("ssh_password", ""),
        "comfy_port": int(os.environ.get("COMFY_REMOTE_PORT") or cfg.get("remote_comfy_port", 6006)),
    }


def connect(cfg: dict | None = None) -> paramiko.SSHClient:
    cfg = cfg or load_cfg()
    if not cfg["host"] or not cfg["pass"]:
        sys.exit("[配置缺失] 需要 ssh_host / ssh_password（或设 COMFY_SSH_* 环境变量）")
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(cfg["host"], cfg["port"], cfg["user"], cfg["pass"],
                timeout=25, look_for_keys=False, allow_agent=False)
    return cli


def run(cli: paramiko.SSHClient, script: str, timeout: float = 600) -> tuple[str, str, int]:
    """执行一段脚本，返回 (stdout, stderr, exit_code)。

    ⚠️ 远端没有 syslog、无 sshpass；用绝对路径调 python。
    """
    stdin, stdout, stderr = cli.exec_command(script, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    return out, err, rc


def main() -> None:
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    if args[0] == "--put":
        # python tools/cloud_sh.py --put <本地文件> <远端路径>
        local, remote = args[1], args[2]
        cli = connect()
        try:
            sftp = cli.open_sftp()
            sftp.put(local, remote)
            st = sftp.stat(remote)
            print(f"[uploaded] {remote}  {st.st_size} bytes")
            sftp.close()
        finally:
            cli.close()
        return
    if args[0] == "--get":
        # python tools/cloud_sh.py --get <远端文件> <本地路径>
        remote, local = args[1], args[2]
        cli = connect()
        try:
            sftp = cli.open_sftp()
            sftp.get(remote, local)
            print(f"[downloaded] {local}  {os.path.getsize(local)} bytes")
            sftp.close()
        finally:
            cli.close()
        return
    if args[0] == "--file":
        with open(args[1], encoding="utf-8") as f:
            script = f.read()
    else:
        script = args[0]
    cli = connect()
    try:
        out, err, rc = run(cli, script)
        sys.stdout.write(out)
        if err:
            sys.stderr.write("\n[stderr]\n" + err)
        print(f"\n[exit={rc}]")
    finally:
        cli.close()


if __name__ == "__main__":
    main()
