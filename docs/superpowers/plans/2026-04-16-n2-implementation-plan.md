# N2: Market Intelligence Tools — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-04-16-n2-market-news-design.md`
**Branch:** `feature/n2-market-intelligence`
**PR:** `feat(N2): add market intelligence tools — news, alerts, derivatives`

---

## Execution Strategy

Bottom-up: foundation → data layer → service layer → tool layer → integration. Each step is independently testable before moving to the next.

---

## Step 1: Foundation

No dependencies. All subsequent steps build on this.

### 1a. Shared cache utility

**File:** `src/utils/__init__.py` (create) + `src/utils/cache.py` (create)

```python
# cached_fetch(cache_dict, key, ttl, fetch_fn) -> data
# - Check cache: if key exists and now - created_at < ttl, return cached data
# - Otherwise call fetch_fn(), store (data, now, ttl), return data
# - On 429: set entry ttl to 1800s, return cached data if exists, else raise
# - Cache entry: dict[str, tuple[Any, float, float]]  # (data, created_at, ttl)
```

**Verify:** Unit test `cached_fetch` with mock async functions.

### 1b. InformationEvent data model

**File:** `src/integrations/news/__init__.py` (create) + `src/integrations/news/models.py` (create)

```python
@dataclass
class InformationEvent:
    timestamp: datetime
    source: str
    category: str
    importance: Literal["low", "medium", "high"]
    title: str
    content: str = ""
    url: str = ""
    symbols: list[str] = field(default_factory=list)
```

### 1c. Derivatives data types + BaseExchange abstract methods

**File:** `src/integrations/exchange/base.py` (edit)

- Add `FundingRate`, `OpenInterest`, `LongShortRatio` dataclasses
- Add 3 abstract methods: `fetch_funding_rate`, `fetch_open_interest`, `fetch_long_short_ratio`

### 1d. NewsConfig

**File:** `src/config.py` (edit)

- Add `NewsConfig(BaseModel)` with `enabled: bool = True`, `cryptopanic_api_key: str = ""`
- Add `news: NewsConfig = NewsConfig()` to `Settings`
- Update `load_settings()` to load `CRYPTOPANIC_API_KEY` from env

**File:** `config/settings.yaml` (edit) — add `news:` section

**Verify:** Extend `tests/test_config.py` with `test_news_config_from_yaml` and `test_news_config_env_override`. Run `pytest tests/test_config.py`.

---

## Step 2: Derivatives Data Path

Depends on Step 1c. Self-contained vertical slice — from exchange layer to tool output.

### 2a. OKXExchange implementation

**File:** `src/integrations/exchange/okx.py` (edit)

- `fetch_funding_rate(symbol)` — `@_retry()`, call `self._client.fetch_funding_rate(symbol)`
- `fetch_open_interest(symbol)` — `@_retry()`, call `self._client.fetch_open_interest(symbol)`
- `fetch_long_short_ratio(symbol)` — `@_retry()`, call `self._client.fetch_long_short_ratio_history(symbol, "5m", limit=1)`, take latest entry, compute `long_ratio = ratio / (1 + ratio)`, `short_ratio = 1 / (1 + ratio)`

### 2b. SimulatedExchange implementation

**File:** `src/integrations/exchange/simulated.py` (edit)

- Same 3 methods, using `self._ccxt` instead of `self._client`
- Same `fetch_long_short_ratio_history` pattern

### 2c. MarketDataService derivatives methods

**File:** `src/integrations/market_data.py` (edit)

- Add `self._derivatives_cache: dict = {}` in `__init__`
- Add `get_funding_rate(symbol)`, `get_open_interest(symbol)`, `get_long_short_ratio(symbol)`
- Each uses `cached_fetch` from `src/utils/cache.py` with TTL=180s, key=`"funding:{symbol}"` etc.

### 2d. Tool: get_derivatives_data

**File:** `src/agent/tools_perception.py` (edit) — add `get_derivatives_data()` implementation
**File:** `src/agent/trader.py` (edit) — register `get_derivatives_data` tool

**Verify:** `tests/test_derivatives_data.py` — mock exchange methods, test format output, cache behavior, API failure degradation. Run `pytest tests/test_derivatives_data.py`.

---

## Step 3: News Data Clients

Depends on Step 1a, 1b. Each client is independent — can be implemented in parallel.

### 3a. CryptoPanic client

**File:** `src/integrations/news/cryptopanic.py` (create)

- `async def fetch_news(http, api_key, news_filter, limit=20) -> list[InformationEvent]`
- Parse JSON response: `results[]` → InformationEvent list
- `extract_base_currency(symbol)` helper function

### 3b. Fear & Greed Index client

**File:** `src/integrations/news/fear_greed.py` (create)

- `async def fetch_fgi(http) -> InformationEvent | None`
- Parse: `data[0].value`, `data[0].value_classification`

### 3c. ForexFactory calendar client

**File:** `src/integrations/news/calendar.py` (create)

- `async def fetch_calendar(http) -> list[InformationEvent]`
- Filter: `country == "USD"` and `impact in ("High", "Medium")`

### 3d. OKX announcements client

**File:** `src/integrations/news/okx_announcements.py` (create)

- `async def fetch_announcements(http, ann_types) -> list[InformationEvent]`
- Default `ann_types`: `["announcements-delistings", "trading-updates-us-aus"]`
- Parse: `data[0].details[]` → InformationEvent list

### 3e. OKX system status client

**File:** `src/integrations/news/okx_status.py` (create)

- `async def fetch_system_status(http, states) -> list[InformationEvent]`
- Default `states`: `["scheduled", "ongoing"]`
- Parse: `data[]` → InformationEvent list

**Verify:** Test each client independently with mock HTTP responses. Part of `tests/test_news_service.py`.

---

## Step 4: NewsService

Depends on Step 3. Aggregates all clients + caching + quota protection.

**File:** `src/integrations/news/service.py` (create)

```python
class NewsService:
    def __init__(self, api_key: str | None = None): ...

    # News + FGI (for get_market_news tool)
    async def get_news(self, symbol, news_filter, max_per_group=5) -> list[InformationEvent]
    async def get_fear_greed_index(self) -> InformationEvent | None

    # Alerts (for get_critical_alerts tool)
    async def get_macro_events(self, lookahead_hours) -> list[InformationEvent]
    async def get_announcements(self, lookback_hours) -> list[InformationEvent]

    async def close(self) -> None
```

Key behaviors to implement:
- CryptoPanic: single API call (limit=20, no currencies filter), local grouping by symbol
- Cache with `cached_fetch`, per-source TTL
- Daily call counter (lazy reset on date change, cap at 180)
- 429 handling: extend TTL, prefer same-filter stale cache, degrade if none
- `deps.news is None` → return "News service not configured"

**Verify:** `tests/test_news_service.py` — cache, quota, 429, degradation, formatting. Run `pytest tests/test_news_service.py`.

---

## Step 5: News & Alerts Tools

Depends on Step 4.

### 5a. Tool: get_market_news

**File:** `src/agent/tools_perception.py` (edit)

```python
async def get_market_news(deps, news_filter=None) -> str:
    # deps.news is None → "News service not configured"
    # Get FGI + news (5 symbol + 5 general)
    # Format as text sections
```

**File:** `src/agent/trader.py` (edit) — register tool with `Literal` type for `news_filter`

### 5b. Tool: get_critical_alerts

**File:** `src/agent/tools_perception.py` (edit)

```python
async def get_critical_alerts(deps, lookback_hours=24, lookahead_hours=12) -> str:
    # deps.news is None → "News service not configured"
    # Get announcements (OKX) + macro events (ForexFactory)
    # Format as text sections
```

**File:** `src/agent/trader.py` (edit) — register tool

### 5c. TradingDeps update

**File:** `src/agent/trader.py` (edit)

- Add `from src.integrations.news.service import NewsService` (direct import)
- Add `news: NewsService | None = None` to TradingDeps

**Verify:** Full tool output format tests in `tests/test_news_service.py`. Run `pytest tests/test_news_service.py tests/test_derivatives_data.py`.

---

## Step 6: Integration

Depends on Steps 2-5.

### 6a. CLI app integration

**File:** `src/cli/app.py` (edit)

- Load `CRYPTOPANIC_API_KEY` from settings/env
- Create `NewsService(api_key=...)` 
- Pass `news=news_service` to `TradingDeps`
- Add `await news_service.close()` in shutdown logic (alongside `exchange.close()`)

### 6b. System prompt update

**File:** `src/agent/persona.py` (edit)

- Add 3 tool descriptions to Layer 1 (facts only, no strategy)

### 6c. Wizard Step 6

**File:** `src/cli/wizard.py` (edit)

- New Step 6 after Persona: CryptoPanic API key (allow skip)
- Skip message: "News headlines disabled, Fear & Greed Index still available"

**Verify:** Run full test suite `pytest`. All existing 417 tests + new tests must pass.

---

## Step 7: Final Verification

- `pytest` — all tests pass (existing + new)
- Manual smoke test: start bot in simulated mode, verify 3 new tools appear and return data
- Review all changed files against spec

---

## File Change Summary

| Action | File | Step |
|--------|------|------|
| Create | `src/utils/__init__.py` | 1a |
| Create | `src/utils/cache.py` | 1a |
| Create | `src/integrations/news/__init__.py` | 1b |
| Create | `src/integrations/news/models.py` | 1b |
| Create | `src/integrations/news/cryptopanic.py` | 3a |
| Create | `src/integrations/news/fear_greed.py` | 3b |
| Create | `src/integrations/news/calendar.py` | 3c |
| Create | `src/integrations/news/okx_announcements.py` | 3d |
| Create | `src/integrations/news/okx_status.py` | 3e |
| Create | `src/integrations/news/service.py` | 4 |
| Create | `tests/test_news_service.py` | 3-5 |
| Create | `tests/test_derivatives_data.py` | 2d |
| Edit | `src/integrations/exchange/base.py` | 1c |
| Edit | `src/integrations/exchange/okx.py` | 2a |
| Edit | `src/integrations/exchange/simulated.py` | 2b |
| Edit | `src/integrations/market_data.py` | 2c |
| Edit | `src/config.py` | 1d |
| Edit | `config/settings.yaml` | 1d |
| Edit | `src/agent/tools_perception.py` | 2d, 5a, 5b |
| Edit | `src/agent/trader.py` | 2d, 5a, 5b, 5c |
| Edit | `src/agent/persona.py` | 6b |
| Edit | `src/cli/app.py` | 6a |
| Edit | `src/cli/wizard.py` | 6c |
| Edit | `tests/test_config.py` | 1d |

**Total:** 12 new files + 12 edited files

---

## Handoff Notes for Implementation Session

1. Read the spec first: `docs/superpowers/specs/2026-04-16-n2-market-news-design.md`
2. Follow this plan step-by-step, run tests at each checkpoint
3. OKX endpoints verified: `/api/v5/support/announcements` + `/api/v5/system/status`
4. ccxt OKX: `has.fetchLongShortRatio` = False, use `fetch_long_short_ratio_history(symbol, "5m", limit=1)`
5. All prompt text is facts-only — no trading heuristics or strategy advice
6. Existing tests must not break (417 tests at baseline)
