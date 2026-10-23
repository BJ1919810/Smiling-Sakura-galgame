# -*- coding: utf-8 -*-
"""GitHub Release 发布：创建 release + 上传发行资产。

为什么是独立脚本：本机没装 `gh` CLI，发行包 140–230 MB 也不适合走网页手拖。
这里用 GitHub REST API + curl（curl 支持大文件流式上传与代理，Python 标准库不带 requests）。

★ token 从哪来：`git credential fill`（GCM 里已存的 GitHub OAuth token）。
  ⛔ 不落盘、不打印、不进命令行 —— 写进临时 header 文件交给 curl `-H @file`，用完立即删。
  ⇒ 进程列表 / 终端历史里都看不到 token。

用法：
    python tools/gh-release.py --tag v0.2.0 --title "笑樱 v0.2.0 测试版" \
        --notes tools/release-notes-v0.2.0.md \
        笑樱-测试版.exe 笑樱-测试版.apk

    python tools/gh-release.py --tag v0.2.0 --list          # 只看现有 release 与资产

前置：本机代理已配在 git config（`http.https://github.com.proxy`），本脚本直接读它。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CURL = 'curl'


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                          errors='replace', **kw)


def git(*args):
    r = run(['git', *args], cwd=ROOT)
    if r.returncode != 0:
        sys.exit(f'[x] git {" ".join(args)} 失败：{r.stderr.strip()}')
    return r.stdout.strip()


def repo_slug() -> str:
    """从 origin URL 解析 owner/repo（支持 https 与 ssh 两种写法）。"""
    url = git('remote', 'get-url', 'origin')
    url = url.removesuffix('.git')
    if url.startswith('git@'):
        slug = url.split(':', 1)[1]
    else:
        parts = [p for p in urllib.parse.urlparse(url).path.split('/') if p]
        slug = '/'.join(parts[-2:])
    if slug.count('/') != 1:
        sys.exit(f'[x] 无法从 origin 解析 owner/repo：{url}')
    return slug


def proxy_args() -> list:
    """沿用 git 为 github 配的代理，避免 API 走直连超时。"""
    r = run(['git', 'config', '--get', 'http.https://github.com.proxy'], cwd=ROOT)
    p = r.stdout.strip()
    return ['-x', p] if p else []


def get_token() -> str:
    """向 git 凭据助手要 token（GCM 会返回已存的 OAuth token）。"""
    r = run(['git', 'credential', 'fill'], cwd=ROOT,
            input='protocol=https\nhost=github.com\n\n')
    for line in r.stdout.splitlines():
        if line.startswith('password='):
            tok = line[len('password='):].strip()
            if tok:
                return tok
    sys.exit('[x] 取不到 GitHub 凭据。先在终端 push 一次，让 GCM 登录。')


class Gh:
    """薄封装：所有请求都走 header 文件，token 不出现在命令行里。"""

    def __init__(self, token: str):
        fd, self.hdr = tempfile.mkstemp(prefix='ghhdr_', suffix='.txt')
        os.close(fd)
        os.chmod(self.hdr, 0o600)
        with open(self.hdr, 'w', encoding='utf-8', newline='\n') as f:
            f.write(f'Authorization: Bearer {token}\n')
            f.write('Accept: application/vnd.github+json\n')
            f.write('X-GitHub-Api-Version: 2022-11-28\n')
            f.write('User-Agent: xiaoying-release-script\n')
        self.proxy = proxy_args()

    def close(self):
        try:
            os.remove(self.hdr)
        except OSError:
            pass

    def _curl(self, url, extra, timeout=60):
        cmd = [CURL, '-sS', '--max-time', str(timeout), '-H', f'@{self.hdr}',
               *self.proxy, *extra, url]
        r = run(cmd)
        if r.returncode != 0:
            sys.exit(f'[x] curl 失败（{r.returncode}）：{r.stderr.strip()[:400]}')
        return r.stdout

    def api(self, path, method='GET', body=None, timeout=60):
        url = f'https://api.github.com{path}'
        extra = ['-X', method]
        if body is not None:
            fd, tmp = tempfile.mkstemp(prefix='ghbody_', suffix='.json')
            os.close(fd)
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(body, f, ensure_ascii=False)
            extra += ['-H', 'Content-Type: application/json', '--data-binary', f'@{tmp}']
            try:
                raw = self._curl(url, extra, timeout)
            finally:
                os.remove(tmp)
        else:
            raw = self._curl(url, extra, timeout)
        try:
            return json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            sys.exit(f'[x] 返回不是 JSON：{raw[:300]}')

    def upload(self, upload_url: str, path: str, name: str, timeout=3600):
        q = urllib.parse.urlencode({'name': name})
        url = upload_url.split('{')[0] + '?' + q
        extra = ['-X', 'POST',
                 '-H', 'Content-Type: application/octet-stream',
                 '--data-binary', f'@{path}']
        return self._curl(url, extra, timeout)


def human(n: int) -> str:
    return f'{n / 1048576:.1f} MB'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', required=True)
    ap.add_argument('--title', default=None)
    ap.add_argument('--notes', default=None, help='markdown 文件路径')
    ap.add_argument('--draft', action='store_true')
    ap.add_argument('--prerelease', action='store_true', default=True)
    ap.add_argument('--list', action='store_true')
    ap.add_argument('assets', nargs='*')
    a = ap.parse_args()

    slug = repo_slug()
    print(f'仓库：{slug}')
    gh = Gh(get_token())
    try:
        if a.list:
            for rel in gh.api(f'/repos/{slug}/releases'):
                print(f"  {rel['tag_name']}  {rel['name']}  "
                      f"({len(rel.get('assets', []))} 个资产)")
                for asst in rel.get('assets', []):
                    print(f"     - {asst['name']}  {human(asst['size'])}")
            return

        # ── 1. 建/复用 release ─────────────────────────────
        body = {'tag_name': a.tag, 'name': a.title or a.tag,
                'draft': a.draft, 'prerelease': a.prerelease}
        if a.notes and os.path.exists(a.notes):
            body['body'] = open(a.notes, encoding='utf-8').read()
        existing = None
        for rel in gh.api(f'/repos/{slug}/releases'):
            if rel['tag_name'] == a.tag:
                existing = rel
                break
        if existing:
            print(f'[1/2] 复用已有 release {a.tag}（id={existing["id"]}）')
            rel = gh.api(f'/repos/{slug}/releases/{existing["id"]}', 'PATCH', body)
        else:
            print(f'[1/2] 创建 release {a.tag}')
            rel = gh.api(f'/repos/{slug}/releases', 'POST', body)
        if 'id' not in rel:
            sys.exit(f'[x] release 创建失败：{rel}')
        print(f'      {rel["html_url"]}')

        # ── 2. 上传资产（同名先删，避免 422）───────────────
        for path in a.assets:
            if not os.path.exists(path):
                sys.exit(f'[x] 文件不存在：{path}')
            name = os.path.basename(path)
            size = os.path.getsize(path)
            for asst in rel.get('assets', []):
                if asst['name'] == name:
                    print(f'[2/2] 删除同名旧资产 {name}')
                    gh.api(f'/repos/{slug}/releases/assets/{asst["id"]}', 'DELETE')
            print(f'[2/2] 上传 {name}  {human(size)} …')
            t0 = time.time()
            res = gh.upload(rel['upload_url'], path, name)
            try:
                j = json.loads(res)
            except json.JSONDecodeError:
                sys.exit(f'[x] 上传返回异常：{res[:300]}')
            if 'browser_download_url' not in j:
                sys.exit(f'[x] 上传失败：{j}')
            print(f'      ok  {time.time() - t0:.0f} s  {j["browser_download_url"]}')
        print('\n完成。')
    finally:
        gh.close()


if __name__ == '__main__':
    main()
