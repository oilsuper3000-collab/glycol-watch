import json
import re
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

import requests
from bs4 import BeautifulSoup

DATA = Path(__file__).resolve().parent / "prices.json"

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    )
}


def fetch(url):
    r = requests.get(url, headers=UA, timeout=35)
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "html.parser")
    txt = " ".join(soup.stripped_strings)
    return soup, txt


def load():
    return json.loads(DATA.read_text(encoding="utf-8"))


def save(d):
    d["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    DATA.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def find_mat(d, mid):
    return next(x for x in d["materials"] if x["id"] == mid)


def parse_en_date(s):
    s = re.sub(r"\s+", " ", s.strip().replace(",", ""))
    return datetime.strptime(s, "%b %d %Y").date().isoformat()


def safe_update(
    mat,
    *,
    market=None,
    native_price=None,
    native_currency=None,
    price_date=None,
    usd_per_ton=None,
    basis=None,
    source_name=None,
    source_url=None,
    price_type=None,
    note=None,
    delivery_period=None,
):
    old_usd = float(mat.get("usd_per_ton") or 0)

    if usd_per_ton is not None and old_usd:
        mat["previous_usd_per_ton"] = old_usd

    if market is not None:
        mat["market"] = market
    if native_price is not None:
        mat["native_price"] = round(float(native_price), 4)
    if native_currency:
        mat["native_currency"] = native_currency
    if price_date:
        mat["price_date"] = price_date
    if usd_per_ton is not None:
        mat["usd_per_ton"] = round(float(usd_per_ton), 2)
    if basis:
        mat["basis"] = basis
    if source_name:
        mat["source_name"] = source_name
    if source_url:
        mat["source_url"] = source_url
    if price_type:
        mat["price_type"] = price_type
    if note:
        mat["note"] = note
    if delivery_period is not None:
        mat["delivery_period"] = delivery_period


def update_fx(d):
    try:
        r = requests.get(
            "https://open.er-api.com/v6/latest/USD",
            headers=UA,
            timeout=20,
        )
        r.raise_for_status()
        rates = r.json().get("rates", {})
        if rates.get("EGP"):
            d["fx"]["USD_EGP"] = float(rates["EGP"])
        if rates.get("CNY"):
            d["fx"]["USD_CNY"] = float(rates["CNY"])

        print(
            "FX updated:",
            "USD/EGP =", d["fx"].get("USD_EGP"),
            "USD/CNY =", d["fx"].get("USD_CNY"),
        )
    except Exception as e:
        print("FX kept previous value:", e)


# -----------------------------------------------------------
# MEG - TRUE ASIAN CONTRACT PRICE
# -----------------------------------------------------------
def update_meg(d):
    url = "https://www.meglobal.biz/news-and-media/?g=28-721-1"
    _, s = fetch(url)

    m = re.search(
        r"announces ACP for\s+([A-Za-z]+\s+\d{4})"
        r".{0,1800}?"
        r"US\$\s*([0-9,]+(?:\.\d+)?)\s*/MT"
        r".{0,300}?"
        r"CFR Asian main ports",
        s,
        re.I | re.S,
    )

    if not m:
        # More tolerant fallback.
        m = re.search(
            r"ACP for\s+([A-Za-z]+\s+\d{4})"
            r".{0,1800}?"
            r"US\$\s*([0-9,]+(?:\.\d+)?)\s*/MT",
            s,
            re.I | re.S,
        )

    if not m:
        raise ValueError("MEG Asian ACP not parsed")

    delivery = m.group(1)
    usd = float(m.group(2).replace(",", ""))

    safe_update(
        find_mat(d, "MEG"),
        market="Asia",
        native_price=usd,
        native_currency="USD",
        usd_per_ton=usd,
        price_date=datetime.now().date().isoformat(),
        basis="Asian Contract Price - CFR Asian main ports",
        source_name="MEGlobal",
        source_url=url,
        price_type="Asia Contract Price",
        note="السعر المرجعي الآسيوي MEGlobal ACP، وليس سعر Spot.",
        delivery_period=f"{delivery} arrival",
    )

    print("MEG ASIA updated:", usd, "USD/ton CFR Asian main ports")


# -----------------------------------------------------------
# DEG - ASIA / CHINA CFR
# -----------------------------------------------------------
def update_deg(d):
    urls = [
        "https://www.echemi.com/productsInformation/pid_Seven1093-diethyleneglycol.html",
        "https://www.echemi.com/price-curve/sinopec-yangzi-petrochemical-pid_Seven1093-4.html",
    ]

    last_error = None

    for url in urls:
        try:
            _, s = fetch(url)

            # ECHEMI summary: International at 1015 USD/ton (China)
            m = re.search(
                r"as of\s+([A-Za-z]{3}\s+\d{1,2},\s+\d{4})"
                r".{0,1000}?"
                r"International at\s+([0-9,]+(?:\.\d+)?)\s*USD/ton"
                r"(?:\s*\(China\))?",
                s,
                re.I | re.S,
            )
            if m:
                dt = parse_en_date(m.group(1))
                usd = float(m.group(2).replace(",", ""))

                safe_update(
                    find_mat(d, "DEG"),
                    market="Asia - China CFR",
                    native_price=usd,
                    native_currency="USD",
                    usd_per_ton=usd,
                    price_date=dt,
                    basis="Asia reference - CFR China",
                    source_name="ECHEMI",
                    source_url=url,
                    price_type="Asia CFR Market Price",
                    note="مرجع سوق آسيوي على أساس CFR China؛ لا يشمل تكلفة الوصول إلى مصر.",
                )
                print("DEG ASIA updated:", usd, "USD/ton CFR China", dt)
                return

            # Price curve/table form.
            m = re.search(
                r"Diethylene glycol\s+China"
                r".{0,120}?"
                r"CFR"
                r".{0,100}?"
                r"([0-9,]+(?:\.\d+)?)"
                r".{0,100}?"
                r"USD/ton"
                r".{0,100}?"
                r"([A-Za-z]{3}\s+\d{1,2},\s+\d{4})",
                s,
                re.I | re.S,
            )
            if m:
                usd = float(m.group(1).replace(",", ""))
                dt = parse_en_date(m.group(2))

                safe_update(
                    find_mat(d, "DEG"),
                    market="Asia - China CFR",
                    native_price=usd,
                    native_currency="USD",
                    usd_per_ton=usd,
                    price_date=dt,
                    basis="Asia reference - CFR China",
                    source_name="ECHEMI",
                    source_url=url,
                    price_type="Asia CFR Market Price",
                    note="مرجع سوق آسيوي على أساس CFR China؛ لا يشمل تكلفة الوصول إلى مصر.",
                )
                print("DEG ASIA updated:", usd, "USD/ton CFR China", dt)
                return

        except Exception as e:
            last_error = e

    raise ValueError(f"DEG Asia price not parsed: {last_error}")


# -----------------------------------------------------------
# TEG - ASIA / EAST CHINA
# -----------------------------------------------------------
def update_teg(d):
    url = "https://www.guidechem.com/price/en/112-27-6.html"
    _, s = fetch(url)

    m = re.search(
        r"([0-9]{4,6}(?:\.\d+)?)\s*CNY/TON"
        r".{0,80}?"
        r"Updated:\s*(\d{4}-\d{2}-\d{2})",
        s,
        re.I | re.S,
    )

    if not m:
        raise ValueError("TEG East China price not parsed")

    cny = float(m.group(1))
    dt = m.group(2)

    usd_cny = float(d["fx"].get("USD_CNY") or 0)
    usd = cny / usd_cny if usd_cny else None

    safe_update(
        find_mat(d, "TEG"),
        market="Asia - East China",
        native_price=cny,
        native_currency="CNY",
        usd_per_ton=usd,
        price_date=dt,
        basis="East China / Shandong 99.9%",
        source_name="GuideChem / GuideTrends",
        source_url=url,
        price_type="Asia Market Indication",
        note="مرجع سوق آسيوي من شرق الصين - Shandong Content 99.9%.",
    )

    print("TEG ASIA updated:", cny, "CNY/ton East China", dt)


# -----------------------------------------------------------
# PEG 400 - ASIA / EAST CHINA MARKET REFERENCE
# Primary: MySteel/Lonzhong market range
# Fallback: ChemicalBook PEG400 supplier median
# -----------------------------------------------------------
def update_peg400(d):
    primary_url = "https://www.mysteel.com/hot/1654547.html"

    try:
        _, s = fetch(primary_url)

        # Look for all dated PEG400 market reference ranges.
        matches = []

        for m in re.finditer(
            r"(20\d{2}-\d{2}-\d{2})"
            r".{0,900}?"
            r"PEG400"
            r".{0,220}?"
            r"([0-9]{4,6})\s*-\s*([0-9]{4,6})"
            r"\s*元/吨",
            s,
            re.I | re.S,
        ):
            dt = m.group(1)
            low = float(m.group(2))
            high = float(m.group(3))
            if 3000 <= low <= 30000 and low <= high <= 30000:
                matches.append((dt, low, high))

        if matches:
            latest = max(x[0] for x in matches)
            same_day = [x for x in matches if x[0] == latest]
            low = median([x[1] for x in same_day])
            high = median([x[2] for x in same_day])
            cny = (low + high) / 2.0

            usd_cny = float(d["fx"].get("USD_CNY") or 0)
            usd = cny / usd_cny if usd_cny else None

            safe_update(
                find_mat(d, "PEG400"),
                market="Asia - East China",
                native_price=cny,
                native_currency="CNY",
                usd_per_ton=usd,
                price_date=latest,
                basis=f"East China PEG400 market range {low:.0f}-{high:.0f} CNY/ton",
                source_name="MySteel / Longzhong market report",
                source_url=primary_url,
                price_type="Asia Market Reference",
                note=(
                    f"مرجع سوق شرق الصين PEG400: {low:.0f}-{high:.0f} يوان/طن. "
                    f"السعر المعروض بالدولار مبني على منتصف النطاق {cny:.0f} يوان/طن. "
                    "PEG400 لا يملك Benchmark آسيوي موحد مثل MEG."
                ),
            )

            print(
                "PEG400 ASIA updated:",
                f"{low:.0f}-{high:.0f} CNY/ton",
                "midpoint =", cny,
                "date =", latest,
            )
            return

    except Exception as e:
        print("PEG400 primary Asia source failed:", e)

    # Fallback: ChemicalBook latest PEG400 supplier quotes.
    fallback_url = "https://m.chemicalbook.com/priceindex_cb6145866.htm"
    soup, s = fetch(fallback_url)

    quotes = []

    # Parse each table row, taking only MW=400 / PEG400 rows.
    for tr in soup.find_all("tr"):
        row = " ".join(tr.stripped_strings)

        if not re.search(r"(分子量\s*[：:]\s*400|PEG\s*-?\s*400)", row, re.I):
            continue

        pm = re.search(r"([0-9,]+(?:\.\d+)?)\s*元/吨", row)
        dm_full = re.search(r"(20\d{2}-\d{2}-\d{2})", row)
        dm_short = re.search(r"(\d{2})-(\d{2})", row)

        if not pm:
            continue

        price = float(pm.group(1).replace(",", ""))

        if dm_full:
            dt = dm_full.group(1)
        elif dm_short:
            year = datetime.now().year
            dt = f"{year}-{dm_short.group(1)}-{dm_short.group(2)}"
        else:
            continue

        if 3000 <= price <= 30000:
            quotes.append((dt, price))

    # Text fallback if rows aren't represented cleanly.
    if not quotes:
        for m in re.finditer(
            r"(20\d{2}-\d{2}-\d{2}|(?:\d{2}-\d{2}))"
            r".{0,300}?"
            r"(?:分子量\s*[：:]\s*400|PEG\s*-?\s*400)"
            r".{0,200}?"
            r"([0-9,]+(?:\.\d+)?)\s*元/吨",
            s,
            re.I | re.S,
        ):
            ds = m.group(1)
            if len(ds) == 5:
                ds = f"{datetime.now().year}-{ds}"
            price = float(m.group(2).replace(",", ""))
            if 3000 <= price <= 30000:
                quotes.append((ds, price))

    if not quotes:
        raise ValueError("PEG400 Asia fallback price not parsed")

    latest = max(x[0] for x in quotes)
    latest_prices = [x[1] for x in quotes if x[0] == latest]
    cny = float(median(latest_prices))

    usd_cny = float(d["fx"].get("USD_CNY") or 0)
    usd = cny / usd_cny if usd_cny else None

    safe_update(
        find_mat(d, "PEG400"),
        market="Asia - China",
        native_price=cny,
        native_currency="CNY",
        usd_per_ton=usd,
        price_date=latest,
        basis="China PEG400 supplier-market median",
        source_name="ChemicalBook",
        source_url=fallback_url,
        price_type="Asia Supplier Market Indicator",
        note=(
            f"وسيط {len(latest_prices)} عروض PEG400 في أحدث يوم. "
            "يُستخدم كمؤشر سوق آسيوي بديل عند تعذر تقرير شرق الصين."
        ),
    )

    print(
        "PEG400 ASIA fallback updated:",
        len(latest_prices),
        "quotes, median =",
        cny,
        "CNY/ton",
        latest,
    )


def main():
    d = load()
    update_fx(d)

    jobs = [
        ("MEG", update_meg),
        ("DEG", update_deg),
        ("TEG", update_teg),
        ("PEG400", update_peg400),
    ]

    for name, fn in jobs:
        try:
            fn(d)
            print(name, "updated")
        except Exception as e:
            print(name, "kept previous value:", e)

    save(d)


if __name__ == "__main__":
    main()
