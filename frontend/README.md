# WebAnchor frontend

A small Vite + TypeScript page that runs both demo contracts against the same
volatile URL and shows the full transaction lifecycle side by side. It talks to
a live GenLayer Studio node over JSON-RPC through `genlayer-js` - nothing here
is mocked.

The deployed copy is at https://webanchor-demo.vercel.app and needs no setup at
all: it talks to the hosted `studionet` node and reads a volatile page served
by its own serverless function. The rest of this file covers both that path and
the local one.

## Which network the page talks to

`VITE_GL_NETWORK` picks it, and the default is `studionet`:

- `studionet` - `https://studio.genlayer.com/api`, the hosted node. Addresses
  come from `src/contracts.studionet.json`. This is what the Vercel build uses.
- `localnet` - `http://127.0.0.1:4000/api`, your own Studio Docker stack.
  Addresses come from `src/contracts.json`.

Both chains report the same chain id (`0xf22f`) and neither one requires the
sender to be funded, so the contracts and the page work unchanged against
either.

## The volatile page

The demo is pointless against static content - the whole claim is that the
leader and every validator fetch the same URL independently and get different
bytes back. `api/volatile.js` is a Vercel serverless function that serves the
same page shape as `tests/integration/volatile_server.py`, with a fresh nonce,
CSRF token, timestamp, ad id and view count on every single request. It is
served at `/orders/100418` and sends `Cache-Control: no-store` plus the two
CDN-specific no-store headers, because if the edge cached it every validator
would get identical bytes, the naive contract would reach consensus, and the
demo would quietly prove the opposite of what it claims.

Worth knowing if you edit that function: every volatile value deliberately sits
in a `<script>` body, an HTML comment, an element attribute or the `<iframe>`
subtree, and every visible text node is static prose. WebAnchor drops scripts,
iframes and comments structurally but does not band arbitrary numbers in
ordinary prose, so moving the view counter into visible text would break the
anchored contract. That would be a fixture bug, not a library result.

You can check the volatility yourself:

```bash
curl -s https://webanchor-demo.vercel.app/orders/100418 > a.html
curl -s https://webanchor-demo.vercel.app/orders/100418 > b.html
diff a.html b.html    # nonce, csrf, request id, ad id, view count, build hash
```

The order number, carrier, tracking id and item table are identical in both.

## Running against the hosted node

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. It defaults to `studionet` and to the deployed
volatile page, so this works with nothing else running.

## Running against a local Studio stack

You need:

- The GenLayer Studio Docker stack running, with `eth_chainId` on
  `http://127.0.0.1:4000/api` returning `0xf22f`.
- Each consensus worker's `/genvm/config/genvm-module-web.yaml` with
  `always_allow_hosts: ["host.docker.internal"]`. GenVM's web module rejects
  `.internal` hosts otherwise. `tests/integration/conftest.py` documents how
  that patch was applied. None of this applies on `studionet`, where a plain
  public https URL on port 443 is fetched without any allow-listing.
- Python with the repo installed (`pip install -e ".[dev]"`) for the deploy
  script, and Node 20+.

Start the volatile page server. It has to bind port 80, because GenVM's web
module only allows ports 80 and 443, and it has to be reachable at
`host.docker.internal` because the workers run in Docker Desktop's VM and
cannot see `127.0.0.1` on the host:

```bash
python tests/integration/volatile_server.py 80
```

Check it from inside a worker if you want to be sure:

```bash
docker exec genlayer-studio-consensus-worker-1 curl -s -o /dev/null -w "%{http_code}\n" http://host.docker.internal/orders/100418
```

Then deploy and run:

```bash
python frontend/deploy.py                # writes src/contracts.json
cd frontend
VITE_GL_NETWORK=localnet npm run dev
```

## Deploying the contracts

Studio's chain state does not survive a stack recreate, so redeploy whenever
addresses stop resolving:

```bash
python frontend/deploy.py                        # localnet
python frontend/deploy.py --network studionet    # hosted node
```

That deploys `contracts/naive_reader.py` and
`contracts/anchored_reader_multi/__init__.py` and writes the two addresses to
`src/contracts.json` or `src/contracts.studionet.json`, which the page loads at
startup. Both address fields in the UI are editable, so you can paste addresses
from your own deploy instead.

The script goes through gltest rather than the `genlayer` CLI on purpose. The
anchored contract is a package with a sibling `webanchor.py`, and the CLI reads
contract files as UTF-8 text, which corrupts the zip bytes of a multi-file
contract. gltest already packages it correctly, and it already knows about both
networks, so `--network` only has to pick between them.

## Deploying the page

```bash
cd frontend
vercel deploy --prod
```

The Vercel project is `webanchor-demo`. `webanchor-demo.vercel.app` is a
project domain rather than an automatic alias, so re-point it after a
production deploy:

```bash
vercel alias set <new-deployment-url> webanchor-demo.vercel.app
```

Nothing needs an API token or an environment variable in Vercel - the build is
static plus one function, and the network default is compiled in.

## Accounts

The page mints a throwaway keypair with `createAccount()` on load and signs
with it. Neither localnet nor studionet requires the sender to be funded, so
there is no faucet step and no wallet extension to install. `genlayer-js` does
support MetaMask, but neither chain needs it here.

## What you should see

NaiveWebReader ends `UNDETERMINED` / `MAJORITY_DISAGREE` with its validators
voting `disagree`, and its storage stays empty. AnchoredWebReader ends
`ACCEPTED` / `MAJORITY_AGREE`, and the page reads the stored fingerprint back
out of chain state with `get_last_fingerprint()`. Each transaction takes
roughly a minute on the hosted node, and both run concurrently so they hit the
same page in the same window.

The outcome is classified on `result_name`, not on `status`. Both transactions
roll forward to `FINALIZED` a few seconds after they settle, so by the time you
look, the status alone no longer tells the two outcomes apart.

`screenshot.png` is a finished localnet run and `screenshot-studionet.png` is a
finished run against the public deployment.
