# WebUI 观察台双修复 — 设计

## 背景与范围

sim-only 观察期,WebUI 为只读观察台。本迭代修两个**观察侧**缺陷,均不碰 agent loop / 撮合 / OKX,**零 DB 迁移**。两个修复逻辑独立,合一 spec / 一 PR。mini-iter:短 spec + inline TDD,跳过独立 plan 文档与 subagent 编排。

---

## 议题 1:cycle 运行中注入的成交不进概述栏

### 现象

cycle 行标题概述栏对**运行途中**注入的成交(止损 / 止盈 / 限价成交)显示「（无交易）」,漏报。

### 根因

`_derive_key_events`(`src/webui/queries.py:92`)只扫两个来源:

1. `trigger_context` 里 `type=="fill"` 的被动 fill —— cycle 开始态快照的**触发** fill。
2. 本轮 `tool_calls` 的主动动作(open / close / limit_order)。

而 cycle 运行途中触发的成交由 `MidCycleEventInjector`(`src/services/midcycle_injector.py:129`)在工具边界 drain 后写入 `injected_events`。该数据**既不进 `trigger_context`(cycle 开始时已快照)、也不是 tool_call**,故不进 `key_events` → 行渲染「（无交易）」(`CycleRowHeader.vue:55`)。

### 实证

DB 全量扫描:5 个 cycle 的 `injected_events` 含 fill 而 `trigger_context` 不含 —— sim#19(2)/ sim#20(1)/ sim#21(2),**全为 stop 止损平空**。该成交在 cycle **详情**的注入卡(`_enrich_injected_events`)可见,仅**行级概述栏**漏。被漏的恰是「仓位被市场止损出场」这类最该提示的事件。

### 设计

**后端**

- `src/webui/schemas.py` —— `KeyEvent` 加字段:
  ```python
  mid_cycle: bool = False   # True = cycle 运行中注入的 fill（前端虚线描边区分）
  ```
  默认 `False` → trigger fills / tool actions / 历史行 / 旧前端全零破坏。

- `src/webui/queries.py` `_derive_key_events` —— 在现有两 pass 之后追加第三 pass:
  ```python
  for rec in _normalize_to_list(_loads(c.injected_events)):
      ev_dict = rec.get("event") if isinstance(rec, dict) else None
      if isinstance(ev_dict, dict) and ev_dict.get("type") == "fill":
          ev = _safe(lambda d=ev_dict: _classify_fill(d))
          if ev is not None:
              events.append(ev.model_copy(update={"mid_cycle": True}))
  ```
  复用 `_classify_fill`(单一权威来源);`trigger_reason=="market"` 回声仍 → `None` 跳过,与 trigger 路径一致。`_classify_fill` 不改签名,`_injection_kind_label`(详情卡)仍复用它且只读 `.label`,零影响。

- **事件顺序**:`[trigger fills] + [tool actions] + [mid-cycle fills]` —— 近似时序(触发 → agent 动作 → 运行中被打掉);虚线描边为主区分信号,顺序为次要。

- **不去重**:scheduler 堆内每事件只被消费一次 —— 要么作唤醒触发进 `trigger_context`,要么 mid-cycle drain 进 `injected_events`,**不可能同时出现在两处**(单次消费不变量)。naive 去重(按 kind / direction)会误删两笔同形止损,故**依赖不变量 + 注释言明,不加去重码**。

- **副作用(预期)**:仅含一笔注入 fill 的 cycle 现在 `key_events.length > 0` → 自动获得 `keyrow` 蓝带高亮。止损出场本就是关键事件,符合预期,无需额外处理。

**前端**

- `frontend/src/api/types.ts` + `frontend/openapi.json` —— regen(`KeyEvent.mid_cycle: boolean`)。openapi.json 保持**单行 minify**(`json.dump(obj, f, ensure_ascii=False)`,无 indent)。

- `frontend/src/components/CycleRowHeader.vue` —— chip 循环:`mid_cycle` 为真时 `:bordered="true"` + class `mid-cycle`,CSS `border-style: dashed`(色继承 chip type)。
  - ⚠ 风险:naive `n-tag` 边框走内部 `.n-tag__border`;`:deep` 内部 class 升级须复验(已记 footgun)。落地时 Playwright 实测虚线确实渲染。

---

## 议题 2:活跃会话小周期 K 线重开全量重下

### 现象

活跃会话(正在跑的 sim)小周期(1m / 5m)K 线每次重开等待很久,像在下载全量数据。

### 根因

缓存命中仅一条(`src/webui/ohlcv_cache.py:45`):`current_end_ms <= fetched_end_ms`,其中 `current_end_ms` = 会话窗口 `end_ms` = `last_active_at`(`src/services/ohlcv_history.py:58`)。两个后果:

1. **全有或全无 + miss 即全量重拉**:`end_ms` 只要前进一点点就 miss,miss 时 `get_ohlcv` 调 `fetch_ohlcv_window(symbol, tf, start_ms, end_ms)` 从会话创建重拉整段(`queries.py:500`),**无增量**。
2. **活跃会话 `last_active_at` 每 cycle 前进** → 几乎必 miss → 每次重开全量重下。
3. **小周期放大**:全量按 `_PAGE_LIMIT=100`/页 + `_THROTTLE_SLEEP_S=0.5s`/页串行分页。

### 实证

sim#21(`status=active`)各 tf 缓存 `fetched_end_ms` 不一致(1m/15m/1h = 当前 last_active;5m / 4h 更旧),旧的那批一开即 miss 全量重拉。1m 缓存 3584 bar ≈ 36 页 × ~0.7s ≈ **25s+**。已结束 / 暂停会话 `last_active` 固定 → 首开后恒命中,慢只发生在**活跃会话**。

### 设计(方案 A:增量尾部拉取;不叠 tf-bucket)

**`src/webui/ohlcv_cache.py` 重构** —— 覆盖判定上移 `queries`,cache 只管文件 I/O:

- `read()` → `read_raw(cache_dir, sid, tf) -> dict | None`:文件存在且合法 → 返回整个 blob(`{symbol, timeframe, fetched_end_ms, bars}`);缺失 / 损坏 → `None`(同现有 graceful degradation)。**不再做 `current_end_ms` 比较**。
- `write()` 不变。

**`src/services/ohlcv_history.py` 加纯函数**:

```python
def merge_bars(old: list[list], new: list[list]) -> list[list]:
    """按 ts merge，new 覆盖 old（刷新边界未收完 bar）；升序、同 ts 去重。"""
    by_ts = {r[0]: r for r in old}
    by_ts.update({r[0]: r for r in new})
    return sorted(by_ts.values(), key=lambda r: r[0])
```

**`src/webui/queries.py` `get_ohlcv` 新流程**:

```python
blob = ohlcv_cache.read_raw(cache_dir, session_id, tf)
if blob is not None and end_ms <= blob["fetched_end_ms"]:
    rows = blob["bars"]                                          # ① 全命中：零网络
elif blob is not None and blob["bars"]:
    last_ts = blob["bars"][-1][0]
    tail = await fetch_ohlcv_window(symbol, tf, last_ts, end_ms) # ② 增量：含 last_ts 刷新边界
    rows = merge_bars(blob["bars"], tail)
    ohlcv_cache.write(cache_dir, session_id, tf, symbol, end_ms, rows)
else:
    rows = await fetch_ohlcv_window(symbol, tf, start_ms, end_ms)# ③ 冷启动/空缓存：全量一次
    ohlcv_cache.write(cache_dir, session_id, tf, symbol, end_ms, rows)
```

- **② 效果**:活跃会话重开,1m 也只拉 `[last_ts, now)` ≈ 1 页(~0.5–1s),不再 36 页 25s。
- **边界 bar 刷新**:tail 从 `last_ts`(含,`fetch_ohlcv_window` 半开过滤 `last_ts <= r[0] < end_ms`)起拉,`merge_bars` 中 new 覆盖 → 那根未收完的 bar 刷新成最新值。
- **空 tail**(last_active 涨了但没出新 bar):tail 至少含边界那根 → `merge` 刷新边界后 `fetched_end` 推到 `end_ms` → 下次同 `end_ms` 重开变 ① 全命中。比方案 B(tf-bucket)多一次 1 页拉取,但简单且足够快(YAGNI 不叠 B)。
- **已结束会话**:`end_ms` 固定 → 首开后恒走 ①,行为同今。
- `OhlcvSeries` schema 不变 → **无 types / openapi 改动**。

### 错误处理

- tail / 冷启动 fetch 重试耗尽(瞬态错)→ `fetch_ohlcv_window` re-raise → 端点 **503(仅类名,redaction 纪律)**,同现状。**不**静默降级返回旧缓存(避免无信号陈旧;符合 fact-provider 纪律)。
- 损坏缓存 → `read_raw` 返回 `None` → 走 ③ 冷启动,graceful。

---

## 测试

**议题 1**(`tests/test_webui_queries.py` + `frontend/test/CycleRowHeader.spec.ts`)

- 注入 fill(无 trigger fill)→ `key_events` 1 条 `mid_cycle=True`;trigger fill / action → `mid_cycle=False`。
- 注入 `trigger_reason=="market"` 回声 fill → 跳过(None)。
- 混合:trigger fill + action + 注入 fill → 3 条,`mid_cycle` flag 与顺序正确(trigger → action → mid-cycle)。
- drift-guard:同形 fill 经 trigger vs 注入两路 `label` 逐字一致(锁 `_classify_fill` 单源)。
- 前端:`mid_cycle=true` chip 有 `mid-cycle` dashed class、`false` 无;仅注入事件的 cycle 行获 `keyrow` 高亮。

**议题 2**(`tests/test_ohlcv_cache.py` + `tests/test_ohlcv_history.py` + `tests/test_webui_queries.py`)

- `read_raw`:命中 / 陈旧均返回 blob;损坏 / 缺失 → `None`。
- `merge_bars`:overlap → new 胜(边界刷新);升序;dedup;空 tail → old;空 old → tail。
- `get_ohlcv`(monkeypatch `fetch_ohlcv_window` 断言入参):① 全命中**不调** fetch;② 增量调 `(last_ts, end_ms)` **非** `(start_ms, end_ms)`、结果 merge、缓存重写 `fetched_end=end_ms`;③ 冷启动调 `(start_ms, end_ms)`;损坏缓存 → 走 ③。

**真实数据 Playwright**

- 活跃会话(sim#21)+ 已结束会话各开 1m / 5m,图正确渲染。
- 议题 1 取 sim#19 / #20 / #21 那 5 个注入止损 cycle 之一,实测行概述栏出现虚线描边「止损平空」chip。
- 增量「只拉尾部」由单测断言入参证明(Playwright 难测时延)。

**全量 gate**:后端 pytest 全绿 + 前端 vitest 全绿 + vue-tsc 0 + build 绿。

---

## 文件清单

| 文件 | 改动 |
|---|---|
| `src/webui/schemas.py` | `KeyEvent.mid_cycle` |
| `src/webui/queries.py` | `_derive_key_events` 第三 pass + `get_ohlcv` 增量流程 |
| `src/webui/ohlcv_cache.py` | `read` → `read_raw` 重构 |
| `src/services/ohlcv_history.py` | `merge_bars` |
| `frontend/openapi.json` + `frontend/src/api/types.ts` | regen(mid_cycle) |
| `frontend/src/components/CycleRowHeader.vue` | mid-cycle chip 虚线描边 |
| `tests/test_webui_queries.py` / `test_ohlcv_cache.py` / `test_ohlcv_history.py` | 后端测试 |
| `frontend/test/CycleRowHeader.spec.ts` | 前端测试 |

---

## 范围外(YAGNI / 守界)

- 不碰 agent loop / 撮合 / OKX 实盘路径。
- 不做 tf-bucket 覆盖判定(方案 B)—— A 已足够。
- 不改首次冷启动全量拉取 —— 痛点是重开非首开。
- 行概述栏只加 mid-cycle **fill**,不加 mid-cycle alert —— 沿用 `key_events` = fills + actions 的既有语义。
