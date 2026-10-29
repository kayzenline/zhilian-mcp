# Local development

`zhilian-mcp` is a FastMCP server. Streamable HTTP is at `/mcp`. Tools: `login_qr`, `login_status`, `search_internships`, `get_job`, `apply_job`.

The browser profile (cookies after WeChat QR login) lives in `storage.json` under the data dir. Do not commit that file, QR screenshots, or the Caddy bearer token.

## Run with Docker Compose

From the repo root:

```bash
mkdir -p data
ZHILIAN_DATA_DIR=./data ZHILIAN_DOCKERFILE=Dockerfile docker compose up --build
```

The server listens on `127.0.0.1:18064`. `ZHILIAN_DATA_DIR` defaults to `/opt/zhilian/data`, which is the ECS path. Set it to `./data` on a laptop so Compose does not need `/opt/zhilian`.

Compose builds `Dockerfile.ecs` by default, because that is what production runs and a default
`docker compose build` on the ECS box must not pull the 2GB official Playwright base. `Dockerfile`
is the full Playwright image (multi-platform, no local base image needed) — select it with
`ZHILIAN_DOCKERFILE=Dockerfile` on a laptop.

Parser checks do not need a browser:

```bash
python3 -m unittest discover -s tests -v
```

## Call `/mcp`

Initialize (note the `Accept` header; FastMCP speaks streamable HTTP):

```bash
curl -sD - -X POST localhost:18064/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"dev","version":"0"}}}'
```

Copy `Mcp-Session-Id` from the response headers into the next calls.

```bash
SID='paste-session-id'
curl -s -X POST localhost:18064/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

curl -s -X POST localhost:18064/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_internships","arguments":{"keywords":"python","city":"538","page":1}}}'
```

## Login QR

1. `tools/call` `login_qr`. The result includes `qr_png_base64` and writes `$ZHILIAN_DATA_DIR/qr.png`. The tool also saves a debug frame at `login_full.png`. Neither file belongs in git.
2. Scan with WeChat (智联校园招聘) or the 智联 app.
3. `tools/call` `login_status` until `logged_in` is true. Cookies (`at` and the rest) are written to `$ZHILIAN_DATA_DIR/storage.json` and reused on the next container start.

`/search/index` answers `302` to `passport.zhaopin.com` when that file is missing. Homepage job cards can render without a session; keyword search on the campus site expects one.

## `search_internships` contract

Arguments: `keywords` (string), `city` (default `538` 上海; also `530` 北京, `765` 深圳, `763` 广州, `653` 杭州, or those 中文名), `page` (1-based).

Result:

| field | meaning |
| --- | --- |
| `source` | `dom` (campus job cards), `cgate` (campus position API), or `none` |
| `count` / `jobs` | internship **job** cards only |
| `search_url` | page that was opened |
| `page_url` | final URL (passport means the search page asked for login) |
| `note` | why a source was skipped (`cgate_anti_bot_fell_back_to_dom`, `search_page_login_wall`, …) |

Each job: `job_id`, `title`, `company`, `city`, `salary` (empty string if the card has no range), `url` (`https://xiaoyuan.zhaopin.com/job/<id>`), `snippet` (summary or the tag line), `work_type`.

The page is `https://xiaoyuan.zhaopin.com/search/index?query=<keywords>&city=<code>&refcode=4430`. Cards are `div.position-card`:

- title: `.position-card__job-name`
- salary: `.position-card__salary`
- company: `.position-card__company__name`
- city: `.position-card__company__work-city`
- id: `jdno` inside `data-sensors-exposure-option` (the card is not an `<a>`)

`.practice__item`, `.brand-zone__item`, and links to `/company/` are company ads and are dropped. A row is a job only when the id looks like `CC…J…`.

If the DOM has no 实习/校园 card, the same browser context POSTs `https://cgate.zhaopin.com/positionbusiness/searchrecommend/searchPositions` with `S_SOU_POSITION_TYPE=4` (实习), `channel=xiaoyuan`, and `S_SOU_FULL_INDEX`. `isVerification`, a non-JSON/HTML challenge, an empty list, or a company-shaped list (`CC…D…` / `CZ…` with no job id) is discarded and the DOM cards are returned instead. `fe-api.zhaopin.com` is not called. `searchPositionsCompany` is not used.

`get_job` / `apply_job` open `https://xiaoyuan.zhaopin.com/job/<id>` (`h1.job-banner__name`, `.job-banner__salary`, `.company-info__name`, `.job-info__desc`, button `.job-banner__deliver__btn`). `apply_job` stays a dry run unless `confirm` is true.

## Manual smoke (needs a real scan)

Unit tests cover fixtures only. After `login_status` reports `logged_in`:

1. `search_internships` with `keywords=python`, `city=538`. Expect `source` `dom` or `cgate`, `count > 0`, every `url` containing `/job/`, and titles that are roles rather than 实习名企 names (TCL、国联证券, and similar ad tiles).
2. `get_job` with one returned `job_id`. Expect `title`, `company`, and a description.
3. `apply_job` with `confirm` false. Expect `dry_run: true` and a button label, or `already_applied` if the resume was sent before.

## How ECS deploy differs

Production on the Aliyun box uses `Dockerfile.ecs`, not `Dockerfile`:

```bash
cd /opt/zhilian/src
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple docker compose build   # builds Dockerfile.ecs
docker compose up -d --no-build
```

Raw equivalent:

```bash
docker build -f Dockerfile.ecs \
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple -t zhilian-mcp:latest .
```

`pypi.org` stalls from this host (a 38MB wheel made no progress in 15 minutes); the mirror flag takes
the build to about a minute. `Dockerfile.ecs` deliberately skips the `playwright==` pin: the base image
ships Playwright 1.47.0 with `chromium-1134`, and installing the 1.48.0 client makes every tool call
fail with `Executable doesn't exist at /ms-playwright/chromium-1140`.

`Dockerfile.ecs` is `FROM shixiseng-mcp:latest` so the 2GB box does not pull the full Playwright base again. Leave that file in place. Compose now defaults to `Dockerfile.ecs`, so a plain `docker compose build` on the box is safe; keep the data volume at `/opt/zhilian/data`.

Caddy (`deploy/Caddyfile.snippet`) binds `127.0.0.1:18065` and checks `Authorization: Bearer` against `ZHILIAN_TOKEN`. cloudflared (`deploy/cloudflared-zhilian.service`) publishes that port. The app container stays on `127.0.0.1:18064`. Generate the token on the server (`openssl rand -hex 32`); `deploy/zhilian.env.example` is only a placeholder. The rest of the host commands are in the README.
