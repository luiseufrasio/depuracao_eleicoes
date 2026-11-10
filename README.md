# Depuração das Eleições

Download dos Boletins de Urna de 2022 e 2026 do TSE e aplicação web para comparar seções, escolas, cidades e estados na eleição para Presidente, mostrando o que mudou no voto e apontando anomalias a investigar.

> **Anomalia não é fraude.** Uma discrepância estatística é um ponto de partida para investigar, nunca uma conclusão. Veja [Limites](#limites).

## Fontes oficiais

Todas do [Portal de Dados Abertos do TSE](https://dadosabertos.tse.jus.br), licença CC-BY.

| Camada | Conteúdo | 2022 | 2026 |
|---|---|---|---|
| CSV de Boletim de Urna | Votos por seção, 2 turnos, 27 UFs + exterior (ZZ) | 1,53 GB | 5,22 GB |
| Arquivos brutos de urna | BU assinado, RDV, log e assinaturas | 168,5 GB | 219,2 GB |
| Cadastro | Escolas com nome e lat/long, perfil do eleitorado por seção | disponível | disponível |

- [BU 2022](https://dadosabertos.tse.jus.br/dataset/resultados-2022-boletim-de-urna) e [BU 2026](https://dadosabertos.tse.jus.br/dataset/resultados-2026-boletim-de-urna)
- [Arquivos brutos 2022](https://dadosabertos.tse.jus.br/dataset/resultados-2022-arquivos-transmitidos-para-totalizacao) e [arquivos brutos 2026](https://dadosabertos.tse.jus.br/dataset/resultados-2026-arquivos-transmitidos-para-totalizacao)

### O que o levantamento mostrou

- O portal de resultados não oferece mais a consulta de 2022 por seção. Os pacotes por UF continuam no Portal de Dados Abertos, então 2022 vem primeiro no download bruto.
- Em AC e SE, 98–99% das seções de 2022 mantêm a mesma chave em 2026, mas só 74–80% têm também o mesmo local e eleitorado parecido (±15%). Comparar só pelo número da seção gera discrepâncias falsas.
- Datas, nomes de colunas e separador decimal das coordenadas mudam entre os anos.

## Como rodar

Os dados não ficam no repositório. Em uma máquina nova, os comandos abaixo baixam cerca de 12 GB do TSE e recriam tudo em `data/`.

```powershell
git clone https://github.com/luiseufrasio/depuracao_eleicoes.git
cd depuracao_eleicoes
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m depuracao baixar     # retoma de onde parou e pula o que já está íntegro
.\.venv\Scripts\python -m depuracao base       # gera data/parquet e data/eleicoes.duckdb
.\.venv\Scripts\python -m depuracao conferir   # reconcilia com a totalização oficial
.\.venv\Scripts\python -m pytest
```

A conferência bloqueia se faltar UF, se a contagem de um candidato diferir da totalização oficial por zona, se o voto de Presidente diferir seção a seção, ou se um candidato com votos faltar na totalização sem que o cadastro oficial mostre candidatura inválida (renúncia, inaptidão etc.). O resultado completo fica em `data/conferencia.json`.

## Plano

| Fase | Entrega | Estado |
|---|---|---|
| 1. Download dos CSVs e do cadastro | Downloader que retoma de onde parou, confere o SHA-512 oficial e registra cada arquivo em um manifesto | concluída |
| 2. Base analítica | Parquet + DuckDB em esquema único. Só avança se os totais baterem com os resultados oficiais por UF | concluída |
| 3. Download e leitura dos brutos | Validação da leitura em AC, depois o restante, começando por 2022 | pendente |
| 4. Correspondência 2022–2026 | Grau de comparabilidade por seção e escola, com o município como nível seguro. Comparação por turno, comparecimento, brancos e nulos, e candidatos agrupados em tabela versionada | pendente |
| 5. Detectores de anomalia | Três níveis, descritos abaixo | pendente |
| 6. Validação dos detectores | Fraude simulada para medir a detecção, eleições anteriores como controle, correção para múltiplos testes | pendente |
| 7. Aplicação web | FastAPI + DuckDB e React com mapa. Navegação Brasil, UF, município, escola e seção, comparador, fila de anomalias e ficha com evidência e link para o arquivo oficial | pendente |

### Resultado da Fase 2

A base nacional cobre as 27 UFs e o exterior nos dois turnos dos dois anos. Nenhum voto de Presidente difere da totalização oficial, nem por zona nem por seção.

| Eleição | Seções com voto para Presidente | Votos (com brancos e nulos) | Chaves divergentes por seção |
|---|---|---|---|
| 2022, 1º turno | 472.028 | 123.682.372 | 0 |
| 2022, 2º turno | 472.028 | 124.252.796 | 0 |
| 2026, 1º turno | 499.206 | 125.275.835 | 0 |
| 2026, 2º turno | 499.205 | 128.236.074 | 0 |

Casos que o relatório mostra e que têm explicação nos próprios dados:

- Em 2026, no 1º turno, Leonardo Avalanche (número 28) tem 5.246 votos nas urnas e não aparece na totalização por zona, porque consta como renúncia no cadastro do TSE. É a única diferença entre os votos nominais por zona e a totalização.
- Em 2026, no 2º turno, duas seções do exterior tiveram comparecimento zero e por isso não têm voto para Presidente: município 29564, seção 91, e município 30686, seção 443. A primeira só existe no arquivo do 2º turno.
- Em 2022, o arquivo marca como anuladas 4 seções no 1º turno e 1 no 2º.

### Detectores (Fase 5)

- **Integridade:** assinatura válida, CSV igual ao BU, recontagem do RDV igual ao BU, votos dentro do comparecimento, urna ou carga duplicada.
- **Operação (logs):** horários, ritmo de votação, liberações sem biometria, substituição de urna.
- **Estatística:** comparação entre seções da mesma escola, resíduos controlados por histórico e perfil, gráfico de comparecimento contra votos (*fingerprint* de Klimek), virada do 1º para o 2º turno. A Lei de Benford fica só como apoio, porque a literatura mostra que ela não distingue bem eleições limpas de fraudadas.

## Decisões

| Tema | Decisão |
|---|---|
| Onde guardar os brutos (388 GB) | A Fase 3 será feita em outro computador, com mais espaço |
| Publicação | O código é público. A aplicação e qualquer lista de anomalias ficam privadas até a Fase 6 terminar |
| Cargos | Somente Presidente, na base e nas comparações. Os pacotes do TSE trazem todos os cargos juntos, então o download não diminui, mas só o voto presidencial entra na base |

## Limites

- A conferência da Fase 2 compara arquivos publicados pelo próprio TSE. Ela mostra que a base reproduz a totalização oficial sem erro de conversão, mas não é evidência independente de que os votos estejam corretos.
- Com cerca de 500 mil seções, um limiar de 1% marca uns 5 mil casos só por acaso.
- Mudanças de voto entre eleições são esperadas. Comunidades indígenas ou rurais costumam votar de forma muito concentrada.
- As checagens de integridade detectam adulteração depois que a urna gerou os arquivos. Se a própria urna gerasse BU e RDV falsos de forma coerente, elas não pegariam, e só a estatística daria algum indício.
- A aplicação fala em "anomalia a investigar", sempre com as explicações alternativas.
