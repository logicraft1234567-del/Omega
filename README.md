# OMEGA — Buy · Verify · Get Paid

**PayPal AI Hackathon submission — "Build what's next with PayPal and AI"**

> Hosted demo: **YOUR_RENDER_URL** · Demo video (3 min): **YOUR_YOUTUBE_LINK** · License: MIT

Omega is a protected marketplace for used electronics. Buying a phone or laptop from a stranger means trusting them with your money first. Omega removes that trust problem: **PayPal holds the buyer's payment, two AI engines verify the item on video, and the money is released to the seller only when 11 backend checks pass.** If the item is not what was promised, the buyer is refunded through PayPal.

## What the project does
1. **Buyer pays with PayPal** (Orders v2). The payment is captured and **held by Omega**, not sent to the seller.
2. **Seller films the real device** while following a random challenge (e.g. "show the back, then say the code word"). Gemini reads the video: model, storage, condition, serial/IMEI.
3. **Seller ships. Buyer films the unboxing.** Gemini (video) and Groq (still frames) each independently decide whether the delivered item is the *same device* the seller filmed.
4. **Both engines agree + all checks pass → the buyer confirms → PayPal Payouts pays the seller.** One payout per order, enforced in code.
5. **Mismatch → nothing is paid.** The order is frozen, an AI dispute summary is written, and an admin refunds the buyer through the PayPal refund API or releases the money.

## How PayPal is used (central to the product)
| PayPal capability | Where | What it does in Omega |
|---|---|---|
| OAuth 2.0 client credentials | `services/payments.py` | Authenticates every call (sandbox or live) |
| **Orders v2** (`/v2/checkout/orders`, intent `CAPTURE`) | checkout → `routes/orders.py`, `routes/payments.py` | Creates the buyer's payment, redirects to PayPal approval (`payer-action` link), captures on return |
| **Payouts** (`/v1/payments/payouts`) | `services/policy.release_payment` | Pays the seller's PayPal email, only after verification passes |
| **Captures refund** (`/v2/payments/captures/{id}/refund`) | `services/policy.refund_payment` | Refunds the buyer when verification fails or a dispute is resolved for the buyer |
| **Webhooks** + `verify-webhook-signature` | `/webhooks/paypal` | Signature-verified, de-duplicated by event id, can update records but can **never** trigger a payout |
| `PayPal-Request-Id` idempotency | every money call | A retry can never charge, pay out or refund twice |
| Complete Payments (`paypal_marketplace` mode) | `services/payments.py` | Seller as payee with `disbursement_mode: DELAYED` (needs PayPal partner approval, off by default) |

## How AI is used (central to the product)
| Engine | Provider | Looks at | Used for |
|---|---|---|---|
| **Fire** 🔥 | Google **Gemini** (Files API + `generateContent`) | The actual **video files** | Reads the device (model, storage, condition, serial/IMEI), checks the seller's random challenge, compares seller video vs buyer unboxing video |
| **Water** 💧 | **Groq** vision model | **Still frames** pulled with OpenCV from both videos, plus the listing photos | Independent second opinion; detects stock photos/renders in listings |

- **AI recommends, the backend decides.** `services/policy.py` is the only code path that can move money. AI output is one of 11 checks (confidence threshold, risk score, no open dispute, buyer confirmation, …).
- **No fake AI, ever.** If an engine can't be reached, Omega says exactly why (key missing/rejected, quota, retired model, network) on the order page, in the timeline and on **Admin → AI engines**, and waits. Nothing is guessed and no payout happens on a missing result. **Retry analysis** re-runs it.
- Both engines must say "same device". If they disagree the order goes to a human, not to an automatic dispute.
- Privacy: videos sent to Gemini are deleted from Google's Files API right after analysis; Groq only receives downsized still frames.

## Tools & tech
Python 3.12 · Flask, SQLAlchemy, Flask-Login, Flask-WTF (CSRF), Flask-Limiter · SQLite (local) / PostgreSQL (production) · OpenCV + Pillow + NumPy (frames) · PayPal REST API (sandbox) · Google Gemini API · Groq API · deployed on **Render** (`render.yaml`) · vanilla JS + CSS glassmorphism UI (no frontend framework).

---

## Run it (judges: ~5 minutes)

### 1. Get the three keys
| Key | Where | Variables |
|---|---|---|
| PayPal **sandbox** app | [developer.paypal.com](https://developer.paypal.com) → Apps & Credentials → **Sandbox** → Create App. Make sure **Payouts** is enabled for the app. | `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` |
| Gemini | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | `GEMINI_API_KEY` |
| Groq | [console.groq.com/keys](https://console.groq.com/keys) | `GROQ_API_KEY` |

### 2. Install and configure
```bash
git clone <this repo> && cd omega
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then paste the 3 keys into .env (PAYPAL_ENV=sandbox is already the default)
flask --app app doctor      # tests every key live and tells you exactly what is missing
python app.py               # http://localhost:5000
```
`flask --app app doctor` prints `OK`/`FAIL` for the PayPal credentials, Gemini and Groq. Fix any `FAIL`, restart, and you're ready.

### 3. Create demo users
```bash
flask --app app seed               # DEMO ONLY: buyer@omega.test, seller@omega.test, admin@omega.test (password omega123) + sample listings
```
For real accounts instead, sign up in the UI (buyer or seller) and run `flask --app app create-admin`. **Sellers need a PayPal email** (the payout destination): in the sandbox use a *Personal* sandbox account email from developer.paypal.com → Testing Tools → Sandbox Accounts. The seed seller's `seller-sandbox@omega.test` is a placeholder: change it in **Settings** to a real sandbox account email before releasing a payout.

### 4. Pay with a PayPal sandbox buyer
At the PayPal approval page, log in with a sandbox **Personal** account (developer.paypal.com → Testing Tools → Sandbox Accounts). No real money moves anywhere.

### Deploy to Render (hosted demo)
1. Push the repo to GitHub (public, with the `LICENSE` file).
2. Render → **New → Blueprint** → select the repo. `render.yaml` creates the web service + PostgreSQL.
3. Fill the prompted variables: `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `GEMINI_API_KEY`, `GROQ_API_KEY`, and `BASE_URL` = your Render URL (no trailing slash).
4. Open the Render shell: `flask --app app doctor`, then `flask --app app seed` if you want demo accounts for judges.
5. Optional webhook: PayPal dashboard → your sandbox app → Webhooks → add `https://<your-app>.onrender.com/webhooks/paypal` (events `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.REFUNDED`, `PAYMENT.CAPTURE.REVERSED`, `PAYMENT.PAYOUTS-ITEM.*`), then set `PAYPAL_WEBHOOK_ID`. Checkout works without it; unverified webhooks are always rejected.
Note: Render's free tier has an ephemeral disk, so uploaded videos disappear on redeploy. That is fine for a demo; use a persistent disk or S3 for production.

---

## 3-minute demo script (for the YouTube video)
1. **(0:00)** One sentence: "Buying used electronics from a stranger means paying first and hoping. Omega makes PayPal hold the money until AI verifies the item."
2. **(0:15)** **Buyer** → Browse → listing → *Buy with protection* → approve in the PayPal sandbox → show "payment secured, held by Omega".
3. **(0:45)** **Seller** → open the order → confirm details → upload the verification video with the random challenge → Gemini + Groq accept it → add tracking.
4. **(1:20)** **Buyer** → confirm delivery → upload the unboxing video → show the Verification Center: both engines say "same device", 11/11 checks → *The item matches — release payment* → PayPal payout sent.
5. **(2:00)** Repeat quickly with a **different** device video: Omega flags the mismatch, holds the money and writes an AI dispute summary → **admin** refunds through PayPal.
6. **(2:40)** Close: "AI recommends, the backend decides, PayPal moves the money."
Tips: record on the device you built for, keep it under 3 minutes, upload it **public on YouTube**, and don't show third-party logos or copyrighted music.

## Tests
```bash
pytest        # 49 tests
```
Includes `tests/test_paypal_sandbox.py`: the full PayPal path (checkout → approval → capture → hold → payout, refund, cancel, bad credentials, forged return URLs, webhook signature + idempotency) against a strict fake of the PayPal REST API, and `tests/test_flow.py`: the two AI engines, every "AI unreachable" reason and retry, the state machine, access control and risk scoring. Only the HTTP edge is faked; everything inside Omega runs for real.
Honest note: the PayPal/Gemini/Groq calls follow their documented REST APIs and are tested against fakes. Run `flask --app app doctor` and one full order with your keys to confirm them live.

## Payment modes
| Mode | Behaviour |
|---|---|
| `unconfigured` (default, no keys) | Checkout is disabled and says so. Nothing is simulated |
| `paypal_standard` (automatic when `PAYPAL_CLIENT_ID/SECRET` are set) | Orders v2 capture; release via Payouts to the seller's PayPal email; refund via capture refund |
| `paypal_marketplace` | Complete Payments: seller as payee, `disbursement_mode: DELAYED`. Needs PayPal partner approval + seller onboarding (`paypal_merchant_id`) |
| `simulated` (only if `PAYMENT_MODE=simulated`) | Local approval page for development/tests. No real money |

Nigeria is send-only on PayPal, so Nigerian sellers can't receive live PayPal payouts: use the sandbox for the hackathon and add a local provider behind `services/payments.py` afterwards.

## Rules enforced in code
- **One payout per order**: `Disbursement.transaction_id` is UNIQUE + `PayPal-Request-Id`. Webhooks are de-duplicated by event id.
- **State machine** (`models.TRANSITIONS`) validates every status change.
- **Silence is never fraud**: missed deadlines go to a human; nothing is auto-paid or auto-refunded (`flask check-deadlines` on a schedule).
- Evidence is private, SHA-256 hashed, content-sniffed on upload and served via short-lived signed URLs. CSRF on every form, rate limiting, role-based access.

## Judging criteria, where to look
| Criterion | Where |
|---|---|
| Technological implementation | PayPal Orders/Payouts/Refunds/Webhooks (`services/payments.py`), two-engine AI pipeline (`services/fire.py`, `water.py`, `pipeline.py`), policy engine (`services/policy.py`), 49 tests |
| Design | Dark/light glass UI, buyer / seller / admin dashboards, Verification Center, timeline per order, animated intro (`static/js/intro.js`) |
| Potential impact | Peer-to-peer electronics fraud (swapped, fake or damaged items) in markets where escrow is not available |
| Innovation | PayPal as the escrow, multimodal AI as the arbiter, and a backend that never lets AI alone move money |
| Presentation | Demo script above |

## Known limits
- Email verification / password-reset emails are not implemented (accounts work immediately). Add an SMTP provider before launch.
- Analysis runs in a thread pool; swap `services/pipeline.submit` for Celery + Redis to scale. Uploads live on local disk (`services/storage.py`).
- Icons are Lucide (ISC licence) from the SVG sprite `static/vendor/icons.svg`; after adding an icon run `python tools/build_icons.py <lucide-static/icons>`.
- AI can't prove hardware authenticity; confidence is not the probability that the seller is honest.

## License
MIT, see [LICENSE](LICENSE).
