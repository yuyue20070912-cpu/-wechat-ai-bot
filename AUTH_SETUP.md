# fishyeah Chat authentication development notes

This branch adds **prototype** mainland-China phone-number + password login. It does not verify ownership of phone numbers, provide password recovery, or accept payments. **Do not deploy to the public production site yet.**

## Required environment variables
- `OPENAI_API_KEY`: existing paid OpenAI API key
- `OPENAI_MODEL`: optional model, defaults to `gpt-4o-mini`
- `SECRET_KEY`: a unique, random string of at least 32 characters; do not commit it
- `DATABASE_URL`: persistent PostgreSQL connection URL (Render Postgres or other managed PostgreSQL). Never use temporary filesystem SQLite for production.
- `RATELIMIT_STORAGE_URI`: shared Redis URL recommended for production. Without it, per-IP limits reset on restart and are separate per worker.
- `COOKIE_SECURE`: keep `true` on HTTPS production. Only set `false` in local HTTP tests.

## Current prototype behavior
- `POST /register` accepts a Chinese mainland mobile number and a password (10–128 chars); creates a user with five free credits.
- `POST /login`, `POST /logout`, `GET /me` provide session authentication.
- `GET /csrf` issues a CSRF token; send it as `X-CSRF-Token` on all POST requests.
- `POST /chat` requires login, validates message length, and atomically deducts one credit before an OpenAI request. On a caught API error, the credit is refunded.
- Passwords are stored as salted Werkzeug password hashes, never in plaintext.

## Before public release
1. Run CI tests and perform a staging deploy with PostgreSQL and HTTPS.
2. Add password recovery and **actual SMS verification** before relying on phone ownership; without verification, users can register someone else's phone number.
3. Add account/IP registration abuse controls, password brute-force lockouts or rate limits, and user deletion/privacy flows.
4. Add a payment provider with server-side webhook verification, ledger and idempotency before selling credits.
5. Set API spend limits and shared rate-limit storage. The free 5 credits are not a guarantee of budget protection.
6. Review data protection and applicable mainland China hosting/payment compliance.
