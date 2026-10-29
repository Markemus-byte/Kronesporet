// Tester for Workeren mot en D1-erstatning på node:sqlite (Node 22+). Kjør: node --test worker/test/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { test } from "node:test";

import worker, { parseSelection, verifyStripe } from "../src/index.js";

const SCHEMA = readFileSync(new URL("../schema.sql", import.meta.url), "utf8");

// Minimal D1: prepare().bind().first()/all()/run() og batch(), med ?1-parametre som i D1.
function fakeD1() {
  const db = new DatabaseSync(":memory:");
  db.exec(SCHEMA);
  const stmt = (sql, args = []) => ({
    bind: (...a) => stmt(sql, a),
    first: async () => db.prepare(sql).get(...args) ?? null,
    all: async () => ({ results: db.prepare(sql).all(...args) }),
    run: async () => ({ meta: { changes: Number(db.prepare(sql).run(...args).changes) } }),
  });
  return { raw: db, prepare: (sql) => stmt(sql), batch: async (list) => { for (const s of list) await s.run(); } };
}

function setup() {
  const mails = [];
  globalThis.fetch = async (url, init) => {
    mails.push({ url, body: JSON.parse(init.body) });
    return new Response("{}", { status: 201 });
  };
  const env = { DB: fakeD1(), SITE_URL: "https://kronesporet.no", AVSENDER: "varsler@kronesporet.no",
                API_TOKEN: "hemmelig", BREVO_API_KEY: "k", STRIPE_WEBHOOK_SECRET: "whsec_test",
                STRIPE_PAYMENT_LINK: "https://buy.stripe.com/test_123", STRIPE_PORTAL_URL: "" };
  const call = (path, init = {}) => worker.fetch(new Request(`https://varsler.example${path}`, init), env);
  return { env, mails, call };
}

const form = (fields) => {
  const f = new FormData();
  for (const [k, v] of Object.entries(fields)) for (const x of [].concat(v)) f.append(k, x);
  return f;
};

test("utvalget ryddes og avgrenses", () => {
  const sel = parseSelection(form({ kjoper: "915 429 785\nabc, 974760673", leverandor: "At\nAtea AS",
                                    cpv: "72000000-1, x, 45", sokeord: "ab, skytjenester", horisont: ["6", "99"] }));
  assert.deepEqual(sel.utvalg, [
    { type: "kjoper", verdi: "915429785" }, { type: "kjoper", verdi: "974760673" },
    { type: "leverandor", verdi: "Atea AS" }, { type: "cpv", verdi: "72000000-1" }, { type: "cpv", verdi: "45" },
    { type: "sokeord", verdi: "skytjenester" }]);
  assert.equal(sel.horisonter, "6");
  assert.equal(sel.csv, 0);
});

test("påmelding, bekreftelse, betaling, utsending og avmelding", async () => {
  const { env, mails, call } = setup();
  let r = await call("/pamelding", { method: "POST", body: form({ epost: "Test@Example.no", kjoper: "915429785",
                                                                  cpv: "72", horisont: ["3", "12"], csv: "1" }) });
  assert.equal(r.status, 303);
  assert.match(r.headers.get("location"), /status=sjekk-epost/);
  assert.equal(mails.length, 1);
  const link = mails[0].body.textContent.match(/https:\/\/\S+\/bekreft\?t=([0-9a-f]{48})/);
  assert.ok(link, "bekreftelseslenke i e-posten");
  const token = link[1];

  // samme adresse rett etterpå: ingen ny e-post
  r = await call("/pamelding", { method: "POST", body: form({ epost: "test@example.no", kjoper: "915429785" }) });
  assert.match(r.headers.get("location"), /status=vent/);
  assert.equal(mails.length, 1);

  // GET viser bare en knapp; ikke bekreftet ennå
  r = await call(`/bekreft?t=${token}`);
  assert.equal(r.status, 200);
  assert.equal(env.DB.raw.prepare("SELECT bekreftet FROM abonnenter").get().bekreftet, null);

  r = await call(`/bekreft?t=${token}`, { method: "POST" });
  assert.equal(r.status, 303);
  const pay = new URL(r.headers.get("location"));
  assert.equal(pay.host, "buy.stripe.com");
  assert.equal(pay.searchParams.get("client_reference_id"), token);
  assert.equal(pay.searchParams.get("prefilled_email"), "test@example.no");

  const auth = { headers: { authorization: "Bearer hemmelig" } };
  assert.equal((await call("/api/abonnenter")).status, 401);
  assert.deepEqual(await (await call("/api/abonnenter", auth)).json(), []); // ikke betalt ennå

  // Stripe: betalingen er gjennomført
  const event = JSON.stringify({ id: "evt_1", type: "checkout.session.completed",
    data: { object: { client_reference_id: token, customer: "cus_1", customer_details: { email: "test@example.no" } } } });
  const t = Math.floor(Date.now() / 1000);
  const { createHmac } = await import("node:crypto");
  const sig = createHmac("sha256", "whsec_test").update(`${t}.${event}`).digest("hex");
  r = await call("/stripe/webhook", { method: "POST", body: event, headers: { "stripe-signature": `t=${t},v1=${sig}` } });
  assert.equal(r.status, 200);
  r = await call("/stripe/webhook", { method: "POST", body: event, headers: { "stripe-signature": `t=${t},v1=bad${sig.slice(3)}` } });
  assert.equal(r.status, 400);

  const upd = JSON.stringify({ id: "evt_3", type: "customer.subscription.updated", data: { object: {
    customer: "cus_1", status: "active", items: { data: [{ price: { recurring: { interval: "year" } } }] } } } });
  const sig3 = createHmac("sha256", "whsec_test").update(`${t}.${upd}`).digest("hex");
  await call("/stripe/webhook", { method: "POST", body: upd, headers: { "stripe-signature": `t=${t},v1=${sig3}` } });

  const subs = await (await call("/api/abonnenter", auth)).json();
  assert.equal(subs.length, 1);
  assert.equal(subs[0].status, "active");
  assert.equal(subs[0].plan, "aar");
  assert.equal(subs[0].horisonter, "3,12");
  assert.equal(subs[0].sist_sendt, null);
  assert.deepEqual(subs[0].utvalg.map((u) => u.type).sort(), ["cpv", "kjoper"]);

  r = await call("/api/sendt", { method: "POST", headers: { authorization: "Bearer hemmelig" },
    body: JSON.stringify([{ email: "test@example.no", kjoring: "2026-10-24", tildelinger: 2, utlop: 5 }]) });
  assert.equal(r.status, 200);
  assert.equal((await (await call("/api/abonnenter", auth)).json())[0].sist_sendt, "2026-10-24");

  // abonnementet går ut i Stripe
  const del = JSON.stringify({ id: "evt_2", type: "customer.subscription.deleted", data: { object: { customer: "cus_1" } } });
  const sig2 = createHmac("sha256", "whsec_test").update(`${t}.${del}`).digest("hex");
  await call("/stripe/webhook", { method: "POST", body: del, headers: { "stripe-signature": `t=${t},v1=${sig2}` } });
  assert.deepEqual(await (await call("/api/abonnenter", auth)).json(), []);

  // gratis settes for hånd; avmelding stopper e-postene, sletting fjerner alt
  env.DB.raw.exec("UPDATE abonnenter SET status = 'gratis', gratis_grunn = 'redaksjon'");
  assert.equal((await (await call("/api/abonnenter", auth)).json()).length, 1);
  r = await call(`/avmeld?t=${token}`, { method: "POST", body: "List-Unsubscribe=One-Click" });
  assert.equal(r.status, 200);
  assert.deepEqual(await (await call("/api/abonnenter", auth)).json(), []);
  await call(`/slett?t=${token}`, { method: "POST" });
  assert.equal(env.DB.raw.prepare("SELECT COUNT(*) AS n FROM abonnenter").get().n, 0);
  assert.equal(env.DB.raw.prepare("SELECT COUNT(*) AS n FROM sendte_varsler").get().n, 0);
});

test("ugyldig påmelding", async () => {
  const { call, mails } = setup();
  let r = await call("/pamelding", { method: "POST", body: form({ epost: "ikke-en-adresse", kjoper: "915429785" }) });
  assert.match(r.headers.get("location"), /ugyldig-epost/);
  r = await call("/pamelding", { method: "POST", body: form({ epost: "a@example.no", kjoper: "123" }) });
  assert.match(r.headers.get("location"), /mangler-utvalg/);
  r = await call("/pamelding", { method: "POST", body: form({ epost: "a@example.no", kjoper: "915429785", nettside: "x" }) });
  assert.match(r.headers.get("location"), /sjekk-epost/);
  assert.equal(mails.length, 0);
});

test("Stripe-signatur med gammelt tidsstempel avvises", async () => {
  const { createHmac } = await import("node:crypto");
  const t = 1000;
  const sig = createHmac("sha256", "s").update(`${t}.{}`).digest("hex");
  assert.equal(await verifyStripe("{}", `t=${t},v1=${sig}`, "s", 300, t + 10), true);
  assert.equal(await verifyStripe("{}", `t=${t},v1=${sig}`, "s", 300, t + 1000), false);
});
