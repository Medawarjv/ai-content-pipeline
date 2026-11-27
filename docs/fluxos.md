# Linha de produção do Foragido GTA: quem faz o quê

Leitura comum dos 3 agentes de produção (`diretor`, `editor`, `embalador`) e de quem os chama. Desde 19/11 o antigo `orquestrador` está dividido nesses três; cada um é um agente novo, sem memória do anterior, e o bastão entre eles é o `estado.md` da pasta do vídeo. O `revisor` ficou fora da linha: no teste cego de 19/11 acertou 1 de 5 vereditos do JV (placar em `.claude/agents/revisor.md`). Ele existe só para segunda opinião pedida pelo JV; o veredito dele não trava nem devolve peça.

## A linha
| Ordem | Agente | Modelo | Etapas | Entrega na pasta do vídeo |
|---|---|---|---|---|
| 1 | `diretor` | opus | 1 validar a pauta · 2 roteiro · 3 direção · 4 música | `roteiro.md`, `narracao.txt`, `beats.json`, decisão da música, `estado.md` |
| 1b | `fotografo` | sonnet | escolha de planos, **só em Short de Dicas e Esquema** e em vídeo reprovado por imagem (JV, 24/11): `tools/fotografia.py` acha os candidatos, o agente escolhe trecho e enquadramento de cada beat que prova algo | `fotografia/folha.md`, `escolha.json`, `folha.jpg` |
| 2 | `editor` | sonnet | 5 narração · 6 fontes · 7 montagem · 8 conferência (medida e de critério) | `edicao.json`, `vN.mp4`, `conferencia/`, `estado.md` |
| 3 | `embalador` | opus | 9 embalagem · 10 candidatos a Short · 11 relatório | `publicar.md`, thumb ou capa, relatório final |

A ordem **história → imagem → música → montagem** é fixa: vídeo que começa pela música ou pelo efeito foi o erro que fez o 1º teste do `fx.py` sair "muito confuso".

**O que roda em paralelo:** pautas diferentes, até a montagem. Render e TTS de vídeo com GPU, um por vez. Dentro de uma pauta, nada: cada agente depende do que o anterior decidiu.

## Voltas
- O `editor` devolveu "impossível de executar" (a história não fecha com o material): chamar o `diretor` com o `estado.md`, e depois o `editor` de novo.
- Conferência com ❌: o próprio `editor` conserta, até 3 rodadas; sobrou defeito, o pacote segue para o JV com o que ficou escrito.
- Reprovação do JV: fluxo "Vídeo reprovado" da tabela abaixo.

## Fluxos por tipo de conteúdo
| Tipo | `diretor` | `editor` | Observação |
|---|---|---|---|
| Informativo ou notícia narrado (Short ou longo) | 1 → 2 → 3 → 4 | 5 → 6 → 7 → 8 | Molde: `saida/2026-11-07/short-que-horas/` e `que-horas-longo/` |
| Corte de streamer (só GTA 6 na tela ou na fala) | 1 → escolha do momento (`montar-short`, 5 a 8 candidatos, nome do fechamento) → 3 → copy das legendas e do título → 4 (quase sempre "nenhuma") | 6 → 7 → 8 | Sem TTS. Se o VOD ainda não foi baixado e transcrito, o `diretor` faz isso antes de escolher (`tools/clipe.py`). Corte de RP ou de GTA V aleatório volta sem produzir (JV, 08/11) |
| Gráfico animado (contagem, número, cartaz; sem narração) | 1 → copy do cartaz → 3 curto → 4 (drop no `paradaNumero`) → `props.json` | render do Remotion → 8 | Sem TTS. Molde: `saida/2026-11-09/short-contagem-10-dias/` |
| Short tirado de um longo pronto | escolha do momento → 3 | 7 → 8 | O longo é a fonte. Um Short por subpasta `short-<slug>/` |
| Molde com lacunas | 1 → 2 com `[COLCHETES]` e cenários → 3 | não roda | O `embalador` escreve título por cenário; o render fica para o dia |
| Episódio de série da casa (Esquema, Segredos, Parece bug...) | 1 → 2 → 3 → 4, com o item `proximo` de `"card": true` no fecho (chamada e mídia do PRÓXIMO episódio, ou `"modelo": "carimbo"`) | 5 → 6 → 7 (cadeia até `tools/card_proximo.py`, por último) → 8 | `revisor-serie` entre os dois. Prévia é o padrão, Carimbo a reserva (JV, 20/11) |
| Vídeo reprovado pelo JV | só se o motivo for tom (→ 2), confusão ou ritmo (→ 3) ou música (→ 4) | sempre que algo mudou; motivo técnico começa aqui (→ 7) | Não refazer tudo e não ir ao extremo oposto da crítica |

**Parada A (roteiro para o JV ler antes do TTS):** longo acima de 2 min, tema novo ou dúvida de tom, quando o briefing pedir. **Não vale para episódio de série (JV, 20/11): lá quem lê o roteiro é o agente `revisor-serie`, entre o `diretor` e o `editor`, e o JV só vê o vídeo pronto.** O `diretor` termina 1 a 4 e devolve; o `editor` só é chamado depois do OK, e pode adiantar a etapa 6 (fontes), que não gasta crédito.

## Pasta e bastão
- **Pasta padrão:** `saida/AAAA-MM-DD/<slug>/`, com `roteiro.md`, `narracao.txt`, `beats.json`, `gerar.py`, `edicao.json`, `fontes.json`, `narracao.*`, `vN.mp4`, `conferencia/`, `publicar.md`, `estado.md`. Versão antiga fica como registro (`v1`, `v2`); a última é a que vale.
- **`estado.md`:** cada agente acrescenta o seu bloco no fim (`## diretor`, `## editor`, `## embalador`), com o que fez, os caminhos, as decisões tomadas e por quê, o que o próximo precisa e o que ficou `CONFIRMAR`. O texto desse bloco é também o que ele devolve como relatório. Quem chega começa lendo o `estado.md` inteiro e os arquivos que ele aponta.
- **Ninguém reabre a decisão do anterior.** Se algo for impossível de executar (fonte sumiu, render quebra, a história não fecha com o material), registrar no `estado.md` e devolver, em vez de improvisar outra história.

## Regras que valem para os três
- **Nunca** publicar, agendar, postar, abrir navegador, mandar mensagem ou criar conta. Quem fala com o JV é a sessão principal.
- Não editar `aprendizados/regras.md`, `feedback.md`, `copy/pares.md` nem `PROXIMA-SESSAO.md`. Exceções: a linha de `aprendizados/musica/banco.md` (etapa 4) e o registro de feedback do fluxo "reprovado", se a sessão principal ainda não registrou.
- **Fato só com fonte de nível 1.** O que não fechar fica `CONFIRMAR` e segue no `estado.md` até o relatório. Boato e leak entram como o público conta, na voz e na imagem (JV, 14/11); fake de IA com asset do jogo, não.
- **Risco concreto dito uma vez, em uma linha, e a peça sai inteira.** Não moralizar, não travar, não encolher a peça.
- **Qualidade antes de rapidez (JV, 14/11):** melhor fonte disponível, prévia e render em resolução cheia.
- **Fable não entra em produção automática; Haiku só para listar e triar,** nunca para texto público.
- Regras do manual: cada agente lê o arquivo da própria fase (`aprendizados/regras-fase-*.md`, gerado por `python tools/regras_nucleo.py`). Quando uma skill mandar ler o `regras.md` inteiro, o núcleo ou uma seção que já está nesse arquivo, **não reler**. Seção que falte: `python tools/regras_nucleo.py secao "Nome"`.
