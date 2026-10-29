"""Parser tests. No live login and no Playwright."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from parse import (  # noqa: E402
    campus_search_body,
    campus_search_url,
    choose_search_results,
    parse_api_payload,
    parse_search_html,
    resolve_city,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "campus_search_cards.html"


def _job(**kwargs):
    base = {
        "job_id": "CC111111111J40000000001",
        "title": "后端实习生",
        "company": "示例公司",
        "city": "上海",
        "salary": "150-200元/天",
        "url": "https://xiaoyuan.zhaopin.com/job/CC111111111J40000000001",
        "snippet": "实习",
        "work_type": "实习",
    }
    base.update(kwargs)
    return base


class SearchHtmlTests(unittest.TestCase):
    def test_fixture_returns_job_cards_not_company_ads(self):
        jobs = parse_search_html(FIXTURE.read_text(encoding="utf-8"))
        ids = [job["job_id"] for job in jobs]
        self.assertIn("CC137751860J40814270007", ids)
        self.assertIn("CC203188020J40990109804", ids)
        # Real homepage card is a job too, even though it is 全职.
        self.assertIn("CC842837920J40868364005", ids)
        titles = [job["title"] for job in jobs]
        for company in ("TCL实业控股股份", "国联证券", "中芯国际", "湖北省电力勘测设计院有限公司"):
            self.assertNotIn(company, titles)
        intern = next(job for job in jobs if job["job_id"] == "CC137751860J40814270007")
        self.assertEqual(intern["title"], "产品实习生（J14665）")
        self.assertEqual(intern["company"], "上海寰通商务科技有限公司")
        self.assertEqual(intern["city"], "上海")
        self.assertEqual(intern["salary"], "120-150元/天")
        self.assertEqual(intern["work_type"], "实习")
        self.assertEqual(intern["url"], "https://xiaoyuan.zhaopin.com/job/CC137751860J40814270007")
        self.assertIn("实习", intern["snippet"])

    def test_company_only_page_is_empty(self):
        html = """
        <li class="practice__item"><a href="https://xiaoyuan.zhaopin.com/company/CC000895521">国联证券</a></li>
        <div class="job-card"><a href="https://xiaoyuan.zhaopin.com/company/KA0227433110D90000125000">中芯国际</a></div>
        """
        self.assertEqual(parse_search_html(html), [])

    def test_choose_drops_fulltime_cards_when_an_internship_card_is_present(self):
        dom = [
            _job(title="销售", work_type="全职", snippet="全职", job_id="CC842837920J40868364005"),
            _job(title="产品实习生"),
        ]
        jobs, source, note = choose_search_results(dom, [], "empty", page=1)
        self.assertEqual(source, "dom")
        self.assertEqual([job["title"] for job in jobs], ["产品实习生"])
        self.assertEqual(note, "filtered_to_internship_or_campus")

    def test_choose_prefers_dom_internships_over_api(self):
        dom = [_job(title="DOM实习生")]
        api = [_job(job_id="CC222222222J40000000002", title="API实习生")]
        jobs, source, note = choose_search_results(dom, api, None, page=1)
        self.assertEqual(source, "dom")
        self.assertEqual(jobs[0]["title"], "DOM实习生")
        self.assertIsNone(note)

    def test_choose_uses_api_when_dom_has_no_internship_cards(self):
        dom = [_job(title="销售", work_type="全职", snippet="全职 · 学历不限", job_id="CC842837920J40868364005")]
        api = [_job()]
        jobs, source, _note = choose_search_results(dom, api, None, page=1)
        self.assertEqual(source, "cgate")
        self.assertEqual(jobs[0]["title"], "后端实习生")

    def test_choose_falls_back_to_dom_when_api_is_antibot(self):
        dom = [_job(title="销售", work_type="全职", snippet="全职", job_id="CC842837920J40868364005")]
        jobs, source, note = choose_search_results(dom, [], "anti_bot", page=1)
        self.assertEqual(source, "dom")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(note, "cgate_anti_bot_fell_back_to_dom")

    def test_choose_drops_company_cards_even_if_dom_empty(self):
        jobs, source, note = choose_search_results([], [], "company_cards", page=1)
        self.assertEqual(jobs, [])
        self.assertEqual(source, "none")
        self.assertEqual(note, "company_cards")

    def test_page_two_uses_api_job_list(self):
        dom = [_job(title="第一页")]
        api = [_job(job_id="CC333333333J40000000003", title="第二页实习生")]
        jobs, source, _note = choose_search_results(dom, api, None, page=2)
        self.assertEqual(source, "cgate")
        self.assertEqual(jobs[0]["title"], "第二页实习生")


class ApiPayloadTests(unittest.TestCase):
    def test_position_payload(self):
        payload = {
            "statusCode": 200,
            "statusDescription": "成功",
            "data": {
                "isVerification": 0,
                "list": [{
                    "name": "技术开发实习生（RPA）",
                    "number": "CC137751860J40814270007",
                    "companyName": "上海寰通商务科技有限公司",
                    "workCity": "上海",
                    "cityDistrict": "浦东",
                    "salary60": "120-150元/天",
                    "salaryCount": "",
                    "workType": "实习",
                    "education": "本科",
                    "jobSummary": "负责 RPA 流程开发，参与需求梳理。",
                    "cardType": 1,
                }],
            },
        }
        jobs, reason = parse_api_payload(payload)
        self.assertIsNone(reason)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job["title"], "技术开发实习生（RPA）")
        self.assertEqual(job["company"], "上海寰通商务科技有限公司")
        self.assertEqual(job["city"], "上海·浦东")
        self.assertEqual(job["salary"], "120-150元/天")
        self.assertEqual(job["url"], "https://xiaoyuan.zhaopin.com/job/CC137751860J40814270007")
        self.assertIn("RPA", job["snippet"])

    def test_verification_is_anti_bot(self):
        jobs, reason = parse_api_payload({"statusCode": 200, "data": {"isVerification": 1, "list": []}})
        self.assertEqual(jobs, [])
        self.assertEqual(reason, "anti_bot")

    def test_html_challenge_is_anti_bot(self):
        jobs, reason = parse_api_payload("<html><title>Security Verification</title></html>")
        self.assertEqual((jobs, reason), ([], "anti_bot"))

    def test_company_shaped_rows_are_rejected(self):
        payload = {
            "statusCode": 200,
            "data": {"isVerification": 0, "list": [
                {"name": "TCL实业控股股份", "companyName": "TCL实业控股股份", "number": "CC000858713D90000000000"},
                {"name": "国联证券", "companyName": "国联证券", "number": "CZ000895521"},
            ]},
        }
        jobs, reason = parse_api_payload(payload)
        self.assertEqual(jobs, [])
        self.assertEqual(reason, "company_cards")

    def test_failed_status_is_anti_bot(self):
        jobs, reason = parse_api_payload({"statusCode": 210, "data": None, "statusDescription": "操作失败"})
        self.assertEqual((jobs, reason), ([], "anti_bot"))


class RepoGuardTests(unittest.TestCase):
    def test_search_path_targets_campus_job_cards(self):
        root = Path(__file__).resolve().parents[1]
        server = (root / "app" / "server.py").read_text(encoding="utf-8")
        self.assertNotIn("https://fe-api.zhaopin.com", server)
        self.assertNotIn("sou.zhaopin.com", server)
        self.assertIn("position-card__job-name", server)
        self.assertIn("searchPositions", server)
        ecs = (root / "Dockerfile.ecs").read_text(encoding="utf-8")
        self.assertIn("FROM shixiseng-mcp:latest", ecs)


class UrlTests(unittest.TestCase):
    def test_campus_search_url_uses_query_and_city(self):
        url = campus_search_url("python 实习", "上海", 1)
        self.assertTrue(url.startswith("https://xiaoyuan.zhaopin.com/search/index?"))
        self.assertIn("query=python", url)
        self.assertIn("city=538", url)
        self.assertIn("refcode=4430", url)
        self.assertNotIn("page=", url)
        paged = campus_search_url("python", "530", 2)
        self.assertIn("city=530", paged)
        self.assertIn("page=2", paged)
        self.assertIn("pageIndex=2", paged)

    def test_city_names(self):
        self.assertEqual(resolve_city("深圳"), "765")
        self.assertEqual(resolve_city("538"), "538")
        self.assertEqual(resolve_city("全国"), "")

    def test_search_body_requests_internships(self):
        body = campus_search_body("数据", "杭州", 3)
        self.assertEqual(body["S_SOU_FULL_INDEX"], "数据")
        self.assertEqual(body["S_SOU_WORK_CITY"], "653")
        self.assertEqual(body["S_SOU_POSITION_TYPE"], "4")
        self.assertEqual(body["pageIndex"], 3)
        self.assertEqual(body["channel"], "xiaoyuan")
        self.assertNotIn("at", body)
        self.assertNotIn("rt", body)
        json.dumps(body)


if __name__ == "__main__":
    unittest.main()
