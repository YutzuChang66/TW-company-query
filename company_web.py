#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
台灣公司查詢網頁工具
執行後在瀏覽器開啟 http://localhost:5000
"""

import re, json, time, ssl, urllib.parse, urllib.request
from http.cookiejar import CookieJar

# serv.gcis.nat.gov.tw 的 SSL 憑證缺少 Subject Key Identifier，需略過驗證
ssl._create_default_https_context = ssl._create_unverified_context
_SSL_CTX = ssl._create_unverified_context()
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# ─── 共用工具 ─────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}

def _get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8")

def _post(url, data, opener, timeout=15):
    encoded = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=encoded,
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
    )
    with opener.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8")

def _strip(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()

def _fmt_date(d):
    if not d or len(d) < 7: return d or ""
    try:
        y, m, day = int(d[:3]) if len(d)==7 else int(d[:2]), d[-4:-2], d[-2:]
        if len(d)==7: y=int(d[:3]); m=d[3:5]; day=d[5:7]
        return f"民國{y}年{m}月{day}日"
    except: return d

# ─── GCIS 開放資料 API ────────────────────────────────────────────────────────

def get_company(tax_id):
    url = (f"https://data.gcis.nat.gov.tw/od/data/api/"
           f"5F64D864-61CB-4D0D-8AD9-492047CC1EA6"
           f"?$format=json&$filter=Business_Accounting_NO%20eq%20{tax_id}&$top=1")
    try:
        raw = _get(url)
        data = json.loads(raw) if raw.strip() else []
        return data[0] if data else None
    except Exception as e:
        import traceback; traceback.print_exc()
        return None

def get_subsidy_record(tax_id):
    """查詢經濟部產業競爭力輔導團 補助申請紀錄"""
    url = f"https://eii.nat.gov.tw/moeai-plus/api/v1/companies/{tax_id}"
    try:
        raw = _get(url)
        data = json.loads(raw)
        if data.get("data"):
            return {"has_record": True, "message": data["data"].get("message", "已有申請紀錄")}
        return {"has_record": False, "message": data.get("messages", {}).get("error", "尚無申請紀錄")}
    except Exception:
        return {"has_record": False, "message": "查詢失敗"}

def get_company_business(tax_id):
    """從 GCIS 應用三取得所營事業（行業代碼 + 行業別），排除 ZZ 雜項"""
    url = (f"https://data.gcis.nat.gov.tw/od/data/api/"
           f"236EE382-4942-41A9-BD03-CA0709025E7C"
           f"?$format=json&$filter=Business_Accounting_NO%20eq%20{tax_id}&$top=1")
    try:
        raw = _get(url)
        data = json.loads(raw) if raw.strip() else []
        if data and data[0].get("Cmp_Business"):
            return [b for b in data[0]["Cmp_Business"]
                    if b.get("Business_Item_Desc", "").strip()
                    and not b.get("Business_Item", "").startswith("ZZ")]
    except Exception:
        pass
    return []

def _strip_company_suffix(name):
    """去除公司名稱中的組織型態後綴，用於工廠名稱搜尋"""
    for suffix in ["股份有限公司", "有限公司", "股份有限", "有限", "股份"]:
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name

# ─── 工廠公示資料系統 ─────────────────────────────────────────────────────────

FSEARCH = "https://serv.gcis.nat.gov.tw/Fidbweb/factInfoListAction.do"
FDETAIL = "https://serv.gcis.nat.gov.tw/Fidbweb/factInfoAction.do"

def _opener_csrf():
    jar = CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=_SSL_CTX),
    )
    req = urllib.request.Request(f"{FSEARCH}?method=qryCount", headers=HEADERS)
    with opener.open(req, timeout=15) as r:
        html = r.read().decode("utf-8")
    m = re.search(r'name="csrfPreventionSalthidden".*?value="([^"]+)"', html, re.DOTALL)
    return opener, (m.group(1) if m else "")

def search_factories(name="", regi_id="", ban_no="", timeout=15):
    opener, csrf = _opener_csrf()
    if not csrf: return []
    try:
        html = _post(FSEARCH, {
            "csrfPreventionSalthidden": csrf, "method": "query",
            "regiID": regi_id, "estbID": "", "factName": name,
            "banNo": ban_no,
            "addrCityCode1": "JJ", "addrCityCode2": "JJ", "factAddr": "",
            "orgCode": "JJ", "statCode": "JJ",
            "cityCode1": "JJ", "cityCode2": "JJ", "tmp_profitem": "JJ", "ITEM": "",
        }, opener, timeout=timeout)
    except Exception:
        return []
    # 允許 <img>（CNS11643 特殊字元會被渲染成圖片），使用 DOTALL
    pat = re.compile(
        r'method=detail&estbid=([^&"]+)&agencyCode=([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL
    )

    def _link_text(raw):
        # 移除 CNS11643 圖片（保留周圍文字）
        raw = re.sub(r"<img[^>]*/?>", "", raw, flags=re.DOTALL)
        return _strip(raw)

    pairs, items = [], list(pat.finditer(html))
    i = 0
    while i < len(items) - 1:
        t1 = _link_text(items[i].group(3))
        t2 = _link_text(items[i+1].group(3))
        e1, e2 = items[i].group(1), items[i+1].group(1)
        if e1 == e2:
            regi = t1 if re.match(r"^[A-Z0-9]{6,}", t1) else t2
            fname = t2 if re.match(r"^[A-Z0-9]{6,}", t1) else t1
            pairs.append({"regi_id": regi, "name": fname,
                          "estbid": e1, "agency": items[i].group(2)})
            i += 2
        else: i += 1
    return pairs

def get_factory_detail(estbid, agency):
    url = f"{FDETAIL}?method=detail&estbid={estbid}&agencyCode={agency}"
    try: html = _get(url)
    except: return {}

    def td_after(label):
        m = re.search(re.escape(label) + r".*?</t[dh]>\s*<td[^>]*>(.*?)</td>",
                      html, re.DOTALL)
        return _strip(m.group(1)) if m else ""

    def next_td_after_kw(kw):
        idx = html.find(kw)
        if idx == -1: return ""
        row_end = html.find("</tr>", idx)
        if row_end == -1: return ""
        m = re.search(r"<td[^>]*>(.*?)</td>", html[row_end:], re.DOTALL)
        return _strip(m.group(1)) if m else ""

    return {
        "regi_id":        td_after("工廠登記編號"),
        "address":        td_after("工廠地址"),
        "company_tax_id": td_after("公司（營利事業）統一編號"),
        "org_type":       td_after("工廠組織型態"),
        "status":         td_after("工廠登記狀態"),
        "last_changed":   td_after("最後核准變更日期"),
        "industry_v11":   next_td_after_kw("第11版)"),
        "main_product_v11": next_td_after_kw("主要產品"),
    }

# ─── API 端點 ─────────────────────────────────────────────────────────────────

@app.route("/api/query")
def api_query():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "請輸入查詢內容"}), 400
    try:
        return _do_query(q)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"伺服器錯誤：{type(e).__name__}: {e}"}), 500

def _do_query(q):
    result = {"query": q, "company": None, "factories": []}

    # 判斷輸入類型
    is_tax_id   = bool(re.match(r"^\d{8}$", q))
    is_regi_id  = bool(re.match(r"^[A-Za-z0-9]{8}$", q) and not q.isdigit())

    if is_regi_id:
        # 工廠登記編號
        facs = search_factories(regi_id=q.upper())
        if facs:
            d = get_factory_detail(facs[0]["estbid"], facs[0]["agency"])
            facs[0].update(d)
            result["factories"] = [facs[0]]
            if d.get("company_tax_id"):
                result["company"] = get_company(d["company_tax_id"])
                result["business_items"] = get_company_business(d["company_tax_id"])
                result["subsidy"] = get_subsidy_record(d["company_tax_id"])
        result["query_type"] = "factory_regi_id"

    elif is_tax_id:
        # 統一編號
        company = get_company(q)
        if company:
            result["company"] = company
            result["query_type"] = "tax_id"
            # 同步取得所營事業（行業代碼/行業別）
            result["business_items"] = get_company_business(q)
            result["subsidy"] = get_subsidy_record(q)
            # 工廠搜尋：先用完整公司名，若無結果再用去掉組織型態的短名
            company_name = company.get("Company_Name", "")
            facs = search_factories(name=company_name)
            if not facs:
                short_name = _strip_company_suffix(company_name)
                if short_name and short_name != company_name:
                    facs = search_factories(name=short_name)
            for f in facs[:10]:
                d = get_factory_detail(f["estbid"], f["agency"])
                f.update(d)
                result["factories"].append(f)
                time.sleep(0.2)
        else:
            # 查無公司登記（可能是合作社、商業行號、有限合夥）
            return jsonify({"error": (
                f"找不到統一編號 '{q}' 的相關資料。\n"
                "可能為合作社、商業行號或有限合夥（非公司登記）。\n\n"
                "💡 建議改用公司或工廠名稱搜尋，\n"
                "或至 findbiz.nat.gov.tw 查詢完整資料。"
            )}), 404

    else:
        # 公司/工廠名稱
        result["query_type"] = "name"
        facs = search_factories(name=q)
        if not facs:
            return jsonify({"error": f"找不到名稱含「{q}」的工廠登記資料。\n若為非製造業，請改用統一編號查詢。"}), 404
        # 從第一筆取統一編號 → 查公司
        d0 = get_factory_detail(facs[0]["estbid"], facs[0]["agency"])
        facs[0].update(d0)
        if d0.get("company_tax_id"):
            result["company"] = get_company(d0["company_tax_id"])
            result["business_items"] = get_company_business(d0["company_tax_id"])
            result["subsidy"] = get_subsidy_record(d0["company_tax_id"])
        for f in facs[1:10]:
            d = get_factory_detail(f["estbid"], f["agency"])
            f.update(d)
            time.sleep(0.2)
        result["factories"] = facs[:10]

    return jsonify(result)

# ─── 前端頁面 ──────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>台灣公司查詢</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: "Microsoft JhengHei", Arial, sans-serif;
         background: #f0f4f8; color: #2d3748; min-height: 100vh; }
  header { background: linear-gradient(135deg, #1a56db, #0e9f6e);
           color: white; padding: 28px 20px; text-align: center; }
  header h1 { font-size: 1.8rem; font-weight: 700; margin-bottom: 6px; }
  header p  { font-size: 0.9rem; opacity: 0.85; }

  .search-box { max-width: 680px; margin: 28px auto; padding: 0 16px; }
  .search-row { display: flex; gap: 10px; }
  .search-row input {
    flex: 1; padding: 14px 18px; font-size: 1rem;
    border: 2px solid #cbd5e0; border-radius: 10px;
    outline: none; transition: border-color .2s;
  }
  .search-row input:focus { border-color: #1a56db; }
  .search-row button {
    padding: 14px 26px; background: #1a56db; color: white;
    border: none; border-radius: 10px; font-size: 1rem;
    cursor: pointer; transition: background .2s; white-space: nowrap;
  }
  .search-row button:hover { background: #1e429f; }
  .hint { font-size: 0.82rem; color: #718096; margin-top: 8px; text-align: center; }

  #result { max-width: 900px; margin: 0 auto 40px; padding: 0 16px; }

  .spinner { text-align: center; padding: 40px; color: #718096; font-size: 1.1rem; }

  .card {
    background: white; border-radius: 12px;
    box-shadow: 0 2px 8px rgba(0,0,0,.08); margin-bottom: 20px; overflow: hidden;
  }
  .card-header {
    padding: 14px 20px; font-weight: 700; font-size: 1rem;
    display: flex; align-items: center; gap: 8px;
  }
  .card-header.blue   { background: #ebf5ff; color: #1a56db; border-bottom: 2px solid #bfdbfe; }
  .card-header.green  { background: #ecfdf5; color: #065f46; border-bottom: 2px solid #a7f3d0; }
  .card-header.orange { background: #fff7ed; color: #9a3412; border-bottom: 2px solid #fed7aa; }
  .card-body { padding: 16px 20px; }

  .info-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }
  .info-item label { font-size: 0.78rem; color: #718096; display: block; margin-bottom: 3px; }
  .info-item span  { font-size: 0.95rem; font-weight: 500; word-break: break-all; }
  .info-item span.capital { color: #c53030; font-weight: 700; }
  .info-item span.status-ok  { color: #065f46; }
  .info-item span.status-bad { color: #9b1c1c; }

  .factory-list { display: flex; flex-direction: column; gap: 14px; }
  .factory-item {
    border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px 16px;
    background: #f8fafc;
  }
  .factory-item h4 { font-size: 0.95rem; color: #1a56db; margin-bottom: 10px; }
  .factory-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; }
  .factory-grid .info-item label { font-size: 0.75rem; }
  .factory-grid .info-item span  { font-size: 0.88rem; }
  .industry-badge {
    display: inline-block; background: #e0f2fe; color: #075985;
    padding: 3px 10px; border-radius: 20px; font-size: 0.8rem;
    margin: 3px 3px 0 0; font-weight: 500;
  }
  .product-badge {
    display: inline-block; background: #f0fdf4; color: #166534;
    padding: 3px 10px; border-radius: 20px; font-size: 0.8rem;
    margin: 3px 3px 0 0;
  }
  .biz-badge {
    display: inline-block; background: #fdf4ff; color: #6b21a8;
    padding: 4px 12px; border-radius: 20px; font-size: 0.82rem;
    border: 1px solid #e9d5ff; cursor: default;
  }
  .biz-list { list-style: none; margin: 0; padding: 0; columns: 2; column-gap: 20px; }
  .biz-list li { font-size: 0.88rem; padding: 3px 0; color: #374151; break-inside: avoid; }
  .biz-list li .biz-code { color: #6b21a8; font-weight: 600; margin-right: 6px; font-size: 0.82rem; }
  .subsidy-badge {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 10px 18px; border-radius: 8px; font-size: 0.95rem; font-weight: 600;
  }
  .subsidy-badge.has { background: #ecfdf5; color: #065f46; border: 1px solid #a7f3d0; }
  .subsidy-badge.none { background: #f9fafb; color: #6b7280; border: 1px solid #e5e7eb; }
  .card-header.purple { background: #faf5ff; color: #6b21a8; border-bottom: 2px solid #e9d5ff; }

  .more-note { text-align: center; color: #718096; font-size: 0.85rem;
               padding: 12px; border-top: 1px solid #e2e8f0; margin-top: 4px; }
  .error-box { background: #fff5f5; border: 1px solid #fed7d7; border-radius: 10px;
               padding: 20px; color: #c53030; text-align: center; white-space: pre-line; }

  @media (max-width: 500px) {
    header h1 { font-size: 1.4rem; }
    .search-row button { padding: 14px 16px; }
    .info-grid { grid-template-columns: 1fr 1fr; }
  }
</style>
</head>
<body>
<header>
  <h1>🏢 台灣公司查詢</h1>
  <p>商工登記公示資料 ／ 工廠登記 ／ 產業類別</p>
</header>

<div class="search-box">
  <div class="search-row">
    <input type="text" id="q" placeholder="輸入統一編號、工廠登記編號或公司名稱" autofocus>
    <button onclick="doSearch()">查詢</button>
  </div>
  <div class="hint">
    範例：22099131（統一編號）／ 93A00070（工廠編號）／ 鴻海精密（公司名稱）
  </div>
</div>

<div id="result"></div>

<script>
const q = document.getElementById('q');
q.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });

function doSearch() {
  const val = q.value.trim();
  if (!val) return;
  const div = document.getElementById('result');
  div.innerHTML = '<div class="spinner">⏳ 查詢中，請稍候…</div>';
  fetch('/api/query?q=' + encodeURIComponent(val))
    .then(r => r.json())
    .then(renderResult)
    .catch(e => { div.innerHTML = '<div class="error-box">❌ 網路錯誤，請確認程式仍在執行中</div>'; });
}

function fmt(v) { return v || '—'; }

function badges(text, cls) {
  if (!text) return '—';
  return text.split(/\\s+(?=\\d+\\s)/).map(s => s.trim()).filter(Boolean)
    .map(s => `<span class="${cls}">${s}</span>`).join('');
}

function renderBusinessItems(items) {
  if (!items || !items.length) return '';
  const rows = items.map(b => {
    const code = b.Business_Item || '';
    const desc = b.Business_Item_Desc || '';
    return `<li><span class="biz-code">${code}</span>${desc}</li>`;
  }).join('');
  return `<div style="margin-top:14px;border-top:1px solid #e2e8f0;padding-top:14px">
    <label style="font-size:.78rem;color:#718096;display:block;margin-bottom:8px">所營事業資料</label>
    <ul class="biz-list">${rows}</ul>
  </div>`;
}

function renderSubsidy(subsidy) {
  if (!subsidy) return '';
  const cls  = subsidy.has_record ? 'has' : 'none';
  const icon = subsidy.has_record ? '✅' : '—';
  return `
  <div class="card">
    <div class="card-header purple">📋 補助申請查詢（經濟部產業競爭力輔導團）</div>
    <div class="card-body" style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
      <span class="subsidy-badge ${cls}">${icon} ${subsidy.message}</span>
      <a href="https://eii.nat.gov.tw/moeai-plus/" target="_blank"
         style="font-size:.85rem;color:#1a56db;text-decoration:underline">前往查詢詳細申請記錄 →</a>
    </div>
  </div>`;
}

function renderResult(data) {
  if (data.error) {
    document.getElementById('result').innerHTML =
      `<div class="error-box">❌ ${data.error.replace('findbiz.nat.gov.tw', '<a href="https://findbiz.nat.gov.tw" target="_blank" style="color:#c53030">findbiz.nat.gov.tw</a>')}</div>`;
    return;
  }

  let html = '';

  // 公司基本資料
  const c = data.company;
  if (c) {
    const capital = parseInt(c.Capital_Stock_Amount || 0).toLocaleString();
    const paid    = parseInt(c.Paid_In_Capital_Amount || 0).toLocaleString();
    const statCls = c.Company_Status_Desc === '核准設立' ? 'status-ok' : 'status-bad';
    html += `
    <div class="card">
      <div class="card-header blue">🏛️ 公司基本資料</div>
      <div class="card-body">
        <div class="info-grid">
          <div class="info-item"><label>統一編號</label><span>${fmt(c.Business_Accounting_NO)}</span></div>
          <div class="info-item"><label>公司名稱</label><span>${fmt(c.Company_Name)}</span></div>
          <div class="info-item"><label>登記現況</label><span class="${statCls}">${fmt(c.Company_Status_Desc)}</span></div>
          <div class="info-item"><label>資本總額</label><span class="capital">NT$ ${capital}</span></div>
          <div class="info-item"><label>已繳資本額</label><span>NT$ ${paid}</span></div>
          <div class="info-item"><label>代表人</label><span>${fmt(c.Responsible_Name)}</span></div>
          <div class="info-item"><label>公司所在地</label><span>${fmt(c.Company_Location)}</span></div>
          <div class="info-item"><label>登記機關</label><span>${fmt(c.Register_Organization_Desc)}</span></div>
          <div class="info-item"><label>核准設立日期</label><span>${fmtDate(c.Company_Setup_Date)}</span></div>
          <div class="info-item"><label>最後變更日期</label><span>${fmtDate(c.Change_Of_Approval_Data)}</span></div>
        </div>
        ${renderBusinessItems(data.business_items)}
      </div>
    </div>`;
  }

  // 工廠資料
  const facs = data.factories || [];
  if (facs.length > 0) {
    const headerColor = c ? 'green' : 'orange';
    html += `
    <div class="card">
      <div class="card-header ${headerColor}">🏭 工廠登記資料（共 ${facs.length} 筆${facs.length===10?' · 僅顯示前10筆':''}）</div>
      <div class="card-body">
        <div class="factory-list">`;
    for (const f of facs) {
      const statCls2 = f.status === '生產中' ? 'status-ok' : 'status-bad';
      html += `
          <div class="factory-item">
            <h4>📌 ${fmt(f.regi_id)} &nbsp;·&nbsp; ${fmt(f.name)}</h4>
            <div class="factory-grid">
              <div class="info-item"><label>工廠地址</label><span>${fmt(f.address)}</span></div>
              <div class="info-item"><label>登記狀態</label><span class="${statCls2}">${fmt(f.status)}</span></div>
              <div class="info-item"><label>組織型態</label><span>${fmt(f.org_type)}</span></div>
              <div class="info-item"><label>最後變更</label><span>${fmt(f.last_changed)}</span></div>
            </div>`;
      if (f.industry_v11) {
        html += `<div style="margin-top:10px"><label style="font-size:.75rem;color:#718096;display:block;margin-bottom:4px">產業類別（第11版）</label>${badges(f.industry_v11,'industry-badge')}</div>`;
      }
      if (f.main_product_v11) {
        html += `<div style="margin-top:8px"><label style="font-size:.75rem;color:#718096;display:block;margin-bottom:4px">主要產品</label>${badges(f.main_product_v11,'product-badge')}</div>`;
      }
      html += `</div>`;
    }
    html += `</div></div></div>`;
  } else if (!c) {
    html += `<div class="error-box">⚠️ 此公司無工廠登記資料</div>`;
  } else {
    html += `
    <div class="card">
      <div class="card-header orange">🏭 工廠登記資料</div>
      <div class="card-body" style="color:#718096;text-align:center;padding:24px">
        此公司無工廠登記資料（非製造業）
      </div>
    </div>`;
  }

  html += renderSubsidy(data.subsidy);

  document.getElementById('result').innerHTML = html;
}

function fmtDate(d) {
  if (!d || d.length < 7) return d || '—';
  try {
    let y, m, day;
    if (d.length === 7) { y = d.slice(0,3); m = d.slice(3,5); day = d.slice(5,7); }
    else                { y = d.slice(0,2); m = d.slice(2,4); day = d.slice(4,6); }
    return `民國${parseInt(y)}年${m}月${day}日`;
  } catch { return d; }
}
</script>
</body>
</html>"""

@app.route("/")
def index():
    return render_template_string(HTML)

# ─── 啟動 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os, webbrowser, threading
    port = int(os.environ.get("PORT", 5000))
    if port == 5000:
        def open_browser():
            time.sleep(1.2)
            webbrowser.open(f"http://localhost:{port}")
        threading.Thread(target=open_browser, daemon=True).start()
        print("=" * 50)
        print("  台灣公司查詢網頁已啟動！")
        print(f"  請在瀏覽器開啟: http://localhost:{port}")
        print("  按 Ctrl+C 可停止程式")
        print("=" * 50)
    app.run(host="0.0.0.0", port=port, debug=False)
