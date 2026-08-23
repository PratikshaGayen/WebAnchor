# WebAnchor frontend

A small Vite + TypeScript page that runs both demo contracts against the same
volatile URL and shows the full transaction lifecycle side by side. It talks to
a live GenLayer Studio node over JSON-RPC through `genlayer-js` - nothing here
is mocked.

## Prerequisites

- The GenLayer Studio Docker stack running locally, with `eth_chainId` on
  `http://127.0.0.1:4000/api` returning `0xf22f`.
- Each consensus worker's `/genvm/config/genvm-module-web.yaml` has
  `always_allow_hosts: ["host.docker.internal"]`. GenVM's web module rejects
  `.internal` hosts otherwise. `tests/integration/conftest.py` documents how
  that patch was applied.
- Python with the repo installed (`pip install -e ".[dev]"`) for the deploy
  script.
- Node 20+.

## Start the volatile page server

The demo is pointless against a static page, so it reads from the same
per-request-volatile server the integration tests use. It has to bind port 80,
because GenVM's web module only allows ports 80 and 443, and it has to be
reachable at `host.docker.internal` because the workers run in Docker Desktop's
VM and cannot see `127.0.0.1` on the host.

```bash
python tests/integration/volatile_server.py 80
```

Check it from inside a worker if you want to be sure:

```bash
docker exec genlayer-studio-consensus-worker-1 curl -s -o /dev/null -w "%{http_code}\n" http://host.docker.internal/orders/100418
```

## Deploy the contracts

Studio's chain state does not survive a stack recreate, so redeploy whenever
addresses stop resolving:

```bash
python frontend/deploy.py
```

That deploys `contracts/naive_reader.py` and
`contracts/anchored_reader_multi/__init__.py` and writes the two addresses to
`frontend/src/contracts.json`, which the page loads at startup. Both address
fields in the UI are editable, so you can paste addresses from your own deploy
instead.

## Run

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 and press "Run both contracts". Each transaction
takes roughly 30 to 60 seconds to reach a terminal status, and both run
concurrently so they hit the same page in the same window.

## Accounts

The page mints a throwaway keypair with `createAccount()` on load and signs
with it. Studio's localnet does not require the sender to be funded, so there
is no faucet step and no wallet extension to install. `genlayer-js` does
support MetaMask via `client.connect("localnet")`, but pointing MetaMask at a
local chain is more setup than this demo is worth.

## What you should see

NaiveWebReader ends `UNDETERMINED` / `MAJORITY_DISAGREE` with every validator
voting `disagree`, and its storage stays empty. AnchoredWebReader ends
`ACCEPTED` / `MAJORITY_AGREE`, and the page reads the stored fingerprint back
out of chain state with `get_last_fingerprint()`.
