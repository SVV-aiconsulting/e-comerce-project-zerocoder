# Data flow AI-системы WebMarket

Версия: 1.0
Дата актуализации: 07.09.2026
Статус: соответствует реализованному MVP

## 1. Сквозной поток заказа

```mermaid
flowchart TD
    C[Клиент] --> CH{Канал}
    CH -->|Telegram| TG[Telegram adapter]
    CH -->|VK| VK[VK adapter]
    CH -->|Email| EM[Яндекс IMAP/SMTP adapter]
    CH -->|Website| WEB[Storefront и AI popup]
    TG --> CONS[Проверка согласия и identity канала]
    VK --> CONS
    EM --> CONS
    WEB --> CONS
    CONS --> EVENT[InboundEvent / durable intake]
    EVENT --> Q[Celery + Redis]
    Q --> AI[OrderAssistantService + GigaChat]
    AI --> TOOLS[Типизированные backend tools]
    TOOLS --> CAT[(Каталог PostgreSQL)]
    TOOLS --> CART[(Общая Cart + checkout state)]
    TOOLS --> CRM[(Customer / Order CRM)]
    TOOLS --> YD[Яндекс Доставка test API]
    TOOLS --> YK[ЮKassa test API]
    TOOLS --> AUDIT[(AssistantTurn / ToolCall audit)]
    CART --> PREVIEW[Server-side preview]
    CAT --> PREVIEW
    YD --> PREVIEW
    PREVIEW --> CONFIRM{Явное подтверждение}
    CONFIRM -->|нет или изменение| CART
    CONFIRM -->|да, preview актуален| ORDER[Идемпотентное создание Order]
    ORDER --> PAY[Идемпотентная Payment + redirect URL]
    PAY --> CLIENT[Ответ клиенту в исходном канале]
    YK -->|webhook + контрольный GET| STATUS[Статус оплаты в CRM]
    STATUS --> CLIENT
    CRM --> DASH[Dashboard менеджера]
```

## 2. Границы ответственности LLM

GigaChat понимает естественный язык, контекст реплики и выбирает разрешённый
инструмент. Модель не имеет доступа к ORM/SQL и не является источником сведений о
товарах, ценах, корзине, клиенте, доставке, заказах или оплате.

Типизированные инструменты получают факты из backend: поиск активного каталога,
чтение и изменение корзины, параметры получения и оплаты, контакты, preview,
историю/повтор заказа, отмену, подтверждение и ссылку оплаты. Все аргументы проходят
Pydantic-валидацию, mutating-вызовы защищены ключами идемпотентности и аудитом.

## 3. Состояние незавершённого оформления

```mermaid
stateDiagram-v2
    [*] --> Cart
    Cart --> Receiving: товары и количество заданы
    Receiving --> Address: выбрана доставка
    Receiving --> Payment: выбран самовывоз
    Address --> Contacts: website или нужен телефон доставки
    Contacts --> Payment: контакт валиден
    Payment --> ReceiptEmail: card_prepayment
    Payment --> Preview: cash_on_delivery
    ReceiptEmail --> Preview: email валиден
    Preview --> Cart: клиент изменил параметры
    Preview --> Order: явное подтверждение актуального preview
    Order --> PaymentLink: card_prepayment
    Order --> [*]: cash_on_delivery
    PaymentLink --> Paid: подтверждённый webhook/polling
```

Ручной интерфейс и AI-ассистент читают и изменяют одну активную корзину данного
канала. При возобновлении устаревшей корзины backend заново проверяет активность,
минимальное количество и текущую цену товара; историческая цена используется только
в уже созданном заказе.

## 4. Идентификация и персональные данные

- Telegram/VK: устойчивый ID платформы; телефон собирается штатным сценарием.
- Email: нормализованный адрес отправителя является identity канала.
- Website без регистрации: сессия нужна только для изоляции корзины/диалога;
  CRM-клиент определяется по данным, явно указанным при оформлении.
- Согласие хранится append-only событием вместе с неизменяемыми версиями Политики и
  Согласия. Новый website AI-диалог имеет отдельный scope согласия.
- Конфликт контактов между карточками не блокирует заказ и передаётся менеджеру.

## 5. Отказоустойчивость и аудит

- Входные события, заказ и платёж создаются идемпотентно.
- Ошибка LLM, доставки или оплаты сохраняется и не заменяется выдуманным результатом.
- Фактический ответ ассистента сохраняется неизменно в `AssistantMessage`.
- Каждый вызов инструмента и его результат фиксируются в `AssistantToolCall` без
  сохранения секретов и платёжных реквизитов.
- Пропущенный webhook ЮKassa компенсируется периодической сверкой незавершённых
  платежей с API провайдера.
