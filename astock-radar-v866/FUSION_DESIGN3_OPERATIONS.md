# New-version operating contract

## Isolation

Only fusion_* modules, their new-version tests and this document are part of this patch. Do not overwrite old V8B code, services, positions, databases or notification configuration.

The new transport reads FUSION_WECOM_WEBHOOK or secrets.local.json: fusion_wecom_webhook. It NEVER falls back to WEWORK_WEBHOOK_URL, wework_webhook_url or V8_WEBHOOK_FILE. Configuration migration must be explicitly approved by the owner. Robot API acknowledgement does not prove a human received the message in the intended group; request human confirmation using a unique test ID.

## Features and operating limits

- Research rules remain execution-ineligible. Entry evidence, missing gates, validity and invalidation are shared by workbench and messages.
- All-market daily cross-section history preparation is resumable and rate-bounded to one date batch per worker step outside continuous trading. At least 60 historical sessions are required. Initial warmup is not instant. Provider coverage is not certified full-market coverage.
- Backfilled bars are context downloaded now, never reconstructed historical PIT decisions.
- Prepared structural candidates supplement the liquidity shortlist, capped at 200. Fresh intraday quotes and the original full research rules remain mandatory.
- New-version portfolio alerts use only data/fusion_positions.json. They never change or import old positions automatically. An absent file means no new-version position monitoring, not an empty verified broker account.
- Optional legacy evidence sources use data/fusion_legacy_sources.json, a JSON list of objects containing an absolute path to an owner-approved CSV export. Only known non-secret fields are imported; the source is read-only. Original source times are retained as unverified source claims, not certified PIT.
- No live broker orders, calibrated probability, automatic buy/sell recommendations or claimed 80% win rate.

## Portfolio input schema

fusion_positions.json is a JSON array. Each position supplies ts_code, cost_price, quantity, stop_price, target_price, available_qty and position_asof. Available quantity is shown only when position_asof has today's date, and still requires broker confirmation. Stop/target alerts use the exact line that triggered, one stock per notification. Recovery resets the state so a subsequent re-crossing can be recorded. Unknown or stale quotes suppress price-based notices.

## Notice schedule

- Research lifecycle: new observation, pause, expiry or confirmed risk. Requires explicit --notify-research opt-in.
- System data failure/recovery: changes only, no stale replay.
- Preopen plan: once during 09:00-09:25 on a confirmed trading day.
- Close review: once during 15:30-16:30 on a confirmed trading day.
- Portfolio review: per changed state and stock, only with new-version position input and fresh quotes.
- Each notice is recorded separately. HELD notices are not replayed when notifications are later enabled. UNCERTAIN sends are never automatically retried.
- These are research/operations notices, not trade instructions. Preopen/close summaries do not certify that all portfolio, market or outcome data is complete.

## Acceptance

Run the four existing fusion test modules and test_fusion_design3 with outbound network blocked. Start the staged HTTP handler with outbound network blocked before switching the new-version service. A live channel test must be clearly labelled, use a unique ID, target only the owner-confirmed new group, and record only acknowledgement and channel fingerprint (never the URL/key).
