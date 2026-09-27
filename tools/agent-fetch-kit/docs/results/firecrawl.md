# Firecrawl v2 evaluation

_captured: 01:21:31 UTC_

## 0. Probe (example.com markdown)

- HTTP 200 elapsed=902ms proxy=basic
- response headers of interest:
- success=True error=None
- markdown head: `# Example Domain

This domain is for use in documentation examples without needing permission. Avoid use in operations.

[Learn more](https://iana.org/domains/example)`
- metadata: `{"title": "Example Domain", "language": "en", "viewport": "width=device-width, initial-scale=1", "favicon": "data:,", "scrapeId": "01a03ba8-7a15-734e-a105-9d8a246e8bda", "sourceURL": "https://example.com", "url": "https://example.com/", "statusCode": 200, "contentType": "text/html", "proxyUsed": "ba`

## 1. Egress IP (ipinfo.io/json as markdown)

- HTTP 200 elapsed=935ms
- Firecrawl egress IP (parsed from markdown): `195.64.115.143`
- markdown head: ````json
{
  "ip": "195.64.115.143",
  "city": "Arlington",
  "region": "Virginia",
  "country": "US",
  "loc": "38.8810,-77.1043",
  "org": "AS22773 Cox Communications Inc.",
  "postal": "22201",
  "timezone": "America/New_York",
  "readme": "https://ipinfo.io/missingauth"
}
````

## 2. JS rendering — quotes.toscrape.com/js/ (waitFor=3000)

- HTTP 200 elapsed=964ms
- markdown length=1500 quote-markers=0
- markdown head: `# [Quotes to Scrape](https://quotes.toscrape.com/)

[Login](https://quotes.toscrape.com/login)

“The world as we have created it is a process of our thinking. It cannot be changed without changing our thinking.”by Albert Einstein

Tags: changedeep-thoughtsthinkingworld

“It is our choices, Harry, th`

## 3. Cloudflare challenge — basic vs stealth proxy

| proxy | http_status | success | title/markers | markdown_head | elapsed_ms |
|---|---|---|---|---|---|
| basic | 408 | False | markers= | `` | 90800 |
| stealth | 200 | True | markers=cloudflare,challenge | `# Cloudflare Challenge

![](https://www.scrapingcourse.com/assets/images/challen` | 10220 |

## 4. nowsecure.nl (hard CF) — stealth proxy

- HTTP 200 success=True elapsed=8852ms
- markers: ['cloudflare', 'challenge', 'nowsecure']
- markdown head: `## NOWSECURE

### by nodriver

Checking your Browser…

Verify you are human

Verifying...

Stuck? [Troubleshoot](https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/f/av0/rch/bbipx/3x00000000000000000000FF/auto/fbE/new/normal?lang=auto#refresh)

Success!

Verification failed

`

## 5. Reddit /r/technology.json — stealth proxy

- HTTP 403 success=False elapsed=719ms
- markdown head: ``
