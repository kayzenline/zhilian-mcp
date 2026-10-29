# 架构 Architecture
Grok Bot → cloudflared (http2) → Caddy :18065 (Bearer) → Docker zhilian-mcp 127.0.0.1:18064 (/mcp, streamable HTTP, FastMCP)
→ 单个 headless Chromium (Playwright), 一个 BrowserContext，asyncio.Lock 串行，省内存 (mem_limit 700m)。

- 登录 Login: passport.zhaopin.com 扫码 → cookies (`at`, `rt` …) → /data/storage.json。
- 搜索 Search: 先在已登录 context 里 `page.request` 调 fe-api.zhaopin.com/c/i/search/positions（未登录会被反爬）；失败则 DOM 抓 sou.zhaopin.com（选择器写了多套 fallback，需上线后校准）。
- 详情 Detail: www.zhaopin.com/jobdetail/<id>.htm DOM。
- 投递 Apply: Playwright 点「申请职位/立即投递」，监听含 apply/deliver 的 XHR 并返回，供后续逆向成纯 API；默认 dry-run，confirm=True 才真投。
- 未知/风险 Unknowns: 真实 apply XHR、选择器、xiaoyuan.zhaopin.com 校园站的独立接口；滑块/风控可能出现。
