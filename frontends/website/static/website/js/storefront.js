(() => {
  const csrfToken = () =>
    document.querySelector('meta[name="csrf-token"]')?.content ||
    document.cookie.match(/csrftoken=([^;]+)/)?.[1] ||
    "";

  const escapeHtml = (value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");

  const money = (value) => `${Number(value).toLocaleString("ru-RU")} ₽`;

  const asQuantity = (value, min) => {
    const amount = Number(value);
    const floor = Number(min || 0.001);
    if (!Number.isFinite(amount) || amount <= 0) return floor;
    return Math.round(amount * 1000) / 1000;
  };

  async function request(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
        ...(options.headers || {}),
      },
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error?.message || "Не удалось выполнить запрос.");
    }
    return data;
  }

  function renderCatalog(products) {
    const grid = document.querySelector("[data-catalog-grid]");
    const count = document.querySelector("[data-catalog-count]");
    if (!grid) return;
    if (count) count.textContent = String(products.length);
    if (!products.length) {
      grid.innerHTML =
        '<p class="empty-catalog">В базе пока нет активных товаров. Добавьте карточки в Django Admin — они появятся здесь и в ботах.</p>';
      return;
    }
    grid.innerHTML = products
      .map((product) => {
        const name = escapeHtml(product.name);
        const description = product.description
          ? `<p class="desc">${escapeHtml(product.description)}</p>`
          : "";
        const image = product.main_image_url
          ? `<img src="${escapeHtml(product.main_image_url)}" alt="${name}" width="800" height="800" loading="lazy">`
          : '<div class="product-card__placeholder">Нет фото в карточке</div>';
        return `
      <article class="product-card" data-product-id="${product.id}" data-name="${name} ${escapeHtml(product.description || "")}">
        <div class="product-card__media">${image}</div>
        <div class="product-card__body">
          <h3>${name}</h3>
          ${description}
          <div class="product-card__meta">
            <div class="price">
              ${escapeHtml(product.base_price)} ₽
              <small>за ${escapeHtml(product.unit_label)}, от ${escapeHtml(product.min_quantity)}</small>
            </div>
            <div class="qty-row">
              <div class="qty">
                <button type="button" data-qty-step="-">−</button>
                <input type="text" inputmode="decimal" value="${escapeHtml(product.min_quantity)}" data-qty data-min="${escapeHtml(product.min_quantity)}" aria-label="Количество ${name}">
                <button type="button" data-qty-step="+">+</button>
              </div>
              <button type="button" class="btn btn-primary" data-add-to-cart>В корзину</button>
            </div>
          </div>
        </div>
      </article>`;
      })
      .join("");
  }

  function renderCart(cart) {
    const itemsNode = document.querySelector("[data-cart-items]");
    const emptyNode = document.querySelector("[data-cart-empty]");
    const countNode = document.querySelector("[data-cart-count]");
    const items = cart.items || [];
    if (countNode) countNode.textContent = String(items.length);
    if (emptyNode) emptyNode.hidden = items.length > 0;
    if (!itemsNode) return;
    itemsNode.innerHTML = items
      .map((item) => {
        const product = item.product;
        return `
        <article class="cart-line" data-product-id="${product.id}" data-min="${escapeHtml(product.min_quantity)}" data-quantity="${escapeHtml(item.quantity)}">
          <div>
            <strong>${escapeHtml(product.name)}</strong>
            <p>${escapeHtml(item.quantity)} ${escapeHtml(product.unit_label)} · ${money(item.line_total)}</p>
          </div>
          <div class="qty">
            <button type="button" data-cart-step="-">−</button>
            <button type="button" data-cart-step="+">+</button>
            <button type="button" class="btn btn-ghost" data-cart-remove>Удалить</button>
          </div>
        </article>`;
      })
      .join("");
    const itemsTotal = document.querySelector("[data-cart-items-total]");
    if (itemsTotal) {
      itemsTotal.textContent = money(cart.items_total || 0);
      itemsTotal.dataset.amount = String(cart.items_total || 0);
    }
  }

  async function refreshCart() {
    const cart = await request("/store/cart/");
    renderCart(cart);
    return cart;
  }

  async function setQuantity(productId, quantity) {
    const cart = await request(`/store/cart/items/${productId}/`, {
      method: "PUT",
      body: JSON.stringify({ quantity }),
    });
    renderCart(cart);
    await refreshPreview();
    return cart;
  }

  const deliveryQuoteId = document.querySelector("[data-delivery-quote-id]");
  const deliveryQuoteStatus = document.querySelector("[data-delivery-quote-status]");
  let deliveryQuoteTimer = null;

  const clearDeliveryQuote = () => {
    if (deliveryQuoteId) deliveryQuoteId.value = "";
  };

  async function refreshPreview({ required = false } = {}) {
    const form = document.querySelector("[data-checkout-form]");
    if (!form) return;
    const payload = Object.fromEntries(new FormData(form).entries());
    payload.personal_data_consent = form.personal_data_consent.checked;
    const needsDelivery = payload.receiving_type === "delivery";
    const address = String(payload.delivery_address || "").trim();
    if (needsDelivery && address.length < 5) {
      clearDeliveryQuote();
      const itemsTotal = document.querySelector("[data-cart-items-total]")?.dataset.amount || 0;
      document.querySelector("[data-cart-discount]").textContent = money(0);
      document.querySelector("[data-cart-delivery]").textContent = "После ввода адреса";
      document.querySelector("[data-cart-total]").textContent = `от ${money(itemsTotal)}`;
      if (deliveryQuoteStatus) {
        deliveryQuoteStatus.textContent =
          "Укажите полный адрес — стоимость рассчитает Яндекс Доставка.";
      }
      if (required) throw new Error("Укажите полный адрес доставки.");
      return null;
    }
    if (deliveryQuoteStatus) {
      deliveryQuoteStatus.textContent = needsDelivery
        ? "Рассчитываем стоимость в Яндекс Доставке…"
        : "Самовывоз — бесплатно.";
    }
    try {
      const preview = await request("/store/checkout/preview/", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      document.querySelector("[data-cart-discount]").textContent = money(preview.discount_amount);
      document.querySelector("[data-cart-delivery]").textContent = money(preview.delivery_cost);
      document.querySelector("[data-cart-total]").textContent = money(preview.total_amount);
      if (deliveryQuoteId) deliveryQuoteId.value = preview.delivery_quote_id || "";
      if (deliveryQuoteStatus) {
        const days = preview.delivery_days
          ? ` Срок: ${preview.delivery_days} дн.`
          : "";
        deliveryQuoteStatus.textContent = needsDelivery
          ? `Адрес: ${preview.delivery_address || address}. Доставка: ${money(preview.delivery_cost)}.${days}`
          : "Самовывоз — бесплатно.";
      }
      return preview;
    } catch (error) {
      clearDeliveryQuote();
      if (deliveryQuoteStatus) {
        deliveryQuoteStatus.textContent = `Расчёт не выполнен: ${error.message}`;
      }
      if (required) throw error;
      return null;
    }
  }

  async function loadCatalogFromBackend() {
    try {
      const response = await fetch("/api/products/", { headers: { Accept: "application/json" } });
      if (!response.ok) return;
      renderCatalog(await response.json());
    } catch (_error) {
      /* SSR уже отрисован из ProductListSerializer той же базы */
    }
  }

  document.addEventListener("click", async (event) => {
    if (event.target.closest("[data-open-assistant]")) {
      openAssistant();
      return;
    }

    if (event.target.closest("[data-close-assistant]")) {
      closeAssistant();
      return;
    }

    if (event.target.closest("[data-new-assistant]")) {
      startNewAssistantConversation();
      return;
    }

    const step = event.target.closest("[data-qty-step]");
    if (step) {
      const card = step.closest("[data-product-id]");
      const input = card.querySelector("[data-qty]");
      const min = Number(input.dataset.min || 1);
      const next = asQuantity(Number(input.value || min) + (step.dataset.qtyStep === "+" ? min : -min), min);
      input.value = String(Math.max(min, next));
      return;
    }

    const add = event.target.closest("[data-add-to-cart]");
    if (add) {
      const card = add.closest("[data-product-id]");
      const input = card.querySelector("[data-qty]");
      try {
        const cart = await refreshCart();
        const current = cart.items.find((item) => String(item.product.id) === String(card.dataset.productId));
        const added = asQuantity(input.value, input.dataset.min);
        const quantity = current ? asQuantity(Number(current.quantity) + added, input.dataset.min) : added;
        await setQuantity(card.dataset.productId, quantity);
      } catch (error) {
        window.alert(error.message);
      }
      return;
    }

    const cartStep = event.target.closest("[data-cart-step]");
    if (cartStep) {
      const line = cartStep.closest(".cart-line");
      const min = Number(line.dataset.min || 1);
      const current = Number(line.dataset.quantity || min);
      const next = cartStep.dataset.cartStep === "+" ? current + min : current - min;
      try {
        await setQuantity(line.dataset.productId, next <= 0 ? 0 : asQuantity(next, min));
      } catch (error) {
        window.alert(error.message);
      }
      return;
    }

    const remove = event.target.closest("[data-cart-remove]");
    if (remove) {
      const line = remove.closest(".cart-line");
      try {
        await request(`/store/cart/items/${line.dataset.productId}/`, { method: "DELETE" });
        await refreshCart();
        await refreshPreview();
      } catch (error) {
        window.alert(error.message);
      }
    }

    if (event.target.closest("[data-open-cart]")) {
      document.querySelector("#cart")?.scrollIntoView({ behavior: "smooth" });
    }
  });

  document.querySelector("[data-catalog-search]")?.addEventListener("input", (event) => {
    const query = event.target.value.trim().toLowerCase();
    document.querySelectorAll("[data-product-id]").forEach((card) => {
      if (!card.closest("[data-catalog-grid]")) return;
      card.classList.toggle("is-hidden", Boolean(query) && !card.dataset.name.toLowerCase().includes(query));
    });
  });

  const checkoutForm = document.querySelector("[data-checkout-form]");
  const addressField = document.querySelector("[data-address-field]");
  const receiptEmailField = checkoutForm?.querySelector('[name="email"]');
  const receiptEmailMark = document.querySelector("[data-receipt-email-mark]");

  const syncReceiptEmailRequirement = () => {
    if (!checkoutForm || !receiptEmailField) return;
    const isOnlinePayment = checkoutForm.payment_method.value === "card_prepayment";
    receiptEmailField.required = isOnlinePayment;
    if (receiptEmailMark) receiptEmailMark.hidden = !isOnlinePayment;
  };

  checkoutForm?.receiving_type.addEventListener("change", () => {
    addressField.hidden = checkoutForm.receiving_type.value === "pickup";
    clearDeliveryQuote();
    refreshPreview();
  });
  checkoutForm?.payment_method.addEventListener("change", () => {
    syncReceiptEmailRequirement();
    clearDeliveryQuote();
    refreshPreview();
  });
  checkoutForm?.delivery_address.addEventListener("input", () => {
    clearDeliveryQuote();
    window.clearTimeout(deliveryQuoteTimer);
    deliveryQuoteTimer = window.setTimeout(() => refreshPreview(), 650);
  });
  checkoutForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    syncReceiptEmailRequirement();
    if (!checkoutForm.reportValidity()) return;
    const errorNode = document.querySelector("[data-checkout-error]");
    const resultNode = document.querySelector("[data-order-result]");
    errorNode.hidden = true;
    resultNode.hidden = true;
    try {
      await refreshPreview({ required: true });
      const payload = Object.fromEntries(new FormData(checkoutForm).entries());
      payload.personal_data_consent = checkoutForm.personal_data_consent.checked;
      const order = await request("/store/orders/", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      resultNode.hidden = false;
      resultNode.textContent =
        `Ваш заказ оформлен. При необходимости наш менеджер свяжется с вами. ` +
        `Номер ${order.public_number}, сумма ${money(order.total_amount)}.`;
      if (order.confirmation_url) {
        resultNode.textContent += " Переходим к оплате…";
        window.setTimeout(() => {
          window.location.href = order.confirmation_url;
        }, 800);
      }
      await refreshCart();
    } catch (error) {
      errorNode.hidden = false;
      errorNode.textContent = error.message;
    }
  });

  const assistantDialog = document.querySelector("[data-assistant-dialog]");
  const assistantMessages = document.querySelector("[data-assistant-messages]");
  const assistantForm = document.querySelector("[data-assistant-form]");
  const assistantError = document.querySelector("[data-assistant-error]");
  const assistantSubmit = document.querySelector("[data-assistant-submit]");
  let assistantHistoryLoaded = false;

  function renderAssistantGreeting() {
    if (!assistantMessages) return;
    assistantMessages.innerHTML = "";
    appendAssistantMessage(
      "assistant",
      "Здравствуйте! Напишите, что хотите заказать. Помогу выбрать товары из каталога, рассчитать доставку и оформить оплату."
    );
  }

  function appendAssistantMessage(role, message, actionUrl = "") {
    if (!assistantMessages) return;
    const item = document.createElement("article");
    item.className = `assistant-message assistant-message--${role}`;
    const text = document.createElement("p");
    text.textContent = message;
    item.append(text);
    if (role === "assistant" && actionUrl) {
      try {
        const url = new URL(actionUrl, window.location.origin);
        if (url.protocol === "https:") {
          const link = document.createElement("a");
          link.className = "btn btn-primary assistant-payment-link";
          link.href = url.href;
          link.textContent = "Перейти к оплате";
          item.append(link);
        }
      } catch (_error) {
        /* Сервер не отдаёт некорректный URL как действие. */
      }
    }
    assistantMessages.append(item);
    assistantMessages.scrollTop = assistantMessages.scrollHeight;
  }

  async function loadAssistantHistory() {
    if (assistantHistoryLoaded || !assistantMessages) return;
    const history = await request("/store/assistant/history/");
    if (history.messages?.length) {
      assistantMessages.innerHTML = "";
      history.messages.forEach((item) =>
        appendAssistantMessage(item.role, item.message, item.action_url)
      );
    }
    assistantHistoryLoaded = true;
  }

  async function openAssistant() {
    if (!assistantDialog) return;
    if (!assistantDialog.open) assistantDialog.showModal();
    try {
      await loadAssistantHistory();
    } catch (_error) {
      appendAssistantMessage(
        "assistant",
        "Историю пока не удалось загрузить. Можете начать новый вопрос."
      );
    }
    assistantForm?.message.focus();
  }

  function closeAssistant() {
    assistantDialog?.close();
  }

  async function startNewAssistantConversation() {
    if (!assistantMessages) return;
    try {
      await request("/store/assistant/conversations/", {
        method: "POST",
        body: "{}",
      });
      assistantHistoryLoaded = true;
      assistantError.hidden = true;
      renderAssistantGreeting();
      assistantForm?.message.focus();
    } catch (error) {
      assistantError.hidden = false;
      assistantError.textContent = error.message;
    }
  }

  async function waitForAssistantEvent(eventId) {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const state = await request(`/store/assistant/events/${eventId}/`);
      if (state.complete) {
        appendAssistantMessage(
          "assistant",
          state.response?.message || "Сообщение обработано. Уточните, если хотите продолжить.",
          state.response?.action_url
        );
        return;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
    appendAssistantMessage(
      "assistant",
      "Обработка продолжается. Оставьте чат открытым или вернитесь через минуту."
    );
  }

  assistantForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = assistantForm.message.value.trim();
    if (!message) return;
    assistantError.hidden = true;
    assistantSubmit.disabled = true;
    appendAssistantMessage("user", message);
    assistantForm.message.value = "";
    try {
      const created = await request("/store/assistant/messages/", {
        method: "POST",
        body: JSON.stringify({
          message,
          personal_data_consent: assistantForm.personal_data_consent.checked,
        }),
      });
      await waitForAssistantEvent(created.event_id);
      assistantHistoryLoaded = false;
    } catch (error) {
      assistantError.hidden = false;
      assistantError.textContent = error.message;
    } finally {
      assistantSubmit.disabled = false;
      assistantForm.message.focus();
    }
  });

  assistantDialog?.addEventListener("click", (event) => {
    if (event.target === assistantDialog) closeAssistant();
  });

  loadCatalogFromBackend();
  syncReceiptEmailRequirement();
  refreshCart().then(refreshPreview).catch(() => undefined);
})();
