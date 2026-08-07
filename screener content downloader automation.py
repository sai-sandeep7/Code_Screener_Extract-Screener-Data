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

CSRF_TOKEN = "sk8S2f2D54SimgGU22XxGOxHkN0fkhOT"
SESSION_ID = "yyfoo9p29l8heu8lcsrpm1gqv0cc2s5w"

MONTHS_BACK = 6
NOW = datetime.now(timezone.utc)
CUTOFF_DATE = NOW - timedelta(days=MONTHS_BACK * 30.44)

# Collect all failed URLs across batch
all_failed_urls = []

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
        return urlparse(url).hostname
    except:
        return None

def is_domain_problematic(url):
    return any(domain in url.lower() for domain in PROBLEMATIC_DOMAINS)

def should_skip_ssl_verification(url):
    return any(domain in url.lower() for domain in SSL_ISSUE_DOMAINS)

def sanitize_filename(name, max_length=150):
    name = " ".join(name.split())
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = name.replace('–', '-').replace('—', '-')
    name = name.replace("'", "").replace('"', "")
    name = name.encode('ascii', 'ignore').decode('ascii')
    return name[:max_length]

# ==================================================
# SESSION MANAGEMENT
# ==================================================

def create_session(verify_ssl=True, with_auth=False):
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
    
    if with_auth:
        session.cookies.set('csrftoken', CSRF_TOKEN, domain='.screener.in')
        session.cookies.set('sessionid', SESSION_ID, domain='.screener.in')
    
    return session

def login():
    print("  🔐 Creating authenticated session...")
    session = create_session(verify_ssl=False, with_auth=True)
    print("  ✅ Authenticated session ready")
    return session

def logout(session):
    if not session:
        return
    print("  🔓 Closing authenticated session...")
    try:
        headers = {'Referer': BASE + '/', 'X-CSRFToken': CSRF_TOKEN}
        session.get(LOGOUT_URL, headers=headers, timeout=5)
    except:
        pass
    finally:
        session.close()
    print("  ✅ Session closed")

def safe_get(url, session=None, stream=False, timeout=(10, 60)):
    if is_domain_problematic(url):
        with open("dead_links.txt", "a") as f:
            f.write(f"{url} # KNOWN PROBLEMATIC DOMAIN\n")
        return None
    
    hostname = extract_hostname(url)
    if hostname and not can_resolve_domain(hostname):
        with open("dead_links.txt", "a") as f:
            f.write(f"{url} # DNS FAILURE\n")
        return None
    
    if session is None:
        session = create_session(verify_ssl=not should_skip_ssl_verification(url))
        close_after = True
    else:
        close_after = False
    
    try:
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
    finally:
        if close_after:
            session.close()

def safe_get_auth(url, session, timeout=(10, 60), referer=None):
    if not session:
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
            r = session.get(url, timeout=timeout, headers=headers if headers else None)
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
# DATA EXTRACTION - HELPERS
# ==================================================

def extract_company_id(soup):
    for button in soup.find_all('button', onclick=True):
        match = re.search(r'/announcements/(?:recent|important)/(\d+)/', button.get('onclick', ''))
        if match:
            return match.group(1)
    for button in soup.find_all('button', {'data-url': True}):
        data_url = button.get('data-url', '')
        for pattern in [r'/company/actions/(\d+)/', r'/results/rpt/(\d+)/', r'/trades/company-(\d+)/']:
            match = re.search(pattern, data_url)
            if match:
                return match.group(1)
    return None

def parse_relative_time(time_str):
    now = NOW
    time_str = time_str.strip()
    
    match = re.match(r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})', time_str, re.I)
    if match:
        month_map = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}
        return datetime(int(match.group(3)), month_map[match.group(2).lower()], int(match.group(1)), tzinfo=timezone.utc)
    
    match = re.match(r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', time_str, re.I)
    if match:
        month_map = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}
        parsed_date = datetime(now.year, month_map[match.group(2).lower()], int(match.group(1)), tzinfo=timezone.utc)
        if parsed_date > now:
            parsed_date = datetime(now.year - 1, month_map[match.group(2).lower()], int(match.group(1)), tzinfo=timezone.utc)
        return parsed_date
    
    match = re.match(r'(\d+)\s*(s|m|h|d|w)', time_str, re.I)
    if match:
        value = int(match.group(1))
        unit = match.group(2).lower()
        units = {'s':'seconds','m':'minutes','h':'hours','d':'days','w':'weeks'}
        return now - timedelta(**{units[unit]: value})
    
    return None

def is_within_window(date_obj):
    if not date_obj:
        return False
    
    cutoff = NOW
    for _ in range(MONTHS_BACK):
        if cutoff.month == 1:
            cutoff = cutoff.replace(year=cutoff.year - 1, month=12)
        else:
            cutoff = cutoff.replace(month=cutoff.month - 1)
    
    try:
        cutoff = cutoff.replace(day=NOW.day)
    except ValueError:
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
# PUBLIC DATA (NO LOGIN REQUIRED)
# ==================================================

def get_recent_announcements(soup):
    results = []
    container = soup.find('div', id='company-announcements-tab')
    if container:
        for li in container.find_all('li', class_='overflow-wrap-anywhere'):
            link = li.find('a', href=True)
            time_div = li.find('div', class_='ink-600')
            if not link or not time_div or not link['href'].endswith('.pdf'):
                continue
            
            time_text = time_div.text.strip()
            title = " ".join(link.text.strip().split())
            parsed_date = parse_relative_time(time_text)
            
            if parsed_date and is_within_window(parsed_date):
                pdf_url = urljoin(BASE, link['href']) if not link['href'].startswith('http') else link['href']
                date_str = parsed_date.strftime('%Y%m%d')
                short_title = clean_announcement_title(title)
                unique_key = f"{date_str}_{short_title[:50]}"
                
                results.append({
                    'type': 'Recent Announcement',
                    'title': title,
                    'url': pdf_url,
                    'date': parsed_date,
                    'text': title,
                    'unique_key': unique_key,
                    'date_str': parsed_date.strftime('%d_%b_%Y')
                })
    return results

def get_quarterly_pdfs(soup, company_name):
    results = []
    for link in soup.select('a[href*="/source/quarter/"]'):
        href = link.get('href')
        if href:
            match = re.search(r'/quarter/\d+/(\d+)/(\d+)/', href)
            if match:
                month_names = {'1':'January','2':'February','3':'March','4':'April',
                              '5':'May','6':'June','7':'July','8':'August',
                              '9':'September','10':'October','11':'November','12':'December'}
                month = month_names.get(match.group(1), match.group(1))
                results.append({
                    'type': 'Quarterly Report',
                    'text': f"{month} {match.group(2)} Quarterly Report",
                    'url': urljoin(BASE, href)
                })
    return results

def get_annual_reports(url):
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    h3 = soup.find("h3", string=lambda x: x and "Annual reports" in x)
    if h3:
        for link in h3.find_parent().find_all("a", href=True):
            text = " ".join(link.text.split())
            href = link["href"]
            if any(year in text for year in FINANCIAL_YEARS) and (href.endswith(".pdf") or href.endswith(".zip")):
                full = href if href.startswith("http") else BASE + href
                results.append({"type": "Annual Report", "text": text, "url": full})
    return results

def get_concall_materials(url):
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    section = soup.select_one("div.documents.concalls.flex-column")
    if section:
        month_pattern = re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}", re.I)
        for div in section.find_all("div", string=lambda x: x and month_pattern.search(x)):
            date_text = " ".join(div.text.split())
            row = div.find_parent()
            for link in row.find_all("a", class_="concall-link", href=True):
                label = link.text.strip().upper()
                if label in ["TRANSCRIPT", "PPT"]:
                    results.append({"type": f"Concall {label}", "text": f"{date_text} {label}", "url": link["href"]})
    return results

def get_credit_ratings(url):
    r = safe_get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    section = soup.select_one("div.documents.credit-ratings.flex-column")
    if section:
        for li in section.find_all("li"):
            link = li.find("a", href=True)
            if link:
                name = li.select_one("div.ink-600.smaller")
                text = " ".join((name.text if name else link.text).split())
                full = link["href"]
                if not full.startswith("http"):
                    full = BASE + full
                results.append({"type": "Credit Rating", "text": text, "url": full})
    return results

# ==================================================
# LOGIN-REQUIRED DATA
# ==================================================

def get_important_announcements(soup, company_url, auth_session):
    if not auth_session:
        return []
    
    results = []
    company_id = extract_company_id(soup)
    if not company_id:
        return []
    
    url = f"{BASE}/announcements/important/{company_id}/"
    r = safe_get_auth(url, auth_session, referer=company_url)
    if not r:
        return []
    
    time.sleep(random.uniform(1, 2))
    imp_soup = BeautifulSoup(r.text, 'html.parser')
    
    for li in imp_soup.find_all('li', class_='overflow-wrap-anywhere'):
        link = li.find('a', href=True)
        time_div = li.find('div', class_='ink-600')
        if not link or not time_div or not link['href'].endswith('.pdf'):
            continue
        
        time_text = time_div.text.strip()
        title = " ".join(link.text.strip().split())
        parsed_date = parse_relative_time(time_text)
        
        if parsed_date and is_within_window(parsed_date):
            pdf_url = urljoin(BASE, link['href']) if not link['href'].startswith('http') else link['href']
            date_str = parsed_date.strftime('%Y%m%d')
            short_title = clean_announcement_title(title)
            unique_key = f"{date_str}_{short_title[:50]}"
            
            results.append({
                'type': 'Important Announcement',
                'title': title,
                'url': pdf_url,
                'date': parsed_date,
                'text': title,
                'unique_key': unique_key,
                'date_str': parsed_date.strftime('%d_%b_%Y')
            })
    
    return results

def get_company_profile(company_url, auth_session):
    if not auth_session:
        return {'about': '', 'key_points': ''}
    
    r = safe_get(company_url, session=auth_session)
    if not r:
        return {'about': '', 'key_points': ''}
    
    soup = BeautifulSoup(r.text, 'html.parser')
    profile = {'about': '', 'key_points': ''}
    
    about_div = soup.find('div', class_='sub show-more-box about')
    if about_div:
        p = about_div.find('p')
        if p:
            profile['about'] = p.get_text('\n', strip=True)
    
    kp_div = soup.find('div', class_='sub commentary always-show-more-box')
    if kp_div:
        p = kp_div.find('p')
        if p:
            profile['key_points'] = p.get_text('\n', strip=True)
    
    return profile

def get_related_party_transactions(soup, company_url, auth_session):
    if not auth_session:
        return None
    
    company_id = extract_company_id(soup)
    if not company_id:
        return None
    
    for pattern in [f"{BASE}/results/rpt/{company_id}/consolidated/", f"{BASE}/results/rpt/{company_id}/"]:
        r = safe_get_auth(pattern, auth_session, referer=company_url)
        if r and r.status_code == 200:
            if 'text/html' in r.headers.get('content-type', '').lower():
                test_soup = BeautifulSoup(r.text, 'html.parser')
                table = test_soup.find('table')
                if table and len(table.find_all('tr')) >= 2:
                    return format_rpt_data(r.text)
        time.sleep(random.uniform(1, 2))
    
    return None

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
    years = []
    if rows:
        year_cells = rows[0].find_all('th')
        years = [cell.get_text(strip=True) for cell in year_cells[1:]]
    
    current_person = None
    person_transactions = OrderedDict()
    
    for row in rows[1:]:
        cells = row.find_all('td')
        if not cells:
            continue
        
        first_cell = cells[0]
        colspan = first_cell.get('colspan')
        if colspan:
            try:
                if int(colspan) >= 10:
                    text_content = first_cell.get_text(strip=True)
                    small_tag = first_cell.find('small', class_='ink-600')
                    if small_tag:
                        role = small_tag.get_text(strip=True)
                        name = text_content.replace(role, '').strip()
                        current_person = f"{name}: {role}" if role else name
                    else:
                        current_person = text_content
                    
                    if current_person and current_person not in person_transactions:
                        person_transactions[current_person] = []
                    continue
            except ValueError:
                pass
        
        is_person = False
        person_name = ""
        person_role = ""
        
        small_tag = first_cell.find('small', class_='ink-600')
        if small_tag:
            role = small_tag.get_text(strip=True)
            full_text = first_cell.get_text(strip=True)
            person_name = full_text.replace(role, '').strip()
            person_role = role
            is_person = True
        
        if not is_person:
            row_classes = row.get('class', [])
            if 'strong' in row_classes and 'stripe' in row_classes:
                text_content = first_cell.get_text(strip=True)
                if text_content and not re.search(r'\d', text_content):
                    person_name = text_content
                    is_person = True
        
        if is_person and person_name:
            current_person = f"{person_name}: {person_role}" if person_role else person_name
            if current_person not in person_transactions:
                person_transactions[current_person] = []
            continue
        
        if current_person:
            transaction_type = first_cell.get_text(strip=True)
            values = [c.get_text(strip=True) for c in cells[1:]]
            if any(v and v != '-' for v in values):
                person_transactions[current_person].append({
                    'type': transaction_type,
                    'values': values,
                    'years': years
                })
    
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
                lines.append(f"  {i}. {trans['type']}: {' | '.join(value_parts)}")
            else:
                lines.append(f"  {i}. {trans['type']}")
    
    return '\n'.join(lines) if person_transactions else "No related party transactions data available."

def get_corporate_actions(soup, company_url, auth_session):
    if not auth_session:
        return None
    
    company_id = extract_company_id(soup)
    if not company_id:
        return None
    
    url = f"{BASE}/company/actions/{company_id}/"
    r = safe_get_auth(url, auth_session, referer=company_url)
    if not r:
        return None
    
    time.sleep(random.uniform(1, 2))
    actions_soup = BeautifulSoup(r.text, 'html.parser')
    
    result = {}
    tabs = {
        'corporate-actions-equityhistory': 'Equity History',
        'corporate-actions-esops': 'ESOPs',
        'corporate-actions-dividend': 'Dividend',
        'corporate-actions-buyback': 'Buyback',
        'corporate-actions-bonus': 'Bonus',
        'corporate-actions-merger': 'Merger',
        'corporate-actions-split': 'Split',
    }
    
    for tab_id, label in tabs.items():
        tab_div = actions_soup.find('div', id=tab_id)
        if tab_div:
            entries = []
            for row in tab_div.find_all('tr'):
                cells = row.find_all('td')
                if len(cells) >= 2:
                    date_divs = cells[0].find_all('div')
                    date_text = " ".join(d.get_text(strip=True) for d in date_divs)
                    detail_cell = cells[1]
                    heading_div = detail_cell.find('div', class_='font-weight-500')
                    sub_div = detail_cell.find('div', class_='sub')
                    
                    if heading_div and sub_div:
                        heading = heading_div.get_text(strip=True)
                        sub_text = sub_div.get_text(strip=True)
                        details = f"{heading}: {sub_text}" if sub_text else heading
                    else:
                        details = detail_cell.get_text(strip=True)
                    
                    if date_text and details:
                        entries.append({'date': date_text, 'details': details})
            
            if entries:
                result[label] = entries
    
    return result if result else None

def get_trades_data(soup, company_url, auth_session):
    if not auth_session:
        return None
    
    company_id = extract_company_id(soup)
    if not company_id:
        return None
    
    url = f"{BASE}/trades/company-{company_id}/"
    r = safe_get_auth(url, auth_session, referer=company_url)
    if not r:
        return None
    
    time.sleep(random.uniform(1, 2))
    trades_soup = BeautifulSoup(r.text, 'html.parser')
    
    result = {}
    tab_mapping = {
        'trades-insider-trades': 'Insider Trades',
        'trades-bulk-deals': 'Bulk Deals',
        'trades-block-deals': 'Block Deals',
        'trades-sast-trades': 'SAST Trades',
    }
    
    for tab_id, label in tab_mapping.items():
        tab_div = trades_soup.find('div', id=tab_id)
        if tab_div:
            if 'Found no recent' in tab_div.get_text():
                continue
            
            table = tab_div.find('table', class_='data-table')
            if table:
                entries = []
                current_date = ""
                for row in table.find_all('tr'):
                    if 'stripe' in row.get('class', []) and 'sticky-2' in row.get('class', []):
                        date_cell = row.find('td')
                        if date_cell:
                            current_date = date_cell.get_text(strip=True)
                        continue
                    
                    if row.find('th'):
                        continue
                    
                    cells = row.find_all('td')
                    if cells:
                        details_parts = []
                        for cell in cells:
                            text = cell.get_text(strip=True)
                            if text and text != current_date:
                                details_parts.append(text)
                        details = " | ".join(details_parts)
                        if details and current_date:
                            entries.append({'date': current_date, 'details': details})
                
                if entries:
                    result[label] = entries
    
    return result if result else None

# ==================================================
# FINANCIAL ANALYSIS
# ==================================================

def extract_financial_data(page):
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
                res.append({'text': f'{sign}{diff_str} ({sign}{pct}%)', 'type': 'pos' if diff >= 0 else 'neg'})
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
                    result['rows'].append({'label': label, 'values': values, 'is_percent': is_percent})
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
    html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Financial Analysis - {company_name}</title>
<style>
  body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #e2e8f0; }}
  .container {{ padding: 30px; }}
  h1 {{ font-size: 26px; margin-bottom: 30px; color: #f8fafc; }}
  h2 {{ margin-top: 40px; border-bottom: 1px solid #1e293b; padding-bottom: 5px; color: #e2e8f0; }}
  h3 {{ margin-top: 25px; font-size: 14px; color: #94a3b8; font-weight: 500; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 25px; background: #1e293b; border-radius: 8px; overflow: hidden; font-size: 13px; }}
  th {{ background: #334155; padding: 10px 12px; font-size: 12px; font-weight: 600; color: #cbd5e1; text-align: right; }}
  th:first-child {{ text-align: left; }}
  td {{ padding: 8px 12px; text-align: right; border-bottom: 1px solid #334155; }}
  td:first-child {{ text-align: left; font-weight: 600; color: #e2e8f0; }}
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
            html += f'<h3>{row["label"]}</h3>\n<table>\n<tr><th>Metric</th>'
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
    html += '</div></body></html>'
    return html

def expand_all_sections(page):
    print("\n📊 EXPANDING ALL SECTIONS...")
    section_ids = ["#quarters", "#profit-loss", "#balance-sheet", "#cash-flow", "#ratios"]
    for section_id in section_ids:
        prev_count = -1
        while True:
            try:
                buttons = page.locator(f'{section_id} button').all()
                plus_buttons = [btn for btn in buttons if '+' in (btn.inner_text() or '')]
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
    print("\n📈 CLICKING MAX ON CHART...")
    try:
        max_button = page.locator('button[name="days"][value="10000"]')
        if max_button.count() > 0:
            max_button.first.click()
            time.sleep(1)
            print("  ✅ Max chart selected")
    except:
        print("  ⚠️ Max button not found")

def expand_and_save_analysis(company_url, save_dir, safe_company, company_name):
    print("\n" + "="*70)
    print("📊 GENERATING FINANCIAL ANALYSIS PDF")
    print("="*70)
    
    analysis_pdf_path = os.path.join(save_dir, f"{safe_company}_Financial_Analysis.pdf")
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--ignore-certificate-errors', '--disable-web-security', '--no-sandbox'])
            context = browser.new_context(viewport={'width': 1920, 'height': 1080}, user_agent=HEADERS["User-Agent"])
            page = context.new_page()
            
            print(f"\n🌐 Loading: {company_url}")
            try:
                page.goto(company_url, wait_until="load", timeout=45000)
                page.wait_for_timeout(3000)
                print("✅ Page loaded")
            except:
                page.wait_for_timeout(8000)
            
            expand_all_sections(page)
            page.wait_for_timeout(2000)
            
            print("\n📊 EXTRACTING FINANCIAL DATA...")
            financial_data = extract_financial_data(page)
            print(f"✅ Extracted {len(financial_data)} sections")
            
            print("\n📝 GENERATING ANALYSIS HTML...")
            analysis_html = generate_financial_html(company_name, financial_data)
            
            print("\n📄 SAVING AS PDF...")
            page.set_content(analysis_html, wait_until="load")
            page.wait_for_timeout(1000)
            page.pdf(path=analysis_pdf_path, format='Legal', landscape=True, print_background=True,
                    margin={'top': '0.4in', 'bottom': '0.4in', 'left': '0.4in', 'right': '0.4in'}, scale=1.0)
            print(f"✅ Financial Analysis PDF saved!")
            
            browser.close()
            return analysis_pdf_path
    except Exception as e:
        print(f"❌ Financial Analysis Error: {e}")
        import traceback
        traceback.print_exc()
        return None

# ==================================================
# DOWNLOAD FUNCTIONS
# ==================================================

def download_icra_pdf(show_url, save_path, session=None):
    match = re.search(r"Id=(\d+)", show_url)
    if not match:
        return False
    download_url = f"https://www.icra.in/Rating/GetRationalReportFilePdf?Id={match.group(1)}"
    r = safe_get(download_url, session=session, stream=True)
    if not r:
        return False
    with open(save_path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    return True

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

def download_document(url, save_dir, filename, session=None):
    os.makedirs(save_dir, exist_ok=True)
    filepath = os.path.join(save_dir, filename)
    
    if "icra.in" in url.lower():
        success = download_icra_pdf(url, filepath, session)
        return (success, False, None) if success else (False, False, "ICRA download failed")
    
    try:
        r = safe_get(url, session=session, stream=True)
    except Exception as e:
        return (False, False, f"Request error: {e}")
    
    if not r:
        return (False, False, "No response from server")
    
    content_type = r.headers.get("content-type", "").lower()
    
    if "application/pdf" in content_type:
        try:
            with open(filepath, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            time.sleep(random.uniform(2, 5))
            return (True, False, None)
        except requests.exceptions.ChunkedEncodingError as e:
            return (False, False, f"Incomplete download: {e}")
        except Exception as e:
            return (False, False, f"Write error: {e}")
    
    if "application/zip" in content_type or url.lower().endswith(".zip"):
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                for chunk in r.iter_content(8192):
                    tmp.write(chunk)
                tmp_path = tmp.name
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
                    print(f"    → Extracted: {renamed_file}")
            os.unlink(tmp_path)
            time.sleep(random.uniform(2, 5))
            return (True, True, None)
        except requests.exceptions.ChunkedEncodingError as e:
            return (False, True, f"Incomplete ZIP: {e}")
        except Exception as e:
            return (False, True, str(e))
    
    if "text/html" in content_type or url.lower().endswith(".html"):
        success = convert_html_to_pdf(url, filepath)
        return (success, False, None if success else "PDF conversion failed")
    
    return (False, False, f"Unknown format: {content_type}")

# ==================================================
# PROCESS SINGLE COMPANY
# ==================================================

def process_company(company_url):
    global all_failed_urls
    company_slug = company_url.split('/company/')[1].split('/')[0]
    
    print(f"\n{'='*70}")
    print(f"📌 [{company_slug}]({company_url})")
    print(f"{'='*70}\n")
    print(f"Window: Last {MONTHS_BACK} months (since {CUTOFF_DATE.strftime('%d %b %Y')})")
    print()
    
    # STEP 1: Fetch public page
    print("🌐 STEP 1: Fetching public page...")
    r = safe_get(company_url)
    if not r:
        print("  ❌ Failed to fetch company page")
        return False
    
    soup = BeautifulSoup(r.text, 'html.parser')
    h1 = soup.select_one('h1')
    company_name = h1.text.strip() if h1 else "Unknown"
    print(f"  ✅ Company: {company_name}\n")
    
    safe_name = sanitize_filename(company_name.replace(" ", "_"))
    save_dir = f"downloads/{safe_name}"
    os.makedirs(save_dir, exist_ok=True)
    
    # STEP 2: FINANCIAL ANALYSIS PDF (Public)
    print("📊 STEP 2: Generating Financial Analysis PDF...")
    analysis_pdf = expand_and_save_analysis(company_url, save_dir, safe_name, company_name)
    if analysis_pdf:
        print(f"  ✅ Financial Analysis: {analysis_pdf}\n")
    else:
        print(f"  ⚠️ Financial Analysis: Skipped\n")
    
    # STEP 3: Collect PUBLIC documents
    print("📄 STEP 3: Collecting PUBLIC documents (no login)...")
    print("-" * 50)
    
    quarterly = get_quarterly_pdfs(soup, company_name)
    print(f"  🌐 Quarterly Reports: {len(quarterly)}")
    
    annual = get_annual_reports(company_url)
    print(f"  🌐 Annual Reports: {len(annual)}")
    
    concall = get_concall_materials(company_url)
    print(f"  🌐 Concall Materials: {len(concall)}")
    
    ratings = get_credit_ratings(company_url)
    print(f"  🌐 Credit Ratings: {len(ratings)}")
    
    recent_ann = get_recent_announcements(soup)
    print(f"  🌐 Recent Announcements: {len(recent_ann)}")
    
    public_docs = quarterly + annual + concall + ratings + recent_ann
    print(f"  📦 Public total: {len(public_docs)}\n")
    
    # STEP 4: LOGIN for authenticated data
    print("🔐 STEP 4: LOGIN for authenticated data...")
    print("-" * 50)
    
    auth_session = login()
    login_pdf_docs = []
    profile_text = ""
    rpt_text = ""
    ca_text = ""
    trades_text = ""
    
    try:
        important_ann = get_important_announcements(soup, company_url, auth_session)
        login_pdf_docs = important_ann  # These are the login-required PDFs
        print(f"  🔒 Important Announcements: {len(important_ann)}")
        
        profile = get_company_profile(company_url, auth_session)
        if profile['about'] or profile['key_points']:
            profile_parts = [f"{'='*80}", f"COMPANY PROFILE - {company_name}", f"{'='*80}", ""]
            if profile['about']:
                profile_parts.extend(["-"*60, "ABOUT", "-"*60, profile['about'], ""])
            if profile['key_points']:
                profile_parts.extend(["-"*60, "KEY POINTS", "-"*60, profile['key_points'], ""])
            profile_text = '\n'.join(profile_parts)
        print(f"  🔒 Profile: {'Found' if profile_text else 'Not available'}")
        
        rpt_data = get_related_party_transactions(soup, company_url, auth_session)
        if rpt_data:
            rpt_text = rpt_data
        print(f"  🔒 Related Party Transactions: {'Found' if rpt_text else 'Not available'}")
        
        ca_data = get_corporate_actions(soup, company_url, auth_session)
        if ca_data:
            ca_parts = [f"{'='*80}", f"CORPORATE ACTIONS - {company_name}", f"{'='*80}", ""]
            for label, entries in ca_data.items():
                ca_parts.extend(["-"*60, f"{label} ({len(entries)} entries)", "-"*60])
                for i, entry in enumerate(entries, 1):
                    ca_parts.append(f"  {i}. {entry['date']}: {entry['details']}")
                ca_parts.append("")
            ca_text = '\n'.join(ca_parts)
        print(f"  🔒 Corporate Actions: {f'{len(ca_data)} categories' if ca_data else 'Not available'}")
        
        trades_data = get_trades_data(soup, company_url, auth_session)
        if trades_data:
            trades_parts = [f"{'='*80}", f"TRADES DATA - {company_name}", f"{'='*80}", ""]
            for label, entries in trades_data.items():
                trades_parts.extend(["-"*60, f"{label} ({len(entries)} entries)", "-"*60, ""])
                for i, entry in enumerate(entries, 1):
                    trades_parts.append(f"  {i}. {entry['date']}: {entry['details']}")
                trades_parts.append("")
            trades_text = '\n'.join(trades_parts)
        print(f"  🔒 Trades Data: {f'{len(trades_data)} categories' if trades_data else 'Not available'}")
        
        print(f"  📦 Login-required PDFs: {len(login_pdf_docs)}\n")
        
        # STEP 5: Save text data
        print("💾 STEP 5: Saving text data...")
        print("-" * 50)
        
        if profile_text:
            with open(os.path.join(save_dir, f"{safe_name}_Profile.txt"), 'w', encoding='utf-8') as f:
                f.write(profile_text)
            print(f"  ✅ Profile saved")
        
        if rpt_text:
            with open(os.path.join(save_dir, f"{safe_name}_Related_Party.txt"), 'w', encoding='utf-8') as f:
                f.write(rpt_text)
            print(f"  ✅ RPT saved")
        
        if ca_text:
            with open(os.path.join(save_dir, f"{safe_name}_Corporate_Actions.txt"), 'w', encoding='utf-8') as f:
                f.write(ca_text)
            print(f"  ✅ Corporate Actions saved")
        
        if trades_text:
            with open(os.path.join(save_dir, f"{safe_name}_Trades.txt"), 'w', encoding='utf-8') as f:
                f.write(trades_text)
            print(f"  ✅ Trades saved")
        
        # STEP 6: Download Important Announcements (AUTH REQUIRED) before logout
        if login_pdf_docs:
            print(f"\n🔒 STEP 6: Downloading {len(login_pdf_docs)} Important Announcements (with auth)...")
            print("-" * 50)
            successful_imp = 0
            failed_imp = 0
            ann_counter = {}
            for idx, doc in enumerate(login_pdf_docs, 1):
                text = doc.get("text", "")
                date_match = re.match(r'(\d{4})(\d{2})(\d{2})', text[:8]) if text else None
                date_part = f"{date_match.group(1)}{date_match.group(2)}{date_match.group(3)}" if date_match else NOW.strftime('%Y%m%d')
                short_title = sanitize_filename(text[9:])[:50] if len(text) > 9 else "ann"
                base_filename = f"{safe_name}_IMP_{date_part}_{short_title}"
                if base_filename in ann_counter:
                    ann_counter[base_filename] += 1
                    filename = f"{base_filename}_{ann_counter[base_filename]}.pdf"
                else:
                    ann_counter[base_filename] = 0
                    filename = f"{base_filename}.pdf"
                
                url = doc['url']
                print(f"  [{idx}/{len(login_pdf_docs)}] {filename[:80]}...")
                success, is_zip, failure_reason = download_document(url, save_dir, filename, session=auth_session)
                if success:
                    successful_imp += 1
                    print(f"    ✅ Downloaded")
                else:
                    failed_imp += 1
                    print(f"    ❌ Failed: {failure_reason}")
                    all_failed_urls.append((url, failure_reason))
                    with open("dead_links.txt", "a") as f:
                        f.write(f"{url} # DOWNLOAD FAILED: {failure_reason}\n")
                time.sleep(random.uniform(1, 3))
            print(f"  📊 Important Announcements: {successful_imp}/{len(login_pdf_docs)} downloaded, {failed_imp} failed")
        else:
            print(f"\n  ⚠️ No Important Announcements to download")
        
        # STEP 7: LOGOUT
        print("\n🔓 STEP 7: Logging out (all login tasks complete)")
        logout(auth_session)
        auth_session = None
        
        # STEP 8: Download PUBLIC PDFs (no auth)
        if public_docs:
            print(f"\n🌐 STEP 8: Downloading {len(public_docs)} public documents...")
            print("-" * 50)
            successful_pub = 0
            failed_pub = 0
            pub_counter = {}
            for idx, doc in enumerate(public_docs, 1):
                text = doc.get("text", "")
                doc_type = doc.get("type", "")
                ext = '.zip' if doc["url"].lower().endswith('.zip') else '.pdf'
                base_text = sanitize_filename(text) if not "Announcement" in doc_type else sanitize_filename(doc.get("title", text))
                if "Announcement" in doc_type:
                    # Recent announcements
                    date_match = re.match(r'(\d{4})(\d{2})(\d{2})', doc.get("unique_key", "")) if doc.get("unique_key") else None
                    date_part = date_match.group(0)[:8] if date_match else NOW.strftime('%Y%m%d')
                    short_title = sanitize_filename(doc.get("title", text))[:50]
                    base_filename = f"{safe_name}_REC_{date_part}_{short_title}"
                else:
                    base_filename = f"{safe_name}_{base_text}"
                
                if base_filename in pub_counter:
                    pub_counter[base_filename] += 1
                    filename = f"{base_filename}_{pub_counter[base_filename]}{ext}"
                else:
                    pub_counter[base_filename] = 0
                    filename = f"{base_filename}{ext}"
                
                url = doc['url']
                print(f"  [{idx}/{len(public_docs)}] {filename[:80]}...")
                success, is_zip, failure_reason = download_document(url, save_dir, filename, session=None)
                if success:
                    successful_pub += 1
                    print(f"    ✅ Downloaded")
                else:
                    failed_pub += 1
                    print(f"    ❌ Failed: {failure_reason}")
                    all_failed_urls.append((url, failure_reason))
                    with open("dead_links.txt", "a") as f:
                        f.write(f"{url} # DOWNLOAD FAILED: {failure_reason}\n")
                time.sleep(random.uniform(1, 3))
            print(f"  📊 Public documents: {successful_pub}/{len(public_docs)} downloaded, {failed_pub} failed")
        
        total_files = len([f for f in os.listdir(save_dir) if os.path.isfile(os.path.join(save_dir, f))])
        
        print(f"\n{'='*70}")
        print(f"📊 SUMMARY - {company_name}")
        print(f"{'='*70}")
        print(f"  📁 Total files: {total_files}")
        print(f"  📊 Financial Analysis: {'✅' if analysis_pdf else '❌'}")
        print(f"  📂 {save_dir}")
        
        return True
        
    except Exception as e:
        if auth_session:
            logout(auth_session)
        raise e

# ==================================================
# BATCH PROCESSOR
# ==================================================

def main():
    global all_failed_urls
    all_failed_urls = []
    print("="*70)
    print("🚀 SCREENER.IN BATCH DOWNLOADER")
    print("="*70)
    print("\n📋 What gets downloaded:")
    print("  🌐 PUBLIC (no login):")
    print("     📊 Financial Analysis PDF (with QoQ/YoY)")
    print("     📄 Quarterly Reports, Annual Reports")
    print("     📄 Concall Transcripts/PPTs, Credit Ratings")
    print("     📄 Recent Announcements")
    print("  🔒 LOGIN REQUIRED (downloaded with auth, then logout):")
    print("     📄 Important Announcements")
    print("     📝 Company Profile, Related Party Transactions")
    print("     📝 Corporate Actions, Trades Data")
    print("\n" + "="*70)
    print("Paste URLs (one per line), press Enter twice to start")
    print("="*70)
    print()
    
    urls = []
    empty_count = 0
    
    while True:
        try:
            line = input()
            line = line.strip()
            
            if line == "":
                empty_count += 1
                if empty_count >= 2 and len(urls) > 0:
                    break
                continue
            
            empty_count = 0
            
            if "screener.in/company/" in line:
                urls.append(line)
                company_slug = line.split('/company/')[1].split('/')[0]
                print(f"  ✓ [{len(urls)}] {company_slug}")
            else:
                print(f"  ⚠️ Skipped: {line[:50]}")
                
        except EOFError:
            break
    
    if not urls:
        print("\n❌ No valid URLs provided")
        return
    
    print(f"\n{'='*70}")
    print(f"📋 Processing {len(urls)} companies")
    print(f"{'='*70}")
    
    start_time = time.time()
    completed = 0
    failed = 0
    
    for idx, url in enumerate(urls, 1):
        company_slug = url.split('/company/')[1].split('/')[0]
        
        print(f"\n{'#'*70}")
        print(f"📌 [{idx}/{len(urls)}] {company_slug}")
        print(f"   ✅ Done: {completed} | ❌ Failed: {failed} | ⏳ Left: {len(urls)-idx}")
        print(f"{'#'*70}")
        
        try:
            if process_company(url):
                completed += 1
            else:
                failed += 1
        except KeyboardInterrupt:
            print(f"\n⚠️ Interrupted! Processed {idx-1}/{len(urls)}")
            failed += len(urls) - idx + 1
            break
        except Exception as e:
            failed += 1
            print(f"\n  ❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
        
        elapsed = time.time() - start_time
        avg_time = elapsed / idx if idx > 0 else 0
        remaining = (len(urls) - idx) * avg_time
        m_e, s_e = divmod(elapsed, 60)
        m_r, s_r = divmod(remaining, 60)
        
        print(f"\n  ⏱️  Elapsed: {int(m_e)}m {s_e:.0f}s | ETA: {int(m_r)}m {s_r:.0f}s")
    
    elapsed = time.time() - start_time
    h, r = divmod(elapsed, 3600)
    m, s = divmod(r, 60)
    
    print(f"\n{'='*70}")
    print(f"📊 BATCH COMPLETE")
    print(f"{'='*70}")
    print(f"  📋 Total:     {len(urls)}")
    print(f"  ✅ Done:      {completed}")
    print(f"  ❌ Failed:    {failed}")
    if h > 0:
        print(f"  ⏱️  Time:      {int(h)}h {int(m)}m {s:.0f}s")
    else:
        print(f"  ⏱️  Time:      {int(m)}m {s:.0f}s")
    print(f"  📁 Output:    downloads/")
    
    if all_failed_urls:
        print(f"\n❌ FAILED DOWNLOADS ACROSS ALL COMPANIES ({len(all_failed_urls)}):")
        for url, reason in all_failed_urls[:10]:
            print(f"  • {url}")
            print(f"    Reason: {reason}")
        if len(all_failed_urls) > 10:
            print(f"  ... and {len(all_failed_urls)-10} more (see dead_links.txt)")
    else:
        print(f"\n🎉 No failed downloads!")
    
    print(f"{'='*70}")

if __name__ == "__main__":
    main()