"""Volume final de um vídeo já renderizado: mede e, se precisar, acerta para -14 LUFS e pico abaixo de -1,5 dB (14/11).

Nasceu do Remotion: o render bruto da série "Faltam X dias" saiu quente 3 vezes seguidas (-12,7 LUFS) e era corrigido
à mão. O fx.py, o montador e o cartelas já normalizam por conta própria; este script é para o que não normaliza
(Remotion, qualquer vídeo de fora da linha).

  python tools/volume.py medir VIDEO.mp4
  python tools/volume.py normalizar VIDEO.mp4 [SAIDA.mp4]

Sem SAIDA, corrige no lugar e guarda o original como VIDEO.bruto.mp4. Se já está dentro (±1 LUFS, pico < -1,5 dB),
não mexe. A imagem é copiada sem recomprimir (-c:v copy); só o som é refeito, em duas passadas: a 1ª mede, a 2ª
aplica ganho linear (não achata a música) e um limitador de segurança no pico.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ALVO, FOLGA, PICO_MAX = -14.0, 1.0, -1.5


def medir(video: Path):
    log = subprocess.run(["ffmpeg", "-nostats", "-i", str(video), "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    resumo = log[log.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", resumo)
    p = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", resumo)
    if not i or "inf" in i.group(1):
        return None, None  # sem som ou silêncio
    return float(i.group(1)), float(p.group(1))


def dentro(i, p):
    return i is not None and abs(i - ALVO) <= FOLGA and p < PICO_MAX


def normalizar(video: Path, saida: Path | None = None) -> bool:
    """True se mexeu. Levanta erro se a correção não acertar o alvo (aí o original fica intacto)."""
    i, p = medir(video)
    if i is None:
        print(f"{video.name}: sem som, nada a fazer")
        return False
    if dentro(i, p):
        print(f"{video.name}: {i:.1f} LUFS, pico {p:.1f} dB, já está dentro")
        return False
    log = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video), "-af",
                          f"loudnorm=I={ALVO}:TP=-2:LRA=18:print_format=json", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    m = json.loads(log[log.rfind("{"):log.rfind("}") + 1])
    tmp = video.with_name(video.stem + ".volume-tmp" + video.suffix)
    filtro = (f"loudnorm=I={ALVO}:TP=-2:LRA=18:linear=true:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
              f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']},"
              "alimiter=limit=0.76:level=false")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-map", "0", "-c:v", "copy", "-af", filtro,
                    "-ar", "48000", "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", str(tmp)], check=True)
    i2, p2 = medir(tmp)
    if not dentro(i2, p2):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{video.name}: a correção deu {i2} LUFS, pico {p2} dB; original mantido")
    if saida:
        shutil.move(tmp, saida)
        destino = saida
    else:
        bruto = video.with_name(video.stem + ".bruto" + video.suffix)
        shutil.move(video, bruto)
        shutil.move(tmp, video)
        destino = video
    print(f"{video.name}: {i:.1f} → {i2:.1f} LUFS, pico {p:.1f} → {p2:.1f} dB ({destino.name}"
          + ("" if saida else f"; original em {video.stem}.bruto{video.suffix}") + ")")
    return True


def main(argv):
    if len(argv) < 2 or argv[0] not in ("medir", "normalizar"):
        sys.exit(__doc__)
    video = Path(argv[1])
    if argv[0] == "medir":
        i, p = medir(video)
        if i is None:
            print("sem som")
            return 0
        print(f"{i:.1f} LUFS, pico {p:.1f} dB: {'dentro' if dentro(i, p) else 'FORA'} (alvo {ALVO} ±{FOLGA:.0f}, pico < {PICO_MAX})")
        return 0 if dentro(i, p) else 1
    normalizar(video, Path(argv[2]) if len(argv) > 2 else None)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
