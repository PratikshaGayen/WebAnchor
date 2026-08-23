// Publicly reachable version of tests/integration/volatile_server.py.
//
// The demo is worthless against a static page: the whole claim is that the
// leader and every validator fetch the same URL independently, get different
// bytes back, and only the anchored contract still agrees. So this has to be
// genuinely volatile per request, and it has to stay uncached - if Vercel's
// edge served every validator the same bytes, the naive contract would reach
// consensus and the demo would quietly prove the opposite of what it claims.
//
// The page shape is copied from the Python server, and the placement of the
// volatile values is copied deliberately, not incidentally. Every fresh value
// lives in a <script> body, an HTML comment, an element attribute, or the
// <iframe> subtree. Every visible text node is static prose. WebAnchor's
// default policy drops scripts, iframes and comments structurally and
// canonicalizes the remaining text, but it does not band arbitrary numbers or
// redact arbitrary random strings sitting in ordinary visible prose
// (Policy.default() leaves number_band_mode="none" - see BENCHMARK.md). A
// view counter rendered into visible text is a known non-convergence case, so
// the counter here rides in an attribute where the library actually handles
// it. Moving any of these values into the prose would break the anchored
// contract, and that would be a fixture bug, not a library result.

const ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";

const AD_COPIES = [
  "40% off garden tools, today only!",
  "free shipping on orders over 50 euro!",
  "buy one get one on all winter coats!",
  "flash sale: 25% off checkout in the next hour!",
];

function rand(n) {
  let out = "";
  for (let i = 0; i < n; i += 1) {
    out += ALPHABET[Math.floor(Math.random() * ALPHABET.length)];
  }
  return out;
}

// Best-effort only. Serverless instances come and go and there are several of
// them, so this is not a real global count - it just has to move on every
// request served by a given instance, which is all the fixture needs.
let views = 0;

function page() {
  const now = Date.now();
  const nonce = rand(24);
  const adId = rand(8);
  const iso = new Date(now).toISOString().replace(/\.\d{3}Z$/, "Z");
  views += 1;

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Order Status</title>
  <script nonce="${nonce}">
    window.__RENDERED_AT__ = "${iso}";
    window.__REQUEST_ID__ = "req-${rand(8)}";
    window.__VIEWS__ = ${views};
  </script>
</head>
<body>
  <!-- build: ${rand(10)} deployed ${iso} from host web-${
    Math.floor(Math.random() * 99) + 1
  } -->
  <header><h1>Order Status</h1></header>
  <main>
    <p>Order 100418 was shipped on 12 April 2024.</p>
    <p>Carrier: Northwind Freight. Tracking: NW-88213-XZ.</p>
    <table>
      <tr><th>Item</th><th>Qty</th></tr>
      <tr><td>Widget 42</td><td>3</td></tr>
      <tr><td>Bracket 7</td><td>1</td></tr>
    </table>
    <form action="/orders/100418/cancel" method="post">
      <input type="hidden" name="csrf_token" value="${rand(32)}">
      <button type="submit">Cancel order</button>
    </form>
    <div class="ad-slot" data-campaign="${rand(
      8,
    )}" data-nonce="${nonce}" data-views="${views}">
      <iframe src="https://ads.example.com/creative/${adId}?cb=${now}" title="advert">
        Rotating creative: ${AD_COPIES[Math.floor(Math.random() * AD_COPIES.length)]}
      </iframe>
      <script nonce="${nonce}">renderAd("${adId}", ${now});</script>
    </div>
    <p>Delivery is expected within two business days.</p>
  </main>
  <footer><p>Northwind Trading Company</p></footer>
</body>
</html>
`;
}

export default function handler(req, res) {
  const body = page();
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  // no-store on all three layers. Cache-Control alone covers browsers and the
  // shared edge cache; the two CDN-specific headers are set as well so that a
  // future change to Vercel's defaults cannot start caching this by accident.
  res.setHeader("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0");
  res.setHeader("CDN-Cache-Control", "no-store");
  res.setHeader("Vercel-CDN-Cache-Control", "no-store");
  res.status(200).send(body);
}
