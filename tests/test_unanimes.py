import duckdb
import pytest

from depuracao.unanimes import SQL_ESCOLAS, SQL_RESUMO, comparar


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute(
        "CREATE TABLE boletim (ano INT, turno INT, uf VARCHAR, cd_municipio INT, nm_municipio VARCHAR, zona INT, secao INT, "
        "local_votacao INT, aptos INT, cd_tipo_urna INT, cd_cargo INT, ds_tipo_votavel VARCHAR, nr_votavel INT, votos INT)"
    )
    c.execute(
        "CREATE TABLE locais (ano INT, nr_turno INT, sg_uf VARCHAR, cd_municipio INT, nr_zona INT, nr_secao INT, "
        "nm_local_votacao VARCHAR, ds_tipo_local VARCHAR)"
    )
    return c


def _secao(con, ano, secao, votos, local=1, aptos=100, urna=1, turno=2):
    for numero, v in votos.items():
        tipo = {95: "branco", 96: "nulo"}.get(numero, "nominal")
        con.execute("INSERT INTO boletim VALUES (?, ?, 'MA', 1, 'X', 1, ?, ?, ?, ?, 1, ?, ?, ?)", [ano, turno, secao, local, aptos, urna, tipo, numero, v])


def _linha(con, criterio, secao):
    cur = con.execute("SELECT * FROM comparacao WHERE criterio = ? AND secao = ?", [criterio, secao])
    linha = cur.fetchone()
    return dict(zip([d[0] for d in cur.description], linha)) if linha else None


def test_marcada_nos_dois_anos_e_comparavel(con):
    _secao(con, 2022, 1, {13: 90})
    _secao(con, 2026, 1, {13: 80}, aptos=110)
    comparar(con, 13)
    r = _linha(con, "todos_os_votos", 1)
    assert r["marcada_2022"] and r["marcada_2026"] and r["comparavel"]


def test_branco_impede_o_criterio_estrito_mas_nao_o_nominal(con):
    _secao(con, 2022, 1, {13: 90, 95: 1})
    comparar(con, 13)
    assert _linha(con, "todos_os_votos", 1) is None
    assert _linha(con, "entre_nominais", 1)["marcada_2022"]


def test_marcada_em_um_ano_mostra_percentual_do_outro(con):
    _secao(con, 2022, 1, {13: 30, 22: 70})
    _secao(con, 2026, 1, {13: 80})
    comparar(con, 13)
    r = _linha(con, "todos_os_votos", 1)
    assert not r["marcada_2022"] and r["marcada_2026"] and r["pct_2022"] == 30.0


def test_outro_candidato_nao_marca(con):
    _secao(con, 2022, 1, {22: 90})
    comparar(con, 13)
    assert _linha(con, "todos_os_votos", 1) is None


def test_secao_inexistente_no_outro_ano(con):
    _secao(con, 2026, 1, {13: 80})
    comparar(con, 13)
    r = _linha(con, "todos_os_votos", 1)
    assert not r["existe_2022"] and r["existe_2026"] and not r["comparavel"]


def test_urna_anulada_nao_e_marcada(con):
    _secao(con, 2022, 1, {13: 90}, urna=4)
    comparar(con, 13)
    assert _linha(con, "todos_os_votos", 1) is None


@pytest.mark.parametrize("mudanca", [{"local": 2}, {"aptos": 200}])
def test_mesma_chave_com_local_ou_eleitorado_diferente_nao_e_comparavel(con, mudanca):
    _secao(con, 2022, 1, {13: 90})
    _secao(con, 2026, 1, {13: 80}, **mudanca)
    comparar(con, 13)
    assert not _linha(con, "todos_os_votos", 1)["comparavel"]


def test_resumo_separa_mais_menos_e_diferentes(con):
    _secao(con, 2022, 1, {13: 90})
    _secao(con, 2026, 1, {13: 90})
    _secao(con, 2022, 2, {13: 90})
    _secao(con, 2026, 2, {13: 40, 22: 40})
    _secao(con, 2026, 3, {13: 90})
    comparar(con, 13)
    cur = con.execute(SQL_RESUMO)
    nomes = [d[0] for d in cur.description]
    linhas = {(l["criterio"], l["turno"]): l for l in (dict(zip(nomes, x)) for x in cur.fetchall())}
    r = linhas[("todos_os_votos", "2")]
    assert (r["em_2022"], r["em_2026"], r["nos_dois"], r["so_2022"], r["so_2026"], r["so_2026_sem_a_secao_em_2022"]) == (2, 2, 1, 1, 1, 1)


def test_nome_do_local_e_indicio_indigena(con):
    _secao(con, 2026, 1, {13: 80})
    con.execute("INSERT INTO locais VALUES (2026, 2, 'MA', 1, 1, 1, 'ESCOLA ALDEIA X', 'Escola')")
    comparar(con, 13)
    r = _linha(con, "todos_os_votos", 1)
    assert r["indicio_no_nome_do_local"] == "indigena" and r["tipo_local"] == "Escola"


def test_escola_conta_uma_vez_e_compara_entre_anos(con):
    _secao(con, 2022, 1, {13: 90}, local=7)
    _secao(con, 2022, 2, {13: 90}, local=7)
    _secao(con, 2026, 1, {13: 90}, local=7)
    comparar(con, 13)
    assert con.execute(SQL_ESCOLAS).fetchall()[0][2:] == (1, 1, 1)
