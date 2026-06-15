import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import InjectionCard from "@/components/InjectionCard.vue";

// 2026-06-14T14:51:08Z 的 epoch ms（月份 5 = June）
const TS = Date.UTC(2026, 5, 14, 14, 51, 8);

describe("InjectionCard", () => {
  it("波动告警摘要：基名 + 窗口 + 带号百分比 + 参考价→现价", () => {
    const inj = { kind_label: "波动告警触发", triggered_ago: "1 min ago", offset_ms: 40312,
      event: { type: "percentage_alert", symbol: "BTC/USDT:USDT", window_minutes: 15,
        change_pct: 0.4076887, reference_price: 63823.2, current_price: 64083.4, timestamp: TS } };
    const w = mount(InjectionCard, { props: { inj: inj as any } });
    const txt = w.text();
    expect(txt).toContain("波动告警触发");        // 后端 kind_label 作标题
    expect(txt).toContain("BTC");                 // 基名（去 /USDT:USDT）
    expect(txt).toContain("15min 窗口");
    expect(txt).toContain("+0.41%");
    expect(txt).toContain("63,823.2");
    expect(txt).toContain("64,083.4");
    expect(txt).toContain("1 min ago");           // age 片
    expect(txt).toContain("触发于 14:51:08");      // UTC 时分秒
    expect(txt).not.toContain("40312");           // 去掉 offset_ms 显示
  });

  it("成交摘要：方向 + 张数@价 + 盈亏（红绿）+ 手续费", () => {
    const inj = { kind_label: "止损平仓", triggered_ago: "1 min ago", offset_ms: 25900,
      event: { type: "fill", position_side: "short", amount: 46.37, fill_price: 64280,
        pnl: -103.59, fee: 29.81, timestamp: TS } };
    const w = mount(InjectionCard, { props: { inj: inj as any } });
    const txt = w.text();
    expect(txt).toContain("止损平仓");
    expect(txt).toContain("空");
    expect(txt).toContain("46.37 张");
    expect(txt).toContain("@64,280");
    expect(txt).toContain("盈亏");
    expect(txt).toContain("−103.59");             // U+2212 负号
    expect(txt).toContain("手续费 29.81 USDT");
    expect(w.find(".neg").exists()).toBe(true);   // 负盈亏红字
  });

  it("成交摘要：pnl 缺省（开仓 fill）不渲盈亏段", () => {
    const inj = { kind_label: "限价开多",
      event: { type: "fill", position_side: "long", amount: 1, fill_price: 63000, pnl: null, fee: 0.5, timestamp: TS } };
    const w = mount(InjectionCard, { props: { inj: inj as any } });
    expect(w.text()).not.toContain("盈亏");
  });

  it("价格告警摘要：方向 @目标价（现价）+ reasoning 次行", () => {
    const inj = { kind_label: "价格告警触发", triggered_ago: "45 sec ago",
      event: { type: "price_level_alert", direction: "above", target_price: 63668,
        current_price: 63669, reasoning: "20:00 candle high", timestamp: TS } };
    const w = mount(InjectionCard, { props: { inj: inj as any } });
    const txt = w.text();
    expect(txt).toContain("上破");
    expect(txt).toContain("@63,668");
    expect(txt).toContain("现价 63,669");
    expect(txt).toContain("20:00 candle high");
  });

  it("triggered_ago=null 不渲 age 片", () => {
    const inj = { kind_label: "波动告警触发", triggered_ago: null,
      event: { type: "percentage_alert", symbol: "BTC/USDT:USDT", window_minutes: 15,
        change_pct: 0.4, reference_price: 1, current_price: 2, timestamp: TS } };
    const w = mount(InjectionCard, { props: { inj: inj as any } });
    expect(w.find(".inj-age").exists()).toBe(false);
  });

  it("原始 JSON 默认折叠，点击展开 JsonBlock", async () => {
    const inj = { kind_label: "止损平仓",
      event: { type: "fill", position_side: "short", amount: 1, fill_price: 64280, pnl: -10, fee: 1,
        order_id: "ord-zzz", timestamp: TS } };
    const w = mount(InjectionCard, { props: { inj: inj as any } });
    expect(w.text()).not.toContain("ord-zzz");          // 折叠态不渲原文
    await w.find(".inj-raw-toggle").trigger("click");
    expect(w.text()).toContain("ord-zzz");              // 展开后 JsonBlock 含 order_id
  });
});
