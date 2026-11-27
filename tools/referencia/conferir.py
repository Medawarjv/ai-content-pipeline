"""Confere um vídeo pronto: mede o que dá para medir e monta as folhas para olhar o resto.

Uso:
    python tools/conferir.py saida/.../edicao.json [--video outro.mp4]
    python tools/conferir.py video.mp4      # sem edição: acha os cortes pela imagem

Saída em <pasta do vídeo>/conferencia/<nome>/: relatorio.md, cortes-NN.jpg (cada linha é um corte:
6 quadros em 1 s, mais a forma de onda) e quadros-NN.jpg (1 quadro/s do vídeo inteiro).
Sai com código 1 se houver ❌. ⚠️ pede olho humano e pode ser escolha da edição.
"""
import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
TAXA = 8000  # áudio mono para medir; basta para achar estalo e defasagem
LUFS_ALVO, LUFS_FOLGA, PICO_MAX = -14, 1.0, -1.5
SYNC_AVISO, SYNC_ERRO = 45, 100  # ms; 1 quadro a 30 fps são 33 ms
# faixa da legenda em fração da altura, tirada de FORMATOS no montador
FAIXA = {"horizontal": (0.83, 0.92), "vertical": (0.72, 0.775)}
CLARO = 175  # mediana de luminância a partir da qual a legenda branca sem barra pode sumir


def rodar(*args):
    return subprocess.run(args, capture_output=True, check=True)


def info(video):
    s = json.loads(rodar("ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)).stdout)
    v = next(x for x in s["streams"] if x["codec_type"] == "video")
    a = next((x for x in s["streams"] if x["codec_type"] == "audio"), None)
    num, den = v["r_frame_rate"].split("/")
    return {"w": v["width"], "h": v["height"], "fps": float(num) / float(den), "dur": float(s["format"]["duration"]),
            "dur_v": float(v.get("duration", 0)), "dur_a": float(a.get("duration", 0)) if a else 0}


def duracao(arq):
    return float(rodar("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(arq)).stdout)


def audio(arq, ini=0.0, dur=None):
    tempo = (["-ss", str(ini)] if ini else []) + (["-t", str(dur)] if dur else [])
    bruto = rodar("ffmpeg", "-v", "error", *tempo, "-i", str(arq), "-ac", "1", "-ar", str(TAXA),
                  "-f", "s16le", "-").stdout
    return np.frombuffer(bruto, np.int16).astype(np.float32) / 32768


def movimento(arq, ini, dur, corte=""):
    """Quanto a imagem muda de um quadro para o outro: resiste a recorte, legenda e fundo desfocado."""
    bruto = rodar("ffmpeg", "-v", "error", "-ss", str(ini), "-t", str(dur), "-i", str(arq),
                  "-vf", f"fps=30,{corte}scale=64:36,format=gray", "-f", "rawvideo", "-").stdout
    q = np.frombuffer(bruto, np.uint8).reshape(-1, 36, 64).astype(np.float32)
    return np.abs(np.diff(q, axis=0)).mean(axis=(1, 2)) if len(q) > 1 else np.zeros(1)


def defasagem(trecho, ref, margem):
    """Onde `trecho` aparece em `ref`, relativo a `margem`. Devolve (amostras, confiança de 0 a 1)."""
    if len(trecho) < 8 or len(ref) <= len(trecho) or trecho.std() < 1e-4:
        return 0, 0.0
    a, b = trecho - trecho.mean(), ref - ref.mean()
    n = len(a) + len(b)
    c = np.fft.irfft(np.fft.rfft(b, n) * np.conj(np.fft.rfft(a, n)), n)[:len(b) - len(a) + 1]
    energia = np.cumsum(np.concatenate(([0], b * b)))
    janela = energia[len(a):] - energia[:-len(a)]
    conf = c / np.sqrt(np.maximum(janela * (a * a).sum(), 1e-9))
    k = int(conf.argmax())
    return k - margem, float(conf[k])


def segmentos(edicao, video):
    """Trechos do montador com o tempo no vídeo final. Formatos sem `tipo` (detalhes.py) voltam vazios."""
    itens = edicao.get("itens", []) if edicao else []
    if not itens or "tipo" not in itens[0]:
        return []
    t, segs = 0.0, []
    for it in itens:
        if it["tipo"] == "cartela":
            d = float(it.get("dur", 2))
        elif "ini" in it:
            d = float(it["fim"]) - float(it["ini"])
        else:
            d = duracao(ROOT / it["arquivo"])
        segs.append({**it, "t": t, "d": d})
        t += d
    # cada emenda pode alongar um pouco o vídeo; sem isso, os cortes do fim saem da posição certa
    escala = info(video)["dur"] / t if t else 1
    return [{**s, "t": s["t"] * escala} for s in segs]


def cortes_pela_imagem(video):
    log = subprocess.run(["ffmpeg", "-i", str(video), "-vf", "scale=320:-2,select='gt(scene,0.35)',showinfo",
                          "-an", "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    return [float(x) for x in re.findall(r"pts_time:([\d.]+)", log)]


def loudness(video):
    log = subprocess.run(["ffmpeg", "-nostats", "-i", str(video), "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    resumo = log[log.rfind("Summary:"):]
    return float(re.search(r"I:\s+(-?[\d.]+) LUFS", resumo).group(1)), float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", resumo).group(1))


SILENCIO_DB, SILENCIO_MAX, SILENCIO_ABERTURA = -45, 2.0, 0.3


def audio_travado(video):
    """Áudio preso num valor constante (nível DC) ou com NaN: não é silêncio, então o trechos_mudos não vê.
    Veículos #2 (21/11): o loudnorm devolveu NaN no vão sem voz e o alimiter travou em -0,76 dos 29 s ao fim; o JV ouviu o som sumir."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
                       capture_output=True).stdout
    a = np.frombuffer(r, np.float32)
    if not len(a):
        return []
    if not np.isfinite(a).all():
        return [(0.0, len(a) / 8000, "NaN")]
    ruins, w = [], 8000
    for k in range(0, len(a) - w, w):
        seg = a[k:k + w]
        if abs(float(seg.mean())) > .25 and float(seg.std()) < .02:
            ruins.append(k / w)
    return [(t, t + 1, "nível constante") for t in ruins]


def trechos_mudos(video):
    """Trechos abaixo de -45 dB por 0,3 s ou mais. O LUFS integrado ignora silêncio (gate), então um minuto mudo
    passava com volume ✅ (JV, 18/11, Peanut v1: "os primeiros segundos tão sem som nenhum, e no meio o som some")."""
    log = subprocess.run(["ffmpeg", "-nostats", "-i", str(video), "-vn", "-af",
                          f"silencedetect=noise={SILENCIO_DB}dB:d={SILENCIO_ABERTURA}", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    ini = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", log)]
    fim = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    fim += [duracao(video)] * (len(ini) - len(fim))
    return list(zip([max(0.0, x) for x in ini], fim))


def trechos_pretos(video):
    log = subprocess.run(["ffmpeg", "-i", str(video), "-vf", "scale=320:-2,blackdetect=d=0.06:pix_th=0.08", "-an",
                          "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)]


def comeco_abafado(video, fim=None):
    """Som abafado na abertura (JV, 18/11, edit "A espera acabou": "não gostei de ter começado com a música abafada, o
    vídeo já deveria começar animado"). Mede a fatia de energia acima de 2 kHz nos 4 primeiros segundos contra a mediana
    do resto do vídeo (até `fim`, o começo do outro). Devolve (abertura, resto)."""
    sr = 22050
    s = np.frombuffer(rodar("ffmpeg", "-v", "error", "-i", str(video), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-").stdout,
                      np.int16).astype(np.float32)
    if fim:
        s = s[:int(fim * sr)]

    def agudo(x):
        e = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
        f = np.fft.rfftfreq(len(x), 1 / sr)
        return float(e[f > 2000].sum() / max(e.sum(), 1e-9))
    jan = [agudo(s[i:i + 2 * sr]) for i in range(0, len(s) - 2 * sr, 2 * sr)]
    if len(jan) < 6:
        return None, None
    return float(np.mean(jan[:2])), float(np.median(jan[2:]))


def clarao_no_fecho(video, fps, outro_ini, janela=4.0, salto=20):
    """Quadro ou plano mais claro entrando no respiro antes do outro (JV, 18/11: "tem um frame perdido esbranquiçado;
    acho bom ter um respiro entre a logo e o outro, mas não com esse frame"). No fecho a imagem só pode escurecer:
    devolve os instantes, nos `janela` s antes do outro, em que o brilho médio sobe mais de `salto` de um quadro para o
    seguinte (escala 0–255)."""
    ini = max(0.0, outro_ini - janela)
    bruto = rodar("ffmpeg", "-v", "error", "-ss", f"{ini:.3f}", "-t", f"{outro_ini - ini:.3f}", "-i", str(video),
                  "-vf", "scale=64:114,format=gray", "-f", "rawvideo", "-").stdout
    y = np.frombuffer(bruto, np.uint8)
    y = y[:len(y) // (64 * 114) * 64 * 114].reshape(-1, 64 * 114).mean(1)
    return [ini + (i + 1) / fps for i in np.where(np.diff(y) > salto)[0]]


def estalos(som, cortes):
    achados = []
    for t in cortes:
        c = int(t * TAXA)
        perto = som[max(c - 120, 0):c + 120]
        volta = som[max(c - 2400, 0):c + 2400]
        if len(perto) == 0 or len(volta) == 0:
            continue
        if np.abs(perto).max() > 0.25 and np.abs(perto).max() > 5 * np.sqrt((volta * volta).mean() + 1e-9):
            achados.append(t)
    return achados


FIM_DE_FRASE = (".", "!", "?", "…")


def palavras_cortadas(segs):
    """(trecho, ponta, tempo no vídeo, palavra, ms perdidos, grave). Grave = o fim do trecho de payoff, fechamento ou do
    último clipe come a palavra ou deixa a frase pela metade (12/11, tweets: "o segundo vídeo cortou antes do payoff")."""
    achados = []
    clipes = [i for i, s in enumerate(segs, 1) if s.get("tipo") == "clipe" and "ini" in s]
    for i, s in enumerate(segs, 1):
        if s.get("tipo") != "clipe" or "ini" not in s:
            continue
        pronto = (ROOT / s["arquivo"]).with_suffix(".palavras.json")
        if not pronto.exists():
            continue
        decisivo = (s.get("beat") or {}).get("funcao") in ("payoff", "fechamento") or i == clipes[-1]
        palavras = json.loads(pronto.read_text(encoding="utf-8"))
        fim = float(s["fim"])
        # trecho que continua a mesma fonte no seguinte (ou vem do anterior) não é corte de fala: é emenda do mesmo take
        ant, seg = (segs[i - 2] if i > 1 else {}), (segs[i] if i < len(segs) else {})
        emenda_ini = ant.get("arquivo") == s["arquivo"] and "fim" in ant and abs(float(ant["fim"]) - float(s["ini"])) < .15
        emenda_fim = seg.get("arquivo") == s["arquivo"] and "ini" in seg and abs(float(seg["ini"]) - fim) < .15
        if emenda_fim:
            decisivo = False
        pontas = [p for p, e in ((("início", float(s["ini"])), emenda_ini), (("fim", fim), emenda_fim)) if not e]
        for w in palavras:
            for nome, ponta in pontas:
                # o tempo do Whisper oscila nas pontas da palavra: só conta perda de 120 ms ou mais
                perde = ponta - w["i"] if nome == "início" else w["f"] - ponta
                if w["i"] < ponta < w["f"] and perde >= 0.12:
                    achados.append((i, nome, s["t"] if nome == "início" else s["t"] + s["d"], w["p"], perde,
                                    decisivo and nome == "fim"))
        if decisivo:
            antes = [w for w in palavras if w["f"] <= fim + 0.05 and w["i"] < fim]
            depois = [w for w in palavras if w["i"] >= fim]
            if antes and depois and depois[0]["i"] - fim < 0.3 and not antes[-1]["p"].rstrip("\"'”»)").endswith(FIM_DE_FRASE):
                achados.append((i, "frase", s["t"] + s["d"], f"{antes[-1]['p']} | {depois[0]['p']}", 0, True))
    return achados


def medir_sync(segs, video):
    """Compara som e imagem de cada trecho com a fonte. Sem isso, dessincronia só aparece assistindo."""
    linhas = []
    for i, s in enumerate(segs, 1):
        if s.get("tipo") != "clipe" or "ini" not in s or s["d"] < 2 or s.get("cima") or s.get("tela_cheia"):
            continue  # com "cima", a imagem é outra: sem boca para comparar
        janela = min(s["d"] - 1, 5)
        meio = (s["d"] - janela) / 2
        ref = float(s["ini"]) + meio - 0.5
        if ref < 0:
            continue
        a, a_conf = defasagem(audio(video, s["t"] + meio, janela), audio(ROOT / s["arquivo"], ref, janela + 1), TAXA // 2)
        corte_final, corte_fonte = "", ""
        if s.get("baixo"):
            # Short empilhado: a metade de baixo não existe na fonte, então compara só a de cima, sem o texto da emenda
            x, y, w, h = s["recorte"]
            corte_final = "crop=iw:ih*0.36:0:0,"
            corte_fonte = f"crop=iw*{w}:ih*{h * 0.72}:iw*{x}:ih*{y},"
        v, v_conf = defasagem(movimento(video, s["t"] + meio, janela, corte_final),
                              movimento(ROOT / s["arquivo"], ref, janela + 1, corte_fonte), 15)
        if a_conf < 0.5 or v_conf < 0.5:  # imagem parada ou som muito mudado: a medida não vale
            linhas.append({"i": i, "t": s["t"], "ok": False})
        else:
            linhas.append({"i": i, "t": s["t"], "ok": True, "som": a * 1000 / TAXA, "img": v * 1000 / 30})
    return linhas


def folha_cortes(video, cortes, som, pasta, vertical):
    if not cortes:
        return []
    alt = 200
    arquivos, linhas = [], []
    tmp = pasta / "_q.jpg"
    for t in cortes:
        rodar("ffmpeg", "-y", "-v", "error", "-ss", f"{max(t - 0.5, 0):.3f}", "-t", "1", "-i", str(video),
              "-vf", f"fps=6,scale=-2:{alt},tile=6x1", "-frames:v", "1", "-update", "1", str(tmp))
        with Image.open(tmp) as q:
            linhas.append((t, q.convert("RGB")))
    tmp.unlink()
    onda_l = 300
    larg = linhas[0][1].width + onda_l + 90
    por_folha = 20 if not vertical else 10
    for n in range(0, len(linhas), por_folha):
        grupo = linhas[n:n + por_folha]
        folha = Image.new("RGB", (larg, (alt + 8) * len(grupo)), "#111")
        d = ImageDraw.Draw(folha)
        for k, (t, q) in enumerate(grupo):
            y = k * (alt + 8)
            d.text((6, y + alt // 2), f"{t:7.2f}s", fill="white")
            folha.paste(q, (90, y))
            # forma de onda de 1 s, com o corte marcado no meio
            c = int(t * TAXA)
            pedaco = som[max(c - TAXA // 2, 0):c + TAXA // 2]
            x0 = 90 + q.width
            for x in range(onda_l):
                faixa = pedaco[int(x * len(pedaco) / onda_l):int((x + 1) * len(pedaco) / onda_l)]
                if len(faixa):
                    h = int(min(float(np.abs(faixa).max()), 1) * alt / 2)
                    d.line((x0 + x, y + alt // 2 - h, x0 + x, y + alt // 2 + h), fill="#6cf")
            d.line((x0 + onda_l // 2, y, x0 + onda_l // 2, y + alt), fill="#f55")
        destino = pasta / f"cortes-{n // por_folha + 1:02}.jpg"
        folha.save(destino, quality=85)
        arquivos.append(destino)
    return arquivos


OUTRO_RECUO = (1.6, 1.2, 0.8)  # segundos antes do fim: depois de 4,2 s o outro escurece e qualquer um parece igual
# diferença média (0 a 1) do fim do vídeo para o outro atual, calibrada em 15/11 em 10 vídeos: com o outro atual 0,002;
# com um outro anterior (mesmo layout, outra arte) 0,077 a 0,081; sem outro (peça termina no conteúdo) 0,22 a 0,23
OUTRO_ATUAL, OUTRO_ANTIGO = 0.03, 0.15
# 17/11: outro novo (OutroNovo do Remotion, fundo B = Vice City fixo depois de 0,5 s, então o fim compara igual entre vídeos).
# Quem bate num ANTIGO (o Foragido de 15/11 ou o do Radar) é ❌, mesmo com diferença baixa.
OUTRO_ATUAIS = lambda f: [f"{f}-foragido-v2.mp4", f"{f}-foragido-v2-sem-voz.mp4"]
OUTRO_ANTIGOS = lambda f: [f"{f}-foragido.mp4", f"{f}-foragido-sem-voz.mp4", f"{f}.mp4", f"{f}-sem-voz.mp4"]
OUTRO_VOZ_MIN = 0.9  # calibrar: correlação do volume da fala do fim com a do outro atual


def _quadro_pequeno(arq, t):
    bruto = rodar("ffmpeg", "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", str(arq), "-frames:v", "1",
                  "-vf", "scale=72:128,format=gray", "-f", "rawvideo", "-").stdout
    return np.frombuffer(bruto, np.uint8).astype(np.float32) / 255 if len(bruto) == 72 * 128 else None


def outro_no_fim(video, inf):
    """Compara o fim do vídeo com o outro atual (marca/outro/short|longo[-sem-voz].mp4). Devolve (diferença, outro mais
    parecido). Origem: 09/11, o final.mp4 da caixa saiu com o outro antigo (radar verde) e ninguém olhou o último quadro."""
    forma = "short" if inf["h"] > inf["w"] else "longo"
    melhor = (9.0, None)
    for nome in OUTRO_ATUAIS(forma) + OUTRO_ANTIGOS(forma):
        o = ROOT / "marca" / "outro" / nome
        if not o.exists():
            continue
        d_o, difs = duracao(o), []
        for r in OUTRO_RECUO:
            a, b = _quadro_pequeno(video, inf["dur"] - r), _quadro_pequeno(o, d_o - r)
            if a is not None and b is not None:
                difs.append(float(np.abs(a - b).mean()))
        if difs and np.mean(difs) < melhor[0]:
            melhor = (float(np.mean(difs)), nome)
    return melhor


def _envelope(arq, ini, dur):
    """Volume em quadros de 20 ms (mono, 8 kHz), para comparar a fala de dois áudios."""
    bruto = rodar("ffmpeg", "-v", "error", "-ss", f"{max(ini, 0):.3f}", "-t", f"{dur:.3f}", "-i", str(arq), "-vn",
                  "-ac", "1", "-ar", "8000", "-f", "s16le", "-").stdout
    x = np.frombuffer(bruto, np.int16).astype(np.float32) / 32768
    q = len(x) // 160
    return np.sqrt((x[:q * 160].reshape(q, 160) ** 2).mean(axis=1)) if q else np.zeros(0)


def voz_do_outro(video, inf, qual):
    """Correlação do volume da fala no fim do vídeo com a do outro atual: a arte pode ser a mesma com a voz antiga
    (15/11: o CTA no v2 foi trocado pelo v3 com o mesmo visual). Devolve a correlação (1 = mesma fala)."""
    o = ROOT / "marca" / "outro" / qual
    d_o = duracao(o)
    a, b = _envelope(video, inf["dur"] - d_o, d_o), _envelope(o, 0, d_o)
    n = min(len(a), len(b)) - 10
    if n < 50 or a.std() < 1e-4 or b.std() < 1e-4:
        return None
    # melhor alinhamento em ±200 ms (recodificar o áudio, como no volume.py, desloca alguns ms)
    return max(float(np.corrcoef(a[5 + k:5 + k + n], b[5:5 + n])[0, 1]) for k in range(-5, 6))


def legenda_clara(video, segs, inf):
    forma = "vertical" if inf["h"] > inf["w"] else "horizontal"
    y0, y1 = FAIXA[forma]
    h, w = max(int((y1 - y0) * inf["h"] / 4), 2), inf["w"] // 4
    achados = []
    for s in segs:
        if not (s.get("legenda") or s.get("falas")):
            continue
        bruto = rodar("ffmpeg", "-v", "error", "-ss", str(s["t"]), "-t", str(s["d"]), "-i", str(video), "-vf",
                      f"fps=2,crop=iw:ih*{y1 - y0:.3f}:0:ih*{y0:.3f},scale={w}:{h},format=gray",
                      "-f", "rawvideo", "-").stdout
        q = np.frombuffer(bruto, np.uint8)
        q = q[:len(q) - len(q) % (w * h)].reshape(-1, h, w)
        claros = np.median(q, axis=(1, 2)) > CLARO
        # só avisa com 1 s seguido: um clarão rápido a legenda aguenta
        if len(claros) > 1 and (claros[:-1] & claros[1:]).any():
            k = int(np.argmax(claros[:-1] & claros[1:]))
            achados.append(s["t"] + k / 2)
    return achados


TRECHO_MIN = 4.0  # s: trecho de música solto mais curto que isso "aparece e some" (JV, 25/11, Veículos #5 e #6: 1,4 e 1,8 s)
SFX_MAX_VOZ = -18  # dB do efeito contra o p90 da voz: contador de cassino a -16 "muito alto", carimbos a -22 ok (JV, 25/11, Esquema #22)
NIVEL_CAMA_MAX = -22  # LUFS do `nivel` da trilha sob a voz: -22 aprovado várias vezes (Segredos #10, Esquema #18), -21 reprovado (JV, 25/11, Esquema #20)


def _fala_na_trilha(arq, ini, dur):
    """Fala ou canto gravado na faixa, no trecho que o vídeo usa: [(t0, t1, texto)] no tempo do vídeo.
    Cache em fontes/_cache/fala-trilha.json (o Whisper na CPU leva ~20 s por minuto de faixa)."""
    cache_arq = ROOT / "fontes" / "_cache" / "fala-trilha.json"
    chave = f"{Path(arq).resolve()}|{Path(arq).stat().st_mtime:.0f}|{ini:.2f}|{dur:.1f}"
    try:
        cache = json.loads(cache_arq.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if chave in cache:
        return [tuple(x) for x in cache[chave]]
    from faster_whisper import WhisperModel  # CPU: a cuBLAS do CUDA 12 não está no PATH (25/11)
    a16 = rodar("ffmpeg", "-v", "error", "-ss", str(ini), "-t", str(dur), "-i", str(arq), "-ac", "1", "-ar", "16000",
                "-f", "s16le", "-").stdout
    sinal = np.frombuffer(a16, np.int16).astype(np.float32) / 32768
    achados = []
    if len(sinal):
        modelo = WhisperModel("small", device="cpu", compute_type="int8")
        segs, _ = modelo.transcribe(sinal, vad_filter=True)
        achados = [(round(s.start, 1), round(s.end, 1), s.text.strip()) for s in segs
                   if s.no_speech_prob < .5 and s.avg_logprob > -1.0 and len(s.text.split()) >= 2]
    cache[chave] = achados
    cache_arq.parent.mkdir(parents=True, exist_ok=True)
    cache_arq.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return achados


FX_SEM_REPETIR = 1795628700  # 25/11/2026 14:45 (São Paulo): dali em diante o fx.py não repete a trilha curta


def trechos_de_musica(mus, dur, taxa=TAXA, limiar=-60, emenda=.5):
    """Trechos contínuos em que a trilha soa (janelas de 0,1 s acima de `limiar` dB, buracos até `emenda` s emendados).
    -60 e não -45: a introdução baixinha da Pantera Cor-de-Rosa (Segredos #9) contava como buraco."""
    j = int(.1 * taxa)
    n = min(len(mus), int(dur * taxa)) // j
    if not n:
        return []
    r = 20 * np.log10(np.sqrt((mus[:n * j].reshape(n, j) ** 2).mean(1)) + 1e-9) > limiar
    trechos, i = [], 0
    while i < n:
        if r[i]:
            k = i
            while k < n and r[k]:
                k += 1
            if trechos and i / 10 - trechos[-1][1] <= emenda:
                trechos[-1] = (trechos[-1][0], k / 10)
            else:
                trechos.append((i / 10, k / 10))
            i = k
        else:
            i += 1
    return trechos


def trilha_sob_voz(ed, voz, dur, video=None):
    render_antigo = video is None or Path(video).stat().st_mtime < FX_SEM_REPETIR
    """A trilha contra o que o JV reprovou em 25/11 (Lote 1): (1) Esquema #20, a faixa do Miami Vice tinha diálogo da
    série gravado e ele saiu por cima da narração; (2) Esquema #20, cama a -21, "muito alta" (aprovados a -24 e -28,4);
    (3) Esquema #21, a narração parou para a música entrar num vão: a música vai baixinha por baixo da voz.
    `voz` = trechos com fala no tempo do edicao.json. Devolve [(selo, texto)]."""
    m = ed["musica"]
    arq = ROOT / m["arquivo"]
    if not arq.exists() or not voz:
        return []
    ini = float(m.get("ini", 0))
    mus = audio(arq, ini, dur)
    if not len(mus):
        return []
    dur_faixa = len(mus) / TAXA
    jan = int(.1 * TAXA)
    notas = []
    # o fx.py toca a trilha do instante 0 do vídeo e REPETE o arquivo até o fim: sting curto (Veículos #5, 16,6 s com o
    # riff no 0) caiu no 0, 16,6 e 33,2 s por cima da voz e o vão dele ficou em silêncio total (JV, 25/11: "o som some
    # por 1 segundo"); cama mais curta que o vídeo recomeça do início no fecho (Veículos #7). Arquivo de sting leva o
    # silêncio na frente até o ponto certo e dura o vídeo inteiro (molde: Esquema #21, musica-grito-money.wav)
    em = float(m.get("em", 0))
    if render_antigo and dur_faixa < dur - .3:
        _cab = mus[:int(2 * TAXA)]
        if len(_cab) and 20 * np.log10(np.sqrt(np.mean(_cab ** 2)) + 1e-9) > -50:
            voltas = [round(dur_faixa * k, 1) for k in range(1, int(dur / dur_faixa) + 1) if dur_faixa * k < dur - .3]
            notas.append(("❌", f"A trilha tem {dur_faixa:.1f} s e o vídeo {dur:.1f} s: o fx.py toca do 0 e repete o arquivo, então a"
                          f" música volta do começo em {', '.join(f'{v:g}' for v in voltas)} s (JV, 25/11, Veículos #5 e #6: \"o som"
                          " some\", o riff fora do lugar). Montar o arquivo com silêncio na frente até o ponto da entrada e"
                          " com a duração do vídeo"))
    if render_antigo:
        while len(mus) < int(dur * TAXA):  # até 25/11 14h45 o fx.py repetia a faixa até o fim
            mus = np.concatenate([mus, mus])
    else:  # agora: entra em `em` e acaba em silêncio
        mus = np.concatenate([np.zeros(int(em * TAXA), np.float32), mus, np.zeros(max(0, int(dur * TAXA)), np.float32)])

    def ativo(a, b):
        seg = mus[int(a * TAXA):int(b * TAXA)]
        n = len(seg) // jan
        if not n:
            return 0.0
        r = 20 * np.log10(np.sqrt((seg[:n * jan].reshape(n, jan) ** 2).mean(1)) + 1e-9)
        return float((r > -45).mean())

    # música não aparece e some em ~2 s: "pra ela entrar 2 segs só e sair não faz o menor sentido, isso aí é só efeito
    # sonoro" (JV, 25/11, Veículos #5 e #6). A música não precisa cobrir o vídeo inteiro ("não precisa tocar 70%"):
    # o que reprova é trecho de música solto mais curto que TRECHO_MIN
    curtos = [(a, b) for a, b in trechos_de_musica(mus, dur) if b - a < TRECHO_MIN]
    if curtos:
        notas.append(("❌", "Música que aparece e some: " + ", ".join(f"{a:.1f}-{b:.1f} s ({b - a:.1f} s)" for a, b in curtos)
                      + f" (menos de {TRECHO_MIN:g} s; JV, 25/11: \"só não é pra ela aparecer e sumir em 2 segundos se não"
                      " for um efeito sonoro\"). Preferir cama de fundo baixa que sobe no destaque (`\"sobe\"` no bloco"
                      " musica); trecho curto de propósito vai como efeito, em `sons`"))
    tot = sum(b - a for a, b in voz)
    sob = sum(ativo(a, b) * (b - a) for a, b in voz) / tot
    nivel = float(m.get("nivel", -18))
    # vão aberto na narração só para a música (o editor guarda o original em narracao.sem-vao.wav); vão natural da
    # história com a música nele (Veículos #4, decolagem nas Valquírias) foi aprovado e não conta
    falas_arq = [c["fala"]["arquivo"] for c in ed["cenas"] if c.get("fala")]
    # sinais do vão aberto pelo editor: narracao.sem-vao.wav (Esquema #21), narracao.antes-gap.* (Veículos #5),
    # narração montada num arquivo *vao*.wav (Esquema #22, arte/narracao-vao.wav)
    _pastas = {(ROOT / f).parent for f in falas_arq} | {(ROOT / f).parent.parent for f in falas_arq}
    vao_inserido = bool(falas_arq) and (any(Path(f).stem.endswith(("-vao", "_vao", "-gap")) for f in falas_arq)
                                        or any(p.joinpath("narracao.sem-vao.wav").exists() or any(p.glob("narracao.antes-gap.*"))
                                               for p in _pastas))
    if sob < .2:
        if vao_inserido:
            notas.append(("⚠️", f"Vão aberto na narração para a música entrar ({sob:.0%} dela sob a fala; a pasta guarda a narração sem o vão):"
                          " pode soar como a narração parando para a música (JV, 25/11, Esquema #21 reprovado; Veículos #4 aprovado). Preferir a narração sem o vão e"
                          " a música baixinha por baixo da voz"))
    elif nivel > NIVEL_CAMA_MAX:
        # ⚠️, não ❌: a -21 o Segredos #6 foi aprovado ("adorei a música") e o Esquema #20 reprovado ("muito alta"); o
        # nível sozinho não separa os dois (faixa densa e com diálogo no #20), então é para o ouvido do JV
        notas.append(("⚠️", f"Cama de música a {nivel:g} LUFS sob a voz, acima do padrão -24 (JV, 25/11: a -21 o Esquema #20 ficou"
                      " \"muito alta\" e o Segredos #6 foi aprovado). Faixa densa ou com batida forte: baixar para -24"))
    else:
        notas.append(("✅", f"Cama de música a {nivel:g} LUFS sob a voz (limite {NIVEL_CAMA_MAX})"))
    if sob >= .2:
        fim_voz = max(b for _, b in voz)
        try:
            falas = _fala_na_trilha(arq, ini, min(fim_voz, dur))
        except Exception as e:  # Whisper ausente não derruba a conferência
            notas.append(("⚠️", f"Não deu para procurar fala gravada na trilha ({e.__class__.__name__}): ouvir o começo"))
            falas = None
        sob_voz = [f for f in (falas or []) if any(a < f[1] and f[0] < b for a, b in voz)]
        if sob_voz:
            notas.append(("❌", "A faixa da música tem fala ou canto gravado por baixo da narração: "
                          + "; ".join(f"{a:.1f}-{b:.1f} s \"{t[:50]}\"" for a, b, t in sob_voz[:4])
                          + " (JV, 25/11, Esquema #20: diálogo do Miami Vice por cima da voz). Mudar o `ini` da faixa para"
                          " pular o trecho falado, ou usar a versão instrumental"))
        elif falas is not None:
            notas.append(("✅", "Nenhuma fala gravada na trilha por baixo da narração"))
    return notas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("alvo")
    ap.add_argument("--video")
    args = ap.parse_args()
    alvo = Path(args.alvo)
    edicao = None
    if alvo.suffix == ".json":
        edicao = json.loads(alvo.read_text(encoding="utf-8"))
        video = Path(args.video) if args.video else ROOT / edicao["saida"]
    else:
        video = alvo
    pasta = video.parent / "conferencia" / video.stem
    pasta.mkdir(parents=True, exist_ok=True)

    inf = info(video)
    vertical = inf["h"] > inf["w"]
    segs = segmentos(edicao, video)
    itens = edicao.get("itens", []) if edicao else []
    if segs:
        cortes = [s["t"] for s in segs[1:]]
    elif itens and "a" in itens[0]:
        # comparar.py: a tela dividida engana a detecção pela imagem, mas cada item tem duração certa
        cortes = [float(x) for x in np.cumsum([it.get("dur", 6) for it in itens])[:-1]]
    else:
        cortes = cortes_pela_imagem(video)
    som = audio(video)
    linhas, erros = [], 0

    def nota(ok, texto):
        nonlocal erros
        erros += ok == "❌"
        linhas.append(f"- {ok} {texto}")

    # 1440x2560 é o Short do fx.py; 1080x1920, o do montador e do Remotion
    certos = {(1080, 1920), (1440, 2560)} if vertical else {(1920, 1080), (2560, 1440), (3840, 2160)}
    nota("✅" if (inf["w"], inf["h"]) in certos else "⚠️", f"Quadro {inf['w']}x{inf['h']} a {inf['fps']:.2f} fps, {inf['dur']:.1f} s, {len(cortes)} cortes")
    if edicao and not vertical:
        # JV, 19/11: faixa que já tomou Content ID no YouTube fica fora dos longos (Shorts seguem livres)
        import unicodedata
        def _norm(s):
            return unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode()
        lista = ROOT / "aprendizados" / "musica" / "reivindicadas.json"
        texto_ed = _norm(json.dumps(edicao, ensure_ascii=False))
        achadas = [f["musica"] for f in json.loads(lista.read_text(encoding="utf-8"))["faixas"]
                   if any(_norm(t) in texto_ed for t in f["termos"])] if lista.exists() else []
        nota("❌" if achadas else "✅", ("Longo com música já reivindicada no YouTube: " + ", ".join(achadas) +
             " (trocar a faixa; lista em aprendizados/musica/reivindicadas.json)") if achadas
             else "Nenhuma música já reivindicada no YouTube (reivindicadas.json)")
    if edicao and edicao.get("fps") and inf["fps"] < float(edicao["fps"]) - 1:
        # 18/11: o prólogo renderizou a 60 fps e o outro.py devolveu 30; o JV reprovou a qualidade geral (2ª vez)
        nota("❌", f"O vídeo final tem {inf['fps']:.0f} fps e a edição foi renderizada a {float(edicao['fps']):.0f}: perdeu quadros no caminho "
                   "(outro, selo ou exportação). O movimento perde fluidez; refazer a junção mantendo o fps do corpo.")
    if edicao and edicao.get("resolucao"):
        # prévia em meia escala esconde fonte fraca: o JV reprovou a do "19 coisas" por isso (13/11)
        pedida = tuple(edicao["resolucao"])
        if inf["w"] < pedida[0] or inf["h"] < pedida[1]:
            nota("❌", f"Vídeo em {inf['w']}x{inf['h']}, menor que a edição pede ({pedida[0]}x{pedida[1]}): prévia em escala reduzida"
                       " esconde fonte fraca. Renderizar em resolução cheia antes de mostrar ao JV.")
        import resolucao  # piso de 1080p em toda fonte e peça esticada; o direcao.py já barra antes do render
        p_res, a_res = resolucao.checar(edicao)
        for p in p_res:
            nota("❌", p)
        for a in a_res:
            nota("⚠️", a)
        if not p_res and not a_res:
            nota("✅", "Todas as fontes em 1080p ou mais, sem peça esticada")
    i, pico = loudness(video)
    conserto = f" Corrigir sem recomprimir a imagem: `python tools/volume.py normalizar {video.name}`."
    nota("✅" if abs(i - LUFS_ALVO) <= LUFS_FOLGA else "❌", f"Volume {i:.1f} LUFS (alvo {LUFS_ALVO} ±{LUFS_FOLGA:.0f})"
         + ("" if abs(i - LUFS_ALVO) <= LUFS_FOLGA else conserto))
    nota("✅" if pico < PICO_MAX else "❌", f"Pico {pico:.1f} dB (máximo {PICO_MAX})" + ("" if pico < PICO_MAX else conserto))
    # o vídeo sempre começa com som (gancho) e nunca fica mudo no meio (JV, 18/11, Peanut v1)
    mudos = trechos_mudos(video)
    abertura = [(a, b) for a, b in mudos if a < 0.25]
    longos = [(a, b) for a, b in mudos if b - a > SILENCIO_MAX and a >= 0.25]
    if abertura:
        nota("❌", f"Começa mudo: sem som de 0 a {abertura[0][1]:.1f} s. O vídeo sempre abre com som, faz parte do gancho"
                   " (fala, som da cena ou efeito no 1º quadro)")
    if longos:
        nota("❌", "Som some no meio: " + ", ".join(f"{a:.1f}-{b:.1f} s" for a, b in longos)
             + f" (mais de {SILENCIO_MAX:.0f} s abaixo de {SILENCIO_DB} dB). Conferir se a fonte tem áudio em todo o trecho")
    travado = audio_travado(video)
    if travado:
        nota("❌", f"Áudio TRAVADO (não é silêncio, é valor constante ou NaN) a partir de {travado[0][0]:.0f} s: {len(travado)} s afetados."
                   " Causa conhecida: loudnorm devolve NaN em silêncio puro (vão sem voz) e o alimiter final trava; o fx.py já limpa o NaN da fala, mas conferir fonte e efeitos")
    # silêncio TOTAL (zero digital) no meio, mesmo curto: em vídeo narrado soa como bug, não como pausa
    # (JV, 25/11, Veículos #5 37,7-38,8 s e #6 32,9-33,6 s: "o som some por 1 segundo", "som tá bugando")
    _j = int(.1 * TAXA)
    _n = len(som) // _j
    _zero = (20 * np.log10(np.sqrt((som[:_n * _j].reshape(_n, _j) ** 2).mean(1)) + 1e-9) < -90) if _n else np.zeros(0, bool)
    buracos, _i = [], 0
    while _i < len(_zero):
        if _zero[_i]:
            _k = _i
            while _k < len(_zero) and _zero[_k]:
                _k += 1
            if _k - _i >= 4 and _i > 3 and _k < len(_zero) - 10:  # 0,4 s ou mais, fora da borda e do último segundo
                buracos.append((_i / 10, _k / 10))
            _i = _k
        else:
            _i += 1
    if buracos:
        nota("❌", "Silêncio total no meio do vídeo: " + ", ".join(f"{a:.1f}-{b:.1f} s" for a, b in buracos)
             + " (JV, 25/11, Veículos #5 e #6: \"o som some por 1 segundo\"). Vão sem voz leva som: a música, o som do jogo"
             " ou um ambiente baixo; se é a trilha de sting, conferir se ela cai mesmo no vão (o fx.py repete o arquivo do 0)")
    if not abertura and not longos and not travado and not buracos:
        nota("✅", f"Som do 1º quadro ao fim, sem buraco maior que {SILENCIO_MAX:.0f} s")
    # trilha no vão sem voz (sting) tão alta quanto a voz: o loudnorm do fx.py normaliza a trilha quase toda muda
    # e o trecho que sobra sai no nível da fala (JV, 20/11, arma perto de casa: "a música no meio entrou muito alta")
    _ed_fx = edicao if edicao and edicao.get("cenas") else None
    if not _ed_fx and (video.parent / "edicao.json").exists():
        try:
            _ed_fx = json.loads((video.parent / "edicao.json").read_text(encoding="utf-8"))
        except ValueError:
            _ed_fx = None
    if _ed_fx and _ed_fx.get("cenas") and _ed_fx.get("musica"):
        _t, _voz, _vaos = 0.0, [], []
        for _c in _ed_fx["cenas"]:
            _d = (_c["fala"]["fim"] - _c["fala"]["ini"]) if _c.get("fala") else float(_c.get("dur", 0))
            (_voz if _c.get("fala") else _vaos).append((_t, _t + _d))
            _t += _d
        _esc = inf["dur"] / _t if _t else 1
        def _rms(a, b):
            seg = som[int(a * _esc * TAXA):int(b * _esc * TAXA)]
            return 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9) if len(seg) else -99
        _ref = np.median([_rms(a, b) for a, b in _voz if b - a > 1]) if _voz else None
        _fim_voz = max((b for _, b in _voz), default=0)
        _altos = [(a, b, _rms(a + .15, b - .15)) for a, b in _vaos if b - a >= .8 and b <= _fim_voz + .01]
        _altos = [(a, b, r) for a, b, r in _altos if _ref is not None and r > _ref - 4]
        if _altos:
            # ⚠️, não ❌: o vão alto do Esquema #2 (nitro, +4,6 dB) foi aprovado como "perfeito"; o sting cômico, não
            nota("⚠️", "Trilha no vão sem voz quase no nível da fala: " + ", ".join(f"{a * _esc:.1f}-{b * _esc:.1f} s ({r - _ref:+.1f} dB da voz)" for a, b, r in _altos)
                 + ". Sting ou pontuação cômica: deixar ao menos 4 dB abaixo da voz (ganho direto na trilha; o `nivel` do edicao.json não muda nada). Momento de exibição (nitro, payoff) pode ficar alto")
        elif _ref is not None and any(b - a >= .8 for a, b in _vaos):
            nota("✅", "Trilha nos vãos sem voz abaixo da fala (4 dB ou mais)")
        for _selo, _txt in trilha_sob_voz(_ed_fx, _voz, _t, video):
            nota(_selo, _txt)
    # efeito sonoro alto contra a voz (JV, 25/11, Esquema #22: contador de cassino a -16 dB da voz "muito alto";
    # carimbos a -22 passaram). Mede como o fx.py mistura: tools/sfx_nivel.py
    if _ed_fx and _ed_fx.get("cenas"):
        try:
            import sfx_nivel
            _sfx = [s for s in sfx_nivel.medir(str(video.parent)) if s[3] is not None]
        except Exception as _e:
            _sfx = None
            nota("⚠️", f"Não deu para medir os efeitos sonoros ({_e.__class__.__name__})")
        if _sfx:
            _alto = [s for s in _sfx if s[3] > SFX_MAX_VOZ]
            if _alto:
                nota("⚠️", "Efeito sonoro alto perto da voz: " + ", ".join(f"{t:.1f} s {n} ({d:+.1f} dB da voz)" for t, n, _, d in _alto)
                     + f". Deixar a {SFX_MAX_VOZ} dB da voz ou menos (ganho mais baixo no edicao.json); efeito contínuo sob a fala, tirar")
            else:
                nota("✅", f"Efeitos sonoros {len(_sfx)}, todos a {SFX_MAX_VOZ} dB da voz ou menos")
    # distância do HUD em pés: o público é BR, vai em metros (JV, 20/11, arma perto de casa)
    try:
        import hud_metros
        _ft = hud_metros.achar(str(video), 2.0)
    except Exception as _e:  # OCR ausente não derruba a conferência
        _ft = None
        nota("⚠️", f"Não deu para procurar distância em pés no HUD ({_e.__class__.__name__})")
    if _ft:
        nota("⚠️", "Distância do HUD em pés (ft) em " + ", ".join(f"{t:.0f} s" for _, t, _ in _ft[:8])
             + (" ..." if len(_ft) > 8 else "") + f". Trocar por metros: `python tools/hud_metros.py trocar {video.name} <saida>.mp4`")
    elif _ft is not None:
        nota("✅", "Nenhuma distância em pés no HUD")
    dif = abs(inf["dur_v"] - inf["dur_a"]) * 1000
    nota("✅" if dif <= SYNC_ERRO else "⚠️", f"Faixas de imagem e som terminam com {dif:.0f} ms de diferença")
    # o relógio do arquivo tem que bater com o som de fato: foi assim que a emenda de AAC do montador se revelou
    relogio = abs(len(som) / TAXA - inf["dur_a"]) * 1000
    nota("✅" if relogio <= SYNC_ERRO else "❌", f"Som real e relógio do arquivo diferem em {relogio:.0f} ms")
    if segs:
        previsto = segs[-1]["t"] + segs[-1]["d"]
        folga = abs(previsto - inf["dur"])
        nota("✅" if folga < 0.5 else "⚠️", f"Duração prevista pela edição {previsto:.1f} s, real {inf['dur']:.1f} s")

    if edicao:
        import manter  # o que o JV mandou manter (manter.md) e sumiu reprova o vídeo (19 coisas, 14/11)
        m_prob, m_olhar = manter.checar(alvo, quieto=True)
        for p in m_prob:
            nota("❌", p)
        for o in m_olhar:
            nota("⚠️", f"Conferir de olho (pedido do JV): {o}")
        if (alvo.parent / "manter.md").exists() and not m_prob:
            nota("✅", "Tudo o que o JV mandou manter está na edição")

    # Selo "GTA 6" escondido aposentado (JV, 20/11: "pare com isso, n ta adiantando nada"): não cobra mais.

    try:  # JV, 19/11: layout medido no quadro final (OCR), não só no plano: peça em cima da legenda, texto na borda
        import layout
        _ped = Path(video).parent / "edicao.json"
        _ed = json.loads(_ped.read_text(encoding="utf-8")) if _ped.exists() else None
        _q, _ach, _res = layout.analisar(video, _ed, 1.0, _ped.parent)
        _folha = layout.folha(_q, _ach, Path(video).parent / "conferencia" / f"layout-{Path(video).stem}.jpg", _res["W"], _res["H"])
        for _k in _ach:
            nota("❌" if _k["grave"] else "⚠️", f"Layout · {_k['tipo']}: {_k['txt']}")
        if not _ach and not _res["com_legenda"]:
            nota("⚠️", "Layout: não reconheci a nossa legenda em nenhum quadro (vídeo sem legenda, prévia de trecho ou "
                 "edicao.json de outra versão); peça em cima da legenda não foi avaliada")
        elif not _ach:
            nota("✅", f"Layout do quadro final: legenda no eixo, nada em cima dela nem na borda ({_res['com_legenda']} quadros com legenda)")
        elif _folha:
            nota("⚠️", f"Folha do layout: {_folha} · conserto: `python tools/layout.py {video} --consertar`")
    except Exception as e:
        nota("⚠️", f"Checagem de layout não rodou ({e})")

    if vertical and "final" in video.stem and "sem-outro" not in video.stem:  # JV, 17/11: chip ou card em todo Short
        cta = video.parent / "cta.json"
        if cta.exists():
            d = json.loads(cta.read_text(encoding="utf-8"))
            nota("✅", f"CTA no meio: {d.get('peca')} aos {d.get('em')} s")
        else:
            nota("❌", "Short sem chip nem card de CTA (sem cta.json na pasta): `python tools/chip_cta.py aplicar` antes do outro (rodízio sozinho)")

    # JV, 20/11 (corte "perseguição neon", 23 s): "tem q mostrar muito mais do que aconteceu de fato, pelo menos dobrar".
    # Corte de live (pasta clipe-*) abaixo de 30 s só mostra o impacto: aviso para conferir o antes e o depois.
    if video.parent.name.startswith("clipe-") and "final" in video.stem and inf["dur"] < 30:
        nota("⚠️", f"Corte de live com {inf['dur']:.1f} s: mostra o que aconteceu de fato (contexto antes, o fato, o depois)? "
                   "Rápido vai de 30 a 60 s; menos só se o momento inteiro cabe")

    props = sorted(video.parent.glob("props*.json"), key=lambda p: p.stat().st_mtime)
    if props:  # JV, 17/11 ("Faltam 2 dias"): "~100" sem unidade ao lado de "~50 HORAS" foi lido como 100 horas
        import re
        d = json.loads(props[-1].read_text(encoding="utf-8"))
        vals = [d.get("painelValor", "")] + [l.get("valor", "") for l in d.get("linhas", [])]
        nus = [v for v in vals if re.search(r"\d", v) and not re.search(r"[^\W\d_]", v)
               and not re.fullmatch(r"\s*(19|20)\d\d\s*", v)]
        if nus:
            nota("❌", f"Painel ({props[-1].name}) com número sem unidade: {', '.join(nus)} (escrever horas, missões, GB...)")
        elif any(re.search(r"\d", v) for v in vals):
            nota("✅", "Todo número do painel tem unidade")

    outro_ini = None  # onde começa o outro aprovado: o fim parado dele é da arte, não defeito do corte (17/11)
    if "final" in video.stem and "sem-outro" not in video.stem:
        dif, qual = outro_no_fim(video, inf)
        if qual is not None and dif < OUTRO_ATUAL:
            outro_ini = inf["dur"] - info(ROOT / "marca" / "outro" / qual)["dur"]
        vertical = inf["h"] > inf["w"]
        if vertical:
            # 1º quadro do Short (19/11, 38 Shorts × "ficaram para assistir"): quem abre escuro (brilho < 45 de 255, fundo
            # escurecido esperando o card entrar) teve mediana de 48% contra 61% do resto; faixa preta (vídeo pequeno no
            # meio) ficou em 29 a 41%. O 1º quadro é o que aparece no feed antes de qualquer animação.
            try:
                def _quadro(t):
                    b = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(video), "-frames:v", "1",
                                        "-vf", "scale=90:160,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
                    return np.frombuffer(b, np.uint8).reshape(160, 90).astype(float)
                q0, q1 = _quadro(0.0), _quadro(1.0)
                if q0.mean() < 45:
                    nota("⚠️", f"Short abre escuro (brilho {q0.mean():.0f} de 255 no 1º quadro; em 1 s, {q1.mean():.0f}): os que "
                               "abriram abaixo de 45 seguraram 48% contra 61% (mediana). Abrir já no plano claro e forte, "
                               "com o texto do gancho visível no quadro 0, sem fade nem fundo escurecido")
                else:
                    nota("✅", f"1º quadro claro (brilho {q0.mean():.0f})")
                preto = float((q1.mean(axis=1) < 12).mean())
                if preto > 0.2:
                    nota("⚠️", f"Faixa preta em {preto:.0%} da altura no 1º segundo: vídeo pequeno no meio da tela segurou "
                               "só 29 a 41%. Encher a tela (recorte ou empilhado)")
            except Exception as ex:  # noqa: BLE001
                nota("⚠️", f"Não medi o 1º quadro ({ex})")
        if vertical and qual is not None and dif < OUTRO_ANTIGO:
            nota("❌", f"Short termina no outro (`marca/outro/{qual}`): desde 19/11 Short NÃO leva outro (JV: só ~30% "
                       "chegam nele, metade sai durante os 4 s e ele quebra o loop). Usar o vídeo sem o `outro.py anexar`; "
                       "o pedido de inscrição vai nos chips/cards de CTA")
        elif vertical:
            nota("✅", "Short sem outro (padrão desde 19/11; o CTA vai nos chips/cards)")
        elif qual is None:
            nota("⚠️", "Sem marca/outro/ para comparar o fim do vídeo")
        elif dif < OUTRO_ATUAL and qual in OUTRO_ANTIGOS("short" if inf["h"] > inf["w"] else "longo"):
            nota("❌", f"Termina no outro ANTIGO (`marca/outro/{qual}`): desde 17/11 o padrão é o outro novo; refazer com "
                       "`python tools/outro.py anexar <vídeo sem outro>`")
        elif dif < OUTRO_ATUAL:
            cor = voz_do_outro(video, inf, qual) if "sem-voz" not in qual else None
            if cor is not None and cor < OUTRO_VOZ_MIN:
                nota("❌", f"Outro com a arte atual mas a VOZ antiga (correlação {cor:.2f} com `marca/outro/{qual}`): "
                           "refazer com `python tools/outro.py anexar <vídeo sem outro>` (JV, 15/11: todo vídeo usa o CTA novo)")
            else:
                nota("✅", f"Termina no outro atual (`marca/outro/{qual}`)" + (f", voz igual ({cor:.2f})" if cor is not None else ""))
        elif dif < OUTRO_ANTIGO:
            nota("❌", f"Termina num outro ANTIGO (diferença {dif:.2f} do `marca/outro/{qual}` atual): refazer com "
                       "`python tools/outro.py anexar <vídeo sem outro>` (09/11: a caixa saiu com o radar verde)")
        else:
            nota("⚠️", f"Não termina em outro (diferença {dif:.2f}): se não é peça de loop sem CTA, anexar com `tools/outro.py anexar`")

    # consistência da voz entre parágrafos (JV, 17/11: "3 vozes diferentes ao longo do vídeo"): tom mediano de cada
    # parágrafo da narração da pasta; amplitude (maior − menor) em semitons. ⚠️ acima de 3,5 st, ❌ acima de 6 st.
    try:
        _np = video.parent / "narracao"
        if _np.with_suffix(".wav").exists() and _np.with_suffix(".palavras.json").exists() and _np.with_suffix(".txt").exists():
            sys.path.insert(0, str(ROOT / "tools"))
            import tts as _tts
            _pals = json.loads(_np.with_suffix(".palavras.json").read_text(encoding="utf-8"))
            _tons = [t for t in _tts.tons_paragrafos(_np.with_suffix(".wav"), _pals, _np.with_suffix(".txt").read_text(encoding="utf-8")) if t]
            if len(_tons) >= 3:
                _amp = 12 * math.log2(max(_tons) / min(_tons))
                _txt = f"Voz entre parágrafos: tom {min(_tons):.0f}–{max(_tons):.0f} Hz, amplitude {_amp:.1f} st"
                nota("❌" if _amp > 6 else "⚠️" if _amp > 3.5 else "✅", _txt + (" (soa como mais de uma voz: gerar outra tomada)" if _amp > 3.5 else ""))
    except Exception as _e:
        nota("⚠️", f"Consistência da voz não medida: {_e}")

    # grito na legenda só com a voz gritando de verdade (JV, 17/11: "QUINTO, TUNA UM CARRO!" tremeu sem grito). O fx.py
    # mede contra a fala inteira e escreve "GRITO (+N dB)" no render.log da pasta; aqui: ❌ se algum ficou abaixo de 7 dB
    # ou se há mais de 2 em 60 s (grito raro; se tudo vira grito, nada é grito).
    try:
        _rl = video.parent / "render.log"
        if _rl.exists():
            _gr = [float(m) for m in __import__("re").findall(r"GRITO \(\+(-?[\d.]+) dB\)", _rl.read_text(encoding="utf-8", errors="ignore"))]
            if _gr:
                _ruim = [g for g in _gr if g < 7]
                if _ruim or len(_gr) > max(2, inf["dur"] / 30):
                    nota("❌", f"Legenda em grito {len(_gr)}x ({', '.join(f'+{g:.1f} dB' for g in _gr)}): só com a voz 7 dB acima da fala toda e raro")
                else:
                    nota("⚠️", f"Legenda em grito {len(_gr)}x ({', '.join(f'+{g:.1f} dB' for g in _gr)}): ouvir se a voz grita de verdade ali")
            # 20/11 (JV, alanzoka corrida: 2 gritos + 11 ênfases em 26 cartelas, "tá usando muito"): grito e ênfase
            # contam juntos; mais de 1 a cada 15 s do vídeo (mínimo 2) reprova
            _en = len(__import__("re").findall(r"ÊNFASE \(\+", _rl.read_text(encoding="utf-8", errors="ignore")))
            if len(_gr) + _en > max(2, inf["dur"] / 15):
                nota("❌", f"Grito + ênfase {len(_gr) + _en}x em {inf['dur']:.0f} s: legenda de grito demais (teto 1 a cada 15 s)")
    except Exception as _e:
        nota("⚠️", f"Grito não conferido: {_e}")

    pretos = trechos_pretos(video)
    # Piscada preta (23/11, Esquema #12 v1: 3 quadros pretos aos 43,4 s, antes do celular; d=0,3 não pegava): preto curto
    # no meio do vídeo é emenda quebrada, nunca efeito. ❌ abaixo de 0,3 s longe da ponta; o resto segue ⚠️.
    _dur = duracao(video)
    piscadas = [(a, b) for a, b in pretos if b - a < 0.3 and a > 0.2 and b < _dur - 0.2]
    if piscadas:
        nota("❌", "Piscada preta (emenda quebrada) em " + ", ".join(f"{a:.2f}–{b:.2f} s" for a, b in piscadas)
             + ": ver o plano que termina antes da fonte (ini/fim além da duração) e refazer o render")
    pretos = [x for x in pretos if x not in piscadas]
    nota("⚠️" if pretos else "✅", "Trecho preto em " + ", ".join(f"{a:.1f}–{b:.1f} s" for a, b in pretos) if pretos else "Nenhum trecho preto")
    try:  # JV, 18/11 (abertura do GTA 6): a mesma cena do jogo vinda de lives diferentes passa pelo direcao.py
        sys.path.insert(0, str(Path(__file__).parent))
        import plano_repetido
        _tr, _tot, _ruim = plano_repetido.achar(video)
        _det = ", ".join(f"{x:.1f}-{y:.1f} s (igual a {o:.1f} s)" for x, y, o in _tr)
        nota("❌" if _ruim else "⚠️" if _tr else "✅",
             f"Plano repetido pela imagem, {_tot:.1f} s: {_det} (trocar o plano ou encurtar)" if _tr else "Nenhuma cena do jogo volta repetida")
    except Exception as _e:
        nota("⚠️", f"Plano repetido não conferido: {_e}")
    try:  # JV, 18/11 (Cellbit beat slam): empilhado = 2 telas cheias, sem janela solta nem barra
        import fundo_sobrando
        _med, _ruim, _av = fundo_sobrando.achar(video)
        nota("❌" if _med >= 40 else "⚠️" if _av else "✅",  # 19/11: 40%+ = Short em formato horizontal ("10 coisas" v3: 49%); falso positivo vai até 24%
             f"Fundo sobrando em {_med:.0f}% da tela (janela solta ou barra; empilhado = 2 telas que enchem tudo)" if _av
             else f"Sem fundo sobrando ({_med:.0f}% da tela)")
    except Exception as _e:
        nota("⚠️", f"Fundo sobrando não conferido: {_e}")
    try:  # JV, 18/11 (edit "A espera acabou"): abertura abafada e clarão no respiro antes do outro
        ab, resto = comeco_abafado(video, outro_ini)
        if ab is not None:
            if ab < 0.02 and ab < 0.15 * resto:
                nota("❌", f"Começa com o som abafado (agudos em {ab:.1%} da energia nos 4 primeiros segundos, {resto:.1%} no resto):"
                           " o vídeo abre com a música ou a fala inteira, para prender a atenção")
            else:
                nota("✅", f"Som aberto desde o começo (agudos {ab:.1%}; resto {resto:.1%})")
        if outro_ini is not None:
            claros = clarao_no_fecho(video, inf["fps"], outro_ini)
            if claros:
                nota("❌", "Clarão no respiro antes do outro em " + ", ".join(f"{t:.2f} s" for t in claros)
                     + ": no fecho a imagem só escurece (nada de plano ou quadro mais claro entrando)")
            else:
                nota("✅", "Respiro antes do outro sem quadro mais claro entrando")
    except Exception as _e:
        nota("⚠️", f"Abertura abafada e clarão no fecho não medidos: {_e}")
    import movimento  # regra do JV: sempre 1 coisa em movimento; as 2 metades nunca param juntas
    mov = movimento.analisar(video)
    if outro_ini is not None:
        mov["juntos"] = [(i, f) for i, f in mov["juntos"] if i < outro_ini - 0.5]
        mov["cima"] = [(i, f) for i, f in mov["cima"] if i < outro_ini - 0.5]
        mov["baixo"] = [(i, f) for i, f in mov["baixo"] if i < outro_ini - 0.5]
    for ini_p, fim_p in mov["juntos"]:
        nota("❌", f"Tela inteira parada em {ini_p:.1f}–{fim_p:.1f} s")
    for ini_p, fim_p in mov["cima"] + mov["baixo"]:
        nota("⚠️", f"Metade parada por muito tempo em {ini_p:.1f}–{fim_p:.1f} s")
    if not mov["juntos"]:
        nota("✅", "Sempre há algo em movimento")
    if edicao and edicao.get("cenas"):  # JV, 21/11 (Esquema #9): metade que anda e trava no meio da cena
        try:
            import congelado
            _cg = congelado.achar(edicao)
            for _a, _b, _nome, _arq, _clonado in _cg:
                if _clonado:
                    nota("❌", f"Plano trava no meio da cena, {_nome}: {_a:.1f}–{_b:.1f} s (quadro congelado; {_arq}): "
                               "trocar por trecho que anda a cena inteira, dividir a cena ou ir para tela cheia")
                else:
                    nota("⚠️", f"Plano quase parado no fim da cena, {_nome}: {_a:.1f}–{_b:.1f} s ({_arq}): olhar")
            if not _cg:
                nota("✅", "Nenhum plano trava no meio da cena")
        except Exception as _e:
            nota("⚠️", f"Plano congelado não conferido: {_e}")
    pops = estalos(som, cortes)
    nota("⚠️" if pops else "✅", "Possível estalo nos cortes em " + ", ".join(f"{t:.1f} s" for t in pops) if pops else "Nenhum estalo nos cortes")

    if segs:
        cortadas = palavras_cortadas(segs)
        for n, nome, t, p, perde, grave in cortadas:
            if nome == "frase":
                nota("❌", f"Trecho {n} (payoff, fechamento ou último clipe) termina em {t:.1f} s com a frase pela metade: "
                           f"\"{p}\". Estender o fim até o ponto final da fala")
            else:
                nota("❌" if grave else "⚠️", f"Trecho {n}: o {nome} ({t:.1f} s) corta {perde * 1000:.0f} ms da palavra \"{p}\""
                     + (" no payoff/último clipe: é o que fica no ouvido, estender o fim" if grave else ""))
        if not cortadas:
            nota("✅", "Nenhuma palavra cortada nas pontas (pela transcrição da fonte)")
        medidas = medir_sync(segs, video)
        boas = [m for m in medidas if m["ok"]]
        if boas:
            dif = {m["i"]: abs(m["img"] - m["som"]) for m in boas}
            pior = max(dif, key=dif.get)
            marca = "❌" if dif[pior] > SYNC_ERRO else "⚠️" if dif[pior] > SYNC_AVISO else "✅"
            nota(marca, f"Sincronia boca/som: pior trecho é o {pior}, com {dif[pior]:.0f} ms; {len(boas)} de {len(medidas)} trechos medidos")
            for m in boas:
                if dif[m["i"]] > SYNC_AVISO:
                    nota("⚠️", f"Trecho {m['i']} ({m['t']:.1f} s): imagem {m['img']:+.0f} ms, som {m['som']:+.0f} ms em relação à fonte")
        else:
            nota("⚠️", "Sincronia: nenhum trecho deu medida confiável, conferir assistindo")
        claras = legenda_clara(video, segs, inf)
        nota("⚠️" if claras else "✅", "Fundo claro atrás da legenda em " + ", ".join(f"{t:.1f} s" for t in claras) if claras else "Fundo da legenda escuro o bastante")

    folhas = folha_cortes(video, cortes, som, pasta, vertical)
    colunas = "4" if vertical else "10"
    rodar("ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf", f"fps=1,scale=-2:{'240' if vertical else '135'},tile={colunas}x{'2' if vertical else '8'}",
          "-q:v", "4", str(pasta / "quadros-%02d.jpg"))

    texto = [f"# Conferência: {video.name}", "", *linhas, "", "## Olhar nas folhas (a máquina não julga)",
             "- `quadros-*.jpg`: o 1º plano é o mais chocante (gancho apelativo, JV 19/11)?; ícone, tela branca ou loading; vinheta de canal-fonte.",
             f"- `cortes-*.jpg` ({len(folhas)}): salto estranho, quadro vazio, legenda em cima de rosto ou HUD. A linha vermelha é o corte.",
             "- Assistir ao gancho e ao fim com som: isto aqui não diz se é bom, só se está quebrado."]
    (pasta / "relatorio.md").write_text("\n".join(texto) + "\n", encoding="utf-8")
    print("\n".join(texto))
    print(f"\n{pasta}")
    sys.exit(1 if erros else 0)


if __name__ == "__main__":
    main()
