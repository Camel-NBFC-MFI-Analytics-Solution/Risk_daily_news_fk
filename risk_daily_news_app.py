# -*- coding: utf-8 -*-
"""Risk Daily News - single-file Streamlit risk-intelligence dashboard.

Focus: India Microfinance, JLG/SHG, NBFC and Climate Risk.
Bank-only news is excluded; mixed bank/NBFC stories are retained.

Install:
    pip install streamlit pandas openpyxl reportlab

Run:
    python -m streamlit run risk_daily_news.py --server.port 8501 --server.address 0.0.0.0
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import re
import sqlite3
import ssl
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import streamlit as st

# -----------------------------------------------------------------------------
# App configuration
# -----------------------------------------------------------------------------
IST = timezone(timedelta(hours=5, minutes=30))
LOOKBACK_HOURS = 48
ARCHIVE_DAYS = 30
TIMEOUT = 18
MAX_WORKERS = 12
REFRESH_SECONDS = 300
DB_PATH = Path(__file__).with_name("risk_daily_news.db")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/124 Safari/537.36"
)

STREAM_MF = "Microfinance / JLG / NBFC"
STREAM_CLIMATE = "Climate Risk"
SEVERITY_ORDER = ["Critical", "High", "Watch", "Information"]

st.set_page_config(
    page_title="Risk Daily News",
    page_icon="📰",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(
    f'<meta http-equiv="refresh" content="{REFRESH_SECONDS}">',
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# Search coverage
# -----------------------------------------------------------------------------
MF_QUERIES = [
    "microfinance India",
    "microfinance institutions MFI India",
    "NBFC-MFI India",
    "MFIN microfinance",
    "Sa-Dhan microfinance",
    "RBI microfinance",
    "JLG loans India",
    '"joint liability group" loans India',
    '"self help group" microfinance India',
    "microfinance stress delinquency NPA default",
    "microfinance collection efficiency India",
    "microfinance guardrails India",
    "microfinance ordinance India",
    "CGFMU microfinance",
    "CreditAccess Grameen",
    "Spandana Sphoorty",
    "Fusion Finance microfinance",
    "Satin Creditcare",
    "Muthoot Microfin",
    "Asirvad Microfinance",
    "Arman Financial",
    "Annapurna Finance microfinance",
    "Svatantra Microfin",
    "Belstar Microfinance",
    "NBFC India RBI regulation",
    "NBFC asset quality NPA stress India",
    "NBFC funding NCD borrowing India",
    "NBFC rating downgrade India",
    "NBFC unsecured loans India",
    "gold loan NBFC India",
]

CLIMATE_QUERIES = [
    "climate risk India",
    "IMD red alert India",
    "IMD orange alert India",
    "flood India district",
    "cyclone India landfall",
    "heatwave India",
    "drought India",
    "monsoon deficit India",
    "excess rainfall India",
    "unseasonal rain crop damage India",
    "crop loss farmers rainfall India",
    "cloudburst landslide India",
    "climate risk microfinance NBFC India",
    "RBI climate risk NBFC",
    "kharif rabi sowing weather India",
]

MONEYCONTROL_QUERIES = [
    "site:moneycontrol.com microfinance India",
    "site:moneycontrol.com MFI India",
    "site:moneycontrol.com NBFC India",
    "site:moneycontrol.com NBFC-MFI",
    "site:moneycontrol.com JLG microfinance",
    "site:moneycontrol.com microfinance NPA stress",
    "site:moneycontrol.com NBFC NPA asset quality",
    "site:moneycontrol.com NBFC RBI regulation",
    "site:moneycontrol.com NBFC funding NCD borrowing",
    "site:moneycontrol.com NBFC rating downgrade upgrade",
    "site:moneycontrol.com unsecured lending NBFC",
    "site:moneycontrol.com gold loan NBFC",
    'site:moneycontrol.com "CreditAccess Grameen"',
    'site:moneycontrol.com "Spandana Sphoorty"',
    'site:moneycontrol.com "Fusion Finance"',
    'site:moneycontrol.com "Satin Creditcare"',
    'site:moneycontrol.com "Muthoot Microfin"',
    'site:moneycontrol.com "Asirvad Microfinance"',
    'site:moneycontrol.com "Arman Financial"',
    'site:moneycontrol.com "Annapurna Finance"',
    'site:moneycontrol.com "Svatantra Microfin"',
    "site:moneycontrol.com Manappuram NBFC",
    'site:moneycontrol.com "Bajaj Finance" NBFC',
    'site:moneycontrol.com "Shriram Finance" NBFC',
    "site:moneycontrol.com Cholamandalam NBFC",
    'site:moneycontrol.com "Muthoot Finance" NBFC',
    'site:moneycontrol.com "Mahindra Finance" NBFC',
    'site:moneycontrol.com "L&T Finance" NBFC',
    'site:moneycontrol.com "IIFL Finance" NBFC',
]

DIRECT_FEEDS = {
    "Economic Times": "https://economictimes.indiatimes.com/industry/banking/finance/rssfeeds/13358259.cms",
    "Business Standard": "https://www.business-standard.com/rss/finance-103.rss",
    "Moneycontrol": "https://www.moneycontrol.com/rss/business.xml",
    "Financial Express": "https://www.financialexpress.com/money/banking-finance/feed/",
    "RBI": "https://www.rbi.org.in/pressreleases_rss.xml",
}

MARKET_SYMBOLS = {
    "NIFTY 50": "^NSEI",
    "CreditAccess Grameen": "CREDITACC.NS",
    "Spandana Sphoorty": "SPANDANA.NS",
    "Fusion Finance": "FUSION.NS",
    "Satin Creditcare": "SATIN.NS",
    "Muthoot Microfin": "MUTHOOTMF.NS",
    "Arman Financial": "ARMANFIN.NS",
    "Manappuram": "MANAPPURAM.NS",
    "Bajaj Finance": "BAJFINANCE.NS",
    "Shriram Finance": "SHRIRAMFIN.NS",
    "Cholamandalam Finance": "CHOLAFIN.NS",
    "Muthoot Finance": "MUTHOOTFIN.NS",
    "M&M Financial": "M&MFIN.NS",
    "L&T Finance": "LTF.NS",
    "IIFL Finance": "IIFL.NS",
}

COMPANY_PATTERNS = {
    "CreditAccess Grameen": r"creditaccess(?: grameen)?",
    "Spandana Sphoorty": r"spandana(?: sphoorty)?",
    "Fusion Finance": r"fusion finance|fusion micro",
    "Satin Creditcare": r"satin creditcare|satin credit",
    "Muthoot Microfin": r"muthoot microfin",
    "Asirvad Microfinance": r"asirvad",
    "Arman Financial": r"arman financial",
    "Annapurna Finance": r"annapurna finance",
    "Svatantra Microfin": r"svatantra",
    "Belstar Microfinance": r"belstar",
    "Manappuram": r"manappuram",
    "Bajaj Finance": r"bajaj finance",
    "Shriram Finance": r"shriram finance",
    "Cholamandalam Finance": r"cholamandalam|chola(?:mandalam)? finance",
    "Muthoot Finance": r"muthoot finance",
    "Mahindra Finance": r"mahindra finance|m&m financial",
    "L&T Finance": r"l&t finance|larsen.{0,8}toubro finance",
    "IIFL Finance": r"iifl finance",
}

STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi",
    "Jammu and Kashmir", "Ladakh", "Puducherry",
]
STATE_ALIASES = {
    "Orissa": "Odisha",
    "NCT Delhi": "Delhi",
    "J&K": "Jammu and Kashmir",
    "Tamilnadu": "Tamil Nadu",
    "Uttaranchal": "Uttarakhand",
}

MF_RE = re.compile(
    r"micro\s?-?financ|\bmfis?\b|nbfc|micro\s?-?lend|\bjlg\b|joint liability|"
    r"\bshgs?\b|self[- ]help group|\bmfin\b|sa-?dhan|creditaccess|spandana|"
    r"fusion finance|satin creditcare|muthoot microfin|asirvad|arman financial|"
    r"annapurna finance|svatantra|belstar|cgfmu|microcredit|gold loan|"
    r"non-?banking financ|bajaj finance|shriram finance|cholamandalam|"
    r"muthoot finance|manappuram|mahindra finance|l&t finance|iifl",
    re.I,
)
CLIMATE_RE = re.compile(
    r"climate|flood|cyclone|heat ?wave|drought|monsoon|rainfall|heavy rain|"
    r"unseasonal|cloudburst|landslide|\bimd\b|red alert|orange alert|weather|"
    r"hailstorm|crop (?:loss|damage|failure)|kharif|rabi|sowing|extreme weather|temperature",
    re.I,
)
BANK_HARD_RE = re.compile(
    r"\bsbi\b|state bank|hdfc bank|icici bank|axis bank|kotak|yes bank|indusind|"
    r"idfc first|bandhan bank|ujjivan|equitas|suryoday|utkarsh|esaf|small finance bank|"
    r"\bsfbs?\b|bank of baroda|punjab national|\bpnb\b|canara bank|union bank|"
    r"bank of india|indian bank|federal bank|rbl bank|idbi|payments bank|"
    r"co-?operative bank|regional rural bank|\brrbs?\b",
    re.I,
)
BANK_SOFT_RE = re.compile(r"\bbanks?\b|\bbanking\b|\bbanker", re.I)
BANK_KEEP_RE = re.compile(r"reserve bank(?: of india)?|world bank", re.I)

CATEGORIES = {
    "Regulatory & Policy": r"\brbi\b|reserve bank|regulat|guideline|circular|ordinance|policy|framework|directive|norms?|penalt|government",
    "Asset Quality & Stress": r"\bnpa|gnpa|delinquen|\bpar\s?\d|default|write-?off|stress|overdue|slippage|credit cost|provision|collection efficiency|asset quality|over-?indebted|\bdpd\b|loss",
    "Fraud & Conduct": r"fraud|coercive|harass|suicide|recovery agent|\bfir\b|arrest|scam|mis-?sell|complaint|protest|raid|cheat",
    "Funding & Ratings": r"fund ?rais|\bncds?\b|\bdebt\b|equity|securiti|borrowing|rating|downgrade|upgrade|icra|crisil|liquidity|capital|sidbi|nabard",
    "Business & Results": r"disburs|\baum\b|loan book|growth|branch|customer|results?|profit|quarter|revenue|margin|share price|stock|merger",
}
CLIMATE_CATEGORIES = {
    "Climate - Flood & Rain": r"flood|heavy rain|rainfall|cloudburst|landslide|inundat|waterlog|unseasonal",
    "Climate - Cyclone & Storm": r"cyclone|storm|hailstorm|landfall|depression",
    "Climate - Heat & Drought": r"heat ?wave|drought|dry spell|temperature|water crisis|deficit",
    "Climate - Agri & Crop": r"crop|kharif|rabi|sowing|farmer|harvest|agri",
    "Climate - Policy & Finance": r"\brbi\b|disclosure|\besg\b|green financ|carbon|emission|policy|framework",
}
HIGH_RE = re.compile(
    r"default|fraud|downgrade|stress|\bnpa|gnpa|delinquen|write-?off|crisis|"
    r"waiver|protest|penalt|raid|arrest|slippage|scam",
    re.I,
)
MED_RE = re.compile(
    r"\brbi\b|regulat|rating|provision|caution|warn|slowdown|declin|fall|drop|"
    r"guardrail|concern|pressure",
    re.I,
)
CLIM_HIGH_RE = re.compile(
    r"red alert|flood|cyclone|cloudburst|landslide|severe|extreme|drought|"
    r"heat ?wave|crop (?:loss|damage|failure)|death|evacuat|disaster",
    re.I,
)
CLIM_MED_RE = re.compile(
    r"orange alert|yellow alert|warning|deficit|unseasonal|heavy rain|dry spell|forecast|risk",
    re.I,
)
REGULATORY_RE = re.compile(
    r"\brbi\b|reserve bank|master direction|circular|guideline|regulat|framework|"
    r"supervisory|penalt|mfin|sa-?dhan|sidbi|nabard|ministry of finance|sebi",
    re.I,
)
NUMBER_RE = re.compile(
    r"(?:₹|rs\.?|inr)\s?[\d,]+(?:\.\d+)?\s?(?:lakh crore|crore|cr\b|lakh|billion|million)?|"
    r"\d+(?:\.\d+)?\s?(?:%|bps|basis points|mm\b|cm\b|°c)",
    re.I,
)
MATERIAL_NUMBER_RULES = [
    ("GNPA / NPA", re.compile(r"(?:gnpa|npa|asset quality).{0,35}(\d+(?:\.\d+)?\s?%)", re.I)),
    ("Credit cost", re.compile(r"credit cost.{0,35}(\d+(?:\.\d+)?\s?(?:%|bps|basis points))", re.I)),
    ("Collection efficiency", re.compile(r"collection efficiency.{0,35}(\d+(?:\.\d+)?\s?%)", re.I)),
    ("Fund raise / borrowing", re.compile(r"(?:fund ?rais|borrowing|ncd|debt).{0,45}((?:₹|rs\.?|inr)\s?[\d,]+(?:\.\d+)?\s?(?:crore|cr|lakh|billion|million)?)", re.I)),
    ("Profit / loss", re.compile(r"(?:profit|loss).{0,35}((?:₹|rs\.?|inr)\s?[\d,]+(?:\.\d+)?\s?(?:crore|cr|lakh|billion|million)?)", re.I)),
]
THEME_PATTERNS = {
    "Delinquency / NPA": r"delinquen|\bnpa|gnpa|overdue|slippage|\bpar\s?\d",
    "Collections": r"collection efficiency|collection|recovery",
    "Fraud / Conduct": r"fraud|coercive|harass|scam|mis-?sell|complaint",
    "Funding / Liquidity": r"fund ?rais|liquidity|borrowing|\bncd|debt|capital",
    "Regulation": r"\brbi\b|regulat|guideline|circular|framework|policy",
    "Flood / Rain": r"flood|heavy rain|rainfall|cloudburst|landslide",
    "Heat / Drought": r"heat ?wave|drought|dry spell|temperature|deficit",
}

SOURCE_TIERS = {
    "RBI": (1, "Very high"),
    "IMD": (1, "Very high"),
    "Economic Times": (2, "High"),
    "Business Standard": (2, "High"),
    "Moneycontrol": (2, "High"),
    "Financial Express": (2, "High"),
    "Reuters": (2, "High"),
}

opener = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=ssl.create_default_context())
)

# -----------------------------------------------------------------------------
# Data helpers
# -----------------------------------------------------------------------------
def fetch_url(url: str, retries: int = 2) -> bytes:
    error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": UA, "Accept": "*/*"},
            )
            with opener.open(request, timeout=TIMEOUT) as response:
                return response.read()
        except Exception as exc:  # network behavior differs by feed
            error = exc
            if attempt < retries:
                time.sleep(2)
    if error:
        raise error
    raise RuntimeError("Unknown network error")


def google_rss(query: str) -> str:
    encoded = urllib.parse.quote(query + " when:2d")
    return f"https://news.google.com/rss/search?q={encoded}&hl=en-IN&gl=IN&ceid=IN:en"


def clean_text(value: str | None) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(no_tags)).strip()


def parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(IST)
    try:
        parsed = parsedate_to_datetime(value)
    except Exception:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return datetime.now(IST)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def child_text(element: ET.Element, tag: str) -> str:
    child = element.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def parse_feed(
    raw: bytes,
    name: str,
    hint: str | None,
    is_google: bool,
    force_source: str | None = None,
) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        start = raw.find(b"<")
        root = ET.fromstring(raw[start:])

    atom = "{http://www.w3.org/2005/Atom}"
    entries = list(root.iter("item")) or list(root.iter(atom + "entry"))
    output: list[dict[str, Any]] = []

    for entry in entries:
        if entry.tag == "item":
            title = clean_text(child_text(entry, "title"))
            link = child_text(entry, "link")
            published = child_text(entry, "pubDate")
            description = clean_text(child_text(entry, "description"))
            source_node = entry.find("source")
            source = (
                (source_node.text or "").strip()
                if source_node is not None and source_node.text
                else name
            )
        else:
            title = clean_text(child_text(entry, atom + "title"))
            link_node = entry.find(atom + "link")
            link = link_node.get("href", "") if link_node is not None else ""
            published = child_text(entry, atom + "updated") or child_text(entry, atom + "published")
            description = clean_text(child_text(entry, atom + "summary"))
            source = name

        if not title or not link:
            continue

        if is_google:
            if source and title.endswith(" - " + source):
                title = title[: -(len(source) + 3)].strip()
            elif " - " in title:
                title, source = title.rsplit(" - ", 1)
            description = ""

        if force_source:
            source = force_source

        output.append(
            {
                "title": title,
                "link": link,
                "source": source,
                "published": parse_datetime(published),
                "summary": description[:500],
                "hint": hint,
            }
        )
    return output


def bank_context(text: str) -> str:
    protected = BANK_KEEP_RE.sub(" ", text)
    has_bank = bool(BANK_HARD_RE.search(protected) or BANK_SOFT_RE.search(protected))
    has_nbfc = bool(MF_RE.search(text))
    if has_bank and has_nbfc:
        return "MIXED_BANK_NBFC"
    if has_bank:
        return "BANK_ONLY"
    return "NBFC_OR_OTHER"


def best_category(patterns: dict[str, str], text: str, default: str) -> str:
    scored = [(len(re.findall(pattern, text, re.I)), category) for category, pattern in patterns.items()]
    count, category = max(scored)
    return category if count else default


def extract_states(text: str) -> list[str]:
    found = [state for state in STATES if re.search(r"\b" + re.escape(state) + r"\b", text, re.I)]
    for alias, canonical in STATE_ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", text, re.I) and canonical not in found:
            found.append(canonical)
    return found


def extract_companies(text: str) -> list[str]:
    return [name for name, pattern in COMPANY_PATTERNS.items() if re.search(pattern, text, re.I)]


def extract_material_numbers(text: str) -> tuple[list[str], list[dict[str, str]]]:
    numbers = list(dict.fromkeys(match.group(0).strip() for match in NUMBER_RE.finditer(text)))[:6]
    labelled: list[dict[str, str]] = []
    for label, pattern in MATERIAL_NUMBER_RULES:
        match = pattern.search(text)
        if match:
            labelled.append({"label": label, "value": match.group(1).strip()})
    return numbers, labelled[:4]


def source_rank(source: str) -> tuple[int, str]:
    for known, result in SOURCE_TIERS.items():
        if known.lower() in source.lower():
            return result
    return 3, "Standard"


def risk_score(article: dict[str, Any], text: str) -> int:
    points = 10
    if HIGH_RE.search(text) or CLIM_HIGH_RE.search(text):
        points += 25
    elif MED_RE.search(text) or CLIM_MED_RE.search(text):
        points += 12
    if REGULATORY_RE.search(text):
        points += 15
    if re.search(r"\bnpa|gnpa|delinquen|default|fraud|downgrade|write-?off", text, re.I):
        points += 15
    if article.get("companies"):
        points += 10
    if article.get("states"):
        points += 8
    if article.get("numbers"):
        points += 5
    tier, _ = source_rank(article.get("source", ""))
    points += {1: 10, 2: 6, 3: 2}.get(tier, 0)
    age_hours = max(0.0, (datetime.now(IST) - article["published"]).total_seconds() / 3600)
    if age_hours <= 6:
        points += 7
    elif age_hours <= 12:
        points += 4
    if article.get("bank_context") == "MIXED_BANK_NBFC":
        points += 2
    return min(100, points)


def score_band(score: int) -> str:
    if score >= 75:
        return "Critical"
    if score >= 50:
        return "High"
    if score >= 25:
        return "Watch"
    return "Information"


def enrich_article(article: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    combined = f"{article['title']} {article['summary']}"
    context = bank_context(combined)
    if context == "BANK_ONLY":
        return None, "bank"

    is_mf = bool(MF_RE.search(combined))
    is_climate = bool(CLIMATE_RE.search(combined))
    if article.get("hint") == STREAM_MF and is_mf:
        stream = STREAM_MF
    elif article.get("hint") == STREAM_CLIMATE and is_climate:
        stream = STREAM_CLIMATE
    elif is_mf:
        stream = STREAM_MF
    elif is_climate:
        stream = STREAM_CLIMATE
    else:
        return None, "irrelevant"

    if stream == STREAM_CLIMATE:
        category = best_category(CLIMATE_CATEGORIES, combined, "Climate - General")
    else:
        category = best_category(CATEGORIES, combined, "General / Sector")

    numbers, labelled_numbers = extract_material_numbers(combined)
    tier, confidence = source_rank(article["source"])
    article.update(
        stream=stream,
        category=category,
        bank_context=context,
        regulatory=bool(REGULATORY_RE.search(combined)),
        companies=extract_companies(combined),
        states=extract_states(combined),
        numbers=numbers,
        labelled_numbers=labelled_numbers,
        source_tier=tier,
        source_confidence=confidence,
    )
    article["risk_score"] = risk_score(article, combined)
    article["severity"] = score_band(article["risk_score"])
    article.pop("hint", None)
    return article, None


def normalized_title(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", title.lower())
    stop = {"the", "a", "an", "to", "of", "in", "on", "for", "and", "with", "as", "at", "from"}
    return " ".join(word for word in words if word not in stop)[:180]


def stable_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8", "ignore")).hexdigest()[:18]


def cluster_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    for article in sorted(articles, key=lambda row: row["published"], reverse=True):
        key = normalized_title(article["title"])
        match: dict[str, Any] | None = None
        for cluster in clusters:
            same_category = article["category"] == cluster["category"]
            similarity = SequenceMatcher(None, key, cluster["norm_title"]).ratio()
            shared_company = bool(set(article["companies"]) & set(cluster["companies"]))
            shared_state = bool(set(article["states"]) & set(cluster["states"]))
            if similarity >= 0.76 or (same_category and similarity >= 0.60 and (shared_company or shared_state)):
                match = cluster
                break

        if match is None:
            article["event_id"] = stable_id(key, article["published"].strftime("%Y-%m-%d"))
            article["source_count"] = 1
            article["event_sources"] = [article["source"]]
            article["norm_title"] = key
            clusters.append(article)
        else:
            sources = list(dict.fromkeys(match["event_sources"] + [article["source"]]))
            match["event_sources"] = sources
            match["source_count"] = len(sources)
            match["risk_score"] = min(100, max(match["risk_score"], article["risk_score"]) + min(10, (len(sources) - 1) * 3))
            match["severity"] = score_band(match["risk_score"])
            match["companies"] = list(dict.fromkeys(match["companies"] + article["companies"]))
            match["states"] = list(dict.fromkeys(match["states"] + article["states"]))
            match["numbers"] = list(dict.fromkeys(match["numbers"] + article["numbers"]))[:6]
            if article["published"] > match["published"]:
                for field in ("title", "link", "source", "published", "summary", "source_tier", "source_confidence"):
                    match[field] = article[field]
    for cluster in clusters:
        cluster.pop("norm_title", None)
    return sorted(clusters, key=lambda row: (row["risk_score"], row["published"]), reverse=True)


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_news() -> tuple[list[dict[str, Any]], dict[str, int]]:
    jobs = [(f"Google: {q}", google_rss(q), STREAM_MF, True, None) for q in MF_QUERIES]
    jobs += [(f"Moneycontrol: {q}", google_rss(q), STREAM_MF, True, "Moneycontrol") for q in MONEYCONTROL_QUERIES]
    jobs += [(f"Google: {q}", google_rss(q), STREAM_CLIMATE, True, None) for q in CLIMATE_QUERIES]
    jobs += [(name, url, None, False, None) for name, url in DIRECT_FEEDS.items()]

    raw_articles: list[dict[str, Any]] = []
    ok = failed = 0

    def worker(job: tuple[str, str, str | None, bool, str | None]) -> list[dict[str, Any]]:
        return parse_feed(fetch_url(job[1]), job[0], job[2], job[3], job[4])

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(worker, job) for job in jobs]
        for future in as_completed(futures):
            try:
                raw_articles.extend(future.result())
                ok += 1
            except Exception:
                failed += 1

    now = datetime.now(IST)
    cutoff = now - timedelta(hours=LOOKBACK_HOURS)
    exact_seen: set[str] = set()
    relevant: list[dict[str, Any]] = []
    bank_dropped = irrelevant_dropped = 0

    for article in raw_articles:
        if not cutoff <= article["published"] <= now + timedelta(hours=2):
            continue
        enriched, reason = enrich_article(article)
        if reason == "bank":
            bank_dropped += 1
        elif reason == "irrelevant":
            irrelevant_dropped += 1
        if not enriched:
            continue
        exact_key = normalized_title(enriched["title"])
        if exact_key in exact_seen:
            continue
        exact_seen.add(exact_key)
        relevant.append(enriched)

    events = cluster_articles(relevant)
    status = {
        "ok": ok,
        "failed": failed,
        "raw": len(raw_articles),
        "events": len(events),
        "bank_dropped": bank_dropped,
        "irrelevant_dropped": irrelevant_dropped,
        "moneycontrol": sum(1 for item in events if item["source"].lower() == "moneycontrol"),
    }
    return events, status

# -----------------------------------------------------------------------------
# Archive and market data
# -----------------------------------------------------------------------------
def init_database() -> None:
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS news_archive (
                event_id TEXT PRIMARY KEY,
                collected_at TEXT NOT NULL,
                published TEXT NOT NULL,
                source TEXT,
                title TEXT,
                link TEXT,
                stream TEXT,
                category TEXT,
                severity TEXT,
                risk_score INTEGER,
                companies TEXT,
                states TEXT,
                numbers TEXT,
                source_count INTEGER
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_archive_published ON news_archive(published)")
        connection.commit()


def save_archive(articles: Iterable[dict[str, Any]]) -> None:
    init_database()
    collected_at = datetime.now(IST).isoformat()
    rows = []
    for article in articles:
        rows.append(
            (
                article["event_id"], collected_at, article["published"].isoformat(), article["source"],
                article["title"], article["link"], article["stream"], article["category"],
                article["severity"], int(article["risk_score"]), json.dumps(article["companies"]),
                json.dumps(article["states"]), json.dumps(article["numbers"]), int(article["source_count"]),
            )
        )
    with sqlite3.connect(DB_PATH) as connection:
        connection.executemany(
            """
            INSERT INTO news_archive (
                event_id, collected_at, published, source, title, link, stream, category,
                severity, risk_score, companies, states, numbers, source_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                collected_at=excluded.collected_at,
                severity=excluded.severity,
                risk_score=MAX(news_archive.risk_score, excluded.risk_score),
                companies=excluded.companies,
                states=excluded.states,
                numbers=excluded.numbers,
                source_count=MAX(news_archive.source_count, excluded.source_count)
            """,
            rows,
        )
        cutoff = (datetime.now(IST) - timedelta(days=ARCHIVE_DAYS)).isoformat()
        connection.execute("DELETE FROM news_archive WHERE published < ?", (cutoff,))
        connection.commit()


def load_archive(days: int = ARCHIVE_DAYS) -> pd.DataFrame:
    init_database()
    cutoff = (datetime.now(IST) - timedelta(days=days)).isoformat()
    with sqlite3.connect(DB_PATH) as connection:
        frame = pd.read_sql_query(
            "SELECT * FROM news_archive WHERE published >= ? ORDER BY published DESC",
            connection,
            params=(cutoff,),
        )
    if not frame.empty:
        frame["published"] = pd.to_datetime(frame["published"], utc=True).dt.tz_convert(IST)
        for column in ("companies", "states", "numbers"):
            frame[column] = frame[column].apply(lambda value: json.loads(value or "[]"))
    return frame


def fetch_quote(item: tuple[str, str]) -> dict[str, Any] | None:
    name, symbol = item
    try:
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{urllib.parse.quote(symbol)}?range=1mo&interval=1d"
        )
        payload = json.loads(fetch_url(url, 1))["chart"]["result"][0]
        closes = [value for value in payload["indicators"]["quote"][0]["close"] if value is not None]
        if not closes:
            return None
        price = payload["meta"].get("regularMarketPrice") or closes[-1]
        previous = closes[-2] if len(closes) > 1 else payload["meta"].get("chartPreviousClose")
        five_day_base = closes[-6] if len(closes) >= 6 else closes[0]
        daily_pct = ((price - previous) / previous * 100) if previous else 0.0
        five_day_pct = ((price - five_day_base) / five_day_base * 100) if five_day_base else 0.0
        return {
            "Company / Index": name,
            "LTP": price,
            "Change": price - previous if previous else 0.0,
            "Change %": daily_pct,
            "5D %": five_day_pct,
            "Alert": "High" if abs(daily_pct) >= 5 else "Watch" if abs(daily_pct) >= 3 else "Normal",
        }
    except Exception:
        return None


@st.cache_data(ttl=900, show_spinner=False)
def market_data() -> list[dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=6) as executor:
        return [row for row in executor.map(fetch_quote, MARKET_SYMBOLS.items()) if row]

# -----------------------------------------------------------------------------
# Portfolio support
# -----------------------------------------------------------------------------
def normalise_portfolio(uploaded_file: Any) -> pd.DataFrame:
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(uploaded_file)
    elif suffix in {".xlsx", ".xls"}:
        frame = pd.read_excel(uploaded_file)
    else:
        raise ValueError("Upload a CSV, XLSX or XLS file.")

    aliases = {str(column).strip().lower(): column for column in frame.columns}
    rename: dict[Any, str] = {}
    for target in ("State", "District", "Branch", "POS", "PAR30", "Customers"):
        key = target.lower()
        if key in aliases:
            rename[aliases[key]] = target
    frame = frame.rename(columns=rename)
    if "State" not in frame.columns:
        raise ValueError("Portfolio file must contain a State column.")
    for column in ("District", "Branch"):
        if column not in frame.columns:
            frame[column] = ""
    for column in ("POS", "PAR30", "Customers"):
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    frame["State"] = frame["State"].astype(str).str.strip()
    frame["District"] = frame["District"].astype(str).str.strip()
    frame["Branch"] = frame["Branch"].astype(str).str.strip()
    return frame


def district_mentions(text: str, portfolio: pd.DataFrame | None) -> list[str]:
    if portfolio is None or portfolio.empty:
        return []
    districts = [value for value in portfolio["District"].dropna().unique() if str(value).strip()]
    return [district for district in districts if re.search(r"\b" + re.escape(str(district)) + r"\b", text, re.I)]


def portfolio_impact(row: pd.Series, portfolio: pd.DataFrame | None) -> dict[str, Any]:
    if portfolio is None or portfolio.empty:
        return {"level": "Not assessed", "pos": 0.0, "branches": 0, "customers": 0, "par30": 0.0, "districts": []}
    text = f"{row.title} {row.summary}"
    states = row.states if isinstance(row.states, list) else []
    districts = district_mentions(text, portfolio)
    affected = portfolio[portfolio["State"].isin(states)]
    if districts:
        affected = affected[affected["District"].isin(districts)]
    if affected.empty:
        return {"level": "Low", "pos": 0.0, "branches": 0, "customers": 0, "par30": 0.0, "districts": districts}
    total_pos = float(affected["POS"].sum())
    overall_pos = float(portfolio["POS"].sum())
    exposure_share = total_pos / overall_pos if overall_pos else 0.0
    par30 = float((affected["PAR30"] * affected["POS"]).sum() / total_pos) if total_pos else float(affected["PAR30"].mean())
    level = "High" if exposure_share >= 0.15 or par30 >= 5 else "Medium" if exposure_share >= 0.05 or par30 >= 3 else "Low"
    return {
        "level": level,
        "pos": total_pos,
        "branches": int(affected["Branch"].replace("", pd.NA).nunique()),
        "customers": int(affected["Customers"].sum()),
        "par30": par30,
        "districts": districts,
    }

# -----------------------------------------------------------------------------
# Export helpers
# -----------------------------------------------------------------------------
def export_frame(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "published", "stream", "severity", "risk_score", "category", "companies", "states",
        "source", "source_count", "title", "numbers", "link",
    ]
    output = frame.copy()
    output["published"] = output["published"].dt.strftime("%Y-%m-%d %H:%M IST")
    for column in ("companies", "states", "numbers"):
        output[column] = output[column].apply(lambda values: " | ".join(values) if isinstance(values, list) else str(values))
    return output[columns]


def to_excel_bytes(frame: pd.DataFrame) -> bytes | None:
    try:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name="Risk News", index=False)
        return buffer.getvalue()
    except Exception:
        return None


def to_pdf_bytes(frame: pd.DataFrame, now: datetime) -> bytes | None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        buffer = io.BytesIO()
        document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=13 * mm, leftMargin=13 * mm, topMargin=13 * mm, bottomMargin=13 * mm)
        styles = getSampleStyleSheet()
        story: list[Any] = [Paragraph("Risk Daily News", styles["Title"]), Paragraph(now.strftime("%d %B %Y · %I:%M %p IST"), styles["Normal"]), Spacer(1, 8)]
        for row in frame.head(30).itertuples():
            story.append(Paragraph(f"<b>{html.escape(row.severity)} · {int(row.risk_score)}/100</b> — {html.escape(row.title)}", styles["BodyText"]))
            story.append(Paragraph(f"{html.escape(row.source)} · {row.published:%d-%b %I:%M %p} · {html.escape(row.category)}", styles["Italic"]))
            story.append(Spacer(1, 6))
        summary = Table([["Events", len(frame)], ["Critical", int((frame.severity == "Critical").sum())], ["High", int((frame.severity == "High").sum())]], colWidths=[45 * mm, 30 * mm])
        summary.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f3f6fb")), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")), ("PADDING", (0, 0), (-1, -1), 5)]))
        story.insert(3, summary)
        story.insert(4, Spacer(1, 10))
        document.build(story)
        return buffer.getvalue()
    except Exception:
        return None

# -----------------------------------------------------------------------------
# Visual design and rendering helpers
# -----------------------------------------------------------------------------
CSS = r"""
<style>
:root{
  --navy:#132238;--navy2:#1d3557;--blue:#2563eb;--cyan:#0891b2;--green:#0f9d72;
  --amber:#d97706;--orange:#ea580c;--red:#dc2626;--purple:#7c3aed;--ink:#172033;
  --muted:#64748b;--line:#dbe4ee;--soft:#f5f8fc;--card:#ffffff;
}
html,body,[class*="css"]{font-family:Inter,"Segoe UI",Arial,sans-serif;color:var(--ink)}
.stApp{background:linear-gradient(180deg,#f7f9fc 0,#ffffff 26rem)}
.block-container{padding-top:1rem;padding-bottom:2rem;max-width:1550px}
[data-testid="stSidebar"]{background:linear-gradient(180deg,#edf2f8,#f8fafc);border-right:1px solid var(--line)}
[data-testid="stSidebar"] .block-container{padding-top:1.25rem}
[data-testid="stSidebar"] h2{font-size:1.08rem;color:var(--navy);letter-spacing:.01em}
.hero{position:relative;overflow:hidden;background:linear-gradient(118deg,#101f35 0%,#1f3b63 58%,#0e7490 100%);color:#fff;border-radius:18px;padding:24px 28px;box-shadow:0 12px 30px rgba(15,34,56,.16);margin-bottom:12px}
.hero:after{content:"";position:absolute;width:260px;height:260px;border-radius:50%;right:-90px;top:-125px;background:rgba(255,255,255,.09)}
.hero-row{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;position:relative;z-index:2}
.hero-title{font-size:1.8rem;font-weight:800;letter-spacing:-.03em;margin:0 0 5px}.hero-sub{font-size:.92rem;color:#dce9f7}.hero-time{font-size:.84rem;color:#c9d8ea;margin-top:9px;font-weight:600}.live-pill{white-space:nowrap;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.22);border-radius:999px;padding:7px 11px;font-size:.76rem;font-weight:700}
.metric-card{height:100%;background:#fff;border:1px solid var(--line);border-radius:14px;padding:13px 14px;box-shadow:0 5px 16px rgba(15,34,56,.05);position:relative;overflow:hidden}.metric-card:before{content:"";position:absolute;left:0;top:0;width:100%;height:3px;background:var(--accent,#2563eb)}.metric-label{font-size:.72rem;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.045em}.metric-value{font-size:1.65rem;line-height:1.1;font-weight:800;color:var(--ink);margin-top:5px}.metric-note{font-size:.69rem;color:#8492a6;margin-top:4px}
.section-head{display:flex;align-items:center;justify-content:space-between;margin:18px 0 10px}.section-title{font-size:1.23rem;font-weight:800;color:var(--navy);margin:0}.section-kicker{font-size:.72rem;font-weight:800;text-transform:uppercase;letter-spacing:.09em;color:var(--blue);margin-bottom:3px}.section-note{font-size:.78rem;color:var(--muted)}
.summary-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:6px 0 12px}.summary-card{border:1px solid var(--line);border-radius:14px;background:#fff;padding:14px;box-shadow:0 4px 14px rgba(15,34,56,.045)}.summary-card .num{font-size:1.55rem;font-weight:800}.summary-card .label{font-size:.78rem;color:var(--muted);font-weight:650}.summary-card.red{border-top:3px solid var(--red)}.summary-card.orange{border-top:3px solid var(--orange)}.summary-card.blue{border-top:3px solid var(--blue)}
.news-card{background:var(--card);border:1px solid var(--line);border-left:5px solid #94a3b8;border-radius:14px;padding:14px 16px;margin:0 0 12px;box-shadow:0 5px 18px rgba(15,34,56,.055);transition:transform .15s ease,box-shadow .15s ease}.news-card:hover{transform:translateY(-1px);box-shadow:0 8px 22px rgba(15,34,56,.09)}.news-card.Critical{border-left-color:var(--red)}.news-card.High{border-left-color:var(--orange)}.news-card.Watch{border-left-color:var(--amber)}.news-card.Information{border-left-color:var(--green)}
.card-top{display:flex;gap:7px;flex-wrap:wrap;align-items:center;margin-bottom:9px}.pill{display:inline-flex;align-items:center;border-radius:999px;padding:3px 8px;font-size:.66rem;line-height:1.25;font-weight:800;background:#edf2f7;color:#42526a;border:1px solid #e1e8f0}.pill.score-Critical{background:#fee2e2;color:#991b1b;border-color:#fecaca}.pill.score-High{background:#ffedd5;color:#9a3412;border-color:#fed7aa}.pill.score-Watch{background:#fef3c7;color:#92400e;border-color:#fde68a}.pill.score-Information{background:#dcfce7;color:#166534;border-color:#bbf7d0}.pill.reg{background:#ede9fe;color:#5b21b6;border-color:#ddd6fe}.pill.climate{background:#cffafe;color:#155e75;border-color:#a5f3fc}.pill.source{background:#e0f2fe;color:#075985;border-color:#bae6fd}.pill.multi{background:#f3e8ff;color:#6b21a8;border-color:#e9d5ff}.pill.impact-High{background:#fee2e2;color:#991b1b}.pill.impact-Medium{background:#ffedd5;color:#9a3412}.pill.impact-Low{background:#dcfce7;color:#166534}
.news-title{display:block;color:#10294c!important;text-decoration:none!important;font-size:.99rem;line-height:1.42;font-weight:800;margin-bottom:8px}.news-title:hover{color:#1d4ed8!important}.card-meta{color:var(--muted);font-size:.74rem;line-height:1.45;margin-bottom:8px}.card-meta strong{color:#334155}.card-summary{font-size:.79rem;line-height:1.5;color:#526074;margin:6px 0 9px}.tag-row{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.tag{display:inline-block;border-radius:7px;padding:3px 7px;font-size:.66rem;font-weight:700;background:#f1f5f9;color:#475569}.tag.number{background:#e8eef9;color:#1e3a8a}.tag.state{background:#ecfdf5;color:#065f46}.tag.company{background:#fff7ed;color:#9a3412}.tag.material{background:#f5f3ff;color:#5b21b6}
.insight{background:linear-gradient(135deg,#eef5ff,#f8fbff);border:1px solid #cfe0f5;border-radius:14px;padding:14px 16px;margin:8px 0}.insight strong{color:#12355b}.insight p{font-size:.82rem;color:#526074;margin:5px 0 0}.alert-box{border:1px solid #fed7aa;background:#fff7ed;border-radius:14px;padding:13px 15px;color:#9a3412;font-size:.82rem;margin:8px 0}.ok-box{border:1px solid #bbf7d0;background:#f0fdf4;border-radius:14px;padding:13px 15px;color:#166534;font-size:.82rem;margin:8px 0}
.company-card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px 15px;margin-bottom:10px;box-shadow:0 4px 14px rgba(15,34,56,.045)}.company-name{font-weight:800;color:var(--navy);font-size:.98rem}.company-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:10px}.mini-stat{background:#f6f8fb;border-radius:9px;padding:7px;text-align:center}.mini-stat b{display:block;font-size:.96rem;color:#1e3a5f}.mini-stat span{font-size:.62rem;color:var(--muted)}
.footer{margin-top:26px;background:linear-gradient(120deg,#111f34,#1b365d);border-radius:15px;padding:18px 20px;color:#dce7f4;text-align:center}.footer b{color:#fff}.footer .small{font-size:.72rem;color:#aebed2;margin-top:6px;line-height:1.55}
.stTabs [data-baseweb="tab-list"]{gap:4px;background:#fff;border:1px solid var(--line);border-radius:13px;padding:5px;box-shadow:0 4px 14px rgba(15,34,56,.04);overflow-x:auto}.stTabs [data-baseweb="tab"]{height:35px;border-radius:9px;padding:0 11px;font-size:.76rem;font-weight:700;color:#526074}.stTabs [aria-selected="true"]{background:#eaf1fb!important;color:#163a67!important}.stTabs [data-baseweb="tab-border"]{display:none}
[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:12px;overflow:hidden}.stDownloadButton button,.stButton button{border-radius:10px;font-weight:700;border:1px solid #cbd8e6;min-height:38px}.stButton button:hover,.stDownloadButton button:hover{border-color:#2563eb;color:#1d4ed8}.stSelectbox label,.stMultiSelect label,.stTextInput label,.stSlider label,.stFileUploader label{font-size:.76rem!important;font-weight:700!important;color:#42526a!important}
hr{border-color:#e4ebf3!important}@media(max-width:950px){.summary-grid{grid-template-columns:1fr}.hero-row{display:block}.live-pill{display:inline-block;margin-top:12px}.company-stats{grid-template-columns:repeat(2,1fr)}}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


def render_html(markup: str) -> None:
    """Render HTML without Markdown interpreting indented lines as code blocks.

    The entire fragment is compacted to one line. This specifically prevents strings such as
    '<b>Business Standard</b> · 23-Nov 06:51 PM' from appearing as grey code blocks.
    """
    compact = re.sub(r">\s+<", "><", markup.strip())
    compact = compact.replace("\n", " ")
    st.markdown(compact, unsafe_allow_html=True)


def metric_card(label: str, value: Any, note: str = "", color: str = "#2563eb") -> str:
    return (
        f'<div class="metric-card" style="--accent:{color}">'
        f'<div class="metric-label">{html.escape(str(label))}</div>'
        f'<div class="metric-value">{html.escape(str(value))}</div>'
        f'<div class="metric-note">{html.escape(str(note))}</div></div>'
    )


def section_header(kicker: str, title: str, note: str = "") -> None:
    render_html(
        f'<div class="section-head"><div><div class="section-kicker">{html.escape(kicker)}</div>'
        f'<div class="section-title">{html.escape(title)}</div></div>'
        f'<div class="section-note">{html.escape(note)}</div></div>'
    )


def safe_url(url: str) -> str:
    return html.escape(url, quote=True) if re.match(r"^https?://", url or "", re.I) else "#"


def article_card(row: Any, portfolio: pd.DataFrame | None = None) -> str:
    impact = portfolio_impact(pd.Series(row._asdict()), portfolio) if portfolio is not None else None
    stream_class = "climate" if row.stream == STREAM_CLIMATE else ""
    badges = [
        f'<span class="pill score-{html.escape(row.severity)}">{int(row.risk_score)}/100 · {html.escape(row.severity)}</span>',
        f'<span class="pill {stream_class}">{html.escape(row.category)}</span>',
        f'<span class="pill source">{html.escape(row.source_confidence)} confidence</span>',
    ]
    if row.regulatory:
        badges.append('<span class="pill reg">Regulatory</span>')
    if int(row.source_count) > 1:
        badges.append(f'<span class="pill multi">{int(row.source_count)} sources</span>')
    if impact:
        badges.append(f'<span class="pill impact-{impact["level"]}">Portfolio: {html.escape(impact["level"])}</span>')

    tags: list[str] = []
    tags += [f'<span class="tag company">{html.escape(value)}</span>' for value in row.companies]
    tags += [f'<span class="tag state">{html.escape(value)}</span>' for value in row.states]
    tags += [f'<span class="tag number">{html.escape(value)}</span>' for value in row.numbers]
    for item in row.labelled_numbers:
        tags.append(f'<span class="tag material">{html.escape(item["label"])}: {html.escape(item["value"])}</span>')

    summary = f'<div class="card-summary">{html.escape(row.summary)}</div>' if row.summary else ""
    impact_text = ""
    if impact and impact["level"] != "Not assessed" and impact["pos"] > 0:
        impact_text = (
            f'<div class="card-summary"><strong>Portfolio exposure:</strong> ₹{impact["pos"]:,.2f} · '
            f'{impact["branches"]} branches · {impact["customers"]:,} customers · PAR30 {impact["par30"]:.2f}%</div>'
        )

    return (
        f'<article class="news-card {html.escape(row.severity)}">'
        f'<div class="card-top">{"".join(badges)}</div>'
        f'<a class="news-title" href="{safe_url(row.link)}" target="_blank" rel="noopener noreferrer">{html.escape(row.title)}</a>'
        f'<div class="card-meta"><strong>{html.escape(row.source)}</strong> &nbsp;·&nbsp; {row.published:%d-%b %I:%M %p} IST'
        f' &nbsp;·&nbsp; {html.escape(row.stream)}</div>'
        f'{summary}{impact_text}<div class="tag-row">{"".join(tags)}</div></article>'
    )


def render_cards(frame: pd.DataFrame, portfolio: pd.DataFrame | None = None, columns: int = 2, limit: int | None = None) -> None:
    if frame.empty:
        st.info("No stories match the current filters.")
        return
    display = frame.head(limit) if limit else frame
    layout = st.columns(columns)
    for index, row in enumerate(display.itertuples(index=False)):
        with layout[index % columns]:
            render_html(article_card(row, portfolio))

# -----------------------------------------------------------------------------
# Load live data
# -----------------------------------------------------------------------------
now = datetime.now(IST)
render_html(
    f'<div class="hero"><div class="hero-row"><div><div class="hero-title">📰 Risk Daily News</div>'
    f'<div class="hero-sub">Microfinance · JLG/SHG · NBFC · Regulatory · Climate · Portfolio Early Warning</div>'
    f'<div class="hero-time">{now:%A, %d %B %Y · %I:%M %p IST}</div></div>'
    f'<div class="live-pill">● LIVE · refreshes every {REFRESH_SECONDS // 60} min</div></div></div>'
)

with st.spinner("Loading and classifying the latest risk news..."):
    news, status = fetch_news()
    market = market_data()

if not news:
    st.error("No relevant stories were received. Check VM internet/proxy access and try Refresh News.")
    st.stop()

save_archive(news)
df = pd.DataFrame(news)
df["published"] = pd.to_datetime(df["published"], utc=True).dt.tz_convert(IST)

# -----------------------------------------------------------------------------
# Sidebar controls
# -----------------------------------------------------------------------------
portfolio: pd.DataFrame | None = None
with st.sidebar:
    st.header("Control Panel")
    if st.button("↻ Refresh News", use_container_width=True, type="primary"):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"Automatic source cache: {REFRESH_SECONDS // 60} minutes")

    uploaded = st.file_uploader("Optional portfolio file", type=["csv", "xlsx", "xls"], help="State is mandatory; District, Branch, POS, PAR30 and Customers are optional.")
    if uploaded is not None:
        try:
            portfolio = normalise_portfolio(uploaded)
            st.success(f"Portfolio loaded: {len(portfolio):,} rows")
        except Exception as exc:
            st.error(str(exc))

    st.caption(
        f"Sources: {status['ok']}/{status['ok'] + status['failed']} · Raw: {status['raw']:,} · "
        f"Bank-only removed: {status['bank_dropped']:,} · Moneycontrol: {status['moneycontrol']:,}"
    )
    st.divider()
    search = st.text_input("Search", placeholder="company, district, event, risk...")
    hours = st.select_slider("Time window", options=[6, 12, 24, 48], value=48, format_func=lambda value: f"Last {value}h")
    streams = st.multiselect("Stream", [STREAM_MF, STREAM_CLIMATE], default=[STREAM_MF, STREAM_CLIMATE])
    severity = st.multiselect("Risk band", SEVERITY_ORDER, default=SEVERITY_ORDER)
    available_sources = sorted(df["source"].dropna().unique())
    chosen_sources = st.multiselect("Source", available_sources, default=[])
    available_categories = sorted(df["category"].dropna().unique())
    chosen_categories = st.multiselect("Category", available_categories, default=available_categories)
    all_states = sorted({state for values in df["states"] for state in values})
    chosen_states = st.multiselect("State", all_states, default=[])
    min_score = st.slider("Minimum risk score", 0, 100, 0, 5)

# -----------------------------------------------------------------------------
# Filtered view and headline indicators
# -----------------------------------------------------------------------------
filtered = df[
    (df["published"] >= pd.Timestamp(now - timedelta(hours=hours)))
    & df["stream"].isin(streams)
    & df["severity"].isin(severity)
    & df["category"].isin(chosen_categories)
    & (df["risk_score"] >= min_score)
].copy()
if chosen_sources:
    filtered = filtered[filtered["source"].isin(chosen_sources)]
if chosen_states:
    filtered = filtered[filtered["states"].apply(lambda values: any(state in values for state in chosen_states))]
if search:
    needle = search.lower()
    searchable = (
        filtered["title"].fillna("") + " " + filtered["source"].fillna("") + " "
        + filtered["summary"].fillna("") + " "
        + filtered["states"].apply(" ".join) + " " + filtered["companies"].apply(" ".join)
    ).str.lower()
    filtered = filtered[searchable.str.contains(needle, regex=False)]
filtered = filtered.sort_values(["risk_score", "published"], ascending=[False, False])

mf_view = filtered[filtered["stream"] == STREAM_MF]
climate_view = filtered[filtered["stream"] == STREAM_CLIMATE]
regulatory_view = filtered[filtered["regulatory"]]
high_view = filtered[filtered["severity"].isin(["Critical", "High"])]
company_names = sorted({company for values in filtered["companies"] for company in values})
state_names = sorted({state for values in filtered["states"] for state in values})

metrics = [
    ("Distinct Events", len(filtered), "after clustering", "#2563eb"),
    ("Critical", int((filtered["severity"] == "Critical").sum()), "score ≥ 75", "#dc2626"),
    ("High", int((filtered["severity"] == "High").sum()), "score 50–74", "#ea580c"),
    ("Regulatory", len(regulatory_view), "RBI / policy", "#7c3aed"),
    ("Climate", len(climate_view), "weather / agriculture", "#0891b2"),
    ("Companies", len(company_names), "tracked names", "#0f9d72"),
    ("States", len(state_names), "mentioned", "#d97706"),
]
metric_columns = st.columns(len(metrics))
for column, (label, value, note, color) in zip(metric_columns, metrics):
    with column:
        render_html(metric_card(label, value, note, color))

# -----------------------------------------------------------------------------
# Main pages
# -----------------------------------------------------------------------------
tabs = st.tabs([
    "Executive Brief", "MFI / NBFC", "Company Watch", "Regulatory", "Climate",
    "Geography", "High Risk", "Risk Trends", "Market", "Archive", "Export",
])

with tabs[0]:
    section_header("Morning intelligence", "Executive Risk Brief", f"Based on {len(filtered)} filtered, clustered events")
    critical_count = int((filtered["severity"] == "Critical").sum())
    high_count = int((filtered["severity"] == "High").sum())
    multi_source = int((filtered["source_count"] > 1).sum())
    render_html(
        f'<div class="summary-grid"><div class="summary-card red"><div class="num">{critical_count}</div><div class="label">Critical events requiring attention</div></div>'
        f'<div class="summary-card orange"><div class="num">{high_count}</div><div class="label">High-risk events to monitor</div></div>'
        f'<div class="summary-card blue"><div class="num">{multi_source}</div><div class="label">Events confirmed by multiple sources</div></div></div>'
    )

    if critical_count:
        top = filtered.iloc[0]
        render_html(
            f'<div class="alert-box"><strong>Priority signal:</strong> {html.escape(top.title)} '
            f'({int(top.risk_score)}/100 · {html.escape(top.source)}).</div>'
        )
    elif high_count:
        render_html('<div class="alert-box"><strong>Watch status:</strong> No critical item, but high-risk developments are present in the selected window.</div>')
    else:
        render_html('<div class="ok-box"><strong>No critical or high-risk event detected</strong> in the current filtered view.</div>')

    left, right = st.columns([1.65, 1])
    with left:
        section_header("Priority queue", "Top items requiring Risk attention", "Highest score first")
        render_cards(filtered, portfolio, columns=1, limit=6)
    with right:
        section_header("Concentration", "Signals by category", "Current filtered view")
        category_counts = filtered["category"].value_counts().head(8)
        if not category_counts.empty:
            st.bar_chart(category_counts, color="#2563eb")
        section_header("Coverage", "Most-mentioned states", "Current filtered view")
        state_series = pd.Series([state for values in filtered["states"] for state in values], dtype="object").value_counts().head(8)
        if not state_series.empty:
            st.bar_chart(state_series, color="#0f9d72")
        else:
            st.info("No state mentions in the selected view.")

with tabs[1]:
    section_header("Sector intelligence", "Microfinance, JLG, SHG and NBFC", f"{len(mf_view)} events")
    render_cards(mf_view, portfolio, columns=2)

with tabs[2]:
    section_header("Tracked entities", "Company Watch", f"{len(company_names)} companies mentioned")
    if not company_names:
        st.info("No tracked company is mentioned in the current filtered view.")
    else:
        company_columns = st.columns(3)
        for index, company in enumerate(company_names):
            company_frame = filtered[filtered["companies"].apply(lambda values, name=company: name in values)]
            top_row = company_frame.sort_values(["risk_score", "published"], ascending=[False, False]).iloc[0]
            high_events = int(company_frame["severity"].isin(["Critical", "High"]).sum())
            regulatory_events = int(company_frame["regulatory"].sum())
            asset_events = int((company_frame["category"] == "Asset Quality & Stress").sum())
            with company_columns[index % 3]:
                render_html(
                    f'<div class="company-card"><div class="company-name">{html.escape(company)}</div>'
                    f'<div class="company-stats"><div class="mini-stat"><b>{len(company_frame)}</b><span>Events</span></div>'
                    f'<div class="mini-stat"><b>{high_events}</b><span>High+</span></div>'
                    f'<div class="mini-stat"><b>{regulatory_events}</b><span>Regulatory</span></div>'
                    f'<div class="mini-stat"><b>{asset_events}</b><span>Asset quality</span></div></div>'
                    f'<div class="card-summary"><strong>Top concern:</strong> {html.escape(top_row.title)}</div></div>'
                )
        selected_company = st.selectbox("Open company news", company_names)
        selected_company_frame = filtered[filtered["companies"].apply(lambda values: selected_company in values)]
        render_cards(selected_company_frame, portfolio, columns=2)

with tabs[3]:
    section_header("Policy radar", "Regulatory Watch", f"{len(regulatory_view)} items")
    official = regulatory_view[regulatory_view["source_tier"] == 1]
    if not official.empty:
        render_html(f'<div class="insight"><strong>{len(official)} official / Tier-1 regulatory item(s)</strong><p>Official sources are placed first, followed by media coverage.</p></div>')
    render_cards(regulatory_view.sort_values(["source_tier", "risk_score", "published"], ascending=[True, False, False]), portfolio, columns=2)

with tabs[4]:
    section_header("Physical-risk monitoring", "Climate Risk", f"{len(climate_view)} events")
    if not climate_view.empty:
        event_types = climate_view["category"].value_counts()
        chart_col, cards_col = st.columns([0.8, 1.5])
        with chart_col:
            st.bar_chart(event_types, color="#0891b2")
        with cards_col:
            render_cards(climate_view, portfolio, columns=1)
    else:
        st.info("No climate events match the current filters.")

with tabs[5]:
    section_header("Location intelligence", "Geography Early Warning", f"{len(state_names)} states mentioned")
    geo_rows: list[dict[str, Any]] = []
    for state in state_names:
        state_frame = filtered[filtered["states"].apply(lambda values, target=state: target in values)]
        row_data: dict[str, Any] = {
            "State": state,
            "Events": len(state_frame),
            "Critical / High": int(state_frame["severity"].isin(["Critical", "High"]).sum()),
            "Max score": int(state_frame["risk_score"].max()),
            "Latest": state_frame["published"].max(),
        }
        if portfolio is not None:
            exposure = portfolio[portfolio["State"].str.casefold() == state.casefold()]
            row_data.update(
                {
                    "Portfolio POS": float(exposure["POS"].sum()),
                    "Branches": int(exposure["Branch"].replace("", pd.NA).nunique()),
                    "Customers": int(exposure["Customers"].sum()),
                }
            )
        geo_rows.append(row_data)
    geo = pd.DataFrame(geo_rows)
    if geo.empty:
        st.info("No state-level mentions in the selected view.")
    else:
        geo = geo.sort_values(["Max score", "Events"], ascending=False)
        formats: dict[str, Any] = {"Latest": st.column_config.DatetimeColumn("Latest", format="DD-MMM HH:mm")}
        if "Portfolio POS" in geo.columns:
            formats["Portfolio POS"] = st.column_config.NumberColumn("Portfolio POS", format="₹ %.2f")
        st.dataframe(geo, hide_index=True, use_container_width=True, column_config=formats)
        st.bar_chart(geo.set_index("State")["Events"], color="#0f9d72")

with tabs[6]:
    section_header("Escalation queue", "Critical and High Risk", f"{len(high_view)} events")
    render_cards(high_view, portfolio, columns=2)

with tabs[7]:
    section_header("Early-warning movement", "Risk Trends", f"Rolling {ARCHIVE_DAYS}-day archive")
    archive = load_archive(ARCHIVE_DAYS)
    if archive.empty:
        st.info("Trend history will appear after the archive has accumulated data.")
    else:
        daily = archive.assign(Date=archive["published"].dt.date).groupby("Date").agg(Events=("event_id", "count"), Average_Risk=("risk_score", "mean"), High_Risk=("severity", lambda values: values.isin(["Critical", "High"]).sum()))
        col_a, col_b = st.columns(2)
        with col_a:
            st.caption("Daily event volume")
            st.line_chart(daily[["Events", "High_Risk"]], color=["#2563eb", "#dc2626"])
        with col_b:
            st.caption("Average risk score")
            st.line_chart(daily[["Average_Risk"]], color="#d97706")

        theme_rows = []
        for theme, pattern in THEME_PATTERNS.items():
            today_mask = filtered["title"].str.contains(pattern, case=False, regex=True) | filtered["summary"].str.contains(pattern, case=False, regex=True)
            seven_cutoff = pd.Timestamp(now - timedelta(days=7))
            archive_text = archive["title"].fillna("")
            seven_count = int(((archive["published"] >= seven_cutoff) & archive_text.str.contains(pattern, case=False, regex=True)).sum())
            theme_rows.append({"Theme": theme, "Current view": int(today_mask.sum()), "Last 7 days": seven_count})
        st.dataframe(pd.DataFrame(theme_rows), hide_index=True, use_container_width=True)

        recent_cutoff = pd.Timestamp(now - timedelta(hours=6))
        previous_cutoff = pd.Timestamp(now - timedelta(hours=12))
        velocity_rows = []
        for company in COMPANY_PATTERNS:
            company_mask = archive["companies"].apply(lambda values, name=company: name in values)
            current_count = int((company_mask & (archive["published"] >= recent_cutoff)).sum())
            previous_count = int((company_mask & (archive["published"] >= previous_cutoff) & (archive["published"] < recent_cutoff)).sum())
            if current_count or previous_count:
                velocity = ((current_count - previous_count) / previous_count * 100) if previous_count else (100.0 if current_count else 0.0)
                velocity_rows.append({"Company": company, "Last 6h": current_count, "Previous 6h": previous_count, "Velocity %": velocity})
        if velocity_rows:
            st.caption("Company news velocity")
            st.dataframe(pd.DataFrame(velocity_rows).sort_values("Velocity %", ascending=False), hide_index=True, use_container_width=True, column_config={"Velocity %": st.column_config.NumberColumn("Velocity %", format="%+.0f%%")})

with tabs[8]:
    section_header("Listed-entity monitor", "Market Watch", "Yahoo Finance delayed market data")
    if market:
        market_frame = pd.DataFrame(market)
        anomaly_count = int(market_frame["Alert"].isin(["High", "Watch"]).sum())
        if anomaly_count:
            render_html(f'<div class="alert-box"><strong>{anomaly_count} market anomaly signal(s)</strong> based on absolute daily move thresholds of 3% and 5%.</div>')
        st.dataframe(
                        getattr(market_frame.style, "map", None) and market_frame.style.map(lambda value: "color:#b91c1c;font-weight:700" if isinstance(value, (int, float)) and value < 0 else "color:#047857;font-weight:700" if isinstance(value, (int, float)) and value > 0 else "", subset=["Change", "Change %", "5D %"]).format({"LTP": "{:,.2f}", "Change": "{:+,.2f}", "Change %": "{:+.2f}%", "5D %": "{:+.2f}%"}) or market_frame.style.applymap(lambda value: "color:#b91c1c;font-weight:700" if isinstance(value, (int, float)) and value < 0 else "color:#047857;font-weight:700" if isinstance(value, (int, float)) and value > 0 else "", subset=["Change", "Change %", "5D %"]).format({"LTP": "{:,.2f}", "Change": "{:+,.2f}", "Change %": "{:+.2f}%", "5D %": "{:+.2f}%"}),
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.warning("Market feed is currently unavailable.")

with tabs[9]:
    archive = load_archive(ARCHIVE_DAYS)
    section_header("Persistent evidence", "30-Day News Archive", f"{len(archive)} stored events")
    if archive.empty:
        st.info("No archived items yet.")
    else:
        archive_display = archive[["published", "severity", "risk_score", "stream", "category", "source", "title", "link"]].copy()
        st.dataframe(
            archive_display,
            hide_index=True,
            use_container_width=True,
            column_config={
                "published": st.column_config.DatetimeColumn("Published", format="DD-MMM-YYYY HH:mm"),
                "link": st.column_config.LinkColumn("Article", display_text="Open"),
                "risk_score": st.column_config.ProgressColumn("Risk score", min_value=0, max_value=100, format="%d"),
            },
        )

with tabs[10]:
    section_header("Management output", "Export Current View", f"{len(filtered)} events")
    export = export_frame(filtered)
    csv_bytes = export.to_csv(index=False).encode("utf-8-sig")
    excel_bytes = to_excel_bytes(export)
    html_bytes = export.to_html(index=False, escape=True).encode("utf-8")
    pdf_bytes = to_pdf_bytes(filtered, now)
    download_columns = st.columns(4)
    with download_columns[0]:
        st.download_button("Download CSV", csv_bytes, f"Risk_Daily_News_{now:%Y-%m-%d}.csv", "text/csv", use_container_width=True)
    with download_columns[1]:
        if excel_bytes:
            st.download_button("Download Excel", excel_bytes, f"Risk_Daily_News_{now:%Y-%m-%d}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
        else:
            st.button("Excel unavailable", disabled=True, use_container_width=True)
    with download_columns[2]:
        st.download_button("Download HTML", html_bytes, f"Risk_Daily_News_{now:%Y-%m-%d}.html", "text/html", use_container_width=True)
    with download_columns[3]:
        if pdf_bytes:
            st.download_button("Download PDF", pdf_bytes, f"Risk_Daily_News_{now:%Y-%m-%d}.pdf", "application/pdf", use_container_width=True)
        else:
            st.button("PDF unavailable", disabled=True, use_container_width=True)

    st.caption("Exports contain the current filter selection. Excel requires openpyxl; PDF requires reportlab.")
    st.dataframe(
        export,
        hide_index=True,
        use_container_width=True,
        column_config={"link": st.column_config.LinkColumn("Article", display_text="Open")},
    )

# -----------------------------------------------------------------------------
# Footer
# -----------------------------------------------------------------------------
tracked = " · ".join(MARKET_SYMBOLS.keys())
render_html(
    f'<div class="footer"><b>Developed by Faiz Khan</b><div class="small">Risk Daily News · Microfinance, JLG/SHG, NBFC, regulatory and climate-risk monitoring</div>'
    f'<div class="small"><b>Tracked companies / index:</b> {html.escape(tracked)}</div>'
    f'<div class="small">Bank-only news is excluded; mixed bank/NBFC developments are retained. Risk scores and tags are rules-based and should be verified against the original source before action.</div></div>'
)
