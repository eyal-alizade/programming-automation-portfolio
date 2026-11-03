import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../ui/app.js", import.meta.url), "utf8");
const savedWarning = "הפעולה נשמרה, אבל התצוגה לא התעדכנה. לחצו על רענון נתונים.";

class FakeElement {
  constructor() {
    this.value = "";
    this.textContent = "";
    this.children = [];
    this.dataset = {};
    this.hidden = false;
    this.disabled = false;
    this.resetCount = 0;
    this.closeCount = 0;
    this.classes = new Set();
    this.classList = {
      toggle: (name, enabled) => enabled ? this.classes.add(name) : this.classes.delete(name)
    };
  }
  addEventListener() {}
  replaceChildren(...children) { this.children = children; }
  reset() { this.resetCount += 1; }
  close() { this.closeCount += 1; }
}

function workflowHarness({ mutationFails = false } = {}) {
  const nodes = new Map();
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, new FakeElement());
    return nodes.get(id);
  };
  const calls = { writes: 0, reads: 0, committed: false, addedLines: 0 };
  const document = {
    getElementById: get,
    querySelectorAll: () => []
  };
  const context = vm.createContext({
    document,
    crypto: { randomUUID },
    // Keep initial page bootstrap pending: each test invokes the actual action handler.
    fetch: () => new Promise(() => {}),
    fakeApi: async (_path, options = {}) => {
      if (options.method === "POST") {
        calls.writes += 1;
        if (mutationFails) {
          const error = new Error("Rejected before commit");
          error.status = 409;
          error.detail = "Insufficient stock or stock capacity exceeded";
          throw error;
        }
        calls.committed = true;
        return { id: 19, status: "cancelled" };
      }
      calls.reads += 1;
      throw new Error("Display reload disconnected after the committed write");
    },
    addPresentationLine: () => { calls.addedLines += 1; }
  });
  vm.runInContext(source, context);
  // Stub transport and one leaf rendering function; mutation/retry/refresh logic stays real.
  vm.runInContext("api = fakeApi; addOrderLine = addPresentationLine; state.cancelOrderId = 19;", context);

  get("order-reference").value = "Synthetic workflow test";
  get("order-lines").children = [{ querySelector: selector => ({
    value: selector === ".line-product" ? "1" : "2"
  }) }];
  get("adjust-delta").value = "2";
  get("adjust-reason").value = "Synthetic stock receipt";
  get("adjust-form").dataset.productId = "1";
  get("adjust-form").querySelector = () => get("adjust-submit");
  for (const id of ["notice", "order-error", "adjust-error", "cancel-error"]) get(id).hidden = true;

  return { context, calls, get };
}

const workflows = [
  {
    name: "order creation", handler: "submitOrder({ preventDefault() {} })",
    error: "order-error", button: "create-order", form: "order-form"
  },
  {
    name: "stock adjustment",
    handler: "submitAdjustment({ preventDefault() {}, currentTarget: document.getElementById('adjust-form') })",
    error: "adjust-error", button: "adjust-submit", dialog: "adjust-dialog"
  },
  {
    name: "order cancellation", handler: "cancelOrder()",
    error: "cancel-error", button: "confirm-cancel", dialog: "cancel-dialog"
  }
];

for (const workflow of workflows) {
  test(`${workflow.name}: committed write plus failed reload shows a saved warning`, async () => {
    const harness = workflowHarness();
    await vm.runInContext(workflow.handler, harness.context);
    assert.equal(harness.calls.writes, 1);
    assert.equal(harness.calls.committed, true);
    assert.ok(harness.calls.reads > 0, "The real refresh path was attempted");
    assert.equal(harness.get("notice").textContent, savedWarning);
    assert.equal(harness.get("notice").hidden, false);
    assert.equal(harness.get(workflow.error).hidden, true, "Do not report the committed write as failed");
    assert.equal(harness.get(workflow.button).disabled, false);
    if (workflow.form) assert.equal(harness.get(workflow.form).resetCount, 1);
    if (workflow.dialog) assert.equal(harness.get(workflow.dialog).closeCount, 1);
  });

  test(`${workflow.name}: rejected mutation keeps the form open and does not refresh`, async () => {
    const harness = workflowHarness({ mutationFails: true });
    await vm.runInContext(workflow.handler, harness.context);
    assert.equal(harness.calls.writes, 1);
    assert.equal(harness.calls.committed, false);
    assert.equal(harness.calls.reads, 0);
    assert.equal(harness.get(workflow.error).hidden, false);
    assert.equal(harness.get("notice").hidden, true);
    assert.equal(harness.get(workflow.button).disabled, false);
    if (workflow.form) assert.equal(harness.get(workflow.form).resetCount, 0);
    if (workflow.dialog) assert.equal(harness.get(workflow.dialog).closeCount, 0);
  });
}
