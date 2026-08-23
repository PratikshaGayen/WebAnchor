import { createClient, createAccount } from "genlayer-js";
import { localnet, studionet } from "genlayer-js/chains";
import { TransactionStatus } from "genlayer-js/types";
import localAddresses from "./contracts.json";
import studioAddresses from "./contracts.studionet.json";

// localnet is http://127.0.0.1:4000/api, which only means anything on the
// machine running the Studio Docker stack. studionet is the hosted node at
// https://studio.genlayer.com/api, same chain id (0xf22f), and it is what the
// deployed build talks to so a visitor needs no local setup. Set
// VITE_GL_NETWORK=localnet to point a dev server back at your own stack.
const NETWORK = import.meta.env.VITE_GL_NETWORK === "localnet" ? "localnet" : "studionet";
const chain = NETWORK === "localnet" ? localnet : studionet;
const deployed = NETWORK === "localnet" ? localAddresses : studioAddresses;

// On localnet the page has to be reachable from inside the consensus-worker
// containers, which live in Docker Desktop's VM and cannot see 127.0.0.1 on
// the host, and GenVM's web module rejects anything that isn't port 80/443 -
// hence host.docker.internal with no port suffix. See
// tests/integration/conftest.py for how that was established. On studionet
// none of that applies: it just needs a normal public https URL, which is what
// the serverless function in frontend/api/volatile.js serves. Prefer the
// origin the page is being served from so preview deploys point at their own
// copy of the function, and fall back to the production host for a dev server.
const PUBLIC_PAGE_HOST = "https://webanchor-demo.vercel.app";

function defaultUrl(): string {
  const override = import.meta.env.VITE_DEMO_PAGE_URL;
  if (override) return override;
  if (NETWORK === "localnet") return "http://host.docker.internal/orders/100418";
  const origin = window.location.origin;
  const base = origin.startsWith("https://") ? origin : PUBLIC_PAGE_HOST;
  return `${base}/orders/100418`;
}

// Statuses at which the vote is already in. Anything not in here means the
// transaction is still working its way through consensus. Note that ACCEPTED
// and UNDETERMINED both roll on to FINALIZED a few seconds later, so the
// status alone does not tell the two outcomes apart once that has happened --
// result_name does, and that is what the outcome is keyed off below.
const TERMINAL: string[] = [
  TransactionStatus.ACCEPTED,
  TransactionStatus.UNDETERMINED,
  TransactionStatus.FINALIZED,
  TransactionStatus.CANCELED,
  TransactionStatus.LEADER_TIMEOUT,
  TransactionStatus.VALIDATORS_TIMEOUT,
];

const POLL_MS = 1500;
// The hosted studionet node is shared and noticeably slower than a local
// stack, so this is generous on purpose.
const POLL_TIMEOUT_MS = 300_000;

// genlayer-js brands its hash type with a fixed length, so a plain string
// won't do. Borrow the exact type back off writeContract.
type TxHash = Awaited<ReturnType<GLClient["writeContract"]>>;
type GLClient = ReturnType<typeof createClient>;

type Panel = {
  root: HTMLElement;
  badge: HTMLElement;
  hash: HTMLElement;
  copy: HTMLButtonElement;
  status: HTMLElement;
  result: HTMLElement;
  timeline: HTMLElement;
  votes: HTMLElement;
  outcome: HTMLElement;
  eqWrap: HTMLElement;
  eq: HTMLElement;
  started: number;
};

function panel(id: string): Panel {
  const root = document.getElementById(id) as HTMLElement;
  const pick = (role: string) =>
    root.querySelector(`[data-role="${role}"]`) as HTMLElement;
  return {
    root,
    badge: pick("badge"),
    hash: pick("hash"),
    copy: pick("copy") as HTMLButtonElement,
    status: pick("status"),
    result: pick("result"),
    timeline: pick("timeline"),
    votes: pick("votes"),
    outcome: pick("outcome"),
    eqWrap: pick("eqwrap"),
    eq: pick("eq"),
    started: 0,
  };
}

const naive = panel("card-naive");
const anchored = panel("card-anchored");

const urlInput = document.getElementById("url") as HTMLInputElement;
const naiveAddr = document.getElementById("naive-address") as HTMLInputElement;
const anchoredAddr = document.getElementById("anchored-address") as HTMLInputElement;
const runButton = document.getElementById("run") as HTMLButtonElement;
const accountLabel = document.getElementById("account") as HTMLElement;
const banner = document.getElementById("banner") as HTMLElement;

urlInput.value = defaultUrl();
naiveAddr.value = deployed.naiveWebReader ?? "";
anchoredAddr.value = deployed.anchoredWebReader ?? "";

// A throwaway keypair minted in the page. Neither localnet nor studionet gates
// transactions on a funded balance, so there is nothing to top up and no
// wallet extension to install -- see frontend/README.md.
const account = createAccount();
const client = createClient({ chain, account });
accountLabel.textContent = `${NETWORK} - burner account ${account.address}`;

function setBanner(text: string, kind: "" | "error" | "ok" = "") {
  banner.textContent = text;
  banner.className = `banner ${kind}`.trim();
  banner.hidden = text === "";
}

function setBadge(p: Panel, text: string, kind: string) {
  p.badge.textContent = text;
  p.badge.className = `badge badge-${kind}`;
}

function step(p: Panel, text: string, kind: "done" | "pass" | "fail" = "done") {
  const li = document.createElement("li");
  li.className = kind;
  const label = document.createElement("span");
  label.textContent = text;
  const t = document.createElement("span");
  t.className = "t";
  t.textContent = `+${((performance.now() - p.started) / 1000).toFixed(1)}s`;
  li.append(label, t);
  p.timeline.append(li);
}

function reset(p: Panel) {
  p.started = performance.now();
  setBadge(p, "submitting", "running");
  p.hash.textContent = "-";
  p.copy.hidden = true;
  p.status.textContent = "-";
  p.result.textContent = "-";
  p.timeline.replaceChildren();
  p.votes.replaceChildren(li("no votes yet", "empty"));
  p.outcome.textContent = "Not read yet.";
  p.eqWrap.hidden = true;
  p.eq.textContent = "";
}

function li(text: string, cls = ""): HTMLLIElement {
  const el = document.createElement("li");
  if (cls) el.className = cls;
  el.textContent = text;
  return el;
}

function showHash(p: Panel, hash: TxHash) {
  p.hash.textContent = hash;
  p.copy.hidden = false;
  p.copy.onclick = async () => {
    await navigator.clipboard.writeText(hash);
    p.copy.textContent = "copied";
    setTimeout(() => (p.copy.textContent = "copy"), 1200);
  };
}

function renderVotes(p: Panel, votes: Record<string, string> | undefined) {
  if (!votes || Object.keys(votes).length === 0) {
    p.votes.replaceChildren(li("receipt exposed no vote map", "empty"));
    return;
  }
  const rows = Object.entries(votes).map(([addr, vote]) => {
    const row = document.createElement("li");
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = addr;
    const what = document.createElement("span");
    what.className = vote === "agree" ? "vote-agree" : "vote-disagree";
    what.textContent = vote;
    row.append(who, what);
    return row;
  });
  p.votes.replaceChildren(...rows);
}

// The leader's equivalence-principle output is the whole argument in one
// value: raw page bytes for the naive contract, a short fingerprint for the
// anchored one. Everything else on the receipt (notably node_config, which
// carries validator private keys on localnet) stays off the page.
function leaderEqOutput(receipt: any): string | null {
  const leader = receipt?.consensus_data?.leader_receipt?.[0];
  const outputs = leader?.eq_outputs;
  if (!outputs) return null;
  const first = outputs[Object.keys(outputs)[0]];
  const readable = first?.payload?.readable;
  return typeof readable === "string" ? readable : null;
}

async function pollUntilTerminal(p: Panel, hash: TxHash): Promise<any> {
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  let last = "";
  for (;;) {
    let tx: any;
    try {
      tx = await client.getTransaction({ hash });
    } catch {
      // A transaction can briefly 404 between submission and indexing.
      tx = null;
    }
    // getTransaction returns statusName; waitForTransactionReceipt returns
    // status_name. Accept either.
    const status = tx?.statusName ?? tx?.status_name;
    if (status && status !== last) {
      last = status;
      p.status.textContent = status;
      if (!TERMINAL.includes(status)) step(p, status.toLowerCase());
    }
    if (status && TERMINAL.includes(status)) return tx;
    if (Date.now() > deadline) {
      throw new Error(
        `gave up after ${POLL_TIMEOUT_MS / 1000}s waiting for a terminal status (last seen: ${last || "none"})`,
      );
    }
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
}

async function readAnchoredState(address: string): Promise<string[]> {
  const [fingerprint, policyId, url] = await Promise.all([
    client.readContract({ address: address as `0x${string}`, functionName: "get_last_fingerprint", args: [] }),
    client.readContract({ address: address as `0x${string}`, functionName: "get_last_policy_id", args: [] }),
    client.readContract({ address: address as `0x${string}`, functionName: "get_last_url", args: [] }),
  ]);
  return [String(fingerprint), String(policyId), String(url)];
}

async function runOne(
  p: Panel,
  kind: "naive" | "anchored",
  address: string,
  url: string,
) {
  reset(p);
  step(p, "submitting read_page");

  let hash: TxHash;
  try {
    hash = await client.writeContract({
      address: address as `0x${string}`,
      functionName: "read_page",
      args: [url],
      value: BigInt(0),
    });
  } catch (err: any) {
    setBadge(p, "error", "fail");
    step(p, `submit failed: ${err?.message ?? err}`, "fail");
    p.outcome.textContent = String(err?.message ?? err);
    return;
  }

  showHash(p, hash);
  step(p, "accepted by node, awaiting consensus");
  setBadge(p, "in consensus", "running");

  let receipt: any;
  try {
    receipt = await pollUntilTerminal(p, hash);
  } catch (err: any) {
    setBadge(p, "timed out", "fail");
    step(p, String(err?.message ?? err), "fail");
    return;
  }

  const status = String(receipt?.statusName ?? receipt?.status_name ?? "");
  const result = String(receipt?.result_name ?? "");
  p.status.textContent = status;
  p.result.textContent = result || "-";
  renderVotes(p, receipt?.consensus_data?.votes);

  const eq = leaderEqOutput(receipt);
  if (eq) {
    p.eqWrap.hidden = false;
    p.eq.textContent = eq.length > 4000 ? `${eq.slice(0, 4000)}\n... (truncated)` : eq;
  }

  if (kind === "naive") {
    // Consensus failure is the expected outcome here, so it is reported as a
    // pass of the demonstration while still being flagged red as a contract
    // outcome. If it ever succeeds, say so instead of pretending otherwise.
    const failedAsExpected = result === "MAJORITY_DISAGREE";
    setBadge(p, failedAsExpected ? "consensus failed" : "unexpected agreement", failedAsExpected ? "fail" : "pass");
    step(p, failedAsExpected ? `${status} / ${result} - as expected` : `${status} / ${result}`, failedAsExpected ? "fail" : "pass");
    let reading = "";
    try {
      reading = String(
        await client.readContract({
          address: address as `0x${string}`,
          functionName: "get_last_reading",
          args: [],
        }),
      );
    } catch {
      reading = "";
    }
    p.outcome.replaceChildren();
    const line = document.createElement("div");
    line.textContent = failedAsExpected
      ? "Every validator fetched the page independently and got different bytes, so strict_eq found no majority. Nothing was written to storage."
      : "This run reached consensus, which is not the expected outcome for raw bytes against volatile content.";
    const row = document.createElement("div");
    row.className = "row";
    row.append(document.createTextNode("get_last_reading() -> "));
    const val = document.createElement("span");
    val.textContent = reading === "" ? '"" (empty)' : `${reading.slice(0, 120)}...`;
    row.append(val);
    p.outcome.append(line, row);
    return;
  }

  const agreed = result === "MAJORITY_AGREE";
  setBadge(p, agreed ? "consensus reached" : "consensus failed", agreed ? "pass" : "fail");
  step(p, `${status} / ${result}`, agreed ? "pass" : "fail");

  if (!agreed) {
    p.outcome.textContent =
      "This run did not reach consensus, which is not the expected outcome for the anchored contract.";
    return;
  }

  try {
    const [fingerprint, policyId, storedUrl] = await readAnchoredState(address);
    step(p, "read state back via view methods", "pass");
    p.outcome.replaceChildren();
    const line = document.createElement("div");
    line.textContent =
      "Every validator fetched different bytes too, but normalization collapsed them to the same fingerprint, so strict_eq agreed and the value was written to storage.";
    const fp = document.createElement("code");
    fp.className = "fp";
    fp.textContent = `get_last_fingerprint() -> ${fingerprint}`;
    const policyRow = document.createElement("div");
    policyRow.className = "row";
    policyRow.append(document.createTextNode("get_last_policy_id() -> "));
    const pol = document.createElement("span");
    pol.textContent = policyId;
    policyRow.append(pol);
    const urlRow = document.createElement("div");
    urlRow.className = "row";
    urlRow.append(document.createTextNode("get_last_url() -> "));
    const u = document.createElement("span");
    u.textContent = storedUrl;
    urlRow.append(u);
    p.outcome.append(line, fp, policyRow, urlRow);
  } catch (err: any) {
    step(p, `view call failed: ${err?.message ?? err}`, "fail");
    p.outcome.textContent = String(err?.message ?? err);
  }
}

runButton.addEventListener("click", async () => {
  const url = urlInput.value.trim();
  const a = naiveAddr.value.trim();
  const b = anchoredAddr.value.trim();
  if (!url || !a || !b) {
    setBanner("Fill in the URL and both contract addresses first.", "error");
    return;
  }

  runButton.disabled = true;
  setBanner(
    "Both transactions are in flight. Real consensus over three validators takes roughly a minute each, sometimes longer on the shared hosted node.",
  );
  try {
    // Run them concurrently so both hit the same volatile page in the same
    // window, which is the honest version of the comparison.
    await Promise.all([
      runOne(naive, "naive", a, url),
      runOne(anchored, "anchored", b, url),
    ]);
    setBanner("Both runs finished.", "ok");
  } finally {
    runButton.disabled = false;
  }
});
