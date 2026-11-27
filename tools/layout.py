"""Layout do quadro FINAL: mede o vídeo renderizado, não o plano (19/11, pedido do JV).

    python tools/layout.py VIDEO.mp4 [--edicao edicao.json] [--passo 1.0] [--ini S] [--json] [--consertar [--aplicar]]

O `ocupacao.py` calcula as caixas pelo edicao.json, antes do render. Este lê o que saiu na tela: tira 1 quadro a cada
`--passo` segundos em resolução cheia, passa o OCR (RapidOCR) e mede em pixels reais:

  legenda fora do centro      a legenda inteira deslocada do eixo da tela
  legenda tampada             só um pedaço da frase se lê em muitos quadros (aviso)
  peça em cima da legenda     caixa de peça do plano (numeral, cronômetro, chip) sobre a caixa REAL da legenda lida
  legenda sobre outro texto   legenda encostada ou por cima de texto de peça ou de texto queimado na fonte (HUD, edição
                              do canal de origem), que o plano não enxerga
  texto na borda              texto nosso a menos de 8% da lateral ou 7% do topo (zona segura do celular, vertical)
  texto que não se lê         texto de peça do edicao.json que o OCR não acha em nenhum quadro da janela dela
Texto da FONTE (legenda nativa do jogo, edição do canal de origem) não gera alerta: sai numa linha ℹ️ e vira obstáculo
do --consertar, que adapta a nossa legenda e as nossas peças (altura e TAMANHO) em volta dele.
  detalhe                     quanto de detalhe fino o quadro tem (imagem mole = fonte ampliada)

Sem `--edicao` ele procura o edicao.json na pasta do vídeo. Sem as palavras da fala (`*.palavras.json`), a legenda é
achada pelo jeito: texto grande, perto do eixo, que muda de um quadro para o outro.
Saída: lista no console, `conferencia/layout.jpg` (os quadros acusados, com as caixas desenhadas) e `layout.json`.
❌ sai com código 1. Calibragem e casos de teste: tools/README.md.
"""
try:
    import _recursos  # noqa: F401  temporários em Youtube/_tmp, fora do Defender (21/11)
except ImportError:
    pass
import argparse
import difflib
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent

LADO_MIN = .08       # as mesmas margens do ocupacao.py (zona segura do celular, JV 16/11)
TOPO_MIN = .07
CENTRO_AVISO = .04   # desvio do eixo, fração da largura
CENTRO_GRAVE = .07
MIN_QUADROS = 3      # um defeito precisa aparecer em pelo menos tantos quadros (o OCR erra um ou outro)
ALT_LEGENDA = .023   # altura mínima da linha, fração da altura, para ser a NOSSA legenda (a linha sem ênfase do karaokê tem ~2,6%; a legenda do jogo, ~1,7%)
FIXO = .45           # texto igual no mesmo lugar em mais que isso dos quadros = fixo (selo, título fixo, HUD)
PEQUENO = .018      # texto mais baixo que isso (fração da altura) é HUD miúdo da fonte: não disputa com a legenda
DETALHE_MOLE = .24   # índice de detalhe abaixo disso em metade dos quadros = imagem mole (só avisa; ver README)


def _norm(s):
    s = unicodedata.normalize("NFD", str(s).upper())
    return re.sub(r"[^A-Z0-9]", "", "".join(c for c in s if unicodedata.category(c) != "Mn"))


def _parecido(a, b):
    """a (lido) bate com b (esperado)? Os dois normalizados."""
    if not a or not b:
        return False
    if len(a) >= 3 and (a in b or b in a):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= .72


def duracao(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def tirar_quadros(video, passo, pasta):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vf", f"fps=1/{passo}", "-q:v", "2",
                    str(Path(pasta) / "q%05d.jpg")], check=True)
    arqs = sorted(Path(pasta).glob("q*.jpg"))
    return [(passo * (i + .5), a) for i, a in enumerate(arqs)]  # o filtro fps pega o meio de cada intervalo


def ler(ocr, img):
    """[{caixa:(x0,y0,x1,y1), txt, n}] de um quadro."""
    res, _ = ocr(img)
    out = []
    for quad, txt, conf in res or []:
        if float(conf) < .5 or not _norm(txt):
            continue
        xs, ys = [p[0] for p in quad], [p[1] for p in quad]
        out.append({"caixa": (min(xs), min(ys), max(xs), max(ys)), "txt": txt, "n": _norm(txt)})
    return out


# ---------- o que se espera na tela (edicao.json) ----------
def esperado(ed, pasta):
    """Textos de peça com janela de tempo e as palavras da fala no tempo do vídeo."""
    pecas, palavras, t = [], [], 0.0
    if not isinstance(ed.get("cenas"), list):
        return pecas, palavras
    cache = {}
    for i, cena in enumerate(ed["cenas"]):
        fala = cena.get("fala") or {}
        dur = (fala["fim"] - fala["ini"]) if fala else float(cena.get("dur", 0))
        for c in cena.get("camadas", []):
            for campo in ("texto", "rotulo", "titulo"):
                if c.get(campo) and c.get("estilo") not in ("karaoke", "cartelas"):
                    pecas.append({"txt": str(c[campo]), "de": t + max(c.get("de", 0), 0), "ate": t + min(c.get("ate", dur), dur),
                                  "nome": f"cena {i + 1} · {c.get('estilo', c['tipo'])}"})
        arq = fala.get("arquivo")
        if arq:
            pj = Path(arq).with_suffix(".palavras.json")
            pj = pj if pj.is_absolute() else ROOT / pj
            if pj not in cache:
                try:
                    cache[pj] = json.loads(pj.read_text(encoding="utf-8"))
                except Exception:
                    cache[pj] = []
            for p in cache[pj]:
                if fala["ini"] - .05 <= p["i"] < fala["fim"]:
                    palavras.append({"n": _norm(p["p"]), "t": t + p["i"] - fala["ini"]})
        t += dur
    for c in ed.get("sobre", []):
        for campo in ("texto", "rotulo", "titulo", "serie"):
            if c.get(campo) and isinstance(c[campo], str):
                pecas.append({"txt": c[campo], "de": c.get("de", 0), "ate": c.get("ate", t), "nome": f"sobre · {c['tipo']}"})
    return pecas, [p for p in palavras if p["n"]]


# ---------- classificação das caixas ----------
def marcar_fixos(quadros, W, H):
    """Texto igual no mesmo lugar em boa parte do vídeo: selo, título fixo, HUD, marca do canal de origem."""
    cont = {}
    for q in quadros:
        vistos = set()
        for b in q["caixas"]:
            cx, cy = (b["caixa"][0] + b["caixa"][2]) / 2, (b["caixa"][1] + b["caixa"][3]) / 2
            ch = (b["n"][:10], round(cx / W * 12), round(cy / H * 20))
            b["_ch"] = ch
            if ch not in vistos:
                vistos.add(ch)
                cont[ch] = cont.get(ch, 0) + 1
    lim = max(3, FIXO * len(quadros))
    for q in quadros:
        for b in q["caixas"]:
            b["fixo"] = cont[b["_ch"]] >= lim


def marcar_legenda(quadros, palavras, W, H):
    """Marca b['leg']: a caixa é a legenda da fala."""
    for q in quadros:
        perto = [p["n"] for p in palavras if q["t"] - 1.6 <= p["t"] <= q["t"] + 1.6]
        junto = "".join(perto)
        for b in q["caixas"]:
            x0, y0, x1, y1 = b["caixa"]
            alto = (y1 - y0) >= ALT_LEGENDA * H
            if palavras:
                # a linha lida é feita de palavras ditas agora (inteiras), e não é um texto parado na tela
                # só palavra de 3+ letras conta: "e", "a", "de" batem com qualquer fala (legenda da fonte passava por nossa)
                partes = [w for w in (_norm(w) for w in re.split(r"\s+", b["txt"])) if len(w) >= 3]
                ok = sum(1 for w in partes if any(len(p) >= 3 and _parecido(w, p) for p in perto) or (len(w) >= 5 and w in junto))
                b["leg"] = bool(partes) and alto and not b["fixo"] and ok >= max(1, round(len(partes) * .6))
                if not b["leg"] and alto and not b["fixo"] and len(b["n"]) >= 4:
                    # a linha inteira, colada, dentro da fala colada ("SEGUNDO,CARRO", "TEVIRAM?", "A'GASOLINA")
                    m = difflib.SequenceMatcher(None, b["n"], junto, autojunk=False).find_longest_match(0, len(b["n"]), 0, len(junto))
                    b["leg"] = m.size >= max(4, .75 * len(b["n"]))
            else:
                eixo = abs((x0 + x1) / 2 - W / 2) < .22 * W
                b["leg"] = (y1 - y0) >= .03 * H and eixo and not b["fixo"]
        mudou = True
        while mudou:  # o OCR separa a palavra colorida do karaokê: o vizinho na mesma linha, da mesma altura, é legenda também
            mudou = False
            for b in q["caixas"]:
                if b["leg"] or b["fixo"]:
                    continue
                for l in q["caixas"]:
                    if not l["leg"]:
                        continue
                    hb, hl = b["caixa"][3] - b["caixa"][1], l["caixa"][3] - l["caixa"][1]
                    sobe = min(b["caixa"][3], l["caixa"][3]) - max(b["caixa"][1], l["caixa"][1])
                    vao = max(b["caixa"][0], l["caixa"][0]) - min(b["caixa"][2], l["caixa"][2])
                    if sobe >= .5 * min(hb, hl) and .6 <= hb / max(hl, 1) <= 1.7 and vao <= 1.2 * hl:
                        b["leg"] = mudou = True
                        break


def tirar_legenda_da_fonte(quadros, H):
    """A legenda do jogo (embaixo, miúda) diz as mesmas palavras da fala do corte e passava por nossa (Alanzoka, 18/11).
    A nossa é a maioria: quem está longe da faixa dela e tem menos de 70% da altura dela não é."""
    legs = [b for q in quadros for b in q["caixas"] if b["leg"]]
    if len(legs) < 6:
        return
    alt = float(np.percentile([b["caixa"][3] - b["caixa"][1] for b in legs], 75))  # a legenda do jogo entra na conta e puxa a mediana
    for q in quadros:
        grandes = [b for b in q["caixas"] if b["leg"] and (b["caixa"][3] - b["caixa"][1]) >= .7 * alt]
        for b in q["caixas"]:
            if b["leg"] and (b["caixa"][3] - b["caixa"][1]) < .7 * alt:
                cy = (b["caixa"][1] + b["caixa"][3]) / 2
                if not any(abs(cy - (g["caixa"][1] + g["caixa"][3]) / 2) < .12 * H for g in grandes):
                    b["leg"] = False


def _inter(a, b):
    return max(min(a[2], b[2]) - max(a[0], b[0]), 0) * max(min(a[3], b[3]) - max(a[1], b[1]), 0)


def _area(a):
    return max(a[2] - a[0], 0) * max(a[3] - a[1], 0)


def _mmss(s):
    return f"{int(s) // 60}:{s % 60:04.1f}"


# ---------- medidas de imagem ----------
def detalhe(img, mascara=None):
    """Índice de detalhe fino, 0 a ~1: quanto da textura some quando o quadro é reduzido à metade e ampliado de volta.
    Fonte ampliada 2x já não tinha esse detalhe, então perde pouco e o índice fica baixo. Mede só onde há textura."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    h, w = g.shape
    meio = cv2.resize(cv2.resize(g, (w // 2, h // 2), interpolation=cv2.INTER_AREA), (w, h), interpolation=cv2.INTER_CUBIC)
    lap_c, lap_m = np.abs(cv2.Laplacian(g, cv2.CV_32F)), np.abs(cv2.Laplacian(meio, cv2.CV_32F))
    tex = cv2.blur(lap_m, (31, 31)) > 1.2  # regiões com alguma textura; céu liso e preto não contam
    if mascara is not None:
        tex &= mascara
    if tex.mean() < .05:
        return None
    return float(1 - lap_m[tex].mean() / max(lap_c[tex].mean(), 1e-6))


# ---------- análise ----------
def analisar(video, ed=None, passo=1.0, pasta_ed=None, ini=0.0):
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    dur = duracao(video)
    quadros, W, H = [], 0, 0
    with tempfile.TemporaryDirectory() as tmp:
        for t, arq in tirar_quadros(video, passo, tmp):
            t += ini  # prévia de um trecho (fx.py --ini): o tempo do quadro volta a ser o do edicao.json
            img = cv2.imread(str(arq))
            if img is None:
                continue
            H, W = img.shape[:2]
            cx = ler(ocr, img)
            masc = np.ones((H, W), bool)
            for b in cx:  # o texto é sempre nítido: não entra na medida de detalhe da imagem
                x0, y0, x1, y1 = (int(v) for v in b["caixa"])
                masc[max(y0 - 12, 0):y1 + 12, max(x0 - 12, 0):x1 + 12] = False
            quadros.append({"t": t, "caixas": cx, "detalhe": detalhe(img, masc),
                            "mini": cv2.resize(img, (W * 480 // H, 480) if H >= W else (480, H * 480 // W))})
    pecas, palavras = esperado(ed, pasta_ed) if ed else ([], [])
    marcar_fixos(quadros, W, H)
    marcar_legenda(quadros, palavras, W, H)
    if ed:
        # a legenda mora onde o plano a pôs: texto que repete palavras da fala em outro lugar (painel "2 CARROS, 1 POSTO",
        # legenda do jogo embaixo) não é a legenda
        for i, t0, t1, leg in cenas_no_tempo(ed):
            if leg is None:
                continue
            cy = leg.get("y", .5) * H
            for q in quadros:
                if t0 <= q["t"] < t1:
                    for b in q["caixas"]:
                        if b["leg"] and abs((b["caixa"][1] + b["caixa"][3]) / 2 - cy) > .10 * H:
                            b["leg"] = False
    tirar_legenda_da_fonte(quadros, H)
    vertical = H > W
    ach = []  # {grave, tipo, t:[...], txt, caixas:[(t, caixa, cor)]}

    def achado(tipo, itens, frase, grave_se=None):
        """itens = [(t, caixa)]; só conta se repetir em MIN_QUADROS quadros (ou em 2 seguidos com legenda)."""
        if len(itens) < MIN_QUADROS:
            return
        ach.append({"grave": bool(grave_se), "tipo": tipo, "t": [i[0] for i in itens],
                    "txt": frase(itens), "caixas": itens})

    # 1. legenda fora do centro (por linha lida inteira: a linha tem que ter 2+ palavras para o eixo valer)
    fora, fora_g, n_leg = [], [], 0
    for q in quadros:
        leg = [b for b in q["caixas"] if b["leg"]]
        if not leg:
            continue
        n_leg += 1
        x0, x1 = min(b["caixa"][0] for b in leg), max(b["caixa"][2] for b in leg)
        y0, y1 = min(b["caixa"][1] for b in leg), max(b["caixa"][3] for b in leg)
        q["legenda"] = (x0, y0, x1, y1)
        d = ((x0 + x1) / 2 - W / 2) / W
        q["desvio"] = d
        if abs(d) >= CENTRO_AVISO:
            fora.append((q["t"], q["legenda"], d))
        if abs(d) >= CENTRO_GRAVE:
            fora_g.append((q["t"], q["legenda"], d))
    if n_leg:
        # desvio sistemático (a legenda inteira deslocada) pesa mais que um quadro com palavra mal lida
        med = float(np.median([q["desvio"] for q in quadros if "desvio" in q]))
        if n_leg >= MIN_QUADROS and abs(med) >= CENTRO_AVISO:
            ach.append({"grave": abs(med) >= CENTRO_GRAVE, "tipo": "legenda fora do centro", "t": [f[0] for f in fora],
                        "txt": f"legenda deslocada {abs(med):.0%} da largura para a {'direita' if med > 0 else 'esquerda'} no vídeo todo "
                               f"(mediana de {n_leg} quadros). Tirar \"legenda_margem_dir\"/\"x\" e mover a peça que a empurrou",
                        "caixas": [(f[0], f[1]) for f in fora[:6]]})
        elif len(fora_g) >= 6 and len(fora_g) >= .15 * n_leg:  # abaixo disso é o OCR lendo meia frase (Papagaio v6: 4 de 29)
            # a legenda nasce no eixo (mediana ~0): se só um pedaço dela se lê, o resto está tampado por uma peça
            # ("10 coisas" v1, reprovado: 11 de 52 quadros, cronômetro e numeral no meio da frase; v3: 0)
            ach.append({"grave": False, "tipo": "legenda tampada", "t": [f[0] for f in fora_g],
                        "txt": f"só um pedaço da legenda se lê em {len(fora_g)} de {n_leg} quadros (o que sobra fica até {max(abs(f[2]) for f in fora_g):.0%} fora do eixo): "
                               + ", ".join(_mmss(f[0]) for f in fora_g[:8]) + ". Alguma peça está por cima dela: ver a folha e rodar com --consertar",
                        "caixas": [(f[0], f[1]) for f in fora_g[:6]]})

    # 2. legenda sobre outro texto (peça nossa ou texto queimado na fonte)
    sobre = []
    for q in quadros:
        if "legenda" not in q:
            continue
        for b in q["caixas"]:
            if b["leg"]:
                continue
            if (b["caixa"][3] - b["caixa"][1]) < PEQUENO * H:  # HUD miúdo do jogo atrás da legenda não é disputa
                continue
            for l in (x for x in q["caixas"] if x["leg"]):
                f = _inter(l["caixa"], b["caixa"]) / max(min(_area(l["caixa"]), _area(b["caixa"])), 1)
                if f >= .10:
                    sobre.append((q["t"], b["caixa"], b["txt"], f))
                    break
    if len(sobre) >= MIN_QUADROS:
        nomes = []
        for s in sobre:
            if s[2] not in nomes:
                nomes.append(s[2])
        ach.append({"grave": len(sobre) >= 2 * MIN_QUADROS, "tipo": "legenda sobre outro texto", "t": [s[0] for s in sobre],
                    "txt": f"legenda encostada ou por cima de outro texto em {len(sobre)} quadros ("
                           + ", ".join(_mmss(s[0]) for s in sobre[:8]) + "): " + " | ".join(f'"{n[:24]}"' for n in nomes[:5])
                           + ". Rodar --consertar: a nossa legenda muda de altura ou de tamanho (o texto da fonte fica como está)",
                    "caixas": [(s[0], s[1]) for s in sobre[:6]]})

    # 2b. peça do plano (numeral, cronômetro, chip, painel, avatar) por cima da caixa REAL da legenda
    if ed:
        try:
            import ocupacao
            plano = [k for k in ocupacao.caixas(ed) if not ocupacao._e_legenda(k) and not k["estimada"]]
        except Exception as e:
            print(f"  (sem as caixas do plano: {e})")
            plano = []
        cima = {}
        for q in quadros:
            if "legenda" not in q:
                continue
            for k in plano:
                c = k["caixa"]
                if (c[2] - c[0]) >= .97 * W and (c[3] - c[1]) >= .5 * H:
                    continue
                if k["de"] + .15 <= q["t"] <= k["ate"] - .15:
                    f = _inter(q["legenda"], c) / max(_area(q["legenda"]), 1)
                    if f >= .20:  # 10 a 19% é sombra ou vizinhança ("10 coisas" v3: chip a 16%, sem tampar)
                        cima.setdefault(k["nome"], []).append((q["t"], tuple(c), f))
        for nome, itens in cima.items():
            if len(itens) >= 2:
                ach.append({"grave": True, "tipo": "peça em cima da legenda", "t": [i[0] for i in itens],
                            "txt": f"{nome} cobre até {max(i[2] for i in itens):.0%} da legenda lida no quadro, em {len(itens)} quadros ("
                                   + ", ".join(_mmss(i[0]) for i in itens[:8]) + "). Mover a peça para a faixa dela (topo ou meio-alto) ou rodar --consertar",
                            "caixas": [(i[0], i[1]) for i in itens[:4]]})

    # 3. texto nosso na borda (vertical): legenda e textos de peça reconhecidos
    def nosso(b, t):
        if b["leg"]:
            return "legenda"
        if b["fixo"]:  # selo "GTA 6" e título fixo ficam no canto de propósito; o ocupacao.py mede os dois no plano
            return None
        for p in pecas:
            if p["de"] - .2 <= t <= p["ate"] + .2:
                alvo = _norm(p["txt"])
                if len(b["n"]) >= 4 and ((b["n"] in alvo and len(b["n"]) >= .5 * len(alvo)) or _parecido(b["n"], alvo)):
                    return p["nome"]
        return None

    if vertical:
        borda = {}
        for q in quadros:
            for b in q["caixas"]:
                quem = nosso(b, q["t"])
                if not quem:
                    continue
                x0, y0, x1, y1 = b["caixa"]
                lado = min(x0, W - x1) / W
                if lado < LADO_MIN:
                    borda.setdefault((quem, "lateral"), []).append((q["t"], b["caixa"], lado, b["txt"]))
                if y0 / H < TOPO_MIN:
                    borda.setdefault((quem, "topo"), []).append((q["t"], b["caixa"], y0 / H, b["txt"]))
        for (quem, onde), itens in borda.items():
            minimo = 2 if quem != "legenda" else MIN_QUADROS
            if len(itens) < minimo:
                continue
            pior = min(i[2] for i in itens)
            ach.append({"grave": pior < (LADO_MIN if onde == "lateral" else TOPO_MIN) * .6, "tipo": "texto na borda",
                        "t": [i[0] for i in itens],
                        "txt": f"{quem} \"{itens[0][3][:26]}\": a {pior:.0%} d{'a lateral' if onde == 'lateral' else 'o topo'} em {len(itens)} quadros "
                               f"({_mmss(itens[0][0])}; mínimo {LADO_MIN if onde == 'lateral' else TOPO_MIN:.0%}, o celular corta). Diminuir ou afastar da borda",
                        "caixas": [(i[0], i[1]) for i in itens[:4]]})

    # 3b. texto da FONTE (legenda nativa do jogo, edição do canal de origem): não é defeito nosso nem dá para mexer.
    # Não vira alerta (JV, 19/11): fica anotado e entra como obstáculo no --consertar, para a nossa peça se adaptar.
    fonte = {}
    for q in quadros:
        for b in q["caixas"]:
            x0, y0, x1, y1 = b["caixa"]
            if b["leg"] or b["fixo"] or (y1 - y0) < PEQUENO * H or len(b["txt"].split()) < 2 or len(b["n"]) < 8:
                continue
            if nosso(b, q["t"]) is None:
                b["fonte"] = True
                fonte.setdefault(round((y0 + y1) / 2 / H, 1), []).append(q["t"])
    # uma linha só, e só para o que fica na tela de verdade (10+ quadros ou 25% do vídeo): é aviso de contexto, não alerta
    faixas = [f"{int(y * 100)}%" for y, ts in sorted(fonte.items()) if len(set(ts)) >= max(10, .25 * len(quadros))]
    info = [f"a fonte já traz texto próprio na altura {', '.join(faixas)} da tela (legenda do jogo ou do canal de origem). "
            "Faz parte do vídeo: não é defeito; o --consertar desvia a nossa legenda e as peças dele"] if faixas else []

    # 4. texto de peça que não se lê em nenhum quadro da janela
    for p in pecas:
        meio = [q for q in quadros if p["de"] + .5 <= q["t"] <= p["ate"] - .5]
        if len(meio) < 2:
            continue
        linhas = [_norm(x) for x in re.split(r"[/\n]", p["txt"].replace("*", "")) if len(_norm(x)) >= 4]
        for alvo in linhas:
            lido = any(_parecido(b["n"], alvo) or (len(b["n"]) >= 4 and b["n"] in alvo) for q in meio for b in q["caixas"])
            if not lido:
                ach.append({"grave": False, "tipo": "texto que não se lê", "t": [meio[0]["t"]],
                            "txt": f"{p['nome']} \"{p['txt'][:30]}\" ({_mmss(p['de'])}–{_mmss(p['ate'])}): o OCR não leu em nenhum dos "
                                   f"{len(meio)} quadros. Ver o quadro: tampado, pequeno, sem contraste ou fora da tela",
                            "caixas": [(meio[len(meio) // 2]["t"], None)]})
                break

    # 6. detalhe (imagem mole)
    dets = [(q["t"], q["detalhe"]) for q in quadros if q["detalhe"] is not None]
    det_med = float(np.median([d[1] for d in dets])) if dets else None
    moles = [d for d in dets if d[1] < DETALHE_MOLE]
    if dets and len(moles) >= max(MIN_QUADROS, .5 * len(dets)):
        ach.append({"grave": False, "tipo": "imagem mole", "t": [m[0] for m in moles],
                    "txt": f"pouco detalhe fino em {len(moles)} de {len(dets)} quadros (índice mediano {det_med:.2f}, mínimo {DETALHE_MOLE}): "
                           + ", ".join(_mmss(m[0]) for m in moles[:8]) + ". Fonte ampliada ou câmera lenta esticada: ver em resolução cheia",
                    "caixas": [(m[0], None) for m in moles[:4]]})

    resumo = {"info": info, "quadros": len(quadros), "com_legenda": n_leg, "detalhe_mediano": det_med, "W": W, "H": H, "dur": dur,
              "desvio_legenda": float(np.median([q["desvio"] for q in quadros if "desvio" in q])) if n_leg else None}
    return quadros, ach, resumo


def cenas_no_tempo(ed):
    """[(indice, t0, t1, camada da legenda ou None)]"""
    out, t = [], 0.0
    for i, cena in enumerate(ed.get("cenas") or []):
        fala = cena.get("fala") or {}
        dur = (fala["fim"] - fala["ini"]) if fala else float(cena.get("dur", 0))
        leg = next((c for c in cena.get("camadas", []) if c.get("tipo") == "texto" and c.get("estilo") in ("karaoke", "cartelas")), None)
        out.append((i, t, t + dur, leg))
        t += dur
    return out


CAMPOS_TAM = ("tam", "largura", "escala", "tamanho")  # o que encolhe cada tipo de peça no edicao.json
MOVE_CURTO = .08    # até aqui a legenda muda de altura sem ninguém notar; além disso tenta encolher antes
PECA_MIN = .70      # peça não encolhe abaixo de 70% do tamanho
LEG_MIN = .75       # legenda não encolhe abaixo de 75% do padrão (ilegível no celular)


def pecas_do_plano(ed, W, H):
    """Peças do edicao.json com a caixa medida e a referência à camada, para poder mexer nela.
    [{nome, de, ate, caixa, ref, cena}] (sem a legenda; contagem e leque entram por parte, sem campo de tamanho)"""
    import ocupacao
    fps, out, t = ed.get("fps", 30), [], 0.0
    for i, cena in enumerate(ed.get("cenas") or []):
        fala = cena.get("fala") or {}
        dur = (fala["fim"] - fala["ini"]) if fala else float(cena.get("dur", 0))
        for c in cena.get("camadas", []):
            if c.get("estilo") in ("karaoke", "cartelas"):
                continue
            caixa, _ = ocupacao.medir(c, W, H, fps)
            if caixa is not None:
                rot = str(c.get("texto") or c.get("rotulo") or Path(str(c.get("imagem", ""))).stem)[:24]
                out.append({"nome": f"{c.get('estilo', c['tipo'])} \"{rot}\"", "de": t + max(c.get("de", 0), 0),
                            "ate": t + min(c.get("ate", dur), dur), "caixa": tuple(caixa), "ref": c, "cena": i})
        t += dur
    for c in ed.get("sobre", []):
        if c.get("tipo") in ("contagem", "leque"):
            for nome, de, ate, caixa, _ in ocupacao._partes_kit(c, W, H, t):
                out.append({"nome": f"{c['tipo']}/{nome}", "de": de, "ate": ate, "caixa": tuple(caixa), "ref": None, "cena": -1})
            continue
        caixa, _ = ocupacao.medir(c, W, H, fps)
        if caixa is not None:
            rot = Path(str(c.get("imagem", ""))).stem or c["tipo"]
            out.append({"nome": f"{rot[:24]}", "de": c.get("de", 0), "ate": min(c.get("ate", t), t), "caixa": tuple(caixa), "ref": c, "cena": -1})
    return [k for k in out if not ((k["caixa"][2] - k["caixa"][0]) >= .97 * W and (k["caixa"][3] - k["caixa"][1]) >= .5 * H)]


def _encolher(k, ed, W, H, livre_de):
    """Menor redução (≥ PECA_MIN) do campo de tamanho da peça que a tira da faixa `livre_de` (y0, y1 em px). (campo, de, para) ou None.
    Mede de novo com o ocupacao.py a cada tentativa: não é conta de cabeça."""
    import ocupacao
    c = k["ref"]
    if c is None:
        return None
    campo = next((f for f in CAMPOS_TAM if isinstance(c.get(f), (int, float))), None)
    if campo is None:
        return None
    for f in (.9, .8, PECA_MIN):
        teste = dict(c, **{campo: c[campo] * f})
        caixa, _ = ocupacao.medir(teste, W, H, ed.get("fps", 30))
        if caixa is not None and not (caixa[1] < livre_de[1] and caixa[3] > livre_de[0]):
            return campo, c[campo], round(c[campo] * f, 4)
    return None


def propor(ed, quadros, ach, W, H):
    """Adapta o que é NOSSO em volta do que não muda. Por cena em que a legenda disputa lugar, tenta nesta ordem:
    1. mudar a altura da legenda até MOVE_CURTO; 2. encolher a peça que invade (≥ 70%); 3. encolher a legenda
    (≥ 75%) e reposicionar; 4. mudar a altura da legenda para onde houver faixa; 5. dizer qual peça tirar.
    Antes, no vídeo todo: legenda mais larga que a zona segura encolhe o "tam" em todas as cenas.
    Texto da fonte (legenda do jogo) é obstáculo fixo. [{onde, alvo, campo, de, para, motivo, ref}]"""
    import fx
    acoes = []
    cenas = cenas_no_tempo(ed)
    tam_padrao = getattr(fx, "KARAOKE_TAM", 104)

    # 0. legenda larga demais para a zona segura: encolhe no vídeo inteiro
    larg = [q["legenda"][2] - q["legenda"][0] for q in quadros if "legenda" in q]
    fator_global = 1.0
    if larg and H > W:
        pior = float(np.percentile(larg, 97))
        cabe = (1 - 2 * LADO_MIN) * W
        if pior > cabe:
            fator_global = max(round(cabe / pior - .01, 2), LEG_MIN)
            for i, t0, t1, leg in cenas:
                if leg is not None:
                    de = leg.get("tam", tam_padrao)
                    acoes.append({"onde": f"cena {i + 1}", "alvo": "legenda", "campo": "tam", "de": de, "para": round(de * fator_global),
                                  "motivo": f"a linha mais larga ocupa {pior / W:.0%} da tela (cabe {cabe / W:.0%})", "ref": leg, "global": True})

    ruins = sorted({t for a in ach if a["tipo"] in ("legenda sobre outro texto", "legenda tampada", "peça em cima da legenda") for t in a["t"]})
    if not ruins:
        return acoes
    try:
        plano = pecas_do_plano(ed, W, H)
    except Exception as e:
        print(f"  (sem as caixas do plano: {e})")
        plano = []
    alts = [(q["legenda"][3] - q["legenda"][1]) / H for q in quadros if "legenda" in q]
    meia0 = (float(np.percentile(alts, 90)) if alts else .09) / 2 * fator_global + .012
    x0, x1 = .11 * W, .89 * W
    for i, t0, t1, leg in cenas:
        if not any(t0 <= t < t1 for t in ruins):
            continue
        onde = f"cena {i + 1} ({_mmss(t0)}–{_mmss(t1)})"
        pecas = [k for k in plano if k["de"] < t1 - .15 and k["ate"] > t0 + .15 and k["caixa"][2] > x0 and k["caixa"][0] < x1]
        fixos = []  # texto lido que não é de peça nossa: fonte, HUD grande
        for q in quadros:
            if t0 <= q["t"] < t1:
                for b in q["caixas"]:
                    c = b["caixa"]
                    dentro = any(_inter(c, k["caixa"]) >= .5 * _area(c) for k in pecas)
                    if not b["leg"] and not dentro and (c[3] - c[1]) >= PEQUENO * H and c[2] > x0 and c[0] < x1:
                        fixos.append((c[1] / H, c[3] / H, f'texto da fonte "{b["txt"][:16]}"' if b.get("fonte") else f'"{b["txt"][:16]}"'))
        moveis = [(k["caixa"][1] / H, k["caixa"][3] / H, k["nome"], k) for k in pecas]
        if leg is None:
            nomes = ", ".join(dict.fromkeys(m[2] for m in moveis)) or "ver a folha"
            acoes.append({"onde": onde, "alvo": "peça", "campo": None, "motivo": f"legenda fixa (ASS): mover ou encolher {nomes}"})
            continue
        y = leg.get("y", .5)

        def choca(v, meia, lista):
            return [o for o in lista if o[0] < v + meia and o[1] > v - meia]

        def faixa_livre(meia, lista, alcance):
            cands = sorted((round(.30 + .01 * k, 2) for k in range(45)), key=lambda v: abs(v - y))
            return next((v for v in cands if abs(v - y) <= alcance and not choca(v, meia, lista)), None)

        tudo = fixos + [m[:3] for m in moveis]
        contra = list(dict.fromkeys(o[2] for o in choca(y, meia0, tudo)))
        if not contra:
            continue
        motivo = "disputa com " + ", ".join(contra[:4])
        # 1. mudança curta de altura
        v = faixa_livre(meia0, tudo, MOVE_CURTO)
        if v is not None:
            acoes.append({"onde": onde, "alvo": "legenda", "campo": "y", "de": y, "para": v, "motivo": motivo, "ref": leg})
            continue
        # 2. encolher a peça nossa que invade (se nada fixo estiver no caminho)
        if not choca(y, meia0, fixos):
            faixa = ((y - meia0) * H, (y + meia0) * H)
            feitos = []
            for m in choca(y, meia0, moveis):
                r = _encolher(m[3], ed, W, H, faixa)
                if r is None:
                    feitos = None
                    break
                feitos.append((m, r))
            if feitos:
                for m, (campo, de, para) in feitos:
                    acoes.append({"onde": onde, "alvo": m[2], "campo": campo, "de": de, "para": para,
                                  "motivo": "invadia a faixa da legenda; encolhida até sair", "ref": m[3]["ref"]})
                continue
        # 3. encolher a legenda e achar faixa
        achou = False
        for f in (.9, .8, LEG_MIN):
            v = faixa_livre(meia0 * f, tudo, .14)
            if v is not None:
                de = leg.get("tam", tam_padrao)
                acoes.append({"onde": onde, "alvo": "legenda", "campo": "tam", "de": de, "para": round(de * f),
                              "motivo": motivo + "; no tamanho padrão não há faixa livre por perto", "ref": leg})
                if abs(v - y) >= .01:
                    acoes.append({"onde": onde, "alvo": "legenda", "campo": "y", "de": y, "para": v, "motivo": "faixa livre com a legenda menor", "ref": leg})
                achou = True
                break
        if achou:
            continue
        # 4. qualquer faixa livre no tamanho padrão
        v = faixa_livre(meia0, tudo, 1)
        if v is not None:
            acoes.append({"onde": onde, "alvo": "legenda", "campo": "y", "de": y, "para": v, "motivo": motivo + " (mudança grande: olhar o quadro)", "ref": leg})
            continue
        # 5. não cabe: dizer o que tirar
        nomes = ", ".join(dict.fromkeys(m[2] for m in choca(y, meia0, moveis))) or ", ".join(contra[:4])
        acoes.append({"onde": onde, "alvo": "peça", "campo": None, "motivo": f"sem faixa livre nem encolhendo: tirar ou atrasar {nomes}"})
    return acoes


def folha(quadros, ach, destino, W, H):
    """Quadros acusados com as caixas desenhadas: vermelho = o defeito, amarelo = legenda, cinza = outro texto."""
    por_t = {}
    for a in ach:
        for item in a["caixas"]:
            por_t.setdefault(round(item[0], 2), []).append((a, item[1]))
    tiras = []
    for q in quadros:
        if round(q["t"], 2) not in por_t:
            continue
        m = q["mini"].copy()
        e = m.shape[0] / H
        for b in q["caixas"]:
            x0, y0, x1, y1 = (int(v * e) for v in b["caixa"])
            cv2.rectangle(m, (x0, y0), (x1, y1), (0, 220, 255) if b["leg"] else (150, 150, 150), 1)
        rot = []
        for a, caixa in por_t[round(q["t"], 2)]:
            if caixa:
                x0, y0, x1, y1 = (int(v * e) for v in caixa)
                cv2.rectangle(m, (x0, y0), (x1, y1), (0, 0, 255), 2)
            rot.append(a["tipo"])
        cv2.line(m, (m.shape[1] // 2, 0), (m.shape[1] // 2, m.shape[0]), (255, 255, 255), 1)
        barra = np.zeros((34, m.shape[1], 3), np.uint8)
        cv2.putText(barra, _mmss(q["t"]) + " " + _norm(rot[0])[:14].lower(), (4, 23), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1, cv2.LINE_AA)
        tiras.append(np.vstack([m, barra]))
        if len(tiras) >= 24:
            break
    if not tiras:
        return None
    col = 6
    larg, alt = tiras[0].shape[1], tiras[0].shape[0]
    linhas = []
    for i in range(0, len(tiras), col):
        grupo = tiras[i:i + col] + [np.zeros((alt, larg, 3), np.uint8)] * (col - len(tiras[i:i + col]))
        linhas.append(np.hstack(grupo))
    destino.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", np.vstack(linhas), [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tofile(str(destino))
    return destino


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video")
    ap.add_argument("--edicao")
    ap.add_argument("--passo", type=float, default=1.0)
    ap.add_argument("--ini", type=float, default=0.0, help="o vídeo é uma prévia que começa neste segundo do edicao.json (fx.py --ini)")
    ap.add_argument("--json", action="store_true", help="imprime só o JSON")
    ap.add_argument("--consertar", action="store_true", help="propõe o novo lugar da legenda nas cenas em que ela disputa espaço")
    ap.add_argument("--aplicar", action="store_true", help="com --consertar: grava no edicao.json (cópia em edicao.antes-layout.json)")
    a = ap.parse_args()
    video = Path(a.video)
    ped = Path(a.edicao) if a.edicao else video.parent / "edicao.json"
    ed = json.loads(ped.read_text(encoding="utf-8")) if ped.exists() else None
    quadros, ach, resumo = analisar(video, ed, a.passo, ped.parent, a.ini)
    conf = video.parent / "conferencia"
    arq = folha(quadros, ach, conf / f"layout-{video.stem}.jpg", resumo["W"], resumo["H"])
    saida = {"video": str(video), "resumo": resumo,
             "achados": [{"grave": k["grave"], "tipo": k["tipo"], "txt": k["txt"], "t": [round(t, 2) for t in k["t"]]} for k in ach]}
    conf.mkdir(parents=True, exist_ok=True)
    (conf / f"layout-{video.stem}.json").write_text(json.dumps(saida, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.json:
        print(json.dumps(saida, ensure_ascii=False))
    else:
        d = resumo["desvio_legenda"]
        print(f"layout de {video.name}: {resumo['quadros']} quadros, legenda lida em {resumo['com_legenda']}"
              + (f", desvio mediano do eixo {d:+.1%}" if d is not None else "")
              + (f", detalhe {resumo['detalhe_mediano']:.2f}" if resumo["detalhe_mediano"] is not None else "")
              + ("" if ed else " (sem edicao.json: só legenda, borda e detalhe)"))
        for k in ach:
            print(f"{'❌' if k['grave'] else '⚠️'} {k['tipo']}: {k['txt']}")
        for linha in resumo["info"]:
            print(f"ℹ️ {linha}")
        if not ach:
            print("✅ legenda no eixo, nenhum texto na borda nem sobre outro")
        if arq:
            print(f"folha: {arq}")
    if a.consertar and ed:
        acoes = propor(ed, quadros, ach, resumo["W"], resumo["H"])
        mudou, visto_global = False, False
        for ac in acoes:
            if ac.get("campo") is None:
                print(f"🔧 {ac['onde']}: {ac['motivo']}")
                continue
            if ac.get("global"):
                if not visto_global:
                    print(f"🔧 vídeo inteiro: legenda \"tam\" {ac['de']} → {ac['para']} em todas as cenas ({ac['motivo']})")
                    visto_global = True
            else:
                print(f"🔧 {ac['onde']}: {ac['alvo']} \"{ac['campo']}\" {ac['de']} → {ac['para']} ({ac['motivo']})")
            if a.aplicar and ac.get("ref") is not None:
                ac["ref"][ac["campo"]] = ac["para"]
                mudou = True
        if mudou:
            copia = ped.with_name(ped.stem + ".antes-layout.json")
            if not copia.exists():
                copia.write_text(ped.read_text(encoding="utf-8"), encoding="utf-8")
            ped.write_text(json.dumps(ed, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"gravado em {ped} (original em {copia.name}). Rodar o direcao.py, renderizar só essas cenas e medir de novo")
        elif not acoes:
            print("🔧 nada a ajustar: a legenda cabe e não disputa lugar")
    sys.exit(1 if any(k["grave"] for k in ach) else 0)


if __name__ == "__main__":
    main()
