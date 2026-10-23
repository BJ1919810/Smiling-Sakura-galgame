# -*- coding: utf-8 -*-
"""批量从云卡下载日配 wav（单 SSH 会话，比逐个调 cloud_sh.py 快得多）。

用法：
    python tools/cloud_fetch_voice.py --remote /root/autodl-tmp/index-tts-out/sakura_all \
        --local .workbuddy/backup/voice_raw [--only-complete]

★ `--only-complete`：先问远端「合成进程是否还在跑」，在跑就只下载已有的
  （避免下到写了一半的 wav）。
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from cloud_sh import connect, run  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--remote', default='/root/autodl-tmp/index-tts-out/sakura_all')
    ap.add_argument('--local', default=os.path.join(HERE, '..', '.workbuddy', 'backup', 'voice_raw'))
    ap.add_argument('--check-running', action='store_true', help='若合成进程仍在跑，报数量后仍继续下载')
    args = ap.parse_args()

    local_dir = os.path.abspath(args.local)
    os.makedirs(local_dir, exist_ok=True)

    cli = connect()
    try:
        out, _, _ = run(cli, f'ls {args.remote}/*.wav 2>/dev/null | wc -l')
        remote_count = int(out.strip() or 0)
        if args.check_running:
            out2, _, _ = run(cli, "pgrep -f 'index-tts-gen.py --ref' | wc -l")
            print(f'远端 wav {remote_count} 个；合成进程数（含 shell 自身，1=已停）: {out2.strip()}')
        print(f'远端 wav：{remote_count} 个')

        sftp = cli.open_sftp()
        remote_files = sorted(
            f for f in sftp.listdir(args.remote) if f.lower().endswith('.wav')
        )
        print(f'准备下载 {len(remote_files)} 个 → {local_dir}')

        done = 0
        skipped = 0
        for name in remote_files:
            rp = f'{args.remote}/{name}'
            lp = os.path.join(local_dir, name)
            try:
                rsize = sftp.stat(rp).st_size
            except OSError:
                continue
            if os.path.exists(lp) and os.path.getsize(lp) == rsize:
                skipped += 1
                continue
            sftp.get(rp, lp)
            done += 1
            if done % 25 == 0:
                print(f'  ... 已下载 {done}')
        sftp.close()
        print(f'完成：新下载 {done}，已存在跳过 {skipped}')
        total = sum(
            os.path.getsize(os.path.join(local_dir, f))
            for f in os.listdir(local_dir)
            if f.endswith('.wav')
        )
        print(f'本地 {len([f for f in os.listdir(local_dir) if f.endswith(".wav")])} 个，合计 {total / 1048576:.1f} MB')
    finally:
        cli.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
