#!/usr/bin/env python3
"""Local stealth browser ladder — clean version with `with` context managers.
Tests: vanilla playwright, playwright+stealth-init, patchright (stealth-maxxing).
Targets: bot.sannysoft.com (detection table) + nowsecure.nl (CF challenge).
Writes docs/results/browser.md incrementally.
"""
from __future__ import annotations
import json, time, pathlib, re

REPO = pathlib.Path("/home/z/agent-kit")
OUT  = REPO / "docs" / "results" / "browser"; OUT.mkdir(parents=True, exist_ok=True)
CHROME = "/home/z/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome"
SECTIONS: list[str] = []
def flush(): (REPO/"docs"/"results"/"browser.md").write_text("\n".join(SECTIONS))
def add(*lines): SECTIONS.extend(lines); SECTIONS.append("")

SECTIONS += [f"# Local stealth browser ladder", "", f"_captured: {time.strftime('%H:%M:%S UTC')}_", ""]

def sannysoft_summary(page):
    try:
        rows = page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('tr').forEach(tr => {
                const tds = tr.querySelectorAll('td');
                if (tds.length >= 2) {
                    out.push({
                        label: (tds[0].innerText||tds[0].textContent||'').trim(),
                        val: (tds[1].innerText||tds[1].textContent||'').trim(),
                        bg: (tds[1].style&&tds[1].style.backgroundColor)||getComputedStyle(tds[1]).backgroundColor
                    });
                }
            });
            return out;
        }""")
        def classify(bg):
            if not bg: return "?"
            bgl = bg.lower()
            if any(c in bgl for c in ["green","rgb(0, 128","rgb(0,128","rgb(34, 197","#008000","#00ff00","lightgreen","rgb(144, 238"]): return "PASS"
            if any(c in bgl for c in ["red","rgb(255, 0","rgb(255,0","rgb(220","rgb(239, 68","#ff0000","#dc3545","salmon"]): return "FAIL"
            return "?"
        for r in rows: r["verdict"] = classify(r.get("bg"))
        passes = sum(1 for r in rows if r["verdict"]=="PASS")
        fails = sum(1 for r in rows if r["verdict"]=="FAIL")
        unknown = sum(1 for r in rows if r["verdict"]=="?")
        return {"passes":passes, "fails":fails, "unknown":unknown, "rows":rows}
    except Exception as e:
        return {"error": repr(e)}

def run_test(label, importer, stealth_init=False, headless=True):
    add(f"## {label}")
    add(f"_headless={headless}, stealth_init={stealth_init}_")
    try:
        with importer() as pw:
            args = ["--no-sandbox","--disable-dev-shm-usage","--disable-blink-features=AutomationControlled","--disable-gpu"]
            b = pw.chromium.launch(headless=headless, executable_path=CHROME, args=args)
            ctx = b.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                locale="en-US", viewport={"width":1920,"height":1080})
            if stealth_init:
                ctx.add_init_script("""
                    Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
                    Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
                    Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
                    window.chrome = {runtime:{}};
                """)
            page = ctx.new_page()

            # bot.sannysoft
            add("### bot.sannysoft.com")
            try:
                page.goto("https://bot.sannysoft.com/", timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                title = page.title()
                s = sannysoft_summary(page)
                add(f"- title: `{title}`")
                add(f"- passes={s.get('passes')} fails={s.get('fails')} unknown={s.get('unknown')}")
                rows = s.get("rows",[])
                # show interesting verdicts
                interesting = [r for r in rows if r.get("verdict") in ("PASS","FAIL")]
                for r in interesting[:15]:
                    add(f"  - {r.get('label','?')[:30]}: {r.get('val','')[:25]} → **{r.get('verdict')}**")
                if not interesting:
                    add(f"- (no classified rows; raw first 3: `{json.dumps(rows[:3])[:400]}`)")
            except Exception as e:
                add(f"- sannysoft err: `{e!r}`")

            # nowsecure.nl
            add("### nowsecure.nl (Cloudflare)")
            try:
                page.goto("https://nowsecure.nl/", timeout=45000, wait_until="domcontentloaded")
                page.wait_for_timeout(10000)
                title = page.title()
                body_text = page.evaluate("() => document.body ? document.body.innerText.slice(0,600) : ''")
                bl = (body_text or "").lower()
                markers = [m for m in ["just a moment","cf-chl","captcha","cloudflare","challenge","success","verification failed","nowsecure","nodriver"] if m in bl]
                add(f"- title: `{title}`")
                add(f"- markers: {markers}")
                add(f"- body[:300]: `{body_text[:300]}`")
            except Exception as e:
                add(f"- nowsecure err: `{e!r}`")
            b.close()
    except Exception as e:
        add(f"- TEST FAILED: `{e!r}`")
    add("")
    flush()

# A. Vanilla playwright
add("## A. Vanilla playwright + chromium-1228 (baseline)")
flush()
from playwright.sync_api import sync_playwright as pw_vanilla
run_test("A. Vanilla playwright", pw_vanilla, stealth_init=False)

# B. playwright + manual stealth init
add("## B. playwright + chromium-1228 + manual stealth init")
flush()
run_test("B. playwright + stealth init", pw_vanilla, stealth_init=True)

# C. patchright (stealth-maxxing, drop-in)
add("## C. patchright + chromium-1228 (stealth-maxxing, drop-in)")
flush()
from patchright.sync_api import sync_playwright as pw_patchright
run_test("C. patchright", pw_patchright, stealth_init=False)

# D. patchright + stealth init (belt-and-suspenders)
add("## D. patchright + stealth init (belt-and-suspenders)")
flush()
run_test("D. patchright + stealth init", pw_patchright, stealth_init=True)

print("wrote docs/results/browser.md")
print(f"sections: {len(SECTIONS)} lines")
