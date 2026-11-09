"""Converte os ZIPs do TSE em Parquet com esquema único e cria as views no DuckDB."""
import csv
import os
import re
import tempfile
import zipfile
from pathlib import Path

import duckdb

ALIASES_BOLETIM = {
    "DS_AGREGADAS": "DS_SECOES_AGREGADAS",
    "QT_ELEITORES_BIOMETRIA_NH": "QT_ELEI_BIOM_SEM_HABILITACAO",
}
FORMATOS_DATA = ["%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%Y-%m-%d"]
SEM_DADO_TEXTO = "('', '#NULO#', '#NULO', '#NE#', '#NE')"
TEXTO_COMO_CODIGO = {"NR_CEP", "NR_TELEFONE_LOCAL"}
DESCARTAR = {"DT_GERACAO", "HH_GERACAO", "NR_TELEFONE_LOCAL"}
# Só o necessário para a conferência; dados pessoais do cadastro (CPF, e-mail) ficam de fora.
COLUNAS_CAND = {"ANO_ELEICAO", "SG_UF", "CD_CARGO", "SQ_CANDIDATO", "NR_CANDIDATO", "NM_URNA_CANDIDATO", "DS_SITUACAO_CANDIDATURA"}
COLUNAS_CAND_COMP = {"ANO_ELEICAO", "SQ_CANDIDATO", "ST_CANDIDATO_INSERIDO_URNA", "DS_SITUACAO_CANDIDATO_TOT"}


LIMITE_MEMORIA = "12GB"


class FormatoInesperado(Exception):
    pass


def _txt(c: str) -> str:
    return f"CASE WHEN {c} IN {SEM_DADO_TEXTO} THEN NULL ELSE {c} END"


def _int(c: str) -> str:
    # O leiame do TSE define -1 como #NULO e -3 como #NE nos campos numéricos.
    return f"CASE WHEN {_txt(c)} IS NULL OR {c} IN ('-1', '-3') THEN NULL ELSE CAST({c} AS BIGINT) END"


def _dth(c: str) -> str:
    formatos = ", ".join(f"'{f}'" for f in FORMATOS_DATA)
    return f"strptime({_txt(c)}, [{formatos}])"


def _coord(c: str) -> str:
    return f"CAST(replace({_txt(c)}, ',', '.') AS DOUBLE)"


COLUNAS_BOLETIM = [
    ("ano", _int, "ANO_ELEICAO"),
    ("turno", _int, "NR_TURNO"),
    ("cd_pleito", _int, "CD_PLEITO"),
    ("cd_eleicao", _int, "CD_ELEICAO"),
    ("ds_eleicao", _txt, "DS_ELEICAO"),
    ("dt_pleito", _dth, "DT_PLEITO"),
    ("uf", _txt, "SG_UF"),
    ("cd_municipio", _int, "CD_MUNICIPIO"),
    ("nm_municipio", _txt, "NM_MUNICIPIO"),
    ("zona", _int, "NR_ZONA"),
    ("secao", _int, "NR_SECAO"),
    ("local_votacao", _int, "NR_LOCAL_VOTACAO"),
    ("cd_cargo", _int, "CD_CARGO_PERGUNTA"),
    ("ds_cargo", _txt, "DS_CARGO_PERGUNTA"),
    ("cargo_secao", _txt, "DS_CARGO_PERGUNTA_SECAO"),
    ("nr_partido", _int, "NR_PARTIDO"),
    ("sg_partido", _txt, "SG_PARTIDO"),
    ("nm_partido", _txt, "NM_PARTIDO"),
    ("cd_tipo_votavel", _int, "CD_TIPO_VOTAVEL"),
    ("ds_tipo_votavel", lambda c: f"lower({_txt(c)})", "DS_TIPO_VOTAVEL"),
    ("nr_votavel", _int, "NR_VOTAVEL"),
    ("nm_votavel", _txt, "NM_VOTAVEL"),
    ("votos", _int, "QT_VOTOS"),
    ("aptos", _int, "QT_APTOS"),
    ("comparecimento", _int, "QT_COMPARECIMENTO"),
    ("abstencoes", _int, "QT_ABSTENCOES"),
    ("biometria_sem_habilitacao", _int, "QT_ELEI_BIOM_SEM_HABILITACAO"),
    ("cd_tipo_urna", _int, "CD_TIPO_URNA"),
    ("ds_tipo_urna", lambda c: f"lower({_txt(c)})", "DS_TIPO_URNA"),
    ("nr_urna", _int, "NR_URNA_EFETIVADA"),
    ("carga_1", _txt, "CD_CARGA_1_URNA_EFETIVADA"),
    ("carga_2", _txt, "CD_CARGA_2_URNA_EFETIVADA"),
    ("flashcard", _txt, "CD_FLASHCARD_URNA_EFETIVADA"),
    ("secoes_agregadas", _txt, "DS_SECOES_AGREGADAS"),
    ("dt_carga", _dth, "DT_CARGA_URNA_EFETIVADA"),
    ("dt_abertura", _dth, "DT_ABERTURA"),
    ("dt_encerramento", _dth, "DT_ENCERRAMENTO"),
    ("dt_emissao_bu", _dth, "DT_EMISSAO_BU"),
    ("dt_bu_recebido", _dth, "DT_BU_RECEBIDO"),
    ("junta", _int, "NR_JUNTA_APURADORA"),
    ("turma", _int, "NR_TURMA_APURADORA"),
]
# Metadados lidos mas não levados à base.
IGNORADAS_BOLETIM = {"DT_GERACAO", "HH_GERACAO", "CD_TIPO_ELEICAO", "NM_TIPO_ELEICAO"}
# Seção sem nenhum comparecimento vem sem cargo; é mantida para não sumir do eleitorado apto.
FILTRO_PRESIDENTE = f"{_int('CD_CARGO_PERGUNTA')} IS NULL OR {_int('CD_CARGO_PERGUNTA')} = 1"


def _cabecalho(csv_path: Path) -> list[str]:
    with csv_path.open(encoding="latin-1", newline="") as f:
        return next(csv.reader(f, delimiter=";"))


def _contar_registros(csv_path: Path) -> int:
    """Conta linhas nos bytes, sem passar pelo leitor de CSV, para conferir a conversão."""
    linhas = 0
    ultimo = b"\n"
    with csv_path.open("rb") as f:
        while bloco := f.read(1 << 24):
            linhas += bloco.count(b"\n")
            ultimo = bloco[-1:]
    return linhas + (ultimo != b"\n") - 1


def _sql_str(p: Path) -> str:
    return "'" + p.as_posix().replace("'", "''") + "'"


def _leitor(csvs: list[Path], renomear: dict[str, str] | None = None) -> str:
    lista = ", ".join(_sql_str(c) for c in csvs)
    fonte = (
        f"read_csv([{lista}], delim=';', quote='\"', header=true, all_varchar=true, "
        "encoding='latin-1', strict_mode=true, union_by_name=true)"
    )
    if not renomear:
        return fonte
    pares = ", ".join(f"{velho} AS {novo}" for velho, novo in renomear.items())
    return f"(SELECT * RENAME ({pares}) FROM {fonte})"


def _gravar(con, select: str, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    con.execute(f"COPY ({select}) TO {_sql_str(tmp)} (FORMAT parquet, COMPRESSION zstd)")
    os.replace(tmp, destino)


def _atualizado(destino: Path, fonte: Path) -> bool:
    return destino.exists() and destino.stat().st_mtime >= fonte.stat().st_mtime


def _exigir_contagem(con, destino: Path, esperado: int, origem: str) -> int:
    obtido = con.execute(f"SELECT count(*) FROM read_parquet({_sql_str(destino)})").fetchone()[0]
    if obtido != esperado:
        destino.unlink()
        raise FormatoInesperado(f"{origem}: {esperado} registros no CSV e {obtido} no Parquet")
    return obtido


def _extrair(zip_path: Path, pasta: Path, filtro) -> list[Path]:
    with zipfile.ZipFile(zip_path) as z:
        nomes = [n for n in z.namelist() if n.lower().endswith(".csv") and filtro(n)]
        if not nomes:
            raise FormatoInesperado(f"{zip_path.name}: nenhum CSV selecionado")
        return [Path(z.extract(n, pasta)) for n in nomes]


def converter_boletim(con, zip_path: Path, destino: Path, ano: int, turno: int, uf: str) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destino.parent, prefix="tmp_") as tmp:
        csvs = _extrair(zip_path, Path(tmp), lambda n: True)
        if len(csvs) != 1:
            raise FormatoInesperado(f"{zip_path.name}: esperava 1 CSV, achei {len(csvs)}")
        cabecalho = _cabecalho(csvs[0])
        renomear = {c: ALIASES_BOLETIM[c] for c in cabecalho if c in ALIASES_BOLETIM}
        canonico = {renomear.get(c, c) for c in cabecalho}
        esperado = {origem for _, _, origem in COLUNAS_BOLETIM} | IGNORADAS_BOLETIM
        if canonico != esperado:
            raise FormatoInesperado(
                f"{zip_path.name}: colunas novas {sorted(canonico - esperado)}, ausentes {sorted(esperado - canonico)}"
            )
        expressoes = ", ".join(f"{fn(origem)} AS {nome}" for nome, fn, origem in COLUNAS_BOLETIM)
        leitor = _leitor(csvs, renomear)
        select = f"SELECT {expressoes} FROM {leitor} WHERE {FILTRO_PRESIDENTE} ORDER BY cd_municipio, zona, secao, cd_cargo"
        _gravar(con, select, destino)
        lidos, selecionados = con.execute(
            f"SELECT count(*), count(*) FILTER (WHERE {FILTRO_PRESIDENTE}) FROM {leitor}"
        ).fetchone()
        registros = _contar_registros(csvs[0])
        if lidos != registros:
            destino.unlink()
            raise FormatoInesperado(f"{zip_path.name}: {registros} registros no CSV e {lidos} lidos")
        _exigir_contagem(con, destino, selecionados, zip_path.name)

    # Sem hive_partitioning: senão ano/turno/uf viriam do caminho e a checagem nunca falharia.
    divergentes = con.execute(
        f"SELECT count(*) FROM read_parquet({_sql_str(destino)}, hive_partitioning=false) "
        "WHERE ano IS DISTINCT FROM ? OR turno IS DISTINCT FROM ? OR uf IS DISTINCT FROM ?",
        [ano, turno, uf],
    ).fetchone()[0]
    if divergentes:
        destino.unlink()
        raise FormatoInesperado(f"{zip_path.name}: {divergentes} linhas não são de {ano}/{turno}º turno/{uf}")


def _expressao_generica(coluna: str) -> str:
    nome = coluna.lower()
    if coluna in ("NR_LATITUDE", "NR_LONGITUDE"):
        return f"{_coord(coluna)} AS {nome}"
    if coluna.startswith("DT_"):
        return f"{_dth(coluna)} AS {nome}"
    if coluna.startswith(("QT_", "CD_", "NR_", "AA_", "ANO_")) and coluna not in TEXTO_COMO_CODIGO:
        return f"{_int(coluna)} AS {nome}"
    return f"{_txt(coluna)} AS {nome}"


def converter_generico(con, zip_path: Path, destino: Path, filtro=lambda n: True, so_colunas: set[str] | None = None) -> None:
    """Converte locais, perfil e arquivos de conferência, tipando cada coluna pelo prefixo."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destino.parent, prefix="tmp_") as tmp:
        csvs = _extrair(zip_path, Path(tmp), filtro)
        cabecalhos = {tuple(_cabecalho(c)) for c in csvs}
        if len(cabecalhos) != 1:
            raise FormatoInesperado(f"{zip_path.name}: CSVs internos com colunas diferentes")
        colunas = [c for c in cabecalhos.pop() if c not in DESCARTAR and (so_colunas is None or c in so_colunas)]
        select = f"SELECT {', '.join(_expressao_generica(c) for c in colunas)} FROM {_leitor(csvs)}"
        _gravar(con, select, destino)
        _exigir_contagem(con, destino, sum(_contar_registros(c) for c in csvs), zip_path.name)


def _unico(pasta: Path, padrao: str) -> Path | None:
    achados = sorted(pasta.glob(padrao))
    if len(achados) > 1:
        raise FormatoInesperado(f"Mais de um arquivo para {padrao}: {[a.name for a in achados]}")
    return achados[0] if achados else None


def construir(raiz: Path, ufs: set[str] | None = None, refazer: bool = False) -> None:
    pq = raiz / "parquet"
    pq.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(config={"memory_limit": LIMITE_MEMORIA, "temp_directory": str(pq / "_duckdb_tmp")})

    def etapa(fonte: Path | None, destino: Path, acao) -> None:
        if fonte is None or (not refazer and _atualizado(destino, fonte)):
            return
        print(f"convertendo {fonte.name}", flush=True)
        acao(destino)

    for ano_dir in sorted((raiz / "raw").glob("20*")):
        ano = int(ano_dir.name)
        bol = ano_dir / "boletim"
        ufs_presentes = sorted({m.group(1) for z in bol.glob("*.zip") if (m := re.match(r"bweb_\dt_([A-Z]{2})_", z.name))})
        for uf in ufs_presentes:
            if ufs and uf not in ufs:
                continue
            for turno in (1, 2):
                fonte = _unico(bol, f"bweb_{turno}t_{uf}_*.zip")
                destino = pq / "boletim_presidente" / f"ano={ano}" / f"turno={turno}" / f"uf={uf}" / "dados.parquet"
                etapa(fonte, destino, lambda d, f=fonte, t=turno, u=uf: converter_boletim(con, f, d, ano, t, u))

        fonte = _unico(ano_dir / "locais", f"eleitorado_local_votacao_{ano}.zip")
        etapa(fonte, pq / "locais" / f"ano={ano}" / "dados.parquet", lambda d, f=fonte: converter_generico(con, f, d))

        for z in sorted((ano_dir / "perfil").glob("*.zip")):
            uf = z.stem.rsplit("_", 1)[1]
            if ufs and uf not in ufs:
                continue
            destino = pq / "perfil" / f"ano={ano}" / f"uf={uf}" / "dados.parquet"
            etapa(z, destino, lambda d, f=z: converter_generico(con, f, d))

        conf = ano_dir / "conferencia"
        fonte = _unico(conf, f"votacao_candidato_munzona_{ano}.zip")
        etapa(
            fonte,
            pq / "conf_munzona" / f"ano={ano}" / "dados.parquet",
            lambda d, f=fonte: converter_generico(con, f, d, lambda n: n.endswith("_BRASIL.csv")),
        )
        fonte = _unico(conf, f"votacao_secao_{ano}_BR.zip")
        etapa(fonte, pq / "conf_secao" / f"ano={ano}" / "dados.parquet", lambda d, f=fonte: converter_generico(con, f, d))
        for prefixo, pasta, colunas in (
            ("consulta_cand", "conf_cand", COLUNAS_CAND),
            ("consulta_cand_complementar", "conf_cand_comp", COLUNAS_CAND_COMP),
        ):
            fonte = _unico(conf, f"{prefixo}_{ano}.zip")
            etapa(
                fonte,
                pq / pasta / f"ano={ano}" / "dados.parquet",
                lambda d, f=fonte, c=colunas: converter_generico(con, f, d, lambda n: n.endswith("_BRASIL.csv"), c),
            )
    con.close()
    criar_views(raiz)


VIEWS = {
    "boletim": "boletim_presidente/ano=*/turno=*/uf=*/dados.parquet",
    "locais": "locais/ano=*/dados.parquet",
    "perfil": "perfil/ano=*/uf=*/dados.parquet",
    "conf_munzona": "conf_munzona/ano=*/dados.parquet",
    "conf_secao": "conf_secao/ano=*/dados.parquet",
    "conf_cand": "conf_cand/ano=*/dados.parquet",
    "conf_cand_comp": "conf_cand_comp/ano=*/dados.parquet",
}

# Situação de quem estava de fato na urna. O cadastro principal de 2026 vem sem situação (#NE),
# e um número pode ter dois registros quando há substituição.
SQL_CONF_CANDIDATOS = """
CREATE OR REPLACE VIEW conf_candidatos AS
SELECT DISTINCT c.ano_eleicao AS ano, c.sg_uf AS uf, c.cd_cargo, c.nr_candidato, c.nm_urna_candidato,
       coalesce(comp.ds_situacao_candidato_tot, c.ds_situacao_candidatura) AS situacao
FROM conf_cand c LEFT JOIN conf_cand_comp comp ON comp.ano_eleicao = c.ano_eleicao AND comp.sq_candidato = c.sq_candidato
WHERE coalesce(comp.st_candidato_inserido_urna, 'SIM') = 'SIM'
"""


def criar_views(raiz: Path) -> None:
    with duckdb.connect(str(raiz / "eleicoes.duckdb")) as con:
        for nome, padrao in VIEWS.items():
            caminho = (raiz / "parquet").resolve().as_posix() + "/" + padrao
            # O ano já é coluna do arquivo; a partição do caminho só evita ler tudo.
            con.execute(
                f"CREATE OR REPLACE VIEW {nome} AS SELECT * "
                f"FROM read_parquet('{caminho}', hive_partitioning=true, union_by_name=true)"
            )
        con.execute(SQL_CONF_CANDIDATOS)
