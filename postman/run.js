// Zero-dependency runner for Vera.postman_collection.json (a tiny subset of Postman's pm/chai API).
// Same idea as `newman run`, without installing anything:   node postman/run.js http://127.0.0.1:8080
const fs = require("fs");
const path = require("path");

const baseUrl = (process.argv[2] || "http://127.0.0.1:8080").replace(/\/+$/, "");
const col = JSON.parse(fs.readFileSync(path.join(__dirname, "Vera.postman_collection.json"), "utf8"));
const vars = { baseUrl };
for (const v of col.variable || []) vars[v.key] = v.value;
const sub = (s) => s.replace(/\{\{(\w+)\}\}/g, (_, k) => (k in vars ? vars[k] : `{{${k}}}`));

function expect(actual) {
  let negate = false;
  const fail = (msg) => { throw new Error(msg); };
  const check = (ok, msg) => { if (negate ? ok : !ok) fail((negate ? "NOT " : "") + msg); };
  const api = {
    get to() { return api; }, get be() { return api; }, get and() { return api; }, get have() { return api; }, get all() { return api; },
    get not() { negate = !negate; return api; },
    get empty() { check(actual.length === 0, `expected ${JSON.stringify(actual)} to be empty`); return api; },
    eql: (v) => { check(JSON.stringify(actual) === JSON.stringify(v), `expected ${JSON.stringify(actual)} to eql ${JSON.stringify(v)}`); return api; },
    include: (v) => { check(actual.includes(v), `expected ${JSON.stringify(actual).slice(0, 120)} to include ${JSON.stringify(v)}`); return api; },
    match: (re) => { check(re.test(actual), `expected ${actual} to match ${re}`); return api; },
    below: (n) => { check(actual < n, `expected ${actual} < ${n}`); return api; },
    most: (n) => { check(actual <= n, `expected ${actual} <= ${n}`); return api; },
    a: (t) => { check(typeof actual === t, `expected type ${t}, got ${typeof actual}`); return api; },
    property: (k) => { check(actual != null && k in actual, `expected property ${k}`); return api; },
    keys: (...ks) => { const a = Object.keys(actual).sort().join(), b = ks.sort().join(); check(a === b, `expected keys ${b}, got ${a}`); return api; },
    status: (s) => { check(actual.code === s, `expected HTTP ${s}, got ${actual.code}`); return api; },
  };
  api.at = api;
  return api;
}

(async () => {
  let passed = 0, failed = 0, requests = 0;
  for (const folder of col.item) {
    console.log(`\n${folder.name}`);
    for (const it of folder.item) {
      requests++;
      const r = it.request;
      const t0 = Date.now();
      const res = await fetch(sub(r.url.raw), { method: r.method, headers: Object.fromEntries(r.header.map((h) => [h.key, h.value])),
                                                body: r.body ? sub(r.body.raw) : undefined });
      const text = await res.text();
      const response = { code: res.status, responseTime: Date.now() - t0, json: () => JSON.parse(text) };
      response.to = { have: { status: (s) => expect(response).to.have.status(s) } };
      const results = [];
      const pm = {
        response,
        test: (name, fn) => { try { fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message}`]); } },
        expect,
        collectionVariables: { set: (k, v) => { vars[k] = v; }, get: (k) => vars[k] },
      };
      const script = (it.event || []).filter((e) => e.listen === "test").flatMap((e) => e.script.exec).join("\n");
      new Function("pm", script)(pm);
      const bad = results.filter((x) => !x[0]);
      passed += results.length - bad.length; failed += bad.length;
      console.log(`  ${bad.length ? "✗" : "✓"} ${it.name}  [${res.status}, ${response.responseTime} ms, ${results.length - bad.length}/${results.length} tests]`);
      bad.forEach((b) => console.log(`      ✗ ${b[1]}`));
    }
  }
  console.log(`\n${requests} requests, ${passed} assertions passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
