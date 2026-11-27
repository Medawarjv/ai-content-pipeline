"""YouTube pela API oficial (Data API v3 + Analytics API), sem YouTube Studio no navegador.

Por que existe: regra do projeto desde 15/11, API oficial onde ela existir, nunca automação de navegador. Mapa do que a API faz e não faz: pesquisa/apis-redes-sociais.md.

Projeto no Google Cloud "Foragido Publicador", na conta pessoal do JV (regra das chaves). O login é do canal
@foragido_gta (conta Google do canal), feito uma vez pelo JV no perfil do Chrome do canal.
  cookies/youtube_oauth_client.json   cliente OAuth "App para computador" (baixado do console)
  cookies/youtube_token.json          token do canal (refresh token; com o app "Em produção" não expira em 7 dias)

Uso:
  python tools/youtube_api.py login                      abre o Chrome radargta para o JV autorizar o canal
  python tools/youtube_api.py quem                       canal autorizado, inscritos, vídeos, confere o id
  python tools/youtube_api.py subir <pasta|video.mp4> [--quando "AAAA-MM-DD HH:MM"] [--thumb img] [--titulo "..."]
        [--titulo-n 2] [--descricao-arquivo txt] [--tags "a, b"] [--privado|--nao-listado] [--ia] [--forcar] [--teste]
        Com uma pasta, lê o publicar.md: arquivo (linha **Arquivo:**), 1º título de "## Título", "## Descrição",
        "## Tags", e "## Título (EN)"/"## Descrição (EN)" (ES opcional) viram título e descrição em inglês. --quando agenda (sobe privado com publishAt; hora de São Paulo). Sem --quando e sem
        --privado/--nao-listado, publica na hora. --teste só imprime o que subiria.
  python tools/youtube_api.py status <video_id>          privacidade, publishAt, processamento, recusa
  python tools/youtube_api.py editar <video_id> [--titulo] [--descricao-arquivo] [--tags] [--quando] [--publicar-agora] [--privado]
  python tools/youtube_api.py localizar <video_id> --lingua en|es --titulo "..." [--descricao-arquivo txt]
  python tools/youtube_api.py apagar <video_id>          apaga de vez (só teste ou duplicata, com OK do JV)
  python tools/youtube_api.py thumb <video_id> <img>     JPG/PNG até 2 MB, 1280x720 (o script reduz se passar)
  python tools/youtube_api.py videos [--n 10]            últimos uploads (inclui agendados e privados)
  python tools/youtube_api.py comentarios <video_id>
  python tools/youtube_api.py comentar <video_id> "texto"      1º comentário (fixar só pelo YouTube Studio)
  python tools/youtube_api.py responder <comment_id> "texto"
  python tools/youtube_api.py varrer --desde AAAA-MM-DD --saida fila/comentarios/<arq>.json  (formato do comentarios.py)
  python tools/youtube_api.py metricas <video_id> [--desde AAAA-MM-DD]   views, minutos, retenção, inscritos, envios
  python tools/youtube_api.py retencao <video_id>        curva de retenção (% do vídeo → quem ainda assiste)
  python tools/youtube_api.py canal [--dias 7]           números do canal por dia
  python tools/youtube_api.py playlists [--itens]        playlists do canal (id, nº de vídeos; --itens lista os vídeos)
  python tools/youtube_api.py sem-playlist [--n 200]     vídeos públicos ou agendados que não estão em nenhuma playlist
  python tools/youtube_api.py playlist-criar "título" [--descricao-arquivo txt] [--privada]   (skill titulos-e-busca)
  python tools/youtube_api.py playlist-editar <playlist_id> [--titulo] [--descricao-arquivo] [--privada|--publica]
  python tools/youtube_api.py playlist-add <playlist_id> <video_id> [<video_id>...] [--posicao N]   (pula o que já está)
  python tools/youtube_api.py playlist-tirar <playlist_id> <video_id>

Pegadinhas:
- Cota: 10.000 unidades/dia por projeto. Upload ~100, thumb 50, edição 50, leitura 1. Erro quotaExceeded = esperar
  a meia-noite do Pacífico (5h de Brasília).
- App não verificado: vídeo pode ficar travado como privado. O `subir` confere logo depois e avisa.
- Analytics tem 1 a 3 dias de atraso; o número de "hoje" vem incompleto.
- A API não faz: tela final, cards, teste A/B de título/thumb, fixar comentário, música do Shorts.
- Na rodada das 4h (RADAR_RODADA=1) só rodam os comandos de leitura.
Log em aprendizados/youtube-api-log.jsonl.
"""
import argparse
import subprocess
import json
import os
import re
import sys
import webbrowser
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
CLIENTE = ROOT / "cookies" / "youtube_oauth_client.json"
TOKEN = ROOT / "cookies" / "youtube_token.json"
LOG = ROOT / "aprendizados" / "youtube-api-log.jsonl"
CANAL_ID = "UC6NN9ImhbkuY2pm4_GCIuJQ"  # @foragido_gta
SP = timezone(timedelta(hours=-3))  # São Paulo, sem horário de verão desde 2019
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PERFIL_RADARGTA = os.environ.get("YT_CHROME_PERFIL", "Default")  # perfil do Chrome logado na conta do canal
ESCOPOS = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
LEITURA = {"quem", "status", "videos", "comentarios", "varrer", "metricas", "retencao", "canal", "playlists", "sem-playlist"}


def registrar(evento):
    evento = {"quando": datetime.now(SP).isoformat(timespec="seconds"), **evento}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(evento, ensure_ascii=False) + "\n")


# ---------- cota (JV 19/11: "A API do youtube da pra aumentar o limite? Eu ainda vou querer postar mais coisa hoje") ----------
# Em 19/11 a cota de 10.000 acabou às ~20h30 com edição em massa (53 playlist-add, 51 localizar, 33 ia_nao, 19 editar...)
# e travou os uploads da noite. Estimativa pelo próprio log (o Google não expõe o saldo); o dia da cota vira às 5h de SP.
CUSTO = {"subir": 100, "thumb": 50, "editar": 50, "localizar": 51, "ia_nao": 50, "comentar": 50, "responder": 50,
         "playlist-add": 50, "playlist-tirar": 50, "playlist-criar": 50, "playlist-editar": 50, "playlist-apagar": 50,
         "apagar": 50, "legenda": 400, "bio_canal": 50, "trailer_canal": 50, "marca-dagua": 50}
COTA, RESERVA = 10000, int(os.environ.get("YT_RESERVA", "2000"))  # 2.000 = ~20 uploads com thumb e comentário
EM_MASSA = {"localizar", "playlist-add", "playlist-tirar", "playlist-editar", "playlist-criar", "editar", "playlist-apagar"}
LOTE_MIN = 15  # um dia normal tem até ~10 publicações; acima disso a operação é edição em massa


def _eventos_hoje():
    agora = datetime.now(SP)
    corte = agora.replace(hour=5, minute=0, second=0, microsecond=0)  # meia-noite do Pacífico (4h de SP no horário de verão de lá)
    if agora < corte:
        corte -= timedelta(days=1)
    evs = []
    if LOG.exists():
        for l in LOG.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(l)
                if datetime.fromisoformat(d["quando"]) >= corte and d.get("evento"):
                    evs.append(d["evento"])
            except Exception:
                pass
    return evs


def gasto_hoje():
    return sum(CUSTO.get(e, 0) for e in _eventos_hoje())


def guardar_reserva(op, forcar=False):
    """Só barra o que é LOTE: a mesma operação já rodou LOTE_MIN vezes hoje E o gasto estimado entrou na reserva dos
    uploads. O passo normal de uma publicação (1 playlist-add, 1 editar, 1 título EN por vídeo) nunca é barrado:
    travar publicação custa mais que a cota (JV, 18/11: erro passageiro não vira trava manual)."""
    if op not in EM_MASSA or forcar or os.environ.get("YT_FORCAR") == "1":
        return
    evs = _eventos_hoje()
    g = sum(CUSTO.get(e, 0) for e in evs)
    if evs.count(op) >= LOTE_MIN and g + CUSTO.get(op, 50) > COTA - RESERVA:
        sys.exit(f"❌ Cota: ~{g} de {COTA} gastas hoje (estimativa do log) e '{op}' já rodou {evs.count(op)}x: é lote. "
                 f"Os últimos {RESERVA} ficam para upload; o lote espera as 5h. Urgente: YT_FORCAR=1 na frente do comando.")


def credenciais():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    if not TOKEN.exists():
        sys.exit("❌ Sem token. Rode: python tools/youtube_api.py login")
    cred = Credentials.from_authorized_user_file(str(TOKEN), ESCOPOS)
    if not cred.valid:
        if cred.refresh_token:
            try:
                cred.refresh(Request())
            except Exception as e:  # refresh token revogado ou vencido (app em "Teste" vence em 7 dias)
                sys.exit(f"❌ Token recusado ({e}). Rode o login de novo: python tools/youtube_api.py login")
            TOKEN.write_text(cred.to_json(), encoding="utf-8")
        else:
            sys.exit("❌ Token sem refresh. Rode: python tools/youtube_api.py login")
    return cred


def yt():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=credenciais(), cache_discovery=False)


def yta():
    from googleapiclient.discovery import build
    return build("youtubeAnalytics", "v2", credentials=credenciais(), cache_discovery=False)


def executar(req):
    from googleapiclient.errors import HttpError
    try:
        return req.execute()
    except HttpError as e:
        motivo = ""
        try:
            motivo = json.loads(e.content)["error"]["errors"][0]["reason"]
        except Exception:
            pass
        registrar({"erro": motivo or str(e.status_code if hasattr(e, "status_code") else e), "detalhe": str(e)[:300]})
        if motivo == "quotaExceeded":
            sys.exit("❌ Cota do dia acabou (10.000 unidades). Volta à meia-noite do Pacífico, 5h de Brasília.")
        if motivo in ("uploadLimitExceeded",):
            sys.exit("❌ Limite de uploads do canal no dia. Subir pelo YouTube Studio ou esperar.")
        sys.exit(f"❌ API do YouTube recusou ({motivo or 'erro'}): {e}")


# ---------- login ----------

def cmd_login(args):
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not CLIENTE.exists():
        sys.exit(f"❌ Falta o cliente OAuth em {CLIENTE} (Google Cloud > Clientes > App para computador > baixar JSON)")
    webbrowser.register("radargta", None,
                        webbrowser.GenericBrowser([CHROME, f"--profile-directory={PERFIL_RADARGTA}", "%s"]))
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENTE), ESCOPOS)
    print("Abrindo o Chrome do canal. Escolha a conta Google e o canal.")
    cred = flow.run_local_server(port=0, browser="radargta", prompt="consent", access_type="offline",
                                 authorization_prompt_message="Se não abrir, cole no Chrome radargta: {url}",
                                 success_message="Pronto, pode fechar esta aba e voltar ao Claude.",
                                 timeout_seconds=1800)
    TOKEN.write_text(cred.to_json(), encoding="utf-8")
    registrar({"evento": "login"})
    print(f"✅ Token salvo em {TOKEN}")
    cmd_quem(args)


def cmd_quem(args):
    r = executar(yt().channels().list(part="snippet,statistics,contentDetails", mine=True))
    if not r.get("items"):
        sys.exit("❌ A conta autorizada não tem canal. Refazer o login escolhendo o canal Foragido GTA.")
    c = r["items"][0]
    s = c["statistics"]
    print(f"Canal: {c['snippet']['title']} ({c['snippet'].get('customUrl', '')}) id {c['id']}")
    print(f"Inscritos {s.get('subscriberCount')} · vídeos {s.get('videoCount')} · views {s.get('viewCount')}")
    if c["id"] != CANAL_ID:
        print(f"⚠️ Não é o @foragido_gta ({CANAL_ID}). Refazer o login e escolher o canal certo.")
    else:
        print("✅ Canal certo")


# ---------- publicar.md ----------

ROTULO = r"^\*\*[^*\n]{1,70}(?::\*\*|\*\*:)"  # linha que abre outro bloco: **Quando:** ...


def secao(md, nome):
    """Bloco de '## Nome ...' ou '**Nome ...:**' até o próximo ## ou rótulo em negrito (3 formatos de publicar.md)."""
    m = re.search(rf"^(?:##+\s*{nome}[^\n]*\n|\*\*{nome}[^*\n]*(?::\*\*|\*\*:)[ \t]*)(.*?)(?=^##|{ROTULO}|\Z)",
                  md, re.M | re.S)
    return m.group(1).strip() if m else ""


def titulos_de(bloco):
    saida = []
    for linha in bloco.splitlines():
        s = re.sub(r"^\s*(?:[-*]\s+|\d+\.\s+)", "", linha.strip())
        if not s or s.lower().startswith(("alternativa", "frase", ">")):
            continue
        m = re.match(r"\*\*(.+?)\*\*", s)
        s = m.group(1) if m else re.sub(r"\s*\([^)]*\)\s*$", "", s)
        s = re.sub(r"^[A-E](?:\s*\([^)]*\))?:\s*", "", s)  # "A (recomendado): ..." → título
        s = s.strip().strip('"“”')
        if s:
            saida.append(s)
    return saida


LINGUAS = {"en": "(Narrated in Portuguese)", "es": "(Narración en portugués)"}
LOCAL_RE = (r"^(?:##+\s*(?P<c1>Título|Descrição)\s*\((?P<l1>EN|ES)\)[^\n]*\n"
            r"|\*\*(?P<c2>Título|Descrição)\s*\((?P<l2>EN|ES)\)[^*\n]*(?::\*\*|\*\*:)[ \t]*)"
            r"(?P<corpo>.*?)(?=^##|" + ROTULO + r"|\Z)")


def localizacoes_de(md):
    """'## Título (EN)' e '## Descrição (EN)' (ES opcional) → {"en": {"title", "description"}}. 18/11, JV: inglês em todo vídeo."""
    achados = {}
    for m in re.finditer(LOCAL_RE, md, re.M | re.S):
        campo, lingua = m["c1"] or m["c2"], (m["l1"] or m["l2"]).lower()
        achados.setdefault(lingua, {})["title" if campo == "Título" else "description"] = m["corpo"].strip()
    loc = {}
    for lingua, d in achados.items():
        titulo = (titulos_de(d.get("title", "")) or [""])[0]
        if not titulo:
            continue
        desc = d.get("description", "").strip()
        fecho = LINGUAS[lingua]
        if fecho.lower() not in desc.lower():
            desc = (desc + "\n\n" + fecho).strip()
        loc[lingua] = {"title": titulo, "description": desc}
    return loc



NOTA_INTERNA = re.compile(r"^\s*\*\(.*\)\*\s*$|^\s*-?\s*Refer[êe]ncia d[ao] (pauta|formato)|teto de 12", re.I)


def limpar_nota_interna(texto):
    """Tira da descrição pública as linhas de nota nossa do publicar.md (JV 19/11: vazaram em 14 vídeos)."""
    if not texto:
        return texto
    linhas = texto.splitlines()
    boas = [l for l in linhas if not NOTA_INTERNA.search(l)]
    if len(boas) != len(linhas):
        print(f"⚠️ {len(linhas) - len(boas)} linha(s) de nota interna tirada(s) da descrição")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(boas)).strip()

def ler_publicar(pasta, titulo_n=1):
    md = (pasta / "publicar.md").read_text(encoding="utf-8")
    md = re.sub(LOCAL_RE, "", md, flags=re.M | re.S)  # tira EN/ES antes de ler o português
    m = re.search(r"\*\*Arquivo:\s*(?:\*\*\s*)?`([^`]+)`", md)  # **Arquivo:** `x` ou **Arquivo: `x`**
    if not m:
        sys.exit("❌ publicar.md sem linha **Arquivo:** `...`")
    video = pasta / m.group(1)
    titulos = titulos_de(secao(md, r"Título"))
    if len(titulos) < titulo_n:
        sys.exit("❌ Não achei o título no publicar.md (## Título ou **Título:**). Passe --titulo.")
    descricao = secao(md, r"Descrição \(YouTube\)") or secao(md, "Descrição")
    tags = [x.strip() for x in secao(md, "Tags").replace("\n", ",").split(",") if x.strip()]
    return video, titulos[titulo_n - 1], descricao, tags


def quando_rfc(texto):
    dt = datetime.strptime(texto, "%Y-%m-%d %H:%M").replace(tzinfo=SP)
    if dt <= datetime.now(SP) + timedelta(minutes=15):
        sys.exit("❌ Horário no passado ou a menos de 15 min. O YouTube exige publishAt no futuro.")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def ver_local(iso):
    if not iso:
        return "—"
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(SP).strftime("%a %d/%m %H:%M")


def uploads(y, n=50):
    canal = executar(y.channels().list(part="contentDetails", mine=True))["items"][0]
    lista = canal["contentDetails"]["relatedPlaylists"]["uploads"]
    itens, token = [], None
    while len(itens) < n:
        r = executar(y.playlistItems().list(part="contentDetails", playlistId=lista, maxResults=50, pageToken=token))
        itens += [i["contentDetails"]["videoId"] for i in r.get("items", [])]
        token = r.get("nextPageToken")
        if not token:
            break
    ids = itens[:n]
    vids = []
    for i in range(0, len(ids), 50):
        r = executar(y.videos().list(part="snippet,status,contentDetails,statistics", id=",".join(ids[i:i + 50])))
        vids += r.get("items", [])
    return vids


def miniatura(img):
    """Thumb do YouTube: até 2 MB. Converte para JPEG 1280x720 se passar."""
    p = Path(img)
    if p.stat().st_size <= 2_000_000 and p.suffix.lower() in (".jpg", ".jpeg", ".png"):
        return p
    from PIL import Image
    saida = p.with_name(p.stem + "_yt.jpg")
    im = Image.open(p).convert("RGB")
    im.thumbnail((1280, 1280))
    q = 92
    while True:
        im.save(saida, "JPEG", quality=q)
        if saida.stat().st_size <= 2_000_000 or q < 60:
            return saida
        q -= 8


def cmd_subir(args):
    from googleapiclient.http import MediaFileUpload
    alvo = Path(args.alvo)
    loc = {}
    if alvo.is_dir():
        video, titulo, descricao, tags = ler_publicar(alvo, args.titulo_n)
        loc = localizacoes_de((alvo / "publicar.md").read_text(encoding="utf-8"))
    else:
        video, titulo, descricao, tags = alvo, None, "", []
    titulo = args.titulo or titulo
    if args.descricao_arquivo:
        descricao = Path(args.descricao_arquivo).read_text(encoding="utf-8").strip()
    if args.tags:
        tags = [x.strip() for x in args.tags.split(",") if x.strip()]
    if not video.exists():
        sys.exit(f"❌ Vídeo não existe: {video}")
    if re.search(r"previa|prévia|preview|_baixa", video.name, re.I):
        sys.exit(f"❌ Isto é prévia, não o final: {video.name}")
    if not titulo:
        sys.exit("❌ Falta título (--titulo ou pasta com publicar.md)")
    if len(titulo) > 100:
        sys.exit(f"❌ Título com {len(titulo)} caracteres (máx. 100)")
    descricao = limpar_nota_interna(descricao)
    if len(descricao) > 5000:
        sys.exit(f"❌ Descrição com {len(descricao)} caracteres (máx. 5.000)")
    while tags and len(",".join(tags)) > 480:  # limite de 500 do YouTube, com folga
        tags.pop()
    n_tags = len({t.lower() for t in re.findall(r"#\w+", titulo + " " + descricao)})
    if n_tags > 12:
        sys.exit(f"❌ {n_tags} hashtags (máximo 12, regra do JV de 15/11)")
    if "<" in descricao or ">" in descricao or "<" in titulo or ">" in titulo:
        sys.exit("❌ Título ou descrição com < ou >: o YouTube recusa")

    status = {"selfDeclaredMadeForKids": False, "embeddable": True, "license": "youtube"}
    if args.quando:
        status["privacyStatus"] = "private"
        status["publishAt"] = quando_rfc(args.quando)
    elif args.privado:
        status["privacyStatus"] = "private"
    elif args.nao_listado:
        status["privacyStatus"] = "unlisted"
    else:
        status["privacyStatus"] = "public"
    # IA realista: "não" por padrão (JV 19/11; footage real, upscaling e o clone da própria voz não se declaram).
    # --ia só quando houver cena realista gerada por IA ou pessoa real dizendo o que não disse.
    status["containsSyntheticMedia"] = bool(args.ia)
    corpo = {"snippet": {"title": titulo, "description": descricao, "tags": tags, "categoryId": "20",
                         "defaultLanguage": "pt-BR", "defaultAudioLanguage": "pt-BR"},
             "status": status}
    for lingua, d in loc.items():
        if len(d["title"]) > 100 or len(d["description"]) > 5000 or re.search(r"[<>]", d["title"] + d["description"]):
            sys.exit(f"❌ Título/descrição em {lingua} passa de 100/5.000 caracteres ou tem < >")
    if loc:
        corpo["localizations"] = loc

    mb = video.stat().st_size / 1e6
    print(f"Vídeo: {video} ({mb:.0f} MB)")
    print(f"Título: {titulo}")
    print(f"Tags: {len(tags)} · descrição: {len(descricao)} caracteres")
    print(f"Privacidade: {status['privacyStatus']}" + (f" · publica {args.quando} (São Paulo)" if args.quando else ""))
    for lingua, d in loc.items():
        print(f"Título {lingua}: {d['title']} · descrição {lingua}: {len(d['description'])} caracteres")
    if "en" not in loc:
        print("⚠️ Sem '## Título (EN)' no publicar.md: o vídeo sobe sem título em inglês (regra de 18/11: todo vídeo).")
    if args.teste:
        print("(teste: nada foi enviado)")
        return

    y = yt()
    if not args.forcar:
        for v in uploads(y, 30):
            if v["snippet"]["title"].strip().lower() == titulo.strip().lower():
                sys.exit(f"❌ Já existe vídeo com esse título: {v['id']} ({v['status']['privacyStatus']}). --forcar para subir mesmo assim.")

    midia = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=16 * 1024 * 1024, resumable=True)
    req = y.videos().insert(part="snippet,status" + (",localizations" if loc else ""), body=corpo, media_body=midia, notifySubscribers=True)
    resp = None
    from googleapiclient.errors import HttpError
    tentativas = 0
    while resp is None:
        try:
            st, resp = req.next_chunk()
            if st:
                print(f"  {st.progress() * 100:.0f}%", flush=True)
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and tentativas < 5:
                tentativas += 1
                continue
            registrar({"evento": "subir", "erro": str(e)[:300], "arquivo": str(video)})
            sys.exit(f"❌ Upload recusado: {e}")
    vid = resp["id"]
    registrar({"evento": "subir", "video_id": vid, "arquivo": str(video), "titulo": titulo,
               "privacidade": status["privacyStatus"], "publishAt": status.get("publishAt"), "linguas": list(loc)})
    print(f"✅ Subiu: https://youtu.be/{vid}  (id {vid})")
    if args.thumb:
        cmd_thumb(argparse.Namespace(video_id=vid, img=args.thumb))
    # conferência lida de volta
    lido = executar(y.videos().list(part="status,localizations", id=vid))["items"][0]
    v = lido["status"]
    print(f"Lido de volta: {v['privacyStatus']} · publishAt {ver_local(v.get('publishAt'))} · upload {v.get('uploadStatus')}"
          + (f" · línguas {', '.join(lido.get('localizations', {})) or 'nenhuma'}" if loc else ""))
    if args.quando and not v.get("publishAt"):
        print("⚠️ O YouTube não guardou o agendamento. Pode ser a trava de app não verificado: conferir no YouTube Studio.")
    if not args.quando and status["privacyStatus"] == "public" and v["privacyStatus"] != "public":
        print("⚠️ Pedi público e ficou privado: trava de app não verificado. Ver pesquisa/apis-redes-sociais.md.")
    # Pendência que só o YouTube Studio faz: Título do jogo = Grand Theft Auto VI (JV 19/11; a API não grava esse campo).
    pend = ROOT / "fila" / "studio-pendente.md"
    if not pend.exists():
        pend.write_text("# YouTube Studio: marcar \"Título do jogo\" = Grand Theft Auto VI\n\n"
                        "Cada upload pela API entra aqui. O `operador` marca no YouTube Studio e troca [ ] por [x].\n\n",
                        encoding="utf-8")
    with open(pend, "a", encoding="utf-8") as f:
        f.write(f"- [ ] {vid} · {datetime.now(SP).strftime('%d/%m %H:%M')} · {titulo}\n")
    print("📝 Jogo GTA VI: pendência gravada em fila/studio-pendente.md (só pelo YouTube Studio).")
    # Legenda PT enviada (JV 19/11): transcreve o arquivo local, que serve também para vídeo agendado ou privado.
    if not getattr(args, "sem_legenda", False):
        r = subprocess.run([sys.executable, str(Path(__file__).parent / "legendas_yt.py"), "local", vid, str(video)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        print("\n".join(l for l in (r.stdout + r.stderr).splitlines() if "arning" not in l)[-600:]
              or "⚠️ legenda: sem saída; rodar python tools/legendas_yt.py local " + vid + " <mp4>")


def cmd_status(args):
    r = executar(yt().videos().list(part="snippet,status,processingDetails,statistics", id=args.video_id))
    if not r.get("items"):
        sys.exit("❌ Vídeo não encontrado (ou não é deste canal)")
    v = r["items"][0]
    s = v["status"]
    print(f"{v['snippet']['title']}  https://youtu.be/{v['id']}")
    print(f"Privacidade {s['privacyStatus']} · publishAt {ver_local(s.get('publishAt'))} · upload {s.get('uploadStatus')}")
    if s.get("rejectionReason") or s.get("failureReason"):
        print(f"⚠️ Recusa: {s.get('rejectionReason') or s.get('failureReason')}")
    p = v.get("processingDetails", {})
    if p:
        print(f"Processamento: {p.get('processingStatus')}")
    st = v.get("statistics", {})
    print(f"Views {st.get('viewCount', 0)} · curtidas {st.get('likeCount', 0)} · comentários {st.get('commentCount', 0)}")


def cmd_editar(args):
    y = yt()
    v = executar(y.videos().list(part="snippet,status", id=args.video_id))["items"][0]
    sn, st = v["snippet"], v["status"]
    if args.titulo:
        sn["title"] = args.titulo
    if args.descricao_arquivo:
        sn["description"] = limpar_nota_interna(Path(args.descricao_arquivo).read_text(encoding="utf-8").strip())
    if args.tags:
        sn["tags"] = [x.strip() for x in args.tags.split(",") if x.strip()]
    if args.quando:
        st["privacyStatus"] = "private"
        st["publishAt"] = quando_rfc(args.quando)
    if args.publicar_agora:
        st.pop("publishAt", None)
        st["privacyStatus"] = "public"
    if args.privado:
        st.pop("publishAt", None)
        st["privacyStatus"] = "private"
    corpo = {"id": args.video_id,
             "snippet": {k: sn[k] for k in ("title", "description", "tags", "categoryId", "defaultLanguage",
                                            "defaultAudioLanguage") if k in sn},
             "status": {k: st[k] for k in ("privacyStatus", "publishAt", "selfDeclaredMadeForKids", "embeddable",
                                           "license", "containsSyntheticMedia") if k in st}}
    executar(y.videos().update(part="snippet,status", body=corpo))
    registrar({"evento": "editar", "video_id": args.video_id, "mudou": [k for k in ("titulo", "descricao_arquivo",
               "tags", "quando", "publicar_agora", "privado") if getattr(args, k)]})
    print("✅ Editado")
    cmd_status(argparse.Namespace(video_id=args.video_id))


def cmd_localizar(args):
    """Título e descrição em outra língua (busca de fora). O áudio continua em português."""
    yt_ = yt()
    v = executar(yt_.videos().list(part="snippet,localizations", id=args.video_id))["items"][0]
    sn = v["snippet"]
    sn["defaultLanguage"] = sn.get("defaultLanguage") or "pt-BR"
    loc = v.get("localizations", {})
    desc = limpar_nota_interna(Path(args.descricao_arquivo).read_text(encoding="utf-8").strip() if args.descricao_arquivo else "")
    if len(args.titulo) > 100:
        sys.exit("❌ Título com mais de 100 caracteres")
    loc[args.lingua] = {"title": args.titulo, "description": desc}
    corpo = {"id": args.video_id, "localizations": loc,
             "snippet": {k: sn[k] for k in ("title", "description", "tags", "categoryId", "defaultLanguage",
                                            "defaultAudioLanguage") if k in sn}}
    r = executar(yt_.videos().update(part="snippet,localizations", body=corpo))
    registrar({"evento": "localizar", "video_id": args.video_id, "lingua": args.lingua})
    print(f"✅ {args.lingua} gravado; línguas do vídeo: {', '.join(r.get('localizations', {}))}")


def cmd_apagar(args):
    executar(yt().videos().delete(id=args.video_id))
    registrar({"evento": "apagar", "video_id": args.video_id})
    print(f"✅ Apagado {args.video_id}")


def cmd_thumb(args):
    from googleapiclient.http import MediaFileUpload
    img = miniatura(args.img)
    tipo = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
    executar(yt().thumbnails().set(videoId=args.video_id, media_body=MediaFileUpload(str(img), mimetype=tipo)))
    registrar({"evento": "thumb", "video_id": args.video_id, "img": str(img)})
    print(f"✅ Thumb aplicada ({img.name})")


def cmd_videos(args):
    for v in uploads(yt(), args.n):
        s = v["status"]
        quando = ver_local(s.get("publishAt")) if s.get("publishAt") else ver_local(v["snippet"].get("publishedAt"))
        print(f"{v['id']}  {s['privacyStatus']:8} {quando:14} {v['statistics'].get('viewCount', 0):>7} views  "
              f"{v['snippet']['title'][:70]}")


# ---------- comentários ----------

def fios(y, video_id):
    itens, token = [], None
    while True:
        r = executar(y.commentThreads().list(part="snippet,replies", videoId=video_id, maxResults=100,
                                             pageToken=token, textFormat="plainText"))
        itens += r.get("items", [])
        token = r.get("nextPageToken")
        if not token:
            return itens


def cmd_comentarios(args):
    for t in fios(yt(), args.video_id):
        c = t["snippet"]["topLevelComment"]
        s = c["snippet"]
        print(f"[{c['id']}] {s['authorDisplayName']}: {s['textDisplay']}  ({s.get('likeCount', 0)} curtidas)")
        for r in (t.get("replies") or {}).get("comments", []):
            print(f"    ↳ [{r['id']}] {r['snippet']['authorDisplayName']}: {r['snippet']['textDisplay']}")


def cmd_comentar(args):
    r = executar(yt().commentThreads().insert(part="snippet", body={"snippet": {"videoId": args.video_id,
                 "topLevelComment": {"snippet": {"textOriginal": args.texto}}}}))
    registrar({"evento": "comentar", "video_id": args.video_id, "comentario_id": r["id"]})
    print(f"✅ Comentado (id {r['id']}). Fixar: YouTube Studio > Comentários.")


def cmd_responder(args):
    r = executar(yt().comments().insert(part="snippet", body={"snippet": {"parentId": args.comment_id,
                 "textOriginal": args.texto}}))
    registrar({"evento": "responder", "pai": args.comment_id, "comentario_id": r["id"]})
    print(f"✅ Respondido (id {r['id']})")


def cmd_varrer(args):
    y = yt()
    desde = datetime.fromisoformat(args.desde).replace(tzinfo=SP)
    saida = []
    for v in uploads(y, 50):
        pub = v["snippet"].get("publishedAt")
        if v["status"]["privacyStatus"] != "public" or not pub:
            continue
        if datetime.fromisoformat(pub.replace("Z", "+00:00")) < desde - timedelta(days=7):
            continue
        if not int(v["statistics"].get("commentCount", 0)):
            continue
        base = {"rede": "youtube", "post_id": v["id"], "post_titulo": v["snippet"]["title"][:80]}
        for t in fios(y, v["id"]):
            c = t["snippet"]["topLevelComment"]
            s = c["snippet"]
            todos = [(c, None)] + [(r, s["authorDisplayName"]) for r in (t.get("replies") or {}).get("comments", [])]
            for com, pai in todos:
                cs = com["snippet"]
                if datetime.fromisoformat(cs["publishedAt"].replace("Z", "+00:00")) < desde:
                    continue
                item = {**base, "comentario_id": com["id"], "autor": cs["authorDisplayName"],
                        "texto": cs["textDisplay"], "data": cs["publishedAt"], "curtidas": cs.get("likeCount", 0)}
                if pai:
                    item["pai"] = pai
                saida.append(item)
    destino = Path(args.saida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(saida, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ {len(saida)} comentário(s) do YouTube em {destino}")


# ---------- métricas ----------

def relatorio(**kw):
    r = executar(yta().reports().query(ids="channel==MINE", **kw))
    cols = [h["name"] for h in r.get("columnHeaders", [])]
    return cols, r.get("rows", [])


def cmd_metricas(args):
    y = yt()
    v = executar(y.videos().list(part="snippet,contentDetails", id=args.video_id))["items"][0]
    inicio = args.desde or v["snippet"]["publishedAt"][:10]
    cols, linhas = relatorio(startDate=inicio, endDate=date.today().isoformat(), filters=f"video=={args.video_id}",
                             metrics="views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
                                     "subscribersGained,subscribersLost,shares,likes,comments")
    print(f"{v['snippet']['title']} ({inicio} a hoje; Analytics atrasa 1 a 3 dias)")
    if not linhas:
        print("Sem dados ainda.")
        return
    nomes = {"views": "views", "estimatedMinutesWatched": "minutos assistidos", "averageViewDuration": "duração média (s)",
             "averageViewPercentage": "% média assistida", "subscribersGained": "inscritos ganhos",
             "subscribersLost": "inscritos perdidos", "shares": "envios", "likes": "curtidas", "comments": "comentários"}
    for c, val in zip(cols, linhas[0]):
        print(f"  {nomes.get(c, c)}: {round(val, 1) if isinstance(val, float) else val}")


def cmd_retencao(args):
    y = yt()
    v = executar(y.videos().list(part="snippet", id=args.video_id))["items"][0]
    cols, linhas = relatorio(startDate=v["snippet"]["publishedAt"][:10], endDate=date.today().isoformat(),
                             filters=f"video=={args.video_id}", dimensions="elapsedVideoTimeRatio",
                             metrics="audienceWatchRatio,relativeRetentionPerformance")
    if not linhas:
        print("Sem curva ainda (precisa de views suficientes e 1 a 3 dias).")
        return
    print("% do vídeo · ainda assistindo · comparado a vídeos parecidos (0,5 = na média)")
    for pos, ratio, rel in linhas:
        if round(pos * 100) % 5 == 0:
            print(f"  {pos * 100:5.0f}%  {ratio * 100:6.1f}%  {rel:.2f}")


def cmd_canal(args):
    fim = date.today()
    cols, linhas = relatorio(startDate=(fim - timedelta(days=args.dias)).isoformat(), endDate=fim.isoformat(),
                             dimensions="day", metrics="views,estimatedMinutesWatched,subscribersGained,subscribersLost",
                             sort="day")
    print("dia         views  minutos  +inscr  -inscr")
    for d, views, mins, ganhos, perdidos in linhas:
        print(f"{d}  {views:>6} {mins:>8} {ganhos:>7} {perdidos:>7}")


# ---- playlists (skill titulos-e-busca, JV 19/11: "criar playlists temáticas e organizar os vídeos") ----
def _playlists(y):
    out, token = [], None
    while True:
        r = executar(y.playlists().list(part="snippet,contentDetails,status", mine=True, maxResults=50, pageToken=token))
        out += r.get("items", [])
        token = r.get("nextPageToken")
        if not token:
            return out


def _itens(y, pid):
    out, token = [], None
    while True:
        r = executar(y.playlistItems().list(part="snippet,contentDetails", playlistId=pid, maxResults=50, pageToken=token))
        out += r.get("items", [])
        token = r.get("nextPageToken")
        if not token:
            return out


def cmd_playlists(args):
    y = yt()
    for p in _playlists(y):
        print(f"{p['id']} · {p['status']['privacyStatus']} · {p['contentDetails']['itemCount']} vídeos · {p['snippet']['title']}")
        if args.itens:
            for i in _itens(y, p["id"]):
                print(f"    {i['snippet']['position']:>2}. {i['contentDetails']['videoId']} · {i['snippet']['title']}")


def cmd_sem_playlist(args):
    y = yt()
    dentro = set()
    for p in _playlists(y):
        dentro |= {i["contentDetails"]["videoId"] for i in _itens(y, p["id"])}
    fora = [v for v in uploads(y, args.n) if v["id"] not in dentro and
            (v["status"]["privacyStatus"] == "public" or v["status"].get("publishAt"))]
    for v in fora:
        dur = v["contentDetails"]["duration"]
        print(f"{v['id']} · {v['snippet']['publishedAt'][:10]} · {dur} · {v['snippet']['title']}")
    print(f"{len(fora)} vídeo(s) fora de playlist")


def cmd_playlist_criar(args):
    desc = Path(args.descricao_arquivo).read_text(encoding="utf-8") if args.descricao_arquivo else ""
    y = yt()
    if any(p["snippet"]["title"].strip().lower() == args.titulo.strip().lower() for p in _playlists(y)):
        sys.exit(f"❌ Já existe playlist com o título '{args.titulo}'.")
    r = executar(y.playlists().insert(part="snippet,status", body={
        "snippet": {"title": args.titulo, "description": desc, "defaultLanguage": "pt-BR"},
        "status": {"privacyStatus": "private" if args.privada else "public"}}))
    registrar({"evento": "playlist-criar", "playlist_id": r["id"], "titulo": args.titulo})
    print(f"✅ {r['id']} · {args.titulo}")


def cmd_playlist_editar(args):
    y = yt()
    item = executar(y.playlists().list(part="snippet,status", id=args.playlist_id))["items"][0]
    atual = item["snippet"]
    sn = {"title": args.titulo or atual["title"], "defaultLanguage": atual.get("defaultLanguage", "pt-BR"),
          "description": Path(args.descricao_arquivo).read_text(encoding="utf-8") if args.descricao_arquivo else atual.get("description", "")}
    st = {"privacyStatus": "private" if args.privada else ("public" if args.publica else item["status"]["privacyStatus"])}
    executar(y.playlists().update(part="snippet,status", body={"id": args.playlist_id, "snippet": sn, "status": st}))
    registrar({"evento": "playlist-editar", "playlist_id": args.playlist_id, "titulo": sn["title"], "status": st["privacyStatus"]})
    print(f"✅ {args.playlist_id} · {st['privacyStatus']} · {sn['title']}")


def cmd_canal_editar(args):
    """Troca a descrição ("Sobre") e acrescenta palavras-chave do canal, preservando o resto do brandingSettings."""
    y = yt()
    c = executar(y.channels().list(part="brandingSettings", mine=True))["items"][0]
    canal = dict(c["brandingSettings"].get("channel", {}))
    if args.descricao_arquivo:
        desc = Path(args.descricao_arquivo).read_text(encoding="utf-8").strip()
        if len(desc) > 1000:
            sys.exit(f"❌ Descrição com {len(desc)} caracteres; o limite do canal é 1.000.")
        canal["description"] = desc
    if args.palavras_mais_arquivo:
        atuais = canal.get("keywords", "")
        novas = [l.strip() for l in Path(args.palavras_mais_arquivo).read_text(encoding="utf-8").splitlines() if l.strip()]
        for p in novas:
            item = f'"{p}"' if " " in p else p
            if item.lower() not in atuais.lower():
                atuais = (atuais + " " + item).strip()
        canal["keywords"] = atuais
    executar(y.channels().update(part="brandingSettings", body={"id": c["id"], "brandingSettings": {"channel": canal}}))
    lido = executar(y.channels().list(part="brandingSettings", mine=True))["items"][0]["brandingSettings"]["channel"]
    registrar({"evento": "canal-editar", "descricao": lido.get("description", "")[:120], "palavras": lido.get("keywords", "")})
    print(f"✅ canal · descrição com {len(lido.get('description', ''))} caracteres · começa: {lido.get('description', '')[:70]}")
    print(f"   palavras-chave: {lido.get('keywords', '')}")


def cmd_playlist_add(args):
    y = yt()
    ja = {i["contentDetails"]["videoId"] for i in _itens(y, args.playlist_id)}
    for n, vid in enumerate(args.video_ids):
        if vid in ja:
            print(f"= {vid} já está")
            continue
        sn = {"playlistId": args.playlist_id, "resourceId": {"kind": "youtube#video", "videoId": vid}}
        if args.posicao is not None:
            sn["position"] = args.posicao + n
        executar(y.playlistItems().insert(part="snippet", body={"snippet": sn}))
        registrar({"evento": "playlist-add", "playlist_id": args.playlist_id, "video_id": vid})
        print(f"✅ {vid} → {args.playlist_id}")


def cmd_playlist_tirar(args):
    y = yt()
    for i in _itens(y, args.playlist_id):
        if i["contentDetails"]["videoId"] == args.video_id:
            executar(y.playlistItems().delete(id=i["id"]))
            registrar({"evento": "playlist-tirar", "playlist_id": args.playlist_id, "video_id": args.video_id})
            print(f"✅ {args.video_id} saiu de {args.playlist_id}")
            return
    print(f"= {args.video_id} não está em {args.playlist_id}")


def cmd_playlist_apagar(args):
    """Apaga a playlist (os vídeos continuam no canal). Ação externa: só com OK do JV."""
    y = yt()
    item = executar(y.playlists().list(part="snippet", id=args.playlist_id))["items"][0]
    executar(y.playlists().delete(id=args.playlist_id))
    registrar({"evento": "playlist-apagar", "playlist_id": args.playlist_id, "titulo": item["snippet"]["title"]})
    print(f"✅ apagada {args.playlist_id} · {item['snippet']['title']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="YouTube pela API oficial")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login").set_defaults(f=cmd_login)
    sub.add_parser("quem").set_defaults(f=cmd_quem)
    s = sub.add_parser("subir"); s.add_argument("alvo"); s.add_argument("--quando"); s.add_argument("--thumb")
    s.add_argument("--titulo"); s.add_argument("--titulo-n", type=int, default=1); s.add_argument("--descricao-arquivo"); s.add_argument("--sem-legenda", action="store_true")
    s.add_argument("--tags"); s.add_argument("--privado", action="store_true"); s.add_argument("--nao-listado", action="store_true")
    s.add_argument("--ia", action="store_true"); s.add_argument("--forcar", action="store_true")
    s.add_argument("--teste", action="store_true"); s.set_defaults(f=cmd_subir)
    s = sub.add_parser("status"); s.add_argument("video_id"); s.set_defaults(f=cmd_status)
    s = sub.add_parser("editar"); s.add_argument("video_id"); s.add_argument("--titulo"); s.add_argument("--descricao-arquivo")
    s.add_argument("--tags"); s.add_argument("--quando"); s.add_argument("--publicar-agora", action="store_true")
    s.add_argument("--privado", action="store_true", help="tira do ar (privado, sem agendamento)")
    s.set_defaults(f=cmd_editar)
    s = sub.add_parser("localizar"); s.add_argument("video_id"); s.add_argument("--lingua", required=True)
    s.add_argument("--titulo", required=True); s.add_argument("--descricao-arquivo"); s.set_defaults(f=cmd_localizar)
    s = sub.add_parser("apagar"); s.add_argument("video_id"); s.set_defaults(f=cmd_apagar)
    s = sub.add_parser("thumb"); s.add_argument("video_id"); s.add_argument("img"); s.set_defaults(f=cmd_thumb)
    s = sub.add_parser("videos"); s.add_argument("--n", type=int, default=10); s.set_defaults(f=cmd_videos)
    s = sub.add_parser("comentarios"); s.add_argument("video_id"); s.set_defaults(f=cmd_comentarios)
    s = sub.add_parser("comentar"); s.add_argument("video_id"); s.add_argument("texto"); s.set_defaults(f=cmd_comentar)
    s = sub.add_parser("responder"); s.add_argument("comment_id"); s.add_argument("texto"); s.set_defaults(f=cmd_responder)
    s = sub.add_parser("varrer"); s.add_argument("--desde", required=True); s.add_argument("--saida", required=True)
    s.set_defaults(f=cmd_varrer)
    s = sub.add_parser("metricas"); s.add_argument("video_id"); s.add_argument("--desde"); s.set_defaults(f=cmd_metricas)
    s = sub.add_parser("retencao"); s.add_argument("video_id"); s.set_defaults(f=cmd_retencao)
    s = sub.add_parser("canal"); s.add_argument("--dias", type=int, default=7); s.set_defaults(f=cmd_canal)
    s = sub.add_parser("canal-editar"); s.add_argument("--descricao-arquivo"); s.add_argument("--palavras-mais-arquivo")
    s.set_defaults(f=cmd_canal_editar)
    s = sub.add_parser("playlists"); s.add_argument("--itens", action="store_true"); s.set_defaults(f=cmd_playlists)
    s = sub.add_parser("sem-playlist"); s.add_argument("--n", type=int, default=200); s.set_defaults(f=cmd_sem_playlist)
    s = sub.add_parser("playlist-criar"); s.add_argument("titulo"); s.add_argument("--descricao-arquivo")
    s.add_argument("--privada", action="store_true"); s.set_defaults(f=cmd_playlist_criar)
    s = sub.add_parser("playlist-editar"); s.add_argument("playlist_id"); s.add_argument("--titulo"); s.add_argument("--descricao-arquivo")
    s.add_argument("--privada", action="store_true"); s.add_argument("--publica", action="store_true")
    s.set_defaults(f=cmd_playlist_editar)
    s = sub.add_parser("playlist-add"); s.add_argument("playlist_id"); s.add_argument("video_ids", nargs="+")
    s.add_argument("--posicao", type=int); s.set_defaults(f=cmd_playlist_add)
    s = sub.add_parser("playlist-tirar"); s.add_argument("playlist_id"); s.add_argument("video_id"); s.set_defaults(f=cmd_playlist_tirar)
    s = sub.add_parser("playlist-apagar"); s.add_argument("playlist_id"); s.set_defaults(f=cmd_playlist_apagar)
    ap.add_argument("--forcar-cota", action="store_true")
    args = ap.parse_args(argv)
    guardar_reserva(args.cmd, args.forcar_cota)
    # piloto automático dos cortes (17/11): a rodada ao-vivo publica sozinha enquanto o piloto não está pausado nem em ensaio
    piloto = (os.environ.get("RADAR_PILOTO") == "1" and not (ROOT / "fila" / "PILOTO-PAUSADO").exists() and not (ROOT / "fila" / "PILOTO-ENSAIO").exists())
    # série automática da rodada das 4h (JV 20/11): só com a liberação aberta pelo tools/serie_auto.py liberar
    import serie_auto
    serie = args.cmd in ("subir", "thumb") and serie_auto.liberacao() is not None
    if os.environ.get("RADAR_RODADA") == "1" and args.cmd not in LEITURA and not serie and not (piloto and args.cmd in ("subir", "comentar", "editar")) and not (os.environ.get("RESPOSTAS_RODADA") == "1" and args.cmd == "responder"):  # rotina de respostas, JV 17/11
        sys.exit(f"❌ Rodada das 4h: '{args.cmd}' não roda sem o JV (só leitura).")
    args.f(args)


if __name__ == "__main__":
    main()
