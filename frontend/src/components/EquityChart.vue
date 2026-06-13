<script lang="ts">
import type { EquityPoint } from "@/api/client";
import { epochSec } from "@/utils/time";
import type { UTCTimestamp } from "lightweight-charts";

/** 逐 cycle 盯市点 → lightweight-charts line data。秒级 UTCTimestamp、升序、同秒去重保留最后。 */
export function toSeriesData(points: EquityPoint[]): { time: UTCTimestamp; value: number }[] {
  const byTime = new Map<number, number>();
  for (const p of points) byTime.set(epochSec(p.at), p.equity);
  return [...byTime.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([time, value]) => ({ time: time as UTCTimestamp, value }));
}
</script>

<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";
import { createChart, type IChartApi, type ISeriesApi } from "lightweight-charts";

const props = defineProps<{ points: EquityPoint[] }>();
const el = ref<HTMLElement | null>(null);
let chart: IChartApi | null = null;
let series: ISeriesApi<"Line"> | null = null;

function render() {
  if (series) series.setData(toSeriesData(props.points));
  chart?.timeScale().fitContent();
}

onMounted(() => {
  if (!el.value) return;
  chart = createChart(el.value, {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: "#9ca3af" },
    grid: { vertLines: { visible: false }, horzLines: { color: "rgba(255,255,255,0.05)" } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, timeVisible: true },
  });
  series = chart.addLineSeries({ color: "#4ade80", lineWidth: 2 });
  render();
});

watch(() => props.points, render, { deep: true });

onUnmounted(() => {
  chart?.remove();
  chart = null;
  series = null;
});
</script>

<template>
  <div ref="el" class="equity-chart"></div>
</template>

<style scoped>
.equity-chart { width: 100%; height: 120px; }
</style>
