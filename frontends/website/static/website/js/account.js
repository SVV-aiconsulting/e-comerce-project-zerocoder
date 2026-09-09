(() => {
  const el = id => document.getElementById(id);
  let challenge = null;
  let challengePurpose = "login";
  async function api(path, method = "GET", data) {
    const csrf = document.querySelector('[name="csrfmiddlewaretoken"]').value;
    const r = await fetch(path, {method, headers: {"Content-Type":"application/json", "X-CSRFToken":csrf}, body: data ? JSON.stringify(data) : undefined});
    const result = await r.json();
    if (!r.ok) throw new Error(result.error?.message || "Не удалось выполнить запрос");
    return result;
  }
  const run = fn => async event => {event?.preventDefault(); try {await fn(event);} catch(e) {el("notice").textContent = e.message;}};
  async function refresh() {
    const state = await api("/store/account/");
    el("login-form").hidden = state.authenticated;
    el("profile").hidden = !state.authenticated;
    el("basket-choice").hidden = !state.cart_choice_required;
    if (state.authenticated) {
      el("email").textContent = state.email;
      el("profile-form").elements.name.value = state.name;
      el("profile-form").elements.phone_login.value = state.phone_login;
      el("history-status").textContent = state.history_pending ? "Часть старой истории проверяет менеджер. Новые заказы доступны сразу." : "Доступная история показана ниже.";
    }
    const data = await api("/store/account/orders/");
    el("orders").replaceChildren();
    if (!data.orders.length) el("orders").textContent = "В этой сессии пока нет доступных заказов.";
    for (const order of data.orders) {
      const button = document.createElement("button");
      button.textContent = `${order.public_number} · ${order.total_amount} ₽ · ${order.order_status_label}`;
      button.onclick = run(async () => {
        const detail = await api(`/store/account/orders/${encodeURIComponent(order.public_number)}/`);
        el("order-detail").textContent = `Заказ ${detail.public_number}\n${detail.order_status_label} · ${detail.payment_status_label}\n` + detail.items.map(i=>`${i.product_name_snapshot}: ${i.quantity} — ${i.total_price} ₽`).join("\n") + `\nИтого: ${detail.total_amount} ₽`;
      });
      el("orders").append(button, document.createElement("br"));
    }
  }
  el("login-form").onsubmit = run(async () => {
    const r = await api("/store/auth/code/request/", "POST", {identifier:el("login-form").identifier.value});
    challenge = r.challenge_id; challengePurpose = "login"; el("notice").textContent = r.message; el("code-form").hidden = false;
  });
  el("email-form").onsubmit = run(async () => {
    const r = await api("/store/auth/code/request/", "POST", {identifier:el("email-form").identifier.value, purpose:"email_change"});
    challenge = r.challenge_id; challengePurpose = "email_change"; el("notice").textContent = r.message; el("code-form").hidden = false;
  });
  el("code-form").onsubmit = run(async () => {
    await api("/store/auth/code/verify/", "POST", {challenge_id:challenge, code:el("code-form").code.value, purpose:challengePurpose});
    location.reload();
  });
  el("profile-form").onsubmit = run(async () => {
    await api("/store/account/", "PATCH", Object.fromEntries(new FormData(el("profile-form"))));
    el("notice").textContent = "Профиль сохранён."; await refresh();
  });
  el("logout").onclick = run(async () => {await api("/store/auth/logout/", "POST", {}); location.reload();});
  for (const b of document.querySelectorAll("[data-choice]")) b.onclick = run(async () => {await api("/store/account/", "PATCH", {cart_choice:b.dataset.choice}); await refresh();});
  run(refresh)();
})();
