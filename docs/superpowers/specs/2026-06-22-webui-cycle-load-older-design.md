# WebUI 加载更早 cycle 历史

## 背景

WebUI 打开会话详情时,`selectSession` 只拉最新 50 条 cycle(`stores/sessions.ts:77` `getCycles(id, { limit: 50 })`,id DESC)。运行中会话靠 `pollTick` 每 5s 用 `after_id` 增量往**更新**方向追加(`stores/sessions.ts:112-113`),数组只增不减(`:95`)。

由此暴露的缺口:**初始 50 条之前的早期历史无 UI 入口可达**。`before_id` 通道前后端其实都现成——后端 `app.py:40` + `queries.py:45-46`(`id < before_id`,DESC LIMIT),前端 client `api/client.ts:41,51` 也暴露了 `beforeId`——唯独 `stores/sessions.ts` 与 `DecisionStream.vue` 从未调用它。对 331-cycle 的长会话(如 sim #22),打开后前 ~281 条永久看不到。

本设计补上这最后一段接线:决策流底部新增「加载更早」按钮,点击往更早方向翻页。

## 目标与范围

**目标**:用户可从决策流底部按需加载初始 50 条之前的早期 cycle,直到会话最早一条。

**范围内**:纯前端——`stores/sessions.ts` 新增一个 action + 两个状态;`DecisionStream.vue` 底部新增按钮区。复用现成的 `mergeCycles` 与后端 `before_id` 通道。

**Non-goals**:
- 不改后端(`before_id` 通道已具备)。
- 不做无限滚动 / 滚动到底自动加载(显式按钮即可)。
- 不治理 P2「运行中会话数组无界增长 / 虚拟滚动」——另议。
- 不改 `pollTick` 增量轮询逻辑。

## 设计

### 数据流

```
用户点击「加载更早」
  → store.loadOlder()
      beforeId = cycles[cycles.length-1].id   // 数组维护为 id DESC,末元 = 当前最早
      api.getCycles(sid, { beforeId, limit: PAGE_SIZE })   // PAGE_SIZE = 50,与首屏共用
        → 后端: WHERE session_id=? AND id < before_id ORDER BY id DESC LIMIT 50
      → mergeCycles(older)   // 复用现成:Set 去重 + id DESC 排序,方向无关
```

每次点击 = 1 次 HTTP 请求,**往返次数恒定**:后端 1 次主查询(取 ≤50 条)+ 1 次批量 join 这批 cycle 的 `tool_calls`(`queries.py:60-65`)。注意恒定的是**往返次数**、与批大小无关——查询**开销**本身仍随会话规模(子查询对全会话开窗算 `row_number`/`lag`,`queries.py:29-38`)与这批 `tool_calls` 量走,不是"成本恒定"。

### Store 改动(`stores/sessions.ts`)

新增模块级常量 `PAGE_SIZE = 50` —— **首屏与到顶口径单源**:`selectSession` 首屏拉取(替换 `:77` 字面量 `50`)与 `loadOlder` 共用同一常量,否则两处到顶判定会各自漂移。

新增 state:
- `loadingOlder: boolean` —— `loadOlder` 在途标志(防重复点击 / 重叠请求)
- `reachedOldest: boolean` —— 已加载到会话最早一条

新增 action `loadOlder()`:
- 前置守卫,任一成立即 return:无 `currentId` / `loadingOlder` 在途 / 已 `reachedOldest` / `cycles` 为空(无游标基准)
- 起手记**双身份令牌**:`const sid = currentId`、`const seq = selectSeq`(**读、不自增**——`loadOlder` 不是新的会话选择;若自增会落进 `selectSession` 的 await 窗口、害它误丢弃自己的首屏)
- 置 `loadingOlder = true`;取 `beforeId = cycles[cycles.length-1].id`
- `api.getCycles(sid, { beforeId, limit: PAGE_SIZE })`
- **身份守卫**:`await` 后若 `currentId !== sid || selectSeq !== seq` 则丢弃结果、直接 return(`currentId` 防跨会话串档,`selectSeq` 防 A→B→A 重入产生空洞;详见「错误与边界」)
- **到顶判定**:返回数 `< PAGE_SIZE` → `reachedOldest = true`(少一次空请求即得知到顶)
- `mergeCycles(older)` 追加
- 错误:`catch` 写 `this.error`(与 `ensureCycleDetail` 一致,横幅提示),会话切走则不写
- `finally`:仅当仍是本会话(`currentId === sid && selectSeq === seq`)时复位 `loadingOlder = false`(重入下由 `selectSession` 负责复位,避免误清新在途请求的标志)

`selectSession`(`:60` 起)成功路径(身份守卫 `:79` 通过、`:83` 赋值 `cycles` 之后):
- 首屏拉取改用 `getCycles(id, { limit: PAGE_SIZE })`
- **`reachedOldest = cycles.length < PAGE_SIZE`** —— 与 `loadOlder` 到顶判定同源:首屏即全量的短会话(如多数 test/sim 短会话)首屏就标到顶,**不显假按钮、不发必然为空的请求**;运行中会话同样正确(顶部增量不改"已到最早一条"这一事实)。
- `loadingOlder = false`(`reachedOldest` 改由首屏长度决定,不再无条件置 `false`)

`clearSelection`(`:151` 起)回 home / 无选中:`loadingOlder = false`、`reachedOldest = false`(无会话、无游标基准)。

### UI 改动(`DecisionStream.vue`)

在 `v-for` 列表(`:23`)**底部**(最早一条之下)、`empty` 提示之前,新增按钮区:
- `cycles.length > 0 && !reachedOldest`:显示「加载更早」按钮,点击调 `store.loadOlder()`
- `loadingOlder`:按钮 `disabled` + 文案改「加载中…」
- `reachedOldest`:不显示按钮,改静态提示「已到最早」
- `cycles.length === 0`:沿用现有 `empty`「暂无决策」,不显示按钮

样式沿用观察台既有令牌(`.ob-*`),不引入新设计令牌。

### 与运行中轮询的关系(已验证无冲突)

- `pollTick` 只用 `after_id` 往**更新**方向追加到数组**顶部**;`loadOlder` 只用 `before_id` 往**更早**方向追加到**底部**。
- 二者均经 `mergeCycles`(Set 去重 + id DESC 排序),幂等且方向无关——同一 cycle 不会重复,顺序始终正确。
- 各自的在途标志(`polling` / `loadingOlder`)独立,并发安全。
- 运行中加载更早后,顶部继续正常增量;`reachedOldest` 只约束「更早」方向,不影响新 cycle 进入。

## 错误与边界

- 网络/后端错误:写 `this.error`,横幅提示;`loadingOlder` 复位,按钮可重试;`reachedOldest` 不置(允许再点)。
- 切会话 / 回 home / 重选同会话:`loadOlder` 用 **`currentId` + `selectSeq` 双守卫**(发起时记 `sid`/`seq`,`await` 后任一不符即丢弃)。`currentId` 防跨会话串档。**`selectSeq` 不可省**——这是与 `pollTick` 的关键差异:`pollTick` 游标 `afterId = cycles[0].id`(**最新**),A→B→A 重选后最新一条不变、游标稳定,迟到响应无害;而 `loadOlder` 游标 `beforeId = cycles.at(-1).id`(**最早**),**深翻多次**后如 `cycles=[331..152]`,在途请求携带深游标 `beforeId=152`——此时 A→B→A 把 `cycles` 复位为首屏 `[331..282]`、丢弃了 281..152 段,迟到响应 `[151..102]` 经 `mergeCycles` 接在 282 之下 → 列表裂出 **281..152 永久空洞**,后续 loadOlder 从 102 续翻永不回填(直到重进会话)。`selectSeq`(每次 `selectSession` `++`)令重入后迟到响应作废、用户在新首屏重翻,根除空洞。注意 `selectSeq` 须**读不自增**(见 Store 改动)。新会话由 `selectSession`/`clearSelection` 重置两状态。
- 恰好剩 50 条:本批返回 50、不置 `reachedOldest`;再点一次返回 0(`< 50`)才置顶——标准分页行为,可接受。
- 空列表:`loadOlder` 守卫直接 return(无 `cycles.at(-1)` 游标)。

## 测试计划(TDD,前端 vitest)

Store(`test/store.spec.ts`):
- `loadOlder` 用最末元 id 作 `beforeId` 请求、`mergeCycles` 正确追加到底部
- 返回 `< PAGE_SIZE` 置 `reachedOldest`;返回 `=== PAGE_SIZE` 不置
- 守卫:`loadingOlder` 在途 / `reachedOldest` 已置 / `cycles` 为空 → 不发请求
- `await` 期间切会话(`currentId` 变更,A→B)→ 丢弃结果(不污染新会话)
- **A→B→A 深翻重入(回归用例)**:深翻多次得 `cycles=[331..152]` → loadOlder 在途(深游标 `beforeId=152`)→ `selectSession` 同会话重选复位为首屏 `[331..282]` → 迟到响应 `[151..102]` 返回 → 断言 `selectSeq` 变更使其被丢弃、**列表无空洞**;去掉 `selectSeq` 守卫则此用例转红(变异验证)
- 错误 → 写 `error`、复位 `loadingOlder`、不置 `reachedOldest`
- `selectSession` 首屏 `< PAGE_SIZE` → `reachedOldest = true`(短会话不显假按钮);首屏 `=== PAGE_SIZE` → `false`;`loadingOlder` 复位
- `clearSelection` 重置 `loadingOlder = false`、`reachedOldest = false`

组件(`test/DecisionStream.spec.ts`,扩充现有文件):
- 三态渲染:有更多时显按钮 / `loadingOlder` 时 disabled+加载中 / `reachedOldest` 时显「已到最早」无按钮
- 空列表不显按钮
- 点击触发 `store.loadOlder()`

## 验收标准

- 打开一个 >50 cycle 的会话(如 sim #22),底部「加载更早」可见;点击后早期 cycle 追加进列表、顺序连续不重复;反复点击直到「已到最早」。
- 打开短会话(≤50 cycle,首屏即全量,如多数 test/sim 短会话):首屏即判定到顶,**不显示**「加载更早」按钮、不发空请求。
- 运行中会话:加载更早后顶部仍正常增量,二者互不干扰。
- 前端 vitest 全绿 + `vue-tsc` 0 + build 绿;真实数据 Playwright 验证(sim #22 加载更早至到顶 / 运行中会话双向并存)。
