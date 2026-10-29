# 架构 Architecture
Grok Bot → cloudflared (http2) → Caddy :18065 (Bearer) → Docker zhilian-mcp 127.0.0.1:18064 (/mcp, streamable HTTP, FastMCP)
→ 单个 headless Chromium (Playwright), 一个 BrowserContext，asyncio.Lock 串行，省内存 (mem_limit 700m)。

- 登录 Login: passport.zhaopin.com 扫码 → cookies (`at`, `rt` …) → /data/storage.json。
- 搜索 Search: 打开 `https://xiaoyuan.zhaopin.com/search/index?query=&city=&refcode=4430`，解析 `div.position-card`（职位名 `.position-card__job-name`，薪资 `.position-card__salary`，公司 `.position-card__company__name`，城市 `.position-card__company__work-city`，职位 id 来自 `data-sensors-exposure-option` 的 `jdno`）。实习名企 / 品牌专区（`.practice__item`、`.brand-zone__item`、`/company/`）不是职位，直接丢掉。DOM 没有实习卡片时，才在同一浏览器上下文 POST `cgate.zhaopin.com/positionbusiness/searchrecommend/searchPositions`（`S_SOU_POSITION_TYPE=4` 实习）。`isVerification`、空列表、公司卡片一律丢弃并回到 DOM。不再请求 `fe-api.zhaopin.com`。
- 详情 Detail: `https://xiaoyuan.zhaopin.com/job/<id>`。标题 `h1.job-banner__name`（去掉其中的薪资），薪资 `.job-banner__salary`，公司 `.company-info__name`，描述 `.job-info__desc`。页面是空壳时再试 `www.zhaopin.com/jobdetail/<id>.htm`。
- 投递 Apply: 点 `.job-banner__deliver__btn` / 「立即投递」「申请职位」。默认 dry-run，`confirm=True` 才真点。
- 未知/风险 Unknowns: `/search/index` 未登录会被 302 到 passport；cgate 在部分网络会返回验证页。真实 apply XHR、滑块仍可能出现。
