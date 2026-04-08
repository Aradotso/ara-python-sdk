# Cal.com Booking Example

This folder is an optional integration example built on top of the public `ara-sdk`.

It is intentionally outside the core package so the SDK stays provider-agnostic.

## What this demonstrates

- Turning inbound chat messages into booking intents
- Looking up next-week availability from Cal.com
- Optionally creating bookings for selected slots
- Forwarding enriched context to Ara app event ingress

## Required environment variables

- `CALCOM_API_KEY`
- `CALCOM_EVENT_TYPE_ID` or `CALCOM_EVENT_TYPE_SLUG`
- `ARA_ACCESS_TOKEN` (from Ara app: `Settings -> System -> Auth Token -> Copy Access Token`)

## Security

- Never hardcode API keys in code.
- Keep `.env` files out of version control.
