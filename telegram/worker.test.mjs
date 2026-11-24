// Pruebas del Worker sin red: node --test telegram/
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import worker, { decidir, RELOJ } from "./worker.mjs";

const ENV = { WEBHOOK_SECRET: "s3creto_de-prueba", JAVIER_ID: "1000", REPO: "javierferraz/tsp-registro",
              GITHUB_TOKEN: "falso" };
const boton = (data, from = 1000, id = "4242") => ({ update_id: 1, callback_query: { id, from: { id: from }, data } });

test("pulsación de Javier en la lista: se despacha a GitHub", () => {
  const d = decidir(ENV.WEBHOOK_SECRET, boton("p:suave"), ENV);
  assert.equal(d.motivo, "ok");
  assert.deepEqual(d.despacho, { event_type: "telegram-boton",
                                 client_payload: { accion: "p:suave", pulsacion: "4242" } });
  assert.equal(d.respuesta.method, "answerCallbackQuery");
  assert.equal(d.respuesta.callback_query_id, "4242");
});

test("identificador ajeno: rechazado, nada sale hacia GitHub", () => {
  for (const ajeno of [1001, 0, "1000 ", null]) {
    const d = decidir(ENV.WEBHOOK_SECRET, boton("p:suave", ajeno), ENV);
    assert.equal(d.motivo, "identificador ajeno", String(ajeno));
    assert.equal(d.despacho, null);
    assert.equal(d.respuesta.text, "No autorizado.");
  }
});

test("secreto del webhook que falta o no es el nuestro: 401", () => {
  for (const s of [null, "", "otro", ENV.WEBHOOK_SECRET + "x"]) {
    const d = decidir(s, boton("p:suave"), ENV);
    assert.equal(d.estado, 401);
    assert.equal(d.despacho, null);
  }
});

test("acciones fuera de la lista y texto libre: no se despachan", () => {
  for (const data of ["publicar", "p:total", "hola, pausa todo", "a:", "a:../x", "r;p:suave", undefined]) {
    const d = decidir(ENV.WEBHOOK_SECRET, boton(data), ENV);
    assert.equal(d.despacho, null, String(data));
  }
  const texto = decidir(ENV.WEBHOOK_SECRET, { update_id: 2, message: { from: { id: 1000 }, text: "pausa" } }, ENV);
  assert.equal(texto.motivo, "no es un botón");
  assert.equal(texto.despacho, null);
});

test("sin configurar: no se fía de nada", () => {
  const d = decidir("", boton("p:suave"), { ...ENV, WEBHOOK_SECRET: "" });
  assert.equal(d.estado, 500);
  assert.equal(d.despacho, null);
});

test("fetch: si GitHub no responde, avisa en el botón y no revienta", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => { throw new TypeError("red caída"); };
  const log = console.log; console.log = () => {};
  try {
    const r = await worker.fetch(new Request("https://w.example/", { method: "POST",
      headers: { "X-Telegram-Bot-Api-Secret-Token": ENV.WEBHOOK_SECRET },
      body: JSON.stringify(boton("p:suave")) }), ENV);
    assert.equal(r.status, 200);
    assert.match((await r.json()).text, /No llegó a GitHub/);
  } finally {
    globalThis.fetch = original; console.log = log;
  }
});

test("fetch: despacha con el token y responde a Telegram en el cuerpo", async () => {
  const llamadas = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (url, init) => { llamadas.push({ url, init }); return new Response(null, { status: 204 }); };
  const log = console.log; console.log = () => {};
  try {
    const req = (cuerpo, secreto = ENV.WEBHOOK_SECRET) => new Request("https://w.example/", {
      method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": secreto }, body: JSON.stringify(cuerpo) });
    let r = await worker.fetch(req(boton("r", 1000, "77")), ENV);
    assert.equal(r.status, 200);
    assert.equal((await r.json()).method, "answerCallbackQuery");
    assert.equal(llamadas.length, 1);
    assert.equal(llamadas[0].url, "https://api.github.com/repos/javierferraz/tsp-registro/dispatches");
    assert.equal(llamadas[0].init.headers.Authorization, "Bearer falso");
    assert.deepEqual(JSON.parse(llamadas[0].init.body).client_payload, { accion: "r", pulsacion: "77" });
    r = await worker.fetch(req(boton("r", 999, "78")), ENV);  // ajeno
    assert.equal((await r.json()).text, "No autorizado.");
    r = await worker.fetch(req(boton("r"), "malo"), ENV);
    assert.equal(r.status, 401);
    assert.equal(llamadas.length, 1, "ni el ajeno ni el secreto malo llegan a GitHub");
    r = await worker.fetch(new Request("https://w.example/"), ENV);
    assert.equal(r.status, 405);
  } finally {
    globalThis.fetch = original; console.log = log;
  }
});

test("reloj: los horarios de wrangler.toml son justo los de RELOJ", () => {
  const toml = readFileSync(new URL("./wrangler.toml", import.meta.url), "utf8");
  const crons = JSON.parse(toml.match(/^crons\s*=\s*(\[.*\])\s*$/m)[1]);
  assert.deepEqual([...crons].sort(), Object.keys(RELOJ).sort());
});

test("reloj: cada horario lanza su repository_dispatch", async () => {
  const llamadas = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (url, init) => { llamadas.push({ url, init }); return new Response(null, { status: 204 }); };
  const log = console.log; console.log = () => {};
  try {
    await worker.scheduled({ cron: "20 */6 * * *" }, ENV);
    await worker.scheduled({ cron: "30 6 * * *" }, ENV);
    assert.deepEqual(llamadas.map((l) => JSON.parse(l.init.body).event_type), ["reloj-latidos", "reloj-planificador"]);
    assert.equal(llamadas[0].url, "https://api.github.com/repos/javierferraz/tsp-registro/dispatches");
    assert.equal(llamadas[0].init.headers.Authorization, "Bearer falso");
    await assert.rejects(worker.scheduled({ cron: "0 0 * * *" }, ENV), /sin tipo/);
    assert.equal(llamadas.length, 2, "un horario desconocido no despacha nada");
  } finally {
    globalThis.fetch = original; console.log = log;
  }
});

test("reloj: si GitHub no acepta, la invocación sale fallida", async () => {
  const original = globalThis.fetch;
  const log = console.log; console.log = () => {};
  try {
    globalThis.fetch = async () => new Response("", { status: 403 });
    await assert.rejects(worker.scheduled({ cron: "30 6 * * *" }, ENV), /403/);
    globalThis.fetch = async () => { throw new TypeError("red caída"); };
    await assert.rejects(worker.scheduled({ cron: "30 6 * * *" }, ENV), /respondió 0/);
  } finally {
    globalThis.fetch = original; console.log = log;
  }
});
