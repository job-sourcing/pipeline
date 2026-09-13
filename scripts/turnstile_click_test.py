#!/usr/bin/env python3
"""
Test 5: SeleniumBase UC Mode WITH uc_click on Turnstile iframe.
Previous test used uc_open_with_reconnect but did NOT click the Turnstile checkbox.
The agent's recommended pattern includes the click step.
"""
import json
import time
from pathlib import Path
from seleniumbase import SB

OUT_DIR = Path('/home/z/my-project/research_data/stealth_tests')
OUT_DIR.mkdir(parents=True, exist_ok=True)


def test_uc_with_turnstile_click(url, name, max_attempts=3):
    """SeleniumBase UC Mode + uc_click on Turnstile iframe."""
    result = {'name': name, 'strategy': 'uc_turnstile_click', 'url': url}
    start = time.time()
    attempts_log = []
    try:
        with SB(
            browser='chrome',
            headless=True,
            uc=True,
            xvfb=False,
            incognito=False,
        ) as sb:
            sb.driver.set_page_load_timeout(60)
            
            for attempt in range(1, max_attempts + 1):
                attempt_start = time.time()
                try:
                    # uc_open_with_reconnect — gives Cloudflare time to issue the challenge
                    sb.uc_open_with_reconnect(url, reconnect_time=6)
                    time.sleep(3)
                    
                    title = sb.driver.title
                    body_text = ''
                    try:
                        body_text = sb.driver.find_element('tag name', 'body').text
                    except Exception:
                        pass
                    
                    # Check if Turnstile iframe is present
                    has_turnstile = False
                    try:
                        # Common Turnstile iframe selectors
                        for sel in [
                            "iframe[title*='Cloudflare']",
                            "iframe[title*='Widget']",
                            "iframe[src*='challenges.cloudflare.com']",
                            "iframe[title*='captcha']",
                        ]:
                            if sb.driver.find_elements('css selector', sel):
                                has_turnstile = sel
                                break
                    except Exception:
                        pass
                    
                    # Check for Cloudflare challenge text
                    blocked_indicators = ['just a moment', 'humans only', 'verify you are human', 
                                           'performing security verification', 'forbidden cf-waf',
                                           'security check', 'additional verification required']
                    blocked = any(ind in (title + ' ' + body_text).lower() for ind in blocked_indicators)
                    
                    attempt_log = {
                        'attempt': attempt,
                        'duration_s': round(time.time() - attempt_start, 2),
                        'title': title[:100],
                        'body_text_size': len(body_text),
                        'has_turnstile': has_turnstile,
                        'blocked': blocked,
                        'body_preview': body_text[:300],
                    }
                    attempts_log.append(attempt_log)
                    
                    # Try clicking the Turnstile iframe if present
                    if has_turnstile and blocked:
                        try:
                            sb.uc_click(has_turnstile)
                            time.sleep(5)  # Wait for challenge to resolve
                            title2 = sb.driver.title
                            body2 = ''
                            try:
                                body2 = sb.driver.find_element('tag name', 'body').text
                            except Exception:
                                pass
                            blocked2 = any(ind in (title2 + ' ' + body2).lower() for ind in blocked_indicators)
                            attempts_log[-1]['after_click_title'] = title2[:100]
                            attempts_log[-1]['after_click_blocked'] = blocked2
                            attempts_log[-1]['after_click_preview'] = body2[:300]
                            
                            if not blocked2:
                                # SUCCESS — Turnstile was bypassed
                                result['status'] = 'bypassed'
                                result['final_title'] = title2
                                result['final_body'] = body2[:1000]
                                result['final_body_size'] = len(body2)
                                result['attempts'] = attempts_log
                                result['duration_s'] = round(time.time() - start, 2)
                                # Save screenshot of the bypassed page
                                sb.driver.save_screenshot(str(OUT_DIR / f'{name}_uc_bypassed.png'))
                                return result
                        except Exception as e:
                            attempts_log[-1]['click_error'] = str(e)[:200]
                    
                    if not blocked:
                        # Page loaded without challenge
                        result['status'] = 'no_challenge'
                        result['final_title'] = title
                        result['final_body'] = body_text[:1000]
                        result['final_body_size'] = len(body_text)
                        result['attempts'] = attempts_log
                        result['duration_s'] = round(time.time() - start, 2)
                        return result
                    
                    # Try reconnect one more time
                    sb.uc_open_with_reconnect(url, reconnect_time=8)
                    time.sleep(3)
                    
                except Exception as e:
                    attempt_log = {
                        'attempt': attempt,
                        'error': f'{type(e).__name__}: {str(e)[:200]}',
                    }
                    attempts_log.append(attempt_log)
            
            # All attempts failed
            result['status'] = 'still_blocked'
            result['attempts'] = attempts_log
            try:
                sb.driver.save_screenshot(str(OUT_DIR / f'{name}_uc_blocked.png'))
            except Exception:
                pass
            result['duration_s'] = round(time.time() - start, 2)
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {str(e)[:300]}'
        result['duration_s'] = round(time.time() - start, 2)
    return result


def main():
    results = []
    
    sites = [
        ('indeed', 'https://www.indeed.com/jobs?q=software+engineer&l=San+Francisco%2C+CA'),
        ('glassdoor', 'https://www.glassdoor.com/Job/jobs.htm?sc.keyword=software+engineer'),
        ('ziprecruiter', 'https://www.ziprecruiter.com/jobs-search?search=software+engineer&location=San+Francisco'),
    ]
    
    print("=" * 70)
    print("SeleniumBase UC Mode WITH Turnstile iframe click")
    print("=" * 70)
    
    for name, url in sites:
        print(f"\n--- {name} ---")
        result = test_uc_with_turnstile_click(url, name, max_attempts=2)
        results.append(result)
        
        print(f"  status: {result.get('status')}")
        print(f"  duration: {result.get('duration_s')}s")
        if result.get('final_title'):
            print(f"  final_title: {result.get('final_title')[:80]!r}")
            print(f"  final_body_size: {result.get('final_body_size', 0)}")
            print(f"  final_body_preview: {result.get('final_body', '')[:300]!r}")
        for att in result.get('attempts', []):
            print(f"  attempt {att.get('attempt')}: title={att.get('title', '')[:60]!r} blocked={att.get('blocked')} turnstile={att.get('has_turnstile')}")
            if 'after_click_blocked' in att:
                print(f"    after_click: blocked={att.get('after_click_blocked')} title={att.get('after_click_title', '')[:60]!r}")
        if 'error' in result:
            print(f"  error: {result.get('error')[:200]}")
    
    out_file = OUT_DIR / 'turnstile_click_results.json'
    out_file.write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
    print(f"\n✓ Results saved to {out_file}")
    
    print()
    print("=" * 70)
    print("FINAL VERDICT")
    print("=" * 70)
    for r in results:
        name = r['name']
        status = r.get('status', 'unknown')
        if status == 'bypassed':
            print(f"  ✅ {name}: BYPASSED — Cloudflare Turnstile click worked")
        elif status == 'no_challenge':
            print(f"  ✅ {name}: NO CHALLENGE — page loaded normally")
        elif status == 'still_blocked':
            print(f"  ❌ {name}: STILL BLOCKED — even with uc_click on Turnstile iframe")
        else:
            print(f"  ❓ {name}: {status} — {r.get('error', '')[:80]}")


if __name__ == '__main__':
    main()
