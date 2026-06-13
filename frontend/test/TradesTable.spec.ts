import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import TradesTable from "@/components/TradesTable.vue";

describe("TradesTable", () => {
  it("渲染成交行", () => {
    const w = mount(TradesTable, {
      props: { trades: [{ at: "2026-06-12T10:00:00Z", action: "open", side: "long", price: 63000, amount: 1, pnl: 50, fee: 1 }] },
    });
    expect(w.text()).toContain("open");
    expect(w.text()).toContain("63000");
  });
});
