import json, re, sys
from datetime import datetime, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

DATA = Path(__file__).resolve().parent / "prices.json"
UA = {"User-Agent":"Mozilla/5.0 (compatible; GlycolWatch/3.0; +https://github.com/)"}

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

def safe_update(mat, native_price=None, native_currency=None, price_date=None,
                usd_per_ton=None, basis=None, source_name=None, source_url=None,
                price_type=None, note=None, delivery_period=None):
    old_usd = float(mat.get("usd_per_ton") or 0)
    if old_usd:
        mat["previous_usd_per_ton"] = old_usd
    if native_price is not None: mat["native_price"] = round(float(native_price), 4)
    if native_currency: mat["native_currency"] = native_currency
    if price_date: mat["price_date"] = price_date
    if usd_per_ton is not None: mat["usd_per_ton"] = round(float(usd_per_ton), 2)
    if basis: mat["basis"] = basis
    if source_name: mat["source_name"] = source_name
    if source_url: mat["source_url"] = source_url
    if price_type: mat["price_type"] = price_type
    if note: mat["note"] = note
    if delivery_period is not None: mat["delivery_period"] = delivery_period

def update_fx(d):
    # Free endpoint; if it fails, old FX rates remain.
    try:
        j = requests.get("https://open.er-api.com/v6/latest/USD", headers=UA, timeout=20).json()
        rates = j.get("rates", {})
        if rates.get("EGP"): d["fx"]["USD_EGP"] = float(rates["EGP"])
        if rates.get("CNY"): d["fx"]["USD_CNY"] = float(rates["CNY"])
    except Exception as e:
        print("FX fallback:", e)

def update_meg(d):
    url = "https://www.meglobal.biz/news-and-media/?g=28-721-1"
    s = text(url)
    # Latest listed ACP block. Example: announces ACP for October 2026 ... US$880/MT
    m = re.search(r"announces ACP for\s+([A-Za-z]+\s+\d{4}).{0,900}?US\$\s*([0-9,]+(?:\.\d+)?)\s*/MT",
                  s, re.I|re.S)
    if not m:
        raise ValueError("MEG ACP not parsed")
    delivery, price = m.group(1), float(m.group(2).replace(",",""))
    safe_update(find_mat(d,"MEG"), native_price=price, native_currency="USD",
                usd_per_ton=price, price_date=datetime.now().date().isoformat(),
                basis="MEGlobal ACP - CFR Asian main ports",
                source_name="MEGlobal", source_url=url, price_type="Contract Price",
                note="عقد آسيوي مرجعي، وليس سعر Spot.",
                delivery_period=f"{delivery} arrival")

def update_deg(d):
    url = "https://www.echemi.com/productsInformation/pid_Seven1093-diethyleneglycol.html"
    s = text(url)
    # Prefer South China regional price if visible.
    m = re.search(r"South China\s+Diethylene glycol(?:\s+GB)?\s+([A-Za-z]{3}\s+\d{1,2},?\s+\d{4}).{0,120}?([0-9]{3,6}(?:\.\d+)?)\s*Yuan/mt",
                  s, re.I|re.S)
    if not m:
        # fallback: any China regional DEG Yuan/mt item
        m = re.search(r"Diethylene glycol.{0,180}?([0-9]{3,6}(?:\.\d+)?)\s*Yuan/mt", s, re.I|re.S)
        if not m: raise ValueError("DEG price not parsed")
        date_iso = datetime.now().date().isoformat()
        cny = float(m.group(1))
    else:
        cny = float(m.group(2))
        date_iso = datetime.strptime(m.group(1).replace(",",""), "%b %d %Y").date().isoformat()
    usd_cny = float(d["fx"].get("USD_CNY") or 0)
    usd = cny/usd_cny if usd_cny else None
    safe_update(find_mat(d,"DEG"), native_price=cny, native_currency="CNY",
                usd_per_ton=usd, price_date=date_iso,
                basis="South China regional market indication",
                source_name="ECHEMI", source_url=url, price_type="Market Indication",
                note="سعر سوق إقليمي داخل الصين؛ لا يساوي بالضرورة CFR Egypt.")

def update_teg(d):
    url = "https://www.guidechem.com/price/en/112-27-6.html"
    s = text(url)
    m = re.search(r"([0-9]{4,6}(?:\.\d+)?)\s*CNY/TON\s*Updated:\s*(\d{4}-\d{2}-\d{2})", s, re.I)
    if not m: raise ValueError("TEG price not parsed")
    cny, dt = float(m.group(1)), m.group(2)
    usd_cny = float(d["fx"].get("USD_CNY") or 0)
    usd = cny/usd_cny if usd_cny else None
    safe_update(find_mat(d,"TEG"), native_price=cny, native_currency="CNY",
                usd_per_ton=usd, price_date=dt,
                basis="China / Shandong 99.9% market indication",
                source_name="GuideChem / GuideTrends", source_url=url,
                price_type="Market Indication",
                note="مؤشر سوق صيني لمحتوى 99.9%.")

def update_peg400(d):
    url = "https://china.guidechem.com/21252/"
    s = text(url)
    # Supplier-listing parsing: look for PEG400 and a nearby RMB/kg quote.
    # This is intentionally labelled Supplier Indication, not a benchmark.
    chunks = re.findall(r".{0,250}PEG\s*400.{0,350}", s, re.I|re.S)
    val = None
    for chunk in chunks:
        m = re.search(r"[￥¥]\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*(?:千克|kg)", chunk, re.I)
        if m:
            val = float(m.group(1))*1000.0  # CNY/kg -> CNY/MT
            break
    if val is None:
        raise ValueError("PEG400 supplier quote not parsed")
    usd_cny = float(d["fx"].get("USD_CNY") or 0)
    usd = val/usd_cny if usd_cny else None
    safe_update(find_mat(d,"PEG400"), native_price=val, native_currency="CNY",
                usd_per_ton=usd, price_date=datetime.now().date().isoformat(),
                basis="China supplier indication - PEG 400 listings",
                source_name="GuideChem supplier listings", source_url=url,
                price_type="Supplier Indication",
                note="PEG400 ليس له Benchmark موحد؛ هذا عرض/إشارة مورد ويجب مقارنته بعروض فعلية لنفس الـGrade.")

def main():
    d=load()
    update_fx(d)
    for name, fn in [("MEG",update_meg),("DEG",update_deg),("TEG",update_teg),("PEG400",update_peg400)]:
        try:
            fn(d)
            print(name, "updated")
        except Exception as e:
            print(name, "kept previous value:", e)
    save(d)

if __name__=="__main__":
    main()
