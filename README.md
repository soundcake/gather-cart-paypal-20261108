# Gather Cart

Gather Cart helps community workshop organizers buy enough materials for their group without exceeding a fixed budget. A local language model maps a plain-language brief to one of three activities. The application calculates whole packs, removes materials already available, and keeps the reviewed price unchanged through PayPal sandbox checkout.

The catalogue is a small, fictional demonstration catalogue in USD: sketching, seed starting and notebook binding. An “already have” selection means there is enough of that material for the entire group. Quantities, prices and order totals come from the server catalogue; model output contains only an activity and an explanation.

## Run the sample rehearsal

Python 3.9 or later is sufficient. No dependencies, accounts or keys are needed.

```sh
python3 -m unittest -v
python3 server.py --port 8786
```

Open `http://127.0.0.1:8786`. The three example buttons use explicit fixtures in this mode. Editing the number of people, budget or owned materials changes the calculation. Sample mode labels itself and disables external checkout.

## Run local inference on Apple Silicon

Use Python 3.11 or later in an isolated environment:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-mlx.txt
.venv/bin/python download_model.py --destination ../model-qwen-1.5b
.venv/bin/python server.py --provider mlx --model ../model-qwen-1.5b --port 8786
```

The download is pinned to `mlx-community/Qwen2.5-1.5B-Instruct-4bit` revision `8b403126fc14f14cfc99bb4cfa72ecbc129ea677`, an Apache-2.0 model. It uses safetensors and the installed MLX model implementation; remote Python code is disabled. Prompts are processed locally. The model selects a supported workshop; schema validation rejects unknown activities, extra fields and unfinished responses.

For an existing local Ollama installation, `python3 server.py --provider ollama --model YOUR_INSTALLED_MODEL` uses only `127.0.0.1:11434`. It does not install a model. The optional Spark provider is retained from the initial prototype; the current prepared demonstration uses Qwen through MLX.

## Configure PayPal sandbox

Use a PayPal Developer sandbox REST app and separate sandbox merchant/buyer accounts. Keep secrets in your shell or secret manager, outside the repository:

```sh
export ENABLE_PAYPAL_SANDBOX=1
export PAYPAL_SANDBOX_CLIENT_ID='YOUR_SANDBOX_CLIENT_ID'
export PAYPAL_SANDBOX_CLIENT_SECRET='YOUR_SANDBOX_CLIENT_SECRET'
.venv/bin/python server.py --provider mlx --model ../model-qwen-1.5b --port 8786
```

After planning, choose **Review in PayPal sandbox** and approve the order with the test buyer. On return, explicitly choose **Confirm sandbox payment**. The server reads the order, checks the basket reference and exact amount, captures it, and verifies the completed capture before showing a receipt. Returned query parameters never determine an amount or a payment result.

The integration uses Orders v2 create/read/capture and the fixed host `api-m.sandbox.paypal.com`. Each quote has stable create/capture request identifiers. Retries reuse the saved order or receipt. An over-budget, zero-cost, expired or different-session quote cannot proceed. Approval redirects must target `www.sandbox.paypal.com`.

This is a local single-user prototype. Its quotes and sessions live in memory; keep the server running during checkout. Quotes expire after 30 minutes. It is bound to loopback and validates the Host, Origin and session CSRF token. The catalogue is illustrative and does not fulfill physical orders.

## Validation

The test suite exercises pack rounding, exact decimal money, owned supplies, budget rejection, immutable/session-bound quotes, expiry, model output boundaries, PayPal approval links, amount mismatch, approval-before-capture, receipt verification, repeated requests and local HTTP protections. Unit tests use a simulated gateway. A separate PayPal sandbox walkthrough on 8 November 2026 created, approved and captured a USD 16.20 basket; the Orders API returned COMPLETED with one completed capture. Reloading the application preserved the receipt. The Qwen 1.5B local-inference validation passed all 12 workshop selection cases.

## Sources

- [PayPal create order](https://developer.paypal.com/api/orders/v2/orders-create)
- [PayPal show order](https://developer.paypal.com/api/orders/v2/orders-get)
- [PayPal capture order](https://developer.paypal.com/api/orders/v2/orders-capture)
- [Qwen model and license](https://huggingface.co/mlx-community/Qwen2.5-1.5B-Instruct-4bit)
- [Ollama structured generation API](https://docs.ollama.com/api/generate)
