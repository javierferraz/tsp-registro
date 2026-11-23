// Worker de Cloudflare: recibe las pulsaciones de botones de TSPBot y las pasa a GitHub
// (PAA2 P3-3, S-1268; 06 §4). No guarda estado: el registro es la única memoria.
//
// Tres filtros, en este orden; si uno falla, no sale nada hacia GitHub:
//   1. el secreto del webhook (cabecera X-Telegram-Bot-Api-Secret-Token) es el nuestro;
//   2. quien pulsa es Javier (callback_query.from.id == JAVIER_ID);
//   3. la acción es de la lista cerrada (misma expresión que org/telegram.py).
// Los mensajes de texto se ignoran: no hay órdenes en texto libre (D-17 de 02).
//
// Variables (secretos del Worker, nunca en el código): WEBHOOK_SECRET, JAVIER_ID,
// GITHUB_TOKEN (fine-grained, solo javierferraz/tsp-registro, Contents: read and write).
// REPO va en wrangler.toml.

export const ACCION = /^(?:p:(suave|dura|emergencia)|(r)|([axd]):([A-Za-z0-9][A-Za-z0-9._-]{0,40}))$/;
const PULSACION = /^[A-Za-z0-9_-]{1,40}$/;

function iguales(a, b) {
  // comparación de tiempo constante para el secreto
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

const responder = (cq, texto) => ({
  method: "answerCallbackQuery", callback_query_id: cq.id, text: texto, show_alert: false,
});

// Decide qué hacer con una actualización, sin red: { estado, despacho, respuesta, motivo }.
// `despacho` es el cuerpo para POST /repos/{REPO}/dispatches, o null.
export function decidir(secreto, update, env) {
  if (!env.WEBHOOK_SECRET || !env.JAVIER_ID) {
    return { estado: 500, despacho: null, respuesta: null, motivo: "sin configurar" };
  }
  if (!iguales(secreto, env.WEBHOOK_SECRET)) {
    return { estado: 401, despacho: null, respuesta: null, motivo: "secreto" };
  }
  const cq = update && update.callback_query;
  if (!cq || typeof cq.id !== "string") {
    return { estado: 200, despacho: null, respuesta: null, motivo: "no es un botón" };
  }
  if (!cq.from || String(cq.from.id) !== String(env.JAVIER_ID)) {
    return { estado: 200, despacho: null, respuesta: responder(cq, "No autorizado."),
             motivo: "identificador ajeno" };
  }
  if (typeof cq.data !== "string" || !ACCION.test(cq.data) || !PULSACION.test(cq.id)) {
    return { estado: 200, despacho: null, respuesta: responder(cq, "Acción no válida."),
             motivo: "fuera de la lista" };
  }
  return {
    estado: 200,
    despacho: { event_type: "telegram-boton", client_payload: { accion: cq.data, pulsacion: cq.id } },
    respuesta: responder(cq, "Recibido. Se registra en un minuto y te aviso."),
    motivo: "ok",
  };
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("", { status: 405 });
    let update = null;
    try { update = await request.json(); } catch { update = null; }
    const d = decidir(request.headers.get("X-Telegram-Bot-Api-Secret-Token"), update, env);
    // Registro mínimo en los logs del Worker: solo el motivo, nunca el identificador.
    console.log(`telegram: ${d.motivo}`);
    if (d.despacho) {
      const r = await fetch(`https://api.github.com/repos/${env.REPO}/dispatches`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "tsp-registro-telegram",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(d.despacho),
      });
      if (r.status !== 204) {
        console.log(`telegram: GitHub respondió ${r.status}`);
        d.respuesta.text = "No llegó a GitHub; prueba desde GitHub o al volver al PC.";
      }
    }
    // Telegram acepta la llamada de respuesta en el cuerpo del webhook: sin token del bot aquí.
    if (!d.respuesta) return new Response("", { status: d.estado });
    return new Response(JSON.stringify(d.respuesta), {
      status: 200, headers: { "Content-Type": "application/json" },
    });
  },
};
