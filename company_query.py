#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
"""
台灣公司查詢工具
查詢商工登記公示資料、工廠登記編號/地址、及產業類別

資料來源:
  1. 商工登記公示資料開放 API (data.gcis.nat.gov.tw) - 公司基本資料
  2. 工廠公示資料查詢系統 (serv.gcis.nat.gov.tw) - 工廠登記與產業類別

使用方法:
  python company_query.py                  # 互動模式
  python company_query.py 22099131         # 直接輸入統一編號
  python company_query.py "台灣積體電路"    # 直接輸入公司名稱
"""

import sys
import re
import json
import time
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from html.parser import HTMLParser


# ─── HTTP 工具 ────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9",
}


def _get(url: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1)


def _post(url: str, data: dict, opener, retries: int = 3) -> str:
    encoded = urllib.parse.urlencode(data).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, data=encoded,
                headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            )
            with opener.open(req, timeout=15) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(1)


def _strip_tags(html: str) -> str:
    """去除 HTML 標籤並壓縮空白"""
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _find_between(text: str, start: str, end: str) -> str:
    i = text.find(start)
    if i == -1:
        return ""
    i += len(start)
    j = text.find(end, i)
    return text[i:j].strip() if j != -1 else text[i:].strip()


# ─── GCIS 開放資料 API (公司基本資料) ──────────────────────────────────────────

GCIS_API_BASIC = (
    "https://data.gcis.nat.gov.tw/od/data/api/"
    "5F64D864-61CB-4D0D-8AD9-492047CC1EA6"
)
GCIS_API_BIZ = (
    "https://data.gcis.nat.gov.tw/od/data/api/"
    "236EE382-4942-41A9-BD03-CA0709025E7C"
)


def query_company_by_taxid(tax_id: str) -> dict | None:
    """透過統一編號查詢公司基本資料 (使用 GCIS 開放資料 API)"""
    url = (
        f"{GCIS_API_BASIC}?$format=json"
        f"&$filter=Business_Accounting_NO%20eq%20{tax_id}&$top=1"
    )
    try:
        raw = _get(url)
        if not raw.strip():
            return None
        data = json.loads(raw)
        return data[0] if data else None
    except Exception:
        return None


def query_company_biz_items(tax_id: str) -> list[dict]:
    """透過統一編號查詢公司所營事業項目"""
    url = (
        f"{GCIS_API_BIZ}?$format=json"
        f"&$filter=Business_Accounting_NO%20eq%20{tax_id}&$top=1"
    )
    try:
        raw = _get(url)
        if not raw.strip():
            return []
        data = json.loads(raw)
        if data and "Cmp_Business" in data[0]:
            return data[0]["Cmp_Business"]
        return []
    except Exception:
        return []


def format_roc_date(roc_date: str) -> str:
    """將民國日期 (如 0760221) 轉為可讀格式 (76年02月21日)"""
    if not roc_date or len(roc_date) < 7:
        return roc_date or ""
    try:
        year = int(roc_date[:3]) if len(roc_date) == 7 else int(roc_date[:2])
        if len(roc_date) == 7:
            month, day = roc_date[3:5], roc_date[5:7]
        else:
            month, day = roc_date[2:4], roc_date[4:6]
        return f"民國{year}年{month}月{day}日"
    except Exception:
        return roc_date


# ─── serv.gcis.nat.gov.tw (工廠公示資料) ───────────────────────────────────────

FACTORY_SEARCH_URL = "https://serv.gcis.nat.gov.tw/Fidbweb/factInfoListAction.do"
FACTORY_DETAIL_URL = "https://serv.gcis.nat.gov.tw/Fidbweb/factInfoAction.do"


def _factory_opener():
    """建立帶 Cookie 的 opener，並取得 CSRF token"""
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        req = urllib.request.Request(
            f"{FACTORY_SEARCH_URL}?method=qryCount", headers=HEADERS
        )
        with opener.open(req, timeout=15) as resp:
            html = resp.read().decode("utf-8")
        m = re.search(
            r'name="csrfPreventionSalthidden".*?value="([^"]+)"', html, re.DOTALL
        )
        csrf = m.group(1) if m else ""
        return opener, csrf
    except Exception:
        return opener, ""


def search_factories(query_name: str, regi_id: str = "") -> list[dict]:
    """
    在工廠公示資料系統中搜尋工廠。
    - query_name: 工廠名稱（通常與公司名稱相同）
    - regi_id:    工廠登記編號（8碼，如已知）
    回傳: [{"regi_id": ..., "name": ..., "estbid": ..., "agency": ...}, ...]
    """
    opener, csrf = _factory_opener()
    if not csrf:
        return []

    data = {
        "csrfPreventionSalthidden": csrf,
        "method": "query",
        "regiID": regi_id,
        "estbID": "",
        "factName": query_name,
        "addrCityCode1": "JJ",
        "addrCityCode2": "JJ",
        "factAddr": "",
        "orgCode": "JJ",
        "statCode": "JJ",
        "cityCode1": "JJ",
        "cityCode2": "JJ",
        "tmp_profitem": "JJ",
        "ITEM": "",
    }
    try:
        html = _post(FACTORY_SEARCH_URL, data, opener)
    except Exception:
        return []

    results = []
    pattern = re.compile(
        r'method=detail&estbid=([^&"]+)&agencyCode=([^"]+)"[^>]*>\s*([^<\s][^<]*?)\s*</a>'
    )
    # Deduplicate: each factory appears twice (once for regi_id, once for name)
    seen = set()
    for m in pattern.finditer(html):
        estbid, agency, text = m.group(1), m.group(2), m.group(3).strip()
        key = estbid
        if key in seen:
            continue
        seen.add(key)
        if re.match(r"^\d|^[A-Z]", text):  # regi_id row
            regi = text
        else:
            regi = estbid[3:] if len(estbid) > 8 else estbid  # fallback
        results.append({"regi_id": regi, "estbid": estbid, "agency": agency, "name": ""})

    # Pair up regi_id rows with name rows
    pairs = []
    items = list(pattern.finditer(html))
    i = 0
    while i < len(items) - 1:
        t1 = items[i].group(3).strip()
        t2 = items[i + 1].group(3).strip()
        e1 = items[i].group(1)
        e2 = items[i + 1].group(1)
        if e1 == e2:
            regi = t1 if re.match(r"^\d|^[A-Z0-9]{6,}", t1) else t2
            name = t2 if re.match(r"^\d|^[A-Z0-9]{6,}", t1) else t1
            pairs.append({
                "regi_id": regi,
                "name": name,
                "estbid": e1,
                "agency": items[i].group(2),
            })
            i += 2
        else:
            i += 1
    return pairs if pairs else results


def get_factory_detail(estbid: str, agency: str) -> dict:
    """
    取得工廠詳細資料（地址、狀態、產業類別、主要產品等）。
    """
    url = f"{FACTORY_DETAIL_URL}?method=detail&estbid={estbid}&agencyCode={agency}"
    try:
        html = _get(url)
    except Exception:
        return {}

    def extract_cell_after(label: str) -> str:
        """找標籤後的下一個 <td> 值"""
        m = re.search(
            re.escape(label) + r".*?</t[dh]>\s*<td[^>]*>(.*?)</td>",
            html, re.DOTALL | re.IGNORECASE
        )
        return _strip_tags(m.group(1)) if m else ""

    def extract_value(label: str) -> str:
        """找標籤旁（同 <tr>）的值"""
        m = re.search(
            r"<tr[^>]*>.*?" + re.escape(label) + r".*?<td[^>]*>(.*?)</td>.*?</tr>",
            html, re.DOTALL | re.IGNORECASE
        )
        if m:
            return _strip_tags(m.group(1))
        return extract_cell_after(label)

    result = {}

    def extract_td_after(label: str) -> str:
        """找標籤（在 th 中）後面緊接的 td 值"""
        m = re.search(
            re.escape(label) + r".*?</t[dh]>\s*<td[^>]*>(.*?)</td>",
            html, re.DOTALL
        )
        return _strip_tags(m.group(1)) if m else ""

    result["regi_id"] = extract_td_after("工廠登記編號")
    result["address"] = extract_td_after("工廠地址")
    result["company_tax_id"] = extract_td_after("公司（營利事業）統一編號")
    result["org_type"] = extract_td_after("工廠組織型態")
    result["status"] = extract_td_after("工廠登記狀態")
    result["last_changed"] = extract_td_after("最後核准變更日期")

    def extract_next_td_after_keyword(keyword: str) -> str:
        """找含特定關鍵字的 <tr>，再提取緊接的下一個 <td> 內容"""
        idx = html.find(keyword)
        if idx == -1:
            return ""
        # 向後找當前 </tr>
        row_end_idx = html.find("</tr>", idx)
        if row_end_idx == -1:
            return ""
        # 在 </tr> 之後找下一個 <td>
        m = re.search(r"<td[^>]*>(.*?)</td>", html[row_end_idx:], re.DOTALL)
        return _strip_tags(m.group(1)) if m else ""

    result["industry_v11"] = extract_next_td_after_keyword("第11版)")
    result["main_product_v11"] = extract_next_td_after_keyword("主要產品")

    return result


# ─── 主查詢流程 ────────────────────────────────────────────────────────────────

def query_by_taxid(tax_id: str) -> None:
    """以統一編號為主鍵查詢公司與工廠資訊"""
    print(f"\n{'='*60}")
    print(f"查詢統一編號: {tax_id}")
    print("=" * 60)

    # 1. 公司基本資料
    print("\n▶ 查詢公司基本資料 (GCIS 開放資料 API)...")
    company = query_company_by_taxid(tax_id)
    if not company:
        print(f"  找不到統一編號 {tax_id} 的公司資料。")
        return

    print(f"\n【公司基本資料】")
    print(f"  統一編號    : {company.get('Business_Accounting_NO', '')}")
    print(f"  公司名稱    : {company.get('Company_Name', '')}")
    print(f"  登記現況    : {company.get('Company_Status_Desc', '')}")
    print(f"  資本總額    : {company.get('Capital_Stock_Amount', 0):,} 元")
    print(f"  已繳資本額  : {company.get('Paid_In_Capital_Amount', 0):,} 元")
    print(f"  代表人      : {company.get('Responsible_Name', '')}")
    print(f"  公司所在地  : {company.get('Company_Location', '')}")
    print(f"  登記機關    : {company.get('Register_Organization_Desc', '')}")
    print(f"  核准設立日期: {format_roc_date(company.get('Company_Setup_Date', ''))}")
    print(f"  最後變更日期: {format_roc_date(company.get('Change_Of_Approval_Data', ''))}")

    # 2. 工廠資料
    company_name = company.get("Company_Name", "")
    print(f"\n▶ 查詢工廠登記資料 (搜尋名稱: {company_name})...")
    factories = search_factories(company_name)

    if not factories:
        print("  此公司無工廠登記資料。")
    else:
        print(f"  找到 {len(factories)} 筆工廠資料：")
        for i, fac in enumerate(factories, 1):
            print(f"\n【工廠 {i}】 登記編號: {fac.get('regi_id', '')}  名稱: {fac.get('name', '')}")
            detail = get_factory_detail(fac["estbid"], fac["agency"])
            if detail:
                if detail.get("address"):
                    print(f"  工廠地址    : {detail['address']}")
                if detail.get("status"):
                    print(f"  登記狀態    : {detail['status']}")
                if detail.get("org_type"):
                    print(f"  組織型態    : {detail['org_type']}")
                if detail.get("industry_v11"):
                    print(f"  產業類別    : {detail['industry_v11']}")
                if detail.get("main_product_v11"):
                    print(f"  主要產品    : {detail['main_product_v11']}")
            time.sleep(0.3)  # 避免請求過快


def query_by_name(company_name: str) -> None:
    """以公司名稱查詢（透過工廠系統搜尋，再反查統一編號）"""
    print(f"\n{'='*60}")
    print(f"查詢公司名稱: {company_name}")
    print("=" * 60)

    # 1. 先搜尋工廠
    print(f"\n▶ 搜尋工廠登記資料 (名稱含 '{company_name}')...")
    factories = search_factories(company_name)

    if not factories:
        print(f"  找不到名稱含「{company_name}」的工廠登記資料。")
        print("  提示：如果此公司為非製造業，可能沒有工廠登記。")
        print("        請嘗試改用統一編號查詢，或至以下網址查詢：")
        print("        https://findbiz.nat.gov.tw/fts/query/QueryBar/queryInit.do")
        return

    print(f"  找到 {len(factories)} 筆工廠資料。")

    # 2. 從第一筆工廠取得公司統一編號，再查公司基本資料
    first_factory = factories[0]
    detail0 = get_factory_detail(first_factory["estbid"], first_factory["agency"])
    company_tax_id = detail0.get("company_tax_id", "")

    if company_tax_id and re.match(r"^\d{8}$", company_tax_id):
        print(f"\n▶ 找到統一編號 {company_tax_id}，查詢公司基本資料...")
        company = query_company_by_taxid(company_tax_id)
        if company:
            print(f"\n【公司基本資料】")
            print(f"  統一編號    : {company.get('Business_Accounting_NO', '')}")
            print(f"  公司名稱    : {company.get('Company_Name', '')}")
            print(f"  登記現況    : {company.get('Company_Status_Desc', '')}")
            print(f"  資本總額    : {company.get('Capital_Stock_Amount', 0):,} 元")
            print(f"  代表人      : {company.get('Responsible_Name', '')}")
            print(f"  公司所在地  : {company.get('Company_Location', '')}")
            print(f"  登記機關    : {company.get('Register_Organization_Desc', '')}")
            print(f"  核准設立日期: {format_roc_date(company.get('Company_Setup_Date', ''))}")

    # 3. 顯示所有工廠資料
    print(f"\n【工廠登記資料】共 {len(factories)} 筆：")
    for i, fac in enumerate(factories, 1):
        print(f"\n  ─ 工廠 {i} ─")
        print(f"  登記編號    : {fac.get('regi_id', '')}")
        print(f"  工廠名稱    : {fac.get('name', '')}")
        if i == 1:
            detail = detail0  # 已在上方查過，直接重用
        else:
            detail = get_factory_detail(fac["estbid"], fac["agency"])
        if detail:
            if detail.get("address"):
                print(f"  工廠地址    : {detail['address']}")
            if detail.get("status"):
                print(f"  登記狀態    : {detail['status']}")
            if detail.get("industry_v11"):
                print(f"  產業類別    : {detail['industry_v11']}")
            if detail.get("main_product_v11"):
                print(f"  主要產品    : {detail['main_product_v11']}")
        if i >= 10:
            remaining = len(factories) - 10
            if remaining > 0:
                print(f"\n  ... 還有 {remaining} 筆工廠資料（僅顯示前10筆）")
            break
        time.sleep(0.3)


def query_by_factory_regi_id(regi_id: str) -> None:
    """直接以工廠登記編號查詢工廠資料"""
    print(f"\n{'='*60}")
    print(f"查詢工廠登記編號: {regi_id}")
    print("=" * 60)

    print("\n▶ 查詢工廠詳細資料...")
    factories = search_factories("", regi_id)
    if not factories:
        print(f"  找不到工廠登記編號 {regi_id} 的資料。")
        return

    fac = factories[0]
    detail = get_factory_detail(fac["estbid"], fac["agency"])

    print(f"\n【工廠資料】")
    print(f"  工廠登記編號: {detail.get('regi_id', regi_id)}")
    print(f"  工廠名稱    : {fac.get('name', '')}")
    print(f"  工廠地址    : {detail.get('address', '')}")
    print(f"  登記狀態    : {detail.get('status', '')}")
    print(f"  組織型態    : {detail.get('org_type', '')}")
    if detail.get("company_tax_id"):
        print(f"  公司統一編號: {detail['company_tax_id']}")
    if detail.get("industry_v11"):
        print(f"  產業類別    : {detail['industry_v11']}")
    if detail.get("main_product_v11"):
        print(f"  主要產品    : {detail['main_product_v11']}")

    # 反查公司資料
    if detail.get("company_tax_id"):
        company = query_company_by_taxid(detail["company_tax_id"])
        if company:
            print(f"\n【所屬公司基本資料】")
            print(f"  公司名稱    : {company.get('Company_Name', '')}")
            print(f"  資本總額    : {company.get('Capital_Stock_Amount', 0):,} 元")
            print(f"  公司所在地  : {company.get('Company_Location', '')}")
            print(f"  登記機關    : {company.get('Register_Organization_Desc', '')}")


# ─── 入口 ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:]).strip()
    else:
        print("=" * 60)
        print("  台灣公司查詢工具（商工登記 + 工廠登記 + 產業類別）")
        print("=" * 60)
        print("  支援查詢方式:")
        print("    1. 統一編號 (8碼數字，如: 22099131)")
        print("    2. 工廠登記編號 (8碼英數，如: 94T00001)")
        print("    3. 公司/工廠名稱 (如: 台灣積體電路)")
        print("  輸入 q 或 exit 離開")
        print("-" * 60)
        query = input("請輸入查詢內容: ").strip()

    if query.lower() in ("q", "exit", "quit", ""):
        return

    # 判斷查詢類型
    if re.match(r"^[A-Za-z0-9]{8}$", query) and not query.isdigit():
        # 8碼含英文字母 → 工廠登記編號
        query_by_factory_regi_id(query.upper())
    elif re.match(r"^\d{8}$", query):
        # 純8位數字 → 先試統一編號，若無結果再試工廠登記編號
        company = query_company_by_taxid(query)
        if company:
            query_by_taxid(query)
        else:
            print(f"\n'{query}' 不是有效的統一編號，改以工廠登記編號查詢...")
            query_by_factory_regi_id(query)
    else:
        # 文字 → 公司名稱搜尋
        query_by_name(query)

    print()

    # 互動模式：持續查詢
    if len(sys.argv) == 1:
        print("-" * 60)
        while True:
            query = input("繼續查詢（或輸入 q 離開）: ").strip()
            if query.lower() in ("q", "exit", "quit", ""):
                break
            if re.match(r"^\d{8}$", query):
                query_by_taxid(query)
            elif re.match(r"^[A-Za-z0-9]{8}$", query) and not query.isdigit():
                query_by_factory_regi_id(query.upper())
            else:
                query_by_name(query)
            print()


if __name__ == "__main__":
    main()
