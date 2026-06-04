#!/usr/bin/env python3
"""
🐟 ONP Fish Market Dashboard
Office National des Pêches — Morocco
"""

import io
import re
import time
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
import plotly.express as px
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
    "-- Choisir un port --": -1,
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
# SELENIUM / SCRAPING FUNCTIONS
# ──────────────────────────────────────────────

def make_driver():
    """
    Creates a Selenium Chrome/Chromium driver compatible with Streamlit Cloud
    and other live Linux deployments.
    """
    import os
    import shutil

    options = Options()

    # More compatible than --headless=new on some cloud Linux images
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--remote-debugging-port=9222")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"--user-agent={USER_AGENT}")

    # Find Chromium/Chrome binary on live deployment
    chrome_binary_candidates = [
        os.environ.get("CHROME_BIN"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ]

    chrome_binary = None

    for candidate in chrome_binary_candidates:
        if candidate and os.path.exists(candidate):
            chrome_binary = candidate
            break

    if not chrome_binary:
        raise RuntimeError(
            "Chrome/Chromium binary not found. "
            "For Streamlit Cloud, add a file named packages.txt containing: "
            "chromium and chromium-driver."
        )

    options.binary_location = chrome_binary

    # Find ChromeDriver on live deployment
    chromedriver_candidates = [
        os.environ.get("CHROMEDRIVER_PATH"),
        shutil.which("chromedriver"),
        "/usr/bin/chromedriver",
        "/usr/lib/chromium/chromedriver",
        "/usr/lib/chromium-browser/chromedriver",
    ]

    chromedriver_path = None

    for candidate in chromedriver_candidates:
        if candidate and os.path.exists(candidate):
            chromedriver_path = candidate
            break

    if chromedriver_path:
        service = Service(chromedriver_path)
    else:
        # Fallback for environments where webdriver-manager can download driver
        service = Service(ChromeDriverManager().install())

    return webdriver.Chrome(service=service, options=options)


def wait_for_page_ready(driver, timeout: int = 25):
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )
    time.sleep(2)


def collect_rendered_html(driver) -> str:
    """
    Collects the rendered HTML from the main page and from iframes.
    Some websites render the actual table inside iframes.
    """
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
    """
    Some ONP pages show an 'Accès Mercuriale' entry point before the data.
    This tries to click it if present.
    """
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
    """
    Tries to select the port from any native <select> element.
    It matches by value or visible text.
    """
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
    """
    Clicks a search/submit button if the website requires one after choosing a port.
    """
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
    """
    Opens the ONP page with Selenium, lets JavaScript render, selects the port if possible,
    and returns the rendered HTML.
    """
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


def normalize_text(text: Optional[str]) -> str:
    if not text:
        return "Inconnu"

    text = re.sub(r"\s+", " ", text.strip())
    return text.title()


def parse_date_value(text: Optional[str]) -> Optional[date]:
    if not text:
        return None

    text = text.strip()

    formats = [
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d/%m/%y",
        "%Y/%m/%d",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    return None


def parse_number(text: Optional[str]) -> Optional[float]:
    if not text:
        return None

    text = text.strip()

    if text in ["", "-", "—", "N/A", "n/a"]:
        return None

    value = (
        text.replace("\xa0", "")
        .replace(" ", "")
        .replace("DH", "")
        .replace("MAD", "")
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


def parse_table(html: str, delegation_name: str, page_num: int) -> list:
    """
    More tolerant parser for the ONP market table.
    It tries to identify species, date, weight, amount and price columns.
    """
    soup = BeautifulSoup(html, "html.parser")
    records = []

    tables = soup.find_all("table")

    if not tables:
        return records

    candidate_tables = []

    for table in tables:
        table_text = table.get_text(" ", strip=True).lower()

        score = 0

        for keyword in [
            "espèce",
            "espece",
            "poisson",
            "prix",
            "poids",
            "quantité",
            "quantite",
            "montant",
            "date",
            "vente",
            "kg",
            "dh",
        ]:
            if keyword in table_text:
                score += 1

        rows_count = len(table.find_all("tr"))

        if rows_count > 1:
            score += rows_count

        candidate_tables.append((score, table))

    candidate_tables.sort(key=lambda x: x[0], reverse=True)

    table = candidate_tables[0][1]
    rows = table.find_all("tr")

    if len(rows) < 2:
        return records

    header_row = rows[0]
    headers = [
        h.get_text(" ", strip=True).lower()
        for h in header_row.find_all(["th", "td"])
    ]

    col_map = {}

    for idx, h in enumerate(headers):
        clean_h = (
            h.replace("è", "e")
            .replace("é", "e")
            .replace("ê", "e")
            .replace("à", "a")
            .replace("â", "a")
            .replace("î", "i")
            .replace("ô", "o")
            .replace("û", "u")
            .replace("ç", "c")
        )

        if any(x in clean_h for x in ["espece", "poisson", "produit", "designation"]):
            col_map["espece"] = idx

        elif "date" in clean_h:
            col_map["date_vente"] = idx

        elif any(x in clean_h for x in ["poids", "quantite", "kg", "volume"]):
            col_map["poids_kg"] = idx

        elif any(x in clean_h for x in ["prix", "moyen", "dh/kg", "dh / kg"]):
            col_map["prix_dh_kg"] = idx

        elif any(x in clean_h for x in ["montant", "valeur", "total", "dh", "mad"]):
            col_map["montant_dh"] = idx

    # Fallback if headers are not clear.
    # Common structure can be: species, weight, amount, price, date
    if "espece" not in col_map:
        max_cols = max(len(r.find_all(["td", "th"])) for r in rows)

        if max_cols >= 4:
            col_map = {
                "espece": 0,
                "poids_kg": 1,
                "montant_dh": 2,
                "prix_dh_kg": 3,
            }

            if max_cols >= 5:
                col_map["date_vente"] = 4

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

        espece = get_text("espece")

        if not espece:
            continue

        espece_lower = espece.lower()

        if any(
            kw in espece_lower
            for kw in [
                "espèce",
                "espece",
                "total",
                "sous-total",
                "poids",
                "prix",
                "montant",
                "date",
            ]
        ):
            continue

        records.append(
            {
                "Espèce": normalize_text(espece),
                "Date de vente": parse_date_value(get_text("date_vente")),
                "Poids (KG)": parse_number(get_text("poids_kg")),
                "Montant (DH)": parse_number(get_text("montant_dh")),
                "Prix (DH/KG)": parse_number(get_text("prix_dh_kg")),
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


def scrape_port(port_name, port_id, max_pages, progress_bar, status_text):
    all_records = []
    driver = make_driver()

    try:
        status_text.text(f"🌐 Ouverture ONP — {port_name}")

        first_html = fetch_rendered_port_page(driver, port_name, port_id, page=1)

        total_pages = detect_total_pages(first_html)

        if max_pages > 0:
            total_pages = min(total_pages, max_pages)

        records = parse_table(first_html, port_name, 1)
        all_records.extend(records)

        progress_bar.progress(1 / max(total_pages, 1))
        status_text.text(f"📄 Page 1/{total_pages} — {len(records)} enregistrements")

        if not records:
            time.sleep(3)
            retry_html = collect_rendered_html(driver)
            retry_records = parse_table(retry_html, port_name, 1)

            if retry_records:
                all_records.extend(retry_records)
                status_text.text(
                    f"📄 Page 1/{total_pages} — "
                    f"{len(retry_records)} enregistrements après attente"
                )
            else:
                status_text.text(
                    f"⚠️ {port_name} : aucune table lisible trouvée dans le HTML rendu"
                )

        for page in range(2, total_pages + 1):
            try:
                html = fetch_rendered_port_page(driver, port_name, port_id, page=page)
                records = parse_table(html, port_name, page)

                if not records:
                    status_text.text(f"⚠️ Page {page} : aucune donnée trouvée")
                    break

                all_records.extend(records)

                progress_bar.progress(page / max(total_pages, 1))
                status_text.text(
                    f"📄 Page {page}/{total_pages} — "
                    f"{len(all_records)} enregistrements au total"
                )

                time.sleep(1.5)

            except Exception as e:
                status_text.text(f"⚠️ Erreur page {page}: {e}")
                continue

    finally:
        driver.quit()

    return pd.DataFrame(all_records)


# ──────────────────────────────────────────────
# STREAMLIT APP
# ──────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="🐟 Prix du Poisson — ONP Maroc",
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
        '<div class="main-title">🐟 Prix du Poisson — Maroc</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="subtitle">Office National des Pêches — Données des marchés de poisson</div>',
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.image("https://www.onp.ma/images/logo.png", width=200)
        st.markdown("---")
        st.markdown("## ⚙️ Paramètres")

        st.markdown("### 🏭 Choix du port")

        port_mode = st.radio(
            "Mode de sélection :",
            ["Un seul port", "Plusieurs ports", "Tous les ports"],
            index=0,
            label_visibility="collapsed",
        )

        selected_ports = {}
        port_list = {k: v for k, v in DELEGATIONS.items() if v != -1}

        if port_mode == "Un seul port":
            port_name = st.selectbox("Port :", list(port_list.keys()), index=0)
            selected_ports = {port_name: port_list[port_name]}

        elif port_mode == "Plusieurs ports":
            port_names = st.multiselect("Ports :", list(port_list.keys()), default=[])
            selected_ports = {n: port_list[n] for n in port_names}

        else:
            selected_ports = port_list

        st.markdown("---")
        st.markdown("### 📄 Nombre de pages")

        max_pages = st.slider(
            "Pages max par port :",
            min_value=1,
            max_value=200,
            value=30,
            help="Augmentez ce nombre si le filtre de date ne trouve rien.",
        )

        st.markdown("---")
        st.markdown("### 📅 Filtre par date")

        use_date = st.checkbox("Activer le filtre par date")

        if use_date:
            date_from = st.date_input("Du :", value=date.today() - timedelta(days=30))
            date_to = st.date_input("Au :", value=date.today())
        else:
            date_from = None
            date_to = None

        st.markdown("---")

        go = st.button(
            "🔍 LANCER LA RECHERCHE",
            type="primary",
            use_container_width=True,
        )

        clear_cache = st.button(
            "🧹 Réinitialiser les données",
            use_container_width=True,
        )

        st.markdown("---")

        st.markdown(
            '<div style="text-align:center; color:#999; font-size:0.8rem;">'
            "Données: onp.ma<br>App créée avec ❤️"
            "</div>",
            unsafe_allow_html=True,
        )

    if "data" not in st.session_state:
        st.session_state.data = None

    if clear_cache:
        st.session_state.data = None
        st.success("Données réinitialisées.")

    if go:
        if not selected_ports:
            st.error("⚠️ Veuillez sélectionner au moins un port !")
            return

        all_data = []
        total_ports = len(selected_ports)

        st.markdown(
            '<div class="section-header">🔄 Collecte en cours...</div>',
            unsafe_allow_html=True,
        )

        overall = st.progress(0)

        for i, (name, pid) in enumerate(selected_ports.items()):
            st.markdown(f"**🏭 {name}** ({i + 1}/{total_ports})")

            prog = st.progress(0)
            status = st.empty()

            try:
                df = scrape_port(name, pid, max_pages, prog, status)

                if not df.empty:
                    all_data.append(df)
                    status.text(f"✅ {name} : {len(df)} enregistrements")
                else:
                    status.text(f"⚠️ {name} : aucune donnée trouvée")

            except Exception as e:
                status.text(f"❌ {name} : erreur — {e}")

            overall.progress((i + 1) / total_ports)

            if i < total_ports - 1:
                time.sleep(2)

        if all_data:
            st.session_state.data = pd.concat(all_data, ignore_index=True)

            total = len(st.session_state.data)

            st.markdown(
                f'<div class="success-box">✅ Terminé ! <strong>{total}</strong> '
                f"enregistrements collectés de <strong>{total_ports}</strong> port(s).</div>",
                unsafe_allow_html=True,
            )

        else:
            st.session_state.data = None
            st.warning("Aucune donnée collectée.")

    df = st.session_state.data

    if df is not None and not df.empty:
        filtered = df.copy()

        if use_date and date_from and date_to:
            filtered["Date de vente"] = pd.to_datetime(
                filtered["Date de vente"],
                errors="coerce",
            )

            filtered = filtered[
                (filtered["Date de vente"] >= pd.Timestamp(date_from))
                & (filtered["Date de vente"] <= pd.Timestamp(date_to))
            ]

        with st.sidebar:
            st.markdown("---")
            st.markdown("### 🎯 Filtres")

            if "Espèce" in filtered.columns:
                species_list = sorted(filtered["Espèce"].dropna().unique().tolist())

                sel_species = st.multiselect(
                    "🐟 Espèces :",
                    species_list,
                    default=[],
                )

                if sel_species:
                    filtered = filtered[filtered["Espèce"].isin(sel_species)]

            if "Prix (DH/KG)" in filtered.columns:
                prices = filtered["Prix (DH/KG)"].dropna()

                if not prices.empty and prices.min() < prices.max():
                    p_range = st.slider(
                        "💰 Prix (DH/KG) :",
                        float(prices.min()),
                        float(prices.max()),
                        (float(prices.min()), float(prices.max())),
                    )

                    filtered = filtered[
                        (filtered["Prix (DH/KG)"] >= p_range[0])
                        & (filtered["Prix (DH/KG)"] <= p_range[1])
                    ]

        st.markdown(
            '<div class="section-header">📊 Résumé</div>',
            unsafe_allow_html=True,
        )

        if filtered.empty:
            st.warning("Aucune donnée après application des filtres.")
            return

        c1, c2, c3, c4, c5 = st.columns(5)

        c1.metric("📋 Enregistrements", f"{len(filtered):,}")
        c2.metric("🐟 Espèces", f"{filtered['Espèce'].nunique():,}")
        c3.metric("🏭 Ports", f"{filtered['Port'].nunique():,}")

        avg_p = filtered["Prix (DH/KG)"].mean()
        c4.metric("💰 Prix moyen", f"{avg_p:,.2f} DH" if pd.notna(avg_p) else "—")

        tot_w = filtered["Poids (KG)"].sum()
        c5.metric("⚖️ Poids total", f"{tot_w:,.0f} KG" if pd.notna(tot_w) else "—")

        st.markdown(
            '<div class="section-header">📋 Tableau des données</div>',
            unsafe_allow_html=True,
        )

        st.dataframe(
            filtered,
            use_container_width=True,
            hide_index=True,
            height=400,
        )

        st.markdown(
            '<div class="section-header">📈 Graphiques</div>',
            unsafe_allow_html=True,
        )

        tab1, tab2, tab3, tab4 = st.tabs(
            [
                "🐟 Espèces",
                "📈 Tendances",
                "🏭 Ports",
                "📊 Distribution",
            ]
        )

        with tab1:
            col_a, col_b = st.columns(2)

            with col_a:
                sw = (
                    filtered.groupby("Espèce")["Poids (KG)"]
                    .sum()
                    .sort_values(ascending=True)
                    .tail(15)
                    .reset_index()
                )

                if not sw.empty:
                    fig = px.bar(
                        sw,
                        x="Poids (KG)",
                        y="Espèce",
                        orientation="h",
                        title="🐟 Top 15 — Poids (KG)",
                        color="Poids (KG)",
                        color_continuous_scale="Blues",
                    )

                    st.plotly_chart(fig, use_container_width=True)

            with col_b:
                sr = (
                    filtered.groupby("Espèce")["Montant (DH)"]
                    .sum()
                    .sort_values(ascending=True)
                    .tail(15)
                    .reset_index()
                )

                if not sr.empty:
                    fig = px.bar(
                        sr,
                        x="Montant (DH)",
                        y="Espèce",
                        orientation="h",
                        title="💰 Top 15 — Montant (DH)",
                        color="Montant (DH)",
                        color_continuous_scale="Greens",
                    )

                    st.plotly_chart(fig, use_container_width=True)

            sp = (
                filtered.groupby("Espèce")["Prix (DH/KG)"]
                .mean()
                .sort_values(ascending=False)
                .head(20)
                .reset_index()
            )

            if not sp.empty:
                fig = px.bar(
                    sp,
                    x="Espèce",
                    y="Prix (DH/KG)",
                    title="💎 Espèces les plus chères — DH/KG moyen",
                    color="Prix (DH/KG)",
                    color_continuous_scale="Reds",
                )

                fig.update_layout(xaxis_tickangle=-45)

                st.plotly_chart(fig, use_container_width=True)

        with tab2:
            dd = filtered.dropna(subset=["Date de vente", "Prix (DH/KG)"]).copy()

            if not dd.empty:
                dd["Date de vente"] = pd.to_datetime(
                    dd["Date de vente"],
                    errors="coerce",
                )

                dd = dd.dropna(subset=["Date de vente"])

                if not dd.empty:
                    trend = (
                        dd.groupby("Date de vente")["Prix (DH/KG)"]
                        .mean()
                        .reset_index()
                        .sort_values("Date de vente")
                    )

                    fig = px.line(
                        trend,
                        x="Date de vente",
                        y="Prix (DH/KG)",
                        title="📈 Prix moyen dans le temps",
                    )

                    fig.update_layout(hovermode="x unified")

                    st.plotly_chart(fig, use_container_width=True)

                    top5 = dd["Espèce"].value_counts().head(5).index.tolist()
                    t5 = dd[dd["Espèce"].isin(top5)]

                    if not t5.empty:
                        ts = (
                            t5.groupby(["Date de vente", "Espèce"])["Prix (DH/KG)"]
                            .mean()
                            .reset_index()
                        )

                        fig = px.line(
                            ts,
                            x="Date de vente",
                            y="Prix (DH/KG)",
                            color="Espèce",
                            title="📈 Tendances — Top 5 espèces",
                        )

                        st.plotly_chart(fig, use_container_width=True)

            else:
                st.info("Pas de données de date disponibles.")

        with tab3:
            if filtered["Port"].nunique() > 1:
                col_a, col_b = st.columns(2)

                with col_a:
                    pv = (
                        filtered.groupby("Port")["Poids (KG)"]
                        .sum()
                        .reset_index()
                    )

                    fig = px.pie(
                        pv,
                        values="Poids (KG)",
                        names="Port",
                        title="🏭 Volume par port",
                    )

                    st.plotly_chart(fig, use_container_width=True)

                with col_b:
                    pr = (
                        filtered.groupby("Port")["Montant (DH)"]
                        .sum()
                        .reset_index()
                    )

                    fig = px.pie(
                        pr,
                        values="Montant (DH)",
                        names="Port",
                        title="💰 Revenu par port",
                    )

                    st.plotly_chart(fig, use_container_width=True)

                pp = (
                    filtered.groupby("Port")["Prix (DH/KG)"]
                    .mean()
                    .sort_values(ascending=False)
                    .reset_index()
                )

                fig = px.bar(
                    pp,
                    x="Port",
                    y="Prix (DH/KG)",
                    title="📊 Prix moyen par port",
                    color="Prix (DH/KG)",
                    color_continuous_scale="Viridis",
                )

                fig.update_layout(xaxis_tickangle=-45)

                st.plotly_chart(fig, use_container_width=True)

            else:
                st.info("Sélectionnez plusieurs ports pour voir la comparaison.")

        with tab4:
            pd_col = filtered["Prix (DH/KG)"].dropna()

            if not pd_col.empty:
                fig = px.histogram(
                    filtered,
                    x="Prix (DH/KG)",
                    nbins=50,
                    title="📊 Distribution des prix",
                    color_discrete_sequence=["#3498db"],
                )

                st.plotly_chart(fig, use_container_width=True)

                box_col = "Port" if filtered["Port"].nunique() > 1 else "Espèce"

                fig = px.box(
                    filtered,
                    x=box_col,
                    y="Prix (DH/KG)",
                    title="📦 Box Plot des prix",
                    color=box_col,
                )

                fig.update_layout(xaxis_tickangle=-45)

                st.plotly_chart(fig, use_container_width=True)

        st.markdown(
            '<div class="section-header">📥 Télécharger les données</div>',
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
                "📄 Télécharger CSV",
                csv_buf.getvalue(),
                f"onp_prix_{date.today()}.csv",
                "text/csv",
                use_container_width=True,
            )

        with c2:
            xl_buf = io.BytesIO()

            filtered.to_excel(
                xl_buf,
                index=False,
                engine="openpyxl",
            )

            st.download_button(
                "📊 Télécharger Excel",
                xl_buf.getvalue(),
                f"onp_prix_{date.today()}.xlsx",
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
                "📋 Télécharger JSON",
                json_str,
                f"onp_prix_{date.today()}.json",
                "application/json",
                use_container_width=True,
            )

    elif not go:
        st.markdown("---")

        st.markdown(
            '<div class="info-box">'
            "👈 <strong>Configurez vos paramètres dans le menu à gauche</strong> "
            "puis cliquez sur <strong>LANCER LA RECHERCHE</strong> pour commencer."
            "</div>",
            unsafe_allow_html=True,
        )

        c1, c2, c3 = st.columns(3)

        with c1:
            st.markdown("### 1️⃣ Choisir")
            st.markdown("Sélectionnez un ou plusieurs ports de pêche marocains.")

        with c2:
            st.markdown("### 2️⃣ Collecter")
            st.markdown("L'application récupère automatiquement les données de l'ONP.")

        with c3:
            st.markdown("### 3️⃣ Analyser")
            st.markdown("Visualisez les graphiques et téléchargez en Excel/CSV.")

    st.markdown("---")

    st.markdown(
        '<div style="text-align:center; color:#aaa; font-size:0.85rem;">'
        '🐟 Données: <a href="https://www.onp.ma">Office National des Pêches</a> — Maroc'
        "</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
