// COSTWISE MARKETPLACE storefront: a tiny hash-routed single-page app over the shop API.
// Every click calls the real API, so a person browsing here adds real load to the pods.

const main = document.getElementById("main");
const servedBy = document.getElementById("served-by");
const cartCount = document.getElementById("cart-count");
const toastEl = document.getElementById("toast");
const INR = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
let categories = [];

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    credentials: "same-origin",
    headers: options.body ? { "Content-Type": "application/json" } : {},
    ...options,
  });
  const pod = res.headers.get("x-served-by");
  if (pod) servedBy.textContent = `served by pod ${pod}`;
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const err = new Error((data && data.detail) || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

function toast(message) {
  toastEl.textContent = message;
  toastEl.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => toastEl.classList.remove("show"), 2200);
}

// ---------------------------------------------------------------- product artwork (generic shapes)
const SHAPES = {
  earbuds: '<circle cx="34" cy="42" r="12"/><circle cx="66" cy="42" r="12"/><rect x="30" y="50" width="8" height="26" rx="4"/><rect x="62" y="50" width="8" height="26" rx="4"/>',
  monitor: '<rect x="12" y="18" width="76" height="48" rx="5"/><rect x="45" y="66" width="10" height="12"/><rect x="32" y="78" width="36" height="5" rx="2"/>',
  keyboard: '<rect x="8" y="34" width="84" height="34" rx="6"/><g fill="#fff" opacity=".7"><rect x="16" y="42" width="8" height="7" rx="1"/><rect x="28" y="42" width="8" height="7" rx="1"/><rect x="40" y="42" width="8" height="7" rx="1"/><rect x="52" y="42" width="8" height="7" rx="1"/><rect x="64" y="42" width="8" height="7" rx="1"/><rect x="76" y="42" width="8" height="7" rx="1"/><rect x="26" y="55" width="48" height="7" rx="1"/></g>',
  speaker: '<rect x="30" y="12" width="40" height="76" rx="18"/><circle cx="50" cy="58" r="12" fill="#fff" opacity=".6"/><circle cx="50" cy="30" r="5" fill="#fff" opacity=".6"/>',
  battery: '<rect x="22" y="16" width="56" height="72" rx="9"/><rect x="38" y="10" width="24" height="8" rx="3"/><path d="M52 32 L40 54 H50 L46 72 L62 46 H52 Z" fill="#fff" opacity=".8"/>',
  camera: '<rect x="12" y="28" width="76" height="52" rx="8"/><rect x="30" y="20" width="22" height="10" rx="3"/><circle cx="50" cy="54" r="16" fill="#fff" opacity=".7"/><circle cx="50" cy="54" r="8"/>',
  phone: '<rect x="30" y="8" width="40" height="84" rx="8"/><rect x="34" y="16" width="32" height="64" rx="3" fill="#fff" opacity=".55"/><circle cx="50" cy="86" r="2.5" fill="#fff"/>',
  tablet: '<rect x="16" y="14" width="68" height="74" rx="8"/><rect x="22" y="20" width="56" height="60" rx="3" fill="#fff" opacity=".55"/>',
  watch: '<rect x="38" y="6" width="24" height="22" rx="4"/><rect x="38" y="72" width="24" height="22" rx="4"/><circle cx="50" cy="50" r="24"/><circle cx="50" cy="50" r="17" fill="#fff" opacity=".65"/><path d="M50 50 V38 M50 50 L58 55" stroke="#123" stroke-width="3" stroke-linecap="round"/>',
  case: '<rect x="28" y="8" width="44" height="84" rx="10"/><circle cx="40" cy="22" r="6" fill="#fff" opacity=".6"/>',
  charger: '<rect x="28" y="30" width="44" height="50" rx="8"/><rect x="38" y="14" width="6" height="18" rx="2"/><rect x="56" y="14" width="6" height="18" rx="2"/>',
  kettle: '<path d="M26 34 H70 L74 84 H22 Z"/><path d="M70 42 C88 42 88 70 72 70" fill="none" stroke-width="7"/><rect x="34" y="22" width="28" height="14" rx="4"/>',
  pot: '<path d="M18 40 H82 V70 C82 80 74 86 64 86 H36 C26 86 18 80 18 70 Z"/><rect x="8" y="40" width="12" height="6" rx="3"/><rect x="80" y="40" width="12" height="6" rx="3"/><rect x="40" y="28" width="20" height="8" rx="4"/>',
  sheet: '<rect x="12" y="26" width="76" height="52" rx="6"/><path d="M12 44 H88 M12 60 H88" stroke="#fff" stroke-width="3" opacity=".6"/>',
  lamp: '<path d="M30 20 H70 L60 48 H40 Z"/><rect x="47" y="48" width="6" height="30"/><rect x="30" y="78" width="40" height="8" rx="4"/>',
  bottle: '<rect x="38" y="8" width="24" height="12" rx="3"/><path d="M34 22 H66 L70 34 V86 C70 90 66 92 62 92 H38 C34 92 30 90 30 86 V34 Z"/><rect x="34" y="46" width="32" height="20" fill="#fff" opacity=".55"/>',
  shoe: '<path d="M10 64 C10 52 20 46 30 44 L44 36 C54 48 66 52 86 56 C92 58 92 72 86 74 H14 C10 74 10 70 10 64 Z"/><rect x="10" y="74" width="80" height="6" rx="3" fill="#fff" opacity=".7"/>',
  shirt: '<path d="M32 14 L18 22 L8 40 L22 46 L26 40 V88 H74 V40 L78 46 L92 40 L82 22 L68 14 C64 22 36 22 32 14 Z"/>',
  bag: '<rect x="20" y="28" width="60" height="62" rx="12"/><path d="M36 30 V22 C36 12 64 12 64 22 V30" fill="none" stroke-width="6"/><rect x="32" y="52" width="36" height="20" rx="4" fill="#fff" opacity=".5"/>',
  scarf: '<path d="M22 18 H78 V36 H54 V88 H38 V36 H22 Z"/><path d="M40 76 H52" stroke="#fff" stroke-width="3" opacity=".7"/>',
  book: '<rect x="22" y="12" width="56" height="76" rx="4"/><rect x="22" y="12" width="9" height="76" fill="#000" opacity=".18"/><rect x="38" y="28" width="32" height="6" rx="2" fill="#fff" opacity=".75"/><rect x="38" y="40" width="24" height="4" rx="2" fill="#fff" opacity=".6"/>',
  mat: '<rect x="10" y="40" width="64" height="28" rx="6"/><circle cx="76" cy="54" r="14"/><circle cx="76" cy="54" r="6" fill="#fff" opacity=".6"/>',
  racket: '<ellipse cx="50" cy="34" rx="22" ry="26"/><ellipse cx="50" cy="34" rx="15" ry="19" fill="#fff" opacity=".55"/><rect x="47" y="58" width="6" height="34" rx="3"/>',
  dumbbell: '<rect x="12" y="34" width="12" height="32" rx="3"/><rect x="76" y="34" width="12" height="32" rx="3"/><rect x="24" y="40" width="8" height="20" rx="2"/><rect x="68" y="40" width="8" height="20" rx="2"/><rect x="32" y="46" width="36" height="8" rx="3"/>',
  shorts: '<path d="M20 18 H80 L86 80 H58 L50 46 L42 80 H14 Z"/>',
  pole: '<rect x="34" y="8" width="6" height="84" rx="3" transform="rotate(-8 37 50)"/><rect x="60" y="8" width="6" height="84" rx="3" transform="rotate(8 63 50)"/>',
  ball: '<circle cx="50" cy="50" r="36"/><path d="M50 30 L62 39 L58 53 H42 L38 39 Z" fill="#fff" opacity=".75"/>',
  drop: '<path d="M50 10 C64 32 76 46 76 62 C76 78 64 90 50 90 C36 90 24 78 24 62 C24 46 36 32 50 10 Z"/><path d="M38 64 C38 72 44 78 50 78" stroke="#fff" stroke-width="4" fill="none" opacity=".7"/>',
  trimmer: '<rect x="36" y="22" width="28" height="70" rx="12"/><rect x="32" y="10" width="36" height="14" rx="3"/><circle cx="50" cy="52" r="5" fill="#fff" opacity=".7"/>',
  leaf: '<path d="M18 80 C18 40 46 16 84 16 C84 54 60 82 18 80 Z"/><path d="M22 76 L66 34" stroke="#fff" stroke-width="3" opacity=".7"/>',
  sack: '<path d="M30 22 H70 L64 30 C80 40 84 60 80 76 C78 86 70 90 50 90 C30 90 22 86 20 76 C16 60 20 40 36 30 Z"/><rect x="36" y="18" width="28" height="6" rx="2"/>',
  jar: '<rect x="30" y="12" width="40" height="12" rx="3"/><rect x="24" y="24" width="52" height="66" rx="12"/><rect x="32" y="44" width="36" height="22" rx="3" fill="#fff" opacity=".6"/>',
};

function art(product, extraClass = "") {
  const hue = Number(product.hue) || 200;
  const shape = SHAPES[product.art] || SHAPES.jar;
  return `<div class="art ${extraClass}" style="background: hsl(${hue} 70% 94%)" aria-hidden="true">
    <svg viewBox="0 0 100 100" fill="hsl(${hue} 55% 42%)" stroke="hsl(${hue} 55% 42%)">${shape}</svg></div>`;
}

const stars = (r) => "★".repeat(Math.round(r)) + "☆".repeat(5 - Math.round(r));

function card(p) {
  return `<a class="card" href="#/p/${p.id}">
    ${art(p)}
    ${p.deal ? '<span class="tag">Deal</span>' : p.bestseller ? '<span class="tag best">Bestseller</span>' : ""}
    <span class="name">${esc(p.name)}</span>
    <span class="brandline">${esc(p.brand)}</span>
    <span class="rating" aria-label="Rated ${p.rating} out of 5">${stars(p.rating)} <span>${p.rating} (${p.reviews.toLocaleString("en-IN")})</span></span>
    <span class="price"><b>${INR.format(p.price)}</b>${p.discount_pct ? `<s>${INR.format(p.mrp)}</s><span class="off">${p.discount_pct}% off</span>` : ""}</span>
  </a>`;
}

const grid = (items) => items.length ? `<div class="grid">${items.map(card).join("")}</div>` : '<p class="empty">No products found.</p>';
const loading = () => { main.innerHTML = '<p class="empty">Loading…</p>'; };
function showError(err) {
  main.innerHTML = `<div class="error-box"><b>Something went wrong.</b> ${esc(err.message)}
    ${err.status === 503 ? "<br>The store is very busy right now (the pods are overloaded). CostWise should add capacity shortly — please retry." : ""}</div>`;
}

async function refreshCartCount() {
  try { cartCount.textContent = (await api("/cart")).count; } catch { /* ignore */ }
}

// ---------------------------------------------------------------- pages
async function home() {
  const [deals, featured] = await Promise.all([api("/deals"), api("/products?sort=rating&limit=8")]);
  main.innerHTML = `
    <section class="hero">
      <div class="hero-main">
        <h1>Big savings, every day.</h1>
        <p>Thousands of shoppers can arrive at once during a flash sale. This store runs on Kubernetes, and CostWise adds or removes pods before the rush hits.</p>
        <a class="btn btn-primary" href="#/deals">Shop today's deals</a>
      </div>
      <div class="hero-side">
        <h2>Deals end in <span class="countdown" id="countdown">--:--</span></h2>
        <p class="muted">${deals.items.length} products at 20% off or more.</p>
        <a class="btn btn-dark" href="#/deals">See all deals</a>
      </div>
    </section>
    <div class="section-head"><h2>Shop by category</h2></div>
    <div class="cat-tiles">${categories.map((c) => `<a class="cat-tile" href="#/c/${c.id}">
      <span class="dot" style="background:hsl(${c.hue} 70% 92%)">${art({ hue: c.hue, art: c.icon === "chip" ? "monitor" : c.icon === "home" ? "lamp" : c.icon })
        .replace('class="art "', 'class="art" style="width:34px;height:34px;background:none"')}</span>
      <b>${esc(c.name)}</b><span class="muted">${c.products} products</span></a>`).join("")}</div>
    <div class="section-head"><h2>Top deals</h2><a href="#/deals">View all</a></div>
    ${grid(deals.items.slice(0, 8))}
    <div class="section-head"><h2>Highly rated</h2><a href="#/c/electronics">Browse</a></div>
    ${grid(featured.items)}`;
  startCountdown(deals.ends_at);
}

function startCountdown(endsAt) {
  clearInterval(startCountdown.timer);
  const tick = () => {
    const el = document.getElementById("countdown");
    if (!el) return clearInterval(startCountdown.timer);
    const left = Math.max(0, endsAt - Date.now() / 1000);
    el.textContent = `${String(Math.floor(left / 60)).padStart(2, "0")}:${String(Math.floor(left % 60)).padStart(2, "0")}`;
  };
  tick();
  startCountdown.timer = setInterval(tick, 1000);
}

async function category(id, sort = "featured") {
  const data = await api(`/products?category=${encodeURIComponent(id)}&sort=${sort}&limit=48`);
  const cat = categories.find((c) => c.id === id);
  main.innerHTML = `<div class="toolbar"><h1 style="margin:0">${esc(cat ? cat.name : id)}</h1>
    <label>Sort by <select id="sort">
      ${[["featured", "Featured"], ["price_asc", "Price: low to high"], ["price_desc", "Price: high to low"], ["rating", "Customer rating"], ["discount", "Biggest discount"]]
        .map(([v, l]) => `<option value="${v}" ${v === sort ? "selected" : ""}>${l}</option>`).join("")}
    </select></label></div>${grid(data.items)}`;
  document.getElementById("sort").addEventListener("change", (e) => category(id, e.target.value));
}

async function deals() {
  const data = await api("/deals");
  main.innerHTML = `<div class="toolbar"><h1 style="margin:0">Today's deals</h1>
    <span>Ends in <span class="countdown" id="countdown">--:--</span></span></div>${grid(data.items)}`;
  startCountdown(data.ends_at);
}

async function searchPage(q) {
  document.getElementById("search-input").value = q;
  const data = await api(`/search?q=${encodeURIComponent(q)}`);
  main.innerHTML = `<h1>Results for “${esc(q)}”</h1><p class="muted">${data.total} products</p>${grid(data.items)}`;
}

async function product(id) {
  const { product: p, related } = await api(`/products/${encodeURIComponent(id)}`);
  main.innerHTML = `
    <div class="detail">
      ${art(p)}
      <div>
        <a href="#/c/${p.category}" class="muted">${esc((categories.find((c) => c.id === p.category) || {}).name || p.category)}</a>
        <h1>${esc(p.name)}</h1>
        <div class="brandline">by ${esc(p.brand)}</div>
        <div class="rating">${stars(p.rating)} <span>${p.rating} · ${p.reviews.toLocaleString("en-IN")} ratings</span></div>
        <div class="price" style="margin:12px 0"><b>${INR.format(p.price)}</b>${p.discount_pct ? `<s>M.R.P. ${INR.format(p.mrp)}</s><span class="off">${p.discount_pct}% off</span>` : ""}</div>
        <p>${esc(p.description)}</p>
        <ul class="features">${p.features.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>
      </div>
      <aside class="buybox">
        <div class="price"><b>${INR.format(p.price)}</b></div>
        <div class="stock">${p.stock > 10 ? "In stock" : `Only ${p.stock} left`}</div>
        <label class="qty">Quantity <select id="qty">${[1, 2, 3, 4, 5].map((n) => `<option>${n}</option>`).join("")}</select></label>
        <button class="btn btn-primary" id="add">Add to cart</button>
        <button class="btn btn-dark" id="buy">Buy now</button>
        <span class="muted" style="font-size:13px">Simulated store — no payment is taken.</span>
      </aside>
    </div>
    <div class="section-head"><h2>Related products</h2></div>${grid(related)}`;
  const add = async (go) => {
    const quantity = Number(document.getElementById("qty").value);
    const cart = await api("/cart", { method: "POST", body: JSON.stringify({ product_id: p.id, quantity }) });
    cartCount.textContent = cart.count;
    toast(`Added ${quantity} × ${p.name} to your cart`);
    if (go) location.hash = "#/checkout";
  };
  document.getElementById("add").addEventListener("click", () => add(false).catch(showError));
  document.getElementById("buy").addEventListener("click", () => add(true).catch(showError));
}

function summaryBox(cart, button) {
  return `<aside class="summary">
    <div class="row"><span>Items (${cart.count})</span><span>${INR.format(cart.subtotal)}</span></div>
    <div class="row"><span>Delivery</span><span>${cart.delivery ? INR.format(cart.delivery) : "FREE"}</span></div>
    <div class="row total"><span>Total</span><span>${INR.format(cart.total)}</span></div>${button}</aside>`;
}

async function cartPage() {
  const cart = await api("/cart");
  cartCount.textContent = cart.count;
  if (!cart.items.length) {
    main.innerHTML = '<div class="empty"><h1>Your cart is empty</h1><a class="btn btn-primary" href="#/deals">Find a deal</a></div>';
    return;
  }
  main.innerHTML = `<h1>Shopping cart</h1><div class="cart-layout"><div>${cart.items.map((l) => `
    <div class="cart-line">${art(l.product)}<div><a href="#/p/${l.product.id}"><b>${esc(l.product.name)}</b></a>
      <div class="muted">${INR.format(l.product.price)} each</div>
      <label class="qty">Qty <select data-pid="${l.product.id}">${[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) =>
        `<option value="${n}" ${n === l.quantity ? "selected" : ""}>${n === 0 ? "Remove" : n}</option>`).join("")}</select></label></div>
      <b>${INR.format(l.line_total)}</b></div>`).join("")}</div>
    ${summaryBox(cart, '<a class="btn btn-primary" style="width:100%;justify-content:center;margin-top:10px" href="#/checkout">Proceed to checkout</a>')}</div>`;
  main.querySelectorAll("select[data-pid]").forEach((sel) => sel.addEventListener("change", async () => {
    await api("/cart", { method: "POST", body: JSON.stringify({ product_id: Number(sel.dataset.pid), quantity: Number(sel.value), mode: "set" }) });
    cartPage().catch(showError);
  }));
}

async function checkoutPage() {
  const cart = await api("/cart");
  if (!cart.items.length) { location.hash = "#/cart"; return; }
  main.innerHTML = `<h1>Checkout</h1><div class="cart-layout">
    <form class="checkout" id="checkout-form">
      <label>Full name <input name="name" required maxlength="60" value="Demo Shopper"></label>
      <label>Delivery address <textarea name="address" rows="3" maxlength="200">12 Demo Street, Bengaluru 560001</textarea></label>
      <label>Payment method <select name="payment"><option value="upi">UPI (simulated)</option><option value="card">Card (simulated)</option><option value="cod">Cash on delivery</option></select></label>
      <p class="muted">This is a simulated checkout. No payment details are requested or stored.</p>
    </form>
    ${summaryBox(cart, '<button class="btn btn-primary" style="width:100%;justify-content:center;margin-top:10px" id="place">Place order</button>')}</div>`;
  document.getElementById("place").addEventListener("click", async (e) => {
    const form = document.getElementById("checkout-form");
    if (!form.reportValidity()) return;
    e.target.disabled = true;
    try {
      const order = await api("/checkout", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(form))) });
      sessionStorage.setItem(`order-${order.order_id}`, JSON.stringify(order));
      cartCount.textContent = 0;
      location.hash = `#/order/${order.order_id}`;
    } catch (err) { e.target.disabled = false; showError(err); }
  });
}

function orderPage(id) {
  let order = null;
  try { order = JSON.parse(sessionStorage.getItem(`order-${id}`)); } catch { /* storage unavailable */ }
  if (!order) { main.innerHTML = `<div class="confirm"><h1>Order ${esc(id)}</h1><p>See <a href="#/orders">your orders</a>.</p></div>`; return; }
  main.innerHTML = `<div class="confirm"><div class="tick" aria-hidden="true">✓</div>
    <h1>Thank you, your order is confirmed</h1>
    <p>Order <b>${esc(order.order_id)}</b> · ${order.count} item(s) · total <b>${INR.format(order.total)}</b></p>
    <p>Estimated delivery in ${order.estimated_delivery_days} days.</p>
    <p class="muted">${esc(order.note)} Processed by pod ${esc(order.served_by)}.</p>
    <a class="btn btn-primary" href="#/">Continue shopping</a> <a class="btn btn-ghost" href="#/orders">Your orders</a></div>`;
}

async function ordersPage() {
  const data = await api("/orders");
  main.innerHTML = `<h1>Your orders</h1><p class="muted">${esc(data.note)}</p>${data.items.length ? data.items.map((o) => `
    <div class="cart-line" style="grid-template-columns:1fr auto"><div><b>${esc(o.order_id)}</b>
      <div class="muted">${new Date(o.placed_at * 1000).toLocaleString()} · ${o.count} item(s) · ${esc(o.status)}</div></div>
      <b>${INR.format(o.total)}</b></div>`).join("") : '<p class="empty">No orders found on this pod yet.</p>'}`;
}

// ---------------------------------------------------------------- router
async function route() {
  const hash = location.hash.slice(1) || "/";
  const [path, query = ""] = hash.split("?");
  const parts = path.split("/").filter(Boolean);
  document.querySelectorAll("#cat-nav a").forEach((a) => a.classList.toggle("active", a.getAttribute("href") === `#${path}`));
  loading();
  window.scrollTo(0, 0);
  try {
    if (!parts.length) await home();
    else if (parts[0] === "c") await category(parts[1]);
    else if (parts[0] === "p") await product(parts[1]);
    else if (parts[0] === "deals") await deals();
    else if (parts[0] === "search") await searchPage(new URLSearchParams(query).get("q") || "");
    else if (parts[0] === "cart") await cartPage();
    else if (parts[0] === "checkout") await checkoutPage();
    else if (parts[0] === "order") orderPage(parts[1]);
    else if (parts[0] === "orders") await ordersPage();
    else main.innerHTML = '<div class="empty"><h1>Page not found</h1><a href="#/">Go home</a></div>';
  } catch (err) { showError(err); }
  main.focus({ preventScroll: true });
}

document.getElementById("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = document.getElementById("search-input").value.trim();
  if (q) location.hash = `#/search?q=${encodeURIComponent(q)}`;
});
window.addEventListener("hashchange", route);

(async () => {
  try {
    categories = (await api("/categories")).items;
    document.getElementById("cat-nav").innerHTML = '<a href="#/deals">Today\'s deals</a>' +
      categories.map((c) => `<a href="#/c/${c.id}">${esc(c.name)}</a>`).join("");
  } catch (err) { /* shown by route() */ }
  refreshCartCount();
  route();
})();
