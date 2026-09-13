#!/usr/bin/env python3
"""Quick single-site turnstile test with hard timeout."""
import json, time, signal, sys
from pathlib import Path
from seleniumbase import SB

OUT_DIR = Path('/home/z/my-project/research_data/stealth_tests')

def test_one(url, name, max_wait_s=45):
    result = {'name': name, 'url': url}
    start = time.time()
    try:
        with SB(browser='chrome', headless=True, uc=True) as sb:
            sb.driver.set_page_load_timeout(30)
            sb.uc_open_with_reconnect(url, reconnect_time=4)
            time.sleep(3)
            title = sb.driver.title
            body = sb.driver.find_element('tag name', 'body').text
            
            # Check for Turnstile
            has_turnstile = False
            for sel in ["iframe[title*='Cloudflare']", "iframe[src*='challenges.cloudflare.com']"]:
                if sb.driver.find_elements('css selector', sel):
                    has_turnstile = sel
                    break
            
            blocked_words = ['just a moment', 'humans only', 'verify you are human', 
                            'performing security verification', 'forbidden cf-waf',
                            'security check', 'additional verification']
            blocked = any(w in (title + ' ' + body).lower() for w in blocked_words)
            
            result['initial_title'] = title[:100]
            result['initial_blocked'] = blocked
            result['has_turnstile'] = has_turnstile
            result['initial_preview'] = body[:300]
            
            if has_turnstile and blocked:
                try:
                    sb.uc_click(has_turnstile, timeout=10)
                    time.sleep(5)
                    title2 = sb.driver.title
                    body2 = sb.driver.find_element('tag name', 'body').text
                    blocked2 = any(w in (title2 + ' ' + body2).lower() for w in blocked_words)
                    result['after_click_title'] = title2[:100]
                    result['after_click_blocked'] = blocked2
                    result['after_click_preview'] = body2[:300]
                    result['bypassed'] = not blocked2
                except Exception as e:
                    result['click_error'] = str(e)[:200]
                    result['bypassed'] = False
            else:
                result['bypassed'] = not blocked
            
            sb.driver.save_screenshot(str(OUT_DIR / f'{name}_quick.png'))
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {str(e)[:200]}'
    result['duration_s'] = round(time.time() - start, 2)
    return result

if __name__ == '__main__':
    name = sys.argv[1] if len(sys.argv) > 1 else 'indeed'
    urls = {
        'indeed': 'https://www.indeed.com/jobs?q=software+engineer&l=San+Francisco%2C+CA',
        'glassdoor': 'https://www.glassdoor.com/Job/jobs.htm?sc.keyword=software+engineer',
        'ziprecruiter': 'https://www.ziprecruiter.com/jobs-search?search=software+engineer&location=San+Francisco',
    }
    print(f"Testing {name}...")
    r = test_one(urls[name], name)
    print(json.dumps(r, indent=2, default=str))
    # save
    all_results = []
    out_file = OUT_DIR / 'turnstile_quick_results.json'
    if out_file.exists():
        all_results = json.loads(out_file.read_text())
    all_results.append(r)
    out_file.write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\n✓ Saved to {out_file}")
