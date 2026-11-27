// Disparo do Instagram na nuvem (Cloudflare Worker, plano grátis): publica na hora marcada com o PC desligado.
//
// Por que existe: a API do Instagram não agenda. O PC prepara o post (kit/instagram_api.py ... --nuvem
// "AAAA-MM-DD HH:MM": sobe a mídia, a Meta processa e devolve um creation_id que vale 24 h) e registra aqui
// "publicar X às HH:MM". O cron roda a cada minuto e chama o media_publish na hora certa, com o PC desligado.
//
// Segredos (só via `npx wrangler secret put`, nunca no wrangler.toml nem no git):
//   IG_TOKEN  token da conta (o mesmo token que o script local usa)
//   IG_ID     id da conta do Instagram (o mesmo "ig_id")
//   CHAVE     senha das rotas HTTP (o mesmo "worker_chave")
// KV FILA, chave única "fila" (um JSON), para ler 1x por minuto sem estourar a cota de list do plano grátis.
//
// Rotas (cabeçalho "x-chave: <CHAVE>"):
//   POST /agendar  {creation_id, quando: "2026-11-25T19:00:00-03:00", rotulo, tipo}
//   GET  /fila     itens pendentes e os 30 últimos feitos
//   POST /cancelar {creation_id}
//   POST /agora    roda o disparo já (só com post real na fila; nunca testar com id falso, ver README)
//   POST /soltar   solta o freio (depois de conferir por que a Meta recusou)

const GRAPH = "https://graph.facebook.com/v27.0";

async function lerFila(env) {
  return (await env.FILA.get("fila", "json")) || { pendentes: [], feitos: [] };
}

async function gravarFila(env, fila) {
  fila.feitos = fila.feitos.slice(-30);
  await env.FILA.put("fila", JSON.stringify(fila));
}

// Freio: erro de bloqueio, permissão, token ou política puxa o freio no KV e o cron para de chamar a Meta até
// alguém soltar (POST /soltar). Limite de uso da Meta (4 app, 17 usuário, 32 página, 613 taxa) passa sozinho em
// até 1 h e NÃO puxa o freio: o post tenta de novo, com espera crescente.
const CODIGOS_FREIO = new Set([10, 102, 190, 368]);
const MAX_TENTATIVAS = 5;              // por post, no total
const ESPERA_MIN = [0, 5, 15, 30, 60]; // minutos depois da hora marcada para cada tentativa

function erroMeta(j, onde) {
  const c = j.error.code || 0;
  const e = new Error(`${onde}: ${c}/${j.error.error_subcode || ""} ${j.error.message}`);
  e.freio = CODIGOS_FREIO.has(c) || (c >= 200 && c < 300) || /blocked/i.test(j.error.message || "");
  return e;
}

async function statusConteiner(env, id) {
  const r = await fetch(`${GRAPH}/${id}?fields=status_code,status&access_token=${encodeURIComponent(env.IG_TOKEN)}`);
  const j = await r.json();
  if (j.error) throw erroMeta(j, "status");
  return j.status_code;
}

async function publicar(env, item) {
  // 1ª tentativa: publica direto (1 chamada). Nas seguintes, confere antes o status do contêiner.
  // A Meta só publica um contêiner uma vez, então tentar de novo nunca duplica o post.
  const st = item.tentativas ? await statusConteiner(env, item.creation_id) : "FINISHED";
  if (st === "PUBLISHED") return { media_id: "", link: "", obs: "já estava publicado" };
  if (st === "EXPIRED" || st === "ERROR") {
    const e = new Error(`contêiner ${st}: precisa preparar de novo`);
    e.definitivo = true;
    throw e;
  }
  const url = `${GRAPH}/${env.IG_ID}/media_publish`;
  const corpo = new URLSearchParams({ creation_id: item.creation_id, access_token: env.IG_TOKEN });
  const r = await fetch(url, { method: "POST", body: corpo });
  const j = await r.json();
  if (j.error) throw erroMeta(j, "publish");
  let link = "";
  if (item.tipo !== "STORIES") {
    const p = await fetch(`${GRAPH}/${j.id}?fields=permalink&access_token=${encodeURIComponent(env.IG_TOKEN)}`);
    link = (await p.json()).permalink || "";
  }
  return { media_id: j.id, link };
}

async function disparar(env) {
  const fila = await lerFila(env);
  if (fila.freio) return { freio: fila.freio };
  const agora = Date.now();
  const vencidos = fila.pendentes.filter(
    (i) => Date.parse(i.quando) + ESPERA_MIN[Math.min(i.tentativas || 0, ESPERA_MIN.length - 1)] * 60000 <= agora);
  if (!vencidos.length) return { publicados: 0 };
  for (const item of vencidos) {
    if (fila.freio) break;
    const tentativa = { ...item, rodou_em: new Date().toISOString() };
    try {
      Object.assign(tentativa, await publicar(env, item), { estado: "publicado" });
    } catch (e) {
      tentativa.tentativas = (item.tentativas || 0) + 1;
      tentativa.erro = String(e.message || e);
      if (e.freio) fila.freio = { quando: new Date().toISOString(), motivo: tentativa.erro };
      // erro passageiro: tenta de novo com espera crescente; bloqueio ou contêiner vencido não tenta
      if (!e.definitivo && !e.freio && tentativa.tentativas < MAX_TENTATIVAS) {
        Object.assign(item, { tentativas: tentativa.tentativas, erro: tentativa.erro });
        continue;
      }
      tentativa.estado = "falhou";
    }
    fila.pendentes = fila.pendentes.filter((i) => i.creation_id !== item.creation_id);
    fila.feitos.push(tentativa);
  }
  await gravarFila(env, fila);
  return { publicados: vencidos.length };
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj, null, 1), { status, headers: { "content-type": "application/json" } });
}

export default {
  async scheduled(_evento, env, ctx) {
    ctx.waitUntil(disparar(env));
  },

  async fetch(req, env) {
    if (!env.CHAVE || req.headers.get("x-chave") !== env.CHAVE) return json({ erro: "sem chave" }, 401);
    const { pathname } = new URL(req.url);
    const fila = await lerFila(env);

    if (req.method === "GET" && pathname === "/fila") return json(fila);

    if (req.method === "POST" && pathname === "/agendar") {
      const b = await req.json();
      if (!b.creation_id || !b.quando || Number.isNaN(Date.parse(b.quando))) return json({ erro: "faltou creation_id ou quando" }, 400);
      if (Date.parse(b.quando) - Date.now() > 24 * 3600 * 1000) return json({ erro: "mais de 24 h à frente: o contêiner vence antes" }, 400);
      if (fila.pendentes.some((i) => i.creation_id === b.creation_id)) return json({ erro: "já agendado" }, 409);
      fila.pendentes.push({ creation_id: b.creation_id, quando: b.quando, rotulo: b.rotulo || "", tipo: b.tipo || "", criado_em: new Date().toISOString() });
      await gravarFila(env, fila);
      return json({ ok: true, pendentes: fila.pendentes.length });
    }

    if (req.method === "POST" && pathname === "/cancelar") {
      const b = await req.json();
      const antes = fila.pendentes.length;
      fila.pendentes = fila.pendentes.filter((i) => i.creation_id !== b.creation_id);
      await gravarFila(env, fila);
      return json({ ok: antes !== fila.pendentes.length });
    }

    if (req.method === "POST" && pathname === "/agora") return json(await disparar(env));

    if (req.method === "POST" && pathname === "/soltar") {
      const era = fila.freio;
      delete fila.freio;
      await gravarFila(env, fila);
      return json({ ok: true, era });
    }

    return json({ erro: "rota desconhecida" }, 404);
  },
};
