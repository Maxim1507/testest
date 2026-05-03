#!/usr/bin/env python3
"""
Prüft Verfügbarkeit und Preis der Rankgitter-Materialien
im Jumbo Bern Bethlehem (Store-ID 1034_POS) via Playwright.
"""
import asyncio, json, re, sys
from playwright.async_api import async_playwright

CHROMIUM = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
STORE_ID  = "1034_POS"   # Jumbo Bern Bethlehem, Kasparstrasse 7/9
STORE_NAME = "Jumbo Bern Bethlehem"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

PRODUCTS = [
    {
        "name": "Latte roh 24×48×3000mm (Fichte, Oecoplan)",
        "url":  "https://www.jumbo.ch/de/bauen-renovieren/holz/bauholz-profilholz/dachlatten/oecoplan-latte-roh-24x48-mm-30-m/p/6416885",
        "qty":  12,
    },
    {
        "name": "Latte roh 24×48×2500mm (Fichte, Oecoplan)",
        "url":  "https://www.jumbo.ch/de/bauen-renovieren/holz/bauholz-profilholz/dachlatten/oecoplan-latte-roh-24x48-mm-25-m/p/3851719",
        "qty":  6,
        "note": "Alternative für 200cm-Stücke (Verschnitt)",
    },
    {
        "name": "Fischer Universaldübel UX 8mm 50 Stk",
        "url":  "https://www.jumbo.ch/de/maschinen-werkstatt/kleineisenwaren/duebel/fischer-universalduebel-ux--8-mm--50-stueck/p/3445352",
        "qty":  2,
        "note": "2 Packungen = 100 Stk (60 benötigt)",
    },
    {
        "name": "SPAX A2 Torx 4×40mm (Holzschrauben)",
        "url":  "https://www.jumbo.ch/de/maschinen-werkstatt/kleineisenwaren/schrauben/holzschrauben/kleinpackungen/spax-a2-rostfrei-torx--4--40-mm/p/6952789",
        "qty":  1,
        "note": "Für Quersegmente; Pack-Inhalt prüfen → 200 benötigt",
    },
    {
        "name": "SPAX A2 Torx 6×100mm (Wandbefestigung)",
        "url":  "https://www.jumbo.ch/de/maschinen-werkstatt/kleineisenwaren/schrauben/holzschrauben/kleinpackungen/spax-a2-rostfrei-torx-6-x-100-mm/p/6952988",
        "qty":  1,
        "note": "60 benötigt; Pack-Inhalt prüfen",
    },
    {
        "name": "Distanzhülsen / Abstandshülsen M6 40mm",
        "url":  "https://www.jumbo.ch/de/suche?q=distanzh%C3%BClse+40mm",
        "qty":  60,
        "note": "Suche – evtl. nicht vorhanden",
        "is_search": True,
    },
]


def clean(txt: str) -> str:
    return " ".join(txt.split())[:120]


async def set_store(page, store_id: str):
    """Setzt den bevorzugten Jumbo-Markt via localStorage + Cookie."""
    await page.evaluate(f"""
        localStorage.setItem('selectedStore', '{store_id}');
        localStorage.setItem('preferredStore', '{store_id}');
        document.cookie = 'storeId={store_id}; path=/; domain=.jumbo.ch';
    """)


async def dismiss_overlay(page):
    for sel in [
        "button[id*='accept']", "#onetrust-accept-btn-handler",
        "button:has-text('Alle akzeptieren')", "button:has-text('Akzeptieren')",
        "[data-testid='cookie-accept']", ".cookie-accept",
    ]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1500):
                await btn.click(timeout=2000)
                await page.wait_for_timeout(600)
                return
        except Exception:
            pass


async def get_price(page) -> str:
    # 1) JSON-LD
    try:
        scripts = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
                        .map(s => s.textContent);
        }""")
        for raw in scripts:
            try:
                d = json.loads(raw)
                offers = d.get("offers") or d.get("Offers") or {}
                if isinstance(offers, list): offers = offers[0]
                p = offers.get("price") or d.get("price", "")
                if p:
                    return f"CHF {p}"
            except Exception:
                pass
    except Exception:
        pass

    # 2) DOM selectors
    for sel in [
        "[data-test='product-price']", "[data-e2e='product-price']",
        "[itemprop='price']", "[class*='ProductPrice']",
        "[class*='product-price']", "[class*='pdp-price']",
        "[class*='PriceValue']", "[class*='priceValue']",
        "[class*='price-value']", ".price",
    ]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=800):
                txt = await el.inner_text()
                if txt.strip():
                    return clean(txt)
        except Exception:
            pass

    # 3) Regex im Body-Text
    try:
        body = await page.evaluate("() => document.body.innerText")
        hits = re.findall(r"CHF\s*[\d'.,]+", body)
        if hits:
            return hits[0]
    except Exception:
        pass

    return "–"


async def get_stock_info(page) -> dict:
    """Liest Lagerbestand / Verfügbarkeit für den gesetzten Store."""
    result = {"status": "unbekannt", "store": "", "text": ""}

    # Warte kurz auf dynamische Inhalte
    await page.wait_for_timeout(1500)

    # Selektoren für Verfügbarkeits-Badges
    for sel in [
        "[data-test='product-availability']",
        "[data-test='store-availability']",
        "[class*='Availability']", "[class*='availability']",
        "[class*='stock']", "[class*='Stock']",
        "[class*='inStock']", "[class*='in-stock']",
        "[class*='StorePickup']", "[class*='store-pickup']",
        "[class*='pickup']",
    ]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=600):
                txt = await el.inner_text()
                if txt.strip():
                    result["text"] = clean(txt)
                    break
        except Exception:
            pass

    # Filial-Name im Text
    try:
        body = await page.evaluate("() => document.body.innerText")
        if "Bethlehem" in body or "bethlehem" in body.lower():
            result["store"] = "Bethlehem gefunden im Text"
        if re.search(r"verfügbar|auf lager|in stock|abholber|lieferbar", body, re.I):
            result["status"] = "verfügbar"
        elif re.search(r"nicht verfügbar|ausverkauft|not available|vergriffen", body, re.I):
            result["status"] = "nicht verfügbar"
        elif re.search(r"online|lieferung|versand", body, re.I):
            result["status"] = "nur online?"
    except Exception:
        pass

    return result


async def check_product(ctx, prod: dict) -> dict:
    page = await ctx.new_page()
    await page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,eot,mp4}", lambda r: r.abort())

    result = {
        "name":   prod["name"],
        "url":    prod["url"],
        "qty":    prod.get("qty", 1),
        "note":   prod.get("note", ""),
        "price":  "–",
        "stock":  "–",
        "store_text": "",
        "pack_content": "",
    }

    try:
        print(f"\n► {prod['name']}")
        await page.goto(prod["url"], timeout=35000, wait_until="domcontentloaded")
        await set_store(page, STORE_ID)
        await dismiss_overlay(page)

        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)

        title = await page.title()
        print(f"  Titel: {title[:80]}")

        result["price"] = await get_price(page)
        stock = await get_stock_info(page)
        result["stock"] = stock["status"]
        result["store_text"] = stock["text"] or stock["store"]

        # Packungsinhalt aus Seitentext extrahieren
        try:
            body = await page.evaluate("() => document.body.innerText")
            m = re.search(r"(\d+)\s*(Stück|Stk\.?|pcs?|pieces?)", body, re.I)
            if m:
                result["pack_content"] = f"{m.group(1)} Stk/Pack"
        except Exception:
            pass

        # Screenshot für Debugging
        await page.screenshot(path=f"/tmp/jumbo_{prod['name'][:20].replace(' ','_')}.png")

        print(f"  Preis: {result['price']}  |  Verfügbarkeit: {result['stock']}  |  {result['store_text']}")

    except Exception as e:
        result["stock"] = f"FEHLER: {e}"
        print(f"  FEHLER: {e}")
    finally:
        await page.close()

    return result


async def main():
    results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            executable_path=CHROMIUM,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled",
                  "--ignore-certificate-errors"],
        )
        ctx = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1400, "height": 900},
            locale="de-CH",
            extra_http_headers={"Accept-Language": "de-CH,de;q=0.9,en;q=0.8"},
        )

        # Store-Cookie einmalig setzen
        await ctx.add_cookies([{
            "name": "storeId", "value": STORE_ID,
            "domain": ".jumbo.ch", "path": "/"
        }])

        for prod in PRODUCTS:
            r = await check_product(ctx, prod)
            results.append(r)
            await asyncio.sleep(2)

        await browser.close()

    # Ausgabe
    print("\n\n" + "═" * 100)
    print(f"  VERFÜGBARKEITS- UND PREISCHECK — {STORE_NAME}")
    print("═" * 100)
    header = f"{'Produkt':<48} {'Preis':>12}  {'Verfügb.':^16}  {'Pack / Hinweis'}"
    print(header)
    print("─" * 100)

    total_min = 0.0
    missing = []

    for r in results:
        # Preis parsen
        price_num = None
        m = re.search(r"[\d']+\.?\d*", r["price"].replace("'", ""))
        if m:
            try:
                price_num = float(m.group(0))
            except ValueError:
                pass

        avail = r["stock"]
        if "verfügbar" in avail.lower():
            avail_str = "✓ verfügbar"
        elif "nicht" in avail.lower() or "fehler" in avail.lower():
            avail_str = "✗ " + avail[:14]
            missing.append(r["name"])
        else:
            avail_str = "? " + avail[:14]

        qty_info = f"×{r['qty']}" if r["qty"] > 1 else ""
        extra = (r["pack_content"] or r["note"] or r["store_text"])[:35]

        price_str = r["price"] if r["price"] != "–" else "–"
        if price_num and r["qty"] > 1:
            total_line = price_num * r["qty"]
            total_min += total_line
            price_str += f"  (×{r['qty']} = CHF {total_line:.2f})"
        elif price_num:
            total_min += price_num

        print(f"  {r['name'][:46]:<46}  {price_str:>14}  {avail_str:^16}  {extra}")

    print("─" * 100)
    print(f"  Geschätztes Total (Mindestrechnung):  CHF {total_min:.2f}")
    print("═" * 100)

    if missing:
        print("\n⚠ Nicht verfügbar / unklar:")
        for m in missing:
            print(f"   – {m}")

    with open("/home/user/testest/store_check.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\nJSON gespeichert: store_check.json")


asyncio.run(main())
