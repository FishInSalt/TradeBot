<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";
import { createChart, type IChartApi, type ISeriesApi } from "lightweight-charts";
import { NRadioGroup, NRadioButton } from "naive-ui";
import { api, ApiError, type OhlcvBar, type TradeRow } from "@/api/client";
import { deriveTradeFills, type DerivedFill } from "@/utils/trades";
import { toCandleData, snapToBarTime, toMarkers, POS_HEX, NEG_HEX } from "@/utils/markers";
import { epochSec } from "@/utils/time";
import { fmtNum, fmtSigned } from "@/utils/format";

const props = defineProps<{
  sessionId: string;
  symbol: string;
  defaultTimeframe: string;
  trades: TradeRow[];
}>();

const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;
const FOLD: Record<string, string> = { H: "h", D: "d", W: "w" };

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

async function load() {
  const seq = ++loadSeq;
  loading.value = true;
  error.value = false;
  hover.value = null;
  try {
    const s = await api.getOhlcv(props.sessionId, tf.value);
    if (unmounted || seq !== loadSeq) return;          // 已卸载 / 被更新的请求取代 → 丢弃本次结果
    bars.value = s.bars;
  } catch (e) {
    if (unmounted || seq !== loadSeq) return;           // 同上：陈旧/卸载后的错误不落地
    if (e instanceof ApiError) error.value = true;
    else throw e;
  } finally {
    if (!unmounted && seq === loadSeq) loading.value = false;   // 仅最新且未卸载才清 loading
  }
}

function render() {
  if (!series) return;
  const candles = toCandleData(bars.value);
  const barTimes = candles.map((c) => c.time as number);
  series.setData(candles);
  const fills = deriveTradeFills(props.trades);
  series.setMarkers(toMarkers(fills, barTimes));
  hoverMap = new Map();
  for (const f of fills) {
    const key = snapToBarTime(epochSec(f.at), barTimes);
    const arr = hoverMap.get(key) ?? [];
    arr.push(f);
    hoverMap.set(key, arr);
  }
  chart?.timeScale().fitContent();
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
  load().then(render);
});

watch(tf, () => load().then(render));
watch(() => props.trades, render, { deep: true });

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
