import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

DATA = Path(__file__).resolve().parent / "prices.json"
UA = {
    "User-Agent": "Mozilla/5.0 (compatible; GlycolWatch/3.1; +https://github.com/)"
}


def get(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.text


def text(url):
    return BeautifulSoup(get(url), "html.parser").get_text(" ", strip=True)


def load():
    return json.loads(DATA.read_text(encoding="utf-8"))


def save(d):
    d["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    DATA.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


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
    if old_usd:
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
        j = requests.get(
            "https://open.er-api.com/v6/latest/USD",
            headers=UA,
            timeout=20
        ).json()
        rates = j.get("rates", {})
        if rates.get("EGP"):
            d["fx"]["USD_EGP"] = float(rates["EGP"])
        if rates.get("CNY"):
            d["fx"]["USD_CNY"] = float(rates["CNY"])
    except Exception as e:
        print("FX fallback:", e)


def update_meg(d):
    url = "https://www.meglobal.biz/news-and-media/?g=28-721-1"
    s = text(url)

    patterns = [
        r"announces ACP for\s+([A-Za-z]+\s+\d{4}).{0,1200}?US\$\s*([0-9,]+(?:\.\d+)?)\s*/MT",
        r"ACP for\s+([A-Za-z]+\s+\d{4}).{0,1200}?\$\s*([0-9,]+(?:\.\d+)?)\s*/MT",
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


def update_deg(d):
    url = "https://www.echemi.com/productsInformation/pid_Seven1093-diethyleneglycol.html"
    s = text(url)

    # Prefer the international CFR China USD/ton quote because it is easier
    # to compare with import offers.
    patterns = [
        r"Diethylene glycol\s+China\s+([A-Za-z]{3}\s+\d{1,2},?\s+\d{4})\s+CFR\s+([0-9,]+(?:\.\d+)?)\s*USD/ton",
        r"China\s+([A-Za-z]{3}\s+\d{1,2},?\s+\d{4}).{0,80}?CFR\s+([0-9,]+(?:\.\d+)?)\s*USD/ton",
        r"CFR\s+([0-9,]+(?:\.\d+)?)\s*USD/ton.{0,120}?([A-Za-z]{3}\s+\d{1,2},?\s+\d{4})",
    ]

    price = None
    date_iso = None

    for i, pat in enumerate(patterns):
        m = re.search(pat, s, re.I | re.S)
        if not m:
            continue

        if i < 2:
            date_iso = parse_en_date(m.group(1))
            price = float(m.group(2).replace(",", ""))
        else:
            price = float(m.group(1).replace(",", ""))
            date_iso = parse_en_date(m.group(2))
        break

    # Fallback: South China regional CNY/mt
    if price is None:
        m = re.search(
            r"South China\s+Diethylene glycol(?:\s+GB)?\s+"
            r"([A-Za-z]{3}\s+\d{1,2},?\s+\d{4})\s+"
            r"([0-9,]+(?:\.\d+)?)\s*Yuan/mt",
            s,
            re.I | re.S,
        )
        if not m:
            raise ValueError("DEG price not parsed")

        cny = float(m.group(2).replace(",", ""))
        usd_cny = float(d["fx"].get("USD_CNY") or 0)
        usd = cny / usd_cny if usd_cny else None

        safe_update(
            find_mat(d, "DEG"),
            native_price=cny,
            native_currency="CNY",
            usd_per_ton=usd,
            price_date=parse_en_date(m.group(1)),
            basis="South China regional market indication",
            source_name="ECHEMI",
            source_url=url,
            price_type="Market Indication",
            note="سعر سوق إقليمي داخل الصين؛ لا يساوي بالضرورة CFR Egypt.",
        )
        return

    safe_update(
        find_mat(d, "DEG"),
        native_price=price,
        native_currency="USD",
        usd_per_ton=price,
        price_date=date_iso,
        basis="CFR China",
        source_name="ECHEMI",
        source_url=url,
        price_type="International Price",
        note="سعر CFR China مرجعي للمقارنة مع عروض الاستيراد، وليس سعر وصول مصر.",
    )


def update_teg(d):
    url = "https://www.guidechem.com/price/en/112-27-6.html"
    s = text(url)

    patterns = [
        r"([0-9]{4,6}(?:\.\d+)?)\s*CNY/TON\s*Updated:\s*(\d{4}-\d{2}-\d{2})",
        r"Updated:\s*(\d{4}-\d{2}-\d{2}).{0,100}?([0-9]{4,6}(?:\.\d+)?)\s*CNY/TON",
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


def update_peg400(d):
    # Stable commodity price list page; select the first/latest quote
    # explicitly marked molecular weight 400.
    url = "https://chem.100ppi.com/price/plist-940-1.html"
    s = text(url)

    patterns = [
        r"分子量[：:]\s*400.{0,120}?([0-9,]+(?:\.\d+)?)元/吨.{0,120}?(\d{4}-\d{2}-\d{2})",
        r"PEG[-\s]?400.{0,140}?([0-9,]+(?:\.\d+)?)元/吨.{0,140}?(\d{4}-\d{2}-\d{2})",
    ]

    cny = None
    dt = None

    for pat in patterns:
        m = re.search(pat, s, re.I | re.S)
        if m:
            cny = float(m.group(1).replace(",", ""))
            dt = m.group(2)
            break

    # Fallback to a stable GuideChem supplier listing.
    if cny is None:
        fallback_url = "https://china.guidechem.com/trade/pdetail36189382.html"
        fs = text(fallback_url)
        m = re.search(r"价\s*格\s*[￥¥]\s*([0-9,]+(?:\.\d+)?)", fs, re.I | re.S)
        dm = re.search(r"更新日期\s*(\d{4})年?(\d{2})月?(\d{2})日?", fs, re.I | re.S)

        if not m:
            raise ValueError("PEG400 price not parsed")

        cny = float(m.group(1).replace(",", ""))
        if dm:
            dt = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}"
        else:
            dt = datetime.now().date().isoformat()

        url = fallback_url
        source_name = "GuideChem supplier listing"
        price_type = "Supplier Indication"
        basis = "China supplier indication - PEG 400"
    else:
        source_name = "SunSirs / 100ppi"
        price_type = "Market Quote"
        basis = "China PEG 400 market quote"

    usd_cny = float(d["fx"].get("USD_CNY") or 0)
    usd = cny / usd_cny if usd_cny else None

    safe_update(
        find_mat(d, "PEG400"),
        native_price=cny,
        native_currency="CNY",
        usd_per_ton=usd,
        price_date=dt,
        basis=basis,
        source_name=source_name,
        source_url=url,
        price_type=price_type,
        note="PEG400 ليس له Benchmark عالمي موحد؛ هذا سعر سوق/مورد صيني للمقارنة ويجب مطابقته مع الـGrade والتعبئة.",
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
