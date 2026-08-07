import os
import time
import random
import re
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from playwright.sync_api import sync_playwright
import zipfile
import tempfile
import shutil
from collections import OrderedDict
import ssl
import urllib3
import socket
from urllib.parse import urlparse, urljoin
import sys
from datetime import datetime, timedelta, timezone

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ==================================================
# GLOBAL CONFIG
# ==================================================

BASE = "https://www.screener.in"
LOGIN_URL = f"{BASE}/login/"
LOGOUT_URL = f"{BASE}/logout/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Connection": "keep-alive",
}

FINANCIAL_YEARS = [
    "Financial Year 2025", "Financial Year 2024", "Financial Year 2023",
    "Financial Year 2022", "Financial Year 2021", "Financial Year 2020",
    "Financial Year 2019", "Financial Year 2018", "Financial Year 2017",
    "Financial Year 2016", "Financial Year 2015", "Financial Year 2014",
    "Financial Year 2013", "Financial Year 2012", "Financial Year 2011", "Financial Year 2010",
    "Financial Year 2009", "Financial Year 2008", "Financial Year 2007", "Financial Year 2006",
    "Financial Year 2005", "Financial Year 2004", "Financial Year 2003", "Financial Year 2002", "Financial Year 2001", "Financial Year 2000"
]

SSL_ISSUE_DOMAINS = ["caplinpoint.net", "www.caplinpoint.net"]
PROBLEMATIC_DOMAINS = ["cmsbox.caplinpoint.net"]
dns_failure_cache = set()

# Global session for maintaining cookies
global_session = None
CSRF_TOKEN = "ECUv6sYGiv4hJEDT8MNiC7lAmaLayhBT"
SESSION_ID = "cfcf6c8qx6231n2dg4ghgmxex9nrag3q"
IS_LOGGED_IN = True

# 6-month window threshold
MONTHS_BACK = 6

# Use UTC now for consistent date calculations
NOW = datetime.now(timezone.utc)
CUTOFF_DATE = NOW - timedelta(days=MONTHS_BACK * 30.44)


# ==================================================
# DNS CHECK UTILITY
# ==================================================

def can_resolve_domain(hostname, timeout=3):
    if hostname in dns_failure_cache:
        return False
    try:
        socket.setdefaulttimeout(timeout)
        socket.gethostbyname(hostname)
        return True
    except socket.gaierror:
        dns_failure_cache.add(hostname)
        return False
    finally:
        socket.setdefaulttimeout(None)


def extract_hostname(url):
    try:
        parsed = urlparse(url)
        return parsed.hostname
    except:
        return None


def is_domain_problematic(url):
    url_lower = url.lower()
    return any(domain in url_lower for domain in PROBLEMATIC_DOMAINS)


# ==================================================
# SSL CONTEXT FOR REQUESTS
# ==================================================

def create_ssl_verified_session(verify_ssl=True):
    session = requests.Session()
    session.headers.update(HEADERS)
    session.verify = verify_ssl
    
    retry_strategy = Retry(
        total=3, backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    return session

ssl_verified_session = create_ssl_verified_session(verify_ssl=True)
ssl_unverified_session = create_ssl_verified_session(verify_ssl=False)


# ==================================================
# INITIALIZE LOGIN SESSION
# ==================================================

def init_login_session():
    global global_session, CSRF_TOKEN, SESSION_ID, IS_LOGGED_IN
    
    print("\n" + "="*70)
    print("🔐 INITIALIZING LOGIN SESSION")
    print("="*70)
    
    global_session = create_ssl_verified_session(verify_ssl=False)
    global_session.cookies.set('csrftoken', CSRF_TOKEN, domain='.screener.in')
    global_session.cookies.set('sessionid', SESSION_ID, domain='.screener.in')
    
    IS_LOGGED_IN = True
    print(f"✅ Session initialized with hardcoded credentials")
    print(f"   CSRF Token: {CSRF_TOKEN[:20]}...")
    print(f"   Session ID: {SESSION_ID[:20]}...")
    return True


def logout():
    global global_session, IS_LOGGED_IN
    
    if not IS_LOGGED_IN or not global_session:
        return
    
    print("\n" + "="*70)
    print("🔓 LOGGING OUT...")
    print("="*70)
    
    try:
        headers = {'Referer': BASE + '/', 'X-CSRFToken': CSRF_TOKEN}
        r = global_session.get(LOGOUT_URL, headers=headers, timeout=10)
        if r.status_code in [200, 302]:
            print("✅ Logged out from screener.in")
    except:
        pass
    
    global_session = None
    IS_LOGGED_IN = False
    
    print("✅ Session cleared - account safe")
    print("="*70)


def safe_get_with_auth(url, stream=False, timeout=(10, 60), referer=None):
    if not IS_LOGGED_IN or not global_session:
        return None
    if is_domain_problematic(url):
        return None
    
    hostname = extract_hostname(url)
    if hostname and not can_resolve_domain(hostname):
        return None
    
    headers = {}
    if referer:
        headers['Referer'] = referer
        headers['X-Requested-With'] = 'XMLHttpRequest'
    
    for attempt in range(2):
        try:
            r = global_session.get(url, timeout=timeout, stream=stream, headers=headers if headers else None)
            if r.status_code in [404, 403]:
                return None
            r.raise_for_status()
            if "Login required" in r.text or "Please create a free login" in r.text:
                return None
            return r
        except:
            time.sleep(2)
    return None


# ==================================================
# SAFE REQUEST
# ==================================================

def should_skip_ssl_verification(url):
    return any(domain in url.lower() for domain in SSL_ISSUE_DOMAINS)


def safe_get(url, stream=False, timeout=(10, 60)):
    if is_domain_problematic(url):
        with open("dead_links.txt", "a") as f:
            f.write(f"{url} # KNOWN PROBLEMATIC DOMAIN\n")
        return None
    
    hostname = extract_hostname(url)
    if hostname and not can_resolve_domain(hostname):
        with open("dead_links.txt", "a") as f:
            f.write(f"{url} # DNS FAILURE\n")
        return None
    
    session = ssl_unverified_session if should_skip_ssl_verification(url) else ssl_verified_session
    
    for attempt in range(3):
        try:
            r = session.get(url, timeout=timeout, stream=stream)
            if r.status_code == 404:
                with open("dead_links.txt", "a") as f:
                    f.write(url + "\n")
                return None
            r.raise_for_status()
            return r
        except:
            time.sleep(2 ** attempt)
    return None


# ==================================================
# FILENAME SANITIZER
# ==================================================

def sanitize_filename(name, max_length=150):
    name = " ".join(name.split())
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = name.replace('–', '-').replace('—', '-')
    name = name.replace("'", "").replace('"', "")
    name = name.encode('ascii', 'ignore').decode('ascii')
    return name[:max_length]


# ==================================================
# EXTRACT COMPANY ID
# ==================================================

def extract_company_id(soup):
    for button in soup.find_all('button', onclick=True):
        onclick = button.get('onclick', '')
        match = re.search(r'/announcements/(?:recent|important)/(\d+)/', onclick)
        if match:
            return match.group(1)
    
    for button in soup.find_all('button', {'data-url': True}):
        data_url = button.get('data-url', '')
        for pattern in [r'/company/actions/(\d+)/', r'/results/rpt/(\d+)/', r'/trades/company-(\d+)/']:
            match = re.search(pattern, data_url)
            if match:
                return match.group(1)
    return None


# ==================================================
# ANNOUNCEMENTS PARSING
# ==================================================

def parse_relative_time(time_str):
    """Parse relative time strings to datetime objects using UTC"""
    now = NOW
    time_str = time_str.strip()
    
    # Try full date with year: "17 Oct 2025"
    match = re.match(r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})', time_str, re.I)
    if match:
        try:
            month_map = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                         'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
            day = int(match.group(1))
            month = month_map[match.group(2).lower()]
            year = int(match.group(3))
            return datetime(year, month, day, tzinfo=timezone.utc)
        except:
            pass
    
    # Try date without year: "17 Oct"
    match = re.match(r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', time_str, re.I)
    if match:
        try:
            month_map = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                         'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
            day = int(match.group(1))
            month = month_map[match.group(2).lower()]
            # Determine year - if month is in the future relative to now, use previous year
            year = now.year
            parsed_date = datetime(year, month, day, tzinfo=timezone.utc)
            if parsed_date > now:
                year = now.year - 1
                parsed_date = datetime(year, month, day, tzinfo=timezone.utc)
            return parsed_date
        except:
            pass
    
    # Parse relative patterns: "2 days ago", "1 week ago", "3 hours ago", "45m ago"
    match = re.match(r'(\d+)\s*(s|m|h|d|w)', time_str, re.I)
    if match:
        value = int(match.group(1))
        unit = match.group(2).lower()
        if unit == 's':
            return now - timedelta(seconds=value)
        elif unit == 'm':
            return now - timedelta(minutes=value)
        elif unit == 'h':
            return now - timedelta(hours=value)
        elif unit == 'd':
            return now - timedelta(days=value)
        elif unit == 'w':
            return now - timedelta(weeks=value)
    
    return None


def is_within_months(date_obj, months=MONTHS_BACK):
    """Check if date is within the specified months window from now.
    Uses exact date comparison, not 30-day month approximation.
    For a 6-month window from May 2, 2026: anything from Nov 2, 2025 onwards is included.
    """
    if not date_obj:
        return False
    
    # Calculate cutoff date using relativedelta-like logic
    # Go back 'months' months from now
    cutoff = NOW
    for _ in range(months):
        # Go back one month
        if cutoff.month == 1:
            cutoff = cutoff.replace(year=cutoff.year - 1, month=12)
        else:
            cutoff = cutoff.replace(month=cutoff.month - 1)
    
    # Handle day overflow (e.g. May 31 -> April 30)
    try:
        cutoff = cutoff.replace(day=NOW.day)
    except ValueError:
        # If the day doesn't exist in the target month, use the last day of that month
        if cutoff.month == 12:
            next_month = cutoff.replace(year=cutoff.year + 1, month=1, day=1)
        else:
            next_month = cutoff.replace(month=cutoff.month + 1, day=1)
        cutoff = next_month - timedelta(days=1)
    
    return date_obj >= cutoff


def clean_announcement_title(title):
    title = " ".join(title.split())
    title = re.sub(r'^Announcement under Regulation \d+ \(LODR\)-', '', title).strip()
    title = re.sub(r'^Announcement under Regulation \d+', '', title).strip()
    title = re.sub(r'[^\w\s-]', '', title)
    title = " ".join(title.split())
    if len(title) > 80:
        title = title[:80] + "..."
    return title


# ==================================================
# PARSE RECENT ANNOUNCEMENTS
# ==================================================

def parse_recent_announcements(soup):
    results = []
    print(f"\n🔍 PARSING RECENT ANNOUNCEMENTS (from initial page HTML)")
    print(f"   Window: Last {MONTHS_BACK} months (since {CUTOFF_DATE.strftime('%d %b %Y')})")
    
    announcements_container = soup.find('div', id='company-announcements-tab')
    if not announcements_container:
        print("⚠️ Could not find announcements container")
        return results
    
    list_items = announcements_container.find_all('li', class_='overflow-wrap-anywhere')
    print(f"📋 Found {len(list_items)} list items in Recent tab")
    
    included = 0
    excluded_window = 0
    excluded_parse = 0
    
    for idx, li in enumerate(list_items, 1):
        link = li.find('a', href=True)
        if not link or not link['href'].endswith('.pdf'):
            continue
        
        time_div = li.find('div', class_='ink-600')
        if not time_div:
            continue
        
        time_text = time_div.text.strip()
        title = link.text.strip()
        clean_title = " ".join(title.split())
        
        parsed_date = parse_relative_time(time_text)
        
        if parsed_date and is_within_months(parsed_date, MONTHS_BACK):
            pdf_url = link['href']
            if not pdf_url.startswith('http'):
                pdf_url = urljoin(BASE, pdf_url)
            
            date_str = parsed_date.strftime('%Y%m%d')
            short_title = clean_announcement_title(clean_title)
            unique_key = f"{date_str}_{short_title[:50]}"
            
            results.append({
                'title': clean_title,
                'url': pdf_url,
                'time_text': time_text,
                'date': parsed_date,
                'type': "Announcement (Recent)",
                'text': f"{clean_title} ({time_text})",
                'unique_key': unique_key,
                'date_str': parsed_date.strftime('%d_%b_%Y')
            })
            included += 1
        elif parsed_date:
            excluded_window += 1
        else:
            excluded_parse += 1
    
    print(f"   ✅ Included (within window): {included}")
    if excluded_window > 0:
        print(f"   ⏭️  Outside {MONTHS_BACK}-month window: {excluded_window}")
    if excluded_parse > 0:
        print(f"   ⚠️  Could not parse date: {excluded_parse}")
    
    return results


def fetch_important_announcements(company_url, soup):
    if not IS_LOGGED_IN:
        return []
    
    results = []
    print(f"\n🔍 FETCHING IMPORTANT ANNOUNCEMENTS")
    print(f"   Window: Last {MONTHS_BACK} months (since {CUTOFF_DATE.strftime('%d %b %Y')})")
    
    company_id = extract_company_id(soup)
    if not company_id:
        print("⚠️ Could not find company ID")
        return results
    
    important_url = f"{BASE}/announcements/important/{company_id}/"
    print(f"URL: {important_url}")
    
    r = safe_get_with_auth(important_url, referer=company_url)
    if not r:
        print("⚠️ Failed to fetch")
        return results
    
    time.sleep(random.uniform(1, 2))
    
    important_soup = BeautifulSoup(r.text, 'html.parser')
    list_items = important_soup.find_all('li', class_='overflow-wrap-anywhere')
    print(f"📋 Found {len(list_items)} items in Important tab")
    
    included = 0
    excluded_window = 0
    excluded_parse = 0
    
    for li in list_items:
        link = li.find('a', href=True)
        if not link or not link['href'].endswith('.pdf'):
            continue
        
        time_div = li.find('div', class_='ink-600')
        if not time_div:
            continue
        
        time_text = time_div.text.strip()
        title = " ".join(link.text.strip().split())
        parsed_date = parse_relative_time(time_text)
        
        if parsed_date and is_within_months(parsed_date, MONTHS_BACK):
            pdf_url = link['href']
            if not pdf_url.startswith('http'):
                pdf_url = urljoin(BASE, pdf_url)
            
            date_str = parsed_date.strftime('%Y%m%d')
            short_title = clean_announcement_title(title)
            unique_key = f"{date_str}_{short_title[:50]}"
            
            results.append({
                'title': title,
                'url': pdf_url,
                'time_text': time_text,
                'date': parsed_date,
                'type': "Announcement (Important)",
                'text': f"{title} ({time_text})",
                'unique_key': unique_key,
                'date_str': parsed_date.strftime('%d_%b_%Y')
            })
            included += 1
        elif parsed_date:
            excluded_window += 1
        else:
            excluded_parse += 1
    
    print(f"   ✅ Included (within window): {included}")
    if excluded_window > 0:
        print(f"   ⏭️  Outside {MONTHS_BACK}-month window: {excluded_window}")
    if excluded_parse > 0:
        print(f"   ⚠️  Could not parse date: {excluded_parse}")
    
    return results


# ==================================================
# COMPANY PROFILE EXTRACTOR
# ==================================================

def extract_company_profile(soup):
    profile_data = {'about': '', 'key_points': ''}
    
    about_div = soup.find('div', class_='sub show-more-box about')
    if about_div:
        about_p = about_div.find('p')
        if about_p:
            profile_data['about'] = about_p.get_text('\n', strip=True)
    
    kp_div = soup.find('div', class_='sub commentary always-show-more-box')
    if kp_div:
        kp_p = kp_div.find('p')
        if kp_p:
            profile_data['key_points'] = kp_p.get_text('\n', strip=True)
    
    return profile_data


# ==================================================
# RELATED PARTY TRANSACTIONS
# ==================================================

def format_rpt_data(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    lines = []
    lines.append("=" * 80)
    lines.append("RELATED PARTY TRANSACTIONS")
    lines.append("=" * 80)
    lines.append("")
    lines.append("(All values in ₹ Cr.)")
    lines.append("")
    
    table = soup.find('table')
    if not table:
        return "No related party transactions data available."
    
    rows = table.find_all('tr')
    header_row = rows[0] if rows else None
    years = []
    if header_row:
        year_cells = header_row.find_all('th')
        years = [cell.get_text(strip=True) for cell in year_cells[1:]]
    
    current_person = None
    person_transactions = OrderedDict()
    
    for row in rows[1:]:
        cells = row.find_all('td')
        if not cells:
            continue
        
        # Check if row spans all columns (colspan), which indicates a person/entity row
        first_cell = cells[0]
        colspan = first_cell.get('colspan')
        if colspan:
            try:
                colspan_val = int(colspan)
                if colspan_val >= 10:  # Person row typically spans all year columns
                    text_content = first_cell.get_text(strip=True)
                    small_tag = first_cell.find('small', class_='ink-600')
                    
                    if small_tag:
                        role = small_tag.get_text(strip=True)
                        name = text_content.replace(role, '').strip()
                        person_name = name
                        person_role = role
                    else:
                        person_name = text_content
                        person_role = ""
                    
                    if person_name:
                        if person_role:
                            current_person = f"{person_name}: {person_role}"
                        else:
                            current_person = person_name
                        
                        if current_person not in person_transactions:
                            person_transactions[current_person] = []
                    continue
            except ValueError:
                pass
        
        is_person_name = False
        person_name = ""
        person_role = ""
        
        # Indicator 1: <small class="ink-600"> tag inside the first cell
        small_tag = first_cell.find('small', class_='ink-600')
        if small_tag:
            role = small_tag.get_text(strip=True)
            full_text = first_cell.get_text(strip=True)
            name = full_text.replace(role, '').strip()
            person_name = name
            person_role = role
            is_person_name = True
        
        # Indicator 2: Row has class 'strong' and 'stripe' (person row without <small>)
        if not is_person_name:
            row_classes = row.get('class', [])
            if 'strong' in row_classes and 'stripe' in row_classes:
                text_content = first_cell.get_text(strip=True)
                if text_content and not re.search(r'\d', text_content):
                    person_name = text_content
                    is_person_name = True
        
        # Indicator 3: First cell has class 'text' (original logic)
        if not is_person_name and 'text' in first_cell.get('class', []):
            text_content = first_cell.get_text(strip=True)
            small_tag_check = first_cell.find('small', class_='ink-600')
            
            if small_tag_check:
                role = small_tag_check.get_text(strip=True)
                name = text_content.replace(role, '').strip()
                person_name = name
                person_role = role
                is_person_name = True
            elif text_content and not re.search(r'\d', text_content):
                if any(kw in text_content for kw in ['Mr.', 'Mrs.', 'Ms.', 'Limited', 'LLP', 'Ltd', 'Inc.', 'Subsidiary', 'LLC']):
                    person_name = text_content
                    is_person_name = True
                # Also check if it's a person name followed by role in separate element
                elif first_cell.find('small'):
                    # Might have role tag without ink-600 class
                    small_any = first_cell.find('small')
                    role = small_any.get_text(strip=True)
                    name = first_cell.get_text(strip=True).replace(role, '').strip()
                    if name and role:
                        person_name = name
                        person_role = role
                        is_person_name = True
        
        # If identified as a person row, set as current person
        if is_person_name and person_name:
            if person_role:
                current_person = f"{person_name}: {person_role}"
            else:
                current_person = person_name
            
            if current_person not in person_transactions:
                person_transactions[current_person] = []
            continue
        
        # Otherwise, this is a transaction row
        if current_person:
            transaction_type = first_cell.get_text(strip=True)
            values = [c.get_text(strip=True) for c in cells[1:]]
            
            # Only add if there's actual data
            if any(v and v != '-' for v in values):
                person_transactions[current_person].append({
                    'type': transaction_type,
                    'values': values,
                    'years': years
                })
    
    # Format output
    for person, transactions in person_transactions.items():
        lines.append(f"\n{'='*60}")
        lines.append(f"  {person}")
        lines.append(f"{'='*60}")
        
        for i, trans in enumerate(transactions, 1):
            value_parts = []
            for j, val in enumerate(trans['values']):
                if val and val != '-':
                    year = trans['years'][j] if j < len(trans['years']) else f"Y{j}"
                    value_parts.append(f"{year}: ₹{val} Cr.")
            
            if value_parts:
                values_str = " | ".join(value_parts)
                lines.append(f"  {i}. {trans['type']}: {values_str}")
            else:
                lines.append(f"  {i}. {trans['type']}")
    
    return '\n'.join(lines) if person_transactions else "No related party transactions data available."


def fetch_related_party_transactions(company_url, soup):
    if not IS_LOGGED_IN:
        return None
    
    print(f"\n🔍 FETCHING RELATED PARTY TRANSACTIONS")
    
    company_id = extract_company_id(soup)
    if not company_id:
        print("⚠️ Could not extract company ID")
        return None
    
    # Try multiple URL patterns (consolidated first, then standalone)
    url_patterns = [
        f"{BASE}/results/rpt/{company_id}/consolidated/",
        f"{BASE}/results/rpt/{company_id}/",
    ]
    
    for rpt_url in url_patterns:
        print(f"Trying URL: {rpt_url}")
        
        r = safe_get_with_auth(rpt_url, referer=company_url)
        if not r:
            print(f"⚠️ No response from {rpt_url}")
            continue
        
        # Check if the response contains actual data (not empty modal or error)
        if r.status_code == 200:
            # Verify it's an HTML response with table data
            if 'text/html' in r.headers.get('content-type', '').lower():
                # Quick check: does it contain a table with rows?
                test_soup = BeautifulSoup(r.text, 'html.parser')
                table = test_soup.find('table')
                
                if table:
                    rows = table.find_all('tr')
                    # Need at least header + 1 data row to be valid
                    if len(rows) >= 2:
                        time.sleep(random.uniform(1, 2))
                        print(f"✅ Related Party Transactions fetched successfully ({len(rows)} rows)")
                        return {
                            'type': 'Related Party Transactions', 
                            'data': r.text, 
                            'url': rpt_url
                        }
                    else:
                        print(f"⚠️ Table found but only {len(rows)} rows (likely empty)")
                else:
                    print(f"⚠️ No table found in response from {rpt_url}")
            else:
                print(f"⚠️ Unexpected content type: {r.headers.get('content-type', 'unknown')}")
        else:
            print(f"⚠️ HTTP {r.status_code} from {rpt_url}")
        
        time.sleep(random.uniform(1, 2))
    
    print("❌ Failed to fetch Related Party Transactions from all URL patterns")
    return None


# ==================================================
# CORPORATE ACTIONS
# ==================================================

def format_date_string(date_str):
    match = re.match(r'(\d{4})\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{1,2})', date_str, re.I)
    if match:
        year, month, day = match.group(1), match.group(2).capitalize(), match.group(3)
        return f"{month} {day}, {year}"
    return date_str


def separate_heading_details(text):
    headings = [
        'Qualified Institutional Placement', 'Issued under ESOP Scheme',
        'Dividend', 'Buy Back', 'Bonus', 'Rights Issue', 'Stock Split',
        'Face Value Split', 'Consolidation of Shares', 'Merger',
    ]
    
    for heading in headings:
        if heading.lower() in text.lower():
            idx = text.lower().find(heading.lower())
            heading_text = text[idx:idx + len(heading)]
            after = text[idx + len(heading):].strip()
            if after:
                return f"{heading_text}: {after}"
            else:
                return heading_text
    return text


def parse_corporate_action_tab(actions_soup, tab_id):
    entries = []
    tab_div = actions_soup.find('div', id=tab_id)
    if not tab_div:
        return entries
    
    for row in tab_div.find_all('tr'):
        cells = row.find_all('td')
        if not cells or len(cells) < 2:
            continue
        
        date_divs = cells[0].find_all('div')
        if not date_divs:
            continue
        
        date_text = " ".join(d.get_text(strip=True) for d in date_divs)
        formatted_date = format_date_string(date_text)
        
        detail_cell = cells[1]
        heading_div = detail_cell.find('div', class_='font-weight-500')
        sub_div = detail_cell.find('div', class_='sub')
        
        if heading_div and sub_div:
            heading = heading_div.get_text(strip=True)
            sub_text = sub_div.get_text(strip=True)
            if sub_text:
                details = f"{heading}: {sub_text}"
            else:
                details = heading
        else:
            details = separate_heading_details(detail_cell.get_text(strip=True))
        
        entries.append({'date': formatted_date, 'details': details})
    
    return entries


def fetch_corporate_actions(company_url, soup):
    if not IS_LOGGED_IN:
        return None
    
    print(f"\n🔍 FETCHING CORPORATE ACTIONS")
    
    company_id = extract_company_id(soup)
    if not company_id:
        return None
    
    actions_url = f"{BASE}/company/actions/{company_id}/"
    print(f"URL: {actions_url}")
    
    r = safe_get_with_auth(actions_url, referer=company_url)
    if r:
        time.sleep(random.uniform(1, 2))
        actions_soup = BeautifulSoup(r.text, 'html.parser')
        result = {
            'type': 'Corporate Actions', 'url': actions_url,
            'equity_history': parse_corporate_action_tab(actions_soup, 'corporate-actions-equityhistory'),
            'esops': parse_corporate_action_tab(actions_soup, 'corporate-actions-esops'),
            'dividend': parse_corporate_action_tab(actions_soup, 'corporate-actions-dividend'),
            'buyback': parse_corporate_action_tab(actions_soup, 'corporate-actions-buyback'),
            'bonus': parse_corporate_action_tab(actions_soup, 'corporate-actions-bonus'),
            'merger': parse_corporate_action_tab(actions_soup, 'corporate-actions-merger'),
            'split': parse_corporate_action_tab(actions_soup, 'corporate-actions-split'),
        }
        
        for key, entries in result.items():
            if key not in ['type', 'url'] and entries:
                print(f"  {key.replace('_', ' ').title()}: {len(entries)} entries")
        
        return result
    
    print("⚠️ Failed to fetch")
    return None


# ==================================================
# TRADES DATA
# ==================================================

def normalize_transaction(text):
    text = text.strip().upper()
    if text in ['B', 'BUY']:
        return 'BUY'
    if text in ['S', 'SELL', 'SALE']:
        return 'SELL'
    if text == 'ACQ':
        return 'ACQUISITION'
    return text


def parse_trades_table(table, trade_type):
    entries = []
    rows = table.find_all('tr')
    current_date = ""
    
    for row in rows:
        if 'stripe' in row.get('class', []) and 'sticky-2' in row.get('class', []):
            date_cell = row.find('td')
            if date_cell:
                current_date = date_cell.get_text(strip=True)
            continue
        
        if row.find('th'):
            continue
        
        cells = row.find_all('td')
        if not cells or len(cells) < 2:
            continue
        
        if trade_type == 'insider':
            name_cell = cells[0]
            name = name_cell.get_text(strip=True)
            role_span = name_cell.find('span', class_='ink-600')
            if role_span:
                role = role_span.get_text(strip=True)
                name = name.replace(role, '').strip()
                name_display = f"{name}: {role}"
            else:
                name = " ".join(name.split())
                name_display = name
            
            quantity = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            avg_price = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            value = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            
            details = f"Name: {name_display} | Quantity: {quantity} | Avg Price: ₹{avg_price} | Value: ₹{value} Lacs"
        
        elif trade_type in ['bulk', 'block']:
            name_cell = cells[0]
            name = " ".join(name_cell.get_text(strip=True).split())
            transaction = normalize_transaction(cells[1].get_text(strip=True)) if len(cells) > 1 else ""
            quantity = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            price = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            
            details = f"Name: {name} | Transaction: {transaction} | Quantity: {quantity} | Price: ₹{price}"
        
        elif trade_type == 'sast':
            name_cell = cells[0]
            name = " ".join(name_cell.get_text(strip=True).split())
            transaction = normalize_transaction(cells[1].get_text(strip=True)) if len(cells) > 1 else ""
            mode = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            quantity = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            percent = cells[4].get_text(strip=True) if len(cells) > 4 else ""
            
            details = f"Name: {name} | Transaction: {transaction} | Mode: {mode} | Quantity: {quantity} | Percent: {percent}"
        
        else:
            details_parts = []
            for cell in cells:
                text = cell.get_text(strip=True)
                if text and text != current_date:
                    details_parts.append(text)
            details = " | ".join(details_parts)
        
        if details.strip():
            entries.append({'date': current_date, 'details': details})
    
    return entries


def fetch_trades_data(company_url, soup):
    if not IS_LOGGED_IN:
        return None
    
    print(f"\n🔍 FETCHING TRADES DATA")
    
    company_id = extract_company_id(soup)
    if not company_id:
        return None
    
    trades_url = f"{BASE}/trades/company-{company_id}/"
    print(f"URL: {trades_url}")
    
    r = safe_get_with_auth(trades_url, referer=company_url)
    if r:
        time.sleep(random.uniform(1, 2))
        trades_soup = BeautifulSoup(r.text, 'html.parser')
        result = {
            'type': 'Trades', 'url': trades_url,
            'insider_trades': [], 'bulk_deals': [],
            'block_deals': [], 'sast_trades': []
        }
        
        tab_mapping = {
            'trades-insider-trades': ('insider_trades', 'insider'),
            'trades-bulk-deals': ('bulk_deals', 'bulk'),
            'trades-block-deals': ('block_deals', 'block'),
            'trades-sast-trades': ('sast_trades', 'sast')
        }
        
        for tab_id, (key, trade_type) in tab_mapping.items():
            tab_div = trades_soup.find('div', id=tab_id)
            if tab_div:
                no_data_text = tab_div.get_text()
                if 'Found no recent' in no_data_text:
                    continue
                
                table = tab_div.find('table', class_='data-table')
                if table:
                    entries = parse_trades_table(table, trade_type)
                    result[key] = entries
        
        for key, entries in result.items():
            if key != 'type' and key != 'url' and entries:
                print(f"  {key.replace('_', ' ').title()}: {len(entries)} entries")
        
        return result
    
    print("⚠️ Failed to fetch")
    return None


# ==================================================
# QUARTERLY PDFs, ANNUAL REPORTS, CONCALL, CREDIT RATINGS
# ==================================================

def fetch_quarterly_pdfs(soup, company_name):
    results = []
    for link in soup.select('a[href*="/source/quarter/"]'):
        href = link.get('href')
        if not href:
            continue
        match = re.search(r'/quarter/\d+/(\d+)/(\d+)/', href)
        if match:
            month_num = match.group(1)
            year = match.group(2)
            month_names = {
                '1': 'January', '2': 'February', '3': 'March', '4': 'April',
                '5': 'May', '6': 'June', '7': 'July', '8': 'August',
                '9': 'September', '10': 'October', '11': 'November', '12': 'December'
            }
            month_name = month_names.get(month_num, month_num)
            full_url = urljoin(BASE, href)
            results.append({
                "type": "Quarterly Report",
                "text": f"{month_name} {year} Quarterly Report",
                "url": full_url,
            })
    return results


def fetch_annual_report_pdfs(url):
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    h3 = soup.find("h3", string=lambda x: x and "Annual reports" in x)
    if not h3:
        return results
    for link in h3.find_parent().find_all("a", href=True):
        text = " ".join(link.text.split())
        href = link["href"]
        if any(year in text for year in FINANCIAL_YEARS) and (href.endswith(".pdf") or href.endswith(".zip")):
            full = href if href.startswith("http") else BASE + href
            results.append({"type": "Annual Report", "text": text, "url": full})
    return results


def fetch_concall_materials(url):
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    section = soup.select_one("div.documents.concalls.flex-column")
    if not section:
        return results
    month_pattern = re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}", re.I)
    for div in section.find_all("div", string=lambda x: x and month_pattern.search(x)):
        date_text = " ".join(div.text.split())
        row = div.find_parent()
        for link in row.find_all("a", class_="concall-link", href=True):
            label = link.text.strip().upper()
            href = link["href"]
            if label in ["TRANSCRIPT", "PPT"]:
                results.append({"type": f"Concall {label}", "text": f"{date_text} {label}", "url": href})
    return results


def fetch_credit_ratings(url):
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    section = soup.select_one("div.documents.credit-ratings.flex-column")
    if not section:
        return results
    for li in section.find_all("li"):
        link = li.find("a", href=True)
        if not link:
            continue
        name = li.select_one("div.ink-600.smaller")
        text = " ".join((name.text if name else link.text).split())
        full = link["href"]
        if not full.startswith("http"):
            full = BASE + full
        results.append({"type": "Credit Rating", "text": text, "url": full})
    return results


# ==================================================
# ICRA SPECIAL HANDLER
# ==================================================

def download_icra_pdf(show_url, save_path):
    match = re.search(r"Id=(\d+)", show_url)
    if not match:
        return False
    download_url = f"https://www.icra.in/Rating/GetRationalReportFilePdf?Id={match.group(1)}"
    r = safe_get(download_url, stream=True)
    if not r:
        return False
    with open(save_path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    return True


# ==================================================
# HTML → PDF CONVERTER
# ==================================================

def convert_html_to_pdf(url, save_path, extra_wait_ms=45000, timeout_ms=120000):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--ignore-certificate-errors', '--disable-web-security'])
            page = browser.new_page()
            try:
                if "fitch" in url.lower():
                    page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                    page.wait_for_timeout(extra_wait_ms)
                else:
                    page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except:
                try:
                    page.goto(url, wait_until="load", timeout=timeout_ms)
                except:
                    browser.close()
                    return False
            page.pdf(path=save_path)
            browser.close()
        return True
    except:
        return False


# ==================================================
# SMART DOWNLOAD ENGINE
# ==================================================

def download_document(url, save_dir, filename):
    os.makedirs(save_dir, exist_ok=True)
    filepath = os.path.join(save_dir, filename)
    url_lower = url.lower()

    if "icra.in" in url_lower:
        success = download_icra_pdf(url, filepath)
        print(("[OK]" if success else "[FAIL]"), filename)
        return (success, False, None)

    r = safe_get(url, stream=True)
    if not r:
        print("[FAIL]", filename)
        return (False, False, "No response from server")

    content_type = r.headers.get("content-type", "").lower()

    if "application/pdf" in content_type:
        with open(filepath, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        print("[OK]", filename)
        time.sleep(random.uniform(2, 5))
        return (True, False, None)

    if "application/zip" in content_type or url_lower.endswith(".zip"):
        print(f"[ZIP] Downloading and extracting: {filename}")
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                tmp_path = tmp.name
                for chunk in r.iter_content(8192):
                    tmp.write(chunk)
            with zipfile.ZipFile(tmp_path, "r") as zip_ref:
                base_name = os.path.splitext(filename)[0]
                for idx, file_info in enumerate(zip_ref.filelist, 1):
                    _, ext = os.path.splitext(file_info.filename)
                    if not ext:
                        ext = ".pdf"
                    renamed_file = f"{base_name}_{idx}{ext}"
                    with zip_ref.open(file_info) as source:
                        with open(os.path.join(save_dir, renamed_file), "wb") as target:
                            shutil.copyfileobj(source, target)
                    print(f"  -> Extracted: {renamed_file}")
            os.unlink(tmp_path)
            print("[OK]", filename)
            time.sleep(random.uniform(2, 5))
            return (True, True, None)
        except Exception as e:
            print("[FAIL]", f"ZIP error: {e}")
            return (False, True, str(e))

    if "text/html" in content_type or url_lower.endswith(".html"):
        success = convert_html_to_pdf(url, filepath)
        print(("[OK]" if success else "[FAIL]"), filename)
        return (success, False, None if success else "PDF conversion failed")

    print("[FAIL]", f"Unknown format: {content_type}")
    return (False, False, f"Unknown format: {content_type}")


# ==================================================
# FINANCIAL ANALYSIS - EXTENSION LOGIC
# ==================================================

def extract_financial_data(page):
    """Extract all financial tables with QoQ/YoY calculations"""
    
    def to_number(val):
        if not val:
            return None
        try:
            return float(val.replace(',', '').replace('%', ''))
        except:
            return None
    
    def safe_round(n, d=2):
        if n is None or not isinstance(n, (int, float)):
            return None
        if not float('-inf') < n < float('inf'):
            return None
        return round(n, d)
    
    def calc_change(values, lag, is_percent):
        res = []
        for i in range(len(values)):
            if i < lag:
                res.append({'text': 'N/A', 'type': 'neutral'})
                continue
            
            curr = to_number(values[i])
            prev = to_number(values[i - lag])
            
            if curr is None or prev is None:
                res.append({'text': 'N/A', 'type': 'neutral'})
                continue
            
            if is_percent:
                diff = safe_round(curr - prev, 2)
                sign = '+' if diff >= 0 else ''
                res.append({'text': f'{sign}{diff}%', 'type': 'pos' if diff >= 0 else 'neg'})
            else:
                diff = curr - prev
                pct = safe_round((diff / prev) * 100, 2) if prev != 0 else 0
                sign = '+' if diff >= 0 else ''
                diff_str = f'{int(diff):,}' if abs(diff) >= 1 else f'{diff:.2f}'
                res.append({
                    'text': f'{sign}{diff_str} ({sign}{pct}%)',
                    'type': 'pos' if diff >= 0 else 'neg'
                })
        return res
    
    def extract_table(section_selector, name):
        result = {'name': name, 'headers': [], 'rows': []}
        
        try:
            table = page.locator(f'{section_selector} table').first
            if not table.count():
                return result
            
            headers = table.locator('thead th').all_text_contents()
            result['headers'] = [h.strip() for h in headers if h.strip()]
            
            rows = table.locator('tbody tr').all()
            for row in rows:
                try:
                    label_cell = row.locator('.text').first
                    if not label_cell.count():
                        continue
                    
                    label = label_cell.inner_text().strip().replace('+', '').strip()
                    if not label or 'Raw PDF' in label:
                        continue
                    
                    is_percent = any(kw in label for kw in ['%', 'OPM', 'ROCE', 'Growth', 'Tax %', 'Payout %'])
                    if label == 'CFO/OP':
                        is_percent = True
                    
                    value_cells = row.locator('td:not(.text)').all()
                    values = [v.inner_text().strip() for v in value_cells]
                    
                    result['rows'].append({
                        'label': label,
                        'values': values,
                        'is_percent': is_percent
                    })
                except:
                    pass
            
            return result
        except:
            return result
    
    sections = [
        ('#quarters', 'Quarterly Results'),
        ('#profit-loss', 'Profit & Loss'),
        ('#balance-sheet', 'Balance Sheet'),
        ('#cash-flow', 'Cash Flows'),
        ('#ratios', 'Ratios'),
    ]
    
    financial_data = []
    
    for selector, name in sections:
        data = extract_table(selector, name)
        if data['rows']:
            for row in data['rows']:
                if name == 'Quarterly Results':
                    row['qoq'] = calc_change(row['values'], 1, row['is_percent'])
                    row['yoy'] = calc_change(row['values'], 4, row['is_percent'])
                else:
                    row['change'] = calc_change(row['values'], 1, row['is_percent'])
            
            financial_data.append(data)
    
    # Extract shareholding
    try:
        yearly_btn = page.locator('button[data-tab-id="yearly-shp"]')
        quarterly_btn = page.locator('button[data-tab-id="quarterly-shp"]')
        
        use_yearly = False
        if yearly_btn.count():
            if 'active' not in (yearly_btn.get_attribute('class') or ''):
                yearly_btn.first.click()
                time.sleep(1)
            
            yearly_table = page.locator('#yearly-shp table').first
            if yearly_table.count():
                headers = yearly_table.locator('thead th').all_text_contents()
                data_headers = [h.strip() for h in headers if h.strip() and h.strip() != 'S.No.']
                if len(data_headers) >= 10:
                    use_yearly = True
        
        if not use_yearly and quarterly_btn.count():
            quarterly_btn.first.click()
            time.sleep(1)
        
        active_id = 'yearly-shp' if use_yearly else 'quarterly-shp'
        shp_data = extract_table(f'#{active_id}', f'Shareholding Pattern ({"Yearly" if use_yearly else "Quarterly"})')
        if shp_data['rows']:
            for row in shp_data['rows']:
                if row['label'] != 'No. of Shareholders':
                    row['is_percent'] = True
                row['change'] = calc_change(row['values'], 1, row['is_percent'])
            financial_data.append(shp_data)
    except:
        pass
    
    return financial_data


def generate_financial_html(company_name, financial_data):
    """Generate dark-themed financial analysis HTML matching extension output"""
    
    html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Financial Analysis - {company_name}</title>
<style>
  body {{
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0f172a;
    color: #e2e8f0;
  }}
  .container {{ padding: 30px; }}
  h1 {{ font-size: 26px; margin-bottom: 30px; color: #f8fafc; }}
  h2 {{
    margin-top: 40px;
    border-bottom: 1px solid #1e293b;
    padding-bottom: 5px;
    color: #e2e8f0;
  }}
  h3 {{
    margin-top: 25px;
    font-size: 14px;
    color: #94a3b8;
    font-weight: 500;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 25px;
    background: #1e293b;
    border-radius: 8px;
    overflow: hidden;
    font-size: 13px;
  }}
  th {{
    background: #334155;
    padding: 10px 12px;
    font-size: 12px;
    font-weight: 600;
    color: #cbd5e1;
    text-align: right;
  }}
  th:first-child {{ text-align: left; }}
  td {{
    padding: 8px 12px;
    text-align: right;
    border-bottom: 1px solid #334155;
  }}
  td:first-child {{
    text-align: left;
    font-weight: 600;
    color: #e2e8f0;
  }}
  tr:nth-child(even) {{ background: #1a2436; }}
  tr:hover {{ background: #253348; }}
  .pos {{ background: rgba(34,197,94,0.15); color: #4ade80; font-weight: 500; }}
  .neg {{ background: rgba(239,68,68,0.15); color: #f87171; font-weight: 500; }}
  .neutral {{ color: #64748b; }}
  .metric-label {{ color: #94a3b8; font-weight: 400; }}
</style>
</head>
<body>
<div class="container">
<h1>📊 Financial Analysis Report</h1>
<h2 style="border:none; margin-top:0; color:#94a3b8;">{company_name}</h2>
'''
    
    for section in financial_data:
        html += f'<h2>{section["name"]}</h2>\n'
        
        for row in section['rows']:
            html += f'<h3>{row["label"]}</h3>\n'
            html += '<table>\n'
            
            html += '<tr><th>Metric</th>'
            for h in section['headers']:
                html += f'<th>{h}</th>'
            html += '</tr>\n'
            
            html += f'<tr><td>{row["label"]}</td>'
            for v in row['values']:
                html += f'<td>{v}</td>'
            html += '</tr>\n'
            
            if 'qoq' in row:
                html += '<tr><td class="metric-label">QoQ</td>'
                for c in row['qoq']:
                    html += f'<td class="{c["type"]}">{c["text"]}</td>'
                html += '</tr>\n'
            
            change_key = 'yoy' if 'yoy' in row else 'change'
            if change_key in row:
                label = 'YoY' if change_key == 'yoy' else 'Change'
                html += f'<tr><td class="metric-label">{label}</td>'
                for c in row[change_key]:
                    html += f'<td class="{c["type"]}">{c["text"]}</td>'
                html += '</tr>\n'
            
            html += '</table>\n'
    
    html += '''</div>
</body>
</html>'''
    
    return html


def expand_all_sections(page):
    """Click all + buttons to expand tables"""
    print("\n📊 EXPANDING ALL SECTIONS...")
    
    section_ids = ["#quarters", "#profit-loss", "#balance-sheet", "#cash-flow", "#ratios"]
    
    for section_id in section_ids:
        prev_count = -1
        while True:
            try:
                buttons = page.locator(f'{section_id} button').all()
                plus_buttons = []
                for btn in buttons:
                    try:
                        text = btn.inner_text()
                        if '+' in text:
                            plus_buttons.append(btn)
                    except:
                        pass
                
                current_count = len(plus_buttons)
                if current_count == 0 or current_count == prev_count:
                    break
                
                prev_count = current_count
                for btn in plus_buttons:
                    try:
                        btn.click()
                        time.sleep(0.3)
                    except:
                        pass
                
                time.sleep(0.5)
            except:
                break
        
        print(f"  ✅ Expanded {section_id}")
    
    # Click Max on chart
    print("\n📈 CLICKING MAX ON CHART...")
    try:
        max_button = page.locator('button[name="days"][value="10000"]')
        if max_button.count() > 0:
            max_button.first.click()
            time.sleep(1)
            print("  ✅ Max chart selected")
    except:
        print("  ⚠️ Max button not found")


def expand_and_save_analysis(company_url, soup, save_dir, safe_company, company_name):
    """Expand all sections, extract financials, and save analysis as PDF"""
    print("\n" + "="*70)
    print("📊 EXPANDING & GENERATING FINANCIAL ANALYSIS")
    print("="*70)
    
    analysis_pdf_path = os.path.join(save_dir, f"{safe_company}_Financial_Analysis.pdf")
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=['--ignore-certificate-errors', '--disable-web-security', '--no-sandbox']
            )
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/147.0.0.0 Safari/537.36'
            )
            page = context.new_page()
            
            # Navigate to company page
            print(f"\n🌐 Loading: {company_url}")
            try:
                page.goto(company_url, wait_until="load", timeout=45000)
                page.wait_for_timeout(3000)
                print("✅ Page loaded")
            except:
                page.wait_for_timeout(8000)
            
            # Expand all sections
            expand_all_sections(page)
            
            # Wait for everything to settle
            page.wait_for_timeout(2000)
            
            # Extract financial data
            print("\n📊 EXTRACTING FINANCIAL DATA...")
            financial_data = extract_financial_data(page)
            print(f"✅ Extracted {len(financial_data)} sections")
            
            # Generate analysis HTML
            print("\n📝 GENERATING FINANCIAL ANALYSIS...")
            analysis_html = generate_financial_html(company_name, financial_data)
            
            # Load HTML and save as PDF
            print("\n📄 SAVING ANALYSIS AS PDF...")
            print(f"  Settings:")
            print(f"    Pages: All")
            print(f"    Layout: Landscape")
            print(f"    Paper size: Legal")
            print(f"    Pages per sheet: 1")
            print(f"    Margins: Default")
            print(f"    Scale: Default")
            
            page.set_content(analysis_html, wait_until="load")
            page.wait_for_timeout(1000)
            
            page.pdf(
                path=analysis_pdf_path,
                format='Legal',
                landscape=True,
                print_background=True,
                margin={'top': '0.4in', 'bottom': '0.4in', 'left': '0.4in', 'right': '0.4in'},
                scale=1.0
            )
            print(f"✅ Analysis PDF saved: {analysis_pdf_path}")
            
            browser.close()
            
            return analysis_pdf_path
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return None


# ==================================================
# SAVE ALL NOTES
# ==================================================

def save_all_notes(save_dir, safe_company, company_name, company_url,
                   recent_announcements, important_announcements,
                   rpt_data, corporate_actions, trades_data, profile_data):
    
    # 1. COMPANY PROFILE
    if profile_data.get('about') or profile_data.get('key_points'):
        profile_notes = []
        profile_notes.append("=" * 80)
        profile_notes.append(f"COMPANY PROFILE - {company_name}")
        profile_notes.append("=" * 80)
        profile_notes.append("")
        
        if profile_data.get('about'):
            profile_notes.append("-" * 60)
            profile_notes.append("ABOUT")
            profile_notes.append("-" * 60)
            profile_notes.append(profile_data['about'])
            profile_notes.append("")
        
        if profile_data.get('key_points'):
            profile_notes.append("-" * 60)
            profile_notes.append("KEY POINTS")
            profile_notes.append("-" * 60)
            profile_notes.append(profile_data['key_points'])
            profile_notes.append("")
        
        profile_file = os.path.join(save_dir, f"{safe_company}_profile.txt")
        with open(profile_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(profile_notes))
        print(f"📝 Profile: {profile_file}")
    
    # 2. RELATED PARTY TRANSACTIONS
    if rpt_data and rpt_data.get('data'):
        rpt_text = format_rpt_data(rpt_data['data'])
        rpt_file = os.path.join(save_dir, f"{safe_company}_related_party.txt")
        with open(rpt_file, 'w', encoding='utf-8') as f:
            f.write(rpt_text)
        print(f"📝 RPT: {rpt_file}")
    
    # 3. CORPORATE ACTIONS
    if corporate_actions:
        ca_notes = []
        ca_notes.append("=" * 80)
        ca_notes.append(f"CORPORATE ACTIONS - {company_name}")
        ca_notes.append("=" * 80)
        ca_notes.append("")
        
        action_tabs = [
            ('equity_history', 'EQUITY HISTORY'), ('esops', 'ESOPs'),
            ('dividend', 'DIVIDEND'), ('buyback', 'BUY BACK'),
            ('bonus', 'BONUS'), ('merger', 'MERGER'), ('split', 'SPLIT'),
        ]
        
        for key, label in action_tabs:
            entries = corporate_actions.get(key, [])
            if entries:
                ca_notes.append("-" * 60)
                ca_notes.append(f"{label} ({len(entries)} entries)")
                ca_notes.append("-" * 60)
                for i, entry in enumerate(entries, 1):
                    ca_notes.append(f"  {i}. Date: {entry['date']}")
                    ca_notes.append(f"     Details: {entry['details']}")
                    ca_notes.append("")
        
        ca_file = os.path.join(save_dir, f"{safe_company}_corporate_actions.txt")
        with open(ca_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(ca_notes))
        print(f"📝 Corporate Actions: {ca_file}")
    
    # 4. TRADES DATA
    if trades_data:
        trade_notes = []
        trade_notes.append("=" * 80)
        trade_notes.append(f"TRADES DATA - {company_name}")
        trade_notes.append("=" * 80)
        trade_notes.append("")
        
        trade_labels = {
            'insider_trades': ('INSIDER TRADES', 'insider'),
            'bulk_deals': ('BULK DEALS', 'bulk'),
            'block_deals': ('BLOCK DEALS', 'block'),
            'sast_trades': ('SAST TRADES', 'sast')
        }
        
        for key, (label, trade_type) in trade_labels.items():
            entries = trades_data.get(key, [])
            trade_notes.append("-" * 60)
            trade_notes.append(f"{label} ({len(entries)} entries)")
            trade_notes.append("-" * 60)
            
            if trade_type == 'sast':
                trade_notes.append("  Name | Transaction | Mode | Quantity | Percent")
            elif trade_type in ['bulk', 'block']:
                trade_notes.append("  Name | Transaction | Quantity | Price")
            elif trade_type == 'insider':
                trade_notes.append("  Name | Quantity | Avg Price | Value (₹ Lacs)")
            trade_notes.append("")
            
            if entries:
                for i, entry in enumerate(entries, 1):
                    trade_notes.append(f"  {i}. Date: {entry['date']}")
                    trade_notes.append(f"     {entry['details']}")
                    trade_notes.append("")
            else:
                trade_notes.append("  No entries found")
                trade_notes.append("")
        
        trade_file = os.path.join(save_dir, f"{safe_company}_trades.txt")
        with open(trade_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(trade_notes))
        print(f"📝 Trades: {trade_file}")


# ==================================================
# MAIN FUNCTION
# ==================================================

def process_company_url(company_url):
    global IS_LOGGED_IN
    
    print("\n" + "="*70)
    print("DOWNLOADING COMPANY DOCUMENTS")
    print("="*70)
    print(f"Current UTC Time: {NOW.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Announcement Window: Last {MONTHS_BACK} months (since {CUTOFF_DATE.strftime('%d %b %Y')})")
    print("="*70)
    
    # Initialize login session with hardcoded credentials
    init_login_session()
    
    response = safe_get(company_url)
    if not response:
        print("Failed to fetch company page")
        return
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    h1 = soup.select_one('h1')
    company_name = h1.text.strip() if h1 else "Unknown_Company"
    print(f"Company: {company_name}")
    
    safe_company = sanitize_filename(company_name.replace(" ", "_"))
    save_dir = f"downloads/{safe_company}"
    os.makedirs(save_dir, exist_ok=True)
    
    # Extract company profile
    profile_data = extract_company_profile(soup)
    
    # ==========================================
    # EXPAND & GENERATE FINANCIAL ANALYSIS PDF
    # ==========================================
    analysis_pdf = expand_and_save_analysis(
        company_url, soup, save_dir, safe_company, company_name
    )
    
    # ==========================================
    # GATHER PUBLIC DOCUMENTS
    # ==========================================
    print("\n" + "-"*50)
    print("📄 GATHERING PUBLIC DOCUMENT LINKS")
    print("-"*50)
    
    quarterly_pdfs = fetch_quarterly_pdfs(soup, company_name)
    print(f"  • Quarterly Report links found: {len(quarterly_pdfs)}")
    
    annual_reports = fetch_annual_report_pdfs(company_url)
    concall_materials = fetch_concall_materials(company_url)
    credit_ratings = fetch_credit_ratings(company_url)
    
    print(f"  • Annual Reports found: {len(annual_reports)}")
    print(f"  • Concall Materials found: {len(concall_materials)}")
    print(f"  • Credit Ratings found: {len(credit_ratings)}")
    
    recent_announcements = parse_recent_announcements(soup)
    print(f"  • Recent Announcements (within {MONTHS_BACK} months): {len(recent_announcements)}")
    
    # ==========================================
    # FETCH LOGIN-REQUIRED DATA
    # ==========================================
    print("\n" + "="*70)
    print("🔐 FETCHING LOGIN-REQUIRED DATA")
    print("="*70)
    print("(Using hardcoded credentials)\n")
    
    important_announcements = fetch_important_announcements(company_url, soup)
    print(f"  • Important Announcements (within {MONTHS_BACK} months): {len(important_announcements)}")
    
    rpt_data = fetch_related_party_transactions(company_url, soup)
    corporate_actions = fetch_corporate_actions(company_url, soup)
    trades_data = fetch_trades_data(company_url, soup)
    
    save_all_notes(save_dir, safe_company, company_name, company_url,
                  recent_announcements, important_announcements,
                  rpt_data, corporate_actions, trades_data, profile_data)
    
    # Logout
    logout()
    
    # Combine all documents
    docs = quarterly_pdfs + annual_reports + concall_materials + credit_ratings + recent_announcements + important_announcements

    total_links = len(docs)
    print(f"\n{'='*70}")
    print(f"📊 DOCUMENT STATISTICS")
    print(f"{'='*70}")
    print(f"  Total links found on page: {total_links}")

    # Group by unique key
    text_map = OrderedDict()
    ignored_duplicates = {}
    ignored_duplicates_urls = {}  # Track URLs for ignored duplicates
    
    for doc in docs:
        if "Announcement" in doc.get("type", ""):
            text = doc.get("unique_key", doc.get("title", "")).strip()
        else:
            text = doc["text"].strip() if "text" in doc else ""
        
        url = doc["url"].strip()
        if text in text_map:
            if url in text_map[text]["urls"]:
                if text not in ignored_duplicates:
                    ignored_duplicates[text] = []
                    ignored_duplicates_urls[text] = []
                ignored_duplicates[text].append(url)
                ignored_duplicates_urls[text].append({"text": text, "url": url})
            else:
                text_map[text]["urls"].append(url)
        else:
            text_map[text] = {"type": doc.get("type", ""), "text": text, "urls": [url]}

    duplicates_map = {text: entry["urls"] for text, entry in text_map.items() if len(entry["urls"]) > 1}
    duplicates_count = sum(len(urls) - 1 for urls in duplicates_map.values())
    ignored_count = sum(len(urls) for urls in ignored_duplicates.values())
    unique_docs = list(text_map.values())

    unique_count = len(unique_docs)
    effective_total = total_links - ignored_count
    
    print(f"  ├─ Unique document labels: {unique_count}")
    if duplicates_count > 0:
        print(f"  ├─ Different duplicates (will download with suffix): {duplicates_count}")
    if ignored_count > 0:
        print(f"  ├─ Identical duplicates ignored: {ignored_count}")
    print(f"  └─ Will attempt to download: {effective_total} files")

    successful_count = 0
    failed_count = 0
    zip_info = []
    failures = []
    recent_ann_count = 0
    important_ann_count = 0

    print("\n" + "-"*50)
    print("⬇️ DOWNLOADING DOCUMENTS")
    print("-"*50)

    ann_counter = {}

    for doc in unique_docs:
        doc_type = doc.get("type", "")
        
        if "Announcement" in doc_type:
            base_text = doc["text"]
            date_match = re.match(r'(\d{4})(\d{2})(\d{2})', base_text[:8]) if base_text else None
            
            if date_match:
                date_part = f"{date_match.group(1)}{date_match.group(2)}{date_match.group(3)}"
                short_title = sanitize_filename(base_text[9:])[:50] if len(base_text) > 9 else "ann"
            else:
                date_part = NOW.strftime('%Y%m%d')
                short_title = sanitize_filename(base_text)[:50]
            
            ann_prefix = "Important" if "Important" in doc_type else "Recent"
            base_filename = f"{safe_company}_{ann_prefix}_{date_part}_{short_title}"
            
            if base_filename in ann_counter:
                ann_counter[base_filename] += 1
                filename = f"{base_filename}_{ann_counter[base_filename]}.pdf"
            else:
                ann_counter[base_filename] = 0
                filename = f"{base_filename}.pdf"
        else:
            base_text = sanitize_filename(doc["text"])
        
        for idx, url in enumerate(doc["urls"], start=1):
            if "Announcement" in doc_type:
                if len(doc["urls"]) > 1 and idx > 1:
                    base, ext = os.path.splitext(filename)
                    filename = f"{base}_v{idx}{ext}"
            else:
                tag = f"_duplicate_{idx-1}" if idx > 1 and len(doc["urls"]) > 1 else ""
                ext = '.zip' if url.lower().endswith('.zip') else '.pdf'
                filename = f"{safe_company}_{base_text}{tag}{ext}"
            
            print(f"  [{successful_count + failed_count + 1}/{effective_total}] {filename[:100]}...")
            
            success, is_zip, failure_reason = download_document(url, save_dir, filename)

            if success:
                successful_count += 1
                if is_zip:
                    base_name = os.path.splitext(filename)[0]
                    extracted_count = len([f for f in os.listdir(save_dir)
                                         if f.startswith(base_name + "_") and f != filename])
                    zip_info.append((filename, extracted_count))
                
                if "Announcement (Recent)" in doc_type:
                    recent_ann_count += 1
                elif "Announcement (Important)" in doc_type:
                    important_ann_count += 1
            else:
                failed_count += 1
                failures.append((filename, failure_reason, url))
                with open("dead_links.txt", "a") as f:
                    f.write(f"{url} # DOWNLOAD FAILED: {failure_reason}\n")

    # Summary
    total_files = len([f for f in os.listdir(save_dir) if os.path.isfile(os.path.join(save_dir, f))])
    total_extracted = sum(count for _, count in zip_info)
    total_zips = len(zip_info)

    print("\n" + "="*70)
    print("DOWNLOAD SUMMARY REPORT")
    print("="*70)
    print(f"\n📊 DETAILED BREAKDOWN:")
    print(f"  Total links found on page:          {total_links}")
    print(f"  ├─ Unique document labels:           {unique_count}")
    if duplicates_count > 0:
        print(f"  ├─ Different duplicates:             {duplicates_count}")
    if ignored_count > 0:
        print(f"  ├─ Identical duplicates ignored:     {ignored_count}")
    print(f"  └─ URLs to download:                 {effective_total}")
    
    single_file_count = effective_total - failed_count - total_zips
    
    print(f"\n  Successfully downloaded:             {successful_count}/{effective_total}")
    print(f"  ├─ Single files:                     {single_file_count}")
    if total_zips > 0:
        print(f"  ├─ ZIP files downloaded:             {total_zips}")
        print(f"  └─ Files extracted from ZIPs:        {total_extracted}")
    print(f"  ──────────────────────────────────────────")
    print(f"  Total files in folder:               {total_files}")
    
    success_rate = (successful_count/effective_total*100) if effective_total > 0 else 0
    print(f"\n📈 Download success rate:              {success_rate:.1f}%")
    
    print(f"\n📋 DOCUMENT TYPE BREAKDOWN:")
    print(f"  • Quarterly Reports:    {len(quarterly_pdfs)}")
    print(f"  • Annual Reports:       {len(annual_reports)}")
    print(f"  • Concall Materials:    {len(concall_materials)}")
    print(f"  • Credit Ratings:       {len(credit_ratings)}")
    print(f"  • Recent Announcements: {len(recent_announcements)} found, {recent_ann_count} downloaded")
    print(f"  • Important Announcements: {len(important_announcements)} found, {important_ann_count} downloaded")
    
    print(f"\n📊 GENERATED FILES:")
    print(f"  • Financial Analysis PDF: {'✅' if analysis_pdf else '❌'}")
    
    print(f"\n📊 SUPPLEMENTARY DATA:")
    print(f"  • Profile:              {'✅' if profile_data.get('about') else '❌'}")
    print(f"  • Related Party:        {'✅' if rpt_data else '❌'}")
    print(f"  • Corporate Actions:    {'✅' if corporate_actions else '❌'}")
    print(f"  • Trades Data:          {'✅' if trades_data else '❌'}")

    if failures:
        print(f"\n❌ FAILED DOWNLOADS ({len(failures)}):")
        for fname, reason, url in failures[:5]:
            print(f"  ✗ {fname[:80]}...")
            print(f"    └─ Reason: {reason}")
            print(f"    └─ URL: {url}")
        if len(failures) > 5:
            print(f"  ... and {len(failures) - 5} more")
    
    if zip_info:
        print(f"\n📦 ZIP FILES EXTRACTED ({len(zip_info)}):")
        for zip_name, extract_count in zip_info:
            print(f"  ✓ {zip_name} → {extract_count} files")

    if duplicates_map:
        print(f"\n🔄 DUPLICATE URLS DOWNLOADED ({duplicates_count}):")
        for text, urls in duplicates_map.items():
            for i, url in enumerate(urls):
                suffix = f" (duplicate {i})" if i > 0 else ""
                print(f"  • {text[:60]}{suffix}: {url}")

    if ignored_duplicates:
        print(f"\n⏭️  IDENTICAL DUPLICATES IGNORED ({ignored_count}):")
        for text, url_items in ignored_duplicates_urls.items():
            print(f"  - \"{text[:60]}\": {len(url_items)} identical URL{'s' if len(url_items)>1 else ''}")
            for item in url_items:
                print(f"    • {item['url']}")

    if IS_LOGGED_IN:
        print(f"\n🔒 Session: LOGGED OUT (safe)")

    print("\n" + "="*70)
    print(f"✅ COMPLETE - {save_dir}")
    print("="*70)


# ==================================================
# CLI
# ==================================================

if __name__ == "__main__":
    if len(sys.argv) > 1:
        start_time = time.time()
        try:
            process_company_url(sys.argv[1])
        finally:
            if IS_LOGGED_IN:
                print("\n⚠️ Interrupted - logging out...")
                logout()
        
        elapsed = time.time() - start_time
        h, r = divmod(elapsed, 3600)
        m, s = divmod(r, 60)
        
        print(f"\n⏱️  Total time taken: {int(h)}h {int(m)}m {s:.1f}s" if h > 0 else f"\n⏱️  Total time taken: {int(m)}m {s:.1f}s" if m > 0 else f"\n⏱️  Total time taken: {s:.1f}s")
    else:
        print("Usage: python script.py https://www.screener.in/company/RATEGAIN/consolidated/")