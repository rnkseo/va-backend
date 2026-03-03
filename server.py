"""
SearchRNK — Production FastAPI Backend
Full-featured SEO crawl engine with streaming, deep scan, and PageSpeed integration.
Deploy on Render.com: uvicorn main:app --host 0.0.0.0 --port 10000
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
import aiohttp
import asyncio
import json
import os
import re
import random
import collections
from urllib.parse import urlparse, urljoin, urldefrag
from bs4 import BeautifulSoup

# ─── APP SETUP ────────────────────────────────────────────────
app = FastAPI(title="SearchRNK SEO Engine", version="3.0.0")

ALLOWED_ORIGINS = [
    "https://www.searchrnk.com",
    "https://searchrnk.com",
    "http://localhost:3000",
    "http://localhost:8080",
    "http://127.0.0.1:5500",
    # Add your actual domain here
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── REQUEST MODELS ────────────────────────────────────────────
class AuditRequest(BaseModel):
    url: str

class DeepScanRequest(BaseModel):
    urls: List[str]
    max_concurrent: Optional[int] = 5

class PSIRequest(BaseModel):
    url: str
    strategy: Optional[str] = "both"  # mobile | desktop | both

# ─── CONSTANTS ────────────────────────────────────────────────
CRAWL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Cache-Control": "max-age=0",
}

JUNK_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.ico', '.bmp',
    '.pdf', '.zip', '.gz', '.tar', '.rar', '.7z',
    '.mp4', '.mp3', '.avi', '.mov', '.mkv',
    '.css', '.js', '.woff', '.woff2', '.ttf', '.eot',
    '.xml', '.json', '.rss', '.atom',
}

JUNK_PATTERNS = [
    r'\?(utm_|fbclid|gclid)',  # Tracking params
    r'#',                       # Anchors
    r'mailto:', r'tel:', r'javascript:',
    r'/wp-json/', r'/wp-admin/',
    r'\.well-known',
]

MAX_URLS_FREE = 150
MAX_LINKS_PER_PAGE = 80

# ─── URL UTILITIES ────────────────────────────────────────────
def normalize_url(url: str) -> str:
    try:
        url, _ = urldefrag(url)
        url = url.split('?')[0] if '?' in url else url
        parsed = urlparse(url)
        path = parsed.path.rstrip('/') or '/'
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"
    except:
        return url

def is_valuable_url(url: str, base_domain: str) -> bool:
    try:
        parsed = urlparse(url)
        if base_domain not in parsed.netloc.lower():
            return False
        path = parsed.path.lower()
        if any(path.endswith(ext) for ext in JUNK_EXTENSIONS):
            return False
        for pattern in JUNK_PATTERNS:
            if re.search(pattern, url, re.IGNORECASE):
                return False
        return True
    except:
        return False

def is_junk_link(url: str) -> bool:
    return any(url.startswith(p) for p in ['mailto:', 'tel:', 'javascript:', '#'])

# ─── HTTP STATUS CHECKER ──────────────────────────────────────
async def check_link_status(session: aiohttp.ClientSession, url: str, referer: str = None) -> int:
    headers = CRAWL_HEADERS.copy()
    if referer:
        headers['Referer'] = referer

    async def _try(method_name: str, is_retry: bool = False) -> Optional[int]:
        if is_retry:
            await asyncio.sleep(random.uniform(0.8, 2.0))
        try:
            method = getattr(session, method_name)
            async with method(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12), allow_redirects=False, ssl=False) as resp:
                status = resp.status
                if status in [405, 501, 503, 429, 502]:
                    return None
                if status in [301, 302, 307, 308]:
                    location = resp.headers.get('Location', '')
                    if location:
                        full_redirect = urljoin(url, location)
                        if normalize_url(full_redirect) == normalize_url(url):
                            return 200
                return status
        except:
            return None

    status = await _try('head')
    if status is None:
        status = await _try('get')
    if status is None:
        status = await _try('get', is_retry=True)

    return status if status is not None else 0

# ─── ON-PAGE EXTRACTOR ────────────────────────────────────────
def extract_on_page(soup: BeautifulSoup, url: str) -> dict:
    """Extract comprehensive on-page SEO data from parsed HTML."""
    # Title
    title = ''
    title_tag = soup.find('title')
    if title_tag:
        title = title_tag.get_text(strip=True)[:200]

    # Meta Description
    meta_desc = ''
    meta_desc_tag = soup.find('meta', attrs={'name': re.compile(r'^description$', re.I)})
    if meta_desc_tag:
        meta_desc = meta_desc_tag.get('content', '')[:300]

    # H1
    h1_tags = soup.find_all('h1')
    h1_text = h1_tags[0].get_text(strip=True)[:150] if h1_tags else ''

    # All headings
    headings = []
    for tag in ['h1', 'h2', 'h3', 'h4']:
        for el in soup.find_all(tag):
            headings.append({'tag': tag.upper(), 'text': el.get_text(strip=True)[:100]})

    # Canonical
    canonical = ''
    canon_tag = soup.find('link', rel='canonical')
    if canon_tag:
        canonical = canon_tag.get('href', '').strip()

    # Robots meta
    robots_meta = 'index, follow'
    robots_tag = soup.find('meta', attrs={'name': re.compile(r'^robots$', re.I)})
    if robots_tag:
        robots_meta = robots_tag.get('content', 'index, follow')

    # OG tags
    og = {}
    for tag in soup.find_all('meta', property=re.compile(r'^og:')):
        prop = tag.get('property', '').replace('og:', '')
        if prop:
            og[prop] = tag.get('content', '')[:200]

    # Images
    images = []
    for img in soup.find_all('img')[:30]:
        src = img.get('src', '') or img.get('data-src', '')
        alt = img.get('alt', '')
        images.append({
            'src': src[:200] if src else '',
            'alt': alt[:150],
            'missing_alt': not bool(alt.strip()),
            'loading': img.get('loading', ''),
        })

    # Words
    for s in soup(['script', 'style', 'noscript', 'nav', 'footer', 'header']):
        s.decompose()
    text = soup.get_text(' ', strip=True)
    word_count = len(text.split())

    # Schema
    schemas = []
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '{}')
            schema_type = data.get('@type', 'Unknown')
            schemas.append({'type': schema_type, 'data': data})
        except:
            pass

    # Security headers check (from response headers, passed in separately)
    return {
        'title': title,
        'title_length': len(title),
        'meta_description': meta_desc,
        'meta_description_length': len(meta_desc),
        'h1': h1_text,
        'h1_count': len(h1_tags),
        'headings': headings[:20],
        'canonical': canonical,
        'robots_meta': robots_meta,
        'og_tags': og,
        'images': images,
        'image_count': len(soup.find_all('img')),
        'missing_alt_count': sum(1 for img in soup.find_all('img') if not img.get('alt')),
        'word_count': word_count,
        'schemas': schemas,
        'schema_count': len(schemas),
    }

# ─── PAGESPEED INTEGRATION ────────────────────────────────────
async def run_pagespeed(url: str, strategy: str = 'mobile') -> dict:
    """Fetch PageSpeed Insights data for a URL."""
    api_key = os.environ.get('PAGESPEED_API_KEY', '').strip()

    empty = {
        'score': None, 'fcp': None, 'lcp': None, 'tbt': None,
        'cls': None, 'inp': None, 'si': None,
        'opportunities': [], 'diagnostics': [], 'passed': []
    }

    if not api_key:
        return empty

    def safe_metric(audits: dict, key: str, unit: str = 's') -> Optional[str]:
        item = audits.get(key, {})
        if 'displayValue' in item:
            return item['displayValue']
        val = item.get('numericValue')
        if val is not None:
            if unit == 'ms':
                return f"{int(val)} ms"
            return f"{val/1000:.2f} s"
        return None

    def extract_opportunities(audits: dict) -> list:
        opps = []
        opp_keys = [
            'render-blocking-resources', 'uses-optimized-images',
            'unused-css-rules', 'unused-javascript',
            'uses-text-compression', 'uses-efficient-cache-policy',
            'offscreen-images', 'unminified-javascript', 'unminified-css',
            'uses-webp-images', 'efficient-animated-content',
        ]
        for key in opp_keys:
            audit = audits.get(key, {})
            if audit.get('score') is not None and audit.get('score') < 1:
                savings = audit.get('details', {}).get('overallSavingsMs') or audit.get('details', {}).get('overallSavingsBytes')
                opps.append({
                    'id': key,
                    'title': audit.get('title', key),
                    'description': audit.get('description', ''),
                    'score': audit.get('score'),
                    'savings': savings,
                    'display_value': audit.get('displayValue', ''),
                })
        return opps

    try:
        api_url = (
            f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
            f"?url={url}&strategy={strategy}"
            f"&category=performance&category=seo&category=accessibility&category=best-practices"
            f"&key={api_key}"
        )
        async with aiohttp.ClientSession() as sess:
            async with sess.get(api_url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                data = await resp.json()

        if 'error' in data or 'lighthouseResult' not in data:
            return empty

        lh = data['lighthouseResult']
        audits = lh.get('audits', {})
        cats = lh.get('categories', {})

        def cat_score(key):
            c = cats.get(key, {})
            s = c.get('score')
            return int(s * 100) if s is not None else None

        return {
            'score': cat_score('performance'),
            'seo_score': cat_score('seo'),
            'accessibility_score': cat_score('accessibility'),
            'best_practices_score': cat_score('best-practices'),
            'fcp': safe_metric(audits, 'first-contentful-paint'),
            'lcp': safe_metric(audits, 'largest-contentful-paint'),
            'tbt': safe_metric(audits, 'total-blocking-time', 'ms'),
            'cls': safe_metric(audits, 'cumulative-layout-shift', ''),
            'inp': safe_metric(audits, 'interaction-to-next-paint', 'ms'),
            'si': safe_metric(audits, 'speed-index'),
            'ttfb': safe_metric(audits, 'server-response-time', 'ms'),
            'opportunities': extract_opportunities(audits),
            'passed': [a for a, v in audits.items() if isinstance(v, dict) and v.get('score') == 1],
        }
    except Exception as e:
        return empty

async def run_pagespeed_parallel(url: str) -> dict:
    """Run mobile + desktop PageSpeed in parallel."""
    mobile, desktop = await asyncio.gather(
        run_pagespeed(url, 'mobile'),
        run_pagespeed(url, 'desktop'),
        return_exceptions=True
    )
    empty = {'score': None, 'fcp': None, 'lcp': None, 'tbt': None, 'cls': None, 'inp': None, 'si': None, 'opportunities': []}
    return {
        'mobile': mobile if not isinstance(mobile, Exception) else empty,
        'desktop': desktop if not isinstance(desktop, Exception) else empty,
    }

# ─── SECURITY HEADERS CHECK ───────────────────────────────────
def analyze_security_headers(response_headers: dict, url: str) -> dict:
    headers = {k.lower(): v for k, v in response_headers.items()}
    checks = {
        'Content-Security-Policy': 'content-security-policy' in headers,
        'Strict-Transport-Security': 'strict-transport-security' in headers,
        'X-Frame-Options': 'x-frame-options' in headers,
        'X-Content-Type-Options': 'x-content-type-options' in headers,
        'Referrer-Policy': 'referrer-policy' in headers,
        'Permissions-Policy': 'permissions-policy' in headers or 'feature-policy' in headers,
    }
    present = sum(checks.values())
    score = int((present / len(checks)) * 100)
    return {
        'https': url.startswith('https://'),
        'headers': checks,
        'score': score,
        'present_count': present,
        'total_count': len(checks),
    }

# ─── SITEMAP PARSER ────────────────────────────────────────────
async def parse_sitemap(session: aiohttp.ClientSession, sitemap_url: str, visited: set) -> tuple:
    """Returns (nested_sitemaps, page_urls)"""
    if sitemap_url in visited:
        return [], []
    visited.add(sitemap_url)

    try:
        async with session.get(sitemap_url, timeout=aiohttp.ClientTimeout(total=12), ssl=False) as resp:
            if resp.status != 200:
                return [], []
            content = await resp.read()

        soup = BeautifulSoup(content, 'xml')
        nested = [s.find('loc').text.strip() for s in soup.find_all('sitemap') if s.find('loc')]
        pages = [u.find('loc').text.strip() for u in soup.find_all('url') if u.find('loc')]
        return nested, pages
    except:
        return [], []

# ─── MAIN ANALYZE ENDPOINT ────────────────────────────────────
@app.post("/analyze")
async def analyze_stream(request: AuditRequest):
    """
    Main streaming endpoint. Yields NDJSON events:
    - {type: "init", final_url, on_page, security_headers}
    - {type: "url", url}
    - {type: "speed_full", data: {mobile, desktop}}
    - {type: "done", stats}
    """
    target = request.url.strip()
    if not target.startswith('http'):
        target = 'https://' + target
    base_domain = urlparse(target).netloc.lower()

    async def generator():
        found_urls: set = set()
        init_data = {
            'type': 'init',
            'final_url': target,
            'on_page': {},
            'security_headers': {},
        }

        speed_task = asyncio.create_task(run_pagespeed_parallel(target))
        speed_sent = False

        connector = aiohttp.TCPConnector(limit=30, ssl=False)
        async with aiohttp.ClientSession(headers=CRAWL_HEADERS, connector=connector) as session:

            # ── Crawl homepage ──────────────────────────────
            try:
                async with session.get(target, timeout=aiohttp.ClientTimeout(total=20), ssl=False, allow_redirects=True) as resp:
                    final_url = str(resp.url)
                    init_data['final_url'] = final_url
                    init_data['security_headers'] = analyze_security_headers(dict(resp.headers), final_url)
                    norm = normalize_url(final_url)
                    found_urls.add(norm)

                    text = await resp.text(errors='replace')
                    soup = BeautifulSoup(text, 'lxml')
                    init_data['on_page'] = extract_on_page(soup, final_url)
            except Exception as e:
                init_data['error'] = str(e)

            yield json.dumps(init_data) + '\n'

            if found_urls:
                yield json.dumps({'type': 'url', 'url': list(found_urls)[0]}) + '\n'

            # ── Sitemap discovery ────────────────────────────
            base = init_data['final_url']
            sitemap_candidates = [
                urljoin(base, '/sitemap.xml'),
                urljoin(base, '/sitemap_index.xml'),
                urljoin(base, '/sitemap-index.xml'),
                urljoin(base, '/wp-sitemap.xml'),
                urljoin(base, '/news-sitemap.xml'),
                urljoin(base, '/video-sitemap.xml'),
            ]

            sitemap_queue = collections.deque(sitemap_candidates)
            visited_sitemaps = set()

            while sitemap_queue and len(found_urls) < MAX_URLS_FREE:
                # Batch 3 sitemaps at a time
                batch = []
                for _ in range(min(3, len(sitemap_queue))):
                    sm = sitemap_queue.popleft()
                    if sm not in visited_sitemaps:
                        batch.append(sm)
                        visited_sitemaps.add(sm)

                if not batch:
                    break

                results = await asyncio.gather(*[parse_sitemap(session, u, visited_sitemaps) for u in batch])

                for nested, pages in results:
                    for nm in nested:
                        sitemap_queue.append(nm)
                    for page in pages:
                        norm = normalize_url(page)
                        if is_valuable_url(norm, base_domain) and norm not in found_urls:
                            if len(found_urls) >= MAX_URLS_FREE:
                                break
                            found_urls.add(norm)
                            yield json.dumps({'type': 'url', 'url': norm}) + '\n'

                # Send speed data if ready
                if not speed_sent and speed_task.done():
                    try:
                        speed_data = speed_task.result()
                        yield json.dumps({'type': 'speed_full', 'data': speed_data}) + '\n'
                        speed_sent = True
                    except:
                        pass

            # ── Homepage link crawl fallback ─────────────────
            if len(found_urls) < 5:
                try:
                    async with session.get(base, timeout=aiohttp.ClientTimeout(total=15), ssl=False) as resp:
                        text = await resp.text(errors='replace')
                        soup = BeautifulSoup(text, 'lxml')
                        for a in soup.find_all('a', href=True):
                            href = urljoin(base, a['href'])
                            if is_junk_link(href):
                                continue
                            norm = normalize_url(href)
                            if is_valuable_url(norm, base_domain) and norm not in found_urls:
                                if len(found_urls) >= MAX_URLS_FREE:
                                    break
                                found_urls.add(norm)
                                yield json.dumps({'type': 'url', 'url': norm}) + '\n'
                except:
                    pass

            # ── Wait for speed data if not yet sent ──────────
            if not speed_sent:
                try:
                    speed_data = await asyncio.wait_for(speed_task, timeout=30)
                    yield json.dumps({'type': 'speed_full', 'data': speed_data}) + '\n'
                except:
                    pass

        yield json.dumps({
            'type': 'done',
            'stats': {
                'total_urls': len(found_urls),
                'limit_reached': len(found_urls) >= MAX_URLS_FREE,
            }
        }) + '\n'

    return StreamingResponse(generator(), media_type='application/x-ndjson')

# ─── DEEP SCAN ENDPOINT ───────────────────────────────────────
@app.post("/deep-scan")
async def deep_scan(request: DeepScanRequest):
    """
    Full deep scan: visits each URL, extracts all links, checks status codes.
    Yields NDJSON per URL.
    """
    async def scanner():
        page_sem = asyncio.Semaphore(request.max_concurrent)
        link_sem = asyncio.Semaphore(20)
        link_cache = {}

        connector = aiohttp.TCPConnector(limit=40, ssl=False)
        async with aiohttp.ClientSession(headers=CRAWL_HEADERS, connector=connector) as session:

            async def check_cached(url: str, referer: str) -> int:
                if url in link_cache:
                    return link_cache[url]
                async with link_sem:
                    status = await check_link_status(session, url, referer)
                    link_cache[url] = status
                    return status

            async def scan_page(page_url: str) -> str:
                async with page_sem:
                    await asyncio.sleep(random.uniform(0.05, 0.15))  # Polite delay
                    result = {
                        'url': page_url,
                        'page_status': 0,
                        'content_internal': 0,
                        'content_external': 0,
                        'canonical': None,
                        'on_page': {},
                        'security': {},
                        'link_data': [],
                    }
                    try:
                        async with session.get(
                            page_url,
                            timeout=aiohttp.ClientTimeout(total=20),
                            ssl=False, allow_redirects=True
                        ) as resp:
                            result['page_status'] = resp.status
                            result['security'] = analyze_security_headers(dict(resp.headers), page_url)
                            text = await resp.text(errors='replace')

                        soup = BeautifulSoup(text, 'html.parser')

                        # Canonical
                        canon_tag = soup.find('link', rel='canonical')
                        if canon_tag and canon_tag.get('href'):
                            result['canonical'] = canon_tag['href'].strip()

                        # On-page extract (lightweight)
                        result['on_page'] = extract_on_page(soup, page_url)

                        # Remove nav/footer for cleaner link extraction
                        for tag in soup.find_all(['script', 'style', 'noscript', 'iframe', 'svg']):
                            tag.decompose()

                        page_domain = urlparse(page_url).netloc.lower()

                        # Collect unique links
                        seen_links = {}
                        for a in soup.find_all('a', href=True):
                            href = a.get('href', '').strip()
                            if is_junk_link(href):
                                continue
                            full_url = urljoin(page_url, href)
                            if not full_url.startswith('http'):
                                continue
                            clean = normalize_url(full_url)
                            anchor = a.get_text(strip=True)[:60] or '[no text]'
                            follow = 'nofollow' not in (a.get('rel') or [])
                            if clean not in seen_links:
                                seen_links[clean] = (anchor, follow)

                        # Limit & check concurrently
                        links_to_check = list(seen_links.items())[:MAX_LINKS_PER_PAGE]
                        status_tasks = [check_cached(url, page_url) for url, _ in links_to_check]
                        statuses = await asyncio.gather(*status_tasks, return_exceptions=True)

                        for (link_url, (anchor, follow)), status in zip(links_to_check, statuses):
                            if isinstance(status, Exception):
                                status = 0
                            is_internal = urlparse(link_url).netloc.lower() == page_domain
                            if is_internal:
                                result['content_internal'] += 1
                            else:
                                result['content_external'] += 1
                            result['link_data'].append({
                                'anchor': anchor,
                                'link': link_url,
                                'status': status or 0,
                                'type': 'Internal' if is_internal else 'External',
                                'follow': follow,
                            })
                    except Exception:
                        pass

                    return json.dumps(result) + '\n'

            tasks = [scan_page(u) for u in request.urls[:MAX_URLS_FREE]]
            for coro in asyncio.as_completed(tasks):
                yield await coro

    return StreamingResponse(scanner(), media_type='application/x-ndjson')

# ─── ON-DEMAND PSI ENDPOINT ───────────────────────────────────
@app.post("/pagespeed")
async def pagespeed_endpoint(request: PSIRequest):
    """On-demand PageSpeed for a specific URL (called from modal)."""
    if request.strategy == 'both':
        data = await run_pagespeed_parallel(request.url)
    else:
        data = await run_pagespeed(request.url, request.strategy)
    return data

# ─── HEALTH CHECK ─────────────────────────────────────────────
@app.get("/")
async def root():
    return {"status": "ok", "version": "3.0.0", "service": "SearchRNK SEO Engine"}

@app.get("/health")
async def health():
    api_key = os.environ.get('PAGESPEED_API_KEY', '')
    return {
        "status": "healthy",
        "pagespeed_configured": bool(api_key),
        "max_urls": MAX_URLS_FREE,
    }

# ─── ENTRY POINT ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
