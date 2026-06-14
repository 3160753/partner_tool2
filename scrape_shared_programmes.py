"""
scrape_shared_programmes.py
────────────────────────────
    This script automatically visits the European Commission's Regional Policy website
    for all 27 EU Member States and collects information about the 2021-2027 Structural
    Funds programmes (such as ERDF, ESF+, Cohesion Fund, Just Transition Fund, Interreg).

    For each programme it finds, it records:
      - Which country it belongs to
      - The name of the programme and its managing authority
      - A contact person and email address
      - A link to the programme's official page
      - The CCI code (a unique EU identifier for each programme)
      - Which fund type it belongs to (e.g. ESF+, ERDF)
      - Which thematic area(s) it covers (e.g. Climate, Digital, Social Inclusion)

    All results are saved to a single file called shared_programmes.json.

Output: shared_programmes.json
  {
    "generated": "...",
    "programmes": [
      {
        "country_code": "AT",
        "country": "Austria",
        "programme_name": "ESF+ Programme Employment Austria & JTF 2021-2027",
        "managing_authority": "Bundesministerium für Arbeit...",
        "contact_name": "Mag. Bibiana Klingseisen",
        "email": "bibiana.klingseisen@...",
        "url": "https://ec.europa.eu/regional_policy/in-your-country/programmes/2021-2027/at/...",
        "cci": "2021AT05FFPR001",
        "fund": "ESF+",
        "thematic_clusters": ["Culture, Creativity & Inclusion", "Cross-cutting / Other"]
      }, ...
    ]
  }

How to run:
    pip install requests beautifulsoup4
    python scrape_shared_programmes.py
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Country list ──────────────────────────────────────────────────────────────

COUNTRIES = [
    ("AT", "Austria",      "austria"),
    ("BE", "Belgium",      "belgium"),
    ("BG", "Bulgaria",     "bulgaria"),
    ("HR", "Croatia",      "croatia"),
    ("CY", "Cyprus",       "cyprus"),
    ("CZ", "Czech Republic","czechia"),
    ("DK", "Denmark",      "denmark"),
    ("EE", "Estonia",      "estonia"),
    ("FI", "Finland",      "finland"),
    ("FR", "France",       "france"),
    ("DE", "Germany",      "germany"),
    ("GR", "Greece",       "greece"),
    ("HU", "Hungary",      "hungary"),
    ("IE", "Ireland",      "ireland"),
    ("IT", "Italy",        "italy"),
    ("LV", "Latvia",       "latvia"),
    ("LT", "Lithuania",    "lithuania"),
    ("LU", "Luxembourg",   "luxembourg"),
    ("MT", "Malta",        "malta"),
    ("NL", "Netherlands",  "netherlands"),
    ("PL", "Poland",       "poland"),
    ("PT", "Portugal",     "portugal"),
    ("RO", "Romania",      "romania"),
    ("SK", "Slovakia",     "slovakia"),
    ("SI", "Slovenia",     "slovenia"),
    ("ES", "Spain",        "spain"),
    ("SE", "Sweden",       "sweden"),
]

BASE_URL = "https://ec.europa.eu/regional_policy/in-your-country/managing-authorities/{}_en"
PROG_BASE = "https://ec.europa.eu/regional_policy/in-your-country/programmes/2021-2027"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Fund detection from CCI code ──────────────────────────────────────────────
# CCI (Common Classification Identifier) format: 2021CCXXYYYYY
#   CC = two-letter country code (e.g. AT for Austria)
#   XX = two-character fund code (e.g. 05 = ESF+, 16 = ERDF)
#   TC = Interreg (territorial cooperation programmes)

def detect_fund(cci: str, name: str) -> str:
    """
    Work out which EU fund type a programme belongs to.

    Every EU Structural Funds programme has a CCI code — a standardised
    reference number assigned by the European Commission. This function reads
    specific characters within that code to identify the fund (e.g. ESF+, ERDF).
    If the CCI code is missing or unclear, it falls back to looking for
    recognisable keywords in the programme's name instead.

    Parameters:
        cci  – The CCI code string (e.g. "2021AT05FFPR001").
        name – The full programme name (e.g. "ESF+ Employment Austria 2021-2027").

    Returns:
        A string with the fund type, such as "ESF+", "ERDF", "CF", "JTF",
        "Interreg", "EMFAF", "EAFRD", or "ERDF/ESF+" when it cannot be
        determined with confidence.
    """
    cci_up  = (cci or "").upper()
    name_lo = (name or "").lower()

    # Check for Interreg (territorial cooperation): signalled by "TC" in the CCI
    if "TC" in cci_up[:8]:          return "Interreg"
    # Check for Just Transition Fund: signalled by "JT" characters in positions 6-9
    if "JT" in cci_up[6:10]:        return "JTF"

    # Read the two-character fund code at positions 6-7 of the CCI
    fund_code = cci_up[6:8] if len(cci_up) >= 8 else ""
    if fund_code == "05":            return "ESF+"   # European Social Fund+
    if fund_code == "16":            return "ERDF"   # European Regional Development Fund
    if fund_code == "08":            return "CF"     # Cohesion Fund

    # Fallback: search the programme name for recognisable keywords
    if "interreg" in name_lo:        return "Interreg"
    if "esf" in name_lo:             return "ESF+"
    if "erdf" in name_lo or "regional development" in name_lo: return "ERDF"
    if "cohesion fund" in name_lo:   return "CF"
    if "just transition" in name_lo or "jtf" in name_lo: return "JTF"
    if "emfaf" in name_lo or "maritime" in name_lo:      return "EMFAF"  # Maritime & Fisheries Fund
    if "eafrd" in name_lo or "rural development" in name_lo:  return "EAFRD"  # Rural Development Fund

    # Could not determine the fund type — return a generic label
    return "ERDF/ESF+"


# ── Thematic classification from fund + programme name ───────────────────────
#
# This dictionary maps each ICONS thematic cluster label to a list of keywords.
# When classifying a programme, the script searches for these keywords in the
# programme name and fund type. If any keyword is found, the programme is tagged
# with that cluster label. A single programme can belong to multiple clusters.
#
# Note: some keywords are partial word stems (e.g. "integr" matches both
# "integration" and "integrated") — this is intentional to catch variations.
# Keywords with a dot (e.g. "low.carbon") are regex patterns where "." means
# "any character", allowing it to match "low-carbon" or "low carbon".

THEMATIC_KEYWORDS = {
    "Health & Life Sciences": [
        "health", "medical", "sanit", "hospital", "care", "salute",
    ],
    "Culture, Creativity & Inclusion": [
        "employment", "social", "inclusion", "education", "training",
        "youth", "poverty", "deprivat", "material", "culture", "creative",
        "erasmus", "esf", "labour", "workforce", "gender", "equal",
        "integr", "migrant", "asylum",
    ],
    "Digital, Industry & Space": [
        "digital", "innovation", "competitiv", "sme", "enterprise",
        "research", "technology", "industri", "smart",
    ],
    "Climate, Energy & Mobility": [
        "climate", "energy", "green", "low.carbon", "transition",
        "transport", "mobility", "infrastructure", "environment",
        "sustainable", "carbon", "jtf", "just transition", "erdf",
        "growth", "investment",
    ],
    "Food, Bioeconomy & Environment": [
        "rural", "agriculture", "food", "bioeconom", "maritime",
        "fisheries", "coastal", "eafrd", "emfaf",
    ],
    "Security & Resilience": [
        "security", "resilience", "civil protection", "border",
        "isf", "migration",
    ],
    "Regional Development & Territorial Cooperation": [
        "interreg", "territorial", "cooperation", "cross.border",
        "transnational",
    ],
}

def classify_thematic(name: str, fund: str) -> list:
    """
    Assign one or more thematic area labels to a programme.

    EU programmes often cover multiple policy topics — this function categorises
    each programme into the thematic clusters used by ICONS (e.g. "Climate, Energy
    & Mobility", "Digital, Industry & Space"). It does this by scanning the
    programme name and fund type for recognisable keywords.

    The matching works as follows:
      1. Convert both the programme name and fund label to lowercase.
      2. For each thematic cluster, check if any of its keywords appear
         anywhere in that combined text.
      3. If no keywords match at all, assign a sensible default based on the
         fund type (e.g. ESF+ → social inclusion, CF → climate/mobility).
      4. Remove any duplicate labels while preserving the order they were found.

    Parameters:
        name – The full programme name (e.g. "ERDF Regional Growth Tuscany 2021-2027").
        fund – The fund type string returned by detect_fund (e.g. "ESF+", "JTF").

    Returns:
        A list of thematic cluster label strings, e.g.:
        ["Climate, Energy & Mobility", "Digital, Industry & Space"]
    """
    name_lo = (name or "").lower()
    fund_lo = (fund or "").lower()
    # Combine name and fund into one string so both are searched at once
    combined = name_lo + " " + fund_lo

    found = []
    for label, keywords in THEMATIC_KEYWORDS.items():
        for kw in keywords:
            # re.search checks whether the keyword appears anywhere in the text
            if re.search(kw, combined):
                found.append(label)
                break  # One keyword match is enough — move on to the next cluster

    # If no keywords matched at all, apply fund-level defaults
    if not found:
        if fund == "ESF+":   found = ["Culture, Creativity & Inclusion"]
        elif fund == "JTF":  found = ["Climate, Energy & Mobility"]
        elif fund == "EAFRD":found = ["Food, Bioeconomy & Environment"]
        elif fund == "EMFAF":found = ["Food, Bioeconomy & Environment"]
        elif fund == "Interreg": found = ["Regional Development & Territorial Cooperation"]
        elif fund == "CF":   found = ["Climate, Energy & Mobility"]
        else:                found = ["Cross-cutting / Other"]

    # Remove any duplicate labels while preserving the order they were added
    return list(dict.fromkeys(found))


# ── HTML parsing ──────────────────────────────────────────────────────────────

def parse_page(html: str, country_code: str, country_name: str) -> list:
    """
    Extract all 2021-2027 Structural Funds programmes from a country page.

    The European Commission's "Managing Authorities" page for each country
    contains information about every programme grouped by programming period
    (e.g. 2021-2027, 2014-2020). This function reads the raw HTML of one such
    page and picks out only the 2021-2027 section, then collects the details
    of each managing authority and its associated programme.

    How the page is structured (and how we read it):
      - An <h4> heading signals the start of a programming period (e.g. "2021-2027").
      - Each managing authority block begins with an <h3> heading.
      - The details (programme name, contact, email, CCI code) appear as
        <dt>/<dd> pairs — like a definition list where <dt> is the label
        and <dd> is the value.

    After collecting all records, the function:
      - Calls detect_fund() to assign a fund type to each programme.
      - Calls classify_thematic() to assign thematic cluster labels.
      - Filters out any records whose CCI code does not start with "2021",
        as a safety check to exclude older programming periods.

    Parameters:
        html         – The raw HTML text of the country's managing authorities page.
        country_code – Two-letter ISO code (e.g. "IT" for Italy).
        country_name – Full country name (e.g. "Italy").

    Returns:
        A list of dictionaries, one per programme found, each containing:
        country_code, country, managing_authority, contact_name, email,
        programme_name, url, cci, fund, thematic_clusters.
    """
    soup = BeautifulSoup(html, "html.parser")
    programmes = []

    # Track whether we are currently reading inside the 2021-2027 section
    in_section = False
    # Holds the data being built for the programme currently being parsed
    current_ma = {}

    # Walk through every sub-heading and definition pair on the page
    for tag in soup.find_all(["h4", "h3", "dt", "dd"]):
        text = tag.get_text(strip=True)

        # <h4> headings mark the boundary between programming periods
        if tag.name == "h4":
            if "2021" in text and "2027" in text:
                # Entering the 2021-2027 section — start collecting
                in_section = True
                continue
            elif "2014" in text or "2007" in text:
                # Entering an older section — stop collecting
                in_section = False
                # Save the last programme block if one was being built
                if current_ma.get("programme_name"):
                    programmes.append(current_ma)
                    current_ma = {}
                continue

        # Skip everything outside the 2021-2027 section
        if not in_section:
            continue

        # <h3> headings mark the start of a new managing authority block.
        # Save the previous block (if any) and start a fresh one.
        if tag.name == "h3":
            if current_ma.get("programme_name"):
                programmes.append(current_ma)
            # Initialise a new record with all fields empty except country info
            current_ma = {
                "country_code": country_code,
                "country": country_name,
                "managing_authority": text,   # The <h3> text is the authority's name
                "contact_name": "",
                "email": "",
                "programme_name": "",
                "url": "",
                "cci": "",
                "fund": "",
                "thematic_clusters": [],
            }

        # <dt>/<dd> pairs contain the actual programme details.
        # <dt> is the field label (e.g. "Contact"), <dd> is the value.
        elif tag.name == "dt":
            label = text.lower()
            # Find the <dd> element that immediately follows this <dt>
            dd = tag.find_next_sibling("dd")
            if not dd:
                continue
            value = dd.get_text(strip=True)

            if "contact" in label:
                # Contact person's name
                current_ma["contact_name"] = value
            elif "email" in label:
                # Extract the email address from the mailto: hyperlink
                a = dd.find("a")
                if a and "mailto" in (a.get("href","") or ""):
                    current_ma["email"] = a["href"].replace("mailto:","").strip()
            elif "operational programme" in label or "programme" in label:
                # Programme name and its link to the EC programme page
                current_ma["programme_name"] = value
                a = dd.find("a")
                if a and a.get("href"):
                    href = a["href"]
                    # Convert relative paths (starting with "/") to full URLs
                    current_ma["url"] = (
                        "https://ec.europa.eu" + href
                        if href.startswith("/") else href
                    )
            elif "cci" in label:
                # CCI code — the unique EU identifier for this programme
                current_ma["cci"] = value

    # After the loop ends, save the last programme block if one is still open
    if current_ma.get("programme_name"):
        programmes.append(current_ma)

    # Enrich each record with fund type and thematic cluster labels
    for p in programmes:
        p["fund"] = detect_fund(p["cci"], p["programme_name"])
        p["thematic_clusters"] = classify_thematic(p["programme_name"], p["fund"])

    # Safety filter: keep only records with a 2021-era CCI code, or no CCI at all
    filtered = []
    for p in programmes:
        cci = p.get("cci","")
        if cci.startswith("2021") or not cci:
            filtered.append(p)

    return filtered


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    """
    Entry point: orchestrate the full scraping run across all 27 EU Member States.

    This function ties everything together. It:
      1. Opens a shared web session (so connections are reused efficiently).
      2. Loops through every country in the COUNTRIES list.
      3. Downloads the EC Managing Authorities page for each country.
      4. Passes the HTML to parse_page() to extract programme records.
      5. Waits half a second between requests to be polite to the EC server.
      6. Removes duplicate Interreg entries — these cross-border programmes
         are listed under each participating country, so the same CCI code
         can appear multiple times; we keep only the first occurrence.
      7. Writes the final combined dataset to shared_programmes.json.
      8. Prints a summary breakdown of how many programmes were found per fund type.
    """
    # Open a persistent HTTP session — this reuses the TCP connection across
    # requests, making the scraper faster and less taxing on the server
    session = requests.Session()
    session.headers.update(HEADERS)

    all_programmes = []

    for code, name, slug in COUNTRIES:
        url = BASE_URL.format(slug)
        print(f"Fetching {name} ({code})… ", end="", flush=True)
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()   # Raise an error if the server returned a failure status
            progs = parse_page(resp.text, code, name)
            print(f"{len(progs)} programmes")
            all_programmes.extend(progs)
        except Exception as e:
            print(f"ERROR: {e}")
        # Pause briefly between requests to avoid overwhelming the EC server
        time.sleep(0.5)

    # De-duplicate Interreg programmes: because Interreg is a cross-border
    # initiative, the same programme appears on the page of every participating
    # country. We track CCI codes already seen and skip repeats.
    seen_cci = set()
    deduped = []
    for p in all_programmes:
        cci = p.get("cci","")
        if p["fund"] == "Interreg" and cci and cci in seen_cci:
            continue   # Already recorded this cross-border programme — skip it
        if cci:
            seen_cci.add(cci)
        deduped.append(p)

    # Assemble the final output structure with a timestamp and total count
    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "count": len(deduped),
        "programmes": deduped,
    }

    # Write everything to a JSON file in the current working directory
    out = Path("shared_programmes.json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n Saved {out} with {len(deduped)} programmes")

    # Print a breakdown of how many programmes belong to each fund type
    funds = {}
    for p in deduped:
        k = p["fund"]
        funds[k] = funds.get(k,0)+1
    print("By fund:", funds)


if __name__ == "__main__":
    main()
