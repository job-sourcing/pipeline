#!/usr/bin/env python3
"""wfetch — universal resilient web fetcher with auto-escalation.

Usage:
  wfetch URL [--mode auto|local|supabase|netlify|firecrawl|zenrows|gha|browser]
            [--render] [--antibot] [--region EU] [--extract css] [--timeout 30]
            [--impersonate chrome131] [--wait-ms 4000] [--out file] [--json] [--verbose]
  wfetch probe                     # local egress + ladder health snapshot
  wfetch gha URL [--mode impersonate|browser|curl]   # GHA remote compute shortcut

Escalation ladder (auto):
  plain:     local → supabase → netlify → firecrawl → zenrows → gha
  --render:  browser → firecrawl → zenrows → gha
  --antibot: zenrows → firecrawl → gha  (takes precedence over --render)

Note: if both --json and --out are given, the body is written to --out AND JSON metadata
is printed to stdout (useful for agents that want both).
"""
from __future__ import annotations
import sys, json, argparse
from .config import Config
from .core import fetch, probe
from .history import History

__version__ = "1.0.0"

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wfetch", description="resilient web fetcher with auto-escalation",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--version", action="version", version=f"wfetch {__version__}")
    sub = p.add_subparsers(dest="cmd")

    # fetch (default)
    f = sub.add_parser("fetch", help="fetch a URL with auto-escalation",
                       formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    f.add_argument("url")
    f.add_argument("--mode", default="auto", choices=["auto","local","supabase","netlify","firecrawl","zenrows","gha","browser"],
                   help="force a backend (default: auto-escalate)")
    f.add_argument("--render", action="store_true", help="JS rendering needed")
    f.add_argument("--antibot", action="store_true", help="hard anti-bot target (CF/DataDome/Akamai); takes precedence over --render")
    f.add_argument("--region", help="geo-pin (supabase x-region like 'eu-west-1' or zenrows 2-letter country like 'US')")
    f.add_argument("--extract", help="CSS selector to extract (supabase distill)")
    f.add_argument("--no-markdown", action="store_true", help="prefer raw HTML (default: markdown for zenrows/firecrawl)")
    f.add_argument("--timeout", type=int, default=30, help="per-backend timeout in seconds")
    f.add_argument("--impersonate", default="chrome131", help="curl_cffi impersonate target")
    f.add_argument("--wait-ms", type=int, default=4000, help="browser/JS wait in ms")
    f.add_argument("--out", help="write body to file")
    f.add_argument("--json", action="store_true", help="output JSON metadata to stdout instead of body")
    f.add_argument("--verbose", action="store_true", help="print each attempt to stderr")

    # probe
    pr = sub.add_parser("probe", help="local egress + ladder health snapshot (includes unconfigured backends with reason)")

    # gha
    g = sub.add_parser("gha", help="GHA remote compute shortcut (Azure IP + curl_cffi/browser)",
                       formatter_class=argparse.RawDescriptionHelpFormatter, epilog=
                       "Modes: curl=plain HTTP | impersonate=curl_cffi chrome131 (default) | browser=system Chrome headless")
    g.add_argument("url")
    g.add_argument("--mode", default="impersonate", choices=["curl","impersonate","browser"],
                   help="curl=plain HTTP | impersonate=curl_cffi chrome131 (default) | browser=system Chrome headless")
    g.add_argument("--wait-ms", type=int, default=4000)
    g.add_argument("--timeout", type=int, default=60)
    g.add_argument("--out", help="write body to file")
    g.add_argument("--json", action="store_true", help="output JSON metadata to stdout")
    g.add_argument("--verbose", action="store_true", help="print progress to stderr")

    return p

def _print_failure(prefix: str, r) -> None:
    """Print a human-readable failure line to stderr (so non-JSON/non-out failures aren't silent)."""
    print(f"[{prefix}] FAILED backend={r.backend} status={r.status} "
          f"error={r.error or '(none)'} challenged={r.challenged} reason={r.challenge_reason}",
          file=sys.stderr)

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Handle top-level --version / -h / --help WITHOUT prepending "fetch" (so they hit the top parser)
    if argv and argv[0] in ("--version", "-V"):
        print(f"wfetch {__version__}")
        return 0
    # Default to "fetch" subcommand if no subcommand token is present.
    # Handles both URL-first (`wfetch URL --json`) and flag-first (`wfetch --json URL`) arg orders.
    SUBCMDS = {"fetch","probe","gha"}
    has_subcmd = any(a in SUBCMDS for a in argv)
    if not has_subcmd and argv and argv[0] != "-h" and argv[0] != "--help":
        argv = ["fetch"] + argv
    elif not argv:
        argv = ["--help"]

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "probe":
        cfg = Config()
        out = probe(cfg)
        print(json.dumps(out, indent=2, default=str))
        # non-zero exit if local_egress failed OR zero backends succeeded
        local_ok = "error" not in out.get("local_egress", {})
        successful = sum(1 for v in out.get("backends", {}).values()
                         if isinstance(v, dict) and v.get("status") == 200 and not v.get("error"))
        return 0 if (local_ok and successful > 0) else 1

    if args.cmd == "gha":
        cfg = Config()
        from .backends import backend_gha
        if args.verbose:
            print(f"[gha] dispatching workflow (mode={args.mode}, timeout={args.timeout}s)...", file=sys.stderr)
        r = backend_gha(args.url, cfg, mode=args.mode, wait_ms=args.wait_ms, timeout=args.timeout)
        if args.verbose:
            print(f"[gha] done: status={r.status} elapsed={r.elapsed_ms}ms error={r.error}", file=sys.stderr)
        if args.json:
            print(json.dumps({"backend":r.backend,"status":r.status,"elapsed_ms":r.elapsed_ms,
                              "error":r.error,"metadata":r.metadata,"challenged":r.challenged,
                              "bytes":len(r.body)}, indent=2, default=str))
        elif args.out:
            with open(args.out, "wb") as f: f.write(r.body)
            print(f"[gha] {'OK' if r.ok else 'FAILED'} backend={r.backend} status={r.status} bytes={len(r.body)} → {args.out}", file=sys.stderr)
        else:
            if not r.ok: _print_failure("gha", r)
            sys.stdout.buffer.write(r.body)
        return 0 if r.ok else 1

    if args.cmd == "fetch":
        cfg = Config()
        hist = History(cfg.history_path)
        r = fetch(args.url, mode=args.mode, render=args.render, antibot=args.antibot,
                  region=args.region, extract=args.extract, markdown=not args.no_markdown,
                  timeout=args.timeout, impersonate=args.impersonate, wait_ms=args.wait_ms,
                  config=cfg, history=hist, verbose=args.verbose)
        if args.json:
            print(json.dumps({"url":r.url,"backend":r.backend,"status":r.status,
                              "challenged":r.challenged,"challenge_reason":r.challenge_reason,
                              "elapsed_ms":r.elapsed_ms,"error":r.error,"bytes":len(r.body),
                              "attempts":r.attempts,"metadata":r.metadata}, indent=2, default=str))
            if args.out:
                with open(args.out, "wb") as f: f.write(r.body)
                print(f"[wfetch] {'OK' if r.ok else 'FAILED'} wrote {len(r.body)} bytes to {args.out}", file=sys.stderr)
        elif args.out:
            with open(args.out, "wb") as f: f.write(r.body)
            print(f"[wfetch] {'OK' if r.ok else 'FAILED'} backend={r.backend} status={r.status} bytes={len(r.body)} → {args.out}", file=sys.stderr)
        else:
            if not r.ok: _print_failure("wfetch", r)
            sys.stdout.buffer.write(r.body)
        return 0 if r.ok else 1

    return 1

if __name__ == "__main__":
    sys.exit(main())
