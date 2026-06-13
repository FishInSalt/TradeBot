import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import JsonBlock from "@/components/JsonBlock.vue";

describe("JsonBlock", () => {
  it("dict/list 渲染为格式化 JSON", () => {
    const w = mount(JsonBlock, { props: { value: { a: 1, b: [2, 3] } } });
    expect(w.text()).toContain('"a"');
    expect(w.text()).toContain("2");
  });

  it("string 原样渲染为代码块", () => {
    const w = mount(JsonBlock, { props: { value: "raw broken json {" } });
    expect(w.text()).toContain("raw broken json {");
  });

  it("null 渲染空态占位", () => {
    const w = mount(JsonBlock, { props: { value: null } });
    expect(w.text()).toContain("—");
  });
});
