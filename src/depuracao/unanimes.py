"""Seções em que todos os votos para Presidente foram para um candidato, comparadas entre 2022 e 2026."""
import json
from pathlib import Path

import duckdb

from .base import LIMITE_MEMORIA
from .conferir import _imprimir, _linhas

# Anomalia a investigar, não fraude: unanimidade é comum em seções pequenas e em comunidades muito coesas.
CRITERIOS = {
    "todos_os_votos": "total > 0 AND v_alvo = total",
    "entre_nominais": "v_alvo > 0 AND outros_nominais = 0",
}

_SQL_SECOES = """
CREATE OR REPLACE TEMP TABLE sec AS
SELECT ano, turno, uf, cd_municipio, any_value(nm_municipio) AS municipio, zona, secao,
       max(local_votacao) AS local_votacao, max(aptos) AS aptos, max(cd_tipo_urna) AS tipo_urna, sum(votos) AS total,
       coalesce(sum(votos) FILTER (WHERE nr_votavel = {alvo}), 0) AS v_alvo,
       coalesce(sum(votos) FILTER (WHERE ds_tipo_votavel = 'nominal' AND nr_votavel <> {alvo}), 0) AS outros_nominais
FROM boletim WHERE cd_cargo = 1
GROUP BY ano, turno, uf, cd_municipio, zona, secao
"""

_SQL_LOCAIS = """
CREATE OR REPLACE TEMP TABLE loc AS
SELECT ano, sg_uf AS uf, cd_municipio, nr_zona AS zona, nr_secao AS secao,
       arg_max(nm_local_votacao, nr_turno) AS nome, arg_max(ds_tipo_local, nr_turno) AS tipo
FROM locais GROUP BY ano, sg_uf, cd_municipio, nr_zona, nr_secao
"""

_SUBCONSULTA_ANO = """
SELECT s.*, ({{condicao}}) AND tipo_urna = 1 AS marcada, l.nome, l.tipo
FROM sec s LEFT JOIN loc l USING (ano, uf, cd_municipio, zona, secao)
WHERE ano = {ano}
"""

# Urna anulada não conta como marcada. A chave de seção se repete entre anos sem garantir o mesmo eleitorado,
# por isso "comparavel" exige também o mesmo local e eleitorado até 15% diferente.
_SQL_COMPARACAO = """
SELECT '{criterio}' AS criterio, turno, uf, cd_municipio, zona, secao,
       coalesce(b.municipio, a.municipio) AS municipio,
       coalesce(a.marcada, false) AS marcada_2022, coalesce(b.marcada, false) AS marcada_2026,
       a.ano IS NOT NULL AS existe_2022, b.ano IS NOT NULL AS existe_2026,
       coalesce(a.local_votacao = b.local_votacao AND abs(b.aptos - a.aptos) <= 0.15 * a.aptos, false) AS comparavel,
       a.local_votacao AS local_2022, b.local_votacao AS local_2026,
       a.aptos AS aptos_2022, b.aptos AS aptos_2026, a.total AS votos_2022, b.total AS votos_2026,
       a.v_alvo AS alvo_2022, b.v_alvo AS alvo_2026,
       round(100.0 * a.v_alvo / nullif(a.total, 0), 1) AS pct_2022, round(100.0 * b.v_alvo / nullif(b.total, 0), 1) AS pct_2026,
       a.nome AS nome_local_2022, b.nome AS nome_local_2026, coalesce(b.tipo, a.tipo) AS tipo_local,
       CASE WHEN regexp_matches(upper(concat_ws(' ', a.nome, b.nome)), 'ALDEIA|IND[IÍ]GENA') THEN 'indigena'
            WHEN regexp_matches(upper(concat_ws(' ', a.nome, b.nome)), 'QUILOMB') THEN 'quilombola'
            WHEN regexp_matches(upper(concat_ws(' ', a.nome, b.nome)), 'ASSENTAMENTO') THEN 'assentamento'
            ELSE 'nao_identificado' END AS indicio_no_nome_do_local
FROM (""" + _SUBCONSULTA_ANO.format(ano=2022) + """) a
FULL JOIN (""" + _SUBCONSULTA_ANO.format(ano=2026) + """) b
  USING (turno, uf, cd_municipio, zona, secao)
WHERE coalesce(a.marcada, false) OR coalesce(b.marcada, false)
"""

SQL_SECOES_POR_ELEICAO = """
SELECT ano, turno, count(*) AS secoes FROM sec GROUP BY ALL ORDER BY ALL
"""

SQL_RESUMO = """
WITH s AS (
  SELECT criterio, turno::VARCHAR AS turno, marcada_2022, marcada_2026, existe_2022, existe_2026, comparavel FROM comparacao
  UNION ALL
  SELECT criterio, 'qualquer', bool_or(marcada_2022), bool_or(marcada_2026), bool_or(existe_2022), bool_or(existe_2026), bool_or(comparavel)
  FROM comparacao GROUP BY criterio, uf, cd_municipio, zona, secao
)
SELECT criterio, turno,
       count(*) FILTER (WHERE marcada_2022) AS em_2022,
       count(*) FILTER (WHERE marcada_2026) AS em_2026,
       count(*) FILTER (WHERE marcada_2022 AND marcada_2026) AS nos_dois,
       count(*) FILTER (WHERE marcada_2022 AND marcada_2026 AND comparavel) AS nos_dois_comparaveis,
       count(*) FILTER (WHERE marcada_2022 AND NOT marcada_2026) AS so_2022,
       count(*) FILTER (WHERE marcada_2022 AND NOT existe_2026) AS so_2022_sem_a_secao_em_2026,
       count(*) FILTER (WHERE marcada_2026 AND NOT marcada_2022) AS so_2026,
       count(*) FILTER (WHERE marcada_2026 AND NOT existe_2022) AS so_2026_sem_a_secao_em_2022
FROM s GROUP BY ALL ORDER BY ALL
"""

SQL_NO_OUTRO_ANO = """
WITH u AS (
  SELECT criterio, turno, 'marcada so em 2022' AS caso, comparavel, existe_2026 AS existe_outro, pct_2026 AS pct_outro
  FROM comparacao WHERE marcada_2022 AND NOT marcada_2026
  UNION ALL
  SELECT criterio, turno, 'marcada so em 2026', comparavel, existe_2022, pct_2022
  FROM comparacao WHERE marcada_2026 AND NOT marcada_2022
)
SELECT criterio, turno, caso, count(*) AS secoes, count(*) FILTER (WHERE existe_outro) AS com_secao_no_outro_ano,
       count(*) FILTER (WHERE comparavel) AS comparaveis, median(pct_outro) AS mediana_pct_no_outro_ano,
       count(*) FILTER (WHERE pct_outro >= 90) AS com_90pct_ou_mais, count(*) FILTER (WHERE pct_outro < 50) AS abaixo_de_50pct
FROM u GROUP BY ALL ORDER BY ALL
"""

SQL_ESCOLAS = """
WITH a AS (SELECT DISTINCT criterio, turno, uf, cd_municipio, zona, local_2022 AS local_votacao FROM comparacao WHERE marcada_2022),
b AS (SELECT DISTINCT criterio, turno, uf, cd_municipio, zona, local_2026 AS local_votacao FROM comparacao WHERE marcada_2026)
SELECT criterio, turno, count(a.uf) AS locais_2022, count(b.uf) AS locais_2026, count(a.uf) FILTER (WHERE b.uf IS NOT NULL) AS nos_dois
FROM a FULL JOIN b USING (criterio, turno, uf, cd_municipio, zona, local_votacao)
GROUP BY ALL ORDER BY ALL
"""

SQL_POR_UF = """
SELECT uf,
       count(*) FILTER (WHERE turno = 1 AND marcada_2022) AS t1_2022, count(*) FILTER (WHERE turno = 1 AND marcada_2026) AS t1_2026,
       count(*) FILTER (WHERE turno = 2 AND marcada_2022) AS t2_2022, count(*) FILTER (WHERE turno = 2 AND marcada_2026) AS t2_2026
FROM comparacao WHERE criterio = 'todos_os_votos' GROUP BY ALL ORDER BY t1_2022 + t1_2026 + t2_2022 + t2_2026 DESC, uf
"""

SQL_INDICIO = """
SELECT criterio, turno, indicio_no_nome_do_local,
       count(*) FILTER (WHERE marcada_2022) AS em_2022, count(*) FILTER (WHERE marcada_2026) AS em_2026
FROM comparacao GROUP BY ALL ORDER BY ALL
"""

SQL_TAMANHO = """
WITH u AS (
  SELECT criterio, turno, 2022 AS ano, votos_2022 AS votos FROM comparacao WHERE marcada_2022
  UNION ALL
  SELECT criterio, turno, 2026, votos_2026 FROM comparacao WHERE marcada_2026
)
SELECT criterio, ano, turno, CASE WHEN votos < 20 THEN '01-19' WHEN votos < 50 THEN '20-49' WHEN votos < 100 THEN '50-99' ELSE '100+' END AS votos_na_secao,
       count(*) AS secoes
FROM u GROUP BY ALL ORDER BY ALL
"""


def comparar(con: duckdb.DuckDBPyConnection, candidato: int) -> None:
    """Cria a tabela temporária `comparacao`, com uma linha por critério, turno e seção marcada em algum ano."""
    con.execute(_SQL_SECOES.format(alvo=int(candidato)))
    con.execute(_SQL_LOCAIS)
    corpo = " UNION ALL ".join(_SQL_COMPARACAO.format(criterio=c, condicao=s) for c, s in CRITERIOS.items())
    con.execute(f"CREATE OR REPLACE TEMP TABLE comparacao AS {corpo}")


def relatorio(raiz: Path, candidatos: list[int]) -> int:
    pasta = raiz / "analises"
    pasta.mkdir(exist_ok=True)
    with duckdb.connect(str(raiz / "eleicoes.duckdb"), read_only=True, config={"memory_limit": LIMITE_MEMORIA}) as con:
        for candidato in candidatos:
            comparar(con, candidato)
            partes = {
                "secoes_por_eleicao": SQL_SECOES_POR_ELEICAO,
                "resumo": SQL_RESUMO,
                "no_outro_ano": SQL_NO_OUTRO_ANO,
                "locais_de_votacao": SQL_ESCOLAS,
                "por_uf": SQL_POR_UF,
                "indicio_no_nome_do_local": SQL_INDICIO,
                "tamanho": SQL_TAMANHO,
            }
            saida = {nome: _linhas(con, sql) for nome, sql in partes.items()}
            (pasta / f"unanimes_{candidato}.json").write_text(json.dumps(saida, ensure_ascii=False, indent=1, default=str), "utf-8")
            con.execute(
                "COPY (SELECT * FROM comparacao ORDER BY criterio, turno, uf, cd_municipio, zona, secao) "
                f"TO '{(pasta / f'unanimes_{candidato}.csv').as_posix()}' (HEADER, DELIMITER ';')"
            )
            print(f"\n######## candidato {candidato}")
            for titulo, linhas in saida.items():
                _imprimir(titulo, linhas)
    return 0
