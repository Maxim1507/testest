#!/usr/bin/env python3
"""
Jumbo Bern Bethlehem – Preis & Verfügbarkeits-Checker
======================================================
Läuft direkt in Termux auf Android (Samsung Galaxy).

EINMALIGE INSTALLATION (in Termux):
  pkg update -y && pkg install python -y
  pip install requests beautifulsoup4

STARTEN:
  python check_jumbo.py
"""

import re, sys, time, json
import requests
from bs4 import BeautifulSoup

# ── Konfiguration ─────────────────────────────────────────────────────────────
STORE_ID   = "1034_POS"
STORE_NAME = "Jumbo Bern Bethlehem (Kasparstrasse 7/9)"

# Samsung Galaxy S24 Ultra / SamsungBrowser 24 – typischer UA vom Gerät selbst
UA = (
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "SamsungBrowser/24.0 Chrome/117.0.0.0 Mobile Safari/537.36"
)

PRODUCTS = [
    {
        "name": "Latte roh 24×48×3000mm (Fichte, Oecoplan)",
        "url":  "https://www.jumbo.ch/de/bauen-renovieren/holz/bauholz-profilholz/dachlatten/oecoplan-latte-roh-24x48-mm-30-m/p/6416885",
        "need": 12,
        "note": "Vertikalen (280 cm → 3m-Stück kürzen)",
    },
    {
        "name": "Latte roh 24×48×2500mm (Fichte, Oecoplan)",
        "url":  "https://www.jumbo.ch/de/bauen-renovieren/holz/bauholz-profilholz/dachlatten/oecoplan-latte-roh-24x48-mm-25-m/p/3851719",
        "need": 6,
        "note": "Quersegmente (17 cm Stücke raussägen)",
    },
    {
        "name": "Fischer Universaldübel UX 8mm, 50 Stk",
        "url":  "https://www.jumbo.ch/de/maschinen-werkstatt/kleineisenwaren/duebel/fischer-universalduebel-ux--8-mm--50-stueck/p/3445352",
        "need": 2,
        "note": "2 Packungen = 100 Stk (60 benötigt)",
    },
    {
        "name": "SPAX A2 Torx 6×100mm (Wandschrauben)",
        "url":  "https://www.jumbo.ch/de/maschinen-werkstatt/kleineisenwaren/schrauben/holzschrauben/kleinpackungen/spax-a2-rostfrei-torx-6-x-100-mm/p/6952988",
        "need": 1,
        "note": "60 Stk benötigt – Pack-Inhalt prüfen!",
    },
    {
        "name": "SPAX A2 Torx 4×40mm (Holzschrauben)",
        "url":  "https://www.jumbo.ch/de/maschinen-werkstatt/kleineisenwaren/schrauben/holzschrauben/kleinpackungen/spax-a2-rostfrei-torx--4--40-mm/p/6952789",
        "need": 1,
        "note": "200 Stk benötigt – Pack-Inhalt prüfen!",
    },
    {
        "name": "Distanzhülsen 40mm (Suche)",
        "url":  "https://www.jumbo.ch/de/suche?q=distanzh%C3%BClse+40mm+m6",
        "need": 60,
        "note": "Evtl. nicht vorhanden → Holzspacer DIY",
        "is_search": True,
    },
]

# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent":      UA,
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT":             "1",
        "Connection":      "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":  "document",
        "Sec-Fetch-Mode":  "navigate",
        "Sec-Fetch-Site":  "none",
    })
    s.cookies.set("storeId", STORE_ID, domain=".jumbo.ch")
    return s


def get_page(session: requests.Session, url: str, retries: int = 2) -> BeautifulSoup | None:
    for attempt in range(retries + 1):
        try:
            r = session.get(url, timeout=15, allow_redirects=True)
            if r.status_code == 200:
                return BeautifulSoup(r.text, "html.parser")
            if r.status_code in (429, 503):
                print(f"  Rate-limit ({r.status_code}) – warte 8s …")
                time.sleep(8)
            else:
                print(f"  HTTP {r.status_code}")
                return None
        except requests.RequestException as e:
            print(f"  Verbindungsfehler: {e}")
            if attempt < retries:
                time.sleep(3)
    return None


def extract_price(soup: BeautifulSoup) -> str:
    # 1) JSON-LD
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(tag.string or "")
            offers = d.get("offers") or d.get("Offers") or {}
            if isinstance(offers, list):
                offers = offers[0]
            p = offers.get("price") or d.get("price", "")
            if p:
                return f"CHF {p}"
        except Exception:
            pass

    # 2) Meta-Tag
    meta = soup.find("meta", {"itemprop": "price"})
    if meta and meta.get("content"):
        return f"CHF {meta['content']}"

    # 3) Typische CSS-Klassen
    for cls in ["ProductPrice", "product-price", "pdp-price", "PriceValue", "price"]:
        el = soup.find(class_=re.compile(cls, re.I))
        if el:
            txt = el.get_text(" ", strip=True)
            if re.search(r"\d", txt):
                return txt[:60]

    # 4) Rohe Regex im HTML
    m = re.search(r"CHF\s*[\d'.,]+", soup.get_text())
    if m:
        return m.group(0)

    return "–"


def extract_availability(soup: BeautifulSoup, store_id: str) -> str:
    text = soup.get_text(" ")

    # Suche nach Bern / Bethlehem / store_id im Text
    store_mentioned = (
        "Bethlehem" in text
        or store_id in text
        or re.search(r"Bern.*Kaspar|Kaspar.*Bern", text)
    )

    if re.search(r"nicht\s+verfügbar|ausverkauft|not\s+available|vergriffen", text, re.I):
        return "✗ nicht verfügbar"
    if re.search(r"in\s+den\s+Warenkorb|add\s+to\s+cart|In den Warenkorb", text, re.I):
        status = "✓ bestellbar (online)"
        if store_mentioned:
            status += " | Filiale erwähnt"
        return status
    if re.search(r"verfügbar|auf\s+lager|in\s+stock|lieferbar|abholbereit", text, re.I):
        return "✓ verfügbar"
    if re.search(r"nur\s+online|versand|lieferung", text, re.I):
        return "⚠ evtl. nur online"

    return "? unklar"


def extract_pack_size(soup: BeautifulSoup) -> str:
    text = soup.get_text(" ")
    m = re.search(r"(\d+)\s*(?:Stück|Stk\.?|pcs?)\s*/?\s*(?:Pack|Packung|Set)?", text, re.I)
    if m:
        return f"{m.group(1)} Stk/Pack"
    return ""


def color(code: str, text: str) -> str:
    codes = {"green": "32", "red": "31", "yellow": "33", "cyan": "36", "bold": "1"}
    return f"\033[{codes.get(code,'0')}m{text}\033[0m"


# ── Hauptprogramm ─────────────────────────────────────────────────────────────

def main():
    print(color("bold", f"\n{'═'*60}"))
    print(color("bold", f"  Jumbo Preischecker – {STORE_NAME}"))
    print(color("bold", f"{'═'*60}\n"))

    session = make_session()

    # Startseite einmal aufrufen um Cookies zu setzen
    print("  Öffne jumbo.ch …")
    session.get("https://www.jumbo.ch/", timeout=15)
    time.sleep(1)

    results = []
    total = 0.0

    for prod in PRODUCTS:
        print(f"\n► {prod['name']}")
        soup = get_page(session, prod["url"])

        if soup is None:
            results.append({**prod, "price": "FEHLER", "avail": "FEHLER", "pack": ""})
            print(color("red", "  Seite nicht abrufbar."))
            time.sleep(2)
            continue

        price_str  = extract_price(soup)
        avail_str  = extract_availability(soup, STORE_ID)
        pack_str   = extract_pack_size(soup)

        print(f"  Preis:        {color('cyan', price_str)}")
        print(f"  Verfügbar:    {avail_str}")
        if pack_str:
            print(f"  Pack-Inhalt:  {pack_str}")
        if prod.get("note"):
            print(f"  Hinweis:      {prod['note']}")

        # Preis für Total-Berechnung
        price_num = None
        m = re.search(r"[\d']+\.?\d*", price_str.replace("'", "").replace("CHF", ""))
        if m:
            try:
                price_num = float(m.group(0))
                line_total = price_num * prod["need"]
                total += line_total
                print(f"  Total ({prod['need']}×):  CHF {line_total:.2f}")
            except ValueError:
                pass

        results.append({
            "name":  prod["name"],
            "url":   prod["url"],
            "need":  prod["need"],
            "price": price_str,
            "avail": avail_str,
            "pack":  pack_str,
            "note":  prod.get("note", ""),
        })

        time.sleep(2)  # höfliche Pause zwischen Requests

    # ── Zusammenfassung ───────────────────────────────────────────────────────
    print(f"\n{color('bold', '═'*60)}")
    print(color("bold", "  ZUSAMMENFASSUNG"))
    print(color("bold", "═"*60))
    for r in results:
        avail_col = "green" if "✓" in r["avail"] else "red" if "✗" in r["avail"] else "yellow"
        print(f"  {r['name'][:45]:<45}  {r['price']:>10}  {color(avail_col, r['avail'][:25])}")
    print(f"\n  {'Geschätztes Total:':<45}  CHF {total:.2f}")
    print(color("bold", "═"*60))

    # JSON speichern
    out_file = "jumbo_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n  Ergebnisse gespeichert: {out_file}\n")


if __name__ == "__main__":
    main()
