import duckdb
import pytest

from depuracao import relatorio
from depuracao.relatorio import _historico, _vizinhanca, montar, resumo


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute(
        "CREATE TABLE boletim (ano INT, turno INT, uf VARCHAR, cd_municipio INT, nm_municipio VARCHAR, zona INT, secao INT, "
        "local_votacao INT, aptos INT, cd_tipo_urna INT, cd_cargo INT, ds_tipo_votavel VARCHAR, nr_votavel INT, votos INT)"
    )
    c.execute(
        "CREATE TABLE locais (ano INT, nr_turno INT, sg_uf VARCHAR, cd_municipio INT, nr_zona INT, nr_secao INT, "
        "nm_local_votacao VARCHAR, ds_tipo_local VARCHAR, ds_endereco VARCHAR, nm_bairro VARCHAR, "
        "ds_tipo_secao_agregada VARCHAR, nr_secao_principal INT, nr_latitude DOUBLE, nr_longitude DOUBLE)"
    )
    return c


def _secao(con, ano, secao, votos, local=1, aptos=100, turno=2):
    for numero, v in votos.items():
        tipo = {95: "branco", 96: "nulo"}.get(numero, "nominal")
        con.execute("INSERT INTO boletim VALUES (?, ?, 'PA', 1, 'X', 1, ?, ?, ?, 1, 1, ?, ?, ?)", [ano, turno, secao, local, aptos, tipo, numero, v])


def _local(con, ano, secao, lat=-1.0, long=-1.0, agregada="Principal", principal=None):
    con.execute("INSERT INTO locais VALUES (?, 2, 'PA', 1, 1, ?, 'ESCOLA ALDEIA', 'Escola', 'RUA A', 'CENTRO', ?, ?, ?, ?)",
                [ano, secao, agregada, principal, lat, long])


def test_vizinhas_da_mesma_escola_e_historico(con):
    _secao(con, 2022, 1, {13: 90, 22: 10})
    _secao(con, 2026, 1, {13: 80})
    _secao(con, 2026, 2, {13: 20, 22: 60})
    _secao(con, 2026, 3, {13: 95, 22: 1}, local=9)
    _local(con, 2026, 1, lat=-1.5, long=-50.0)
    linhas = montar(con, 13, 2026, 2, "todos_os_votos")
    assert [l["secao"] for l in linhas] == [1]
    l = linhas[0]
    assert (l["vizinhas"], l["vizinhas_abaixo"], l["pct_min_vizinha"]) == (1, 1, 25.0)
    assert _historico(l, 2022) == "90.0% na mesma seção e local"
    assert _vizinhanca(l) == "1 de 1 vizinhas abaixo de 70%"
    assert (l["lat"], l["long"], l["endereco"], l["indicio"]) == (-1.5, -50.0, "RUA A", "indigena")
    assert l["escola_outro_pct"] == 90.0 and l["escola_outro_secoes"] == 1


def test_coordenada_menos_um_e_ausencia(con):
    _secao(con, 2026, 1, {13: 80})
    _local(con, 2026, 1)
    l = montar(con, 13, 2026, 2, "todos_os_votos")[0]
    assert l["lat"] is None and _vizinhanca(l) == "seção única no local"
    assert _historico(l, 2022) == "não existia em 2022"


def test_secao_agregada_entra_no_resumo(con):
    _secao(con, 2026, 1, {13: 80})
    _local(con, 2026, 1, agregada="Agregada", principal=7)
    linhas = montar(con, 13, 2026, 2, "todos_os_votos")
    assert dict(resumo(linhas, 2022))["Seção agregada ou principal de agregação"] == 1


def test_criterio_desconhecido(con):
    with pytest.raises(ValueError):
        montar(con, 13, 2026, 2, "x")


def test_gera_pdf(tmp_path, monkeypatch):
    pytest.importorskip("reportlab")
    pytest.importorskip("matplotlib")
    con = duckdb.connect(str(tmp_path / "eleicoes.duckdb"))
    con.execute(
        "CREATE TABLE boletim (ano INT, turno INT, uf VARCHAR, cd_municipio INT, nm_municipio VARCHAR, zona INT, secao INT, "
        "local_votacao INT, aptos INT, cd_tipo_urna INT, cd_cargo INT, ds_tipo_votavel VARCHAR, nr_votavel INT, votos INT)"
    )
    con.execute(
        "CREATE TABLE locais (ano INT, nr_turno INT, sg_uf VARCHAR, cd_municipio INT, nr_zona INT, nr_secao INT, "
        "nm_local_votacao VARCHAR, ds_tipo_local VARCHAR, ds_endereco VARCHAR, nm_bairro VARCHAR, "
        "ds_tipo_secao_agregada VARCHAR, nr_secao_principal INT, nr_latitude DOUBLE, nr_longitude DOUBLE)"
    )
    _secao(con, 2026, 1, {13: 80})
    _secao(con, 2026, 2, {13: 50, 22: 50})
    _local(con, 2026, 1, lat=-3.0, long=-52.0)
    con.close()
    monkeypatch.setattr(relatorio, "LIMITE_MEMORIA", "1GB")
    saida = relatorio.gerar(tmp_path, 13, 2026, 2, "todos_os_votos")
    assert saida.exists() and saida.read_bytes().startswith(b"%PDF")
    assert not list(tmp_path.glob("analises/tmp*"))
