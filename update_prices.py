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


def get(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.text


def soup_and_text(url):
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")
    txt = " ".join(soup.stripped_strings)
    return soup, txt


def load():
    return json.loads(DATA.read_text(encoding="utf-8"))


def save(d):
    d["updated_at"] = (
        datetime.now(timezone.utc)
        .astimezone()
        .isoformat(timespec="seconds")
    )
    DATA.write_text(
        json.dumps(d, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def find_mat(d, mid):
    return next(x for x in d["materials"] if x["id"] == mid)


def safe_update(
    mat,
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

    # Only move "current" to "previous" if the new value is valid.
    if usd_per_ton is not None and old_usd:
        mat["previous_usd_per_ton"] = old_usd

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


def parse_en_date(s):
    s = re.sub(r"\s+", " ", s.strip().replace(",", ""))
    return datetime.strptime(s, "%b %d %Y").date().isoformat()


def update_fx(d):
    try:
        r = requests.get(
            "https://open.er-api.com/v6/latest/USD",
            headers=UA,
            timeout=20,
        )
        r.raise_for_status()
        j = r.json()
        rates = j.get("rates", {})

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


def update_meg(d):
    url = "https://www.meglobal.biz/news-and-media/?g=28-721-1"
    _, s = soup_and_text(url)

    patterns = [
        r"announces ACP for\s+([A-Za-z]+\s+\d{4}).{0,1600}?US\$\s*([0-9,]+(?:\.\d+)?)\s*/MT",
        r"ACP for\s+([A-Za-z]+\s+\d{4}).{0,1600}?\$\s*([0-9,]+(?:\.\d+)?)\s*/MT",
    ]

    match = None
    for pat in patterns:
        match = re.search(pat, s, re.I | re.S)
        if match:
            break

    if not match:
        raise ValueError("MEG ACP not parsed")

    delivery = match.group(1)
    price = float(match.group(2).replace(",", ""))

    safe_update(
        find_mat(d, "MEG"),
        native_price=price,
        native_currency="USD",
        usd_per_ton=price,
        price_date=datetime.now().date().isoformat(),
        basis="MEGlobal ACP - CFR Asian main ports",
        source_name="MEGlobal",
        source_url=url,
        price_type="Contract Price",
        note="عقد آسيوي مرجعي، وليس سعر Spot.",
        delivery_period=f"{delivery} arrival",
    )

    print("MEG parsed:", price, "USD/ton", delivery)


def update_deg(d):
    url = (
        "https://www.echemi.com/productsInformation/"
        "pid_Seven1093-diethyleneglycol.html"
    )
    _, s = soup_and_text(url)

    # Most robust path: ECHEMI's own summary sentence.
    # Example:
    # "... as of Sep 18, 2026: ... International at 1015 USD/ton (China) ..."
    m = re.search(
        r"Diethylene glycol prices across.*?"
        r"as of\s+([A-Za-z]{3}\s+\d{1,2},\s+\d{4})"
        r".{0,800}?"
        r"International at\s+([0-9,]+(?:\.\d+)?)\s*USD/ton",
        s,
        re.I | re.S,
    )

    if m:
        dt = parse_en_date(m.group(1))
        usd = float(m.group(2).replace(",", ""))

        safe_update(
            find_mat(d, "DEG"),
            native_price=usd,
            native_currency="USD",
            usd_per_ton=usd,
            price_date=dt,
            basis="International price - China (ECHEMI)",
            source_name="ECHEMI",
            source_url=url,
            price_type="International Price",
            note=(
                "سعر دولي مرجعي للصين للمقارنة مع عروض الاستيراد؛ "
                "ليس سعر وصول مصر."
            ),
        )
        print("DEG parsed from summary:", usd, "USD/ton", dt)
        return

    # Fallback: explicit CFR China row.
    m = re.search(
        r"Diethylene glycol\s+China\s+"
        r"([A-Za-z]{3}\s+\d{1,2},?\s+\d{4})"
        r".{0,120}?CFR\s*([0-9,]+(?:\.\d+)?)\s*USD/ton",
        s,
        re.I | re.S,
    )

    if m:
        dt = parse_en_date(m.group(1))
        usd = float(m.group(2).replace(",", ""))

        safe_update(
            find_mat(d, "DEG"),
            native_price=usd,
            native_currency="USD",
            usd_per_ton=usd,
            price_date=dt,
            basis="CFR China",
            source_name="ECHEMI",
            source_url=url,
            price_type="International Price",
            note=(
                "سعر CFR China مرجعي للمقارنة مع عروض الاستيراد؛ "
                "ليس سعر وصول مصر."
            ),
        )
        print("DEG parsed from CFR row:", usd, "USD/ton", dt)
        return

    # Final fallback: China Domestic.
    m = re.search(
        r"China Domestic at\s+([0-9,]+(?:\.\d+)?)\s*Yuan/mt",
        s,
        re.I | re.S,
    )
    date_m = re.search(
        r"as of\s+([A-Za-z]{3}\s+\d{1,2},\s+\d{4})",
        s,
        re.I,
    )

    if m:
        cny = float(m.group(1).replace(",", ""))
        usd_cny = float(d["fx"].get("USD_CNY") or 0)
        usd = cny / usd_cny if usd_cny else None
        dt = (
            parse_en_date(date_m.group(1))
            if date_m else datetime.now().date().isoformat()
        )

        safe_update(
            find_mat(d, "DEG"),
            native_price=cny,
            native_currency="CNY",
            usd_per_ton=usd,
            price_date=dt,
            basis="China Domestic",
            source_name="ECHEMI",
            source_url=url,
            price_type="Market Indication",
            note=(
                "سعر محلي صيني تم استخدامه كبديل عند عدم توفر "
                "السعر الدولي في الصفحة."
            ),
        )
        print("DEG parsed from domestic fallback:", cny, "CNY/mt", dt)
        return

    raise ValueError("DEG price not parsed")


def update_teg(d):
    url = "https://www.guidechem.com/price/en/112-27-6.html"
    _, s = soup_and_text(url)

    patterns = [
        r"([0-9]{4,6}(?:\.\d+)?)\s*CNY/TON\s*Updated:\s*(\d{4}-\d{2}-\d{2})",
        r"Updated:\s*(\d{4}-\d{2}-\d{2}).{0,150}?([0-9]{4,6}(?:\.\d+)?)\s*CNY/TON",
    ]

    cny = None
    dt = None

    m = re.search(patterns[0], s, re.I | re.S)
    if m:
        cny = float(m.group(1))
        dt = m.group(2)
    else:
        m = re.search(patterns[1], s, re.I | re.S)
        if m:
            dt = m.group(1)
            cny = float(m.group(2))

    if cny is None:
        raise ValueError("TEG price not parsed")

    usd_cny = float(d["fx"].get("USD_CNY") or 0)
    usd = cny / usd_cny if usd_cny else None

    safe_update(
        find_mat(d, "TEG"),
        native_price=cny,
        native_currency="CNY",
        usd_per_ton=usd,
        price_date=dt,
        basis="China / Shandong 99.9% market indication",
        source_name="GuideChem / GuideTrends",
        source_url=url,
        price_type="Market Indication",
        note="مؤشر سوق صيني لمحتوى 99.9%.",
    )

    print("TEG parsed:", cny, "CNY/ton", dt)


def update_peg400(d):
    url = "https://chem.100ppi.com/price/plist-940-1.html"
    soup, s = soup_and_text(url)

    quotes = []

    # Parse table rows rather than relying on one brittle regex.
    for tr in soup.find_all("tr"):
        rt = " ".join(tr.stripped_strings)

        if not re.search(r"分子量\s*[：:]\s*400", rt):
            continue

        pm = re.search(r"([0-9,]+(?:\.\d+)?)\s*元/吨", rt)
        dm = re.search(r"(20\d{2}-\d{2}-\d{2})", rt)

        if pm and dm:
            quotes.append(
                {
                    "date": dm.group(1),
                    "price": float(pm.group(1).replace(",", "")),
                    "row": rt,
                }
            )

    # Regex fallback if HTML table structure changes.
    if not quotes:
        for m in re.finditer(
            r"分子量\s*[：:]\s*400"
            r".{0,250}?"
            r"([0-9,]+(?:\.\d+)?)\s*元/吨"
            r".{0,250}?"
            r"(20\d{2}-\d{2}-\d{2})",
            s,
            re.S,
        ):
            quotes.append(
                {
                    "date": m.group(2),
                    "price": float(m.group(1).replace(",", "")),
                    "row": m.group(0),
                }
            )

    if not quotes:
        raise ValueError("PEG400 price not parsed")

    latest_date = max(q["date"] for q in quotes)
    latest_prices = [
        q["price"] for q in quotes if q["date"] == latest_date
    ]

    # Use median because multiple suppliers quote PEG400 on the same day.
    cny = float(median(latest_prices))

    usd_cny = float(d["fx"].get("USD_CNY") or 0)
    usd = cny / usd_cny if usd_cny else None

    safe_update(
        find_mat(d, "PEG400"),
        native_price=cny,
        native_currency="CNY",
        usd_per_ton=usd,
        price_date=latest_date,
        basis="Median of current China supplier quotes - MW 400",
        source_name="SunSirs / 100ppi",
        source_url=url,
        price_type="Supplier Market Median",
        note=(
            f"وسيط {len(latest_prices)} عروض PEG 400 المنشورة لنفس اليوم. "
            "PEG400 ليس له Benchmark عالمي موحد، لذلك هذا مؤشر موردين "
            "وليس سعر بورصة."
        ),
    )

    print(
        "PEG400 parsed:",
        len(latest_prices),
        "quotes on",
        latest_date,
        "median =",
        cny,
        "CNY/ton",
    )


def main():
    d = load()
    update_fx(d)

    for name, fn in [
        ("MEG", update_meg),
        ("DEG", update_deg),
        ("TEG", update_teg),
        ("PEG400", update_peg400),
    ]:
        try:
            fn(d)
            print(name, "updated")
        except Exception as e:
            print(name, "kept previous value:", e)

    save(d)


if __name__ == "__main__":
    main()
