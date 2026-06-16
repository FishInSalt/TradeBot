<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { createChart, type IChartApi, type ISeriesApi } from "lightweight-charts";
import { NRadioGroup, NRadioButton } from "naive-ui";
import { api, ApiError, type OhlcvBar, type TradeRow } from "@/api/client";
import { deriveTradeFills, type DerivedFill } from "@/utils/trades";
import { toCandleData, snapToBarTime, toMarkers, latestVisibleRange, POS_HEX, NEG_HEX } from "@/utils/markers";
import { epochSec } from "@/utils/time";
import { fmtNum, fmtSigned } from "@/utils/format";

const props = defineProps<{
  sessionId: string;
  symbol: string;
  defaultTimeframe: string;
  trades: TradeRow[];
  latestCycleId: number | null;   // 最新 cycle id（DESC 首元）；变化 → 追平 bar/markers，见 syncSig
}>();

const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;
const FOLD: Record<string, string> = { H: "h", D: "d", W: "w" };
const MIN_BAR_SPACING = 8;        // px：细周期下限，保蜡烛可读（放不下则右锚最新、可左滚）
const MAX_BAR_SPACING = 16;       // px：粗周期上限，防蜡烛膨胀

function normalizeTf(tf: string): string {
  const m = /^(\d+)([a-zA-Z])$/.exec((tf ?? "").trim());
  const folded = m ? `${m[1]}${FOLD[m[2]] ?? m[2]}` : tf;
  return (TIMEFRAMES as readonly string[]).includes(folded) ? folded : "1h";
}

const tf = ref(normalizeTf(props.defaultTimeframe));
const bars = ref<OhlcvBar[]>([]);
const loading = ref(false);
const error = ref(false);
const hover = ref<{ x: number; y: number; fills: DerivedFill[] } | null>(null);

const el = ref<HTMLElement | null>(null);
let chart: IChartApi | null = null;
let series: ISeriesApi<"Candlestick"> | null = null;
let hoverMap = new Map<number, DerivedFill[]>();
let unmounted = false;
let loadSeq = 0;
let pendingFit = false;       // 待决 fit 意图：切 tf/首载请求被 sync 抢占时，由获胜请求认领其视口重置
let renderedCount = 0;        // 上次渲染的 bar 数：sync 前据此 + 当前视口判用户是否贴右边沿（粘性右锚）

// fit=true：首载 / 切 tf —— 重拉并重置视口。fit=false：新 cycle / 新成交追平 —— 重拉补尾部新 bar +
// 重绘 markers；视口默认保留，但用户原本贴右边沿时粘性右锚（让新最新入视野，见 render）。
async function load(fit: boolean) {
  const seq = ++loadSeq;
  if (fit) { pendingFit = true; loading.value = true; error.value = false; hover.value = null; }
  try {
    const s = await api.getOhlcv(props.sessionId, tf.value);
    if (unmounted || seq !== loadSeq) return;          // 已卸载 / 被更新的请求取代 → 丢弃（不消费 pendingFit）
    error.value = false;                               // 任何成功渲染清错误态（对称 loading 解耦）：sync 成功不被陈旧 error 遮罩盖住
    bars.value = s.bars;
    const doFit = pendingFit;                           // 获胜请求认领任何待决 fit（被抢占的切 tf 视口重置不丢）
    pendingFit = false;
    render(doFit);
  } catch (e) {
    if (unmounted || seq !== loadSeq) return;          // 陈旧/卸载后的错误不落地
    // ApiError 与非预期错统一处理；非预期错额外 console.error（不静默吞、也不抛成 unhandled rejection）
    if (!(e instanceof ApiError)) console.error("PriceChart 价格数据加载失败", e);
    if (fit) error.value = true;                       // 仅首载/切 tf 显错误占位；同步失败不毁现有图
  } finally {
    // 解耦 fit：获胜请求（无论 fit 与否）兜底清 loading，避免 fit-load 被抢占后遮罩永久卡死
    if (!unmounted && seq === loadSeq) loading.value = false;
  }
}

function render(fit: boolean) {
  if (!series || !chart) return;
  // 追新前判用户是否原本贴右边沿（末根在视野内）——决定 sync 后是否粘性重锚到新最新
  const prev = chart.timeScale().getVisibleLogicalRange();
  const stick = renderedCount === 0 || (prev != null && prev.to >= renderedCount - 1.5);

  const candles = toCandleData(bars.value);
  const barTimes = candles.map((c) => c.time as number);
  series.setData(candles);
  renderedCount = candles.length;
  const fills = deriveTradeFills(props.trades);
  series.setMarkers(toMarkers(fills, barTimes));
  hoverMap = new Map();
  for (const f of fills) {
    const key = snapToBarTime(epochSec(f.at), barTimes);
    const arr = hoverMap.get(key) ?? [];
    arr.push(f);
    hoverMap.set(key, arr);
  }
  // fit=重置视口；stick=sync 时用户原本贴右沿 → 重锚右展示新 bar/markers；否则保留用户视口
  if (fit || stick) applyViewport(barTimes.length);
}

// 右锚视口：clamp 间距防蜡烛膨胀（含 barCount===1 不被 fitContent 拉满全宽）；
// 放得下则满铺、放不下则展示最新一段（末根贴右，历史可左滚）。width 未布局/无数据 → fitContent 兜底。
function applyViewport(barCount: number) {
  const ts = chart?.timeScale();
  if (!ts) return;
  const range = latestVisibleRange(el.value?.clientWidth ?? 0, barCount, MIN_BAR_SPACING, MAX_BAR_SPACING);
  if (range) ts.setVisibleLogicalRange(range);
  else ts.fitContent();
}

onMounted(() => {
  if (!el.value) return;
  chart = createChart(el.value, {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: "#6b7280" },
    grid: { vertLines: { visible: false }, horzLines: { color: "#e5e7eb" } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, timeVisible: true },
  });
  series = chart.addCandlestickSeries({
    upColor: POS_HEX, downColor: NEG_HEX,
    borderUpColor: POS_HEX, borderDownColor: NEG_HEX,
    wickUpColor: POS_HEX, wickDownColor: NEG_HEX,
  });
  chart.subscribeCrosshairMove((param) => {
    const t = param.time as number | undefined;
    if (t == null || !param.point || !hoverMap.has(t)) { hover.value = null; return; }
    hover.value = { x: param.point.x, y: param.point.y, fills: hoverMap.get(t)! };
  });
  load(true);
});

// 同步信号：仅在「新 cycle」或「成交笔数变化」时追平——避开 5s 轮询每拍换新引用却内容不变的复位。
// 覆盖两类：cycle 推进（可能跨新 bar，即便无成交）+ 成交落地（含 mid-cycle 同 id 下笔数增长）。
const syncSig = computed(() => `${props.latestCycleId ?? ""}:${props.trades.length}`);

watch(tf, () => load(true));                                   // 切 tf：重拉 + 重置视口
watch(syncSig, () => { if (!unmounted) load(false); });        // 追平：重拉 + 重绘，保留视口

onUnmounted(() => {
  unmounted = true;
  chart?.remove();
  chart = null;
  series = null;
});

const sideText = (s: string | null | undefined) => (s === "long" ? "多" : s === "short" ? "空" : "—");
</script>

<template>
  <div class="price-chart-wrap ob-card">
    <div class="pc-head">
      <span class="pc-title">价格走势 · {{ symbol }}</span>
      <n-radio-group v-model:value="tf" size="small">
        <n-radio-button v-for="f in TIMEFRAMES" :key="f" :value="f">{{ f }}</n-radio-button>
      </n-radio-group>
    </div>
    <div class="pc-body">
      <div ref="el" class="pc-canvas"></div>
      <div v-if="loading" class="pc-overlay">加载价格数据…</div>
      <div v-else-if="error" class="pc-overlay">价格数据拉取失败</div>
      <div v-else-if="bars.length === 0" class="pc-overlay">该窗口无行情数据</div>
      <div v-if="hover" class="pc-tip" :style="{ left: hover.x + 'px', top: hover.y + 'px' }">
        <div v-for="(f, i) in hover.fills" :key="i" class="pc-tip-row">
          {{ f.type }} · {{ sideText(f.side) }} · 价 {{ fmtNum(f.price) }} · 量 {{ fmtNum(f.amount, 4) }}
          <template v-if="f.grossPnl != null">
            · 毛利 {{ fmtSigned(f.grossPnl) }}
            <template v-if="f.finalPnl != null"> / 最终 {{ fmtSigned(f.finalPnl) }}</template>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.price-chart-wrap { padding: 8px 12px; }
.pc-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 6px; }
.pc-title { font-size: 12px; color: var(--ob-text-muted); }
.pc-body { position: relative; width: 100%; height: 280px; }
.pc-canvas { width: 100%; height: 100%; }
.pc-overlay {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  color: var(--ob-text-muted); font-size: 13px; background: var(--ob-block-bg);
}
.pc-tip {
  position: absolute; pointer-events: none; transform: translate(8px, 8px); z-index: 2;
  background: var(--ob-block-bg); border: 1px solid var(--ob-border); border-radius: 4px;
  padding: 4px 8px; font-size: 11px; max-width: 320px;
}
.pc-tip-row { white-space: nowrap; }
</style>
