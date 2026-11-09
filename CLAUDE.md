# Depuração das Eleições

O plano, as fases e os comandos estão no `README.md`. Mantenha a tabela de fases e as decisões do README atualizadas a cada avanço. Este arquivo guarda o que é preciso saber para continuar o trabalho.

## Escopo

- Somente a eleição para **Presidente**, 2022 e 2026, 1º e 2º turnos.
- O usuário escreve em português. Código, comentários e documentação também.
- Anomalia não é fraude. Nunca apresente uma seção, escola ou pessoa como fraudulenta. Use "anomalia a investigar" e mostre as explicações alternativas.
- O código é público. A aplicação e listas de anomalias ficam privadas até a Fase 6 terminar. `data/` nunca vai para o Git.

## Próximo passo: Fase 3, em outra máquina com mais disco

Os pacotes brutos somam 388 GB compactados (168,5 GB em 2022 e 219,2 GB em 2026). O maior, 2º turno de SP em 2026, tem 26 GB.

- Datasets CKAN: `resultados-{ano}-arquivos-transmitidos-para-totalizacao`, 56 ZIPs por ano.
- URL: `https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes{ano}/arqurnatot/bu_imgbu_logjez_rdv_vscmr_{ano}_{1|2}t_{UF}.zip`
- Comece por AC (cerca de 0,3 a 0,5 GB por pacote) para validar a leitura de BU, RDV e log, e baixe 2022 antes de 2026.
- Nenhum pacote bruto foi aberto até agora. O formato interno ainda precisa ser confirmado.
- Os pacotes trazem todos os cargos juntos. Só o voto presidencial e os dados de log e integridade da seção devem entrar na base.
- Em 2026, o portal de resultados lista por seção `rdv.dat`, `bu.dat`, `vota.vsc` e `log.jez`, sem `imgbu`, apesar do nome do pacote.
- O portal de resultados já não tem 2022 por seção. Em 2022, os pacotes do Portal de Dados Abertos são a única fonte encontrada.
- Códigos de pleito: 2022 é 406 e 407, 2026 é 3220 e 3221.

## Armadilhas já encontradas

- Com caminhos `uf=AC/`, o DuckDB substitui a coluna do arquivo pelo valor do caminho. Validações sobre `ano`, `turno` ou `uf` precisam de `hive_partitioning=false`, ou nunca falham.
- A chave de seção se repete entre anos, mas só 74–80% das seções de AC e SE têm também o mesmo local e eleitorado parecido. Não compare seções só pelo número.
- `votacao_candidato_munzona` omite candidatos com candidatura inválida que ainda receberam votos na urna. O cadastro principal de 2026 vem com a situação vazia (`#NE`), e por isso a situação vem de `consulta_cand_complementar`, filtrada por quem estava na urna.
- Formatos entre anos: datas `DD/MM/AAAA` em 2022 e ISO em 2026, vírgula ou ponto nas coordenadas, `-1` é `#NULO` e `-3` é `#NE`. Tudo em latin-1.
- A conversão falha de propósito diante de coluna ou formato desconhecido. Não troque por conversão tolerante, porque erro silencioso aqui viraria falsa anomalia.
- A conversão com todos os cargos foi interrompida pelo sistema por falta de memória em uma máquina de 32 GB, sem confirmação de qual processo consumiu a RAM. A base guarda só Presidente, e o DuckDB fica limitado a 12 GB. Mesmo assim, a conversão nacional foi interrompida outra vez como processo em segundo plano do Claude Code, que encerra esses processos quando a memória do sistema fica baixa. Rode `python -m depuracao base` em um terminal próprio. Ela retoma do ponto em que parou, sem refazer o que já está pronto.

## Regras de trabalho

- Depois de mudar a conversão, rode `python -m pytest` e `python -m depuracao conferir`. O resultado precisa continuar reconciliado com a totalização.
- Não afrouxe `conferir.py` para fazer uma divergência sumir. Investigue a causa primeiro.
