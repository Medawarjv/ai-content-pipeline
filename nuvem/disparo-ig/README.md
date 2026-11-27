# Disparo do Instagram na nuvem

Worker da Cloudflare (plano grátis) que publica no Instagram na hora marcada, com o PC desligado.

**Por que existe:** a API do Instagram não tem agendamento.

**Como funciona:**
1. O PC prepara o post (`python tools/instagram_api.py reel|foto|carrossel|story ... --nuvem "AAAA-MM-DD HH:MM"`).
2. A Meta devolve um `creation_id`, que vale 24 h.
3. O script registra esse id no Worker (`POST /agendar`).
4. Um cron roda a cada minuto e chama o `media_publish` quando chega a hora.

## Implantar
Com o Node 22, nesta pasta:
1. `npx wrangler login`.
2. `npx wrangler kv namespace create FILA` e colar o `id` devolvido no `wrangler.toml`.
3. Segredos, um por vez (o wrangler pede o valor e nada fica em arquivo):
   - `npx wrangler secret put IG_TOKEN`;
   - `npx wrangler secret put IG_ID`;
   - `npx wrangler secret put CHAVE` (uma senha longa, a mesma que o script local manda no cabeçalho `x-chave`).
4. `npx wrangler deploy`.

## Rotas (cabeçalho `x-chave`)
| Rota | Faz |
|---|---|
| `POST /agendar` | `{creation_id, quando: "2026-11-25T19:00:00-03:00", rotulo, tipo}`; recusa mais de 24 h à frente (o contêiner vence) |
| `GET /fila` | pendentes e os 30 últimos feitos |
| `POST /cancelar` | `{creation_id}` |
| `POST /agora` | roda o disparo já (só com post real na fila) |
| `POST /soltar` | solta o freio depois de conferir a causa |

## Segurança
- **Freio:** erro de token, permissão, bloqueio ou política para a fila até alguém conferir e soltar.
- **Limite de uso da Meta:** os códigos 4, 17, 32 e 613 passam sozinhos; o post tenta de novo com espera crescente, sem freio.
- **Senha obrigatória:** sem a `CHAVE` configurada, toda rota responde 401.
