"use strict";

const state = { products: [], orders: [], key: null, cancelOrderId: null };
const pendingMutations = new Map();
const money = new Intl.NumberFormat("he-IL", { style: "currency", currency: "ILS" });
const dateTime = new Intl.DateTimeFormat("he-IL", {
  day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"
});

function byId(id) { return document.getElementById(id); }
function orderNumber(id) { return `#${String(id).padStart(4, "0")}`; }
function price(cents) { return money.format(cents / 100); }

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function readableError(error) {
  if (error.status === 409 && error.detail?.includes("stock")) {
    return "אין מספיק מלאי זמין לכמות שבחרתם. עדכנו את הכמות ונסו שוב.";
  }
  if (error.status === 422) return "בדקו שנבחר מוצר ושהכמויות הן מספרים שלמים וחיוביים.";
  if (error.status === 401) return "סביבת ההדגמה השתנתה. רעננו את הדף ונסו שוב.";
  if (error.status === 404) return "הפריט המבוקש לא נמצא. רעננו את הנתונים.";
  if (error.status === 409) return "הפעולה לא הושלמה. רעננו את הנתונים ונסו שוב.";
  if (error.status === 503) return "המערכת עסוקה כרגע. המתינו רגע ונסו שוב.";
  return "לא הצלחנו להשלים את הפעולה. בדקו שההדגמה פתוחה ונסו שוב.";
}

function setError(id, error) {
  const target = byId(id);
  target.textContent = typeof error === "string" ? error : readableError(error);
  target.hidden = !error;
}

function notify(text, isError = false) {
  const notice = byId("notice");
  notice.textContent = text;
  notice.classList.toggle("error", isError);
  notice.hidden = false;
}

async function api(path, options = {}) {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers: { "X-API-Key": state.key, "Content-Type": "application/json", ...options.headers }
  });
  const data = await response.json();
  if (!response.ok) {
    const error = new Error("Request failed");
    error.status = response.status;
    error.detail = typeof data.detail === "string" ? data.detail : "";
    throw error;
  }
  return data;
}

async function mutateOnce(path, body) {
  // Keep the same key after a lost response. A retry must not deduct stock twice.
  const payload = JSON.stringify(body);
  const fingerprint = `${path}|${payload}`;
  const key = pendingMutations.get(fingerprint) || crypto.randomUUID();
  pendingMutations.set(fingerprint, key);
  try {
    const result = await api(path, {
      method: "POST", body: payload, headers: { "Idempotency-Key": key }
    });
    pendingMutations.delete(fingerprint);
    return result;
  } catch (error) {
    if (error.status >= 400 && error.status < 500) pendingMutations.delete(fingerprint);
    throw error;
  }
}

function actionButton(label, callback, className = "text-button") {
  const button = element("button", className, label);
  button.type = "button";
  button.addEventListener("click", callback);
  return button;
}

function visibleProducts() {
  const query = byId("product-search").value.trim().toLocaleLowerCase();
  return state.products.filter(product => {
    const matches = `${product.name} ${product.sku}`.toLocaleLowerCase().includes(query);
    return matches && (!byId("low-only").checked || product.available <= 5);
  });
}

function renderProducts() {
  const body = byId("products-body");
  body.replaceChildren();
  const products = visibleProducts();
  for (const product of products) {
    const row = body.insertRow();
    const description = row.insertCell();
    description.append(element("span", "product-name", product.name));
    description.append(element("span", "product-sku", product.sku));
    row.insertCell().textContent = price(product.price_cents);
    row.insertCell().append(element("span", "stock-number", product.available));
    const low = product.available <= 5;
    row.insertCell().append(element("span", `pill${low ? " low" : ""}`,
      product.available === 0 ? "אזל מהמלאי" : low ? "מלאי נמוך" : "במלאי"));
    const actions = element("div", "row-actions");
    actions.append(actionButton("עדכון", () => openAdjustment(product)));
    actions.append(actionButton("תנועות", () => openLedger(product.id), "text-button muted"));
    row.insertCell().append(actions);
  }
  if (!products.length) {
    const cell = body.insertRow().insertCell();
    cell.colSpan = 5;
    cell.className = "empty";
    cell.textContent = "לא נמצאו מוצרים התואמים לחיפוש.";
  }
  byId("products-shown").textContent = `${products.length} מתוך ${state.products.length} מוצרים`;
}

function renderOverview() {
  byId("product-count").textContent = state.products.length;
  byId("stock-count").textContent = state.products.reduce((sum, product) => sum + product.available, 0);
  byId("order-count").textContent = state.orders.filter(order => order.status === "reserved").length;
  const low = state.products.filter(product => product.available <= 5).length;
  byId("low-summary").textContent = low ? `${low} מוצרים דורשים תשומת לב למלאי` : "כל המוצרים עם מלאי זמין";
}

function renderOrders() {
  const body = byId("orders-body");
  body.replaceChildren();
  const filter = byId("order-filter").value;
  const orders = state.orders.filter(order => filter === "all" || order.status === filter);
  for (const order of orders) {
    const row = body.insertRow();
    row.insertCell().append(element("span", "order-number", orderNumber(order.id)));
    row.insertCell().textContent = order.customer_reference || "ללא אסמכתה";
    row.insertCell().textContent = dateTime.format(new Date(order.created_at));
    row.insertCell().textContent = price(order.total_cents);
    const cancelled = order.status === "cancelled";
    row.insertCell().append(element("span", `pill${cancelled ? " cancelled" : ""}`,
      cancelled ? "בוטלה · המלאי הוחזר" : "פעילה · המלאי שמור"));
    const actions = row.insertCell();
    if (!cancelled) actions.append(actionButton("ביטול הזמנה", () => openCancellation(order), "text-button red"));
    else actions.textContent = "—";
  }
  if (!orders.length) {
    const cell = body.insertRow().insertCell();
    cell.colSpan = 6;
    cell.className = "empty";
    cell.textContent = "אין הזמנות להצגה בסינון הנוכחי.";
  }
}

function fillProductOptions(select, selectedId = select.value) {
  select.replaceChildren();
  for (const product of state.products) {
    const option = element("option", "", `${product.name} · ${product.available} זמינים`);
    option.value = product.id;
    select.append(option);
  }
  if (state.products.some(product => String(product.id) === String(selectedId))) select.value = selectedId;
}

function addOrderLine() {
  const line = byId("order-line-template").content.firstElementChild.cloneNode(true);
  const select = line.querySelector(".line-product");
  fillProductOptions(select);
  const selected = new Set([...byId("order-lines").querySelectorAll(".line-product")].map(item => item.value));
  const unused = state.products.find(product => !selected.has(String(product.id)));
  if (unused) select.value = unused.id;
  line.addEventListener("input", renderOrderTotal);
  line.querySelector(".remove-line").addEventListener("click", () => {
    if (byId("order-lines").children.length > 1) line.remove();
    renderOrderTotal();
  });
  byId("order-lines").append(line);
  renderOrderTotal();
}

function orderItems() {
  return [...byId("order-lines").children].map(line => ({
    product_id: Number(line.querySelector(".line-product").value),
    quantity: Number(line.querySelector(".line-quantity").value)
  }));
}

function renderOrderTotal() {
  const total = orderItems().reduce((sum, item) => {
    const product = state.products.find(product => product.id === item.product_id);
    return sum + (product?.price_cents || 0) * (Number.isFinite(item.quantity) ? item.quantity : 0);
  }, 0);
  byId("order-total").textContent = price(total);
}

async function refreshData() {
  const [products, orders] = await Promise.all([api("/products?limit=100"), api("/orders?limit=100")]);
  state.products = products.items;
  state.orders = orders.items;
  renderProducts();
  renderOrders();
  renderOverview();
  for (const select of document.querySelectorAll(".line-product")) fillProductOptions(select);
  renderOrderTotal();
}

async function refreshAfterSavedAction(successMessage) {
  notify(successMessage);
  try {
    await refreshData();
  } catch (_error) {
    notify("הפעולה נשמרה, אבל התצוגה לא התעדכנה. לחצו על רענון נתונים.", true);
  }
}

async function submitOrder(event) {
  event.preventDefault();
  setError("order-error", "");
  const items = orderItems();
  if (new Set(items.map(item => item.product_id)).size !== items.length) {
    setError("order-error", "כל מוצר יכול להופיע פעם אחת. עדכנו את הכמות בשורה הקיימת.");
    return;
  }
  const button = byId("create-order");
  button.disabled = true;
  try {
    let order;
    try {
      order = await mutateOnce("/orders", {
        customer_reference: byId("order-reference").value.trim(), items
      });
    } catch (error) {
      setError("order-error", error);
      return;
    }
    byId("order-form").reset();
    byId("order-lines").replaceChildren();
    addOrderLine();
    await refreshAfterSavedAction(`הזמנה ${orderNumber(order.id)} נוצרה. הפריטים נשמרו מהמלאי הזמין.`);
  } finally {
    button.disabled = false;
  }
}

function openAdjustment(product) {
  byId("adjust-form").reset();
  byId("adjust-form").dataset.productId = product.id;
  byId("adjust-product").textContent = `${product.name} · ${product.available} יחידות זמינות`;
  setError("adjust-error", "");
  byId("adjust-dialog").showModal();
}

async function submitAdjustment(event) {
  event.preventDefault();
  const delta = Number(byId("adjust-delta").value);
  if (!Number.isInteger(delta) || delta === 0) {
    setError("adjust-error", "יש להזין מספר שלם שאינו אפס. מספר שלילי מפחית מלאי.");
    return;
  }
  const button = event.currentTarget.querySelector("button[type='submit']");
  button.disabled = true;
  try {
    try {
      await mutateOnce(`/products/${byId("adjust-form").dataset.productId}/adjustments`, {
        delta, reason: byId("adjust-reason").value.trim()
      });
    } catch (error) {
      setError("adjust-error", error);
      return;
    }
    byId("adjust-dialog").close();
    await refreshAfterSavedAction("המלאי עודכן והשינוי נוסף ליומן התנועות.");
  } finally {
    button.disabled = false;
  }
}

function openCancellation(order) {
  state.cancelOrderId = order.id;
  byId("cancel-description").textContent = `לבטל את הזמנה ${orderNumber(order.id)} — ${order.customer_reference || "ללא אסמכתה"}?`;
  setError("cancel-error", "");
  byId("cancel-dialog").showModal();
}

async function cancelOrder() {
  const button = byId("confirm-cancel");
  const orderId = state.cancelOrderId;
  button.disabled = true;
  try {
    try {
      await api(`/orders/${orderId}/cancel`, { method: "POST" });
    } catch (error) {
      setError("cancel-error", error);
      return;
    }
    byId("cancel-dialog").close();
    await refreshAfterSavedAction(`הזמנה ${orderNumber(orderId)} בוטלה. הפריטים הוחזרו למלאי.`);
  } finally {
    button.disabled = false;
  }
}

async function openLedger(productId = state.products[0]?.id) {
  fillProductOptions(byId("ledger-product"), productId);
  byId("ledger-dialog").showModal();
  await loadLedger();
}

async function loadLedger() {
  setError("ledger-error", "");
  const labels = { opening: "מלאי פתיחה", adjustment: "עדכון מלאי", reserve: "שמירה להזמנה", release: "החזרה מביטול" };
  try {
    const data = await api(`/products/${byId("ledger-product").value}/ledger`);
    const body = byId("ledger-body");
    body.replaceChildren();
    for (const movement of data.items) {
      const row = body.insertRow();
      const description = row.insertCell();
      description.textContent = labels[movement.kind];
      if (movement.order_id) description.append(element("small", "product-sku", `הזמנה ${orderNumber(movement.order_id)}`));
      const delta = row.insertCell();
      delta.textContent = movement.delta > 0 ? `+${movement.delta}` : movement.delta;
      delta.dir = "ltr";
      row.insertCell().textContent = movement.balance_after;
      row.insertCell().textContent = dateTime.format(new Date(movement.created_at));
    }
  } catch (error) {
    setError("ledger-error", error);
  }
}

function connectControls() {
  byId("product-search").addEventListener("input", renderProducts);
  byId("low-only").addEventListener("change", renderProducts);
  byId("order-filter").addEventListener("change", renderOrders);
  byId("add-line").addEventListener("click", addOrderLine);
  byId("order-form").addEventListener("submit", submitOrder);
  byId("adjust-form").addEventListener("submit", submitAdjustment);
  byId("confirm-cancel").addEventListener("click", cancelOrder);
  byId("open-ledger").addEventListener("click", () => openLedger());
  byId("ledger-product").addEventListener("change", loadLedger);
  byId("refresh").addEventListener("click", async () => {
    try { await refreshData(); notify("הנתונים מעודכנים."); }
    catch (error) { notify(readableError(error), true); }
  });
  for (const button of document.querySelectorAll("[data-close]")) {
    button.addEventListener("click", () => byId(button.dataset.close).close());
  }
}

async function initialize() {
  connectControls();
  try {
    const response = await fetch("/demo/session", { cache: "no-store" });
    const session = await response.json();
    if (!response.ok || session.synthetic_data !== true) throw new Error("Demo unavailable");
    state.key = session.api_key;
    await refreshData();
    addOrderLine();
  } catch (error) {
    notify(readableError(error), true);
  }
}

initialize();
