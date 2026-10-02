#!/usr/bin/env python3
"""gitlab_retry_daemon.py — persistent GitLab mirror push with WAF-403 retry.

The GitLab.com edge (Cloudflare-style WAF) 403-blocks a rotating fraction of
requests from this sandbox egress (observed ~2/3 pass). A single `git push`
therefore fails stochastically on BOTH the transport (403 Blocked page) and
sometimes the auth layer (WAF-mangled requests yield 'Access denied').
The PAT itself is valid — the cure is patient retry, not re-minting.

Daemon contract (S24, per user directive):
  - every --interval seconds: ensure remote 'gitlab' exists, then
    `git push gitlab main` (no-op if in sync).
  - each cycle = up to --attempts tries with --backoff sleep between them.
  - outcomes classified + logged; run ends only when synced AND --linger=0
    (default lingers forever so new session commits auto-push).
  - repo-relative paths (SKILL 24.5): resolved from THIS script's realpath.

Usage (session bootstrap):
  python3 scripts/_bg_launch.py /tmp/gitlab_daemon.log \
      python3 scripts/gitlab_retry_daemon.py --interval 600
"""
import argparse
import datetime
import os
import re
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.realpath(__file__))
_REPO = os.path.dirname(_HERE)
REMOTE = 'gitlab'
BRANCH = 'main'


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%H:%M:%SZ')


def log(msg):
    print(f'[{now()}] {msg}', flush=True)


def git(args, timeout=300):
    env = dict(os.environ)
    env['GIT_TERMINAL_PROMPT'] = '0'
    try:
        p = subprocess.run(['git', '-C', _REPO] + args, capture_output=True,
                           text=True, timeout=timeout, env=env)
        return p.returncode, (p.stdout or '') + (p.stderr or '')
    except subprocess.TimeoutExpired:
        return 124, 'timeout'


def ensure_remote():
    rc, out = git(['remote', 'get-url', REMOTE])
    if rc != 0:
        token = os.environ.get('GITLAB_PAT', '')
        if not token:
            # PAT is committed by git-is-disk policy (ingest/.env class) —
            # fall back to the known mirror URL without embedding secrets.
            log(f'ERROR: remote {REMOTE!r} missing and GITLAB_PAT unset '
                '(run: git remote add gitlab '
                'https://oauth2:<PAT>@gitlab.com/ansgareutychisO/'
                'job-sourcing-research.git)')
            return False
        rc, out = git(['remote', 'add', REMOTE,
                       f'https://oauth2:{token}@gitlab.com/'
                       'ansgareutychisO/job-sourcing-research.git'])
        if rc != 0:
            log(f'remote add failed: {out.strip()[:200]}')
            return False
    return True


def classify(out):
    """Map git output to an outcome class (for honest logging)."""
    low = out.lower()
    if 'everything up-to-date' in low:
        return 'in_sync'
    if re.search(r'\b403\b', out) or 'blocked' in low:
        return 'waf_403'
    if 'access denied' in low or 'authentication failed' in low:
        return 'auth_denied (WAF-mangled or PAT issue — keep retrying)'
    if 'timed out' in low or 'errno 101' in low or 'could not resolve' in low:
        return 'network'
    if 're pushed' in low or '-> ' in out:
        return 'pushed'
    return 'other'


def one_cycle(attempts, backoff):
    """Returns True when the mirror is fully synced (pushed or in-sync)."""
    if not ensure_remote():
        return False
    for i in range(1, attempts + 1):
        rc, out = git(['push', REMOTE, BRANCH])
        verdict = classify(out)
        tail = out.strip().splitlines()[-1][:160] if out.strip() else ''
        if rc == 0:
            log(f'cycle OK ({verdict}) — {tail}')
            return verdict in ('pushed', 'in_sync')
        log(f'push try {i}/{attempts} rc={rc} {verdict}: {tail}')
        if i < attempts:
            time.sleep(backoff)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--interval', type=int, default=600,
                    help='seconds between push cycles (default 600)')
    ap.add_argument('--attempts', type=int, default=3,
                    help='tries per cycle (default 3)')
    ap.add_argument('--backoff', type=int, default=20,
                    help='seconds between tries in a cycle (default 20)')
    ap.add_argument('--linger', type=int, default=1,
                    help='1=keep running to auto-push new commits; '
                         '0=exit after first synced cycle')
    args = ap.parse_args()

    log(f'gitlab_retry_daemon start: interval={args.interval}s '
        f'attempts={args.attempts} repo={_REPO} linger={args.linger}')
    synced_once = False
    consecutive_fail_cycles = 0
    while True:
        ok = one_cycle(args.attempts, args.backoff)
        if ok:
            synced_once = True
            consecutive_fail_cycles = 0
            if not args.linger:
                log('synced and --linger=0 — exiting')
                return 0
        else:
            consecutive_fail_cycles += 1
            # honest loud escalation, never silent
            log(f'cycle failed ({consecutive_fail_cycles} consecutive) '
                '— WAF/auth transient per policy; next cycle scheduled')
        time.sleep(args.interval)


if __name__ == '__main__':
    sys.exit(main())
