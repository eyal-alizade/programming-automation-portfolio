"use strict";

const elements = {
  form: document.querySelector("#message-form"),
  input: document.querySelector("#message-input"),
  messages: document.querySelector("#messages"),
  actions: document.querySelector("#quick-actions"),
  tickets: document.querySelector("#ticket-list"),
  notice: document.querySelector("#notice"),
};
let currentRole = "customer";
let busy = false;
let previousMessageKey = "";

const steps = ["name", "need", "contact", "confirm"];
const placeholders = {
  name: "איך קוראים לך או לעסק?",
  need: "מה צריך לפתור?",
  contact: "איך נוח לחזור אליך?",
  confirm: "אפשר לכתוב ״שלח״ לאישור…",
};

function showNotice(message = "") {
  elements.notice.textContent = message;
  elements.notice.hidden = !message;
}

function setBusy(value) {
  busy = value;
  elements.input.disabled = value;
  document.querySelectorAll("button").forEach(button => { button.disabled = value; });
  document.querySelector("#send-button").firstChild.textContent = value ? "רגע… " : "שליחה ";
}

async function request(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "הפעולה לא הושלמה. נסו שוב.");
  return data;
}

function createButton(label, command, className = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  if (className) button.className = className;
  button.addEventListener("click", () => sendMessage(command));
  return button;
}

function renderMessages(messages) {
  const key = `${currentRole}:${messages.at(-1)?.id || 0}`;
  if (key === previousMessageKey) return;
  previousMessageKey = key;
  const fragment = document.createDocumentFragment();
  messages.forEach(message => {
    const bubble = document.createElement("div");
    bubble.className = `message ${message.direction}`;
    const author = document.createElement("span");
    author.className = "message-author";
    author.textContent = message.direction === "bot" ? "דסקבאדי" : currentRole === "admin" ? "מנהל הדגמה" : "לקוח הדגמה";
    bubble.append(author, document.createTextNode(message.text));
    fragment.append(bubble);
  });
  elements.messages.replaceChildren(fragment);
  elements.messages.scrollTop = elements.messages.scrollHeight;
}

function renderActions() {
  const actions = currentRole === "admin"
    ? [["תור בקשות", "/queue"], ["עזרה", "/help"]]
    : [["בקשה חדשה", "/new"], ["הבקשות שלי", "/status"], ["ביטול טיוטה", "/cancel"]];
  elements.actions.replaceChildren(...actions.map(([label, command]) => createButton(label, command)));
}

function renderProgress(step) {
  document.querySelector("#progress-card").hidden = currentRole === "admin";
  const active = steps.indexOf(step);
  document.querySelector("#draft-badge").textContent = active < 0 ? "אין טיוטה פתוחה" : `שלב ${active + 1} מתוך 4`;
  document.querySelectorAll("#progress-list li").forEach((element, index) => {
    element.classList.toggle("current", index === active);
    element.classList.toggle("done", active >= 0 && index < active);
  });
  document.querySelector("#confirm-button").hidden = step !== "confirm";
  document.querySelector("#progress-note").textContent = step === "confirm"
    ? "בודקים שהפרטים נכונים ומאשרים שליחה למנהל."
    : active >= 0 ? "אפשר לעצור בכל רגע עם ״ביטול טיוטה״." : "פותחים בקשה ועונים על כמה שאלות קצרות.";
  elements.input.placeholder = currentRole === "admin" ? "פקודת מנהל או הודעה…" : placeholders[step] || "אפשר להתחיל עם בקשה חדשה…";
}

function renderTicket(ticket) {
  const card = document.createElement("article");
  card.className = "ticket";
  const top = document.createElement("div");
  top.className = "ticket-top";
  const identifier = document.createElement("span");
  identifier.className = "ticket-id";
  identifier.textContent = `בקשה #${ticket.id}`;
  const status = document.createElement("span");
  status.className = `ticket-status ${ticket.status === "closed" ? "closed" : ""}`;
  status.textContent = ticket.status === "open" ? "פתוחה" : "נסגרה";
  top.append(identifier, status);
  const name = document.createElement("h3");
  name.textContent = ticket.name;
  const description = document.createElement("p");
  description.textContent = ticket.need.length > 110 ? ticket.need.slice(0, 110) + "…" : ticket.need;
  const actions = document.createElement("div");
  actions.className = "ticket-actions";
  if (currentRole === "admin") {
    actions.append(createButton("פרטים", `/ticket ${ticket.id}`));
    if (ticket.status === "open") actions.append(createButton("סגירת בקשה", `/close ${ticket.id}`, "close"));
  } else {
    actions.append(createButton("הצגת מצב", `/status ${ticket.id}`));
  }
  card.append(top, name, description, actions);
  return card;
}

function renderTickets(state) {
  document.querySelector("#tickets-title").textContent = currentRole === "admin" ? "תור הבקשות" : "הבקשות שלי";
  document.querySelector("#ticket-count").textContent = state.tickets.length;
  document.querySelector("#open-count").textContent = state.open_count;
  document.querySelector("#closed-count").textContent = state.closed_count;
  if (state.tickets.length) {
    elements.tickets.replaceChildren(...state.tickets.map(renderTicket));
    return;
  }
  const empty = document.createElement("div");
  empty.className = "empty-tickets";
  const icon = document.createElement("span");
  icon.textContent = "▤";
  const text = document.createElement("p");
  text.textContent = currentRole === "admin" ? "עדיין אין בקשות. אפשר לעבור לצד הלקוח ולפתוח אחת." : "עוד אין כאן בקשות. הבקשה הראשונה תופיע אחרי האישור.";
  empty.append(icon, text);
  elements.tickets.replaceChildren(empty);
}

function renderState(state) {
  currentRole = state.role;
  document.querySelectorAll("[data-role]").forEach(button => {
    button.setAttribute("aria-pressed", String(button.dataset.role === currentRole));
  });
  document.querySelector("#chat-title").textContent = currentRole === "admin" ? "עמדת המנהל" : "השיחה שלי";
  document.querySelector("#chat-subtitle").textContent = currentRole === "admin" ? "מנהל הדגמה · תור וטיפול בבקשות" : "לקוח הדגמה · פתיחת בקשה ומעקב";
  renderActions();
  renderMessages(state.messages);
  renderProgress(state.draft_step);
  renderTickets(state);
}

async function sendMessage(text) {
  text = text.trim();
  if (busy || !text) return;
  setBusy(true);
  showNotice();
  try {
    const state = await request("/api/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: currentRole, text, request_id: crypto.randomUUID() }),
    });
    elements.input.value = "";
    renderState(state);
  } catch (error) {
    showNotice(error.message || "לא ניתן להתחבר להדגמה המקומית.");
  } finally {
    setBusy(false);
    elements.input.focus();
  }
}

async function selectRole(role) {
  if (busy || role === currentRole) return;
  setBusy(true);
  showNotice();
  try {
    renderState(await request(`/api/state?role=${role}`));
    elements.input.value = "";
  } catch (error) {
    showNotice(error.message);
  } finally {
    setBusy(false);
  }
}

elements.form.addEventListener("submit", event => { event.preventDefault(); sendMessage(elements.input.value); });
elements.input.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    sendMessage(elements.input.value);
  }
});
document.querySelectorAll("[data-role]").forEach(button => {
  button.addEventListener("click", () => selectRole(button.dataset.role));
});
document.querySelector("#confirm-button").addEventListener("click", () => sendMessage("שלח"));

async function initialize() {
  setBusy(true);
  try {
    renderState(await request("/api/state?role=customer"));
  } catch {
    showNotice("לא ניתן להתחבר להדגמה המקומית. בדקו שהשרת עדיין פועל.");
  } finally {
    setBusy(false);
  }
}

initialize();
