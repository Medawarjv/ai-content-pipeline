"""Instagram pela API oficial da Meta (Instagram API with Instagram Login), sem navegador.

Por que existe: a Meta diz que a API é o único acesso programático permitido; nada de automação de navegador. Pesquisa: pesquisa/meta-ferramentas-oficiais.md.

Token em cookies/instagram.json. Em uso desde 15/11: token "EA..." de usuário do sistema do app
"Foragido Publicador" (login unificado, graph.facebook.com), que NÃO expira. Token "IG..." (login do
Instagram, graph.instagram.com) também serve: vale 60 dias e renova sozinho depois de 30. Escopos: instagram_business_basic, instagram_business_content_publish,
instagram_business_manage_comments.

Uso:
  python tools/instagram_api.py token <TOKEN>             salva o token e mostra a conta
  python tools/instagram_api.py quem                       conta, idade do token e cota de 24 h
  python tools/instagram_api.py renovar                    renova o token (60 dias a partir de agora)
  python tools/instagram_api.py reel <video.mp4> --legenda-arquivo <txt> [--quadro <seg> | --capa <img>] [--fora-do-feed]
  python tools/instagram_api.py foto <img> --legenda-arquivo <txt>
  python tools/instagram_api.py carrossel <img1> <img2> ... --legenda-arquivo <txt>
  python tools/instagram_api.py story <img|video>
  python tools/instagram_api.py posts [--n 10]             últimos posts (id, data, link, comentários)
  python tools/instagram_api.py comentarios <media_id>     comentários e respostas de um post
  python tools/instagram_api.py responder <comment_id> "texto"
  python tools/instagram_api.py comentar <media_id> "texto"      1º comentário nosso (fixar só pelo app)
  python tools/instagram_api.py comentar-pos "<começo da legenda>" "texto"   1º comentário de post agendado na nuvem
        (agendar uns 3 min depois do post: agendar "AAAA-MM-DD HH:MM" -- comentar-pos "..." "...")
  python tools/instagram_api.py varrer --desde AAAA-MM-DD --saida fila/comentarios/<arq>.json  (formato do comentarios.py)
  python tools/instagram_api.py insights <media_id>      métricas do post (precisa de instagram_manage_insights)
  python tools/instagram_api.py insights-rodada [--dias 7] [--publico]   leitura diária: posts + conta (Agendador, 6h)
  python tools/instagram_api.py agendar "AAAA-MM-DD HH:MM" -- reel <mp4> --legenda-arquivo <txt>
        Agendador do Windows, pasta Foragido; o PC precisa estar ligado na hora (StartWhenAvailable roda ao ligar)
  python tools/instagram_api.py agendados | cancelar "<nome da tarefa>"
  python tools/instagram_api.py reel <mp4> --legenda-arquivo <txt> --nuvem "AAAA-MM-DD HH:MM"
        PADRÃO PARA AGENDAR (15/11): prepara agora e um Worker da Cloudflare publica na hora, com o PC desligado.
        Até 23h30 à frente (o post preparado vence em 24 h). Vale para reel, foto, carrossel e story.
  python tools/instagram_api.py nuvem [--meta] | nuvem-cancelar <creation_id>     fila da nuvem (--meta: status na Meta)
  python tools/instagram_api.py soltar     solta o freio (fila/IG-FREIO.json) depois de conferir a causa
  python tools/instagram_api.py concorrente gta6_hub outro_perfil [--n 12]   números públicos de concorrentes
  Opções de reel/foto/carrossel: --audio "nome do áudio" (Reel) · --colab a,b,c · --alt "texto" (imagem) ·
        --trial manual|auto (Trial Reel, só para não seguidores) · --ia (selo de IA) · --forcar (repost de propósito)
  python tools/instagram_api.py vigia      plano B no PC (tarefa \\Foragido\\vigia, a cada 5 min): publica o que a nuvem
                                           deixou passar 4+ min e grava fila/ALERTA-INSTAGRAM.md

Todo comando de publicar aceita --teste: cria o contêiner e espera ficar FINISHED, sem publicar
(não conta na cota de posts; conta na de 400 contêineres/24 h).

Pegadinhas (docs de 15/11/2026, v27.0):
- Imagem só JPEG, até 8 MB, 320-1440 px de largura; o script converte PNG/WEBP para JPEG sRGB.
- Imagem precisa de URL pública: o script abre um túnel temporário da Cloudflare (cloudflared, sem conta)
  servindo o arquivo do próprio PC; o link morre no fim do comando. Instalar: winget install --id Cloudflare.cloudflared
- Vídeo tenta upload direto (rupload.facebook.com); se a Meta recusar, vai pelo túnel.
- Reel: 3 s a 15 min, até 300 MB. Story em vídeo: 3 a 60 s, até 100 MB. Story pela API não tem adesivo.
- Legenda: até 2.200 caracteres, 30 hashtags e 20 menções.
- Limite: 50 posts publicados pela API em 24 h (carrossel conta 1).
"""
import argparse
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
ARQ_TOKEN = ROOT / "cookies" / "instagram.json"
VERSAO = "v27.0"
GRAPH = f"https://graph.instagram.com/{VERSAO}"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{VERSAO}"
LOG = ROOT / "aprendizados" / "instagram-api-log.jsonl"
VIDEO_EXT = {".mp4", ".mov"}
IG_FORAGIDO = os.environ.get("IG_ID", "")  # id da conta do Instagram (variável de ambiente)


# ---------- token ----------

def ler_token():
    if not ARQ_TOKEN.exists():
        sys.exit("❌ Sem token. Rodar: python tools/instagram_api.py token <TOKEN> (passo a passo em COMO-PUBLICAR.md)")
    dados = json.loads(ARQ_TOKEN.read_text(encoding="utf-8"))
    if dados.get("host") == "facebook":  # token de usuário do sistema (login unificado): nunca expira
        global GRAPH
        GRAPH = f"https://graph.facebook.com/{VERSAO}"
        return dados
    obtido = datetime.fromisoformat(dados["obtido"])
    idade = datetime.now() - obtido
    if idade > timedelta(days=59):
        sys.exit("❌ Token com mais de 59 dias: expirou ou vai expirar. Gerar outro no App Dashboard.")
    if idade > timedelta(days=30):
        dados = renovar(dados)
    return dados


def salvar(dados):
    ARQ_TOKEN.parent.mkdir(exist_ok=True)
    ARQ_TOKEN.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def renovar(dados):
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": dados["token"]}, timeout=30)
    j = r.json()
    if "access_token" not in j:
        print(f"⚠️ Não renovou o token: {j}")
        return dados
    dados["token"] = j["access_token"]
    dados["obtido"] = datetime.now().isoformat(timespec="seconds")
    salvar(dados)
    print(f"🔄 Token renovado; vale até {(datetime.now() + timedelta(seconds=j.get('expires_in', 0))):%d/%m/%Y}")
    return dados


# ---------- freio (15/11: a Meta bloqueou a API depois de horas de consulta a cada minuto) ----------
# Regra: nenhuma chamada se repete sozinha sem limite. Erro de bloqueio, permissão ou limite da Meta puxa o FREIO:
# toda chamada para até alguém conferir e rodar `soltar`. O teto de chamadas por hora é nosso: estourar só adia a
# chamada (sai com código 75 e a hora da próxima vaga), sem puxar o freio (17/11).
FREIO = ROOT / "fila" / "IG-FREIO.json"
CHAMADAS = ROOT / "fila" / "ig-chamadas.json"
TETO_HORA = 30  # chamadas de API por hora (1 post com comentário gasta ~10 a 15; pedido do JV em 15/11: nada de 80)
LEITURAS = ROOT / "fila" / "ig-leituras.json"
TETO_LEITURA_HORA = 40  # (era 120 até 18/11: somados, os 3 tetos davam 230 > 200 da Meta, sem contar o Worker) GET de varredura (comentários, respostas, listar posts, insights). A Meta aceita ~200/h por conta. JV, 16/11: a varredura puxava o freio das escritas
# 18/11 (JV): postagem tem prioridade sempre. Os GET de quem PUBLICA (subir, conferir contêiner, comentar, story) têm pista
# própria e nunca esperam a varredura: antes, 150 leituras de comentário travaram um Reel e dois stories de madrugada.
LEITURAS_PUB = ROOT / "fila" / "ig-leituras-pub.json"
TETO_LEITURA_PUB_HORA = 70  # 30 escritas + 50 leituras + 70 de publicação = 150; sobram ~50 das 200/h do app para o Worker da nuvem
LEITURAS_METRICAS = ROOT / "fila" / "ig-leituras-metricas.json"
TETO_LEITURA_METRICAS_HORA = 80  # 21/11: leitura diária de insights (6h, quando respostas e postagem
# quase não rodam); vaga própria para não tirar a da varredura. O uso real da Meta (conferir_uso) segue valendo.
COMANDOS_PUBLICAR = {"reel", "foto", "carrossel", "story", "comentar", "comentar-pos", "agendar", "agendados",
                     "cancelar", "nuvem", "nuvem-cancelar", "soltar", "executar"}
# códigos da Meta que não se resolvem tentando de novo: token/sessão (190, 102), permissão e bloqueio (10, 200-299),
# limites de uso (4, 17, 32, 613), spam ou política (368), conta checkpoint (subcódigos 459, 464, 490, 5258005)
CODIGOS_FREIO = {10, 102, 190, 368}
# 18/11 (JV: "os processos têm que continuar"): limite de uso da Meta (4 app, 17 usuário, 32 página, 613 taxa) passa
# sozinho em até 1 h. Em vez do freio, que parava tudo até alguém soltar à mão, entra uma ESPERA de 20 min que vence
# sozinha: as chamadas saem com código 75 ("tente mais tarde") e voltam depois, sem ninguém mexer.
CODIGOS_ESPERA = {4, 17, 32, 613}
ESPERA = ROOT / "fila" / "IG-ESPERA.json"
ESPERA_MIN = 20
# Pista da rotina de respostas e directs (IG_PISTA=rotina no rodar-respostas.cmd). Conta do app por hora:
# 30 escritas + 40 leituras + 70 de publicação + 30 e 8 da rotina = 178 das ~200 da Meta; o resto é do Worker da nuvem.
ROTINA_LEITURAS = ROOT / "fila" / "ig-rotina-leituras.json"
ROTINA_ESCRITAS = ROOT / "fila" / "ig-rotina-escritas.json"
TETO_ROTINA_LEITURA_HORA = 30   # uma varredura de comentários de 4 dias gasta ~26
TETO_ROTINA_ESCRITA_HORA = 8
REF_LEITURAS = ROOT / "fila" / "ig-leituras-referencias.json"
TETO_REF_LEITURA_HORA = 25  # vigia de referências (IG_PISTA=referencias, 24/11); o conferir_uso faz ele ceder acima de 70%
# Uso real do app, medido pela própria Meta (cabeçalho X-App-Usage, em % das 200/h, já com o Worker dentro).
USO = ROOT / "fila" / "ig-uso.json"
USO_CEDE, USO_PARA = 70, 90     # a partir de 70% a leitura e a rotina cedem a vez; a partir de 90% tudo espera


def puxar_freio(motivo):
    FREIO.write_text(json.dumps({"quando": datetime.now().isoformat(timespec="seconds"), "motivo": motivo},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    registrar({"acao": "freio", "motivo": motivo})
    try:
        alerta(f"FREIO PUXADO: {motivo}. Nada chama a API até conferir e rodar `instagram_api.py soltar`.")
    except Exception:
        pass


def conferir_freio():
    if FREIO.exists():
        f = json.loads(FREIO.read_text(encoding="utf-8"))
        sys.exit(f"❌ Freio do Instagram puxado em {f['quando']}: {f['motivo']}\n"
                 "   Conferir o Facebook/Instagram do JV e o painel do app; depois `python tools/instagram_api.py soltar`.")


def anotar_uso(cabecalhos):
    """Guarda o uso que a Meta devolve nas respostas, em % do limite: X-App-Usage (limite do app por hora) e
    X-Business-Use-Case-Usage (limite da conta do Instagram; conferido em 18/11: é o que vem em toda chamada). Se a Meta
    disser quanto falta para liberar (estimated_time_to_regain_access, em minutos), vira a espera automática."""
    try:
        medidas = []
        u = json.loads(cabecalhos.get("x-app-usage") or "{}")
        if u:
            medidas.append(u)
        for lista in json.loads(cabecalhos.get("x-business-use-case-usage") or "{}").values():
            medidas.extend(lista)
        if not medidas:
            return
        pct = max(max(m.get("call_count", 0), m.get("total_time", 0), m.get("total_cputime", 0)) for m in medidas)
        USO.write_text(json.dumps({"pct": pct, "quando": time.time(), "bruto": medidas}), encoding="utf-8")
        volta = max(m.get("estimated_time_to_regain_access", 0) for m in medidas)
        if volta:
            ESPERA.write_text(json.dumps({"ate": time.time() + volta * 60, "motivo": f"a Meta pediu {volta} min de pausa"},
                                         ensure_ascii=False), encoding="utf-8")
    except (ValueError, OSError, AttributeError, TypeError):
        pass


def conferir_uso():
    """Cede a vez antes de a Meta recusar: com o app acima de 70% do limite, leitura e rotina esperam; acima de 90%,
    tudo espera. Só vale medida com menos de 15 min (o uso cai sozinho ao longo da hora)."""
    try:
        u = json.loads(USO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if time.time() - u.get("quando", 0) > 900:
        return
    pct = u.get("pct", 0)
    publica = len(sys.argv) > 1 and sys.argv[1] in COMANDOS_PUBLICAR and os.environ.get("IG_PISTA") != "rotina"
    if pct >= USO_PARA or (pct >= USO_CEDE and not publica):
        volta = datetime.fromtimestamp(u["quando"] + 900)
        print(f"⏳ App do Instagram em {pct}% do limite por hora da Meta: "
              f"{'tudo espera' if pct >= USO_PARA else 'leitura e respostas cedem a vez à postagem'}. "
              f"Tentar de novo depois das {volta:%H:%M}.", file=sys.stderr)
        sys.exit(75)


def conferir_espera():
    try:
        e = json.loads(ESPERA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if time.time() < e.get("ate", 0):
        print(f"⏳ A Meta pediu pausa ({e.get('motivo')}). Nenhuma chamada sai até "
              f"{datetime.fromtimestamp(e['ate']):%H:%M}; tentar de novo depois disso.", file=sys.stderr)
        sys.exit(75)
    ESPERA.unlink(missing_ok=True)


def contar_chamada(leitura=False):
    """Escrita (POST) e leitura (GET) têm tetos separados por hora."""
    pub = leitura and len(sys.argv) > 1 and sys.argv[1] in COMANDOS_PUBLICAR
    if os.environ.get("IG_PISTA") == "rotina":
        # respostas a comentários e directs (fila/rodar-respostas.cmd): prioridade baixa, pista própria e pequena,
        # para nunca tirar vaga de postagem (JV, 18/11: "deixe um limite menor pra elas")
        arquivo, teto, tipo = ((ROTINA_LEITURAS, TETO_ROTINA_LEITURA_HORA, "leituras da rotina de respostas") if leitura
                               else (ROTINA_ESCRITAS, TETO_ROTINA_ESCRITA_HORA, "escritas da rotina de respostas"))
    elif os.environ.get("IG_PISTA") == "referencias":
        # 24/11 (JV): vigia dos perfis de referência (tools/referencias_ig.py, Business Discovery). Só leitura,
        # pista própria e pequena (~17 por olhada, 2 olhadas por dia: 8h e 20h), para nunca tirar vaga de postagem nem de respostas.
        arquivo, teto, tipo = REF_LEITURAS, TETO_REF_LEITURA_HORA, "leituras do vigia de referências"
    elif pub:
        arquivo, teto, tipo = LEITURAS_PUB, TETO_LEITURA_PUB_HORA, "leituras de publicação"
    elif leitura and len(sys.argv) > 1 and sys.argv[1] == "insights-rodada":
        arquivo, teto, tipo = LEITURAS_METRICAS, TETO_LEITURA_METRICAS_HORA, "leituras de métricas"
    else:
        arquivo, teto, tipo = (LEITURAS, TETO_LEITURA_HORA, "leituras") if leitura else (CHAMADAS, TETO_HORA, "escritas")
    agora = time.time()
    try:
        lista = [t for t in json.loads(arquivo.read_text(encoding="utf-8")) if agora - t < 3600]
    except (OSError, ValueError):
        lista = []
    if len(lista) >= teto:
        # 17/11: o teto é nosso, não da Meta. Estourar não puxa mais o freio (que travava tudo até alguém soltar à mão,
        # 2 das 3 vezes até aqui): a chamada não sai, nada chega na Meta, e quem chamou tenta de novo depois da vaga.
        # O freio fica só para erro real da Meta (CODIGOS_FREIO). Código de saída 75 = "tente mais tarde".
        vaga = datetime.fromtimestamp(min(lista) + 3600)
        registrar({"acao": "teto_espera", "tipo": tipo, "chamadas_hora": len(lista), "vaga_as": f"{vaga:%H:%M}"})
        print(f"⏳ Teto próprio do Instagram: {len(lista)} {tipo} na última hora (teto {teto}). A chamada não saiu; "
              f"a próxima vaga abre às {vaga:%H:%M}. Tentar de novo depois disso.", file=sys.stderr)
        sys.exit(75)
    lista.append(agora)
    arquivo.write_text(json.dumps(lista), encoding="utf-8")


def api(metodo, caminho, tok, **params):
    conferir_freio()
    conferir_espera()
    conferir_uso()
    contar_chamada(leitura=(metodo == "GET"))
    params["access_token"] = tok
    url = caminho if caminho.startswith("http") else f"{GRAPH}/{caminho.lstrip('/')}"
    r = requests.request(metodo, url, params=params if metodo == "GET" else None,
                         data=None if metodo == "GET" else params, timeout=120)
    anotar_uso(r.headers)
    try:
        j = r.json()
    except ValueError:
        sys.exit(f"❌ Resposta não-JSON ({r.status_code}) em {caminho}: {r.text[:300]}")
    if "error" in j:
        e = j["error"]
        texto = f"{e.get('type')} {e.get('code')}/{e.get('error_subcode')}: {e.get('message')}"
        cod = e.get("code") or 0
        if cod in CODIGOS_ESPERA:
            ate = time.time() + ESPERA_MIN * 60
            ESPERA.write_text(json.dumps({"ate": ate, "motivo": texto}, ensure_ascii=False), encoding="utf-8")
            registrar({"acao": "espera_meta", "motivo": texto, "ate": f"{datetime.fromtimestamp(ate):%H:%M}"})
            try:  # 3ª pausa em 3 h: algo está gastando demais. Não trava, mas avisa o JV.
                corte = (datetime.now() - timedelta(hours=3)).isoformat(timespec="seconds")
                rec = [l for l in LOG.read_text(encoding="utf-8").splitlines()[-400:]
                       if '"espera_meta"' in l and json.loads(l).get("quando", "") >= corte]
                if len(rec) >= 3:
                    alerta(f"Instagram: {len(rec)} pausas por limite da Meta em 3 h ({texto}). O trabalho segue, "
                           "mas algo está chamando a API demais: ver aprendizados/instagram-api-log.jsonl.")
            except Exception:
                pass
            print(f"⏳ {texto}. Limite de uso da Meta: pausa de {ESPERA_MIN} min, até "
                  f"{datetime.fromtimestamp(ate):%H:%M}. Vence sozinha; tentar de novo depois.", file=sys.stderr)
            sys.exit(75)
        if cod in CODIGOS_FREIO or 200 <= cod < 300 or "blocked" in str(e.get("message", "")).lower():
            puxar_freio(f"{caminho}: {texto}")
            sys.exit(f"❌ {texto}\n   Freio puxado: nenhuma nova chamada até `soltar`.")
        raise RuntimeError(texto)
    return j


def registrar(evento):
    evento["quando"] = datetime.now().isoformat(timespec="seconds")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(evento, ensure_ascii=False) + "\n")


# ---------- arquivos ----------

def jpeg(caminho):
    """Devolve bytes JPEG sRGB, até 1440 px de largura e 8 MB."""
    from PIL import Image
    img = Image.open(caminho)
    if img.mode != "RGB":
        fundo = Image.new("RGB", img.size, (0, 0, 0))
        fundo.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
        img = fundo
    if img.width > 1440:
        img = img.resize((1440, round(img.height * 1440 / img.width)), Image.LANCZOS)
    for q in (95, 90, 85, 80):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True)
        if buf.tell() <= 8 * 1024 * 1024:
            return buf.getvalue()
    sys.exit(f"❌ {caminho} não coube em 8 MB nem com qualidade 80")


class Tunel:
    """Serve uma pasta temporária do PC por um link público da Cloudflare (quick tunnel, sem conta).

    A Meta exige URL pública para imagem (e para vídeo quando o upload direto falha). Os arquivos
    ficam só no PC; o link morre quando o comando termina.
    """
    def __init__(self):
        self.url = None

    def __enter__(self):
        return self

    def iniciar(self):
        import http.server, re, shutil, socket, subprocess, tempfile, threading
        exe = shutil.which("cloudflared") or next((str(c) for c in (
            ROOT / "tools" / "bin" / "cloudflared.exe",
            Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"),
            Path(r"C:\Program Files\cloudflared\cloudflared.exe")) if c.exists()), None)
        if not exe:
            sys.exit("❌ Falta o cloudflared (link público temporário). Instalar: winget install --id Cloudflare.cloudflared")
        self.pasta = Path(tempfile.mkdtemp(prefix="ig_"))
        with socket.socket() as so:
            so.bind(("127.0.0.1", 0)); porta = so.getsockname()[1]
        pasta = str(self.pasta)
        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(s, *a, **k): super().__init__(*a, directory=pasta, **k)
            def log_message(s, *a): pass
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", porta), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{porta}"],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        inicio = time.time()
        for linha in self.proc.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", linha)
            if m:
                self.url = m.group(0); break
            if time.time() - inicio > 60:
                break
        if not self.url:
            sys.exit("❌ O cloudflared não devolveu o link em 60 s")
        threading.Thread(target=lambda: [None for _ in self.proc.stdout], daemon=True).start()
        teste = self.pasta / "ok.txt"; teste.write_text("ok")
        for _ in range(30):  # o DNS do link novo leva alguns segundos
            try:
                if requests.get(f"{self.url}/ok.txt", timeout=10).text == "ok":
                    return
            except requests.RequestException:
                pass
            time.sleep(2)
        sys.exit("❌ O link do túnel não respondeu em 60 s")

    def publicar(self, nome, conteudo):
        if not self.url:
            self.iniciar()
        nome = f"{int(time.time()*1000)}_{nome}".replace(" ", "_")
        (self.pasta / nome).write_bytes(conteudo)
        return f"{self.url}/{nome}"

    def __exit__(self, *a):
        if self.url:
            self.proc.terminate(); self.srv.shutdown()
            import shutil; shutil.rmtree(self.pasta, ignore_errors=True)
            self.url = None


TUNEL = Tunel()


def subir_temporario(nome, conteudo):
    return TUNEL.publicar(nome, conteudo)


def url_imagem(caminho):
    dados = jpeg(caminho)
    url = subir_temporario(Path(caminho).stem + ".jpg", dados)
    print(f"   imagem no ar: {url}")
    return url


def conferir_video(caminho, tipo):
    p = Path(caminho)
    if not p.exists():
        sys.exit(f"❌ Não existe: {p}")
    mb = p.stat().st_size / 1024 / 1024
    limite = 100 if tipo == "STORIES" else 300
    if mb > limite:
        sys.exit(f"❌ {p.name} tem {mb:.0f} MB; {tipo} aceita até {limite} MB. Não recomprimir sem o JV decidir.")
    specs_video(p, tipo)


def specs_video(p, tipo):
    """Confere o arquivo contra as regras da Meta ANTES de subir. O arquivo vai byte a byte (sem recompressão
    nossa); quem recomprime é o Instagram, e ele recomprime menos e melhor quando o arquivo já está dentro das regras."""
    import subprocess
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,codec_name,width,height,avg_frame_rate,pix_fmt,color_range,sample_rate,bit_rate"
                        ":format=duration,bit_rate", "-of", "json", str(p)], capture_output=True, text=True)
    if r.returncode:
        print(f"⚠️ ffprobe não leu {p.name}; seguindo sem a conferência técnica")
        return
    j = json.loads(r.stdout)
    v = next((x for x in j["streams"] if x["codec_type"] == "video"), {})
    a = next((x for x in j["streams"] if x["codec_type"] == "audio"), {})
    dur = float(j["format"].get("duration", 0))
    num, den = (v.get("avg_frame_rate") or "0/1").split("/")
    fps = float(num) / float(den or 1)
    vbr = int(v.get("bit_rate") or j["format"].get("bit_rate") or 0) / 1e6
    erros, avisos = [], []
    if v.get("codec_name") not in ("h264", "hevc"):
        erros.append(f"vídeo em {v.get('codec_name')} (a Meta aceita H.264 ou HEVC)")
    if int(v.get("width") or 0) > 1920:
        erros.append(f"largura {v.get('width')} px (máximo 1920)")
    if not 23 <= fps <= 60:
        erros.append(f"{fps:.2f} fps (aceita de 23 a 60)")
    if vbr > 25:  # a doc diz máximo 25, mas em 15/11 a Meta aceitou 34 Mbps (FINISHED): só aviso
        avisos.append(f"{vbr:.1f} Mbps de vídeo (a doc da Meta diz até 25); o que vale é o status FINISHED do --teste")
    if a and int(a.get("sample_rate") or 0) > 48000:
        erros.append(f"áudio a {a.get('sample_rate')} Hz (máximo 48 kHz)")
    if a and a.get("codec_name") != "aac":
        erros.append(f"áudio em {a.get('codec_name')} (a Meta pede AAC)")
    lim = (3, 60) if tipo == "STORIES" else (3, 900)
    if not lim[0] <= dur <= lim[1]:
        erros.append(f"{dur:.1f} s (aceita de {lim[0]} a {lim[1]} s)")
    if (v.get("pix_fmt") or "").startswith("yuvj") or v.get("color_range") == "pc":
        avisos.append("cor em faixa cheia (yuvj/pc): o Instagram pode lavar ou escurecer a cor; "
                      "no próximo render, exportar yuv420p com faixa limitada (tv)")
    if int(v.get("width") or 0) < 1080:
        avisos.append(f"largura {v.get('width')} px: abaixo de 1080 o Instagram amplia e perde nitidez")
    if 0 < vbr < 6:
        avisos.append(f"só {vbr:.1f} Mbps: pouco para 1080p; o Instagram vai recomprimir em cima de pouca informação")
    with p.open("rb") as f:
        cab = f.read(1024 * 1024)
    i, m = cab.find(b"moov"), cab.find(b"mdat")
    if i == -1 or (m != -1 and m < i):
        avisos.append("moov no fim do arquivo (sem faststart): a Meta pede no início; pode atrasar ou falhar o processamento")
    print(f"   arquivo: {v.get('width')}x{v.get('height')} {v.get('codec_name')} {fps:.0f} fps {vbr:.1f} Mbps · "
          f"áudio {a.get('codec_name')} {a.get('sample_rate')} Hz · {dur:.1f} s · enviado sem recompressão")
    for x in avisos:
        print(f"   ⚠️ {x}")
    if erros:
        sys.exit("❌ Fora das regras da Meta: " + "; ".join(erros) + ". Corrigir no render, não aqui.")


# ---------- contêiner ----------

def faststart(p):
    """21/11: o Esquema #7 (moov no fim) virou contêiner ERROR na nuvem. Se o moov está no fim, faz uma cópia com o
    moov no início, só remuxando (-c copy: mesmos bytes de vídeo e áudio, sem recompressão). O original fica igual."""
    with p.open("rb") as f:
        cab = f.read(1024 * 1024)
    i, m = cab.find(b"moov"), cab.find(b"mdat")
    if not (i == -1 or (m != -1 and m < i)):
        return p
    import subprocess, tempfile
    out = Path(tempfile.gettempdir()) / f"ig-faststart-{p.stem}.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(p), "-map", "0", "-c", "copy",
                        "-movflags", "+faststart", str(out)], capture_output=True, text=True)
    if r.returncode or not out.exists():
        print(f"⚠️ faststart falhou ({r.stderr[:200]}); subindo o original")
        return p
    print("   moov movido para o início (remux sem recompressão)")
    return out


def conteiner_video(dados, caminho, **campos):
    """Cria o contêiner de vídeo: upload direto primeiro, host temporário se a Meta recusar."""
    tok, ig = dados["token"], dados["ig_id"]
    p = faststart(Path(caminho))
    try:
        j = api("POST", f"{ig}/media", tok, upload_type="resumable", **campos)
        r = requests.post(j.get("uri") or f"{RUPLOAD}/{j['id']}", data=p.read_bytes(), timeout=1800,
                          headers={"Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(p.stat().st_size)})
        if r.ok and r.json().get("success"):
            print("   vídeo enviado direto (rupload)")
            return j["id"]
        print(f"⚠️ upload direto recusado ({r.status_code}): {r.text[:200]}")
    except RuntimeError as e:
        print(f"⚠️ upload direto não aceito: {e}")
    url = subir_temporario(p.name, p.read_bytes())
    print(f"   vídeo no ar: {url}")
    return api("POST", f"{ig}/media", tok, video_url=url, **campos)["id"]


def esperar(tok, cid, limite_s=660):
    """Espera a Meta processar o contêiner, com consultas espaçadas (JV, 18/11: "58 consultas de status de upload
    parece muito exagerado"). Antes era de 20 em 20 s (~10 consultas por vídeo de 270 MB, que leva 3 a 4 min); agora
    a 1ª consulta sai aos 45 s e as seguintes a 45, 60, 60, 90, 90, 120... (4 a 5 consultas no mesmo vídeo)."""
    inicio = time.time()
    for pausa in (45, 45, 60, 60, 90, 90, 120, 150):
        time.sleep(pausa)
        j = api("GET", cid, tok, fields="status_code,status")
        st = j.get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"contêiner {cid}: {st} {j.get('status')}")
        if time.time() - inicio >= limite_s:
            break
    raise RuntimeError(f"contêiner {cid} ainda processando depois de {time.time() - inicio:.0f} s")


ARQ_NUVEM = ROOT / "cookies" / "disparo.json"
NUVEM = {"quando": None}  # preenchido pelo --nuvem


def nuvem(metodo, rota, corpo=None):
    c = json.loads(ARQ_NUVEM.read_text(encoding="utf-8"))
    r = requests.request(metodo, c["url"] + rota, json=corpo, headers={"x-chave": c["chave"]}, timeout=60)
    j = r.json()
    if r.status_code >= 400:
        raise RuntimeError(f"disparo na nuvem: {r.status_code} {j}")
    return j


def quando_nuvem(texto):
    q = datetime.strptime(texto, "%Y-%m-%d %H:%M")
    falta = q - datetime.now()
    if falta < timedelta(minutes=3):
        sys.exit(f"❌ --nuvem {texto}: no passado ou a menos de 3 min")
    if falta > timedelta(hours=23, minutes=30):
        sys.exit(f"❌ --nuvem {texto}: mais de 23h30 à frente. O post preparado vence em 24 h; preparar mais perto "
                 "da hora (ou usar o agendar do Windows, que prepara na hora)")
    return q


def copyright(tok, cid, tipo):
    """Contêiner de vídeo: a Meta checa copyright antes de publicar. Avisa (não barra: nosso conteúdo usa
    clipes e música de terceiros e o JV decide)."""
    if tipo not in ("REELS", "STORIES", "CAROUSEL"):
        return
    c, st = {}, None
    for pausa in (0, 40, 60, 80):  # até 3 min, no máximo 4 consultas (eram 6, de 20 em 20 s; JV, 18/11)
        time.sleep(pausa)
        try:
            c = api("GET", cid, tok, fields="copyright_check_status").get("copyright_check_status") or {}
        except RuntimeError:
            return
        st = c.get("status")
        if st in ("completed", "complete", "error"):
            break
    if c.get("matches_found"):
        print("   ⚠️ COPYRIGHT: a Meta achou conteúdo protegido neste vídeo (matches_found). Pode sair sem som, "
              "com alcance menor ou ser bloqueado. Avisar o JV antes de publicar.")
    else:
        print(f"   copyright: {st or 'sem resposta'}, sem conteúdo protegido encontrado")


def publicar(dados, cid, tipo, origem, teste):
    tok, ig = dados["token"], dados["ig_id"]
    esperar(tok, cid)
    copyright(tok, cid, tipo)
    if NUVEM["quando"] and not teste:
        q = NUVEM["quando"]
        j = nuvem("POST", "/agendar", {"creation_id": cid, "quando": q.strftime("%Y-%m-%dT%H:%M:00-03:00"),
                                       "rotulo": Path(origem).parent.name, "tipo": tipo})
        registrar({"acao": "agendado_nuvem", "tipo": tipo, "creation_id": cid, "quando_publica": f"{q:%Y-%m-%d %H:%M}",
                   "arquivo": str(Path(origem).resolve())})
        print(f"☁️ {tipo} preparado (contêiner {cid}) e agendado na nuvem para {q:%d/%m/%Y %H:%M}. "
              f"O PC pode desligar. {j.get('pendentes')} na fila. Conferir: python tools/instagram_api.py nuvem")
        return None
    if teste:
        print(f"🧪 Teste: contêiner {cid} FINISHED, NÃO publicado.")
        return None
    mid = api("POST", f"{ig}/media_publish", tok, creation_id=cid)["id"]
    link = ""
    if tipo != "STORIES":
        link = api("GET", mid, tok, fields="permalink").get("permalink", "")
    publicado = datetime.now()
    registrar({"acao": "publicado", "tipo": tipo, "media_id": mid, "link": link, "arquivo": str(Path(origem).resolve())})
    print(f"✅ {tipo} publicado {publicado:%d/%m/%Y %H:%M} · id {mid} {link}")
    return mid


def legenda(args):
    if not args.legenda_arquivo:
        return None
    txt = Path(args.legenda_arquivo).read_text(encoding="utf-8").strip()
    if len(txt) > 2200:
        sys.exit(f"❌ Legenda com {len(txt)} caracteres (máx. 2.200)")
    if txt.count("#") > 5:  # o Instagram passou a limitar a 5 (JV, 16/11); com 6-7 o Reel saiu sem legenda
        sys.exit(f"❌ Legenda com {txt.count('#')} hashtags (máx. 5 desde 16/11)")
    if txt.count("@") > 20:
        sys.exit("❌ Legenda com mais de 20 menções")
    return txt


# ---------- comandos ----------

def cmd_token(args):
    tok = args.valor.strip()
    if tok.startswith("EA"):  # login unificado (Facebook): token de usuário do sistema, sem /me do Instagram
        global GRAPH
        GRAPH = f"https://graph.facebook.com/{VERSAO}"
        ig = args.ig_id or IG_FORAGIDO
        me = api("GET", ig, tok, fields="id,username")
        salvar({"token": tok, "ig_id": me["id"], "username": me["username"], "host": "facebook",
                "obtido": datetime.now().isoformat(timespec="seconds")})
        print(f"✅ Token (login unificado, não expira) salvo para @{me['username']} (id {me['id']}) em {ARQ_TOKEN}")
        return
    me = api("GET", "me", tok, fields="user_id,username,account_type")
    dados = {"token": tok, "ig_id": str(me["user_id"]), "username": me["username"],
             "obtido": datetime.now().isoformat(timespec="seconds")}
    salvar(dados)
    print(f"✅ Token salvo para @{me['username']} (id {me['user_id']}, {me.get('account_type')}) em {ARQ_TOKEN}")
    if me["username"] != "foragido_gta":
        print("⚠️ A conta não é @foragido_gta. Conferir antes de publicar qualquer coisa.")


def cmd_quem(args):
    dados = ler_token()
    me = api("GET", dados["ig_id"], dados["token"], fields="username,followers_count,media_count")
    idade = (datetime.now() - datetime.fromisoformat(dados["obtido"])).days
    print(f"@{me['username']} · {me.get('followers_count')} seguidores · "
          f"{me.get('media_count')} posts · token com {idade} dias")
    try:
        lim = api("GET", f"{dados['ig_id']}/content_publishing_limit", dados["token"], fields="quota_usage,config")
        d = lim["data"][0]
        print(f"Cota: {d.get('quota_usage')} de {d.get('config', {}).get('quota_total')} posts nas últimas 24 h")
    except (RuntimeError, KeyError, IndexError) as e:
        print(f"(cota não lida: {e})")


def cmd_renovar(args):
    renovar(json.loads(ARQ_TOKEN.read_text(encoding="utf-8")))


def extras(args, tipo):
    """Campos opcionais da API: nome do áudio, colaboradores, texto alternativo, Trial Reel, selo de IA."""
    c = {}
    if getattr(args, "audio", None) and tipo == "REELS":
        c["audio_name"] = args.audio  # só Reels; renomeia o áudio original UMA vez (não escolhe música)
    if getattr(args, "colab", None) and tipo != "STORIES":
        c["collaborators"] = json.dumps([u.lstrip("@") for u in args.colab.split(",")][:3])
    if getattr(args, "alt", None) and tipo == "IMAGE":
        c["alt_text"] = args.alt[:1000]
    if getattr(args, "trial", None) and tipo == "REELS":
        c["trial_params"] = json.dumps({"graduation_strategy": "SS_PERFORMANCE" if args.trial == "auto" else "MANUAL"})
    if getattr(args, "ia", False) and tipo != "STORIES":
        c["is_ai_generated"] = "true"
    return c


def cmd_reel(args):
    dados = ler_token()
    conferir_video(args.arquivo, "REELS")
    campos = {"media_type": "REELS", "share_to_feed": "false" if args.fora_do_feed else "true", **extras(args, "REELS")}
    cap = legenda(args)
    if cap:
        campos["caption"] = cap
    if args.capa is None and args.quadro is None and not args.sem_capa:
        # JV, 16/11: capa escolhida em todo Reel (clique no Explorar e grade bonita); o padrão é um bom quadro do vídeo,
        # capa desenhada só nos Shorts em que vamos investir mais
        sys.exit("❌ Reel sem capa. Passe --quadro <segundos> (um bom quadro do vídeo) ou --capa <img>; --sem-capa só de propósito")
    if args.quadro is not None and not args.capa:
        campos["thumb_offset"] = str(int(float(args.quadro) * 1000))  # ms; a Meta ignora se houver cover_url
    if args.capa:
        from PIL import Image
        w, h = Image.open(args.capa).size
        if w < 1080 or abs(w / h - 9 / 16) > .01:
            sys.exit(f"❌ Capa {w}x{h}: precisa ser 9:16 com 1080 px ou mais (a grade mostra o miolo 3:4)")
        campos["cover_url"] = url_imagem(args.capa)
    cid = conteiner_video(dados, args.arquivo, **campos)
    publicar(dados, cid, "REELS", args.arquivo, args.teste)


def cmd_foto(args):
    dados = ler_token()
    campos = {"image_url": url_imagem(args.arquivo), **extras(args, "IMAGE")}
    cap = legenda(args)
    if cap:
        campos["caption"] = cap
    cid = api("POST", f"{dados['ig_id']}/media", dados["token"], **campos)["id"]
    publicar(dados, cid, "IMAGE", args.arquivo, args.teste)


def cmd_carrossel(args):
    dados = ler_token()
    tok, ig = dados["token"], dados["ig_id"]
    if not 2 <= len(args.arquivos) <= 20:
        sys.exit("❌ Carrossel vai de 2 a 20 itens")
    filhos = []
    for i, a in enumerate(args.arquivos, 1):
        print(f"[{i}/{len(args.arquivos)}] {Path(a).name}")
        if Path(a).suffix.lower() in VIDEO_EXT:
            cid = conteiner_video(dados, a, media_type="VIDEO", is_carousel_item="true")
        else:
            alt = {"alt_text": args.alt[:1000]} if getattr(args, "alt", None) else {}
            cid = api("POST", f"{ig}/media", tok, image_url=url_imagem(a), is_carousel_item="true", **alt)["id"]
        esperar(tok, cid)
        filhos.append(cid)
    campos = {"media_type": "CAROUSEL", "children": ",".join(filhos),
              **{k: v for k, v in extras(args, "CAROUSEL").items() if k in ("collaborators", "is_ai_generated")}}
    cap = legenda(args)
    if cap:
        campos["caption"] = cap
    cid = api("POST", f"{ig}/media", tok, **campos)["id"]
    publicar(dados, cid, "CAROUSEL", args.arquivos[0], args.teste)


def cmd_story(args):
    dados = ler_token()
    if Path(args.arquivo).suffix.lower() in VIDEO_EXT:
        conferir_video(args.arquivo, "STORIES")
        cid = conteiner_video(dados, args.arquivo, media_type="STORIES")
    else:
        cid = api("POST", f"{dados['ig_id']}/media", dados["token"],
                  media_type="STORIES", image_url=url_imagem(args.arquivo))["id"]
    publicar(dados, cid, "STORIES", args.arquivo, args.teste)


def cmd_posts(args):
    dados = ler_token()
    j = api("GET", f"{dados['ig_id']}/media", dados["token"],
            fields="id,caption,media_type,timestamp,permalink,comments_count,like_count", limit=args.n)
    for m in j.get("data", []):
        cap = (m.get("caption") or "").replace("\n", " ")[:50]
        print(f"{m['timestamp'][:16]} · {m['id']} · {m['media_type']} · 💬{m.get('comments_count', 0)} "
              f"❤️{m.get('like_count', 0)} · {m.get('permalink')} · {cap}")


def cmd_comentarios(args):
    dados = ler_token()
    j = api("GET", f"{args.media_id}/comments", dados["token"],
            fields="id,text,username,timestamp,like_count,replies{id,text,username,timestamp}")
    for c in j.get("data", []):
        print(f"[{c['id']}] @{c.get('username')} ({c['timestamp'][:16]}): {c['text']}")
        for r in (c.get("replies") or {}).get("data", []):
            print(f"    ↳ [{r['id']}] @{r.get('username')}: {r['text']}")


def cmd_responder(args):
    dados = ler_token()
    rid = api("POST", f"{args.comment_id}/replies", dados["token"], message=args.texto)["id"]
    registrar({"acao": "resposta", "comment_id": args.comment_id, "resposta_id": rid, "texto": args.texto})
    print(f"✅ Resposta postada (id {rid})")


def cmd_comentar(args):
    """Comentário nosso no post (o 1º comentário). Fixar não existe na API: fica para o JV no app."""
    dados = ler_token()
    cid = api("POST", f"{args.media_id}/comments", dados["token"], message=args.texto)["id"]
    registrar({"acao": "comentario", "media_id": args.media_id, "comentario_id": cid, "texto": args.texto})
    print(f"✅ Comentário postado (id {cid}). Fixar: só o JV, pelo app.")


def cmd_varrer(args):
    """Comentários dos posts desde uma data, no formato do tools/comentarios.py importar."""
    dados = ler_token()
    tok = dados["token"]
    desde = datetime.fromisoformat(args.desde)
    posts = api("GET", f"{dados['ig_id']}/media", tok, fields="id,caption,timestamp,permalink,comments_count", limit=50)
    saida = []
    for m in posts.get("data", []):
        if datetime.fromisoformat(m["timestamp"][:19]) < desde or not m.get("comments_count"):
            continue
        titulo = (m.get("caption") or "").split("\n")[0][:80]
        j = api("GET", f"{m['id']}/comments", tok,
                fields="id,text,username,timestamp,like_count,replies{id,text,username,timestamp,like_count}", limit=100)
        for c in j.get("data", []):
            base = {"rede": "instagram", "post_id": m["id"], "post_titulo": titulo}
            saida.append({**base, "comentario_id": c["id"], "autor": "@" + c.get("username", "?"),
                          "texto": c.get("text", ""), "data": c.get("timestamp", ""), "curtidas": c.get("like_count", 0)})
            for r in (c.get("replies") or {}).get("data", []):
                saida.append({**base, "comentario_id": r["id"], "autor": "@" + r.get("username", "?"),
                              "texto": r.get("text", ""), "data": r.get("timestamp", ""),
                              "curtidas": r.get("like_count", 0), "pai": "@" + c.get("username", "?")})
    destino = Path(args.saida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(saida, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ {len(saida)} comentário(s) do Instagram em {destino}")


METRICAS = ["views", "reach", "saved", "shares", "likes", "comments", "total_interactions",
            "ig_reels_avg_watch_time", "follows", "profile_visits"]
# 21/11 (JV): uma chamada só por post, com as métricas que valem para o tipo (antes era uma por métrica, ~10 GET).
METRICAS_REEL = ["views", "reach", "saved", "shares", "likes", "comments", "total_interactions",
                 "ig_reels_avg_watch_time", "ig_reels_video_view_total_time"]
METRICAS_FEED = ["views", "reach", "saved", "shares", "likes", "comments", "total_interactions",
                 "follows", "profile_visits"]
PASTA_INSIGHTS = ROOT / "aprendizados" / "instagram-insights"
CSV_POSTS_IG = ROOT / "aprendizados" / "instagram-insights.csv"
CSV_CONTA_IG = ROOT / "aprendizados" / "instagram-conta.csv"


def _valor(item):
    v = (item.get("values") or [{}])[0].get("value")
    return v if v is not None else (item.get("total_value") or {}).get("value")


def ler_insights(tok, media_id, tipo="REELS"):
    """Métricas de um post numa chamada só; se a Meta recusar o lote, cai para uma por métrica."""
    mets = METRICAS_REEL if tipo in ("REELS", "VIDEO") else METRICAS_FEED
    try:
        j = api("GET", f"{media_id}/insights", tok, metric=",".join(mets))
        res = {m: None for m in mets}
        res.update({i["name"]: _valor(i) for i in j.get("data", [])})
        return res
    except RuntimeError as e:
        if " 10/" in str(e) or "(#10)" in str(e):
            sys.exit("❌ O token não tem a permissão instagram_manage_insights. Adicionar a permissão no caso de uso "
                     "do app e gerar o token de novo (PENDENCIAS-JV.md).")
    res = {}
    for met in mets:
        try:
            res[met] = _valor(api("GET", f"{media_id}/insights", tok, metric=met)["data"][0])
        except RuntimeError:
            res[met] = None  # métrica que não vale para esse tipo de post
    return res


def cmd_insights(args):
    """Métricas de um post. Precisa da permissão instagram_manage_insights no token."""
    dados = ler_token()
    tipo = api("GET", args.media_id, dados["token"], fields="media_product_type").get("media_product_type", "REELS")
    print(json.dumps({"media_id": args.media_id, "tipo": tipo, **ler_insights(dados["token"], args.media_id, tipo)},
                     ensure_ascii=False))


def _conta(tok, ig, publico):
    """Números da conta: seguidores, o dia de ontem e, com publico=True, idade, gênero e cidade de quem segue."""
    import calendar
    hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    ontem = hoje - timedelta(days=1)

    def ts(d):  # meia-noite de São Paulo em epoch
        return calendar.timegm(d.timetuple()) + 3 * 3600

    perfil = api("GET", ig, tok, fields="followers_count,follows_count,media_count")
    conta = {"dia": f"{ontem:%Y-%m-%d}", "seguidores": perfil.get("followers_count"),
             "posts": perfil.get("media_count")}
    mets = ["reach", "views", "accounts_engaged", "total_interactions", "likes", "comments", "shares", "saves",
            "profile_links_taps", "follows_and_unfollows", "profile_views"]  # profile_views: 24/11 (plano da agenda)
    try:
        j = api("GET", f"{ig}/insights", tok, metric=",".join(mets), period="day", metric_type="total_value",
                since=ts(ontem), until=ts(hoje))
        for i in j.get("data", []):
            tv = i.get("total_value") or {}
            if i["name"] == "follows_and_unfollows":
                for b in (tv.get("breakdowns") or [{}])[0].get("results", []):
                    nome = {"FOLLOWER": "seguiu", "NON_FOLLOWER": "deixou"}.get(b["dimension_values"][0],
                                                                                b["dimension_values"][0])
                    conta[nome] = b["value"]
                conta.setdefault("seguiu_menos_deixou", tv.get("value"))
            else:
                conta[i["name"]] = tv.get("value")
    except RuntimeError as e:
        conta["erro_dia"] = str(e)[:200]
    try:  # seguidores novos por dia (série dos últimos dias)
        j = api("GET", f"{ig}/insights", tok, metric="follower_count", period="day",
                since=ts(hoje - timedelta(days=7)), until=ts(hoje))
        conta["novos_por_dia"] = {v["end_time"][:10]: v["value"] for v in j["data"][0].get("values", [])}
    except (RuntimeError, IndexError, KeyError) as e:
        conta["novos_por_dia"] = f"indisponível: {str(e)[:120]}"
    if publico:
        conta["publico"] = {}
        for quebra in ("age", "gender", "city", "country"):
            try:
                j = api("GET", f"{ig}/insights", tok, metric="follower_demographics", period="lifetime",
                        metric_type="total_value", breakdown=quebra)
                res = j["data"][0]["total_value"]["breakdowns"][0]["results"]
                conta["publico"][quebra] = sorted(((r["dimension_values"][0], r["value"]) for r in res),
                                                  key=lambda x: -x[1])
            except (RuntimeError, IndexError, KeyError) as e:
                conta["publico"][quebra] = f"indisponível: {str(e)[:120]}"
    return conta


def _preencher_metricas_csv(leituras):
    """Põe views_24h / views_48h / views_7d no metricas.csv quando a idade da leitura cai na janela e a célula está vazia.
    Mexe só nessas células, linha a linha (há linhas com colunas a mais; o resto do arquivo fica como estava)."""
    import csv
    arq = ROOT / "aprendizados" / "metricas.csv"
    with open(arq, encoding="utf-8", newline="") as f:
        linhas = list(csv.reader(f))
    cab = linhas[0]
    pos = {c: cab.index(c) for c in ("video_id", "views_24h", "views_48h", "views_7d")}
    por_id = {l["media_id"]: l for l in leituras}
    mudou = 0
    for l in linhas[1:]:
        if len(l) <= pos["video_id"]:
            continue
        lei = por_id.get(l[pos["video_id"]])
        if not lei or lei.get("views") is None:
            continue
        h = lei["idade_h"]
        col = "views_24h" if 18 <= h < 42 else "views_48h" if 42 <= h < 66 else "views_7d" if 150 <= h < 198 else None
        if not col:
            continue
        while len(l) <= pos[col]:
            l.append("")
        if not l[pos[col]].strip():
            l[pos[col]] = str(lei["views"])
            mudou += 1
    if mudou:
        tmp = arq.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            csv.writer(f, lineterminator="\r\n" if b"\r\n" in arq.read_bytes()[:500] else "\n").writerows(linhas)
        tmp.replace(arq)
    return mudou


def cmd_insights_rodada(args):
    """Leitura diária (Agendador, 6h): posts dos últimos N dias, uma chamada cada, + números da conta.
    Grava aprendizados/instagram-insights/<data>.json, instagram-insights.csv, instagram-conta.csv e as views no
    metricas.csv."""
    import csv
    dados = ler_token()
    tok, ig = dados["token"], dados["ig_id"]
    agora = datetime.now().astimezone()
    corte = agora - timedelta(days=args.dias)
    j = api("GET", f"{ig}/media", tok, limit=50,
            fields="id,caption,media_type,media_product_type,timestamp,permalink,comments_count,like_count")
    midias = list(j.get("data", []))
    while j.get("paging", {}).get("next") and midias and \
            datetime.strptime(midias[-1]["timestamp"], "%Y-%m-%dT%H:%M:%S%z") >= corte:
        j = api("GET", j["paging"]["next"], tok)  # mais de 50 posts no período: próxima página
        midias += j.get("data", [])
    leituras, parou = [], None
    for m in midias:
        pub = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z")
        if pub < corte:
            continue
        try:
            ins = ler_insights(tok, m["id"], m.get("media_product_type", "REELS"))
        except SystemExit as e:  # teto ou espera: guarda o que já leu e para aqui
            print(f"⚠️ Leitura parou no meio ({len(leituras)} post(s) lidos); o resto fica para a próxima.",
                  file=sys.stderr)
            parou = e
            break
        leituras.append({"lido_em": f"{agora:%Y-%m-%d %H:%M}", "media_id": m["id"],
                         "publicado": f"{pub.astimezone():%Y-%m-%d %H:%M}",
                         "idade_h": round((agora - pub).total_seconds() / 3600, 1),
                         "tipo": m.get("media_product_type"),
                         "titulo": (m.get("caption") or "").split("\n")[0][:80], "link": m.get("permalink"), **ins})
    try:
        conta = _conta(tok, ig, args.publico or agora.weekday() == 6) if not parou else {"dia": "", "erro": "teto"}
    except SystemExit:
        conta, parou = {"dia": "", "erro": "teto"}, parou or SystemExit(75)
    PASTA_INSIGHTS.mkdir(parents=True, exist_ok=True)
    saida = PASTA_INSIGHTS / f"{agora:%Y-%m-%d_%H%M}.json"
    saida.write_text(json.dumps({"conta": conta, "posts": leituras}, ensure_ascii=False, indent=1), encoding="utf-8")
    cols = ["lido_em", "media_id", "publicado", "idade_h", "tipo", "titulo", "link"] + \
        list(dict.fromkeys(METRICAS_REEL + METRICAS_FEED))
    novo = not CSV_POSTS_IG.exists()
    with open(CSV_POSTS_IG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        if novo:
            w.writeheader()
        w.writerows(leituras)
    cols_c = ["dia", "seguidores", "posts", "reach", "views", "accounts_engaged", "total_interactions", "likes",
              "comments", "shares", "saves", "profile_links_taps", "seguiu", "deixou", "profile_views"]
    novo = not CSV_CONTA_IG.exists()
    ja = "" if novo else CSV_CONTA_IG.read_text(encoding="utf-8")
    if conta["dia"] and f"\n{conta['dia']}," not in ja:
        with open(CSV_CONTA_IG, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols_c, extrasaction="ignore")
            if novo:
                w.writeheader()
            w.writerow(conta)
    mudou = _preencher_metricas_csv(leituras)
    registrar({"acao": "insights_rodada", "posts": len(leituras), "metricas_csv": mudou})
    print(f"✅ {len(leituras)} post(s) lidos, conta de {conta['dia']} ({conta.get('seguidores')} seguidores); "
          f"{mudou} célula(s) no metricas.csv. Arquivo: {saida}")
    if parou:
        sys.exit(75)


def _ps(script):
    import subprocess
    import base64
    script = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + script
    cod = base64.b64encode(script.encode("utf-16-le")).decode()
    r = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", cod], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode:
        sys.exit(f"❌ PowerShell: {r.stderr.strip()[:400]}")
    return r.stdout


PASTA_TAREFAS = "\\Foragido\\"



def cmd_comentar_pos(args):
    """1º comentário num post agendado na nuvem (o id só existe depois de publicar): acha o post pelo começo
    da legenda entre os publicados nas últimas 3 h e comenta. Tenta 5 vezes em 15 min; não comenta duas vezes."""
    dados = ler_token()
    tok, ig = dados["token"], dados["ig_id"]
    trecho = args.legenda_inicio.strip().lower()
    for tentativa in range(5):
        j = api("GET", f"{ig}/media", tok, fields="id,caption,timestamp,permalink", limit=10)
        for m in j.get("data", []):
            ts = datetime.strptime(m["timestamp"][:19], "%Y-%m-%dT%H:%M:%S") - timedelta(hours=3)
            if datetime.now() - ts > timedelta(hours=3):
                continue
            if not (m.get("caption") or "").strip().lower().startswith(trecho):
                continue
            ja = api("GET", f"{m['id']}/comments", tok, fields="text,username").get("data", [])
            if any(c.get("username") == dados.get("username", "foragido_gta") and c.get("text") == args.texto for c in ja):
                print(f"já comentado em {m['permalink']}")
                return
            cid = api("POST", f"{m['id']}/comments", tok, message=args.texto)["id"]
            registrar({"acao": "comentario", "media_id": m["id"], "comentario_id": cid, "texto": args.texto})
            print(f"✅ Comentário postado em {m['permalink']} (id {cid}). Fixar: só o JV, pelo app.")
            return
        print(f"tentativa {tentativa + 1}: post com a legenda \"{args.legenda_inicio[:40]}\" ainda não apareceu")
        time.sleep(180)
    registrar({"acao": "comentario_falhou", "legenda_inicio": args.legenda_inicio, "texto": args.texto})
    sys.exit("❌ Post não encontrado em 15 min; comentar à mão")

def cmd_agendar(args):
    """Agenda um comando deste script no Agendador do Windows (a API não agenda; o PC precisa estar ligado)."""
    quando = datetime.strptime(args.quando, "%Y-%m-%d %H:%M")
    if quando < datetime.now() + timedelta(minutes=5):
        sys.exit(f"❌ {quando:%d/%m %H:%M} está no passado ou a menos de 5 min")
    resto = [a for a in args.comando if a != "--"]
    if not resto or resto[0] not in ("reel", "foto", "carrossel", "story", "comentar", "comentar-pos"):
        sys.exit("❌ Depois de -- vem o comando: reel|foto|carrossel|story|comentar|comentar-pos e os argumentos")
    if "--teste" in resto:
        sys.exit("❌ Não se agenda --teste: rodar o teste agora, antes de agendar")
    resto = [str(Path(a).resolve()) if Path(a).exists() else a for a in resto]  # a tarefa roda de outra pasta
    rotulo = Path(resto[1]).parent.name[:30] if len(resto) > 1 else ""
    nome = f"IG {quando:%Y-%m-%d %H%M} {resto[0]} {rotulo}".strip()
    logs = ROOT / "fila" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f"instagram-{quando:%Y%m%d-%H%M}.log"
    pasta_args = ROOT / "fila" / "agendados-ig"
    pasta_args.mkdir(parents=True, exist_ok=True)
    arq_args = pasta_args / f"{quando:%Y%m%d-%H%M}-{resto[0]}-{int(time.time())}.json"
    arq_args.write_text(json.dumps(resto, ensure_ascii=False, indent=1), encoding="utf-8")
    cmd = " ".join(f'"{x}"' for x in [sys.executable, str(Path(__file__).resolve()), "executar", str(arq_args)])
    acao = f'/c "{cmd} >> "{log}" 2>&1"'.replace("'", "''")
    ps = (f"$a = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '{acao}' -WorkingDirectory '{ROOT}'; "
          f"$t = New-ScheduledTaskTrigger -Once -At ([datetime]::ParseExact('{quando:%Y-%m-%d %H:%M}','yyyy-MM-dd HH:mm',$null)); "
          f"$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1); "
          f"Register-ScheduledTask -TaskPath '{PASTA_TAREFAS}' -TaskName '{nome}' -Action $a -Trigger $t -Settings $s "
          f"-Description 'instagram_api.py agendado' | Out-Null; 'ok'")
    _ps(ps)
    registrar({"acao": "agendado", "quando_publica": args.quando, "tarefa": nome, "comando": resto})
    print(f"✅ Agendado para {quando:%d/%m/%Y %H:%M} · tarefa {PASTA_TAREFAS}{nome} · log em {log}")
    print("   Conferir com: python tools/instagram_api.py agendados")


EXECUTANDO = {"arquivo": None}


def cmd_executar(args):
    """Roda o comando guardado em JSON pelo agendar (os argumentos vêm em UTF-8, com acento inteiro)."""
    resto = json.loads(Path(args.arquivo).read_text(encoding="utf-8"))
    EXECUTANDO["arquivo"] = Path(args.arquivo).name  # a própria tarefa não conta como duplicata
    print(f"--- {datetime.now():%d/%m %H:%M:%S} executar {resto[0]}")
    main(resto)


def cmd_agendados(args):
    saida = _ps(f"Get-ScheduledTask -TaskPath '{PASTA_TAREFAS}' -ErrorAction SilentlyContinue | ForEach-Object {{ "
                "$i = $_ | Get-ScheduledTaskInfo; '{0} | proxima: {1} | ultimo resultado: {2}' -f $_.TaskName, "
                "$i.NextRunTime, $i.LastTaskResult }; exit 0").strip()
    print(saida or "Nada agendado.")


def cmd_cancelar(args):
    _ps(f"Unregister-ScheduledTask -TaskPath '{PASTA_TAREFAS}' -TaskName '{args.nome}' -Confirm:$false; 'ok'")
    registrar({"acao": "cancelado", "tarefa": args.nome})
    print(f"✅ Cancelado: {args.nome}")


def cmd_nuvem(args):
    j = nuvem("GET", "/fila")
    print("Pendentes:" if j["pendentes"] else "Pendentes: nenhum")
    dados = ler_token()
    ESPELHO.write_text(json.dumps(j["pendentes"], ensure_ascii=False), encoding="utf-8")
    for i in j["pendentes"]:
        st = "não consultado (--meta consulta)"
        if args.meta:  # 1 chamada por contêiner: só quando precisar mesmo
            try:
                st = api("GET", i["creation_id"], dados["token"], fields="status_code").get("status_code")
            except RuntimeError as e:
                st = f"? ({e})"
        extra = f" · Meta: {st}" + (f" · erro (tentativa {i['tentativas']}): {i['erro']}" if i.get("erro") else "")
        print(f"  {i['quando'][:16].replace('T', ' ')} · {i.get('tipo')} · {i.get('rotulo')} · {i['creation_id']}{extra}")
    for i in j["feitos"][-args.n:]:
        print(f"  feito {i.get('rodou_em', '')[:16]} UTC · {i.get('estado')} · {i.get('rotulo')} · "
              f"{i.get('link') or i.get('media_id') or i.get('erro')}")


def cmd_soltar(args):
    try:
        era = nuvem("POST", "/soltar").get("era")
        print(f"nuvem: {'freio solto (era: ' + era.get('motivo', '') + ')' if era else 'sem freio'}")
    except Exception as e:
        print(f"⚠️ nuvem não respondeu ao soltar: {e}")
    ESPERA.unlink(missing_ok=True)
    if not FREIO.exists():
        print("PC: freio não estava puxado.")
        return
    f = json.loads(FREIO.read_text(encoding="utf-8"))
    FREIO.unlink()
    registrar({"acao": "freio_solto", "motivo_era": f.get("motivo")})
    print(f"✅ Freio solto (era: {f.get('motivo')}). A próxima chamada vai à API; se falhar de novo, o freio volta.")


VIGIA_TENTOU = ROOT / "fila" / "vigia-tentou.json"


def cmd_nuvem_cancelar(args):
    j = nuvem("POST", "/cancelar", {"creation_id": args.creation_id})
    registrar({"acao": "cancelado_nuvem", "creation_id": args.creation_id})
    print("✅ Cancelado na nuvem" if j.get("ok") else "⚠️ Não estava na fila")


ALERTA = ROOT / "fila" / "ALERTA-INSTAGRAM.md"


def alerta(texto):
    linha = f"- {datetime.now():%d/%m %H:%M} · {texto}\n"
    if ALERTA.exists() and f"{datetime.now():%d/%m}" in ALERTA.read_text(encoding="utf-8") and \
            any(l.endswith(f"· {texto}") for l in ALERTA.read_text(encoding="utf-8").splitlines()):
        return  # o mesmo alerta já está no arquivo hoje (o vigia roda a cada 5 min)
    novo = not ALERTA.exists()
    with ALERTA.open("a", encoding="utf-8") as f:
        if novo:
            f.write("# Alerta do Instagram (vigia do PC)\n\nApagar este arquivo depois de ler.\n\n")
        f.write(linha)
    print("⚠️ " + texto)
    try:  # 21/11: o #7 falhou na nuvem e ninguém viu; alerta do Instagram vai também para o celular (ntfy do vigia)
        sys.path.insert(0, str(ROOT / "tools"))
        import vigia
        vigia.notificar("Instagram: falha", texto, prioridade=4, tags=["warning"])
    except Exception as e:  # noqa: BLE001
        print(f"   (aviso no celular não saiu: {e})")


def cmd_vigia(args):
    """Plano B do disparo na nuvem (Agendador do Windows a cada 5 min). Post vencido há 4+ min e ainda não publicado:
    publica pelo PC. Contêiner que não está FINISHED antes da hora: alerta. Nunca duplica (a Meta só publica um
    contêiner uma vez, e o status é conferido antes)."""
    dados = ler_token()
    tok, ig = dados["token"], dados["ig_id"]
    try:
        fila = nuvem("GET", "/fila")
    except Exception as e:  # nuvem fora: o vigia não sabe o que está agendado; usa o espelho local
        alerta(f"disparo na nuvem não respondeu ({e}); usando o espelho local da fila")
        fila = {"pendentes": json.loads(ESPELHO.read_text(encoding="utf-8")) if ESPELHO.exists() else []}
    else:
        ESPELHO.write_text(json.dumps(fila["pendentes"], ensure_ascii=False), encoding="utf-8")
    conferir_freio()
    agora = datetime.now().astimezone()
    try:
        tentados = set(json.loads(VIGIA_TENTOU.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        tentados = set()
    for i in fila["pendentes"]:
        quando = datetime.fromisoformat(i["quando"])
        # 15/11: o vigia lia TODOS os contêineres a cada 5 min (~100 chamadas/h) e a Meta bloqueou a API.
        # Agora só olha o que venceu há 4+ min, uma única vez por contêiner.
        if agora - quando < timedelta(minutes=4) or i["creation_id"] in tentados:
            continue
        tentados.add(i["creation_id"])
        VIGIA_TENTOU.write_text(json.dumps(sorted(tentados)), encoding="utf-8")
        try:
            st = api("GET", i["creation_id"], tok, fields="status_code").get("status_code")
        except RuntimeError as e:
            alerta(f"{i.get('rotulo')} ({quando:%d/%m %H:%M}): não consegui ler o contêiner {i['creation_id']}: {e}")
            continue
        if st == "PUBLISHED":
            continue
        if st != "FINISHED":
            alerta(f"{i.get('rotulo')} ({quando:%d/%m %H:%M}): contêiner {i['creation_id']} está {st}; preparar de novo")
            continue
        try:
            mid = api("POST", f"{ig}/media_publish", tok, creation_id=i["creation_id"])["id"]
            link = api("GET", mid, tok, fields="permalink").get("permalink", "") if i.get("tipo") != "STORIES" else ""
            registrar({"acao": "publicado_pelo_vigia", "creation_id": i["creation_id"], "media_id": mid, "link": link})
            alerta(f"{i.get('rotulo')} ({quando:%d/%m %H:%M}): a nuvem não publicou; o vigia do PC publicou às "
                   f"{datetime.now():%H:%M} · {link or mid}")
            try:
                nuvem("POST", "/cancelar", {"creation_id": i["creation_id"]})
            except Exception:
                pass
        except RuntimeError as e:
            if "PUBLISHED" in str(e) or "already" in str(e).lower():
                continue
            alerta(f"{i.get('rotulo')} ({quando:%d/%m %H:%M}): nem a nuvem nem o PC conseguiram publicar: {e}")
    # 21/11: o Esquema #7 falhou 2x na nuvem (contêiner ERROR) e saiu dos pendentes; o vigia não via os feitos
    for i in fila.get("feitos", []):
        chave = f"falhou:{i.get('creation_id')}:{i.get('rodou_em')}"
        if i.get("estado") != "falhou" or chave in tentados:
            continue
        tentados.add(chave)
        VIGIA_TENTOU.write_text(json.dumps(sorted(tentados)), encoding="utf-8")
        alerta(f"{i.get('rotulo')} ({str(i.get('quando', ''))[:16].replace('T', ' ')}): a nuvem desistiu "
               f"({i.get('erro')}). Repostar: instagram_api.py reel/story com --forcar")
    if args.verbose:
        print(f"vigia ok {datetime.now():%H:%M} · {len(fila['pendentes'])} pendente(s)")


ESPELHO = ROOT / "fila" / "nuvem-espelho.json"


def cmd_concorrente(args):
    """Números públicos de outra conta profissional (Business Discovery): seguidores e, por post,
    views, curtidas e comentários. Não dá alcance, envios, salvos nem retenção."""
    dados = ler_token()
    campos = ("business_discovery.username({u}){{username,followers_count,media_count,"
              "media.limit({n}){{timestamp,media_type,like_count,comments_count,view_count,permalink,caption}}}}")
    for u in args.usernames:
        try:
            b = api("GET", dados["ig_id"], dados["token"], fields=campos.format(u=u.lstrip("@"), n=args.n))["business_discovery"]
        except RuntimeError as e:
            print(f"@{u}: ❌ {e}")
            continue
        print(f"\n@{b['username']} · {b.get('followers_count')} seguidores · {b.get('media_count')} posts")
        for m in b.get("media", {}).get("data", []):
            cap = (m.get("caption") or "").replace("\n", " ")[:55]
            print(f"  {m['timestamp'][:10]} {m.get('media_type', ''):<14} 👁 {m.get('view_count', '-'):>8} "
                  f"❤️ {m.get('like_count', '-'):>6} 💬 {m.get('comments_count', '-'):>4} · {cap}")


def checar_duplicata(arquivo):
    """Recusa publicar ou agendar um arquivo que já está na fila da nuvem, numa tarefa do Windows ainda não
    rodada, ou publicado nas últimas 24 h. --forcar passa por cima (repost de propósito)."""
    absoluto = lambda x: str((Path(x) if Path(x).is_absolute() else ROOT / x).resolve()).lower()
    alvo = absoluto(arquivo) if not Path(arquivo).exists() else str(Path(arquivo).resolve()).lower()
    log = [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()] if LOG.exists() else []
    mesmo = lambda a: a and absoluto(a) == alvo
    try:
        pend = {i["creation_id"]: i["quando"] for i in nuvem("GET", "/fila")["pendentes"]}
    except Exception:
        pend = {}
    for e in log:
        if e.get("acao") == "agendado_nuvem" and mesmo(e.get("arquivo")) and e.get("creation_id") in pend:
            sys.exit(f"❌ Esse arquivo já está na fila da nuvem para {pend[e['creation_id']][:16].replace('T', ' ')} "
                     f"(contêiner {e['creation_id']}). Cancelar antes (nuvem-cancelar) ou usar --forcar.")
        if e.get("acao") == "publicado" and mesmo(e.get("arquivo")) and \
                datetime.now() - datetime.fromisoformat(e["quando"]) < timedelta(hours=24):
            sys.exit(f"❌ Esse arquivo foi publicado em {e['quando'][:16]} ({e.get('link')}). --forcar para repetir.")
    pasta = ROOT / "fila" / "agendados-ig"
    if pasta.exists():
        tarefas = _ps(f"Get-ScheduledTask -TaskPath '{PASTA_TAREFAS}' -ErrorAction SilentlyContinue | "
                      "ForEach-Object { $_.Actions[0].Arguments }; exit 0")
        for f in pasta.glob("*.json"):
            if f.name != EXECUTANDO["arquivo"] and f.name in tarefas and any(mesmo(a) for a in json.loads(f.read_text(encoding="utf-8"))[1:]
                                         if isinstance(a, str) and Path(a).suffix):
                sys.exit(f"❌ Esse arquivo já está numa tarefa do Agendador do Windows ({f.name}). "
                         "Cancelar antes (instagram_api.py agendados / cancelar) ou usar --forcar.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("token"); s.add_argument("valor"); s.add_argument("--ig-id"); s.set_defaults(f=cmd_token)
    sub.add_parser("quem").set_defaults(f=cmd_quem)
    sub.add_parser("renovar").set_defaults(f=cmd_renovar)
    for nome, f in (("reel", cmd_reel), ("foto", cmd_foto), ("story", cmd_story)):
        s = sub.add_parser(nome); s.add_argument("arquivo"); s.add_argument("--teste", action="store_true")
        s.add_argument("--nuvem", metavar="'AAAA-MM-DD HH:MM'")
        s.add_argument("--forcar", action="store_true", help="publicar mesmo se o arquivo já estiver agendado/publicado")
        s.add_argument("--colab", help="até 3 @ separados por vírgula (não vale em story)")
        s.add_argument("--ia", action="store_true", help="marca o post como feito com IA (is_ai_generated)")
        if nome == "reel":
            s.add_argument("--audio", help="nome do áudio original do Reel (só uma vez)")
            s.add_argument("--trial", choices=["manual", "auto"], help="Trial Reel: vai só para não seguidores. Em 15/11 a Meta recusou para a conta (erro 2207081, sem acesso)")
        if nome == "foto":
            s.add_argument("--alt", required=True, help="texto alternativo, OBRIGATÓRIO desde 19/11: frase natural descrevendo a imagem com a frase de busca (até 1000 caracteres)")
        if nome != "story":
            s.add_argument("--legenda-arquivo")
        if nome == "reel":
            s.add_argument("--capa"); s.add_argument("--fora-do-feed", action="store_true"); s.add_argument("--sem-capa", action="store_true"); s.add_argument("--quadro", help="segundo do vídeo usado como capa")
        s.set_defaults(f=f)
    s = sub.add_parser("carrossel"); s.add_argument("arquivos", nargs="+")
    s.add_argument("--legenda-arquivo"); s.add_argument("--teste", action="store_true")
    s.add_argument("--nuvem", metavar="'AAAA-MM-DD HH:MM'"); s.add_argument("--forcar", action="store_true")
    s.add_argument("--colab"); s.add_argument("--alt", required=True, help="texto alternativo das imagens, OBRIGATÓRIO desde 19/11 (vai em todo slide de imagem)"); s.add_argument("--ia", action="store_true")
    s.set_defaults(f=cmd_carrossel)
    s = sub.add_parser("posts"); s.add_argument("--n", type=int, default=10); s.set_defaults(f=cmd_posts)
    s = sub.add_parser("comentarios"); s.add_argument("media_id"); s.set_defaults(f=cmd_comentarios)
    s = sub.add_parser("responder"); s.add_argument("comment_id"); s.add_argument("texto"); s.set_defaults(f=cmd_responder)
    s = sub.add_parser("comentar"); s.add_argument("media_id"); s.add_argument("texto"); s.set_defaults(f=cmd_comentar)
    s = sub.add_parser("comentar-pos"); s.add_argument("legenda_inicio"); s.add_argument("texto"); s.set_defaults(f=cmd_comentar_pos)
    s = sub.add_parser("varrer"); s.add_argument("--desde", required=True); s.add_argument("--saida", required=True)
    s.set_defaults(f=cmd_varrer)
    s = sub.add_parser("insights"); s.add_argument("media_id"); s.set_defaults(f=cmd_insights)
    s = sub.add_parser("insights-rodada"); s.add_argument("--dias", type=int, default=7)
    s.add_argument("--publico", action="store_true", help="idade, gênero e cidade (sai sozinho aos domingos)")
    s.set_defaults(f=cmd_insights_rodada)
    s = sub.add_parser("agendar"); s.add_argument("quando"); s.add_argument("comando", nargs=argparse.REMAINDER)
    s.set_defaults(f=cmd_agendar)
    sub.add_parser("agendados").set_defaults(f=cmd_agendados)
    s = sub.add_parser("cancelar"); s.add_argument("nome"); s.set_defaults(f=cmd_cancelar)
    s = sub.add_parser("nuvem"); s.add_argument("--n", type=int, default=5); s.add_argument("--meta", action="store_true")
    s.set_defaults(f=cmd_nuvem)
    s = sub.add_parser("soltar"); s.set_defaults(f=cmd_soltar)
    s = sub.add_parser("nuvem-cancelar"); s.add_argument("creation_id"); s.set_defaults(f=cmd_nuvem_cancelar)
    s = sub.add_parser("vigia"); s.add_argument("-v", "--verbose", action="store_true"); s.set_defaults(f=cmd_vigia)
    s = sub.add_parser("concorrente"); s.add_argument("usernames", nargs="+"); s.add_argument("--n", type=int, default=12)
    s.set_defaults(f=cmd_concorrente)
    s = sub.add_parser("executar"); s.add_argument("arquivo"); s.set_defaults(f=cmd_executar)
    args = ap.parse_args(argv)
    import os
    piloto = (os.environ.get("RADAR_PILOTO") == "1" and not (ROOT / "fila" / "PILOTO-PAUSADO").exists() and not (ROOT / "fila" / "PILOTO-ENSAIO").exists())  # piloto automático dos cortes (17/11)
    import serie_auto  # série automática da rodada das 4h (JV 20/11): só com a liberação do tools/serie_auto.py liberar
    serie = args.cmd in ("reel", "story") and serie_auto.liberacao() is not None
    if os.environ.get("RADAR_RODADA") == "1" and args.cmd not in ("executar", "concorrente", "quem", "posts", "comentarios", "varrer", "insights", "insights-rodada", "agendados", "nuvem") and not serie             and not (piloto and args.cmd in ("reel", "story", "comentar")) and not (os.environ.get("RESPOSTAS_RODADA") == "1" and args.cmd == "responder"):  # rotina de respostas, JV 17/11
        sys.exit("Bloqueado: a rodada automatica das 4h nao publica, nao comenta e nao agenda no Instagram (so le).")
    if getattr(args, "nuvem", None):
        NUVEM["quando"] = quando_nuvem(args.nuvem)
    if args.cmd in ("reel", "foto", "story", "carrossel") and not getattr(args, "teste", False) \
            and not getattr(args, "forcar", False):
        checar_duplicata(args.arquivos[0] if args.cmd == "carrossel" else args.arquivo)
    try:
        with TUNEL:
            args.f(args)
    except RuntimeError as e:
        sys.exit(f"❌ API: {e}")


if __name__ == "__main__":
    main()
