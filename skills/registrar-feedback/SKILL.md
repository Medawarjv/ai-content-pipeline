---
name: registrar-feedback
description: Transforma cada comentário do JV sobre um vídeo, Short, carrossel, copy, arte ou processo do Radar GTA em aprendizado permanente, num passo só - registro em aprendizados/feedback.md, regra nova ou ajustada em regras.md, checagem automática quando o defeito dá para medir, par "meu → dele" em pares.md quando ele reprova texto, manter.md quando ele manda manter algo, e o bastão. Use SEMPRE que o JV comentar, aprovar, reprovar ou pedir ajuste em qualquer peça ("ficou ruim", "gostei", "tira isso", "mantém", "não era isso", "cringe", "pode postar assim"), ou decidir algo que muda o jeito de produzir, mesmo que ele não peça para anotar e mesmo no meio de outra tarefa.
---

# Registrar feedback

Regra só no papel não impediu a repetição; checagem impediu (14/11). E o JV já precisou repetir pedido ("você removeu 2 dos cards dos músicos, mesmo depois de eu dizer explicitamente para manter", 14/11). Este é o passo que garante que um comentário dele vale para sempre, não só para o vídeo da vez. Faz-se **na hora**, antes de voltar ao conserto, e sem ele pedir.

## 1. Separar o comentário em itens

O JV costuma mandar vários pontos numa mensagem. Quebrar em itens e classificar cada um:

| Tipo | Exemplo | Vai para |
|---|---|---|
| **conserto só deste vídeo** | "no 03:03 apareceu uma mulher de stock" | conserto + feedback.md |
| **gosto que vale para os próximos** | "não gosto de funk, evitar mencionar" | + regra em regras.md |
| **defeito medível** | posição, resolução, sobreposição, volume, recorte, texto saindo, repetição, CTA errado | + checagem automática (passo 4) |
| **texto reprovado** | título, gancho, legenda, fecho | + par em pares.md (passo 5) |
| **"manter"** | "mantém os cards dos músicos", "pode postar assim" | + manter.md do vídeo (passo 6) |
| **aprovação** | "ficou ótimo", "excelentes respostas" | feedback.md + subir a confiança da regra que funcionou |
| **decisão de processo** | "pode continuar produzindo", "continue publicando" | regra ou bastão; memória só se valer fora deste projeto |
| **veredito de vídeo com música** (aprovou, elogiou ou reprovou, falando da música ou não) | "adorei a música", "a música entra e some", "Esquema 19 aprovado" | + `python tools/ouvido.py registrar <arquivo exato que ele viu> aprovado\|reprovado "frase dele"` **antes de qualquer conserto ou novo render** (a foto guarda as medidas e a configuração de som daquele arquivo; em 26/11 o gabarito retroativo saiu sujo porque os reprovados já tinham sido consertados por cima), uma linha em `aprendizados/musica/placar.md` e `python tools/ouvido.py calibrar` (só grava régua com 8 reprovados limpos) |

Item ambíguo (não dá para saber se é do vídeo ou geral, ou o que ficou ruim): perguntar **uma** vez, curto, e registrar a pergunta como pendente ("Falta o JV dizer o que ficou ruim"). Nunca inventar a causa.

## 2. `aprendizados/feedback.md` (no topo, logo abaixo do cabeçalho)

```
## DD/MM/AAAA · <peça> (`saida/AAAA-MM-DD/<slug>/<arquivo>`)
**Disse:** "<as palavras dele, entre aspas, sem resumir o que importa>"
**Mudou:** <o que eu consertei ou vou consertar, por item>
**Causa:** <só se eu souber; hipótese marcada como hipótese>
**Regras:** <seção do regras.md afetada, checagem criada, par, manter.md; ou "nenhuma, conserto pontual">
```

As palavras dele vão literais: é o que permite reler a intenção depois.

**Episódio de série que passou pelo `revisor-serie`** (tem `revisao-roteiro.md` na pasta): preencher a coluna "veredito do JV" da linha dele em `aprendizados/revisao-serie-placar.md` (aprovou, ou reprovou por quê) e dizer se o defeito já estava no roteiro. É o que mede o revisor.

## 3. `aprendizados/regras.md`

- Achar a seção certa (`grep` pelo assunto) e **ajustar a regra que já existe** em vez de criar outra ao lado. Duas regras sobre o mesmo assunto viram contradição na produção.
- Nível: 👀 pedido dele; ✅ pedido dele já confirmado por resultado ou por repetição; 📈 dado do canal (≥ 3 vídeos); 🧪 hipótese minha. Origem no fim: `*(JV, DD/MM, <peça>: "<frase curta dele>")*`.
- Comentário que contradiz uma regra: reescrever a regra e dizer no feedback.md o que mudou. Regra dele não cai por dado sozinho; regra 🧪 minha cai com o comentário dele.
- Corrigir na medida: a regra nova diz o ajuste que ele pediu, não o extremo oposto (memória "Sutileza").
- Depois: `python tools/regras_nucleo.py` (o núcleo é gerado; nunca editar à mão).

## 4. Checagem automática, quando o defeito dá para medir

Perguntar: **uma máquina consegue ver isso sem assistir?** Se sim, a checagem é obrigatória, no script que já roda naquela etapa:

| Etapa | Script | Já pega |
|---|---|---|
| antes do render | `tools/direcao.py` | ritmo, efeito demais, som repetido, manter.md, faixa repetida entre vídeos |
| antes do render | `tools/resolucao.py`, `tools/ocupacao.py` | fonte abaixo de 1080p, peça esticada, ocupação da tela |
| variedade | `tools/acervo.py variedade` | fonte acima de 40%, plano de streamer repetido, imagem repetida 3+ vezes |
| arte | `tools/kit.py checar` | texto estourando, sobreposto, colado |
| depois do render | `tools/conferir.py` | volume, sync, palavra cortada, fala cortada no payoff, outro antigo no fim, manter.md |

Fazer assim:
1. Escrever a checagem como ❌ quando o JV reprovou, ⚠️ quando é questão de gosto.
2. **Testar contra o caso que falhou** (tem que dar ❌) **e contra um aprovado** (tem que dar ✅). Sem os dois testes, não está pronta.
3. Anotar no feedback.md em "Regras:" o nome da checagem e os dois resultados, e atualizar a linha do script em `tools/README.md`.

Se não der para medir (gosto, graça, coerência), dizer no feedback.md "não medível" e deixar a regra no `regras.md`, que a `conferir-video` lê.

## 5. Texto reprovado → par em `aprendizados/copy/pares.md`

Se ele reprovou um texto sem reescrever, pedir em uma linha: **"Reescreve essa frase do jeito que você falaria? Guardo o par para não repetir."** Com a frase dele, linha nova no topo da tabela: data, peça, minha versão, a dele, o que mudou (o princípio, em uma frase). Se ele não quiser reescrever, registrar só o motivo no feedback.md.

## 6. "Mantém isso" → `manter.md` do vídeo

Tudo que ele mandar manter, ou aprovar explicitamente numa versão, entra no `manter.md` da pasta do vídeo, para nenhuma versão seguinte perder:

```
python tools/manter.py add saida/AAAA-MM-DD/<slug> texto "RICK ROSS" --origem "JV 14/11"
python tools/manter.py add saida/AAAA-MM-DD/<slug> arquivo "fontes/hq/QdBZY2fkU-0.mp4" --origem "JV 14/11"
python tools/manter.py add saida/AAAA-MM-DD/<slug> fala "fica até o final" --origem "JV 13/11"
python tools/manter.py add saida/AAAA-MM-DD/<slug> olhar "bullets com número rosa, sem hífen" --origem "JV 14/11"
```

`texto`, `arquivo` e `fala` são conferidos pela máquina (`direcao.py` barra o render, `conferir.py` reprova); `olhar` vai listado na conferência para eu ver na folha. Versão nova em pasta nova (`-v2`, `-v3`): copiar o `manter.md`; o `manter.py` acusa se a anterior tinha e a nova não.

## 7. Fechar

- Se o comentário mudou o que está pendente (peça descartada, decisão tomada), atualizar `PROXIMA-SESSAO.md` e riscar ou acrescentar em `PENDENCIAS-JV.md`.
- Memória (`~/.claude/projects/.../memory/`) só para o que vale além deste projeto ou para o jeito de trabalhar com ele; o que o `regras.md` já guarda não vai para a memória.
- **Narração no perfil v4 comentada (aprovou ou reprovou a voz):** `python tools/tts.py --veredito <pasta>/narracao.mp3 bom|ruim "o que ele disse"`. Alimenta o aprendizado do v4 (`--aprendizado` mostra onde a régua discorda do ouvido dele e o tom das aprovadas; a margem da régua se ajusta por esse dado, nunca por uma tomada só). *(JV, 17/11)*
- Aprovação de primeira: se a peça vai ser publicada, anotar `aprovado_de_primeira` (`sim` se ele aprovou a 1ª versão que viu, `nao` se pediu ajuste) para a linha do `metricas.csv` (skill `publicar`).
- Responder ao JV numa linha por item: o que mudou e onde ficou ("Funk: regra nova em Narração. Cards dos músicos: no manter.md, o render barra se sumirem.").
