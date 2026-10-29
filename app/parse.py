"""Pure parsers for 智联校园 internship cards.

The campus site (xiaoyuan.zhaopin.com) renders job cards as ``div.position-card``.
Company ads (实习名企 / 品牌专区) are separate blocks and must not be returned as jobs.
``fe-api.zhaopin.com`` is intentionally not parsed: it is often an anti-bot page, and
older selectors treated company links as jobs.
"""
import json
import re
import urllib.parse
from html.parser import HTMLParser

CAMPUS_ORIGIN = "https://xiaoyuan.zhaopin.com"
# Campus PC search route from the site router: /search/[data] with data=index.
CAMPUS_SEARCH_URL = CAMPUS_ORIGIN + "/search/index"
# Internship filter on cgate searchPositions. Verified to return workType=实习.
# This is S_SOU_POSITION_TYPE, not the older S_SOU_POSITION_SOURCE_TYPE.
INTERNSHIP_POSITION_TYPE = "4"
CAMPUS_SEARCH_API = "https://cgate.zhaopin.com/positionbusiness/searchrecommend/searchPositions"
# Company endpoints. Responses from these are never job listings.
COMPANY_API_MARKERS = ("searchPositionsCompany", "searchQueryCompany", "getSearchQueryCompany")

# City codes documented on the public campus/social search URLs.
CITY_CODES = {
    "北京": "530",
    "上海": "538",
    "广州": "763",
    "深圳": "765",
    "杭州": "653",
    "成都": "801",
    "南京": "635",
    "天津": "531",
    "武汉": "736",
    "西安": "854",
    "全国": "",
    "不限": "",
}

# CC203188020J40990109804 and CCL1358866250J41013534202. Company ids use D/CZ and no J.
JOB_ID_RE = re.compile(r"(CC[A-Z]?\d+J\d+)", re.I)
JOB_HREF_RE = re.compile(r"(?:/job/|jobdetail/)(CC[A-Z]?\d+J\d+)", re.I)
SNIPPET_LIMIT = 180

# Roots that are job cards. Company ad roots are skipped entirely.
_JOB_ROOTS = {"position-card", "similar-position__item", "joblist-box__item"}
_SKIP_ROOTS = {"practice__item", "brand-zone__item", "company-card"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_FIELD_CLASS = {
    "position-card__job-name": "title",
    "similar-position__item__name": "title",
    "jobinfo__name": "title",
    "iteminfo__line1__jobname__name": "title",
    "position-card__salary": "salary",
    "similar-position__item__salary": "salary",
    "jobinfo__salary": "salary",
    "position-card__company__name": "company",
    "similar-position__item__company-name": "company",
    "companyinfo__name": "company",
    "iteminfo__line1__compname": "company",
    "position-card__company__work-city": "city",
    "similar-position__item__location": "city",
}


def resolve_city(city: str) -> str:
    """Return a Zhaopin city code. Chinese names map to codes; digits pass through."""
    text = (city or "").strip()
    if not text:
        return ""
    if text in CITY_CODES:
        return CITY_CODES[text]
    if text.isdigit():
        return text
    return text


def campus_search_url(keywords: str, city: str, page: int = 1) -> str:
    """Campus search page. ``query`` + ``city`` match the site's own router.

    ``refcode=4430`` is the search page code in the campus bundle.
    ``page`` / ``pageIndex`` are included from page 2 so a logged-in SPA that
    reads them can leave the first screen; the cgate body uses the same index.
    """
    params = {"query": keywords or "", "refcode": "4430"}
    code = resolve_city(city)
    if code:
        params["city"] = code
    if page and int(page) > 1:
        params["page"] = str(int(page))
        params["pageIndex"] = str(int(page))
    return CAMPUS_SEARCH_URL + "?" + urllib.parse.urlencode(params)


def campus_job_url(job_id: str) -> str:
    return f"{CAMPUS_ORIGIN}/job/{job_id}"


def social_job_url(job_id: str) -> str:
    return f"https://www.zhaopin.com/jobdetail/{job_id}.htm"


def is_job_id(value: str) -> bool:
    return bool(value) and bool(JOB_ID_RE.fullmatch(value.strip()))


def job_id_from_text(value: str) -> str:
    if not value:
        return ""
    match = JOB_ID_RE.search(value)
    return match.group(1) if match else ""


def _loads_sensors(raw: str) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if isinstance(data, dict) and isinstance(data.get("properties"), dict):
        return data["properties"]
    return data if isinstance(data, dict) else {}


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()


def _snippet(parts) -> str:
    text = _clean(" ".join(p for p in parts if p))
    if len(text) > SNIPPET_LIMIT:
        return text[: SNIPPET_LIMIT - 1].rstrip() + "…"
    return text


def _classes(raw: str) -> list:
    return [c for c in (raw or "").split() if c]


class _CardParser(HTMLParser):
    """Collect campus position cards and ignore company-ad blocks."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = []
        self._skip = 0
        self._card = None
        self._capture = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, void=tag.lower() in _VOID)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, void=True)

    def _start(self, tag, attrs, void=False):
        if self._skip:
            if not void:
                self._skip += 1
            return
        attr = {k: (v or "") for k, v in attrs}
        classes = _classes(attr.get("class"))
        if any(c in _SKIP_ROOTS or c.startswith("brand-zone__item") for c in classes):
            if not void:
                self._skip = 1
            return
        href = attr.get("href") or ""
        if self._card is None and any(c in _JOB_ROOTS for c in classes):
            sensors = _loads_sensors(attr.get("data-sensors-exposure-option") or "")
            self._card = {
                "depth": 0,
                "job_id": job_id_from_text(str(sensors.get("jdno") or "")),
                "work_type": _clean(str(sensors.get("jobemploy_type") or "")),
                "title": "",
                "company": "",
                "city": "",
                "salary": "",
                "tags": [],
                "url": "",
            }
        if self._card is not None and not void:
            self._card["depth"] += 1
            field = next((name for cls, name in _FIELD_CLASS.items() if cls in classes), None)
            if field:
                self._capture = field
                self._buf = []
            elif "position-card__tags__item" in classes or "jobinfo__other-info-item" in classes:
                self._capture = "tag"
                self._buf = []
            found = job_id_from_text(href)
            if found:
                self._card["job_id"] = found
                self._card["url"] = href if href.startswith("http") else campus_job_url(found)

    def handle_data(self, data):
        if self._card is not None and self._capture and data:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if self._skip:
            self._skip -= 1
            return
        if self._card is None:
            return
        if self._capture:
            text = _clean("".join(self._buf))
            if text:
                if self._capture == "tag":
                    self._card["tags"].append(text)
                elif not self._card.get(self._capture):
                    self._card[self._capture] = text
            self._capture = None
            self._buf = []
        self._card["depth"] -= 1
        if self._card["depth"] <= 0:
            self._finish()

    def _finish(self):
        card = self._card
        self._card = None
        job = job_from_card(card)
        if job:
            self.cards.append(job)


def job_from_card(card: dict):
    """Turn one parsed card into the search contract, or None if it is not a job."""
    if not card:
        return None
    job_id = job_id_from_text(card.get("job_id") or "")
    title = _clean(card.get("title") or "")
    company = _clean(card.get("company") or "")
    if not is_job_id(job_id) or not title:
        return None
    # A company tile has no job id; if one slips through with the company name as the title
    # and no salary or tags, drop it.
    if title == company and not _clean(card.get("salary") or "") and not card.get("tags"):
        return None
    tags = [_clean(t) for t in card.get("tags") or [] if _clean(t)]
    work_type = _clean(card.get("work_type") or "")
    if not work_type:
        for label in ("实习", "校园", "全职", "兼职"):
            if label in tags or label in title:
                work_type = label
                break
    salary = _clean(card.get("salary") or "")
    return {
        "job_id": job_id,
        "title": title,
        "company": company,
        "city": _clean(card.get("city") or ""),
        "salary": salary,
        "url": campus_job_url(job_id),
        "snippet": _snippet(tags) or _snippet([work_type, salary, company]),
        "work_type": work_type,
    }


def parse_search_html(html: str) -> list:
    """Parse campus (and resilient social) search HTML into job cards only."""
    if not html:
        return []
    parser = _CardParser()
    parser.feed(html)
    parser.close()
    seen = set()
    jobs = []
    for job in parser.cards:
        if job["job_id"] in seen:
            continue
        seen.add(job["job_id"])
        jobs.append(job)
    return jobs


def _company_name(record: dict) -> str:
    company = record.get("companyName") or record.get("company") or ""
    if isinstance(company, dict):
        company = company.get("name") or ""
    return _clean(str(company or ""))


def _salary(record: dict) -> str:
    salary = _clean(str(record.get("salary60") or record.get("salary") or ""))
    count = _clean(str(record.get("salaryCount") or ""))
    if salary and count:
        return f"{salary}·{count}"
    return salary or count


def _city(record: dict) -> str:
    city = record.get("workCity") or record.get("city") or ""
    if isinstance(city, dict):
        city = city.get("display") or city.get("name") or ""
    city = _clean(str(city or ""))
    district = _clean(str(record.get("cityDistrict") or ""))
    if city and district and district not in city:
        return f"{city}·{district}"
    return city or district


def _api_snippet(record: dict, work_type: str, salary: str) -> str:
    for key in ("jobSummary", "jobHitReason", "aiPositionRecommendReason", "positionHighlight"):
        text = record.get(key)
        if isinstance(text, str) and _clean(text):
            return _snippet([text])
    bits = [work_type, record.get("education"), record.get("workingExp"), record.get("needMajor"), salary]
    labels = []
    for item in record.get("skillLabel") or []:
        if isinstance(item, dict) and item.get("value"):
            labels.append(str(item["value"]))
        elif isinstance(item, str):
            labels.append(item)
    return _snippet([str(b) for b in bits if b] + labels[:4])


def job_from_api_record(record: dict):
    """Normalize one cgate/fe-api record. Company-shaped rows return None."""
    if not isinstance(record, dict):
        return None
    # cardType 1 is a position card in the campus payload. Other types (company / ad)
    # are ignored when they also lack a real job id.
    job_id = job_id_from_text(str(record.get("number") or record.get("jobNumber") or ""))
    title = _clean(str(record.get("name") or record.get("jobName") or ""))
    company = _company_name(record)
    if not is_job_id(job_id) or not title:
        return None
    if title == company and not (record.get("salary60") or record.get("salary") or record.get("workType")):
        return None
    work_type = _clean(str(record.get("workType") or ""))
    salary = _salary(record)
    return {
        "job_id": job_id,
        "title": title,
        "company": company,
        "city": _city(record),
        "salary": salary,
        "url": campus_job_url(job_id),
        "snippet": _api_snippet(record, work_type, salary),
        "work_type": work_type,
    }


def parse_api_payload(payload):
    """Return ``(jobs, reason)``.

    ``reason`` is None when ``jobs`` is usable. Otherwise it is ``anti_bot``,
    ``empty``, or ``company_cards`` and the caller must fall back to the DOM.
    """
    if isinstance(payload, str):
        stripped = payload.lstrip().lower()
        if stripped.startswith("<") or "security verification" in stripped or "验证" in payload[:400]:
            return [], "anti_bot"
        return [], "anti_bot"
    if not isinstance(payload, dict):
        return [], "anti_bot"
    status = payload.get("statusCode", 200)
    try:
        status_ok = int(status) == 200
    except (TypeError, ValueError):
        status_ok = False
    data = payload.get("data") if "data" in payload or "statusCode" in payload else payload
    if not status_ok:
        return [], "anti_bot"
    if not isinstance(data, dict):
        return [], "empty"
    verification = data.get("isVerification", 0)
    try:
        flagged = int(verification) != 0
    except (TypeError, ValueError):
        flagged = bool(verification)
    if flagged:
        return [], "anti_bot"
    rows = data.get("list")
    if rows is None:
        rows = data.get("results")
    if not isinstance(rows, list) or not rows:
        return [], "empty"
    jobs = []
    seen = set()
    for row in rows:
        job = job_from_api_record(row)
        if not job or job["job_id"] in seen:
            continue
        seen.add(job["job_id"])
        jobs.append(job)
    if not jobs:
        return [], "company_cards"
    return jobs, None


def is_internship_job(job: dict) -> bool:
    blob = " ".join([
        job.get("work_type") or "",
        job.get("title") or "",
        job.get("snippet") or "",
    ])
    return "实习" in blob or "校园" in blob


def prefer_internship_jobs(jobs: list):
    """Keep 实习/校园 cards when the page mixed them with 全职. Otherwise keep jobs."""
    picked = [job for job in jobs if is_internship_job(job)]
    if picked and len(picked) < len(jobs):
        return picked, "filtered_to_internship_or_campus"
    if picked:
        return picked, None
    return list(jobs), "no_internship_label"


def choose_search_results(dom_jobs, api_jobs, api_reason, page: int = 1):
    """Pick a job list. DOM internship cards win on page 1; bad API payloads do not.

    ``api_reason`` is None when ``api_jobs`` came from a real position list.
    ``anti_bot``, ``empty``, and ``company_cards`` mean the API must not win
    over whatever job cards the DOM actually contained.
    """
    dom_jobs = list(dom_jobs or [])
    api_jobs = list(api_jobs or [])
    dom_intern = [job for job in dom_jobs if is_internship_job(job)]
    api_intern = [job for job in api_jobs if is_internship_job(job)]
    if int(page or 1) > 1 and api_intern:
        return api_intern, "cgate", None
    if dom_intern:
        note = None if len(dom_intern) == len(dom_jobs) else "filtered_to_internship_or_campus"
        return dom_intern, "dom", note
    if api_intern:
        return api_intern, "cgate", None
    if dom_jobs:
        note = "no_internship_label"
        if api_reason in ("anti_bot", "company_cards"):
            note = f"cgate_{api_reason}_fell_back_to_dom"
        return dom_jobs, "dom", note
    if api_jobs:
        return api_jobs, "cgate", "no_internship_label"
    return [], "none", api_reason or "no_job_cards"


def campus_search_body(keywords: str, city: str, page: int) -> dict:
    """JSON body for cgate searchPositions. No cookies or signed tokens."""
    body = {
        "S_SOU_FULL_INDEX": keywords or "",
        "pageIndex": max(int(page or 1), 1),
        "pageSize": 20,
        "identity": "1",
        "version": "8.2.6",
        "channel": "xiaoyuan",
        "platform": "14",
        "eventScenario": "PCCampusSearch",
        "S_SOU_POSITION_TYPE": INTERNSHIP_POSITION_TYPE,
    }
    code = resolve_city(city)
    if code:
        body["S_SOU_WORK_CITY"] = code
    return body
