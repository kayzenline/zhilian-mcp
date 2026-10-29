"""Zhaopin (智联招聘) MCP server — streamable HTTP at /mcp. One headless Chromium, persisted storage state."""
import asyncio, base64, json, os, re, urllib.parse
from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright

DATA = os.environ.get("DATA_DIR", "/data")
STATE = f"{DATA}/storage.json"; QR = f"{DATA}/qr.png"
PORT = int(os.environ.get("PORT", "18064"))
mcp = FastMCP("zhilian", host="0.0.0.0", port=PORT, streamable_http_path="/mcp")
_lock = asyncio.Lock(); _pw = _browser = _ctx = None; _login_page = None

async def ctx():
    global _pw, _browser, _ctx
    if _ctx: return _ctx
    _pw = await async_playwright().start()
    _browser = await _pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu","--js-flags=--max-old-space-size=256"])
    _ctx = await _browser.new_context(storage_state=STATE if os.path.exists(STATE) else None,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
        locale="zh-CN", viewport={"width":1366,"height":900})
    return _ctx

async def save():
    await (await ctx()).storage_state(path=STATE)

def job_url(x: str) -> str:
    return x if x.startswith("http") else f"https://www.zhaopin.com/jobdetail/{x}.htm"

@mcp.tool()
async def login_qr() -> dict:
    """Open passport.zhaopin.com, switch to WeChat/app QR, save screenshot to /data/qr.png. Scan it, then call login_status."""
    global _login_page
    async with _lock:
        c = await ctx()
        if _login_page:
            try: await _login_page.close()
            except Exception: pass
            _login_page = None
        _login_page = await c.new_page()
        await _login_page.goto("https://passport.zhaopin.com/login", wait_until="networkidle")
        await _login_page.wait_for_timeout(1500)
        clicked = None
        # Corner QR switch on the SMS login card (usually top-left/right of white panel)
        for sel in [
            ".zppp-panel-qrcode-bar__img", ".zppp-panel-qrcode-bar",
            "[class*=\"qrcode-bar\"]", "[class*=\"qrcode-switch\"]",
            "[class*=\"QrcodeBar\"]", ".login-code-btn",
            "div[class*=\"code-btn\"]", "img[alt*=\"二维码\"]",
            "text=扫码登录", "text=微信登录",
        ]:
            try:
                loc = _login_page.locator(sel).first
                if await loc.count() == 0: continue
                await loc.click(timeout=2000); clicked = sel; break
            except Exception: pass
        if not clicked:
            # Click near top-right of the login white card (common QR toggle placement)
            try:
                box = await _login_page.locator(".zppp-panel, .login-register, [class*=\"zppp\"]").first.bounding_box()
                if box:
                    await _login_page.mouse.click(box["x"] + box["width"] - 24, box["y"] + 24)
                    clicked = "panel-top-right"
                    await _login_page.wait_for_timeout(500)
                    # also try top-left
                    if not await _login_page.query_selector("img[src*=\"qrcode\"], canvas, .zppp-panel-qrcode-img"):
                        await _login_page.mouse.click(box["x"] + 24, box["y"] + 24)
                        clicked = "panel-top-left"
            except Exception: pass
        await _login_page.wait_for_timeout(2500)
        el = None
        for sel in [
            ".zppp-panel-qrcode-img", "img[src*=\"qrcode\"]", "img[src*=\"QR\"]",
            "canvas", "iframe[src*=\"weixin\"]", ".zppp-panel-qrcode",
            "[class*=\"qrcode\"] img", "[class*=\"Qrcode\"] img",
        ]:
            el = await _login_page.query_selector(sel)
            if el: break
        await (el or _login_page).screenshot(path=QR)
        # also dump full page for debug
        await _login_page.screenshot(path=f"{DATA}/login_full.png", full_page=False)
        return {
            "qr_path": QR,
            "clicked": clicked,
            "has_element": bool(el),
            "qr_png_base64": base64.b64encode(open(QR,"rb").read()).decode(),
            "next": "scan then call login_status",
        }

@mcp.tool()
async def login_status() -> dict:
    """Check if session is logged in (cookie at_/zp_token/passport); persists /data/storage.json when logged in."""
    async with _lock:
        c = await ctx(); ck = {k["name"] for k in await c.cookies()}
        ok = bool(ck & {"at","rt","zp_passport_deepknow_sessionId","zp-route-meta"}) and "at" in ck
        if ok: await save()
        return {"logged_in": ok, "cookie_names": sorted(ck)[:40]}

async def _api_search(page, kw, city, pg):
    # fe-api (needs login cookies; anti-bot without). Response: data.list[] / data.results[]
    u = "https://fe-api.zhaopin.com/c/i/search/positions?" + urllib.parse.urlencode({"S_SOU_FULL_INDEX":kw,"S_SOU_WORK_CITY":city,"pageIndex":pg,"pageSize":30,"S_SOU_POSITION_SOURCE_TYPE":"4"})
    r = await page.request.get(u)
    if r.ok:
        j = await r.json(); d = (j.get("data") or {})
        lst = d.get("list") or d.get("results") or []
        if lst: return [{"job_id":x.get("number") or x.get("jobNumber"),"title":x.get("name") or x.get("jobName"),
            "company":(x.get("companyName") or (x.get("company") or {}).get("name")),"salary":x.get("salary60") or x.get("salary"),
            "city":x.get("workCity") or (x.get("city") or {}).get("display"),"url":x.get("positionURL") or x.get("positionUrl")} for x in lst]
    return None

@mcp.tool()
async def search_internships(keywords: str, city: str = "538", page: int = 1) -> dict:
    """Search internships. city: zhaopin city code (538=上海,530=北京,765=深圳,763=广州,653=杭州) or name. Tries fe-api then DOM scrape of sou.zhaopin.com (实习 filter)."""
    async with _lock:
        p = await (await ctx()).new_page()
        try:
            res = await _api_search(p, keywords, city, page)
            src = "fe-api"
            if not res:
                src = "dom"
                await p.goto(f"https://sou.zhaopin.com/?jl={city}&kw={urllib.parse.quote(keywords+' 实习')}&p={page}&et=4", wait_until="domcontentloaded")
                await p.wait_for_timeout(2500)
                res = await p.eval_on_selector_all(".joblist-box__item, .positionlist__list-item, .job-card", """els=>els.map(e=>{
                  const a=e.querySelector('a[href*="jobdetail"]')||e.querySelector('a');const t=s=>(e.querySelector(s)||{}).innerText||'';
                  const h=a?a.href:'';const m=h.match(/jobdetail\\/([^.?]+)/);
                  return {job_id:m?m[1]:null,title:t('.jobinfo__name, .iteminfo__line1__jobname, .job-name')||(a&&a.innerText),
                   company:t('.companyinfo__name, .iteminfo__line1__compname, .company-name'),salary:t('.jobinfo__salary, .iteminfo__line2__jobdesc__salary'),url:h}})""")
            return {"source": src, "count": len(res), "jobs": res, "page_url": p.url}
        finally: await p.close()

@mcp.tool()
async def get_job(job: str) -> dict:
    """Job detail by job_id (e.g. CC123...J40...) or full url."""
    async with _lock:
        p = await (await ctx()).new_page()
        try:
            await p.goto(job_url(job), wait_until="domcontentloaded"); await p.wait_for_timeout(1500)
            async def t(sel):
                el = await p.query_selector(sel); return (await el.inner_text()).strip() if el else None
            return {"url": p.url, "title": await t(".summary-plane__title, .job-summary h1, h1"),
                    "salary": await t(".summary-plane__salary"), "company": await t(".company__title, .company-name"),
                    "info": await t(".summary-plane__info"), "description": await t(".describtion__detail-content, .job-detail, .describtion")}
        finally: await p.close()

@mcp.tool()
async def apply_job(job: str, confirm: bool = False) -> dict:
    """投递: requires login. confirm=True actually clicks 申请/投递 on the job page; otherwise dry run. Returns page text after click."""
    async with _lock:
        p = await (await ctx()).new_page()
        try:
            await p.goto(job_url(job), wait_until="domcontentloaded"); await p.wait_for_timeout(1500)
            btn = None
            for sel in ["a:has-text('申请职位')","button:has-text('申请职位')","text=立即投递","text=投递简历","text=申请职位",".summary-plane__action a"]:
                btn = await p.query_selector(sel)
                if btn: break
            if not btn:
                txt = await p.inner_text("body")
                return {"ok": False, "reason": "apply button not found", "already_applied": "已申请" in txt or "已投递" in txt, "url": p.url}
            label = (await btn.inner_text()).strip()
            if not confirm: return {"ok": None, "dry_run": True, "button": label, "url": p.url}
            xhr = []
            p.on("response", lambda r: xhr.append({"url": r.url, "status": r.status}) if "apply" in r.url.lower() or "deliver" in r.url.lower() else None)
            async with p.context.expect_page(timeout=4000) as np:
                await btn.click()
            try: p2 = await np.value; await p2.wait_for_timeout(2500); body = await p2.inner_text("body")
            except Exception: await p.wait_for_timeout(2500); body = await p.inner_text("body")
            ok = any(k in body for k in ["投递成功","申请成功","已申请","已投递"])
            await save()
            return {"ok": ok, "button": label, "apply_xhr": xhr[:10], "snippet": body[:600]}
        except Exception as e:
            return {"ok": False, "error": repr(e)}
        finally: await p.close()

if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True); mcp.run(transport="streamable-http")
