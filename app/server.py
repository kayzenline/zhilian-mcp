"""Zhaopin campus (智联校园) MCP server — streamable HTTP at /mcp.

One headless Chromium, session persisted at /data/storage.json.
search_internships reads job cards from xiaoyuan.zhaopin.com, not company ads.
"""
import asyncio, base64, json, os
from mcp.server.fastmcp import FastMCP
from playwright.async_api import async_playwright

from parse import (
    CAMPUS_SEARCH_API,
    COMPANY_API_MARKERS,
    campus_job_url,
    campus_search_body,
    campus_search_url,
    choose_search_results,
    is_internship_job,
    job_id_from_text,
    parse_api_payload,
    parse_search_html,
    resolve_city,
    social_job_url,
)

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
    """Campus job page. Full URLs are opened as given."""
    text = (x or "").strip()
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return campus_job_url(text)

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

async def _read_captured(captured):
    reason = "empty"
    for payload in captured:
        found, why = parse_api_payload(payload)
        if found:
            return found, None
        reason = why or reason
    return [], reason


async def _campus_api_search(page, keywords, city, page_no):
    """POST cgate searchPositions with the browser cookie jar. No fe-api call."""
    body = campus_search_body(keywords, city, page_no)
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/plain, */*",
        "origin": "https://xiaoyuan.zhaopin.com",
        "referer": campus_search_url(keywords, city, page_no),
        "x-zp-business-system": "40",
        "x-zp-platform": "14",
    }
    try:
        response = await page.request.post(
            CAMPUS_SEARCH_API,
            data=json.dumps(body),
            headers=headers,
            timeout=15000,
        )
    except Exception:
        return [], "anti_bot"
    ctype = (response.headers.get("content-type") or "").lower()
    if "json" not in ctype:
        return [], "anti_bot"
    try:
        payload = await response.json()
    except Exception:
        return [], "anti_bot"
    jobs, reason = parse_api_payload(payload)
    if not jobs:
        return [], reason or "empty"
    return jobs, None


async def _settle(page, timeout: int = 8000) -> None:
    """Wait for the SPA to stop bouncing.

    The campus job page finishes domcontentloaded and *then* navigates (login bounce,
    city redirect, client-side router), which destroys the JS execution context and makes
    the next query_selector blow up with "Execution context was destroyed". Load state
    first, then let the caller retry whatever it wanted to read.
    """
    try:
        await page.wait_for_load_state("load", timeout=timeout)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        pass


async def _probe(page, sel: str, attempts: int = 3):
    """query_selector that survives a navigation in flight."""
    last = None
    for i in range(attempts):
        try:
            return await page.query_selector(sel)
        except Exception as e:  # context destroyed mid-navigation
            last = e
            await page.wait_for_timeout(800 * (i + 1))
            await _settle(page)
    if last:
        raise last
    return None


async def _open_job(page, job: str) -> str:
    """Open the campus job page. If it is an empty shell, try the social jobdetail URL."""
    target = job_url(job)
    await page.goto(target, wait_until="domcontentloaded")
    await _settle(page)
    empty = await _probe(page, ".page-empty, .page-error")
    title = await _probe(page, "h1.job-banner__name, .summary-plane__title, h1")
    if empty and not title and not str(job).startswith("http"):
        alt = social_job_url(str(job).strip())
        await page.goto(alt, wait_until="domcontentloaded")
        await _settle(page)
    return page.url


async def _job_fields(page) -> dict:
    last = None
    for i in range(3):
        try:
            return await _eval_job_fields(page)
        except Exception as e:  # context destroyed mid-navigation
            last = e
            await page.wait_for_timeout(800 * (i + 1))
            await _settle(page)
    raise last


async def _eval_job_fields(page) -> dict:
    return await page.evaluate("""() => {
      const text = (sel) => {
        const el = document.querySelector(sel);
        return el ? (el.innerText || "").trim() : "";
      };
      const nameEl = document.querySelector("h1.job-banner__name, .summary-plane__title, .job-summary h1, h1");
      let title = "";
      if (nameEl) {
        const clone = nameEl.cloneNode(true);
        clone.querySelectorAll(".job-banner__salary, .summary-plane__salary").forEach((n) => n.remove());
        title = (clone.innerText || "").trim();
      }
      const description = text(".job-info__desc, .describtion__detail-content, .job-detail, .describtion");
      return {
        title,
        salary: text(".job-banner__salary, .summary-plane__salary"),
        company: text(".company-info__name, .company__title"),
        city: text(".job-banner__desc-text"),
        info: text(".job-banner__desc, .summary-plane__info"),
        description,
      };
    }""")


@mcp.tool()
async def search_internships(keywords: str, city: str = "538", page: int = 1) -> dict:
    """Search 智联校园 internships and return job cards, not company cards.

    Each job has: job_id, title, company, city, salary (range when the card shows one),
    url (https://xiaoyuan.zhaopin.com/job/<id>), snippet (short why: summary or tags),
    work_type.

    city: Zhaopin city code (538=上海, 530=北京, 765=深圳, 763=广州, 653=杭州) or 中文名.
    page: 1-based.

    Opens the campus search page
    https://xiaoyuan.zhaopin.com/search/index?query=<keywords>&city=<code>&refcode=4430
    and reads div.position-card (title .position-card__job-name, salary
    .position-card__salary, company .position-card__company__name, city
    .position-card__company__work-city, id from data-sensors jdno).
    Company ads (.practice__item, .brand-zone__item, /company/ links) are ignored.

    If that DOM has no internship cards, falls through to
    POST https://cgate.zhaopin.com/positionbusiness/searchrecommend/searchPositions
    with S_SOU_POSITION_TYPE=4 (实习) inside the logged-in browser context.
    Empty, isVerification, or company-only API payloads are discarded and the DOM
    is used instead. fe-api.zhaopin.com is not called.
    """
    search_url = campus_search_url(keywords, resolve_city(city), page)
    async with _lock:
        p = await (await ctx()).new_page()
        pending = []

        def on_response(response):
            # Keyword search only. searchPositionsCompany is a company list and
            # searchRecommendCampus is the homepage feed, not this query.
            url = response.url or ""
            if "searchPositions" not in url or any(marker in url for marker in COMPANY_API_MARKERS):
                return
            pending.append(response)

        p.on("response", on_response)
        try:
            await p.goto(search_url, wait_until="domcontentloaded")
            try:
                await p.wait_for_selector(
                    ".position-card__job-name, .similar-position__item__name, .jobinfo__name",
                    timeout=8000,
                )
            except Exception:
                await p.wait_for_timeout(1500)
            html = await p.content()
            captured = []
            for response in pending:
                ctype = (response.headers.get("content-type") or "").lower()
                if "json" not in ctype:
                    captured.append("anti_bot")
                    continue
                try:
                    captured.append(await response.json())
                except Exception:
                    captured.append("anti_bot")
            dom_jobs = parse_search_html(html)
            api_jobs, api_reason = await _read_captured(captured)
            # DOM internship cards are enough. Call cgate only to fill a gap,
            # and ignore it when the payload is empty, blocked, or company-shaped.
            if not api_jobs and not any(is_internship_job(job) for job in dom_jobs):
                api_jobs, api_reason = await _campus_api_search(p, keywords, city, page)
            jobs, source, note = choose_search_results(dom_jobs, api_jobs, api_reason, int(page or 1))
            login_wall = "passport.zhaopin.com" in (p.url or "")
            if login_wall and source == "cgate":
                note = "search_page_login_wall; used cgate job list"
            elif login_wall and source != "dom":
                note = "search_page_login_wall; " + (note or "no_job_cards")
            return {
                "source": source,
                "count": len(jobs),
                "jobs": jobs,
                "page_url": p.url,
                "search_url": search_url,
                "note": note,
            }
        except Exception as exc:
            return {
                "source": "none",
                "count": 0,
                "jobs": [],
                "search_url": search_url,
                "error": repr(exc),
                "note": "search_failed",
            }
        finally:
            await p.close()

@mcp.tool()
async def get_job(job: str) -> dict:
    """Job detail by campus job_id (CC…J…) or full url. Reads the xiaoyuan job page."""
    async with _lock:
        p = await (await ctx()).new_page()
        try:
            url = await _open_job(p, job)
            fields = await _job_fields(p)
            description = fields.get("description") or ""
            return {
                "url": url,
                "job_id": job_id_from_text(str(job)) or job_id_from_text(url) or None,
                "title": fields.get("title") or None,
                "salary": fields.get("salary") or None,
                "company": fields.get("company") or None,
                "city": fields.get("city") or None,
                "info": fields.get("info") or None,
                "description": description or None,
                "snippet": (description[:180] if description else None),
            }
        except Exception as e:
            return {"ok": False, "error": repr(e), "job": str(job)}
        finally:
            await p.close()

@mcp.tool()
async def apply_job(job: str, confirm: bool = False) -> dict:
    """投递: requires login. confirm=True clicks 立即投递/申请职位; otherwise dry run."""
    async with _lock:
        p = await (await ctx()).new_page()
        try:
            url = await _open_job(p, job)
            btn = None
            for sel in [
                ".job-banner__deliver__btn",
                "a:has-text('申请职位')",
                "button:has-text('申请职位')",
                "text=立即投递",
                "text=投递简历",
                "text=申请职位",
                ".summary-plane__action a",
            ]:
                btn = await p.query_selector(sel)
                if not btn:
                    continue
                class_name = (await btn.get_attribute("class")) or ""
                if "disabled" in class_name:
                    btn = None
                    continue
                break
            if not btn:
                txt = await p.inner_text("body")
                return {
                    "ok": False,
                    "reason": "apply button not found",
                    "already_applied": "已申请" in txt or "已投递" in txt,
                    "url": url,
                }
            label = (await btn.inner_text()).strip()
            if not confirm:
                return {"ok": None, "dry_run": True, "button": label, "url": p.url}
            xhr = []
            p.on(
                "response",
                lambda r: xhr.append({"url": r.url, "status": r.status})
                if any(k in r.url.lower() for k in ("apply", "deliver"))
                else None,
            )
            async with p.context.expect_page(timeout=4000) as np:
                await btn.click()
            try:
                p2 = await np.value
                await p2.wait_for_timeout(2500)
                body = await p2.inner_text("body")
            except Exception:
                await p.wait_for_timeout(2500)
                body = await p.inner_text("body")
            ok = any(k in body for k in ["投递成功", "申请成功", "已申请", "已投递"])
            await save()
            return {"ok": ok, "button": label, "apply_xhr": xhr[:10], "snippet": body[:600]}
        except Exception as e:
            return {"ok": False, "error": repr(e)}
        finally:
            await p.close()

if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True); mcp.run(transport="streamable-http")
