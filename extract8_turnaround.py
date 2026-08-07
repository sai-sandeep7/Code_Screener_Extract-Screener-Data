import os
import time
import json
import random
import datetime
import pandas as pd
import requests
import re
from bs4 import BeautifulSoup
from collections import defaultdict

BASE = "https://www.screener.in"

CACHE_FILE = "name_cache.json"
INDUSTRY_CACHE_FILE = "industry_cache.json"
METRICS_CACHE_FILE = "metrics_cache.json"  # Cache for Market Cap, P/E, and Current Price


# ==========================
# SHEET NAME SANITIZER
# ==========================
def sanitize_sheet_name(name, max_length=31):
    """
    Sanitize sheet name to be valid for Excel:
    - Max 31 characters
    - No invalid characters: [ ] : * ? / \ 
    - Replace invalid characters with spaces
    """
    # Replace invalid characters with space
    invalid_chars = r'[\[\]:*?/\\]'
    sanitized = re.sub(invalid_chars, ' ', name)
    
    # Remove extra spaces
    sanitized = ' '.join(sanitized.split())
    
    # Truncate to max length
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length-3] + "..."
    
    return sanitized if sanitized else "Sheet"


# ==========================
# TIMER DECORATOR
# ==========================
def timer(func):
    def wrapper(*args, **kwargs):
        start_time = time.time()
        print(f"\n{'='*60}")
        print(f"STARTING: {func.__name__}")
        print(f"{'='*60}\n")
        
        result = func(*args, **kwargs)
        
        end_time = time.time()
        elapsed_time = end_time - start_time
        minutes = int(elapsed_time // 60)
        seconds = elapsed_time % 60
        
        print(f"\n{'='*60}")
        print(f"COMPLETED: {func.__name__}")
        print(f"Total Time: {minutes} minutes {seconds:.2f} seconds")
        print(f"{'='*60}\n")
        
        return result
    return wrapper


# ==========================
# CACHE HELPERS
# ==========================
def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def load_industry_cache():
    """Load industry cache and convert old format entries to new format"""
    if not os.path.exists(INDUSTRY_CACHE_FILE):
        return {}
    
    with open(INDUSTRY_CACHE_FILE, "r", encoding="utf-8") as f:
        cache = json.load(f)
    
    # Convert old format (string) to new format (dict)
    converted = False
    for url, value in cache.items():
        if isinstance(value, str):
            # Old format: just a string (likely broad_industry)
            cache[url] = {
                "broad_industry": value if value not in ["None", "-", ""] else None,
                "industry": None
            }
            converted = True
        elif isinstance(value, dict):
            # Already new format, ensure both keys exist
            if "broad_industry" not in value:
                value["broad_industry"] = None
            if "industry" not in value:
                value["industry"] = None
    
    if converted:
        # Save converted cache back to file
        with open(INDUSTRY_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        print("✓ Converted old industry cache to new format")
    
    return cache


def save_industry_cache(cache):
    with open(INDUSTRY_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def load_metrics_cache():
    if not os.path.exists(METRICS_CACHE_FILE):
        return {}
    with open(METRICS_CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_metrics_cache(cache):
    with open(METRICS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


# ==========================
# METRICS CONVERSION HELPER
# ==========================
def convert_metric_to_number(value_str):
    """
    Convert metric strings like '1,23,456', '1.2k', '1.5M', '1.8B' to float numbers.
    Also handles 'Cr' (Crore) and other Indian notations.
    """
    if not value_str or value_str == "-" or value_str == "":
        return None
    
    # Remove commas and extra spaces
    value_str = str(value_str).strip().replace(',', '')
    
    # Handle Crore (Cr) notation
    if 'Cr' in value_str or 'cr' in value_str:
        value_str = value_str.replace('Cr', '').replace('cr', '').strip()
        try:
            return float(value_str) * 100  # Convert Cr to absolute number (1 Cr = 100)
        except ValueError:
            return None
    
    # Handle Lakh (L) notation
    if 'L' in value_str or 'lakh' in value_str.lower():
        value_str = value_str.replace('L', '').replace('lakh', '').replace('Lakh', '').strip()
        try:
            return float(value_str)  # Lakh to number (1 L = 1)
        except ValueError:
            return None
    
    # Handle K (Thousands)
    if value_str.endswith('k') or value_str.endswith('K'):
        value_str = value_str[:-1].strip()
        try:
            return float(value_str) * 1000
        except ValueError:
            return None
    
    # Handle M (Millions)
    if value_str.endswith('m') or value_str.endswith('M'):
        value_str = value_str[:-1].strip()
        try:
            return float(value_str) * 1000000
        except ValueError:
            return None
    
    # Handle B (Billions)
    if value_str.endswith('b') or value_str.endswith('B'):
        value_str = value_str[:-1].strip()
        try:
            return float(value_str) * 1000000000
        except ValueError:
            return None
    
    # Handle plain numbers with possible decimal
    try:
        return float(value_str)
    except ValueError:
        return None


# ==========================
# NAME SCRAPER
# ==========================
def get_company_name(company_url, cache):
    """Retrieve official displayed company name."""
    if company_url in cache:
        return cache[company_url]

    try:
        r = requests.get(company_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        h1 = soup.select_one("h1")

        if h1:
            clean = h1.text.strip()
        else:
            title = soup.title.text if soup.title else ""
            clean = title.split("|")[0].strip()

        cache[company_url] = clean
        time.sleep(random.uniform(0.5, 1.1))
        return clean
    except Exception as e:
        print(f"  ⚠ Error getting company name from {company_url}: {str(e)}")
        return company_url.rstrip("/").split("/")[-1]  # Fallback to raw name


# ==========================
# GET BROAD INDUSTRY AND INDUSTRY FROM COMPANY PAGE
# ==========================
def get_company_industry_info(company_url, cache):
    """
    Extract both Broad Industry and Industry from company page.
    Returns dict with 'broad_industry' and 'industry' keys.
    """
    if company_url in cache:
        cached_value = cache[company_url]
        # Handle case where cache might have stored a string (older version)
        if isinstance(cached_value, str):
            return {
                "broad_industry": cached_value if cached_value not in ["None", "-", ""] else None,
                "industry": None
            }
        return cached_value

    industry_info = {
        "broad_industry": None,
        "industry": None
    }
    
    try:
        r = requests.get(company_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        
        # Look for the peer comparison section - this is where industry info is usually found
        peer_section = soup.find("section", {"id": "peers"})
        
        if peer_section:
            # Find the paragraph with industry hierarchy
            industry_para = peer_section.find("p", class_="sub")
            if industry_para:
                # Find all links in this paragraph
                links = industry_para.find_all("a")
                
                for link in links:
                    # Check title attribute
                    title = link.get("title", "")
                    if title == "Broad Industry":
                        industry_info["broad_industry"] = link.text.strip()
                    elif title == "Industry":
                        industry_info["industry"] = link.text.strip()
                    
                    # Also check by the icon class preceding the link
                    prev_icon = link.find_previous("i")
                    if prev_icon:
                        icon_class = prev_icon.get("class", [])
                        if "icon-industry" in icon_class and not industry_info["broad_industry"]:
                            industry_info["broad_industry"] = link.text.strip()
                        elif "icon-tools-1" in icon_class and not industry_info["industry"]:
                            industry_info["industry"] = link.text.strip()
        
        # If not found in peer section, search entire document
        if not industry_info["industry"] or not industry_info["broad_industry"]:
            # Find all <a> tags with title="Industry"
            industry_link = soup.find("a", {"title": "Industry"})
            if industry_link:
                industry_info["industry"] = industry_link.text.strip()
            
            # Find all <a> tags with title="Broad Industry"
            broad_industry_link = soup.find("a", {"title": "Broad Industry"})
            if broad_industry_link:
                industry_info["broad_industry"] = broad_industry_link.text.strip()
        
        # Clean up any "None" strings or empty values
        if not industry_info["industry"] or industry_info["industry"] in ["None", "-", ""]:
            industry_info["industry"] = None
        if not industry_info["broad_industry"] or industry_info["broad_industry"] in ["None", "-", ""]:
            industry_info["broad_industry"] = None
            
        time.sleep(random.uniform(0.5, 1.1))
    except Exception as e:
        print(f"  ⚠ Error extracting industry info from {company_url}: {str(e)}")

    cache[company_url] = industry_info
    return industry_info


# ==========================
# EXTRACT MARKET CAP, P/E, AND CURRENT PRICE FROM COMPANY PAGE
# ==========================
def get_company_metrics(company_url, cache):
    """
    Extract Market Cap, Stock P/E, and Current Price from the company page's top ratios section.
    Returns dict with raw string values and converted numeric values.
    """
    if company_url in cache:
        return cache[company_url]

    metrics = {
        "Market Cap": None,
        "Stock P/E": None,
        "Current Price": None,
        "Market Cap_Num": None,
        "Stock P/E_Num": None,
        "Current Price_Num": None
    }
    
    try:
        r = requests.get(company_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        
        # Find the top-ratios list
        top_ratios = soup.select_one("#top-ratios")
        
        if top_ratios:
            # Find all list items
            ratio_items = top_ratios.select("li")
            
            for item in ratio_items:
                name_elem = item.select_one(".name")
                value_elem = item.select_one(".value .number")
                
                if name_elem and value_elem:
                    name = name_elem.text.strip()
                    value = value_elem.text.strip()
                    
                    if "Market Cap" in name:
                        metrics["Market Cap"] = value
                        metrics["Market Cap_Num"] = convert_metric_to_number(value)
                    elif "Stock P/E" in name:
                        metrics["Stock P/E"] = value
                        metrics["Stock P/E_Num"] = convert_metric_to_number(value)
                    elif "Current Price" in name:
                        metrics["Current Price"] = value
                        metrics["Current Price_Num"] = convert_metric_to_number(value)
        
        time.sleep(random.uniform(0.5, 1.1))
    except Exception as e:
        print(f"  ⚠ Error extracting metrics from {company_url}: {str(e)}")
    
    cache[company_url] = metrics
    return metrics


# ==========================
# EXTRACT SCREEN LINKS
# ==========================
def extract_company_urls_from_screen(screen_url):
    """Extract company URLs from Screener screen with pagination using HTML, not tables."""
    all_urls = set()
    page = 1
    last_batch = set()

    while True:
        paged = f"{screen_url}?page={page}"
        print(f"  Fetching listing page {page}: {paged}")

        try:
            r = requests.get(paged, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            soup = BeautifulSoup(r.text, "html.parser")

            rows = soup.select("tbody tr a[href^='/company/']")
            urls = {BASE + a.get("href") for a in rows if "/company/" in a.get("href", "")}

            if not urls:
                print(f"  No valid rows on page {page}. Stopping.")
                break
            if urls == last_batch:
                print(f"  Duplicate data detected page {page}. Stopping.")
                break

            all_urls |= urls
            last_batch = urls
            page += 1
            time.sleep(random.uniform(0.6, 1.4))
        except Exception as e:
            print(f"  ⚠ Error fetching page {page}: {str(e)}")
            break

    return sorted(all_urls)


# ==========================
# PROCESS SINGLE SCREEN
# ==========================
def process_screen(screen_id, url):
    print("\n====================================================")
    print(f"START → SCREEN {screen_id.upper()}")
    print(f"URL   → {url}")
    print("====================================================\n")

    # Extract all company page URLs
    company_urls = extract_company_urls_from_screen(url)
    if not company_urls:
        print(f"NO COMPANIES FOUND FOR SCREEN {screen_id}")
        return None

    print(f"Total companies found: {len(company_urls)}\n")

    # Retrieve raw text from screen AND official names
    mapping_rows = []
    cache = load_cache()

    # LIVE MAPPING LOGS
    print(f"Mapping company names for {screen_id}...\n")

    for company_url in company_urls:
        raw_name = company_url.rstrip("/").split("/")[-1]
        official = get_company_name(company_url, cache)

        print(f"  NEW → {raw_name} : {official}")

        mapping_rows.append({
            "Screen_Name": raw_name,
            "Official_Name": official,
            "URL": company_url
        })

    save_cache(cache)

    # Return the set of official names and URL mapping for this screen
    return {row["Official_Name"]: row["URL"] for row in mapping_rows}


# ==========================
# CONSOLIDATE ALL SCREENS
# ==========================
@timer
def consolidate_screens():
    """Consolidate data from all screens and create consolidated Excel with broad industry and industry information"""
    
    # UPDATED: 7 screens with one version each
    SCREENS = {
        "Early spot": "https://www.screener.in/screens/3603992/early-spot/",
        "Early turnaround": "https://www.screener.in/screens/3604013/early-turnaround/",
        "Strong growth": "https://www.screener.in/screens/3604284/strong-growth/",
        "Early Turnarounds": "https://www.screener.in/screens/3608038/early-turnarounds/",
        "Stable Turnarounds": "https://www.screener.in/screens/3608044/stable-turnarounds/"
    }

    # Group screens by type (simple grouping for 7 screens)
    screen_groups = {
        "all_screens": ["Early spot", "Early turnaround", "Strong growth", "Early Turnarounds", "Stable Turnarounds"]
    }

    all_companies = set()
    screen_data = {}  # Store companies for each screen with their URLs
    screen_mapping = defaultdict(set)  # Store companies by screen group

    print("\n" + "="*60)
    print("CONSOLIDATING DATA FROM ALL SCREENS")
    print("="*60 + "\n")

    # Extract companies from each screen
    for sid, link in SCREENS.items():
        print(f"\n{'─'*50}")
        print(f"Processing Screen: {sid.upper()}")
        print(f"URL: {link}")
        print(f"{'─'*50}\n")
        
        screen_companies_dict = process_screen(sid, link)
        if screen_companies_dict:
            screen_data[sid] = screen_companies_dict
            companies_set = set(screen_companies_dict.keys())
            all_companies |= companies_set
            
            # Add to appropriate group
            for group_name, screen_list in screen_groups.items():
                if sid in screen_list:
                    screen_mapping[group_name] |= companies_set
                    break

    print(f"\n{'='*60}")
    print(f"Total unique companies across all screens: {len(all_companies)}")
    print(f"{'='*60}\n")

    # Build reverse mapping for all company URLs (combining from all screens)
    all_company_urls = {}
    for sid, companies_dict in screen_data.items():
        for official_name, url in companies_dict.items():
            if official_name not in all_company_urls:
                all_company_urls[official_name] = url

    # Extract Market Cap, P/E, and Current Price for all companies
    print("Extracting Market Cap, P/E, and Current Price for all companies...\n")
    metrics_cache = load_metrics_cache()
    company_metrics = {}
    
    for i, (company, url) in enumerate(all_company_urls.items(), 1):
        print(f"  [{i}/{len(all_company_urls)}] Processing: {company}")
        metrics = get_company_metrics(url, metrics_cache)
        company_metrics[company] = metrics
        if i % 10 == 0:  # Save cache every 10 companies
            save_metrics_cache(metrics_cache)
    
    save_metrics_cache(metrics_cache)

    # Extract industry information for all companies
    print("\nExtracting Broad Industry and Industry for all companies...\n")
    industry_cache = load_industry_cache()
    company_industry_info = {}
    
    for i, (company, url) in enumerate(all_company_urls.items(), 1):
        print(f"  [{i}/{len(all_company_urls)}] Processing: {company}")
        industry_info = get_company_industry_info(url, industry_cache)
        company_industry_info[company] = industry_info
        if i % 10 == 0:  # Save cache every 10 companies
            save_industry_cache(industry_cache)
    
    save_industry_cache(industry_cache)

    # Build data for industry summaries and separate sheets
    broad_industry_data = defaultdict(list)  # For broad industry sheets
    industry_data = defaultdict(list)  # For industry sheets
    
    for company, info in company_industry_info.items():
        # Ensure info is a dict
        if not isinstance(info, dict):
            info = {"broad_industry": None, "industry": None}
        
        broad_industry = info.get("broad_industry")
        industry = info.get("industry")
        
        # Add to broad industry data
        if broad_industry and broad_industry not in ["None", "-", ""]:
            broad_industry_data[broad_industry].append(company)
        else:
            broad_industry_data["Unclassified - Broad Industry"].append(company)
        
        # Add to industry data
        if industry and industry not in ["None", "-", ""]:
            industry_data[industry].append(company)
        else:
            industry_data["Unclassified - Industry"].append(company)

    # Count for summary
    broad_industry_counts = {industry: len(companies) for industry, companies in broad_industry_data.items()}
    industry_counts = {industry: len(companies) for industry, companies in industry_data.items()}

    # Save consolidated Excel
    today = datetime.date.today().strftime("%d_%m_%Y")
    fname = f"TURNAROUND_SCREENS_{today}.xlsx"

    print(f"\n{'='*60}")
    print(f"Writing consolidated results to: {fname}")
    print(f"{'='*60}\n")

    with pd.ExcelWriter(fname, engine="openpyxl") as writer:
        # Summary sheet
        summary_data = []
        
        # Add screen group summaries
        for group_name, companies in screen_mapping.items():
            summary_data.append({
                "Category": group_name.upper(),
                "Type": "Screen Group",
                "Company Count": len(companies)
            })
        
        # Add individual screen summaries
        for sid, companies_dict in screen_data.items():
            summary_data.append({
                "Category": sid.upper(),
                "Type": "Individual Screen",
                "Company Count": len(companies_dict)
            })
        
        # Add broad industry summaries
        for industry, count in sorted(broad_industry_counts.items(), key=lambda x: x[1], reverse=True):
            if industry != "Unclassified - Broad Industry":
                summary_data.append({
                    "Category": f"Broad: {industry}",
                    "Type": "Broad Industry",
                    "Company Count": count
                })
        
        # Add unclassified broad industry
        if "Unclassified - Broad Industry" in broad_industry_counts:
            summary_data.append({
                "Category": "Unclassified - Broad Industry",
                "Type": "Broad Industry",
                "Company Count": broad_industry_counts["Unclassified - Broad Industry"]
            })
        
        # Add industry summaries
        for industry, count in sorted(industry_counts.items(), key=lambda x: x[1], reverse=True):
            if industry != "Unclassified - Industry":
                summary_data.append({
                    "Category": f"Industry: {industry}",
                    "Type": "Industry",
                    "Company Count": count
                })
        
        # Add unclassified industry
        if "Unclassified - Industry" in industry_counts:
            summary_data.append({
                "Category": "Unclassified - Industry",
                "Type": "Industry",
                "Company Count": industry_counts["Unclassified - Industry"]
            })
        
        if summary_data:
            df_summary = pd.DataFrame(summary_data)
            df_summary = df_summary.sort_values(["Type", "Category"])
            df_summary.to_excel(writer, sheet_name="Summary", index=False)
            print(f"✓ Summary sheet created")

        # Individual screen sheets with Market Cap, P/E, Broad Industry, and Industry
        print("\nCreating individual screen sheets with Market Cap, P/E, Broad Industry, and Industry...")
        for sid, companies_dict in screen_data.items():
            if companies_dict:
                data = []
                for company_name in sorted(companies_dict.keys()):
                    metrics = company_metrics.get(company_name, {})
                    industry_info = company_industry_info.get(company_name, {})
                    
                    # Ensure industry_info is a dict
                    if not isinstance(industry_info, dict):
                        industry_info = {"broad_industry": None, "industry": None}
                    
                    broad_industry = industry_info.get("broad_industry")
                    industry = industry_info.get("industry")
                    
                    # Convert None to "Unclassified"
                    broad_industry = broad_industry if broad_industry and broad_industry not in ["None", "-"] else "Unclassified"
                    industry = industry if industry and industry not in ["None", "-"] else "Unclassified"
                    
                    data.append({
                        "Company Name": company_name,
                        "Broad Industry": broad_industry,
                        "Industry": industry,
                        "Market Cap (₹ Cr)": metrics.get("Market Cap"),
                        "Stock P/E": metrics.get("Stock P/E"),
                        "Current Price (₹)": metrics.get("Current Price")
                    })
                
                df = pd.DataFrame(data)
                sheet_name = sanitize_sheet_name(sid[:31])
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                print(f"  ✓ {sheet_name}: {len(df)} entries")

        # All screens combined sheet
        print("\nCreating all screens combined sheet with Market Cap, P/E, Broad Industry, and Industry...")
        for group_name, companies in screen_mapping.items():
            if companies:
                data = []
                for company_name in sorted(companies):
                    metrics = company_metrics.get(company_name, {})
                    industry_info = company_industry_info.get(company_name, {})
                    
                    # Ensure industry_info is a dict
                    if not isinstance(industry_info, dict):
                        industry_info = {"broad_industry": None, "industry": None}
                    
                    broad_industry = industry_info.get("broad_industry")
                    industry = industry_info.get("industry")
                    
                    # Convert None to "Unclassified"
                    broad_industry = broad_industry if broad_industry and broad_industry not in ["None", "-"] else "Unclassified"
                    industry = industry if industry and industry not in ["None", "-"] else "Unclassified"
                    
                    # Find which screens this company appears in
                    appears_in = []
                    for sid, companies_dict in screen_data.items():
                        if company_name in companies_dict:
                            appears_in.append(sid.upper())
                    
                    data.append({
                        "Company Name": company_name,
                        "Broad Industry": broad_industry,
                        "Industry": industry,
                        "Market Cap (₹ Cr)": metrics.get("Market Cap"),
                        "Stock P/E": metrics.get("Stock P/E"),
                        "Current Price (₹)": metrics.get("Current Price"),
                        "Appears In": ", ".join(appears_in) if appears_in else "Unknown"
                    })
                
                df = pd.DataFrame(data)
                sheet_name = sanitize_sheet_name("all_screens_combined")
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                print(f"  ✓ {sheet_name}: {len(df)} entries")

        # All companies sheet
        print("\nCreating all companies sheet...")
        all_data = []
        for company in sorted(all_companies):
            metrics = company_metrics.get(company, {})
            industry_info = company_industry_info.get(company, {})
            
            # Ensure industry_info is a dict
            if not isinstance(industry_info, dict):
                industry_info = {"broad_industry": None, "industry": None}
            
            broad_industry = industry_info.get("broad_industry")
            industry = industry_info.get("industry")
            
            # Convert None to "Unclassified"
            broad_industry = broad_industry if broad_industry and broad_industry not in ["None", "-"] else "Unclassified"
            industry = industry if industry and industry not in ["None", "-"] else "Unclassified"
            
            # Find which screens this company appears in
            appears_in = []
            for sid, companies_dict in screen_data.items():
                if company in companies_dict:
                    appears_in.append(sid.upper())
            
            all_data.append({
                "Company Name": company,
                "Broad Industry": broad_industry,
                "Industry": industry,
                "Market Cap (₹ Cr)": metrics.get("Market Cap"),
                "Stock P/E": metrics.get("Stock P/E"),
                "Current Price (₹)": metrics.get("Current Price"),
                "Appears In": ", ".join(appears_in) if appears_in else "Unknown"
            })
        
        df_all = pd.DataFrame(all_data)
        df_all.to_excel(writer, sheet_name="All Companies", index=False)
        
        # Count unclassified companies
        unclassified_broad = sum(1 for row in all_data if row["Broad Industry"] == "Unclassified")
        unclassified_industry = sum(1 for row in all_data if row["Industry"] == "Unclassified")
        print(f"  ✓ All Companies sheet: {len(df_all)} entries")
        if unclassified_broad > 0:
            print(f"    ⚠ Companies with missing broad industry: {unclassified_broad}")
        if unclassified_industry > 0:
            print(f"    ⚠ Companies with missing industry: {unclassified_industry}")

        # BROAD INDUSTRY SHEETS (with metrics)
        print("\nCreating BROAD INDUSTRY sheets with Market Cap, P/E, and Current Price...")
        for broad_industry, companies in sorted(broad_industry_data.items()):
            if companies and broad_industry != "Unclassified - Broad Industry":
                data = []
                for company_name in sorted(companies):
                    metrics = company_metrics.get(company_name, {})
                    industry_info = company_industry_info.get(company_name, {})
                    
                    # Get regular industry for this company
                    if isinstance(industry_info, dict):
                        industry = industry_info.get("industry")
                    else:
                        industry = None
                    
                    industry = industry if industry and industry not in ["None", "-"] else "Unclassified"
                    
                    # Find which screens this company appears in
                    appears = []
                    for sid, companies_dict in screen_data.items():
                        if company_name in companies_dict:
                            appears.append(sid.upper())
                    
                    data.append({
                        "Company Name": company_name,
                        "Industry": industry,
                        "Market Cap (₹ Cr)": metrics.get("Market Cap"),
                        "Stock P/E": metrics.get("Stock P/E"),
                        "Current Price (₹)": metrics.get("Current Price"),
                        "Appears In": ", ".join(appears) if appears else "Unknown"
                    })
                
                df_with_metrics = pd.DataFrame(data)
                
                # Create sheet name
                sheet_name = sanitize_sheet_name(broad_industry)
                df_with_metrics.to_excel(writer, sheet_name=sheet_name, index=False)
                print(f"  ✓ {sheet_name}: {len(companies)} entries")
        
        # Unclassified broad industry sheet (if any)
        if "Unclassified - Broad Industry" in broad_industry_data and broad_industry_data["Unclassified - Broad Industry"]:
            data = []
            for company_name in sorted(broad_industry_data["Unclassified - Broad Industry"]):
                metrics = company_metrics.get(company_name, {})
                industry_info = company_industry_info.get(company_name, {})
                
                # Get regular industry for this company
                if isinstance(industry_info, dict):
                    industry = industry_info.get("industry")
                else:
                    industry = None
                
                industry = industry if industry and industry not in ["None", "-"] else "Unclassified"
                
                # Find which screens this company appears in
                appears = []
                for sid, companies_dict in screen_data.items():
                    if company_name in companies_dict:
                        appears.append(sid.upper())
                
                data.append({
                    "Company Name": company_name,
                    "Industry": industry,
                    "Market Cap (₹ Cr)": metrics.get("Market Cap"),
                    "Stock P/E": metrics.get("Stock P/E"),
                    "Current Price (₹)": metrics.get("Current Price"),
                    "Appears In": ", ".join(appears) if appears else "Unknown"
                })
            
            df_with_metrics = pd.DataFrame(data)
            sheet_name = sanitize_sheet_name("Unclassified - Broad")
            df_with_metrics.to_excel(writer, sheet_name=sheet_name, index=False)
            print(f"  ✓ {sheet_name}: {len(broad_industry_data['Unclassified - Broad Industry'])} entries")

        # INDUSTRY SHEETS (with metrics)
        print("\nCreating INDUSTRY sheets with Market Cap, P/E, and Current Price...")
        for industry, companies in sorted(industry_data.items()):
            if companies and industry != "Unclassified - Industry":
                data = []
                for company_name in sorted(companies):
                    metrics = company_metrics.get(company_name, {})
                    industry_info = company_industry_info.get(company_name, {})
                    
                    # Get broad industry for this company
                    if isinstance(industry_info, dict):
                        broad_industry = industry_info.get("broad_industry")
                    else:
                        broad_industry = None
                    
                    broad_industry = broad_industry if broad_industry and broad_industry not in ["None", "-"] else "Unclassified"
                    
                    # Find which screens this company appears in
                    appears = []
                    for sid, companies_dict in screen_data.items():
                        if company_name in companies_dict:
                            appears.append(sid.upper())
                    
                    data.append({
                        "Company Name": company_name,
                        "Broad Industry": broad_industry,
                        "Market Cap (₹ Cr)": metrics.get("Market Cap"),
                        "Stock P/E": metrics.get("Stock P/E"),
                        "Current Price (₹)": metrics.get("Current Price"),
                        "Appears In": ", ".join(appears) if appears else "Unknown"
                    })
                
                df_with_metrics = pd.DataFrame(data)
                
                # Create sheet name - use sanitize function
                sheet_name = sanitize_sheet_name(f"IND {industry}")
                df_with_metrics.to_excel(writer, sheet_name=sheet_name, index=False)
                print(f"  ✓ {sheet_name}: {len(companies)} entries")
        
        # Unclassified industry sheet (if any)
        if "Unclassified - Industry" in industry_data and industry_data["Unclassified - Industry"]:
            data = []
            for company_name in sorted(industry_data["Unclassified - Industry"]):
                metrics = company_metrics.get(company_name, {})
                industry_info = company_industry_info.get(company_name, {})
                
                # Get broad industry for this company
                if isinstance(industry_info, dict):
                    broad_industry = industry_info.get("broad_industry")
                else:
                    broad_industry = None
                
                broad_industry = broad_industry if broad_industry and broad_industry not in ["None", "-"] else "Unclassified"
                
                # Find which screens this company appears in
                appears = []
                for sid, companies_dict in screen_data.items():
                    if company_name in companies_dict:
                        appears.append(sid.upper())
                
                data.append({
                    "Company Name": company_name,
                    "Broad Industry": broad_industry,
                    "Market Cap (₹ Cr)": metrics.get("Market Cap"),
                    "Stock P/E": metrics.get("Stock P/E"),
                    "Current Price (₹)": metrics.get("Current Price"),
                    "Appears In": ", ".join(appears) if appears else "Unknown"
                })
            
            df_with_metrics = pd.DataFrame(data)
            sheet_name = sanitize_sheet_name("IND Unclassified")
            df_with_metrics.to_excel(writer, sheet_name=sheet_name, index=False)
            print(f"  ✓ {sheet_name}: {len(industry_data['Unclassified - Industry'])} entries")

        # Numeric data sheet for analysis
        print("\nCreating numeric data sheet for analysis...")
        numeric_data = []
        for company in sorted(all_companies):
            metrics = company_metrics.get(company, {})
            industry_info = company_industry_info.get(company, {})
            
            # Ensure industry_info is a dict
            if not isinstance(industry_info, dict):
                industry_info = {"broad_industry": None, "industry": None}
            
            broad_industry = industry_info.get("broad_industry")
            industry = industry_info.get("industry")
            
            # Convert None to "Unclassified"
            broad_industry = broad_industry if broad_industry and broad_industry not in ["None", "-"] else "Unclassified"
            industry = industry if industry and industry not in ["None", "-"] else "Unclassified"
            
            numeric_data.append({
                "Company Name": company,
                "Broad Industry": broad_industry,
                "Industry": industry,
                "Market Cap (Cr) - Numeric": metrics.get("Market Cap_Num"),
                "Stock P/E - Numeric": metrics.get("Stock P/E_Num"),
                "Current Price (₹) - Numeric": metrics.get("Current Price_Num")
            })
        
        df_numeric = pd.DataFrame(numeric_data)
        df_numeric.to_excel(writer, sheet_name="Numeric Data", index=False)
        print(f"  ✓ Numeric Data sheet: {len(df_numeric)} entries")

    print(f"\n{'='*60}")
    print(f"✓ CONSOLIDATION COMPLETE!")
    print(f"✓ File saved: {fname}")
    print(f"✓ Total unique companies: {len(all_companies)}")
    print(f"✓ Individual screens processed: {len(screen_data)}")
    print(f"✓ Screen groups created: {len(screen_mapping)}")
    print(f"✓ Broad Industry categories: {len([k for k in broad_industry_data.keys() if k != 'Unclassified - Broad Industry'])}")
    print(f"✓ Industry categories: {len([k for k in industry_data.keys() if k != 'Unclassified - Industry'])}")
    print(f"{'='*60}\n")
    
    return fname


# ==========================
# MAIN ENTRY
# ==========================
if __name__ == "__main__":
    consolidate_screens()