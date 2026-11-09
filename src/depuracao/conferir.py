"""Reconcilia a base com os arquivos oficiais de totalização e mede a coerência interna."""
import json
from pathlib import Path

import duckdb

from .catalogo import ANOS, QTD_UFS

SQL_COBERTURA = """
SELECT ano, turno, count(DISTINCT uf) AS ufs, count(*) AS linhas, sum(votos) AS votos
FROM boletim GROUP BY ALL ORDER BY ALL
"""

# Votos nominais por zona e candidato: o nível mais fino comum às duas fontes.
# Candidato com votos na urna pode faltar na totalização só se a candidatura não era válida
# (renúncia, inaptidão etc.); qualquer outra diferença bloqueia.
_CTE_COMPARACAO = """
WITH presentes AS (SELECT DISTINCT ano, turno, uf FROM boletim),
b AS (
  SELECT ano, turno, uf, cd_municipio, zona, cd_cargo, nr_votavel AS candidato, sum(votos) AS v
  FROM boletim WHERE ds_tipo_votavel = 'nominal' GROUP BY ALL
),
r AS (
  SELECT ano_eleicao AS ano, nr_turno AS turno, sg_uf AS uf, cd_municipio, nr_zona AS zona, cd_cargo,
         nr_candidato AS candidato, sum(qt_votos_nominais) AS v
  FROM conf_munzona JOIN presentes p ON p.ano = ano_eleicao AND p.turno = nr_turno AND p.uf = sg_uf
  WHERE cd_cargo = 1
  GROUP BY ALL
),
situacao AS (
  -- Inválida só se todos os registros daquele número o forem.
  SELECT ano, uf, cd_cargo, nr_candidato AS candidato, string_agg(DISTINCT situacao, ' / ') AS situacao,
         bool_and(situacao IS NOT NULL AND situacao <> 'APTO' AND situacao NOT LIKE 'DEFERIDO%') AS invalida
  FROM conf_candidatos GROUP BY ALL
),
j AS (
  SELECT ano, turno, uf, cd_municipio, zona, cd_cargo, candidato, b.v AS vb, r.v AS vr
  FROM b FULL JOIN r USING (ano, turno, uf, cd_municipio, zona, cd_cargo, candidato)
),
comp AS (
  SELECT j.*, s.situacao, coalesce(s.invalida, false) AS invalida
  FROM j LEFT JOIN situacao s ON s.ano = j.ano AND s.cd_cargo = j.cd_cargo AND s.candidato = j.candidato
       AND s.uf = CASE WHEN j.cd_cargo = 1 THEN 'BR' ELSE j.uf END
)
"""

SQL_MUNZONA = _CTE_COMPARACAO + """
SELECT ano, turno, cd_cargo, count(*) AS chaves,
       count(*) FILTER (WHERE vb IS NOT NULL AND vr IS NOT NULL AND vb <> vr) AS valor_diferente,
       count(*) FILTER (WHERE vb IS NULL AND vr > 0) AS so_no_oficial,
       count(*) FILTER (WHERE vr IS NULL AND vb > 0 AND NOT invalida) AS sem_totalizacao_sem_explicacao,
       count(*) FILTER (WHERE vr IS NULL AND vb > 0 AND invalida) AS sem_totalizacao_cand_invalida,
       coalesce(sum(vb) FILTER (WHERE vr IS NULL AND invalida), 0) AS votos_cand_invalida,
       sum(coalesce(vb, 0)) AS votos_boletim, sum(coalesce(vr, 0)) AS votos_oficial
FROM comp GROUP BY ALL ORDER BY ALL
"""

SQL_SEM_TOTALIZACAO = _CTE_COMPARACAO + """
SELECT ano, turno, cd_cargo, situacao, count(DISTINCT (uf, candidato)) AS candidatos, sum(vb) AS votos
FROM comp WHERE vr IS NULL AND vb > 0 GROUP BY ALL ORDER BY ALL
"""

# Presidente, seção a seção, incluindo brancos (95) e nulos (96).
SQL_SECAO = """
WITH presentes AS (SELECT DISTINCT ano, turno, uf FROM boletim),
b AS (
  SELECT ano, turno, uf, cd_municipio, zona, secao, nr_votavel AS votavel, sum(votos) AS v
  FROM boletim WHERE cd_cargo = 1 GROUP BY ALL
),
r AS (
  SELECT ano_eleicao AS ano, nr_turno AS turno, sg_uf AS uf, cd_municipio, nr_zona AS zona, nr_secao AS secao,
         nr_votavel AS votavel, sum(qt_votos) AS v
  FROM conf_secao JOIN presentes p ON p.ano = ano_eleicao AND p.turno = nr_turno AND p.uf = sg_uf
  GROUP BY ALL
)
SELECT ano, turno, count(*) AS chaves,
       count(*) FILTER (WHERE b.v IS NULL) AS so_no_oficial,
       count(*) FILTER (WHERE r.v IS NULL) AS so_no_boletim,
       count(*) FILTER (WHERE coalesce(b.v, 0) <> coalesce(r.v, 0)) AS divergentes,
       sum(abs(coalesce(b.v, 0) - coalesce(r.v, 0))) AS votos_diferenca,
       sum(coalesce(b.v, 0)) AS votos_boletim, sum(coalesce(r.v, 0)) AS votos_oficial
FROM b FULL JOIN r USING (ano, turno, uf, cd_municipio, zona, secao, votavel)
GROUP BY ALL ORDER BY ALL
"""

# Regras internas do próprio boletim. Violações são achados a examinar, não erros de carga.
SQL_COERENCIA = """
WITH c AS (
  SELECT ano, turno, uf, cd_municipio, zona, secao, cd_cargo,
         max(aptos) AS aptos, min(aptos) AS aptos_min, max(comparecimento) AS comp, min(comparecimento) AS comp_min,
         max(abstencoes) AS abst, sum(votos) AS votos, count(*) FILTER (WHERE votos IS NULL) AS votos_nulos,
         max(cd_tipo_urna) AS tipo
  FROM boletim GROUP BY ALL
)
SELECT ano, turno, cd_cargo, count(*) AS secoes_cargo,
       count(*) FILTER (WHERE aptos <> aptos_min OR comp <> comp_min) AS aptos_ou_comp_inconstante,
       count(*) FILTER (WHERE aptos <> comp + abst) AS aptos_dif_comp_mais_abst,
       count(*) FILTER (WHERE comp > aptos) AS comp_acima_de_aptos,
       count(*) FILTER (WHERE votos_nulos > 0) AS com_votos_ausentes,
       count(*) FILTER (WHERE votos > comp) AS votos_acima_do_limite,
       count(*) FILTER (WHERE tipo <> 1) AS urna_nao_apurada
FROM c GROUP BY ALL ORDER BY ALL
"""


def _linhas(con, sql: str) -> list[dict]:
    cur = con.execute(sql)
    nomes = [d[0] for d in cur.description]
    return [dict(zip(nomes, linha)) for linha in cur.fetchall()]


def _imprimir(titulo: str, linhas: list[dict]) -> None:
    print(f"\n== {titulo}")
    if not linhas:
        print("(vazio)")
        return
    print(" | ".join(linhas[0]))
    for linha in linhas:
        print(" | ".join(str(v) for v in linha.values()))


def conferir(raiz: Path, completo: bool = True) -> int:
    with duckdb.connect(str(raiz / "eleicoes.duckdb"), read_only=True) as con:
        cobertura = _linhas(con, SQL_COBERTURA)
        munzona = _linhas(con, SQL_MUNZONA)
        sem_totalizacao = _linhas(con, SQL_SEM_TOTALIZACAO)
        secao = _linhas(con, SQL_SECAO)
        coerencia = _linhas(con, SQL_COERENCIA)

    relatorio = {
        "cobertura": cobertura,
        "munzona": munzona,
        "sem_totalizacao": sem_totalizacao,
        "secao": secao,
        "coerencia": coerencia,
    }
    (raiz / "conferencia.json").write_text(json.dumps(relatorio, ensure_ascii=False, indent=1, default=str), "utf-8")
    for titulo, linhas in relatorio.items():
        _imprimir(titulo, linhas)

    bloqueios = []
    if completo:
        faltando = {(a, t) for a in ANOS for t in (1, 2)} - {(c["ano"], c["turno"]) for c in cobertura if c["ufs"] == QTD_UFS}
        if faltando:
            bloqueios.append(f"cobertura incompleta em {sorted(faltando)}")
    for campo in ("valor_diferente", "so_no_oficial", "sem_totalizacao_sem_explicacao"):
        if total := sum(l[campo] for l in munzona):
            bloqueios.append(f"munzona: {total} chaves em {campo}")
    if total := sum(l["divergentes"] for l in secao):
        bloqueios.append(f"secao: {total} chaves divergentes")

    print("\nRESULTADO:", "OK, totais reconciliados" if not bloqueios else "; ".join(bloqueios))
    return 1 if bloqueios else 0
