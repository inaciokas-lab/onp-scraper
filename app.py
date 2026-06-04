#!/usr/bin/env python3
"""
ONP Fish Market Dashboard
Office National des Pêches — Morocco

Hybrid scraper:
1. Static HTML with requests + BeautifulSoup
2. Hidden API / JSON discovery
3. Selenium fallback
"""

import glob
import io
import os
import re
import shutil
import time
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urljoin

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager


# ──────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────

BASE_URL = "https://www.onp.ma/prix/"

DELEGATIONS = {
    "-- Select a port --": -1,
    "Agadir": 1,
    "Al Hoceima": 2,
    "Casablanca": 3,
    "Dakhla": 4,
    "El Jadida": 5,
    "Essaouira": 6,
    "Kénitra": 7,
    "Laâyoune": 8,
    "Larache": 9,
    "Mehdia": 10,
    "Nador": 11,
    "Safi": 12,
    "Tan-Tan": 13,
    "Tanger": 14,
    "Tiznit": 15,
    "Sidi Ifni": 16,
    "Tarfaya": 17,
    "Boujdour": 18,
    "Jebha": 19,
    "Fnideq": 20,
    "Chefchaouen": 21,
    "Oualidia": 22,
    "Mohammedia": 23,
    "Ras Kebdana": 24,
    "Ksar Sghir": 25,
    "M'diq": 26,
    "Assilah": 27,
    "Ifni": 28,
    "Imesouane": 29,
    "Sidi Boulfdail": 30,
    "Tafedna": 31,
    "Souiria Kdima": 32,
    "Delegation 33": 33,
    "Delegation 34": 34,
    "Delegation 35": 35,
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


# ──────────────────────────────────────────────
# GENERIC HELPERS
# ──────────────────────────────────────────────

def normalize_text(text: Optional[str]) -> str:
    if not text:
        return "Unknown"

    text = re.sub(r"\s+", " ", str(text).strip())
    return text.title()


def clean_header(text: str) -> str:
    text = str(text).lower().strip()

    replacements = {
        "è": "e",
        "é": "e",
        "ê": "e",
        "ë": "e",
        "à": "a",
        "â": "a",
        "î": "i",
        "ï": "i",
        "ô": "o",
        "û": "u",
        "ù": "u",
        "ç": "c",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"\s+", " ", text)
    return text


def normalize_key(key: str) -> str:
    key = clean_header(str(key))
    key = re.sub(r"[^a-z0-9]+", "_", key)
    return key.strip("_")


def parse_date_value(text: Optional[str]) -> Optional[date]:
    if text is None:
        return None

    text = str(text).strip()

    if not text or text.lower() in ["none", "null", "nan", "-"]:
        return None

    formats = [
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d/%m/%y",
        "%Y/%m/%d",
        "%d %m %Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    return None


def parse_number(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None

    text = str(text).strip()

    if text in ["", "-", "—", "N/A", "n/a", "null", "None", "nan"]:
        return None

    value = (
        text.replace("\xa0", "")
        .replace(" ", "")
        .replace("DH", "")
        .replace("MAD", "")
        .replace("Dhs", "")
        .replace("dhs", "")
        .replace("Kg", "")
        .replace("KG", "")
        .replace("kg", "")
    )

    if "," in value and "." in value:
        value = value.replace(".", "").replace(",", ".")
    elif "," in value:
        value = value.replace(",", ".")

    value = re.sub(r"[^0-9.\-]", "", value)

    try:
        return round(float(value), 2)
    except ValueError:
        return None


def build_dataframe(records: list) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    expected_cols = [
        "Species",
        "Sale date",
        "Weight (KG)",
        "Amount (DH)",
        "Price (DH/KG)",
        "Port",
    ]

    for col in expected_cols:
        if col not in df.columns:
            df[col] = None

    duplicate_cols = [col for col in expected_cols if col in df.columns]

    if duplicate_cols:
        df = df.drop_duplicates(subset=duplicate_cols)

    return df[expected_cols].reset_index(drop=True)


# ──────────────────────────────────────────────
# TABLE PARSER
# ──────────────────────────────────────────────

def parse_table(html: str, delegation_name: str, page_num: int = 1) -> list:
    """
    Parses only tables that look like real fish-market data.
    Rejects administrative/procurement tables such as DAO, tenders, notices, etc.
    """
    soup = BeautifulSoup(html, "html.parser")
    records = []

    tables = soup.find_all("table")

    if not tables:
        return records

    candidate_tables = []

    admin_keywords = [
        "dao",
        "avis de modification",
        "appel d'offres",
        "appel d’offres",
        "consultation",
        "marché public",
        "marche public",
        "règlement",
        "reglement",
        "référence",
        "reference",
    ]

    market_keywords = [
        "espèce",
        "espece",
        "poisson",
        "produit",
        "designation",
        "prix",
        "poids",
        "quantité",
        "quantite",
        "montant",
        "date",
        "vente",
        "kg",
        "dh/kg",
        "dh / kg",
    ]

    for table in tables:
        table_text = table.get_text(" ", strip=True).lower()

        if any(keyword in table_text for keyword in admin_keywords):
            continue

        score = 0

        for keyword in market_keywords:
            if keyword in table_text:
                score += 5

        rows_count = len(table.find_all("tr"))
        score += rows_count

        if score >= 8:
            candidate_tables.append((score, table))

    candidate_tables.sort(key=lambda x: x[0], reverse=True)

    for _, table in candidate_tables:
        table_records = parse_single_table(table, delegation_name)

        if table_records:
            records.extend(table_records)

    return records


def parse_single_table(table, delegation_name: str) -> list:
    records = []
    rows = table.find_all("tr")

    if len(rows) < 2:
        return records

    full_table_text = table.get_text(" ", strip=True).lower()

    bad_table_keywords = [
        "dao",
        "avis de modification",
        "appel d'offres",
        "appel d’offres",
        "consultation",
        "marché public",
        "marche public",
        "règlement",
        "reglement",
    ]

    if any(keyword in full_table_text for keyword in bad_table_keywords):
        return records

    header_row = rows[0]
    headers = [
        h.get_text(" ", strip=True).lower()
        for h in header_row.find_all(["th", "td"])
    ]

    if not headers:
        return records

    col_map = {}

    for idx, h in enumerate(headers):
        clean_h = clean_header(h)

        if any(x in clean_h for x in ["espece", "poisson", "produit", "designation", "libelle"]):
            col_map["species"] = idx

        elif "date" in clean_h:
            col_map["sale_date"] = idx

        elif any(x in clean_h for x in ["poids", "quantite", "volume", "kg"]):
            col_map["weight_kg"] = idx

        elif any(x in clean_h for x in ["prix", "moyen", "dh/kg", "dh / kg"]):
            col_map["price_dh_kg"] = idx

        elif any(x in clean_h for x in ["montant", "valeur", "total", "dh", "mad"]):
            col_map["amount_dh"] = idx

    max_cols = max(len(r.find_all(["td", "th"])) for r in rows)

    market_signal_words = [
        "espèce",
        "espece",
        "poisson",
        "prix",
        "poids",
        "quantité",
        "quantite",
        "kg",
        "dh/kg",
    ]

    market_signal_count = sum(
        1 for word in market_signal_words if word in full_table_text
    )

    if "species" not in col_map and max_cols >= 4 and market_signal_count >= 2:
        col_map = {
            "species": 0,
            "weight_kg": 1,
            "amount_dh": 2,
            "price_dh_kg": 3,
        }

        if max_cols >= 5:
            col_map["sale_date"] = 4

    if "species" not in col_map:
        return records

    if "weight_kg" not in col_map and "price_dh_kg" not in col_map and "amount_dh" not in col_map:
        return records

    data_rows = rows[1:]

    for row in data_rows:
        cells = row.find_all(["td", "th"])

        if not cells:
            continue

        values = [c.get_text(" ", strip=True) for c in cells]

        def get_text(field):
            idx = col_map.get(field)

            if idx is not None and idx < len(values):
                value = values[idx].strip()
                return value if value else None

            return None

        species = get_text("species")

        if not species:
            continue

        species_lower = species.lower().strip()

        if any(
            kw in species_lower
            for kw in [
                "espèce",
                "espece",
                "total",
                "sous-total",
                "poids",
                "prix",
                "montant",
                "date",
                "quantité",
                "quantite",
                "dao",
                "avis",
                "modification",
                "appel d'offres",
                "appel d’offres",
                "consultation",
            ]
        ):
            continue

        if re.search(r"\b(dao|ca|ao|avis|n°|nº|no|num|numero)\b", species_lower):
            continue

        weight = parse_number(get_text("weight_kg"))
        amount = parse_number(get_text("amount_dh"))
        price = parse_number(get_text("price_dh_kg"))

        if price is not None and price > 5000:
            continue

        if weight is None and amount is None and price is None:
            continue

        records.append(
            {
                "Species": normalize_text(species),
                "Sale date": parse_date_value(get_text("sale_date")),
                "Weight (KG)": weight,
                "Amount (DH)": amount,
                "Price (DH/KG)": price,
                "Port": delegation_name,
            }
        )

    return records


def detect_total_pages(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    max_page = 1

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        text = link.get_text(strip=True)

        match = re.search(r"(?:page|paged)=(\d+)", href)

        if match:
            max_page = max(max_page, int(match.group(1)))

        if text.isdigit():
            max_page = max(max_page, int(text))

    return max_page


# ──────────────────────────────────────────────
# METHOD 1 — STATIC HTML
# ──────────────────────────────────────────────

def fetch_static_html(url: str, timeout: int = 8) -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Referer": BASE_URL,
    }

    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def get_static_candidate_urls(port_id: int, page: int = 1) -> list:
    if page == 1:
        return [
            f"{BASE_URL}?search-delegation={port_id}",
            f"{BASE_URL}?delegation={port_id}",
        ]

    return [
        f"{BASE_URL}?search-delegation={port_id}&page={page}",
        f"{BASE_URL}?delegation={port_id}&page={page}",
    ]


def discover_related_urls(html: str, current_url: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    urls = []

    for tag in soup.find_all("a", href=True):
        href = tag.get("href")

        if href:
            urls.append(urljoin(current_url, href))

    for tag in soup.find_all("iframe", src=True):
        src = tag.get("src")

        if src:
            urls.append(urljoin(current_url, src))

    for tag in soup.find_all("script", src=True):
        src = tag.get("src")

        if src:
            urls.append(urljoin(current_url, src))

    useful = []

    for url in urls:
        low = url.lower()

        if any(
            key in low
            for key in [
                "prix",
                "mercuriale",
                "delegation",
                "search-delegation",
                "poisson",
                "marche",
                "march",
                "vente",
                "api",
                "ajax",
                "json",
                "onp",
            ]
        ):
            useful.append(url)

    return list(dict.fromkeys(useful))


def scrape_port_static(port_name, port_id, max_pages, progress_bar, status_text) -> pd.DataFrame:
    all_records = []
    checked_urls = set()

    total_pages = min(max_pages if max_pages > 0 else 1, 2)

    for page in range(1, total_pages + 1):
        status_text.text(f"1. Static HTML — {port_name}, page {page}")

        page_records = []
        candidate_urls = get_static_candidate_urls(port_id, page)

        for url in candidate_urls:
            if url in checked_urls:
                continue

            checked_urls.add(url)

            try:
                html = fetch_static_html(url, timeout=8)
            except Exception:
                continue

            records = parse_table(html, port_name, page)

            if records:
                page_records.extend(records)

        if page_records:
            all_records.extend(page_records)
            status_text.text(
                f"Static HTML found {len(page_records)} records for {port_name}, page {page}"
            )
        else:
            status_text.text(f"Static HTML found no data for {port_name}, page {page}")
            break

        progress_bar.progress(page / max(total_pages, 1))
        time.sleep(0.2)

    return build_dataframe(all_records)


# ──────────────────────────────────────────────
# METHOD 2 — HIDDEN API / JSON DISCOVERY
# ──────────────────────────────────────────────

def find_json_like_urls(html: str, base_url: str) -> list:
    urls = set()
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["script", "iframe"], src=True):
        urls.add(urljoin(base_url, tag.get("src")))

    for tag in soup.find_all(["a", "iframe"], href=True):
        urls.add(urljoin(base_url, tag.get("href")))

    patterns = [
        r"""["']([^"']*(?:api|ajax|json|mercuriale|prix|poisson|vente|delegation|march)[^"']*)["']""",
        r"""url\s*:\s*["']([^"']+)["']""",
        r"""fetch$["']([^"']+)["']$""",
        r"""\.get\(["']([^"']+)["']""",
        r"""\.post\(["']([^"']+)["']""",
    ]

    for pattern in patterns:
        for match in re.findall(pattern, html, flags=re.IGNORECASE):
            if match and not match.startswith(("data:", "javascript:", "#")):
                urls.add(urljoin(base_url, match))

    filtered = []

    for url in urls:
        low = url.lower()

        if any(
            key in low
            for key in [
                "api",
                "ajax",
                "json",
                "mercuriale",
                "prix",
                "poisson",
                "vente",
                "delegation",
                "march",
            ]
        ):
            filtered.append(url)

    return list(dict.fromkeys(filtered))


def extract_dict_rows(data):
    rows = []

    if isinstance(data, list):
        if all(isinstance(x, dict) for x in data):
            rows.extend(data)

        for item in data:
            rows.extend(extract_dict_rows(item))

    elif isinstance(data, dict):
        for value in data.values():
            rows.extend(extract_dict_rows(value))

    return rows


def map_json_row_to_record(row: dict, port_name: str) -> Optional[dict]:
    normalized = {normalize_key(k): v for k, v in row.items()}

    def pick(possible_keys):
        for key in possible_keys:
            nk = normalize_key(key)

            if nk in normalized:
                return normalized[nk]

        return None

    species = pick(
        [
            "espece",
            "espèce",
            "species",
            "poisson",
            "produit",
            "designation",
            "libelle",
            "libelle_espece",
            "nom",
            "name",
        ]
    )

    weight = pick(
        [
            "poids",
            "poids_kg",
            "quantite",
            "quantité",
            "volume",
            "qte",
            "kg",
        ]
    )

    amount = pick(
        [
            "montant",
            "montant_dh",
            "valeur",
            "total",
            "ca",
            "mad",
            "dh",
        ]
    )

    price = pick(
        [
            "prix",
            "prix_moyen",
            "prix_dh_kg",
            "prix_kg",
            "dh_kg",
            "moyen",
        ]
    )

    sale_date = pick(
        [
            "date",
            "date_vente",
            "jour",
            "dateoperation",
            "date_operation",
        ]
    )

    if not species:
        return None

    parsed_price = parse_number(price) if price is not None else None

    if parsed_price is not None and parsed_price > 5000:
        return None

    return {
        "Species": normalize_text(species),
        "Sale date": parse_date_value(sale_date) if sale_date else None,
        "Weight (KG)": parse_number(weight) if weight is not None else None,
        "Amount (DH)": parse_number(amount) if amount is not None else None,
        "Price (DH/KG)": parsed_price,
        "Port": port_name,
    }


def try_fetch_json(url: str, port_id: int) -> Optional[object]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json,text/javascript,*/*;q=0.8",
        "Referer": BASE_URL,
        "X-Requested-With": "XMLHttpRequest",
    }

    candidate_requests = [
        ("GET", url, {}),
        ("GET", url, {"search-delegation": port_id}),
        ("GET", url, {"delegation": port_id}),
        ("GET", url, {"port": port_id}),
        ("GET", url, {"id_delegation": port_id}),
        ("POST", url, {"search-delegation": port_id}),
        ("POST", url, {"delegation": port_id}),
        ("POST", url, {"port": port_id}),
        ("POST", url, {"id_delegation": port_id}),
    ]

    for method, request_url, params in candidate_requests:
        try:
            if method == "GET":
                response = requests.get(
                    request_url,
                    params=params,
                    headers=headers,
                    timeout=20,
                )
            else:
                response = requests.post(
                    request_url,
                    data=params,
                    headers=headers,
                    timeout=20,
                )

            if response.status_code >= 400:
                continue

            content_type = response.headers.get("content-type", "").lower()
            text = response.text.strip()

            if "json" in content_type or text.startswith("{") or text.startswith("["):
                return response.json()

        except Exception:
            continue

    return None


def scrape_port_api(port_name, port_id, max_pages, progress_bar, status_text) -> pd.DataFrame:
    status_text.text(f"2. Hidden API discovery — {port_name}")

    try:
        base_html = fetch_static_html(BASE_URL)
    except Exception:
        return pd.DataFrame()

    api_urls = find_json_like_urls(base_html, BASE_URL)
    related_urls = discover_related_urls(base_html, BASE_URL)

    for related_url in related_urls[:10]:
        try:
            related_html = fetch_static_html(related_url)
            api_urls.extend(find_json_like_urls(related_html, related_url))
        except Exception:
            continue

    api_urls = list(dict.fromkeys(api_urls))

    if not api_urls:
        status_text.text("No API candidates discovered")
        return pd.DataFrame()

    all_records = []

    for i, api_url in enumerate(api_urls[:20]):
        status_text.text(f"2. Testing API candidate {i + 1}/{min(len(api_urls), 20)}")

        data = try_fetch_json(api_url, port_id)

        if data is None:
            progress_bar.progress((i + 1) / max(min(len(api_urls), 20), 1))
            continue

        rows = extract_dict_rows(data)

        for row in rows:
            record = map_json_row_to_record(row, port_name)

            if record:
                all_records.append(record)

        if all_records:
            break

        progress_bar.progress((i + 1) / max(min(len(api_urls), 20), 1))

    return build_dataframe(all_records)


# ──────────────────────────────────────────────
# METHOD 3 — SELENIUM FALLBACK
# ──────────────────────────────────────────────

def make_driver():
    options = Options()

    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--remote-debugging-port=9222")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"--user-agent={USER_AGENT}")

    chrome_binary_candidates = [
        os.environ.get("CHROME_BIN"),
        os.environ.get("GOOGLE_CHROME_BIN"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/app/.apt/usr/bin/chromium",
        "/app/.apt/usr/bin/chromium-browser",
    ]

    chrome_binary_candidates.extend(glob.glob("/usr/bin/*chrom*"))
    chrome_binary_candidates.extend(glob.glob("/app/.apt/usr/bin/*chrom*"))

    chrome_binary = None

    for candidate in chrome_binary_candidates:
        if candidate and os.path.exists(candidate) and os.access(candidate, os.X_OK):
            chrome_binary = candidate
            break

    if not chrome_binary:
        raise RuntimeError(
            "Chrome/Chromium binary not found. "
            "If using Streamlit Cloud, add packages.txt at the repository root with "
            "chromium and chromium-driver, then clear cache and reboot the app."
        )

    options.binary_location = chrome_binary

    chromedriver_candidates = [
        os.environ.get("CHROMEDRIVER_PATH"),
        shutil.which("chromedriver"),
        "/usr/bin/chromedriver",
        "/usr/lib/chromium/chromedriver",
        "/usr/lib/chromium-browser/chromedriver",
        "/app/.apt/usr/bin/chromedriver",
    ]

    chromedriver_candidates.extend(glob.glob("/usr/bin/*chromedriver*"))
    chromedriver_candidates.extend(glob.glob("/usr/lib/**/chromedriver", recursive=True))
    chromedriver_candidates.extend(glob.glob("/app/.apt/usr/bin/*chromedriver*"))

    chromedriver_path = None

    for candidate in chromedriver_candidates:
        if candidate and os.path.exists(candidate) and os.access(candidate, os.X_OK):
            chromedriver_path = candidate
            break

    if chromedriver_path:
        service = Service(chromedriver_path)
    else:
        service = Service(ChromeDriverManager().install())

    return webdriver.Chrome(service=service, options=options)


def wait_for_page_ready(driver, timeout: int = 25):
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )
    time.sleep(2)


def collect_rendered_html(driver) -> str:
    html_parts = [driver.page_source]

    frames = driver.find_elements(By.TAG_NAME, "iframe")

    for frame in frames:
        try:
            driver.switch_to.frame(frame)
            html_parts.append(driver.page_source)
            driver.switch_to.default_content()
        except Exception:
            driver.switch_to.default_content()

    return "\n".join(html_parts)


def try_click_access_mercuriale(driver) -> bool:
    xpath = (
        "//*[contains(translate(normalize-space(.), "
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZÉÈÊÀÂÎÔÛÇ', "
        "'abcdefghijklmnopqrstuvwxyzéèêàâîôûç'), "
        "'accès mercuriale') "
        "or contains(translate(normalize-space(.), "
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZÉÈÊÀÂÎÔÛÇ', "
        "'abcdefghijklmnopqrstuvwxyzéèêàâîôûç'), "
        "'acces mercuriale')]"
    )

    try:
        elements = driver.find_elements(By.XPATH, xpath)

        for el in elements:
            tag = el.tag_name.lower()
            onclick = el.get_attribute("onclick")

            if tag in ["a", "button"] or onclick:
                driver.execute_script("arguments[0].click();", el)
                wait_for_page_ready(driver, timeout=15)
                return True

    except Exception:
        pass

    return False


def try_select_port(driver, port_name: str, port_id: int) -> bool:
    selected = False
    selects = driver.find_elements(By.TAG_NAME, "select")

    for select_el in selects:
        try:
            select = Select(select_el)

            for option in select.options:
                value = (option.get_attribute("value") or "").strip()
                text = option.text.strip()

                if value == str(port_id):
                    select.select_by_value(value)
                    selected = True
                    break

                if port_name.lower() in text.lower():
                    select.select_by_visible_text(text)
                    selected = True
                    break

            if selected:
                driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('change', { bubbles: true }));",
                    select_el,
                )
                time.sleep(1)
                break

        except Exception:
            continue

    return selected


def try_submit_search(driver) -> bool:
    xpaths = [
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'rechercher')]",
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'chercher')]",
        "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'search')]",
        "//input[@type='submit']",
        "//button[@type='submit']",
        "//input[contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'rechercher')]",
        "//input[contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'chercher')]",
    ]

    for xpath in xpaths:
        try:
            buttons = driver.find_elements(By.XPATH, xpath)

            for btn in buttons:
                if btn.is_displayed() and btn.is_enabled():
                    driver.execute_script("arguments[0].click();", btn)
                    wait_for_page_ready(driver, timeout=20)
                    return True

        except Exception:
            continue

    return False


def fetch_rendered_port_page(driver, port_name: str, port_id: int, page: int = 1) -> str:
    if page <= 1:
        url = f"{BASE_URL}?search-delegation={port_id}"
    else:
        url = f"{BASE_URL}?search-delegation={port_id}&page={page}"

    driver.get(url)
    wait_for_page_ready(driver)

    try_click_access_mercuriale(driver)

    selected = try_select_port(driver, port_name, port_id)

    if selected:
        try_submit_search(driver)
        time.sleep(3)

    return collect_rendered_html(driver)


def scrape_port_selenium(port_name, port_id, max_pages, progress_bar, status_text) -> pd.DataFrame:
    all_records = []
    driver = None

    try:
        driver = make_driver()

        status_text.text(f"3. Selenium fallback — opening ONP — {port_name}")

        first_html = fetch_rendered_port_page(driver, port_name, port_id, page=1)

        total_pages = detect_total_pages(first_html)

        if max_pages > 0:
            total_pages = min(total_pages, max_pages)

        records = parse_table(first_html, port_name, 1)
        all_records.extend(records)

        progress_bar.progress(1 / max(total_pages, 1))
        status_text.text(f"3. Selenium — page 1/{total_pages} — {len(records)} records")

        if not records:
            time.sleep(3)
            retry_html = collect_rendered_html(driver)
            retry_records = parse_table(retry_html, port_name, 1)

            if retry_records:
                all_records.extend(retry_records)
                status_text.text(
                    f"3. Selenium — page 1/{total_pages} — "
                    f"{len(retry_records)} records after retry"
                )
            else:
                status_text.text(
                    f"Selenium — {port_name}: no readable market table in rendered HTML"
                )

        for page in range(2, total_pages + 1):
            try:
                html = fetch_rendered_port_page(driver, port_name, port_id, page=page)
                records = parse_table(html, port_name, page)

                if not records:
                    status_text.text(f"Selenium — page {page}: no data")
                    break

                all_records.extend(records)

                progress_bar.progress(page / max(total_pages, 1))
                status_text.text(
                    f"3. Selenium — page {page}/{total_pages} — "
                    f"{len(all_records)} total records"
                )

                time.sleep(1.5)

            except Exception as e:
                status_text.text(f"Selenium page {page} error: {e}")
                continue

    finally:
        if driver is not None:
            driver.quit()

    return build_dataframe(all_records)


# ──────────────────────────────────────────────
# HYBRID SCRAPER
# ──────────────────────────────────────────────

def scrape_port(port_name, port_id, max_pages, progress_bar, status_text) -> pd.DataFrame:
    # 1. Static HTML
    try:
        status_text.text(f"1. Trying static HTML — {port_name}")

        df_static = scrape_port_static(
            port_name,
            port_id,
            max_pages,
            progress_bar,
            status_text,
        )

        if df_static is not None and not df_static.empty:
            status_text.text(
                f"{port_name}: {len(df_static)} records found with static HTML"
            )
            return df_static

    except Exception as e:
        status_text.text(f"Static HTML failed — {e}")

    # 2. Hidden API
    try:
        status_text.text(f"2. Trying hidden API discovery — {port_name}")

        df_api = scrape_port_api(
            port_name,
            port_id,
            max_pages,
            progress_bar,
            status_text,
        )

        if df_api is not None and not df_api.empty:
            status_text.text(
                f"{port_name}: {len(df_api)} records found through hidden API"
            )
            return df_api

    except Exception as e:
        status_text.text(f"Hidden API discovery failed — {e}")

    # 3. Selenium
    try:
        status_text.text(f"3. Trying Selenium fallback — {port_name}")

        df_selenium = scrape_port_selenium(
            port_name,
            port_id,
            max_pages,
            progress_bar,
            status_text,
        )

        if df_selenium is not None and not df_selenium.empty:
            status_text.text(
                f"{port_name}: {len(df_selenium)} records found with Selenium"
            )
            return df_selenium

    except Exception as e:
        status_text.text(f"Selenium fallback failed — {e}")

    return pd.DataFrame()


# ──────────────────────────────────────────────
# STREAMLIT APP
# ──────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Fish Prices — ONP Morocco",
        page_icon="🐟",
        layout="wide",
    )

    st.markdown(
        """
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');

            .block-container { padding-top: 2rem; }

            .main-title {
                font-family: 'Inter', sans-serif;
                font-size: 2.8rem;
                font-weight: 700;
                color: #0C4B8E;
                text-align: center;
                margin-bottom: 0;
            }

            .subtitle {
                font-family: 'Inter', sans-serif;
                text-align: center;
                color: #5A7DA5;
                font-size: 1.1rem;
                margin-bottom: 2rem;
            }

            .section-header {
                font-family: 'Inter', sans-serif;
                font-size: 1.4rem;
                font-weight: 600;
                color: #0C4B8E;
                border-bottom: 3px solid #0C4B8E;
                padding-bottom: 0.5rem;
                margin-top: 2rem;
                margin-bottom: 1rem;
            }

            .info-box {
                background: #EBF5FB;
                border-left: 5px solid #2E86C1;
                padding: 1rem 1.5rem;
                border-radius: 0 8px 8px 0;
                margin: 1rem 0;
                font-size: 1rem;
            }

            .success-box {
                background: #EAFAF1;
                border-left: 5px solid #27AE60;
                padding: 1rem 1.5rem;
                border-radius: 0 8px 8px 0;
                margin: 1rem 0;
            }

            .warning-box {
                background: #FFF8E1;
                border-left: 5px solid #F39C12;
                padding: 1rem 1.5rem;
                border-radius: 0 8px 8px 0;
                margin: 1rem 0;
            }

            .stMetric {
                background: #F8F9FA;
                border-radius: 10px;
                padding: 0.5rem;
                border: 1px solid #E5E8EB;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="main-title">🐟 Fish Prices — Morocco</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="subtitle">Office National des Pêches — Fish market data</div>',
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.markdown("## 🐟 ONP Morocco")
        st.markdown("---")
        st.markdown("## Settings")

        st.markdown("### Port selection")

        port_mode = st.radio(
            "Selection mode:",
            ["Single port", "Multiple ports", "All ports"],
            index=0,
            label_visibility="collapsed",
        )

        selected_ports = {}
        port_list = {k: v for k, v in DELEGATIONS.items() if v != -1}

        if port_mode == "Single port":
            port_name = st.selectbox("Port:", list(port_list.keys()), index=0)
            selected_ports = {port_name: port_list[port_name]}

        elif port_mode == "Multiple ports":
            port_names = st.multiselect("Ports:", list(port_list.keys()), default=[])
            selected_ports = {n: port_list[n] for n in port_names}

        else:
            selected_ports = port_list

        st.markdown("---")
        st.markdown("### Number of pages")

        max_pages = st.slider(
            "Max pages per port:",
            min_value=1,
            max_value=20,
            value=3,
            help="Start with 1 to 3 pages. Increase only if needed.",
        )

        st.markdown("---")
        st.markdown("### Date filter")

        use_date = st.checkbox("Enable date filter")

        if use_date:
            date_from = st.date_input("From:", value=date.today() - timedelta(days=30))
            date_to = st.date_input("To:", value=date.today())
        else:
            date_from = None
            date_to = None

        st.markdown("---")

        go = st.button(
            "Search",
            type="primary",
            use_container_width=True,
        )

        clear_cache = st.button(
            "Reset data",
            use_container_width=True,
        )

        st.markdown("---")

        st.markdown(
            '<div style="text-align:center; color:#999; font-size:0.8rem;">'
            "Data source: onp.ma<br>Hybrid mode: HTML → API → Selenium"
            "</div>",
            unsafe_allow_html=True,
        )

    if "data" not in st.session_state:
        st.session_state.data = None

    if clear_cache:
        st.session_state.data = None
        st.success("Data reset.")

    if go:
        if not selected_ports:
            st.error("Please select at least one port.")
            return

        all_data = []
        total_ports = len(selected_ports)

        st.markdown(
            '<div class="section-header">Collection in progress...</div>',
            unsafe_allow_html=True,
        )

        st.markdown(
            '<div class="warning-box">'
            "The app tries 3 methods in this order: "
            "<strong>static HTML</strong>, then <strong>hidden API/JSON</strong>, "
            "then <strong>Selenium</strong> if needed."
            "</div>",
            unsafe_allow_html=True,
        )

        overall = st.progress(0)

        for i, (name, pid) in enumerate(selected_ports.items()):
            st.markdown(f"**{name}** ({i + 1}/{total_ports})")

            prog = st.progress(0)
            status = st.empty()

            try:
                df = scrape_port(name, pid, max_pages, prog, status)

                if not df.empty:
                    all_data.append(df)
                    status.text(f"{name}: {len(df)} records")
                else:
                    status.text(f"{name}: no data found")

            except Exception as e:
                status.text(f"{name}: error — {e}")

            overall.progress((i + 1) / total_ports)

            if i < total_ports - 1:
                time.sleep(1)

        if all_data:
            st.session_state.data = pd.concat(all_data, ignore_index=True)

            total = len(st.session_state.data)

            st.markdown(
                f'<div class="success-box">Done. <strong>{total}</strong> '
                f"records collected from <strong>{total_ports}</strong> port(s).</div>",
                unsafe_allow_html=True,
            )

        else:
            st.session_state.data = None
            st.warning(
                "No data collected. If Selenium failed with Chrome/Chromium, "
                "check that packages.txt exists at the repository root and reboot the app."
            )

    df = st.session_state.data

    if df is not None and not df.empty:
        filtered = df.copy()

        if use_date and date_from and date_to:
            filtered["Sale date"] = pd.to_datetime(
                filtered["Sale date"],
                errors="coerce",
            )

            filtered = filtered[
                (filtered["Sale date"] >= pd.Timestamp(date_from))
                & (filtered["Sale date"] <= pd.Timestamp(date_to))
            ]

        with st.sidebar:
            st.markdown("---")
            st.markdown("### Filters")

            if "Species" in filtered.columns:
                species_list = sorted(filtered["Species"].dropna().unique().tolist())

                selected_species = st.multiselect(
                    "Species:",
                    species_list,
                    default=[],
                )

                if selected_species:
                    filtered = filtered[filtered["Species"].isin(selected_species)]

            if "Price (DH/KG)" in filtered.columns:
                prices = filtered["Price (DH/KG)"].dropna()

                if not prices.empty and prices.min() < prices.max():
                    price_range = st.slider(
                        "Price (DH/KG):",
                        float(prices.min()),
                        float(prices.max()),
                        (float(prices.min()), float(prices.max())),
                    )

                    filtered = filtered[
                        (filtered["Price (DH/KG)"] >= price_range[0])
                        & (filtered["Price (DH/KG)"] <= price_range[1])
                    ]

        st.markdown(
            '<div class="section-header">Summary</div>',
            unsafe_allow_html=True,
        )

        if filtered.empty:
            st.warning("No data after applying filters.")
            return

        c1, c2, c3, c4, c5 = st.columns(5)

        c1.metric("Records", f"{len(filtered):,}")
        c2.metric("Species", f"{filtered['Species'].nunique():,}")
        c3.metric("Ports", f"{filtered['Port'].nunique():,}")

        avg_price = filtered["Price (DH/KG)"].mean()
        c4.metric("Average price", f"{avg_price:,.2f} DH" if pd.notna(avg_price) else "—")

        total_weight = filtered["Weight (KG)"].sum()
        c5.metric("Total weight", f"{total_weight:,.0f} KG" if pd.notna(total_weight) else "—")

        st.markdown(
            '<div class="section-header">Data table</div>',
            unsafe_allow_html=True,
        )

        st.dataframe(
            filtered,
            use_container_width=True,
            hide_index=True,
            height=400,
        )

        st.markdown(
            '<div class="section-header">Charts</div>',
            unsafe_allow_html=True,
        )

        tab1, tab2, tab3, tab4 = st.tabs(
            [
                "Species",
                "Trends",
                "Ports",
                "Distribution",
            ]
        )

        with tab1:
            col_a, col_b = st.columns(2)

            with col_a:
                species_weight = (
                    filtered.groupby("Species")["Weight (KG)"]
                    .sum()
                    .sort_values(ascending=True)
                    .tail(15)
                    .reset_index()
                )

                if not species_weight.empty:
                    fig = px.bar(
                        species_weight,
                        x="Weight (KG)",
                        y="Species",
                        orientation="h",
                        title="Top 15 species by weight",
                        color="Weight (KG)",
                        color_continuous_scale="Blues",
                    )

                    st.plotly_chart(fig, use_container_width=True)

            with col_b:
                species_amount = (
                    filtered.groupby("Species")["Amount (DH)"]
                    .sum()
                    .sort_values(ascending=True)
                    .tail(15)
                    .reset_index()
                )

                if not species_amount.empty:
                    fig = px.bar(
                        species_amount,
                        x="Amount (DH)",
                        y="Species",
                        orientation="h",
                        title="Top 15 species by amount",
                        color="Amount (DH)",
                        color_continuous_scale="Greens",
                    )

                    st.plotly_chart(fig, use_container_width=True)

            species_price = (
                filtered.groupby("Species")["Price (DH/KG)"]
                .mean()
                .sort_values(ascending=False)
                .head(20)
                .reset_index()
            )

            if not species_price.empty:
                fig = px.bar(
                    species_price,
                    x="Species",
                    y="Price (DH/KG)",
                    title="Most expensive species — average DH/KG",
                    color="Price (DH/KG)",
                    color_continuous_scale="Reds",
                )

                fig.update_layout(xaxis_tickangle=-45)

                st.plotly_chart(fig, use_container_width=True)

        with tab2:
            dated_data = filtered.dropna(subset=["Sale date", "Price (DH/KG)"]).copy()

            if not dated_data.empty:
                dated_data["Sale date"] = pd.to_datetime(
                    dated_data["Sale date"],
                    errors="coerce",
                )

                dated_data = dated_data.dropna(subset=["Sale date"])

                if not dated_data.empty:
                    trend = (
                        dated_data.groupby("Sale date")["Price (DH/KG)"]
                        .mean()
                        .reset_index()
                        .sort_values("Sale date")
                    )

                    fig = px.line(
                        trend,
                        x="Sale date",
                        y="Price (DH/KG)",
                        title="Average price over time",
                    )

                    fig.update_layout(hovermode="x unified")

                    st.plotly_chart(fig, use_container_width=True)

                    top5 = dated_data["Species"].value_counts().head(5).index.tolist()
                    top5_data = dated_data[dated_data["Species"].isin(top5)]

                    if not top5_data.empty:
                        trend_top5 = (
                            top5_data.groupby(["Sale date", "Species"])["Price (DH/KG)"]
                            .mean()
                            .reset_index()
                        )

                        fig = px.line(
                            trend_top5,
                            x="Sale date",
                            y="Price (DH/KG)",
                            color="Species",
                            title="Trends — Top 5 species",
                        )

                        st.plotly_chart(fig, use_container_width=True)

            else:
                st.info("No date data available.")

        with tab3:
            if filtered["Port"].nunique() > 1:
                col_a, col_b = st.columns(2)

                with col_a:
                    port_weight = (
                        filtered.groupby("Port")["Weight (KG)"]
                        .sum()
                        .reset_index()
                    )

                    fig = px.pie(
                        port_weight,
                        values="Weight (KG)",
                        names="Port",
                        title="Volume by port",
                    )

                    st.plotly_chart(fig, use_container_width=True)

                with col_b:
                    port_amount = (
                        filtered.groupby("Port")["Amount (DH)"]
                        .sum()
                        .reset_index()
                    )

                    fig = px.pie(
                        port_amount,
                        values="Amount (DH)",
                        names="Port",
                        title="Revenue by port",
                    )

                    st.plotly_chart(fig, use_container_width=True)

                port_price = (
                    filtered.groupby("Port")["Price (DH/KG)"]
                    .mean()
                    .sort_values(ascending=False)
                    .reset_index()
                )

                fig = px.bar(
                    port_price,
                    x="Port",
                    y="Price (DH/KG)",
                    title="Average price by port",
                    color="Price (DH/KG)",
                    color_continuous_scale="Viridis",
                )

                fig.update_layout(xaxis_tickangle=-45)

                st.plotly_chart(fig, use_container_width=True)

            else:
                st.info("Select multiple ports to see comparisons.")

        with tab4:
            price_col = filtered["Price (DH/KG)"].dropna()

            if not price_col.empty:
                fig = px.histogram(
                    filtered,
                    x="Price (DH/KG)",
                    nbins=50,
                    title="Price distribution",
                    color_discrete_sequence=["#3498db"],
                )

                st.plotly_chart(fig, use_container_width=True)

                box_col = "Port" if filtered["Port"].nunique() > 1 else "Species"

                fig = px.box(
                    filtered,
                    x=box_col,
                    y="Price (DH/KG)",
                    title="Price box plot",
                    color=box_col,
                )

                fig.update_layout(xaxis_tickangle=-45)

                st.plotly_chart(fig, use_container_width=True)

        st.markdown(
            '<div class="section-header">Download data</div>',
            unsafe_allow_html=True,
        )

        c1, c2, c3 = st.columns(3)

        with c1:
            csv_buf = io.StringIO()

            filtered.to_csv(
                csv_buf,
                index=False,
                sep=";",
                encoding="utf-8",
            )

            st.download_button(
                "Download CSV",
                csv_buf.getvalue(),
                f"onp_prices_{date.today()}.csv",
                "text/csv",
                use_container_width=True,
            )

        with c2:
            excel_buf = io.BytesIO()

            filtered.to_excel(
                excel_buf,
                index=False,
                engine="openpyxl",
            )

            st.download_button(
                "Download Excel",
                excel_buf.getvalue(),
                f"onp_prices_{date.today()}.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

        with c3:
            json_str = filtered.to_json(
                orient="records",
                force_ascii=False,
                indent=2,
            )

            st.download_button(
                "Download JSON",
                json_str,
                f"onp_prices_{date.today()}.json",
                "application/json",
                use_container_width=True,
            )

    elif not go:
        st.markdown("---")

        st.markdown(
            '<div class="info-box">'
            "Configure your settings in the sidebar, then click "
            "<strong>Search</strong> to start."
            "</div>",
            unsafe_allow_html=True,
        )

        c1, c2, c3 = st.columns(3)

        with c1:
            st.markdown("### 1. Choose")
            st.markdown("Select one or more Moroccan fishing ports.")

        with c2:
            st.markdown("### 2. Collect")
            st.markdown("The app tries static HTML, hidden API, then Selenium.")

        with c3:
            st.markdown("### 3. Analyze")
            st.markdown("View charts and download the data as Excel/CSV.")

    st.markdown("---")

    st.markdown(
        '<div style="text-align:center; color:#aaa; font-size:0.85rem;">'
        'Data source: <a href="https://www.onp.ma">Office National des Pêches</a> — Morocco'
        "</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
