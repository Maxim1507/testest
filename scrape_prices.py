#!/usr/bin/env python3
"""
Preisscraper für Schweizer Baumaterialien via Playwright (headless Chromium)
Verbesserte Version mit JS-Evaluation und JSON-LD-Extraktion
"""

import asyncio
import json
import re
from playwright.async_api import async_playwright

RESULTS = []

# ─── Hilfsfunktionen ──────────────────────────────────────────────────────────

def clean_price(raw: str) -> str:
    """Normalisiert eine Preisangabe auf das Format '12.50'."""
    if not raw:
        return "–"
    raw = raw.strip()
    # Apostrophe als Tausendertrennzeichen entfernen, Komma → Punkt
    raw = raw.replace("'", "").replace(",", ".")
    m = re.search(r"\d+\.\d{2}", raw)
    return m.group(0) if m else raw[:20]


def is_laerche(text: str) -> str:
    t = text.lower()
    if re.search(r"l[äa]rche|larch|mél[eè]ze", t):
        return "JA (Lärche)"
    if re.search(r"fichte|tanne|épicéa|spruce|pine", t):
        return "NEIN (Fichte/Tanne)"
    return "unbekannt"


async def dismiss_cookies(page):
    """Versucht Cookie-Banner wegzuklicken."""
    selectors = [
        "#onetrust-accept-btn-handler",
        "button[id*='accept']",
        "button[class*='accept']",
        "button:has-text('Alle akzeptieren')",
        "button:has-text('Akzeptieren')",
        "button:has-text('Accept all')",
        "button:has-text('Alle Cookies akzeptieren')",
        ".js-accept-cookies",
        "[data-testid='accept-cookies']",
        "[aria-label*='accept']",
        "[aria-label*='Akzept']",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                await btn.click(timeout=2000)
                await page.wait_for_timeout(800)
                return
        except Exception:
            pass


async def load_page(page, url: str, extra_wait: int = 5000) -> tuple[str, str]:
    """Lädt eine Seite, klickt Cookie-Banner weg, gibt (html, title) zurück."""
    try:
        await page.goto(url, timeout=40000, wait_until="domcontentloaded")
    except Exception as e:
        return f"GOTO_ERROR: {e}", ""
    await dismiss_cookies(page)
    try:
        await page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        pass
    await page.wait_for_timeout(extra_wait)
    html = await page.content()
    title = await page.title()
    return html, title


# ─── JS-Evaluatoren ───────────────────────────────────────────────────────────

PRICE_JS = """() => {
    const selectors = [
        '[itemprop="price"]',
        '[data-test="product-price"]',
        '[data-e2e="product-price"]',
        '.product-price',
        '[class*="ProductPrice"]',
        '[class*="product-price"]',
        '[class*="pdp-price"]',
        '[class*="PriceValue"]',
        '[class*="priceValue"]',
        '[class*="price-value"]',
        '.price',
        '[class*="Price"]',
        '[class*="preis"]',
        '[class*="Preis"]',
    ];
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el) {
            const txt = el.innerText || el.textContent || el.getAttribute('content') || '';
            if (txt.trim()) return {sel, txt: txt.trim().substring(0, 80)};
        }
    }
    // Fallback: suche CHF im Body
    const body = document.body.innerText;
    const m = body.match(/CHF\\s*[\\d'.,']+/g);
    return {sel: 'body-fallback', txt: m ? m.slice(0,6).join(' | ') : ''};
}"""

SEARCH_JS = """() => {
    // Versuche Produktkacheln zu finden
    const cardSelectors = [
        '[class*="product-card"]', '[class*="ProductCard"]',
        '[class*="product-tile"]', '[class*="ProductTile"]',
        '[class*="product-item"]', '[class*="ProductItem"]',
        'article[class*="product"]', 'li[class*="product"]',
        '[data-test*="product"]',
    ];
    let cards = [];
    for (const s of cardSelectors) {
        cards = Array.from(document.querySelectorAll(s));
        if (cards.length > 0) break;
    }
    const items = [];
    for (const card of cards.slice(0, 5)) {
        const nameEl = card.querySelector('h2,h3,[class*="name"],[class*="Name"],[class*="title"],[class*="Title"]');
        const priceEl = card.querySelector('[class*="price"],[class*="Price"],[itemprop="price"]');
        const name = nameEl ? nameEl.textContent.trim().substring(0, 120) : '';
        const price = priceEl ? priceEl.textContent.trim().substring(0, 60) : '';
        if (name || price) items.push({name, price});
    }
    if (items.length === 0) {
        const body = document.body.innerText;
        const m = body.match(/CHF\\s*[\\d'.,]+/g);
        return [{name: '(body fallback)', price: m ? m.slice(0,6).join(' | ') : 'kein Preis'}];
    }
    return items;
}"""

JSONLD_JS = """() => {
    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
    const out = [];
    scripts.forEach(s => { try { out.push(JSON.parse(s.textContent)); } catch(e) {} });
    return out;
}"""

# ─── Preis aus JSON-LD ────────────────────────────────────────────────────────

def price_from_jsonld(data) -> str:
    if isinstance(data, list):
        for item in data:
            p = price_from_jsonld(item)
            if p:
                return p
    if isinstance(data, dict):
        # Offer/offers
        for key in ("offers", "Offers"):
            if key in data:
                p = price_from_jsonld(data[key])
                if p:
                    return p
        if "price" in data:
            return str(data["price"])
    return ""


# ─── Produkt-Scraper ──────────────────────────────────────────────────────────

async def scrape_product(page, shop: str, name: str, url: str, check_wood: bool = False, extra_wait: int = 5000):
    print(f"\n[{shop}] {name}")
    print(f"  URL: {url}")

    html, title = await load_page(page, url, extra_wait)
    if html.startswith("GOTO_ERROR") or html.startswith("ERROR"):
        print(f"  FEHLER: {html}")
        RESULTS.append({"shop": shop, "name": name, "price": "Fehler", "laerche": "–", "note": html[:120], "url": url})
        return

    print(f"  Titel: {title[:80]}")

    # 1) JSON-LD
    price = ""
    try:
        jsonld = await page.evaluate(JSONLD_JS)
        price = price_from_jsonld(jsonld)
        if price:
            print(f"  JSON-LD Preis: {price}")
    except Exception:
        pass

    # 2) JS DOM
    if not price:
        try:
            js_res = await page.evaluate(PRICE_JS)
            if js_res and js_res.get("txt"):
                m = re.search(r"[\d'.,]+", js_res["txt"])
                price = m.group(0) if m else js_res["txt"]
                print(f"  DOM ({js_res['sel']}): {js_res['txt'][:60]}")
        except Exception as e:
            print(f"  JS-Fehler: {e}")

    # 3) Regex im HTML
    if not price or price == "–":
        m = re.search(r'CHF\s*([\d\'.,]+)', html)
        if m:
            price = m.group(1)
            print(f"  Regex-Preis: {price}")

    price_clean = clean_price(price) if price else "–"

    laerche = "–"
    if check_wood:
        visible_text = re.sub(r'<[^>]+>', ' ', html)
        laerche = is_laerche(title + " " + visible_text[:8000])

    print(f"  => Preis: {price_clean} CHF  |  Lärche: {laerche}")
    RESULTS.append({"shop": shop, "name": name, "price": price_clean, "laerche": laerche, "note": "", "url": url})


async def search_and_scrape(page, shop: str, name: str, url: str, extra_wait: int = 6000):
    print(f"\n[{shop}] SUCHE: {name}")
    print(f"  URL: {url}")

    html, title = await load_page(page, url, extra_wait)
    if html.startswith("GOTO_ERROR") or html.startswith("ERROR"):
        print(f"  FEHLER: {html}")
        RESULTS.append({"shop": shop, "name": name, "price": "Fehler", "laerche": "–", "note": html[:120], "url": url})
        return

    print(f"  Titel: {title[:80]}")

    # JS-Suche nach Produktkacheln
    items = []
    try:
        items = await page.evaluate(SEARCH_JS)
    except Exception as e:
        print(f"  JS-Fehler: {e}")

    if items:
        for i, it in enumerate(items[:3]):
            print(f"  [{i+1}] {it.get('name','')[:80]}  |  Preis: {it.get('price','')[:40]}")

    # Erstes Ergebnis mit Preis
    first_price = "–"
    first_name = "–"
    for it in items:
        p_raw = it.get("price", "")
        n_raw = it.get("name", "")
        p = clean_price(p_raw)
        if p and p != "–":
            first_price = p
            first_name = n_raw[:80]
            break

    print(f"  => Bestes Ergebnis: '{first_name}'  Preis: {first_price} CHF")
    RESULTS.append({
        "shop": shop,
        "name": name,
        "found": first_name,
        "price": first_price,
        "laerche": "–",
        "note": f"Suchergebnis",
        "url": url,
    })


# ─── Hauptprogramm ────────────────────────────────────────────────────────────

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="de-CH",
            extra_http_headers={"Accept-Language": "de-CH,de;q=0.9"},
        )
        page = await context.new_page()
        # Bilder/Fonts blockieren → schneller
        await page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,eot}", lambda r: r.abort())

        # ── DIREKTE PRODUKTSEITEN ──────────────────────────────────────────

        await scrape_product(page, "Jumbo",
            "Latte 24x48mm 2.5m (Oecoplan roh)",
            "https://www.jumbo.ch/de/bauen-renovieren/holz/bauholz-profilholz/dachlatten/oecoplan-latte-roh-24x48-mm-25-m/p/3851719",
            check_wood=True, extra_wait=7000)

        await asyncio.sleep(2)

        await scrape_product(page, "Jumbo",
            "Latte 24x48mm 3.0m (Oecoplan roh)",
            "https://www.jumbo.ch/de/bauen-renovieren/holz/bauholz-profilholz/dachlatten/oecoplan-latte-roh-24x48-mm-30-m/p/6416885",
            check_wood=True, extra_wait=7000)

        await asyncio.sleep(2)

        await scrape_product(page, "Bauhaus",
            "Holzlatte 24x48mm 3000mm",
            "https://www.bauhaus.ch/de/p/holzlatte-24-x-48-14416250",
            check_wood=True, extra_wait=8000)

        await asyncio.sleep(2)

        await scrape_product(page, "Bauhaus",
            "Dachlatte 2500x48x24mm",
            "https://www.bauhaus.ch/de/dachlatte-2500-x-48-x-24-mm-60099871",
            check_wood=True, extra_wait=8000)

        await asyncio.sleep(2)

        await scrape_product(page, "Hornbach",
            "Konsta Latte Fichte 3000x48x24mm",
            "https://www.hornbach.ch/de/p/konsta-latte-fichte-3000-x-48-x-24-mm/1000686/",
            check_wood=True, extra_wait=7000)

        await asyncio.sleep(2)

        # ── JUMBO SUCHEN ──────────────────────────────────────────────────

        await search_and_scrape(page, "Jumbo",
            "Schrauben Edelstahl 4x40mm",
            "https://www.jumbo.ch/de/suche?q=schrauben+edelstahl+4x40mm", 7000)
        await asyncio.sleep(2)

        await search_and_scrape(page, "Jumbo",
            "Distanzhülsen 40mm M6",
            "https://www.jumbo.ch/de/suche?q=distanzh%C3%BClse+40mm+M6+edelstahl", 7000)
        await asyncio.sleep(2)

        await search_and_scrape(page, "Jumbo",
            "Schrauben A2 Edelstahl 6x100mm Senkkopf Torx",
            "https://www.jumbo.ch/de/suche?q=schraube+edelstahl+A2+6x100+senkkopf+torx", 7000)
        await asyncio.sleep(2)

        await search_and_scrape(page, "Jumbo",
            "Fischer UX 8mm Universaldübel 50er",
            "https://www.jumbo.ch/de/suche?q=fischer+ux+8mm+universald%C3%BCbel+50", 7000)
        await asyncio.sleep(2)

        await search_and_scrape(page, "Jumbo",
            "Holzschrauben A2 4x40mm Senkkopf Torx 200er",
            "https://www.jumbo.ch/de/suche?q=holzschraube+edelstahl+A2+4x40+torx+200", 7000)
        await asyncio.sleep(2)

        # ── BAUHAUS SUCHEN ────────────────────────────────────────────────

        await search_and_scrape(page, "Bauhaus",
            "Distanzhülsen 40mm M6",
            "https://www.bauhaus.ch/de/search?q=distanzh%C3%BClse+40mm+M6", 8000)
        await asyncio.sleep(2)

        await search_and_scrape(page, "Bauhaus",
            "Schrauben A2 Edelstahl 6x100mm Senkkopf Torx",
            "https://www.bauhaus.ch/de/search?q=schraube+A2+edelstahl+6x100+senkkopf+torx", 8000)
        await asyncio.sleep(2)

        await search_and_scrape(page, "Bauhaus",
            "Fischer UX 8mm Universaldübel 50er",
            "https://www.bauhaus.ch/de/search?q=fischer+ux+8mm+universald%C3%BCbel", 8000)
        await asyncio.sleep(2)

        await search_and_scrape(page, "Bauhaus",
            "Holzschrauben A2 4x40mm Senkkopf Torx 200er",
            "https://www.bauhaus.ch/de/search?q=holzschraube+edelstahl+A2+4x40+senkkopf+torx", 8000)
        await asyncio.sleep(2)

        await browser.close()

    # ── AUSGABE ───────────────────────────────────────────────────────────────
    print("\n\n" + "=" * 110)
    print("PREISTABELLE – Baumaterialien Schweiz (Stand: 2026-05-03)")
    print("=" * 110)
    fmt = "{:<12} {:<55} {:>12} {:<24} {}"
    print(fmt.format("Shop", "Produkt", "Preis CHF", "Lärche?", "Gefunden / Hinweis"))
    print("-" * 110)

    for r in RESULTS:
        name = r["name"][:54]
        found = r.get("found", r.get("note", ""))[:40]
        laerche = r.get("laerche", "–")
        price = r["price"]
        if price != "–" and re.match(r"[\d.]+", price):
            price = f"CHF {price}"
        print(fmt.format(r["shop"], name, price, laerche, found))

    print("=" * 110)

    with open("/home/user/testest/prices.json", "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    print("\nJSON gespeichert: /home/user/testest/prices.json")


asyncio.run(main())
