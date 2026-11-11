"""Relatório em PDF das seções com votos só para um candidato, com o histórico de 2022 e as vizinhas da mesma escola."""
import tempfile
from pathlib import Path

import duckdb

from .base import LIMITE_MEMORIA
from .conferir import _linhas
from .unanimes import CRITERIOS, comparar

AVISO = (
    "Unanimidade não é evidência de fraude. Ela é comum em seções pequenas e em comunidades muito coesas, como aldeias "
    "indígenas, quilombos e assentamentos. Este relatório lista anomalias a investigar e mostra, para cada seção, o que a "
    "mesma seção fez na eleição anterior e como votaram as outras seções da mesma escola. Os números vêm dos Boletins de "
    "Urna publicados pelo TSE e foram reconciliados com a totalização oficial."
)

# Abaixo deste percentual, uma vizinha na mesma escola é tratada como de perfil diferente.
LIMIAR_VIZINHA = 70.0

_SQL_FOCO = """
CREATE OR REPLACE TEMP TABLE foco AS
WITH escola_outro AS (
  SELECT uf, cd_municipio, zona, local_votacao, count(*) AS secoes,
         round(100.0 * sum(v_alvo) / nullif(sum(total), 0), 1) AS pct
  FROM sec WHERE ano = {outro} AND turno = {turno} GROUP BY ALL
)
SELECT c.uf, c.cd_municipio, c.municipio, c.zona, c.secao, c.local_{ano} AS local_votacao, c.nome_local_{ano} AS nome_local,
       c.aptos_{ano} AS aptos, c.votos_{ano} AS votos, c.alvo_{ano} AS alvo,
       c.existe_{outro} AS existia, c.comparavel, c.pct_{outro} AS pct_outro, c.local_{outro} AS local_outro,
       c.indicio_no_nome_do_local AS indicio, e.secoes AS escola_outro_secoes, e.pct AS escola_outro_pct
FROM comparacao c
LEFT JOIN escola_outro e ON e.uf = c.uf AND e.cd_municipio = c.cd_municipio AND e.zona = c.zona AND e.local_votacao = c.local_{ano}
WHERE c.criterio = '{criterio}' AND c.turno = {turno} AND c.marcada_{ano}
"""

_SQL_VIZINHAS = """
CREATE OR REPLACE TEMP TABLE vizinhas AS
SELECT f.uf, f.cd_municipio, f.zona, f.secao AS secao_foco, s.secao, s.aptos, s.total, s.v_alvo AS alvo,
       s.outros_nominais, s.total - s.v_alvo - s.outros_nominais AS brancos_nulos,
       round(100.0 * s.v_alvo / nullif(s.total, 0), 1) AS pct, s.secao = f.secao AS e_a_foco
FROM foco f
JOIN sec s ON s.ano = {ano} AND s.turno = {turno} AND s.uf = f.uf AND s.cd_municipio = f.cd_municipio
     AND s.zona = f.zona AND s.local_votacao = f.local_votacao
"""

# Coordenada -1 é o marcador de ausência do TSE. A tabela de 2026 repete linhas, por isso o GROUP BY.
_SQL_ENDERECOS = """
CREATE OR REPLACE TEMP TABLE enderecos AS
SELECT sg_uf AS uf, cd_municipio, nr_zona AS zona, nr_secao AS secao,
       arg_max(ds_endereco, nr_turno) AS endereco, arg_max(nm_bairro, nr_turno) AS bairro,
       arg_max(ds_tipo_secao_agregada, nr_turno) AS tipo_agregada, arg_max(nr_secao_principal, nr_turno) AS secao_principal,
       arg_max(CASE WHEN nr_latitude BETWEEN -35 AND 6 AND nr_longitude BETWEEN -75 AND -30 THEN nr_latitude END, nr_turno) AS lat,
       arg_max(CASE WHEN nr_latitude BETWEEN -35 AND 6 AND nr_longitude BETWEEN -75 AND -30 THEN nr_longitude END, nr_turno) AS long
FROM locais WHERE ano = {ano} GROUP BY ALL
"""

SQL_LISTA = """
WITH v AS (
  SELECT uf, cd_municipio, zona, secao_foco, count(*) - 1 AS vizinhas,
         min(pct) FILTER (WHERE NOT e_a_foco) AS pct_min_vizinha,
         median(pct) FILTER (WHERE NOT e_a_foco) AS pct_mediana_vizinha,
         count(*) FILTER (WHERE NOT e_a_foco AND pct < {limiar}) AS vizinhas_abaixo
  FROM vizinhas GROUP BY ALL
)
SELECT f.*, v.vizinhas, v.pct_min_vizinha, v.pct_mediana_vizinha, v.vizinhas_abaixo,
       e.endereco, e.bairro, e.tipo_agregada, e.secao_principal, e.lat, e.long
FROM foco f
JOIN v ON v.uf = f.uf AND v.cd_municipio = f.cd_municipio AND v.zona = f.zona AND v.secao_foco = f.secao
LEFT JOIN enderecos e ON e.uf = f.uf AND e.cd_municipio = f.cd_municipio AND e.zona = f.zona AND e.secao = f.secao
ORDER BY v.vizinhas_abaixo DESC, v.pct_min_vizinha NULLS LAST, f.votos DESC, f.uf, f.municipio, f.zona, f.secao
"""

SQL_FUNDO_MAPA = """
SELECT DISTINCT round(nr_latitude, 2) AS lat, round(nr_longitude, 2) AS long
FROM locais WHERE ano = {ano} AND nr_latitude BETWEEN -35 AND 6 AND nr_longitude BETWEEN -75 AND -30
"""


def montar(con: duckdb.DuckDBPyConnection, candidato: int, ano: int, turno: int, criterio: str) -> list[dict]:
    """Cria as tabelas temporárias e devolve uma linha por seção marcada, com histórico, vizinhança e endereço."""
    if criterio not in CRITERIOS:
        raise ValueError(f"critério desconhecido: {criterio}")
    outro = 2022 if ano == 2026 else 2026
    comparar(con, candidato)
    con.execute(_SQL_FOCO.format(ano=ano, outro=outro, turno=turno, criterio=criterio))
    con.execute(_SQL_VIZINHAS.format(ano=ano, turno=turno))
    con.execute(_SQL_ENDERECOS.format(ano=ano))
    return _linhas(con, SQL_LISTA.format(limiar=LIMIAR_VIZINHA))


def _historico(linha: dict, outro: int) -> str:
    if not linha["existia"]:
        return f"não existia em {outro}"
    pct = f"{linha['pct_outro']:.1f}%" if linha["pct_outro"] is not None else "sem votos"
    if linha["comparavel"]:
        return f"{pct} na mesma seção e local"
    if linha["local_outro"] != linha["local_votacao"]:
        return f"{pct}, em outro local"
    return f"{pct}, eleitorado mudou"


def _escola_outro(linha: dict, outro: int) -> str:
    if linha["escola_outro_secoes"] is None:
        return f"local sem seções em {outro}"
    return f"{linha['escola_outro_pct']:.1f}% em {linha['escola_outro_secoes']} seções"


def _vizinhanca(linha: dict) -> str:
    if linha["vizinhas"] == 0:
        return "seção única no local"
    if linha["vizinhas_abaixo"]:
        return f"{linha['vizinhas_abaixo']} de {linha['vizinhas']} vizinhas abaixo de {LIMIAR_VIZINHA:.0f}%"
    return f"{linha['vizinhas']} vizinhas, mínimo {linha['pct_min_vizinha']:.1f}%"


def resumo(linhas: list[dict], outro: int) -> list[tuple[str, int]]:
    ja_unanime = [l for l in linhas if l["comparavel"] and l["pct_outro"] == 100]
    return [
        ("Seções marcadas", len(linhas)),
        (f"Já eram unânimes em {outro}, na mesma seção e local", len(ja_unanime)),
        (f"Existiam em {outro} com outro resultado ou outro local", sum(1 for l in linhas if l["existia"]) - len(ja_unanime)),
        (f"Não existiam em {outro}", sum(1 for l in linhas if not l["existia"])),
        ("Seção única no local de votação", sum(1 for l in linhas if l["vizinhas"] == 0)),
        (f"Com vizinha na mesma escola abaixo de {LIMIAR_VIZINHA:.0f}%", sum(1 for l in linhas if l["vizinhas_abaixo"])),
        ("Local com nome indígena, quilombola ou de assentamento", sum(1 for l in linhas if l["indicio"] != "nao_identificado")),
        ("Seção agregada ou principal de agregação", sum(1 for l in linhas if _agregada(l))),
    ]


def _agregada(linha: dict) -> bool:
    return linha["tipo_agregada"] not in (None, "Principal") or linha["secao_principal"] is not None


def _mapa(fundo: list[dict], linhas: list[dict], destino: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 7))
    ax.scatter([f["long"] for f in fundo], [f["lat"] for f in fundo], s=0.3, c="#c8c8c8", linewidths=0, rasterized=True)
    com = [l for l in linhas if l["lat"] is not None]
    ax.scatter([l["long"] for l in com], [l["lat"] for l in com], s=28, c="#b30000", edgecolors="white", linewidths=0.6, zorder=3)
    ax.set_aspect("equal")
    ax.set_axis_off()
    sem = len(linhas) - len(com)
    ax.set_title(f"Locais das seções marcadas ({len(com)} com coordenada" + (f", {sem} sem)" if sem else ")"), fontsize=10)
    fig.tight_layout()
    fig.savefig(destino, dpi=200)
    plt.close(fig)


def _grafico_vizinhas(con: duckdb.DuckDBPyConnection, linhas: list[dict], destino: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    com_vizinhas = [l for l in linhas if l["vizinhas"] > 0]
    if not com_vizinhas:
        return
    fig, ax = plt.subplots(figsize=(10, max(3.0, 0.28 * len(com_vizinhas) + 1.2)))
    for i, l in enumerate(com_vizinhas):
        viz = con.execute(
            "SELECT pct, e_a_foco FROM vizinhas WHERE uf = ? AND cd_municipio = ? AND zona = ? AND secao_foco = ?",
            [l["uf"], l["cd_municipio"], l["zona"], l["secao"]],
        ).fetchall()
        outras = [p for p, foco in viz if not foco and p is not None]
        ax.scatter(outras, [i] * len(outras), s=22, facecolors="none", edgecolors="#555555", zorder=2)
        ax.scatter([100], [i], s=34, c="#b30000", zorder=3)
    rotulos = [f"{l['uf']} {l['municipio']} z{l['zona']} s{l['secao']} ({l['vizinhas']} viz.)" for l in com_vizinhas]
    ax.set_yticks(range(len(com_vizinhas)))
    ax.set_yticklabels(rotulos, fontsize=7)
    ax.invert_yaxis()
    ax.set_xlim(-2, 102)
    ax.set_xlabel("% dos votos da seção para o candidato (vermelho: seção marcada; cinza: outras seções do mesmo local)")
    ax.axvline(LIMIAR_VIZINHA, color="#999999", linestyle="--", linewidth=0.8)
    ax.grid(axis="x", linewidth=0.3)
    fig.tight_layout()
    fig.savefig(destino, dpi=200)
    plt.close(fig)


def gerar(raiz: Path, candidato: int, ano: int, turno: int, criterio: str) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    outro = 2022 if ano == 2026 else 2026
    pasta = raiz / "analises"
    pasta.mkdir(exist_ok=True)
    saida = pasta / f"relatorio_{candidato}_{ano}_{turno}t_{criterio}.pdf"
    estilos = getSampleStyleSheet()
    normal = ParagraphStyle("n", parent=estilos["Normal"], fontSize=8.5, leading=11)
    pequeno = ParagraphStyle("p", parent=normal, fontSize=7, leading=8.5)
    h1, h2 = estilos["Title"], estilos["Heading2"]
    grade = TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 7), ("LEADING", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ])

    def p(texto, estilo=pequeno):
        return Paragraph(str(texto), estilo)

    with duckdb.connect(str(raiz / "eleicoes.duckdb"), read_only=True, config={"memory_limit": LIMITE_MEMORIA}) as con, \
            tempfile.TemporaryDirectory(dir=pasta) as tmp:
        linhas = montar(con, candidato, ano, turno, criterio)
        fundo = _linhas(con, SQL_FUNDO_MAPA.format(ano=ano))
        mapa, grafico = Path(tmp) / "mapa.png", Path(tmp) / "vizinhas.png"
        _mapa(fundo, linhas, mapa)
        _grafico_vizinhas(con, linhas, grafico)

        descricao = "todos os votos, inclusive brancos e nulos" if criterio == "todos_os_votos" else "todos os votos nominais"
        corpo = [
            Paragraph(f"Seções em que {descricao} foram para o candidato {candidato}", h1),
            Paragraph(f"{turno}º turno de {ano}, eleição para Presidente, comparado com o {turno}º turno de {outro}", estilos["Heading3"]),
            Paragraph(AVISO, normal), Spacer(1, 0.4 * cm),
            Paragraph("Resumo", h2),
            Table([[p(t, normal), p(v, normal)] for t, v in resumo(linhas, outro)], colWidths=[12 * cm, 2 * cm], style=grade),
            Spacer(1, 0.4 * cm),
            Paragraph(
                f"Critérios: uma seção de {outro} só é considerada a mesma quando tem a mesma chave, o mesmo local de votação e "
                f"eleitorado até 15% diferente. Vizinhas são as outras seções do mesmo local de votação em {ano}. O indício "
                "indígena, quilombola ou de assentamento vem só do nome do local e deixa casos de fora.", normal),
            PageBreak(),
            Paragraph("Onde ficam", h2), Image(str(mapa), width=15 * cm, height=15 * cm),
            PageBreak(),
        ]
        if grafico.exists():
            altura = min(17 * cm, 0.6 * cm * sum(1 for l in linhas if l["vizinhas"] > 0) + 2.5 * cm)
            corpo += [Paragraph("Seção marcada contra as vizinhas da mesma escola", h2),
                      Image(str(grafico), width=25 * cm, height=altura), PageBreak()]

        cab = ["UF", "Município", "Zona", "Seção", "Local de votação", "Votos", "Aptos", f"Mesma seção em {outro}",
               f"Escola em {outro}", f"Vizinhas em {ano}", "Indício no nome"]
        dados = [[p(c) for c in cab]]
        for l in linhas:
            dados.append([p(l["uf"]), p(l["municipio"]), p(l["zona"]), p(l["secao"]), p(l["nome_local"] or ""),
                          p(f"{l['alvo']} de {l['votos']}"), p(l["aptos"]), p(_historico(l, outro)), p(_escola_outro(l, outro)),
                          p(_vizinhanca(l)), p(l["indicio"].replace("_", " "))])
        larguras = [0.9, 3.2, 1.0, 1.0, 5.5, 1.8, 1.2, 3.6, 3.0, 3.6, 2.0]
        corpo += [Paragraph("Lista das seções, com as vizinhas mais discrepantes primeiro", h2),
                  Table(dados, colWidths=[w * cm for w in larguras], style=grade, repeatRows=1), PageBreak(),
                  Paragraph("Detalhe por seção", h2)]

        cab_viz = ["Seção", "Aptos", "Votos (com brancos e nulos)", f"Candidato {candidato}", "Outros nominais",
                   "Brancos e nulos", f"% {candidato}"]
        for l in linhas:
            coords = f"{l['lat']:.5f}, {l['long']:.5f}" if l["lat"] is not None else "sem coordenada"
            agreg = ""
            if _agregada(l):
                agreg = f" Seção agregada: {l['tipo_agregada']}" + (f", principal {l['secao_principal']}." if l["secao_principal"] else ".")
            corpo += [
                Paragraph(f"{l['uf']} · {l['municipio']} · zona {l['zona']} · seção {l['secao']}", estilos["Heading4"]),
                Paragraph(f"{l['nome_local'] or 'local sem nome'} (nº {l['local_votacao']}). {l['endereco'] or ''}, "
                          f"{l['bairro'] or ''}. Coordenadas: {coords}.{agreg}", normal),
                Paragraph(f"Em {outro}: {_historico(l, outro)}. Escola em {outro}: {_escola_outro(l, outro)}.", normal),
            ]
            viz = con.execute(
                "SELECT secao, aptos, total, alvo, outros_nominais, brancos_nulos, pct, e_a_foco FROM vizinhas "
                "WHERE uf = ? AND cd_municipio = ? AND zona = ? AND secao_foco = ? ORDER BY secao",
                [l["uf"], l["cd_municipio"], l["zona"], l["secao"]],
            ).fetchall()
            tabela = [[p(c) for c in cab_viz]]
            for secao, aptos, total, alvo, outros, bn, pct, foco in viz:
                tabela.append([p(f"{secao} (marcada)" if foco else secao), p(aptos), p(total), p(alvo), p(outros), p(bn),
                               p("-" if pct is None else f"{pct:.1f}%")])
            corpo += [Table(tabela, colWidths=[2.5 * cm, 1.5 * cm, 4 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm, 1.8 * cm],
                            style=grade, repeatRows=1), Spacer(1, 0.3 * cm)]

        SimpleDocTemplate(str(saida), pagesize=landscape(A4), leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                          topMargin=1.2 * cm, bottomMargin=1.2 * cm,
                          title=f"Seções unânimes, candidato {candidato}, {ano}").build(corpo)
    return saida
