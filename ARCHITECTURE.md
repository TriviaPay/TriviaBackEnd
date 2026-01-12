# Architecture Boundaries

This backend organizes router modules into explicit domains. Treat each domain as a
hard boundary for feature logic and router code.

Domains (by router file):
- Auth/Profile: `routers/auth/login.py`, `routers/auth/refresh.py`,
  `routers/auth/profile.py`, `routers/auth/admin.py`, `auth.py`
- Trivia/Draws/Rewards: `routers/trivia/trivia*.py`, `routers/trivia/draw.py`,
  `routers/trivia/rewards.py`, `routers/trivia/internal.py`,
  `updated_scheduler.py`
- Payments/Wallet/IAP: `app/routers/payments/wallet.py`,
  `app/routers/payments/payments.py`, `app/routers/payments/stripe_webhook.py`,
  `app/routers/payments/stripe_connect.py`, `app/routers/payments/iap.py`,
  `app/routers/payments/admin_withdrawals.py`
- Store/Cosmetics: `routers/store/store.py`, `routers/store/cosmetics.py`,
  `routers/store/badges.py`
- Messaging/Realtime: `routers/messaging/global_chat.py`,
  `routers/messaging/private_chat.py`, `routers/messaging/dm_*.py`,
  `routers/messaging/group_*.py`, `routers/messaging/status.py`,
  `routers/messaging/presence.py`
- Notifications: `routers/notifications/notifications.py`,
  `routers/notifications/onesignal.py`, `routers/notifications/pusher_auth.py`

Rule: no cross-domain imports. Code inside a domain must not import modules from
another domain. Shared, generic functionality belongs in neutral modules (for
example `utils/`, `core/`, `db.py`, `models.py`) and can be imported by any domain.

Allowed imports (shared modules):
- `utils.*`
- `core.*` (cross-domain facades and shared helpers)
- `db` / `db.py`
- `models` / `models.py`
- `config` / `config.py`

Module layout (per domain):
- `api.py`: Aggregates the domain's routers and exports a single `router`.
- `schemas.py`: Pydantic request/response models owned by the domain.
- `repository.py`: Database queries and persistence logic.
- `service.py`: Business rules that orchestrate repository calls.

# Phase 3: Data Ownership + Internal Interfaces

## Data Ownership Map

- Auth/Profile owns: `User`, `UserSubscription`, login/profile related tables.
- Trivia/Draws/Rewards owns: trivia mode configs, questions, winners, daily draw data.
- Store/Cosmetics owns: avatars, frames, badges, gem packages.
- Payments/Wallet/IAP owns: wallet balances, wallet transactions, withdrawals, Stripe tables.
- Messaging/Realtime owns: DM/group/status tables, presence tables.
- Notifications owns: OneSignal player registrations, push settings.

## Migration Checklist

- Repositories only touch tables owned by their domain.
- Cross-domain reads/writes happen only via explicit service APIs (or `core/*` facades).
- Router modules stay thin and delegate to `service.py`.
