#!/usr/bin/env python3
"""Background retry-creator for the GitLab mirror project. Double-forked so it
survives the bash toolcall that launches it. Writes outcome to a status file.
"""
import os, sys, time, json, pathlib, urllib.request, urllib.error

LOG = "/home/z/agent-kit/.gitlab-creation.log"


def _env(*names):
    """os.environ first, then kit .env, then the repo secret store ingest/.env
    (S8-A scrub — the PAT is never hardcoded)."""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    here = pathlib.Path(__file__).resolve()
    for p in (here.parents[1] / ".env", here.parents[3] / "ingest" / ".env"):
        try:
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() in names and v.strip():
                    return v.strip().strip("'\"")
        except OSError:
            pass
    return ""


GL_PAT = _env("GITLAB_PAT")

def daemonize():
    if os.fork(): os._exit(0)
    os.setsid()
    if os.fork(): os._exit(0)
    sys.stdout.flush(); sys.stderr.flush()
    devnull = os.open('/dev/null', os.O_RDWR)
    os.dup2(devnull, 0); os.dup2(devnull, 1); os.dup2(devnull, 2)

def attempt():
    data = (b"name=agent-fetch-kit&visibility=private&"
            b"description=Resilient+web+fetch+agent+kit+(mirror+of+github.com/zmytone/agent-fetch-kit)")
    req = urllib.request.Request(
        "https://gitlab.com/api/v4/projects",
        data=data,
        headers={"PRIVATE-TOKEN": GL_PAT,
                 "Content-Type": "application/x-www-form-urlencoded"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")
    except Exception as e:
        return 0, repr(e)

def lookup():
    req = urllib.request.Request(
        "https://gitlab.com/api/v4/projects?owned=true&search=agent-fetch-kit",
        headers={"PRIVATE-TOKEN": GL_PAT})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            arr = json.loads(r.read().decode("utf-8","ignore"))
            return arr[0] if arr else None
    except Exception as e:
        return {"error": repr(e)}

def main():
    daemonize()
    log = open(LOG, "a", buffering=1)
    log.write(f"\n[{time.strftime('%H:%M:%S')}] gitlab mirror creator started pid={os.getpid()}\n")
    for i in range(1, 31):  # 30 attempts ~ 15 min
        code, body = attempt()
        log.write(f"[{time.strftime('%H:%M:%S')}] attempt {i}: HTTP {code}\n")
        if code == 201:
            try:
                j = json.loads(body)
                log.write(f"  CREATED id={j.get('id')} url={j.get('http_url_to_repo')}\n  DONE\n")
            except Exception:
                log.write(f"  body: {body[:300]}\n  DONE\n")
            return
        if code == 422 and "has already been taken" in body:
            log.write("  project exists; looking it up...\n")
            p = lookup()
            log.write(f"  lookup: {json.dumps(p)[:300]}\n  DONE\n")
            return
        if code == 403:
            # WAF block — retry
            pass
        else:
            log.write(f"  body: {body[:300]}\n")
        time.sleep(30)
    log.write("  EXHAUSTED retries (will need manual gitlab project creation)\n")

if __name__ == "__main__":
    main()
