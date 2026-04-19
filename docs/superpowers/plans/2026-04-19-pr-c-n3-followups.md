# PR C — N3 Follow-up 批次 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 spec §3 的 6 类 N3 follow-up 散点修复落地：AV daily-call counter 可观测性、DefiLlama schema drift 防御、HTF 三态降级契约、HTF MA 格式与 PR B 对齐、persona ETF 措辞动态化、M1-M3 minor 格式/事实修复、§3.6 key scrubber 审计复核。预计 ~80 行代码 + 12 个新增测试 + 1 个既有 HTF 测试更新 + 1 个 docs drift 修订。

**Architecture:** 本 PR 不涉及新模块或数据流改造，6 类修复按触点分布在 `alpha_vantage.py` / `onchain/service.py` + `onchain/models.py` / `tools_perception.py`（HTF + Stablecoin render）/ `persona.py` / `crypto_etf/service.py` + `tools_perception.py`（ETF footer）各局部。每项改动独立可测，按 6-7 commit 推进便于 review。First commit 同步回填 `docs/source-risk-matrix.md` 的 drift，避免文档漂移贯穿整个 PR 开发周期。

**Urgency note:** PR B (#18) 已合并，`format_for_llm` 短周期 MA 已切换到 `(price vs MA: +X%)` 新格式；本 PR Task 4 才把 HTF MA 对齐。合并前观察期 agent 会看到短周期/HTF 两种 MA 输出格式并存——本 PR 尽快合并可消除此 drift。

**Tech Stack:** Python 3.12、httpx、pandas、pytest + pytest-asyncio（项目既有依赖，零新增）。

**Spec:** `docs/superpowers/specs/2026-04-19-hardening-batch-design.md` §3 + §4 + §5.2 + §6.2

---

## Design Deviations from Spec

本 plan 完全遵循 spec §3 设计决策，但与 spec 的测试数统计有一处分歧（已与用户确认）：

| Area | Spec position | Plan position | Rationale |
|---|---|---|---|
| §3.6 API key scrubber 测试数（spec §6.2 + §5.2）| "FRED + AV 各新增一个 `test_http_error_does_not_leak_key`"（2 新增）| **无新增**，验证现有 2 测试已满足 spec §3.6 "测试 scope 声明" | `tests/test_macro_clients.py` 已有 `test_fred_5xx_error_does_not_leak_api_key`（`:114-128`）+ `test_av_5xx_error_does_not_leak_api_key`（`:328-342`），两者均 mock 5xx、用 SECRET-* 作 api_key 值、断言 `api_key not in str(exc_info.value)` — 完全覆盖 spec §3.6 定义的"stdlib logging + exception traceback 路径的 sanitization 回归保护"scope |
| §3.5 M1 测试数（spec §5.2）| "M1 latest 分支 = 1 test" | **2 tests**（`range_latest_when_zero_ago` + `range_singular_when_one_ago`）| spec §3.5 M1 描述覆盖两个独立的格式瑕疵（0 {unit} ago 歧义 + 单复数瑕疵），两个分支互不依赖，各测一个让失败时定位更清晰。合并成单一测试会增加 setup 复杂度 |

综合以上 2 条 deviation：最终测试数 **663**（spec §6.2 列的 664 高估 2 个 §3.6 测试但低估 1 个 M1 测试；净差 651 + 12 = 663）。

所有其他设计（§3.1 AV counter try/finally + flag、§3.2 DefiLlama first-occurrence + drift warning、§3.3 HTF 三态 "insufficient data" 措辞、§3.4 动态化 "past 7 days"、§3.5 M2/M3/M5、§3.6 audit 范围）均 100% 按 spec 实施。

**Spec 行号 drift 说明**（非设计偏离，是 spec 自身的 snapshot 过期）：spec `§1.4` / `§3` 多处行号是 PR B 合并前的快照，本 plan 已用 PR B 后（commit `4b38b82`）的正确行号。对照表：

| spec 引用 | plan 实际 | 文件 |
|-----------|-----------|------|
| HTF MA `:655` | `:642` | `tools_perception.py` |
| HTF `df.empty` `:625-632` | `:612-619` | `tools_perception.py` |
| M2 clamp `:820` | `:807` | `tools_perception.py` |
| M3 render `:914,920` | `:901,907` | `tools_perception.py` |

spec 未回填这些行号——属 known-stale 问题，不影响本 plan 的可执行性，也不需要本 PR 回填（spec 本身属于 PR #17，PR C 不动 spec 文档）。本提示仅供未来 reviewer 对照 spec 时参考。

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `docs/source-risk-matrix.md` | Modify | 回填 drift：DefiLlama 聚合方案 / M6 移除 / §4.x → §3.x 编号 |
| `src/integrations/macro/alpha_vantage.py` | Modify | §3.1 AV daily counter（`_daily_count` + `_daily_count_date` + `_warned_today` + try/finally `consumed_quota` flag）|
| `src/integrations/onchain/service.py` | Modify | §3.2 DefiLlama first-occurrence + 大小写 trim + drift WARN；§3.5 M3 pct = None when prev_week = 0 |
| `src/integrations/onchain/models.py` | Modify | §3.5 M3 `change_7d_pct` 与 `total_change_7d_pct` 类型：`float` → `float \| None` |
| `src/agent/tools_perception.py` | Modify | §3.3 HTF 三态 + MA 格式；§3.5 M1 latest/singular；§3.5 M2 ETF footer 从 service 结果推导；§3.5 M3 stablecoin render 处理 None |
| `src/agent/persona.py` | Modify | §3.4 ETF bullet 措辞动态化 |
| `src/integrations/crypto_etf/service.py` | Not changed | §3.5 M2 保留 service 层 clamp（权威），仅删工具层 clamp |
| `tests/test_macro_clients.py` | Modify | §3.1 AV counter 3 新测试 |
| `tests/test_onchain_service.py` | Modify | §3.2 DefiLlama 归一化 3 测试 + §3.5 M3 `prev_week=0` 服务层 1 测试 |
| `tests/test_perception_tools_n3.py` | Modify | §3.3 HTF 三态 1 新 + HTF MA 1 新 + 既有 outage 测试 update；§3.5 M1 latest 1 新；§3.5 M3 render 1 新 |

---

## Task 0: 基线与分支验证

**Files:** 无代码改动。

- [ ] **Step 1: 确认分支 + 干净工作树 + base 与 origin/main 同步**

Run:

```bash
git status
git rev-parse --abbrev-ref HEAD
git fetch origin
git log --oneline origin/main..HEAD
git log --oneline HEAD..origin/main
```

Expected:
- `On branch feat/pr-c-n3-followups` + clean working tree
- `git log origin/main..HEAD` 空输出（本分支 HEAD 未超前于 origin/main）
- `git log HEAD..origin/main` 空输出（origin/main 没有本分支未包含的 commit — 即分支真正基于最新 main）
- **两者同时空**才等于"完全同步"。若第一条非空，说明已有未推送 commit（不正常，刚切的分支不该有）；若第二条非空，说明分支基于旧 main，需 rebase 或重建

- [ ] **Step 2: 基线全量测试**

Run: `uv run pytest -q 2>&1 | tail -5`
Expected: **651 passed**（main 合并 PR B 后的水位）。

---

## Task 1: `source-risk-matrix.md` drift backfill（独立 docs commit）

**背景**：PR A（`docs/source-risk-matrix.md`）早于本 spec 定稿，三处 drift 已在 spec §9 标记：
- L488 / L512 / L537 仍描述 DefiLlama "多行同 symbol 聚合求和"——已改为 "first occurrence wins + drift 告警"（spec §3.2）
- L539 "M6 class-level throttle" ——本 spec 已移除 M6
- 多处使用 "PR C §4.x" 编号——实际 §3.x

PR C 第一个 commit 就回填，避免 drift 贯穿整个 PR 开发周期。

**Files:** Modify `docs/source-risk-matrix.md`

- [ ] **Step 1: 定位 3 处 DefiLlama "聚合求和" 描述**

Run: `grep -n '多行同 symbol\|求和\|M6\|§4\.' docs/source-risk-matrix.md`
Record each hit with line number and surrounding context.

- [ ] **Step 2: 对每个 DefiLlama "聚合求和" 描述做定向替换**

三处改动的通用模式：把"多行同 symbol 合并求和"替换为"first occurrence 胜出 + schema drift 告警"的描述。具体措辞按上下文调整，但要传达下列要点：

- DefiLlama 当前 top-level `circulating` 已是 across-every-chain 合计（见 `defillama.py:16-17`）
- 多行同 symbol 视为 schema drift 信号，log WARN，保留 first occurrence
- 避免"求和"会在当前 schema 下重复计数

一个模板（按原文段落实际语境调整）：

```
归一化（大小写 + whitespace trim）+ first-occurrence 胜出；多行同 symbol
触发 log WARN 作为 schema drift 信号（DefiLlama 当前 top-level
`circulating` 已是全链合计，求和会重复计数）。
```

- [ ] **Step 3: 删除 L539 M6 相关段落**

Run: `grep -n 'M6\|class-level throttle\|class-level.*throttle' docs/source-risk-matrix.md`
把与 "M6 class-level throttle" 相关的段落整体移除，并在附近加一行说明（若上下文需要承接）："AV throttle 保持 instance-level（见本 spec `§3.5 说明`）"。

- [ ] **Step 4: 替换 §4.x → §3.x 编号引用**

Run: `grep -n '§4\.\|PR C §4' docs/source-risk-matrix.md`
逐处检查：如果上下文提及的是 "hardening-batch-design.md §4.x"，改为 `§3.x`（这是本 spec 内 PR C 的正确段号）。如果指向其他文档的 §4.x，保留不动。

- [ ] **Step 5: 核查改动仅限 docs 层**

Run: `git diff --stat`
Expected: only `docs/source-risk-matrix.md` shows up. 没有意外的其他文件。

- [ ] **Step 6: Commit**

```bash
git add docs/source-risk-matrix.md
git commit -m "$(cat <<'EOF'
docs(risk-matrix): backfill PR C spec drift (DefiLlama / M6 / §4→§3)

- DefiLlama aggregation notes updated: "first occurrence wins + drift
  WARN" replaces the older "multi-row sum" approach (top-level
  circulating is already across-every-chain, sum would double-count).
- M6 class-level throttle section removed — spec dropped it; AV
  throttle stays instance-level.
- §4.x numbering references corrected to §3.x (this spec's PR C
  sections).

Spec: docs/superpowers/specs/2026-04-19-hardening-batch-design.md §9

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: §3.1 — AlphaVantage daily-call counter + 80% warning

**背景**：AV 免费档 25 req/day。当前无可观测性；观察期若透支，事后才能从 log 推。

**设计要点**（见 spec §3.1）：
- Instance-level `_daily_count`、`_daily_count_date`（UTC 日期字符串）、`_warned_today`（flag）
- 递增策略：`consumed_quota` flag + try/finally；任何 "请求已到达 AV 且 AV 已处理" 的路径都递增，HTTPStatusError（4xx/5xx，非 429）和网络错误不递增
- Warning 阈值 count >= 20（即 80%），同日内首次触发后 flag 置位，不重复；date 切换时 flag 随同 reset

**Files:**
- Modify: `src/integrations/macro/alpha_vantage.py`
- Test: `tests/test_macro_clients.py`

### 2a — 先写 3 个新测试（TDD red）

- [ ] **Step 1: 在 `tests/test_macro_clients.py` 末尾追加 3 个新测试**

定位文件末尾（约 `:379` 之后），追加：

```python


async def test_daily_count_increments_on_success(monkeypatch):
    """Each successful fetch_quote increments the daily counter."""
    import src.integrations.macro.alpha_vantage as av_module

    async def fake_sleep(duration: float) -> None:
        return None
    monkeypatch.setattr(av_module.asyncio, "sleep", fake_sleep)

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=AV_RESPONSE_SPY)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = av_module.AlphaVantageClient(http, api_key="k")
        assert client._daily_count == 0
        await client.fetch_quote("SPY")
        assert client._daily_count == 1
        await client.fetch_quote("QQQ")
        assert client._daily_count == 2


async def test_daily_count_warning_at_threshold_only_once(monkeypatch, caplog):
    """Warning fires at count>=20 the FIRST time only; repeats same day do not spam."""
    import logging
    import src.integrations.macro.alpha_vantage as av_module

    async def fake_sleep(duration: float) -> None:
        return None
    monkeypatch.setattr(av_module.asyncio, "sleep", fake_sleep)

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=AV_RESPONSE_SPY)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = av_module.AlphaVantageClient(http, api_key="k")
        # Fast-forward: simulate 19 prior calls having already consumed quota
        client._daily_count = 19
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=av_module.__name__):
            await client.fetch_quote("SPY")  # count goes 19 → 20 → warning
            warnings_after_first = [
                r for r in caplog.records if r.levelno == logging.WARNING
                and "daily budget" in r.getMessage()
            ]
            await client.fetch_quote("SPY")  # count → 21, no repeat
            await client.fetch_quote("SPY")  # count → 22, no repeat
            warnings_after_third = [
                r for r in caplog.records if r.levelno == logging.WARNING
                and "daily budget" in r.getMessage()
            ]
    assert len(warnings_after_first) == 1, (
        f"expected 1 budget warning at count=20, got {len(warnings_after_first)}"
    )
    assert len(warnings_after_third) == 1, (
        f"expected still 1 (no repeat); got {len(warnings_after_third)}"
    )
    # Spec §7.2 requires "(date %s UTC)" in the message for observation-period
    # log/alert correlation. Guard against format drift dropping the UTC label.
    assert "UTC" in warnings_after_first[0].getMessage(), (
        f"missing UTC marker in warning: {warnings_after_first[0].getMessage()!r}"
    )


async def test_daily_count_resets_on_new_date(monkeypatch):
    """When UTC date string changes, _daily_count and _warned_today both reset."""
    import src.integrations.macro.alpha_vantage as av_module

    async def fake_sleep(duration: float) -> None:
        return None
    monkeypatch.setattr(av_module.asyncio, "sleep", fake_sleep)

    # Mutable single-element holder lets the test control WHEN the date flips,
    # independent of HOW MANY TIMES _increment_daily_count reads it. Previous
    # iter-based approach enumerated exact call counts and would StopIteration
    # if the implementation added any extra _utc_date_str() lookup (e.g. in
    # a log format call) — that's implementation coupling, not behavior.
    current_date = ["2026-04-19"]
    monkeypatch.setattr(av_module, "_utc_date_str",
                        lambda: current_date[0])

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=AV_RESPONSE_SPY)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = av_module.AlphaVantageClient(http, api_key="k")
        assert client._daily_count_date == "2026-04-19"
        client._warned_today = True  # simulate prior same-day warning
        await client.fetch_quote("SPY")  # same day, count → 1
        assert client._daily_count == 1
        assert client._warned_today is True  # not reset
        await client.fetch_quote("SPY")  # same day, count → 2
        assert client._daily_count == 2

        # Flip the clock — NEXT fetch must observe a new day and reset.
        current_date[0] = "2026-04-20"
        await client.fetch_quote("SPY")
        # On the new day: counter resets first, THEN this call increments it
        assert client._daily_count == 1, (
            f"expected count=1 after date flip (reset + this call), got {client._daily_count}"
        )
        assert client._daily_count_date == "2026-04-20"
        assert client._warned_today is False, "warned flag should reset on date flip"
```

- [ ] **Step 2: 跑 3 个新测试，确认全部 fail（TDD red）**

Run: `uv run pytest tests/test_macro_clients.py -v -k "daily_count" 2>&1 | tail -20`
Expected: 3 FAILs with `AttributeError: 'AlphaVantageClient' object has no attribute '_daily_count'` 等。

### 2b — 实施 AV daily counter

- [ ] **Step 3a: 扩展 `alpha_vantage.py` 模块级 imports + logger**

定位 `src/integrations/macro/alpha_vantage.py:9-19` 的 imports 块。改动：
- `from datetime import datetime` → `from datetime import datetime, timezone`
- 在 stdlib 组内按字母序插入 `import logging`（放在 `import asyncio` 与 `import time` 之间），保持 isort 默认 stdlib → third-party 分组
- 在 imports 块结束后（`_AV_URL` 定义之前）追加 `logger = logging.getLogger(__name__)`

改后 imports 段应为：

```python
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from src.integrations.macro.models import EquityQuote
from src.utils.cache import RateLimitHit

_AV_URL = "https://www.alphavantage.co/query"
_NY = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)
```

这与 `onchain/service.py:4,13` 的既有模块级 import + logger 惯例一致。

- [ ] **Step 3b: 在模块级添加 UTC 日期工具函数**

定位 `logger = logging.getLogger(__name__)`（Step 3a 新加的那一行）之后。追加：

```python


def _utc_date_str() -> str:
    """UTC date string ("YYYY-MM-DD") used as the quota-window key.

    UTC is the default; observation-period validation tracks whether AV's
    actual reset clock matches UTC. If real reset happens in another zone
    with > 1h offset, this helper is the single switch to change.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
```

注意：使用的 `datetime`、`timezone` 已在 Step 3a 从顶部导入，**不再**使用函数内延迟 import。

- [ ] **Step 4: 在 `AlphaVantageClient` 顶部 + 构造器中加计数器字段 + budget 常量**

在 `_MIN_INTERVAL = 1.1` 之后（约 `:55`）追加：

```python
    _DAILY_BUDGET = 25              # AV free tier hard limit
    _WARN_THRESHOLD = 20            # 80% of 25 — observation heads-up
```

在 `__init__` 末尾（`self._last_fetch_at = 0.0` 之后，约 `:60`）追加：

```python
        self._daily_count: int = 0
        self._daily_count_date: str = _utc_date_str()
        self._warned_today: bool = False
```

- [ ] **Step 5: 添加内部方法 `_increment_daily_count`（date 切换 + warning 频控集中处理）**

在 `__init__` 方法之后（`fetch_quote` 之前）追加。使用 Step 3a 在模块级声明的 `logger`，不在函数体内做 `import logging`：

```python
    def _increment_daily_count(self) -> None:
        """Record one consumed AV quota unit. Resets on UTC date flip,
        emits a WARNING exactly once per day when the 80%-of-budget
        threshold is crossed. Reset is lazy (checked on every call rather
        than by a timer) — counter is best-effort infrastructure
        observability, process restarts also zero it."""
        today = _utc_date_str()
        if today != self._daily_count_date:
            self._daily_count = 0
            self._daily_count_date = today
            self._warned_today = False
        self._daily_count += 1
        if self._daily_count >= self._WARN_THRESHOLD and not self._warned_today:
            logger.warning(
                "AV daily budget at %d/%d (date %s UTC)",
                self._daily_count, self._DAILY_BUDGET, self._daily_count_date,
            )
            self._warned_today = True
```

- [ ] **Step 6: 重构 `fetch_quote` 使用 `consumed_quota` flag + try/finally 集中递增**

把 `fetch_quote`（现 `:62-118`）整体替换为：

```python
    async def fetch_quote(self, symbol: str) -> EquityQuote:
        elapsed = time.monotonic() - self._last_fetch_at
        if elapsed < self._MIN_INTERVAL:
            await asyncio.sleep(self._MIN_INTERVAL - elapsed)

        # consumed_quota: True when the request reached AV and AV processed it
        # (success, soft rate limit, hard 429, response shape error, or JSON
        # parse error on a 2xx). False for HTTP 4xx/5xx (industry assumption:
        # AV doesn't bill error responses) and for network errors / 429s
        # below. flag is consulted in `finally` so we increment exactly once
        # regardless of which branch raises.
        consumed_quota = False
        try:
            try:
                resp = await self._http.get(
                    _AV_URL,
                    params={
                        "function": "GLOBAL_QUOTE",
                        "symbol": symbol,
                        "apikey": self._api_key,
                    },
                )
            finally:
                # Advance the throttle clock on network failure too so
                # retries respect the 1 req/sec hard limit.
                self._last_fetch_at = time.monotonic()

            if resp.status_code == 429:
                # Hard 429: AV enforced quota — count as consumed.
                consumed_quota = True
                raise RateLimitHit(f"Alpha Vantage hard 429 for {symbol}")
            if resp.is_error:
                # 4xx/5xx other than 429: assumed non-billed; see spec §3.1.
                # Don't use raise_for_status — httpx's default HTTPStatusError
                # message includes the full request URL, which here contains the
                # apikey query param. `exc_info=True` in the service layer would
                # otherwise serialize the key into application logs.
                # NOTE (API key leakage boundary): `str(exc)` is sanitized, so
                # Python-stdlib traceback formatting is safe. `exc.request.url`
                # and `exc.response.request.url` still reference the original
                # URL with the apikey — if this project ever integrates Sentry /
                # Datadog / other APM that walks exception attributes, configure
                # their URL/query-string scrubber to redact `apikey=`.
                raise httpx.HTTPStatusError(
                    f"Alpha Vantage returned HTTP {resp.status_code} for {symbol}",
                    request=resp.request,
                    response=resp,
                ) from None

            # Past is_error: AV returned 2xx/3xx — quota consumed even if
            # downstream parsing fails. Set flag BEFORE resp.json() so that
            # a JSONDecodeError (AV occasionally returns 200 + non-JSON
            # error page) is still counted.
            consumed_quota = True
            data = resp.json()

            # AV signals soft rate limit via HTTP 200 body. Both 'Information'
            # (new) and 'Note' (legacy) are observed — check both.
            soft_msg = data.get("Information") or data.get("Note")
            if soft_msg:
                raise RateLimitHit(f"Alpha Vantage soft rate limit: {soft_msg}")

            quote = data.get("Global Quote")
            if not quote:
                raise ValueError(
                    f"Alpha Vantage returned unexpected shape: {list(data.keys())}"
                )

            return EquityQuote(
                symbol=quote.get("01. symbol", symbol),
                price=float(quote["05. price"]),
                change_pct=float(quote["10. change percent"].rstrip("%")),
                latest_trading_day=quote.get("07. latest trading day", ""),
            )
        finally:
            if consumed_quota:
                self._increment_daily_count()
```

- [ ] **Step 7: 跑 3 个新测试**

Run: `uv run pytest tests/test_macro_clients.py -v -k "daily_count"`
Expected: all 3 PASS。

- [ ] **Step 8: 跑整个 test_macro_clients.py（确保未破坏 FRED / AV 既有行为）**

Run: `uv run pytest tests/test_macro_clients.py -v 2>&1 | tail -30`
Expected: all PASS（既有 + 3 new）。

- [ ] **Step 9: Commit**

```bash
git add src/integrations/macro/alpha_vantage.py tests/test_macro_clients.py
git commit -m "$(cat <<'EOF'
feat(macro/av): daily-call counter + 80% budget warning (N3 follow-up)

- AlphaVantageClient gains instance-level _daily_count + _daily_count_date
  (UTC) + _warned_today flag. Counter increments in a try/finally using a
  consumed_quota flag, covering hard 429 / soft rate limit / response
  shape error / JSON parse error / success; HTTP 4xx/5xx and network
  errors do NOT increment (industry assumption: AV doesn't bill error
  responses, tracked as observation follow-up per spec §3.1).
- At count >= 20 (80% of 25/day), logs WARNING exactly once per day.
  Same-day repeats are throttled via _warned_today; date flip (UTC)
  resets both counter and flag lazily on the next call.
- Counter is best-effort observability — process restarts zero it;
  AV-side 429 enforcement is authoritative. Observation period will
  verify the UTC reset assumption (spec §7.2).

Spec: docs/superpowers/specs/2026-04-19-hardening-batch-design.md §3.1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: §3.2 — DefiLlama symbol normalization + schema drift warning

**背景**：当前 `src/integrations/onchain/service.py:49` 用字典推导 `{a.get("symbol"): a for a in raw if a.get("symbol")}`：
- 若 schema 变为多行（每链分条），后者静默覆盖前者
- 大小写 / whitespace 敏感

实测当前 DefiLlama 返回单行干净 `"USDT"` / `"USDC"`，不触发问题。本改动**纯防御性**：first occurrence wins + 归一化 + 多行触发 WARN。

**Files:**
- Modify: `src/integrations/onchain/service.py`
- Test: `tests/test_onchain_service.py`

### 3a — 先写 3 个新测试（TDD red）

- [ ] **Step 1: 在 `tests/test_onchain_service.py` 末尾追加 3 个新测试**

定位文件末尾（约 `:87`），追加：

```python


async def test_multi_row_same_symbol_first_occurrence_wins_with_warning(caplog):
    """Multiple rows for the same symbol: first occurrence kept, drift WARN logged."""
    import logging
    svc = _make_service()
    svc._client.fetch_stablecoins.return_value = [
        _asset("USDT", 100e9, 98e9),
        _asset("USDT", 50e9, 48e9),  # second-occurrence duplicate
    ]
    caplog.clear()
    with caplog.at_level(logging.WARNING,
                        logger="src.integrations.onchain.service"):
        result = await svc.get_stablecoin_snapshot()
    by_sym = {s.symbol: s for s in result["coins"]}
    assert by_sym["USDT"].circulating_usd == pytest.approx(100e9), (
        "first occurrence must win (not overwritten by second)"
    )
    drift_warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "schema drift" in r.getMessage().lower()
    ]
    assert len(drift_warnings) == 1, (
        f"expected exactly 1 schema-drift warning, got {len(drift_warnings)}"
    )
    assert "USDT" in drift_warnings[0].getMessage()


async def test_symbol_normalization_whitespace_and_case():
    """Symbol lookup is tolerant of 'USDT ' / ' usdt' / 'Usdt' variants."""
    svc = _make_service()
    svc._client.fetch_stablecoins.return_value = [
        _asset(" usdt", 100e9, 98e9),  # lowercase + leading whitespace
        _asset("USDC ", 50e9, 49e9),   # trailing whitespace
    ]
    result = await svc.get_stablecoin_snapshot()
    by_sym = {s.symbol: s for s in result["coins"]}
    assert "USDT" in by_sym
    assert "USDC" in by_sym
    assert by_sym["USDT"].circulating_usd == pytest.approx(100e9)
    assert by_sym["USDC"].circulating_usd == pytest.approx(50e9)


async def test_unknown_symbol_does_not_trigger_drift_warning(caplog):
    """Untracked symbols (e.g., DAI) silently skip — no drift WARN noise."""
    import logging
    svc = _make_service()
    svc._client.fetch_stablecoins.return_value = [
        _asset("USDT", 100e9, 98e9),
        _asset("DAI", 5e9, 5e9),
        _asset("DAI", 3e9, 3e9),  # duplicate of an untracked symbol
    ]
    caplog.clear()
    with caplog.at_level(logging.WARNING,
                        logger="src.integrations.onchain.service"):
        result = await svc.get_stablecoin_snapshot()
    by_sym = {s.symbol: s for s in result["coins"]}
    assert "USDT" in by_sym
    assert "DAI" not in by_sym
    drift_warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "schema drift" in r.getMessage().lower()
    ]
    assert len(drift_warnings) == 0, (
        f"expected zero drift warnings (DAI is untracked), got {len(drift_warnings)}"
    )
```

- [ ] **Step 2: 跑 3 个新测试**

Run: `uv run pytest tests/test_onchain_service.py -v -k "normalization or first_occurrence or unknown_symbol"`
Expected: all 3 FAIL（当前字典推导覆盖、大小写敏感、无 drift WARN）。

### 3b — 实施归一化 + first-occurrence + drift WARN

- [ ] **Step 3: 替换 `src/integrations/onchain/service.py` 的 `by_sym` 构造 + 后续组装段**

定位 `onchain/service.py:49-81`（从 `by_sym = ...` 开始，到 `StablecoinTotal(...)` 构造完成为止；`:83` 的 `return {"coins": coins, "total": total}` **保持不变**）。整块替换为：

```python
        # === Phase 1: normalize + first-occurrence dedup ===
        # Original `{a.get("symbol"): a for a in raw if a.get("symbol")}` had
        # two gaps: case/whitespace sensitivity, and silent overwrite when
        # multiple rows share a symbol. Fix: strip+upper, keep first-
        # occurrence, emit schema-drift WARN on duplicates within tracked
        # symbols. Untracked symbols skip silently to avoid log noise.
        #
        # IMPORTANT: DefiLlama top-level `circulating` is already
        # across-every-chain (see defillama.py:16-17). Multi-row same-symbol
        # should be treated as schema drift (e.g., if DefiLlama splits into
        # per-chain rows), NOT summed — summing would double-count under the
        # current schema.
        by_sym: dict[str, dict] = {}
        seen_duplicates: set[str] = set()
        for asset in raw:
            sym_raw = asset.get("symbol")
            if not sym_raw:
                continue
            sym = sym_raw.strip().upper()
            if sym not in _TRACKED_SYMBOLS:
                continue
            if sym in by_sym:
                seen_duplicates.add(sym)
                continue  # first occurrence wins
            by_sym[sym] = asset
        if seen_duplicates:
            logger.warning(
                "DefiLlama schema drift: multiple rows for symbol(s) %s; "
                "using first occurrence. Review if aggregation semantics changed.",
                sorted(seen_duplicates),
            )

        # === Phase 2: extract per-symbol + build totals ===
        coins: list[StablecoinSnapshot] = []
        total_circ = 0.0
        total_prev = 0.0
        for sym in _TRACKED_SYMBOLS:
            asset = by_sym.get(sym)
            if asset is None:
                continue
            circulating = float(
                (asset.get("circulating") or {}).get("peggedUSD", 0.0)
            )
            prev_week = float(
                (asset.get("circulatingPrevWeek") or {}).get("peggedUSD", 0.0)
            )
            delta = circulating - prev_week
            pct = (delta / prev_week * 100.0) if prev_week > 0 else 0.0
            coins.append(StablecoinSnapshot(
                symbol=sym,
                circulating_usd=circulating,
                change_7d_usd=delta,
                change_7d_pct=pct,
            ))
            total_circ += circulating
            total_prev += prev_week

        total_delta = total_circ - total_prev
        total_pct = (total_delta / total_prev * 100.0) if total_prev > 0 else 0.0
        total = StablecoinTotal(
            total_circulating_usd=total_circ,
            total_change_7d_usd=total_delta,
            total_change_7d_pct=total_pct,
        )
```

**注意**：本 step 仅处理 §3.2 归一化；`prev_week > 0 else 0.0` 的 pct 逻辑暂保留，Task 6（§3.5 M3）会再改为 `None`。

- [ ] **Step 4: 跑 3 个新测试 + 既有 onchain_service 测试**

Run: `uv run pytest tests/test_onchain_service.py -v`
Expected: all PASS（既有 + 3 new）。

- [ ] **Step 5: Commit**

```bash
git add src/integrations/onchain/service.py tests/test_onchain_service.py
git commit -m "$(cat <<'EOF'
feat(onchain): DefiLlama symbol normalization + drift WARN (N3 follow-up)

- Replace `{a.get("symbol"): a for a in raw if ...}` with explicit loop
  doing strip+upper normalization and first-occurrence dedup.
- Track duplicates within _TRACKED_SYMBOLS only (USDT/USDC); untracked
  symbols (e.g., DAI) skip silently to avoid log noise.
- Emit logger.warning when any tracked symbol appears multiple times —
  signals potential schema drift (e.g., DefiLlama switching to per-chain
  rows). Under the current schema (top-level `circulating` already
  across-every-chain), summing rows would double-count, so first-
  occurrence is the safer default.

Note: this commit preserves the existing `prev_week == 0 → pct = 0.0`
behavior unchanged. That legacy branch is intentionally fixed in the
next commit (§3.5 M3 → returns None + render "N/A (no prior-week data)")
to keep the normalization change reviewable on its own.

Spec: docs/superpowers/specs/2026-04-19-hardening-batch-design.md §3.2

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: §3.3 — HTF three-state `df.empty` + MA format align + §3.5 M1

**背景**：三个改动共享 `src/agent/tools_perception.py` 的 HTF 渲染区段（`:596-680`），在同一 commit 处理最紧凑：

1. **§3.3 主**：`df.empty` 走 "insufficient data"（data-gap）分支，与 exception 分支（outage）分开；两分支都加 `({timeframe}, {symbol})` 前缀
2. **§3.3 附加**：HTF MA 格式 `(price +X%)` → `(price vs MA: +X%)`，与 PR B 短周期 MA 对齐
3. **§3.5 M1**：HTF range `(0 {unit} ago)` / `(1 {unit} ago)` 的 0/singular 瑕疵

**Files:**
- Modify: `src/agent/tools_perception.py`
- Test: `tests/test_perception_tools_n3.py`

### 4a — 先写新测试 + 更新既有 outage 测试（TDD red）

- [ ] **Step 1: 更新既有 `test_htf_view_upstream_failure_degrades`（`:154-162`）**

定位测试，追加对新措辞前缀的断言。完整替换：

```python
async def test_htf_view_upstream_failure_degrades():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.side_effect = RuntimeError("OKX down")
    deps = _make_deps(market_data=market_data)
    result = await get_higher_timeframe_view(deps, timeframe="1d")

    assert "temporarily unavailable" in result.lower()
    # Context prefix added for clarity (spec §3.3)
    assert "(1d, BTC/USDT:USDT)" in result
```

- [ ] **Step 2: 在 `tests/test_perception_tools_n3.py` 追加 4 个新测试**

定位 `test_htf_view_insufficient_data_for_ma200` 测试（`:165` 起）。找到该测试之后的空行，追加：

```python


async def test_htf_empty_dataframe_returns_insufficient_data():
    """Empty DataFrame (successful fetch but no rows) is a data-gap, not outage."""
    from src.agent.tools_perception import get_higher_timeframe_view
    import pandas as pd

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = pd.DataFrame({
        "timestamp": [], "open": [], "high": [], "low": [], "close": [], "volume": [],
    })
    deps = _make_deps(market_data=market_data)
    result = await get_higher_timeframe_view(deps, timeframe="1d")

    assert "insufficient data" in result.lower()
    assert "temporarily unavailable" not in result.lower()
    assert "(1d, BTC/USDT:USDT)" in result


async def test_htf_ma_format_includes_vs_ma_prefix():
    """HTF MA line aligns with PR B short-period MA: 'price vs MA: +X%'."""
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)
    result = await get_higher_timeframe_view(deps, timeframe="1d")

    # Must contain the new prefix; must NOT contain the old bare 'price +X%'
    assert "(price vs MA:" in result
    # Guard against regression to the old format
    assert "(price +" not in result and "(price -" not in result


async def test_htf_range_latest_when_zero_ago():
    """When the max/min occurs on the last bar, render 'latest' instead of '0 X ago'."""
    from src.agent.tools_perception import get_higher_timeframe_view
    import pandas as pd

    market_data = AsyncMock()
    n = 100
    # Fabricate a series where both the global high AND global low land on the
    # very last bar (last bar has highest high AND lowest low — spike candle).
    # hi_ago and lo_ago are both 0, so both range lines should render "latest".
    rows = []
    for i in range(n):
        if i == n - 1:
            rows.append({
                "timestamp": i * 86_400_000,
                "open": 100.0, "high": 200.0, "low": 50.0,
                "close": 150.0, "volume": 1.0,
            })
        else:
            rows.append({
                "timestamp": i * 86_400_000,
                "open": 100.0, "high": 110.0, "low": 90.0,
                "close": 100.0, "volume": 1.0,
            })
    market_data.get_ohlcv_dataframe.return_value = pd.DataFrame(rows)
    deps = _make_deps(market_data=market_data)
    result = await get_higher_timeframe_view(deps, timeframe="1d")

    # BOTH hi_ago=0 AND lo_ago=0 hold (spike bar is max high AND min low);
    # both Range lines must render "latest". count==2 catches the partial-
    # fix bug where only one line got updated.
    lower = result.lower()
    assert lower.count("latest") == 2, (
        f"expected exactly 2 'latest' occurrences (hi + lo lines), got "
        f"{lower.count('latest')}:\n{result}"
    )
    # Must NOT emit the old "0 days ago" / "0 4h-bars ago" phrasing
    assert "0 day" not in lower
    assert "0 4h-bar" not in lower
    assert "0 week" not in lower
    assert "0 month" not in lower


async def test_htf_range_singular_when_one_ago():
    """When hi_ago or lo_ago == 1, use singular 'day/week/4h-bar/month'."""
    from src.agent.tools_perception import get_higher_timeframe_view
    import pandas as pd

    market_data = AsyncMock()
    n = 100
    # Spike on the second-to-last bar (index 98 → hi_ago=1)
    rows = []
    for i in range(n):
        if i == n - 2:
            rows.append({
                "timestamp": i * 86_400_000,
                "open": 100.0, "high": 300.0, "low": 90.0,
                "close": 100.0, "volume": 1.0,
            })
        else:
            rows.append({
                "timestamp": i * 86_400_000,
                "open": 100.0, "high": 110.0, "low": 95.0,
                "close": 100.0, "volume": 1.0,
            })
    market_data.get_ohlcv_dataframe.return_value = pd.DataFrame(rows)
    deps = _make_deps(market_data=market_data)
    result = await get_higher_timeframe_view(deps, timeframe="1d")

    # BOTH hi_ago=1 (spike high at n-2) AND lo_ago=1 (spike low at n-2) hold;
    # both Range lines must render "1 day ago" (singular). count==2 catches
    # the partial-fix bug where only one line uses singular.
    lower = result.lower()
    assert lower.count("1 day ago") == 2, (
        f"expected exactly 2 '1 day ago' occurrences (hi + lo lines), got "
        f"{lower.count('1 day ago')}:\n{result}"
    )
    # Must NOT emit the plural form anywhere (would indicate singular logic missed)
    assert "1 days ago" not in lower
```

- [ ] **Step 3: 跑新测试**

Run: `uv run pytest tests/test_perception_tools_n3.py -v -k "htf_empty or vs_ma_prefix or range_latest or range_singular or upstream_failure" 2>&1 | tail -30`
Expected: **5 FAIL**（所有 5 个测试都必然失败，因底层实现尚未改）：

- `htf_empty`：当前 `df.empty` 路径返回 "temporarily unavailable"，与新断言 "insufficient data" 矛盾
- `vs_ma_prefix`：当前 HTF MA 输出 `(price +X%)`，与新断言 `"(price vs MA:"` 矛盾
- `range_latest`：当前 `hi_ago == 0` 渲染成 "0 days ago"，与新断言 `"latest"` 矛盾
- `range_singular`：当前 `n == 1` 仍用复数 "1 days ago"，与新断言 `"1 day ago"` 矛盾
- `upstream_failure`（updated）：当前输出不含 `"(1d, BTC/USDT:USDT)"` 前缀

### 4b — 实施 HTF 三态 + MA 格式 + M1

- [ ] **Step 4: 修改 `_UNIT_LABEL`，补 singular map + 模块级 `_htf_ago_fmt` 辅助函数**

定位 `src/agent/tools_perception.py:596-597`。替换：

```python
# Unit labels for "N periods ago" rendered below range highs/lows.
_UNIT_LABEL = {"4h": "4h-bars", "1d": "days", "1w": "weeks", "1M": "months"}
_UNIT_LABEL_SINGULAR = {"4h": "4h-bar", "1d": "day", "1w": "week", "1M": "month"}


def _htf_ago_fmt(n: int, timeframe: str) -> str:
    """Render the 'N periods ago' suffix with proper latest/singular/plural
    grammar (spec §3.5 M1). 0 periods ago renders as 'latest' (the max/min
    landed on the most recent bar); 1 period uses the singular label; N>=2
    uses the plural label. Placed at module scope alongside _UNIT_LABEL*
    for consistency with other HTF helpers."""
    if n == 0:
        return "latest"
    if n == 1:
        return f"1 {_UNIT_LABEL_SINGULAR[timeframe]} ago"
    return f"{n} {_UNIT_LABEL[timeframe]} ago"
```

- [ ] **Step 5: 重写 HTF fetch 的异常分支 + empty 分支**

定位 `src/agent/tools_perception.py:612-619`。替换为：

```python
    try:
        df = await deps.market_data.get_ohlcv_dataframe(symbol, timeframe, limit=250)
    except Exception:
        logger.warning("HTF fetch failed for %s %s", symbol, timeframe, exc_info=True)
        return f"Higher timeframe view ({timeframe}, {symbol}): temporarily unavailable"

    if df.empty:
        return f"Higher timeframe view ({timeframe}, {symbol}): insufficient data"
```

- [ ] **Step 6: 改 HTF MA 行格式对齐 PR B 短周期**

定位 `src/agent/tools_perception.py:641-643`（`f"MA{period}: {ma:,.2f} (price {dist_pct:+.1f}%)"`）。替换为：

```python
        sections.append(
            f"MA{period}: {ma:,.2f} (price vs MA: {dist_pct:+.1f}%)"
        )
```

- [ ] **Step 7: 替换 Range High/Low 两行调用模块级 `_htf_ago_fmt`**

定位 `src/agent/tools_perception.py:662-663`。替换 Range High/Low 两行（保留 `""`、`"=== Range Position ==="` 和 `Current price within range` 行不变）：

```python
        sections.extend([
            "",
            "=== Range Position ===",
            f"100-period High: {hi100:,.2f} ({_htf_ago_fmt(hi_ago, timeframe)})",
            f"100-period Low:  {lo100:,.2f} ({_htf_ago_fmt(lo_ago, timeframe)})",
            f"Current price within range: {rng_pos:.1f}%",
        ])
```

本 step **不引入 inner 函数**；`_htf_ago_fmt` 在 Step 4 已置于模块级与 `_UNIT_LABEL*` 同层。此处仅调用。

**注意**：Step 4 引入 `_UNIT_LABEL_SINGULAR` 和 `_htf_ago_fmt` 后，HTF 函数的 `unit = _UNIT_LABEL[timeframe]` 行（`:645`）成为 dead code — 已核对当前代码，`unit` 仅在本段（100-period High/Low 两行）被引用；`:668-678` 的 20-period section 不引用 `unit`。**本步骤必须同时删除 `:645` 的 `unit = _UNIT_LABEL[timeframe]` 赋值行。**

为防误删（若未来有人在本 Step 之前引入新的 `unit` 使用），动手前先跑一次 grep 兜底：

```bash
grep -n '\bunit\b' src/agent/tools_perception.py
```

预期只见 `:645` 定义 + 被替换段的两处使用（总共 3 处）。若见额外引用，保留 `:645` 赋值行不删。

- [ ] **Step 8: 跑新测试 + 全部 HTF 族测试**

Run: `uv run pytest tests/test_perception_tools_n3.py -v -k "htf" 2>&1 | tail -20`
Expected: all PASS（既有 HTF 测试 + 5 updated/new 测试）。

**为何既有 `test_htf_view_period_label_for_*` 测试仍通过（fixture/M1 互动分析）**：`tests/test_perception_tools_n3.py:44-65` 的 `_make_ohlcv_df(250)` helper 构造的 OHLCV 序列是**线性上升**（`close = base + i * 50`），再加上 `high = close + 500`。因此 `:72,96,107` 等测试用 `_make_ohlcv_df(250)` 时：
- 100-period high 必然落在最后一根（index 99）→ `hi_ago = 0` → M1 后渲染 `"latest"`
- 100-period low 必然落在窗口第一根（相对 index 0）→ `lo_ago = 99` → 走 plural 分支，渲染 `"99 days ago"` / `"99 4h-bars ago"` / etc.

既有 4 个 period_label 测试（`test_htf_view_format_1d` / `_4h` / `_1w` / `_1m`）断言的是 `"days ago"` / `"4h-bars ago"` / `"weeks ago"` / `"months ago"` 子串存在——这些仍被 lo 行的 plural suffix 命中，M1 不破。Step 8 的 pytest 结果就是这个互动的直接验证。

- [ ] **Step 9: Commit**

```bash
git add src/agent/tools_perception.py tests/test_perception_tools_n3.py
git commit -m "$(cat <<'EOF'
refactor(perception/htf): three-state degradation + MA fmt + singular labels

- df.empty is a distinct condition from upstream exception (spec §3.3
  three-state contract). Empty DataFrame renders "insufficient data"
  (data-gap); exception renders "temporarily unavailable" (outage).
  Both branches now carry a `({timeframe}, {symbol})` prefix so the
  agent sees which HTF is degraded.
- HTF MA line uses "(price vs MA: +X%)" to align with PR B's short-
  period MA output; eliminates ambiguity with "price +X%".
- Range lines: 0 periods ago renders "latest"; 1 period ago uses the
  singular unit label (day / week / 4h-bar / month) via a new
  _UNIT_LABEL_SINGULAR map. Fixes the "0 days ago" / "1 days ago"
  grammar debt (M1).

Spec: docs/superpowers/specs/2026-04-19-hardening-batch-design.md §3.3 + §3.5 M1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: §3.4 persona ETF 措辞动态化 + §3.5 M2 ETF footer 双 clamp 去重

**背景**：两项都与 ETF 工具相关，共同一 commit。

- **§3.4**：`persona.py:43` 硬编码 "past 7 days" 但工具支持 1-14 天；改为描述默认 + 可调参数范围
- **§3.5 M2**：`tools_perception.py:807` 与 `crypto_etf/service.py:47` 都 clamp `days` 参数；删 tool 层 clamp，footer 从 service 返回的 list 长度推导

**Files:**
- Modify: `src/agent/persona.py`
- Modify: `src/agent/tools_perception.py`
- `src/integrations/crypto_etf/service.py` 保持不变（service clamp 仍是权威）

### 5a — §3.4 persona 措辞动态化

- [ ] **Step 1: 定位 `src/agent/persona.py` 内 ETF bullet**

Run: `grep -n 'ETF flows' src/agent/persona.py`
Expected: hit at `:43`。

- [ ] **Step 2: 替换 ETF bullet**

把 `persona.py` 中的 ETF bullet 整行（`:43` 单行 bullet）替换：

FROM:
```
- **ETF flows**: Use get_etf_flows for daily net flow data of US-traded BTC and ETH spot ETFs over the past 7 days, plus cumulative AUM. Today's value may be revised T+1.
```

TO:
```
- **ETF flows**: Use get_etf_flows for daily net flow data of US-traded BTC and ETH spot ETFs, plus cumulative AUM. Default lookback is 7 days; pass days parameter (1-14) to adjust. Today's value may be revised T+1.
```

- [ ] **Step 3: 确认 persona 测试无硬编码 "past 7 days" 断言**

Run: `grep -n 'past 7 days' tests/test_persona.py`
Expected: no hit（spec §3.4 已实测）。若有，此步需追加"更新 persona 测试断言"子任务。

- [ ] **Step 4: 跑 test_persona.py**

Run: `uv run pytest tests/test_persona.py -v 2>&1 | tail -10`
Expected: all PASS（persona 结构不变）。

### 5b — §3.5 M2 双 clamp 去重

- [ ] **Step 5-pre: pre-flight grep 既有 days 边界测试**

Run:

```bash
grep -n 'get_etf_flows.*days=\|Past .* trading days' tests/test_perception_tools_n3.py tests/test_crypto_etf_service.py
```

预期在 `tests/test_perception_tools_n3.py` 看到：
- `:444` — `await get_etf_flows(deps, days=30)` + `:446` `assert "Past 14 trading days" in result` + `:447` `assert "Past 30 trading days" not in result`
- `:461` — `await get_etf_flows(deps, days=0)` + `:463` `assert "Past 1 trading days" in result`

**含义**：既有测试断言的是 **clamped 后的 footer 文本**（14 / 1），不是 agent 传入的 `days` 原值。M2 改动后：
- 工具层不再 clamp；service 内部仍 clamp 到 14（或 1 when 0）；footer 用 `len(btc/eth)` = 14（或 1）渲染
- 外部行为**一致**，这两个测试不需改

若 grep 发现**其他**处断言 `"Past 30 trading days"` / `"Past 0 trading days"` / `"Past {raw_days}"`，标记为本 task 需同步更新的断言；当前实测无此类情况。

- [ ] **Step 5: 删除 `tools_perception.py` 的 tool 层 clamp**

定位 `src/agent/tools_perception.py:803-807`（注释块 + `days = max(1, min(days, 14))`）。整块替换为：

```python
    # `days` parameter is clamped in CryptoEtfService.get_etf_flows
    # (src/integrations/crypto_etf/service.py:47) — single source of truth.
    # The footer below derives the rendered day-count from the service's
    # actual result lengths, NOT the user-supplied `days`, so over-range
    # requests (e.g., days=30) render "Past 14 trading days" consistent
    # with the clamped value rather than the misleading "Past 30".
```

**注意**：删掉 `days = max(1, min(days, 14))` 赋值行；`days` 变量在下方 footer 不再可靠，必须从 service 结果推导（下一 step）。

- [ ] **Step 6: 改 footer，从 btc/eth 的实际长度推导天数**

定位 `src/agent/tools_perception.py:861-865`（`if btc or eth: sections.append(f"Note: Past {days} trading days ...")`）。替换为：

```python
    # Footer: operational facts the Agent needs in-context (spec §3.6).
    # The trading-day count is derived from the service's actual result
    # length — under the M2 single-clamp regime (§3.5), the clamp expression
    # lives only in CryptoEtfService.get_etf_flows:47 and the tool layer
    # reads the clamped outcome back from the result to keep the clamp
    # logic in one place (DRY). When btc and eth are both non-empty,
    # invariant len(btc) == len(eth) holds (same clamp + same parallel
    # fetch path in CryptoEtfService); pick whichever is non-empty to read
    # the rendered day count. Footer is emitted only when at least one
    # side rendered flow rows — a mix of outage (None) + data-gap ([]) has
    # no "today's value" for the T+1 caveat to refer to, so suppressing
    # the footer avoids misleading noise.
    if btc or eth:
        days_rendered = len(next((f for f in (btc, eth) if f), []))
        sections.append(
            f"Note: Past {days_rendered} trading days (weekends/holidays excluded).\n"
            "Note: Issuer-reported; today's value may be revised T+1."
        )
```

- [ ] **Step 7: 跑 test_tool_enhancement.py + test_perception_tools_n3.py（含 ETF 测试）**

Run: `uv run pytest tests/test_perception_tools_n3.py tests/test_tool_enhancement.py -v -k "etf or flow" 2>&1 | tail -20`
Expected: all PASS（footer 推导后的天数与 service 返回一致；既有 ETF 测试不应受影响）。

- [ ] **Step 8: 跑全量快速回归**

Run: `uv run pytest -q 2>&1 | tail -5`
Expected: counts match baseline + new tests added so far。

- [ ] **Step 9: Commit**

```bash
git add src/agent/persona.py src/agent/tools_perception.py
git commit -m "$(cat <<'EOF'
refactor(etf): persona wording + footer day count derived from result

- Persona Layer 1 ETF bullet no longer hard-codes "past 7 days" — now
  describes the 7-day default plus the 1-14 parameter range, matching
  the actual tool signature (get_etf_flows days=...).
- Drop the redundant clamp in tools_perception.get_etf_flows. The
  authoritative clamp lives in CryptoEtfService.get_etf_flows:47. The
  footer now derives "Past N trading days" from the rendered result
  length, so over-range requests (e.g., days=30) correctly say
  "Past 14 trading days" instead of the misleading "Past 30".

Spec: docs/superpowers/specs/2026-04-19-hardening-batch-design.md §3.4 + §3.5 M2

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: §3.5 M3 — `prev_week == 0` 时 pct 返回 None + 渲染层适配

**背景**：`onchain/service.py:65,76` 当前 `prev_week == 0` 时返回 `pct = 0.0`，语义上把"无数据"渲染为"0% 变化"，误导 agent。改为返回 `None`，渲染层显示 `"N/A (no prior-week data)"`。

**类型变更**：`StablecoinSnapshot.change_7d_pct` 和 `StablecoinTotal.total_change_7d_pct` 类型 `float` → `float | None`。

**渲染层适配**：`tools_perception.py` 的 stablecoin render 两处（`:901`、`:907`）使用 `{pct:+.2f}%` format spec，`None` 进来会抛 `TypeError`，必须条件渲染。

**Files:**
- Modify: `src/integrations/onchain/models.py`（类型）
- Modify: `src/integrations/onchain/service.py`（返回 None）
- Modify: `src/agent/tools_perception.py`（render 条件）
- Test: `tests/test_onchain_service.py`、`tests/test_perception_tools_n3.py`

### 6a — 先写 2 个新测试（TDD red）

- [ ] **Step 1: 在 `tests/test_onchain_service.py` 末尾追加服务层测试**

```python


async def test_prev_week_zero_returns_none_pct():
    """prev_week == 0 must render pct as None (no misleading 0%)."""
    svc = _make_service()
    svc._client.fetch_stablecoins.return_value = [
        _asset("USDT", 100e9, 0.0),  # prev_week=0
    ]
    result = await svc.get_stablecoin_snapshot()
    by_sym = {s.symbol: s for s in result["coins"]}
    assert by_sym["USDT"].change_7d_pct is None, (
        "prev_week=0 should yield pct=None, not 0.0 (spec §3.5 M3)"
    )
    # total_prev may still be 0 if USDC is absent; exercise that too
    total = result["total"]
    assert total.total_change_7d_pct is None
```

- [ ] **Step 2: 在 `tests/test_perception_tools_n3.py` 追加渲染层测试**

定位 `tests/test_perception_tools_n3.py` 里的 stablecoin 测试区段（grep `stablecoin` 定位）。在 stablecoin 测试区段末尾之前追加：

```python


async def test_stablecoin_render_handles_none_pct():
    """When StablecoinSnapshot.change_7d_pct is None, render 'N/A (no prior-week data)' without TypeError."""
    from src.agent.tools_perception import get_stablecoin_supply
    from src.integrations.onchain.models import StablecoinSnapshot, StablecoinTotal

    onchain = AsyncMock()
    onchain.get_stablecoin_snapshot.return_value = {
        "coins": [
            StablecoinSnapshot(
                symbol="USDT",
                circulating_usd=100e9,
                change_7d_usd=0.0,
                change_7d_pct=None,
            ),
        ],
        "total": StablecoinTotal(
            total_circulating_usd=100e9,
            total_change_7d_usd=0.0,
            total_change_7d_pct=None,
        ),
    }
    deps = _make_deps(onchain=onchain)
    # Must not raise TypeError on `None` in {v:+.2f}%
    result = await get_stablecoin_supply(deps)
    assert "N/A" in result
    assert "no prior-week data" in result
```

- [ ] **Step 3: 跑新测试**

Run: `uv run pytest tests/test_onchain_service.py::test_prev_week_zero_returns_none_pct tests/test_perception_tools_n3.py::test_stablecoin_render_handles_none_pct -v`
Expected: 2 FAIL（当前实现返回 0.0，不返回 None；渲染层无条件走 format spec）。

### 6b — 实施 M3 全链路（model → service → render）

- [ ] **Step 4: 改类型 `src/integrations/onchain/models.py`**

定位 `models.py`。替换全文为：

```python
"""Data models for stablecoin supply data."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StablecoinSnapshot:
    """Single-stablecoin supply snapshot with 7d change.

    change_7d_pct is None when prev_week == 0 (no baseline to compute %
    against). Rendering layer must condition on None and emit
    'N/A (no prior-week data)' rather than formatting None into a % spec.
    """
    symbol: str                    # "USDT" / "USDC"
    circulating_usd: float
    change_7d_usd: float
    change_7d_pct: float | None


@dataclass(frozen=True)
class StablecoinTotal:
    """Aggregate total across tracked stablecoins.

    total_change_7d_pct is None when total_prev == 0 (all tracked symbols
    missing prior-week data). See StablecoinSnapshot for rendering rule.
    """
    total_circulating_usd: float
    total_change_7d_usd: float
    total_change_7d_pct: float | None
```

- [ ] **Step 5: 改 service 返回 None**

定位 `src/integrations/onchain/service.py:65` 和 `:76`（Task 3 after state）。替换：

```python
            # M3 (spec §3.5): no prior-week baseline → pct is undefined. Use
            # None to let the render layer emit 'N/A (no prior-week data)'
            # instead of the misleading 0.0%.
            pct = (delta / prev_week * 100.0) if prev_week > 0 else None
```

和

```python
        total_pct = (
            (total_delta / total_prev * 100.0) if total_prev > 0 else None
        )
```

- [ ] **Step 6: 在模块级添加 `_fmt_pct` 辅助函数 + 改 render 条件**

**Step 6a — 在模块级添加 `_fmt_pct`**

定位 `src/agent/tools_perception.py` 里 `_fmt_signed_dollars` 函数（约 `:683-691`）与 `_fmt_big_usd` 函数（约 `:694-702`）。在 `_fmt_big_usd` 结尾（`:702` 或其后空行）之后、`get_macro_context` 定义（约 `:705`）之前，追加：

```python


def _fmt_pct(v: float | None) -> str:
    """Render a 7-day percentage change, tolerating None.

    Returns 'N/A (no prior-week data)' when pct is None (OnchainService sets
    change_7d_pct / total_change_7d_pct to None when prev_week == 0; §3.5 M3).
    Sibling to _fmt_big_usd / _fmt_signed_dollars — module-level helper,
    not inner-defined in get_stablecoin_supply.
    """
    if v is None:
        return "N/A (no prior-week data)"
    return f"{v:+.2f}%"
```

**Step 6b — 改 stablecoin render 段**

定位 `src/agent/tools_perception.py:896-908` 附近（stablecoin render 段）。替换 render 部分为：

```python
    lines = ["=== Stablecoin Supply ==="]
    for coin in result["coins"]:
        lines.append(
            f"{coin.symbol}: {_fmt_big_usd(coin.circulating_usd)} "
            f"(7d: {_fmt_signed_dollars(coin.change_7d_usd)}, "
            f"{_fmt_pct(coin.change_7d_pct)})"
        )
    total = result["total"]
    lines.append(
        f"Total Stablecoin Mcap: {_fmt_big_usd(total.total_circulating_usd)} "
        f"(7d: {_fmt_signed_dollars(total.total_change_7d_usd)}, "
        f"{_fmt_pct(total.total_change_7d_pct)})"
    )
```

`_fmt_pct` 从 Step 6a 定义的模块级函数引用，无 inner function。

- [ ] **Step 7: 传染性 grep — 确认无其他消费点假设 pct 是 float（对齐 spec §6.2 两套检查）**

spec §6.2 要求同时覆盖两个 angle：字段名访问 + 构造器位置的 isinstance 判断。分别跑两套 grep，并把两者结果都手动核查。

**第一套 — 字段名消费（捕 TypeError 风险：算术 / format spec）**：

```bash
grep -rn 'change_7d_pct\|total_change_7d_pct' src/ tests/
```

预期：只有 service 构造、模型定义、render、测试 fixture 场景。若见 `x.change_7d_pct > 0` / `x.change_7d_pct + ...` / `f"{x.change_7d_pct:...}"` 等假设 float 的用法（除 Task 6 Step 6 新加的 `_fmt_pct` 守护），补 None 处理。

**第二套 — 构造器/类型名消费（捕不访字段但做 isinstance / 类型判定的边缘情况）**：

```bash
grep -rn 'StablecoinSnapshot(\|StablecoinTotal(\|isinstance.*Stablecoin' src/ tests/
```

预期：所有 hit 要么是 model 本身的 `class` 定义，要么是 service 构造，要么是测试 fixture 的实例化。若见 `isinstance(x, StablecoinSnapshot)` 后紧跟对 `change_7d_pct` 做 non-None 断言的代码路径，补 None 守护。

若两套 grep 任一发现漏网消费点，追加到本 task 的改动清单。

- [ ] **Step 8: 跑 2 个新测试 + 全量 stablecoin 相关测试**

Run: `uv run pytest tests/test_onchain_service.py tests/test_perception_tools_n3.py -v -k "stablecoin or prev_week" 2>&1 | tail -30`
Expected: all PASS。

- [ ] **Step 9: Commit**

```bash
git add src/integrations/onchain/models.py src/integrations/onchain/service.py src/agent/tools_perception.py tests/test_onchain_service.py tests/test_perception_tools_n3.py
git commit -m "$(cat <<'EOF'
fix(onchain): prev_week=0 yields None pct, not misleading 0.0 (N3 M3)

- StablecoinSnapshot.change_7d_pct and StablecoinTotal.total_change_7d_pct
  change type float → float | None.
- OnchainService returns None when prev_week == 0 (no baseline to compute
  %); the previous 0.0 was indistinguishable from "0% change actually
  happened", which is misleading.
- tools_perception.get_stablecoin_supply render layer conditions on None
  and emits "N/A (no prior-week data)" rather than formatting None into
  a :+.2f spec (which would TypeError). Uses a module-level _fmt_pct
  helper (alongside _fmt_big_usd and _fmt_signed_dollars) to avoid
  duplicating the condition at each render site.

Spec: docs/superpowers/specs/2026-04-19-hardening-batch-design.md §3.5 M3

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: §3.6 API key scrubber 审计复核 + §3.5 M5 throttle test 容差审视

**背景**：Spec §3.6 原期望新增 2 个 test_http_error_does_not_leak_key 测试。Task 0 读码阶段已确认 `tests/test_macro_clients.py:114` + `:328` 两个既有测试**完全覆盖 spec §3.6 "stdlib logging + exception traceback 路径的 sanitization 回归保护" scope**。本 task 只做 audit 复核 + PR 描述备案。

**M5**：spec §3.5 M5 已允许"审视后保持现状"。本 task 把"审视"做完：若无 flaky 历史则不改。

**Files:** 无代码改动；审计结论纳入最终 PR description。

### 7a — §3.6 audit grep

- [ ] **Step 1: grep `exc_info=True` 在 MacroService 调用栈的所有出现**

Run:

```bash
grep -rn 'exc_info=True\|logger\.exception\|logger\.warning.*exc_info' src/integrations/macro src/services src/agent/tools_perception.py 2>&1 | head -30
```

逐处检查：若有任何 `logger.warning(..., exc_info=True)` 路径可能传播含 api_key 的 URL 到 log，记录为新 issue。预期：
- `tools_perception.py` get_macro_context 的 `logger.warning("Macro snapshot fetch failed", exc_info=True)` — 这是 spec §3.6 审计目标
- 其他 `exc_info=True`（`:263` fetch_order、`:628` HTF、`:894` stablecoin）不涉及 api_key URL

- [ ] **Step 2: 确认现有 FRED + AV 测试的覆盖面**

Run:

```bash
uv run pytest tests/test_macro_clients.py::test_fred_5xx_error_does_not_leak_api_key tests/test_macro_clients.py::test_av_5xx_error_does_not_leak_api_key -v
```

Expected: both PASS。核对测试代码，确认：
- mock 4xx/5xx 响应
- 构造器传 `api_key="SECRET-FRED-KEY"` / `"SECRET-AV-KEY"`
- 断言 `api_key_str not in str(exc_info.value)` — 这覆盖了 exception message（stdlib traceback 打印的核心）

若所有确认无误，§3.6 audit 结论为："**既有测试满足 spec 要求；本 PR 不新增相关测试**"。此结论将写入最终 PR 描述。

### 7b — §3.5 M5 throttle 容差审视

- [ ] **Step 3: 查 test_av_throttles_consecutive_calls 历史 flakiness**

Run:

```bash
git log --all --oneline -- tests/test_macro_clients.py | head -20
```

浏览历史 commit message，确认无 "flaky" / "throttle tolerance" 等词出现。

再跑 20 次本地稳定性验证（项目无 `pytest-repeat`，spec §1.2 禁加新 dev 依赖，故统一用 bash loop；输出 `FAILED` 计数，要求 20 次全绿）：

```bash
fails=0
for i in $(seq 1 20); do
  if ! uv run pytest tests/test_macro_clients.py::test_av_throttles_consecutive_calls -q 2>&1 | tail -1 | grep -q "1 passed"; then
    fails=$((fails + 1))
    echo "iteration $i: FAILED"
  fi
done
echo "20 iterations completed; failures=$fails"
```

Expected：`failures=0`（20/20 PASS）。若出现任何 flaky（`failures > 0`），记录并启动独立排查。若全绿，结论："**当前 `0.9 ≤ x ≤ 1.2` 容差在 20/20 本地跑中稳定，无须收紧**"，写入 PR 描述。

### 7c — 提交结论备忘（本 task 不产 commit）

- [ ] **Step 4: 把 audit + M5 结论写进 PR 描述草稿（Task 9 会用到）**

无代码改动，无 commit。记下两段文字供 PR 描述使用：

```
§3.6 audit conclusion: tests/test_macro_clients.py already contains
test_fred_5xx_error_does_not_leak_api_key + test_av_5xx_error_does_not_leak_api_key
covering spec §3.6's "stdlib logging + exception traceback sanitization
regression guard" scope (both construct manual HTTPStatusError, both
assert api_key not in str(exc_info.value)). No new tests needed.

§3.5 M5 (throttle tolerance review): 20 consecutive local runs of
test_av_throttles_consecutive_calls passed with no flake. Current
0.9 ≤ sleep_duration ≤ 1.2 window (vs _MIN_INTERVAL=1.1) stays.
```

---

## Task 8: 全量回归 + 验收脚本

- [ ] **Step 1: 全量 pytest**

Run: `uv run pytest -q 2>&1 | tail -5`
Expected: **663 passed**（baseline 651 + 12 new：3 AV counter + 3 DefiLlama + 2 HTF §3.3（empty + ma_prefix）+ 2 HTF §3.5 M1（latest + singular）+ 2 M3（service + render）；§3.6 audit 新增 0，已有测试满足）。

补偏差处置：
- 若 > 663：核对是否新加了额外测试（符合 plan 即可）
- 若 < 663：逐 task 确认对应测试已落地

- [ ] **Step 2: inline 验证 §3.1 counter 行为**

Run:

```bash
uv run python -c "
from unittest.mock import AsyncMock
import asyncio, httpx
from src.integrations.macro.alpha_vantage import AlphaVantageClient

async def main():
    resp_data = {'Global Quote': {
        '01. symbol': 'SPY', '05. price': '500.0',
        '10. change percent': '0.5%', '07. latest trading day': '2026-04-19',
    }}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=resp_data))
    async with httpx.AsyncClient(transport=transport) as http:
        c = AlphaVantageClient(http, api_key='k')
        assert c._daily_count == 0
        assert c._daily_count_date != ''
        assert c._warned_today is False
        print('OK — counter fields initialized as expected')

asyncio.run(main())
"
```

Expected: `OK — counter fields initialized as expected`。

- [ ] **Step 3: inline 验证 §3.3 HTF 三态消息格式**

Run:

```bash
uv run python -c "
from unittest.mock import AsyncMock
import pandas as pd, asyncio
from src.agent.tools_perception import get_higher_timeframe_view

async def main():
    # outage
    deps = AsyncMock(); deps.symbol = 'BTC/USDT:USDT'
    deps.market_data = AsyncMock()
    deps.market_data.get_ohlcv_dataframe.side_effect = RuntimeError('down')
    r1 = await get_higher_timeframe_view(deps, timeframe='1d')
    assert '(1d, BTC/USDT:USDT): temporarily unavailable' in r1, r1

    # data gap
    deps.market_data.get_ohlcv_dataframe.side_effect = None
    deps.market_data.get_ohlcv_dataframe.return_value = pd.DataFrame({
        'timestamp': [], 'open': [], 'high': [], 'low': [], 'close': [], 'volume': [],
    })
    r2 = await get_higher_timeframe_view(deps, timeframe='1d')
    assert '(1d, BTC/USDT:USDT): insufficient data' in r2, r2
    print('OK — HTF three-state contract holds')

asyncio.run(main())
"
```

Expected: `OK — HTF three-state contract holds`。

- [ ] **Step 4: 传染性 grep — pct None 消费点**

Run:

```bash
grep -rn 'change_7d_pct' src/ tests/ | grep -v -E 'models.py|service.py|test_onchain_service.py|test_perception_tools_n3.py|test_onchain_client.py|tools_perception.py'
```

Expected: 空输出。`test_onchain_client.py:91,94,102` 的 3 处 `change_7d_pct=1.27` / `total_change_7d_pct=1.22` 字面量实例化已在 filter 内排除：spec §3.5 M3 已确认这 3 处在类型扩到 `float | None` 后兼容（1.27 是 valid float），无需改。

---

## Task 9: push + 创建 PR（**需用户 checkpoint**）

Push 分支与 `gh pr create` 都是对外可见操作（分支出现在 origin、PR 触发 CI / 通知 reviewer），按 `feedback_review_before_commit` + system prompt 的"shared state 操作需 confirm"原则，**执行前必须先报告给用户并等待明确批准**，不得在无确认情况下连贯跑完。

### 9a — Push 分支（等用户批准再执行）

- [ ] **Step 1: 预检：汇报即将推送的内容**

Run:

```bash
git log --oneline origin/main..HEAD
git diff --stat origin/main..HEAD
```

把输出整理为摘要（commit 列表 / 文件 / 总行数），报告给用户并**明确提问**："确认 push `feat/pr-c-n3-followups` 到 origin 吗？"

- [ ] **Step 2: 等用户明确批准后再执行 push**

**禁止自动跑**。仅当用户回答 "push / 可以 / 好 / 确认" 等明确同意时执行：

```bash
git push -u origin feat/pr-c-n3-followups
```

Expected: 新分支创建在 origin。

### 9b — 创建 PR（等用户批准再执行）

- [ ] **Step 3: 预检：把拟 PR 标题 + body 草稿贴给用户审阅**

把下列内容作为"将用 `gh pr create` 提交的草稿"**先贴给用户**，等待用户回答 "创建 PR / 可以 / 确认" 后再执行 Step 4：

```
标题: refactor(hardening): N3 follow-up batch (AV counter / DefiLlama / HTF / M1-M3 / audit)

Body:

## Summary

- **§3.1** AlphaVantageClient instance-level daily-call counter + 80% warning (UTC date window; try/finally with `consumed_quota` flag).
- **§3.2** DefiLlama symbol normalization (strip+upper) + first-occurrence dedup + schema-drift WARN on multi-row same-symbol (untracked skip silently).
- **§3.3** HTF three-state: `df.empty` → "insufficient data" (data-gap), exception → "temporarily unavailable" (outage), both with `({tf}, {sym})` prefix. HTF MA line aligned with PR B short-period format: `(price vs MA: +X%)`.
- **§3.4** persona ETF bullet: "past 7 days" → dynamic description of 7-day default + 1-14 parameter range.
- **§3.5 M1** HTF Range: `0 {unit} ago` → `latest`; `1 {unit} ago` uses singular (day / week / 4h-bar / month).
- **§3.5 M2** `get_etf_flows` tool-layer clamp removed (service layer `:47` remains authoritative); footer day-count now derived from service result length.
- **§3.5 M3** `StablecoinSnapshot.change_7d_pct` + `StablecoinTotal.total_change_7d_pct`: `float` → `float | None`. `prev_week == 0` yields `None` (render emits "N/A (no prior-week data)" instead of misleading 0.0%).
- **§3.5 M5** Throttle test tolerance reviewed; no flake history, kept as-is.
- **§3.6** FRED + AV key-scrubber audit: existing `test_fred_5xx_error_does_not_leak_api_key` + `test_av_5xx_error_does_not_leak_api_key` cover the spec's "stdlib logging + exception traceback sanitization regression guard" scope. No new tests needed.
- **Docs** `docs/source-risk-matrix.md` backfilled: DefiLlama aggregation approach / M6 removal / §4→§3 numbering.

## Test plan

- [ ] `uv run pytest -q` → 663 passed (baseline 651 + 12 new: 3 AV counter + 3 DefiLlama + 2 HTF §3.3 + 2 HTF §3.5 M1 + 2 M3; §3.6 audit adds 0 tests)
- [ ] AV counter: `_daily_count` increments only on quota-consumed paths; 80% warning fires once per UTC day
- [ ] DefiLlama: first-occurrence wins on multi-row same-symbol; drift WARN emitted; untracked symbols skip silently
- [ ] HTF: `df.empty` and exception branches distinguish in output; MA format aligns with PR B
- [ ] ETF footer: `days_rendered = len(...)` uses real result length, not user-supplied `days`
- [ ] Stablecoin: `None` pct path returns `"N/A (no prior-week data)"` without TypeError

Spec: `docs/superpowers/specs/2026-04-19-hardening-batch-design.md` §3

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

- [ ] **Step 4: 用户批准后再执行 `gh pr create`**

**禁止自动跑**。仅当用户明确同意后：

```bash
gh pr create --title "refactor(hardening): N3 follow-up batch (AV counter / DefiLlama / HTF / M1-M3 / audit)" --body "$(cat <<'EOF'
## Summary

- **§3.1** AlphaVantageClient instance-level daily-call counter + 80% warning (UTC date window; try/finally with `consumed_quota` flag).
- **§3.2** DefiLlama symbol normalization (strip+upper) + first-occurrence dedup + schema-drift WARN on multi-row same-symbol (untracked skip silently).
- **§3.3** HTF three-state: `df.empty` → "insufficient data" (data-gap), exception → "temporarily unavailable" (outage), both with `({tf}, {sym})` prefix. HTF MA line aligned with PR B short-period format: `(price vs MA: +X%)`.
- **§3.4** persona ETF bullet: "past 7 days" → dynamic description of 7-day default + 1-14 parameter range.
- **§3.5 M1** HTF Range: `0 {unit} ago` → `latest`; `1 {unit} ago` uses singular (day / week / 4h-bar / month).
- **§3.5 M2** `get_etf_flows` tool-layer clamp removed (service layer `:47` remains authoritative); footer day-count now derived from service result length.
- **§3.5 M3** `StablecoinSnapshot.change_7d_pct` + `StablecoinTotal.total_change_7d_pct`: `float` → `float | None`. `prev_week == 0` yields `None` (render emits "N/A (no prior-week data)" instead of misleading 0.0%).
- **§3.5 M5** Throttle test tolerance reviewed; no flake history, kept as-is.
- **§3.6** FRED + AV key-scrubber audit: existing `test_fred_5xx_error_does_not_leak_api_key` + `test_av_5xx_error_does_not_leak_api_key` cover the spec's "stdlib logging + exception traceback sanitization regression guard" scope. No new tests needed.
- **Docs** `docs/source-risk-matrix.md` backfilled: DefiLlama aggregation approach / M6 removal / §4→§3 numbering.

## Test plan

- [ ] \`uv run pytest -q\` → 663 passed (baseline 651 + 12 new: 3 AV counter + 3 DefiLlama + 2 HTF §3.3 + 2 HTF §3.5 M1 + 2 M3; §3.6 audit adds 0 tests)
- [ ] AV counter: \`_daily_count\` increments only on quota-consumed paths; 80% warning fires once per UTC day
- [ ] DefiLlama: first-occurrence wins on multi-row same-symbol; drift WARN emitted; untracked symbols skip silently
- [ ] HTF: \`df.empty\` and exception branches distinguish in output; MA format aligns with PR B
- [ ] ETF footer: \`days_rendered = len(...)\` uses real result length, not user-supplied \`days\`
- [ ] Stablecoin: \`None\` pct path returns \`"N/A (no prior-week data)"\` without TypeError

Spec: \`docs/superpowers/specs/2026-04-19-hardening-batch-design.md\` §3

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: 返回 PR URL；贴给用户。

---

## 完工验收（spec §6.2 回检）

- [ ] §3.1 AV 达 80% 阈值时 log warning（`test_daily_count_warning_at_threshold_only_once` 通过）
- [ ] §3.1 AV `_daily_count_date` 用 UTC + `alpha_vantage.py` 类注释写明"默认 UTC reset"
- [ ] §3.2 DefiLlama 归一化的 3 个防御测试通过（first occurrence / 大小写 / 未知 symbol 不噪音）
- [ ] §3.3 HTF 三态契约的 2 个测试通过（empty df insufficient + MA 格式 vs_ma_prefix）
- [ ] §3.3 既有 `test_htf_view_upstream_failure_degrades` 追加 `({tf}, {sym})` 前缀断言
- [ ] §3.4 `persona.py:43` 表述已动态化
- [ ] §3.5 M1 latest + singular 两新测试通过
- [ ] §3.5 M2 footer 从 service 结果长度推导（day count 与 clamp 一致）
- [ ] §3.5 M3 服务层 `prev_week=0` 返回 None，模型类型改 `float | None`，渲染层条件处理不 TypeError
- [ ] §3.5 M3 传染性 grep：全仓 `change_7d_pct` / `total_change_7d_pct` 消费点无假设 float 用法
- [ ] §3.5 M5 throttle 测试容差审视后保持现状（PR 描述说明 20/20 本地通过）
- [ ] §3.6 FRED + AV 现有 `_does_not_leak_api_key` 测试已覆盖 spec 要求（PR 描述记录不新增）
- [ ] 文档 drift 回填：`docs/source-risk-matrix.md` 已改 3 处 DefiLlama 描述 + 删 M6 + §4→§3 编号
- [ ] 测试总数 **663**（651 + 12 新增）
