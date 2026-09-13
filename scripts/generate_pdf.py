#!/usr/bin/env python3
"""
Generate a polished PDF from the job sourcing research markdown report.
Uses pandoc to convert MD → HTML, then weasyprint to convert HTML → PDF.
"""

import subprocess
import os
from pathlib import Path

MD_PATH = "/home/z/my-project/download/job_sourcing_research_second_pass.md"
HTML_PATH = "/home/z/my-project/scripts/job_sourcing_research_second_pass.html"
PDF_PATH = "/home/z/my-project/download/job_sourcing_research_second_pass.pdf"
CSS_PATH = "/home/z/my-project/scripts/report.css"

CSS = """
@page {
    size: A4;
    margin: 2cm 1.8cm 2cm 1.8cm;
    @bottom-center {
        content: counter(page);
        font-size: 9pt;
        color: #64748b;
        font-family: 'Inter', 'Helvetica', sans-serif;
    }
    @top-right {
        content: 'Job Sourcing API Landscape';
        font-size: 8pt;
        color: #94a3b8;
        font-family: 'Inter', 'Helvetica', sans-serif;
    }
}

@page :first {
    margin: 0;
    @top-right { content: ''; }
    @bottom-center { content: ''; }
}

html {
    font-size: 10.5pt;
    line-height: 1.55;
    color: #1e293b;
    background: #ffffff;
    font-family: 'Inter', 'Helvetica Neue', Helvetica, Arial, sans-serif;
}

body {
    margin: 0;
    padding: 0;
}

h1 {
    font-size: 22pt;
    color: #0f172a;
    margin: 0 0 1.2em 0;
    padding-bottom: 0.4em;
    border-bottom: 3px solid #3b82f6;
    line-height: 1.2;
    font-weight: 700;
    letter-spacing: -0.5px;
}

/* Cover page H1 */
body > section.cover h1 {
    font-size: 36pt;
    margin: 0 0 0.4em 0;
    padding: 0;
    border: 0;
    color: #ffffff;
    text-align: left;
    font-weight: 800;
    letter-spacing: -1.2px;
}

h2 {
    font-size: 16pt;
    color: #1e3a8a;
    margin: 1.8em 0 0.6em 0;
    padding-bottom: 0.3em;
    border-bottom: 1px solid #cbd5e1;
    font-weight: 700;
    page-break-after: avoid;
    letter-spacing: -0.3px;
}

h3 {
    font-size: 13pt;
    color: #1e40af;
    margin: 1.4em 0 0.5em 0;
    font-weight: 700;
    page-break-after: avoid;
}

h4 {
    font-size: 11.5pt;
    color: #334155;
    margin: 1.1em 0 0.4em 0;
    font-weight: 700;
    page-break-after: avoid;
}

p {
    margin: 0 0 0.8em 0;
    text-align: left;
    orphans: 2;
    widows: 2;
}

ul, ol {
    margin: 0 0 0.8em 0;
    padding-left: 1.5em;
}

li {
    margin: 0.2em 0;
    text-align: left;
}

strong {
    color: #0f172a;
    font-weight: 700;
}

em {
    color: #475569;
}

a {
    color: #2563eb;
    text-decoration: none;
    word-break: break-all;
}

code {
    font-family: 'JetBrains Mono', 'Menlo', 'Monaco', 'Consolas', monospace;
    font-size: 0.88em;
    background: #f1f5f9;
    color: #be123c;
    padding: 0.1em 0.3em;
    border-radius: 3px;
}

pre {
    background: #0f172a;
    color: #e2e8f0;
    padding: 0.9em 1.1em;
    border-radius: 6px;
    overflow-x: auto;
    margin: 0.8em 0;
    font-size: 8.5pt;
    line-height: 1.5;
    page-break-inside: avoid;
}

pre code {
    background: transparent;
    color: inherit;
    padding: 0;
    font-size: inherit;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
    font-size: 8.5pt;
    line-height: 1.35;
    page-break-inside: avoid;
    table-layout: auto;
}

thead {
    background: #1e3a8a;
    color: #ffffff;
}

th {
    padding: 0.5em 0.6em;
    text-align: left;
    font-weight: 700;
    border: 1px solid #1e3a8a;
    vertical-align: top;
}

td {
    padding: 0.45em 0.6em;
    border: 1px solid #cbd5e1;
    vertical-align: top;
}

tbody tr:nth-child(even) {
    background: #f8fafc;
}

tbody tr:nth-child(odd) {
    background: #ffffff;
}

blockquote {
    margin: 1em 0;
    padding: 0.6em 1em;
    border-left: 4px solid #3b82f6;
    background: #eff6ff;
    color: #1e40af;
    font-style: italic;
}

blockquote p {
    margin: 0;
}

hr {
    border: 0;
    border-top: 1px solid #cbd5e1;
    margin: 2em 0;
}

/* Cover page */
.cover {
    page-break-after: always;
    height: 100vh;
    background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 60%, #3b82f6 100%);
    color: #ffffff;
    padding: 6cm 2cm 2cm 2cm;
    margin: 0;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    box-sizing: border-box;
}

.cover .subtitle {
    font-size: 14pt;
    color: #cbd5e1;
    margin: 0.5em 0 0 0;
    font-weight: 400;
    letter-spacing: 0.3px;
}

.cover .meta {
    color: #94a3b8;
    font-size: 10pt;
    line-height: 1.6;
    margin-top: 2em;
}

.cover .meta strong {
    color: #e2e8f0;
    font-weight: 600;
}

.cover .tag {
    display: inline-block;
    background: rgba(59, 130, 246, 0.2);
    border: 1px solid rgba(59, 130, 246, 0.5);
    color: #93c5fd;
    padding: 0.3em 0.8em;
    border-radius: 4px;
    font-size: 9pt;
    margin-right: 0.4em;
    margin-top: 0.8em;
    letter-spacing: 0.5px;
}

.cover .accent-line {
    width: 80px;
    height: 4px;
    background: #3b82f6;
    margin-bottom: 1.5em;
}
"""

# First, prepend a cover page section to the markdown
COVER_MARKDOWN = """
<section class="cover" markdown="1">

<div class="accent-line"></div>

# Job Sourcing & Automation — Second Pass

<p class="subtitle">A Personal-Scale Research Report — Reframed for an Individual Job Seeker, With Hands-On Stealth Validation</p>

<div markdown="1">

<span class="tag">PERSONAL SCALE</span>
<span class="tag">STEALTH TESTED</span>
<span class="tag">FREE APIs</span>
<span class="tag">2026</span>

</div>

<div class="meta">

**Author:** Z.ai Research  
**Date:** 2026-08-24  
**Context correction:** This is a personal job-search endeavor, NOT a startup. Cost ceiling: $0–$50/month. Scale: 1 user, 1K–10K jobs ingested per day, 10–50 applications submitted per week.

**Methodology:** Hands-on stealth browser testing (Playwright + playwright-stealth + SeleniumBase UC Mode + curl_cffi + undetected-chromedriver) against Indeed, LinkedIn, Glassdoor, ZipRecruiter, and Workday per-tenant CXS API. LinkedIn guest API validated at personal scale (189 jobs from one query, ~500 cap). 2 parallel deep-dive research agents: personal job-search tools (26 tools evaluated) and stealth automation techniques.

**Key corrections to first pass:**
- LinkedIn guest API real ceiling is ~500 jobs/query (not ~1000)
- SeleniumBase UC Mode does NOT bypass Cloudflare on Indeed/Glassdoor/ZipRecruiter — all three return challenge pages WITHOUT interactive Turnstile iframes (contrary to documentation claims)
- Workday CXS API requires per-company Job_Posting_Site_ID research; the agent's "90% success with plain requests" claim is disputed

</div>

</section>

"""

def main():
    # Read the markdown content
    with open(MD_PATH, "r") as f:
        md_content = f.read()

    # Strip the original H1 and metadata header (we'll use the cover instead)
    # Find the first ---
    lines = md_content.split("\n")
    # Find where the actual content starts (after the first --- divider)
    start_idx = 0
    for i, line in enumerate(lines):
        if line.strip() == "---" and i > 2:
            start_idx = i + 1
            break

    body_md = "\n".join(lines[start_idx:])

    # Combine cover + body
    full_md = COVER_MARKDOWN + "\n\n" + body_md

    # Write the combined markdown
    combined_md_path = "/home/z/my-project/scripts/job_sourcing_combined.md"
    with open(combined_md_path, "w") as f:
        f.write(full_md)

    # Write CSS
    with open(CSS_PATH, "w") as f:
        f.write(CSS)

    # Convert markdown → HTML using pandoc
    cmd = [
        "pandoc",
        combined_md_path,
        "-f", "markdown+pipe_tables+raw_html+markdown_in_html_blocks",
        "-t", "html5",
        "--standalone",
        "--css", CSS_PATH,
        "--metadata", "title=Job Sourcing API Landscape",
        "-o", HTML_PATH,
    ]
    print("Running pandoc:", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("PANDOC STDERR:", result.stderr)
        print("PANDOC STDOUT:", result.stdout)
        raise SystemExit(1)
    print(f"HTML written: {HTML_PATH}")

    # Convert HTML → PDF using weasyprint
    print("Converting HTML → PDF via weasyprint...")
    from weasyprint import HTML, CSS as WPCSS
    HTML(filename=HTML_PATH).write_pdf(PDF_PATH, stylesheets=[WPCSS(filename=CSS_PATH)])

    size_kb = os.path.getsize(PDF_PATH) / 1024
    print(f"PDF written: {PDF_PATH} ({size_kb:.1f} KB)")

if __name__ == "__main__":
    main()
