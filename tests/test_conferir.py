import duckdb

from depuracao.conferir import SQL_MUNZONA


def _resultado(boletim, oficial, candidatos, extra_oficial=()):
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE boletim (ano INT, turno INT, uf VARCHAR, cd_municipio INT, zona INT, cd_cargo INT, "
        "nr_votavel INT, votos INT, ds_tipo_votavel VARCHAR)"
    )
    con.execute(
        "CREATE TABLE conf_munzona (ano_eleicao INT, nr_turno INT, sg_uf VARCHAR, cd_municipio INT, nr_zona INT, "
        "cd_cargo INT, nr_candidato INT, qt_votos_nominais INT)"
    )
    con.execute("CREATE TABLE conf_candidatos (ano INT, uf VARCHAR, cd_cargo INT, nr_candidato INT, situacao VARCHAR)")
    for c, v in boletim:
        con.execute("INSERT INTO boletim VALUES (2026, 1, 'AC', 1, 1, 1, ?, ?, 'nominal')", [c, v])
    for c, v in oficial:
        con.execute("INSERT INTO conf_munzona VALUES (2026, 1, 'AC', 1, 1, 1, ?, ?)", [c, v])
    for cargo, c, v in extra_oficial:
        con.execute("INSERT INTO conf_munzona VALUES (2026, 1, 'AC', 1, 1, ?, ?, ?)", [cargo, c, v])
    for c, s in candidatos:
        con.execute("INSERT INTO conf_candidatos VALUES (2026, 'BR', 1, ?, ?)", [c, s])
    cur = con.execute(SQL_MUNZONA)
    nomes = [d[0] for d in cur.description]
    return dict(zip(nomes, cur.fetchone()))


def test_totais_iguais_nao_acusam_nada():
    r = _resultado([(13, 10)], [(13, 10)], [(13, "DEFERIDO")])
    assert (r["valor_diferente"], r["so_no_oficial"], r["sem_totalizacao_sem_explicacao"]) == (0, 0, 0)


def test_contagem_diferente_e_acusada_mesmo_com_candidatura_invalida():
    assert _resultado([(13, 10)], [(13, 9)], [(13, "RENÚNCIA")])["valor_diferente"] == 1


def test_candidatura_invalida_explica_ausencia_na_totalizacao():
    r = _resultado([(28, 7)], [], [(28, "RENÚNCIA")])
    assert (r["sem_totalizacao_cand_invalida"], r["sem_totalizacao_sem_explicacao"], r["votos_cand_invalida"]) == (1, 0, 7)


def test_candidatura_valida_nao_pode_faltar_na_totalizacao():
    r = _resultado([(28, 7)], [], [(28, "DEFERIDO")])
    assert (r["sem_totalizacao_cand_invalida"], r["sem_totalizacao_sem_explicacao"]) == (0, 1)


def test_candidato_fora_do_cadastro_nao_e_explicado():
    assert _resultado([(28, 7)], [], [])["sem_totalizacao_sem_explicacao"] == 1


def test_voto_oficial_sem_boletim_e_acusado():
    assert _resultado([(45, 1)], [(45, 1), (13, 5)], [(13, "DEFERIDO")])["so_no_oficial"] == 1


def test_outros_cargos_da_totalizacao_sao_ignorados():
    con_outro_cargo = _resultado([(13, 10)], [(13, 10)], [(13, "DEFERIDO")], extra_oficial=[(6, 1234, 50)])
    assert con_outro_cargo["so_no_oficial"] == 0


def test_situacao_de_outro_candidato_nao_explica():
    assert _resultado([(28, 7)], [], [(99, "RENÚNCIA")])["sem_totalizacao_sem_explicacao"] == 1


def test_numero_com_registro_valido_nao_e_explicado():
    r = _resultado([(28, 7)], [], [(28, "RENÚNCIA"), (28, "DEFERIDO")])
    assert (r["sem_totalizacao_cand_invalida"], r["sem_totalizacao_sem_explicacao"]) == (0, 1)
