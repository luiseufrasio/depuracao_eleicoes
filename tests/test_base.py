import zipfile

import duckdb
import pytest

from depuracao.base import FormatoInesperado, _contar_registros, converter_boletim, converter_generico

CAMPOS_COMUNS = (
    "DT_GERACAO;HH_GERACAO;ANO_ELEICAO;CD_TIPO_ELEICAO;NM_TIPO_ELEICAO;CD_PLEITO;DT_PLEITO;NR_TURNO;CD_ELEICAO;"
    "DS_ELEICAO;SG_UF;CD_MUNICIPIO;NM_MUNICIPIO;NR_ZONA;NR_SECAO;NR_LOCAL_VOTACAO;CD_CARGO_PERGUNTA;"
    "DS_CARGO_PERGUNTA;NR_PARTIDO;SG_PARTIDO;NM_PARTIDO;DT_BU_RECEBIDO;QT_APTOS;QT_COMPARECIMENTO;QT_ABSTENCOES;"
    "CD_TIPO_URNA;DS_TIPO_URNA;CD_TIPO_VOTAVEL;DS_TIPO_VOTAVEL;NR_VOTAVEL;NM_VOTAVEL;QT_VOTOS;NR_URNA_EFETIVADA;"
    "CD_CARGA_1_URNA_EFETIVADA;CD_CARGA_2_URNA_EFETIVADA;CD_FLASHCARD_URNA_EFETIVADA;DT_CARGA_URNA_EFETIVADA;"
    "DS_CARGO_PERGUNTA_SECAO;{agregadas};DT_ABERTURA;DT_ENCERRAMENTO;{biometria};DT_EMISSAO_BU;NR_JUNTA_APURADORA;"
    "NR_TURMA_APURADORA"
)
CAB_2022 = CAMPOS_COMUNS.format(agregadas="DS_AGREGADAS", biometria="QT_ELEITORES_BIOMETRIA_NH")
CAB_2026 = CAMPOS_COMUNS.format(agregadas="DS_SECOES_AGREGADAS", biometria="QT_ELEI_BIOM_SEM_HABILITACAO")

LINHA_2022 = (
    '31/10/2022;15:53:15;2022;0;Eleição Ordinária;407;30/10/2022;2;545;Geral;ZZ;29254;ABIDJÃ;1;1;1015;1;Presidente;'
    '-1;#NULO#;#NULO#;30/10/2022 18:10:05;77;43;34;1;APURADA;2;Branco;95;Branco;2;1273426;935.304.;528.014;538F2F43;'
    '28/09/2022 15:13:00;1 - 1;#NULO#;;;0;30/10/2022 17:17:32;1;1'
)
LINHA_2026 = (
    '26/10/2026;07:42:38;2026;0;Eleição Ordinária;3221;2026-10-25 00:00:00;2;6258;Geral;RR;3018;BOA VISTA;1;74;1775;1;'
    'Presidente;13;PT;PARTIDO DOS TRABALHADORES;2026-10-25 20:14:20;297;240;57;1;Apurada;1;Nominal;13;LULA;65;2226131;'
    '465.657.;495.818;BD909F11;2026-09-24 08:17:00;1 - 74;186;2026-10-25 07:00:01;2026-10-25 16:01:36;11;'
    '2026-10-25 16:03:17;-1;-1'
)


def _zip(tmp_path, nome, cabecalho, *linhas):
    caminho = tmp_path / nome
    corpo = "\n".join([cabecalho, *linhas]) + "\n"
    with zipfile.ZipFile(caminho, "w") as z:
        z.writestr(nome.replace(".zip", ".csv"), corpo.encode("latin-1"))
        z.writestr("leiame.pdf", b"x")
    return caminho


def _destino(tmp_path, *partes):
    d = tmp_path.joinpath("pq", "boletim", *partes, "dados.parquet")
    d.parent.mkdir(parents=True)
    return d


def _ler(con, destino):
    cur = con.execute(f"SELECT * FROM read_parquet('{destino.as_posix()}', hive_partitioning=false)")
    nomes = [d[0] for d in cur.description]
    return dict(zip(nomes, cur.fetchone()))


def test_esquema_de_2022(tmp_path):
    con = duckdb.connect()
    z = _zip(tmp_path, "bweb_2t_ZZ_1.zip", CAB_2022, LINHA_2022)
    d = _destino(tmp_path, "ano=2022", "turno=2", "uf=ZZ")
    converter_boletim(con, z, d, 2022, 2, "ZZ")
    r = _ler(con, d)
    assert r["nm_municipio"] == "ABIDJÃ"
    assert r["nr_partido"] is None and r["sg_partido"] is None
    assert r["secoes_agregadas"] is None and r["dt_abertura"] is None
    assert r["biometria_sem_habilitacao"] == 0
    assert r["dt_pleito"].isoformat() == "2022-10-30T00:00:00"
    assert r["ds_tipo_urna"] == "apurada" and r["votos"] == 2


def test_esquema_de_2026_converge_para_o_mesmo(tmp_path):
    con = duckdb.connect()
    z = _zip(tmp_path, "bweb_2t_RR_1.zip", CAB_2026, LINHA_2026)
    d = _destino(tmp_path, "ano=2026", "turno=2", "uf=RR")
    converter_boletim(con, z, d, 2026, 2, "RR")
    r = _ler(con, d)
    assert r["dt_pleito"].isoformat() == "2026-10-25T00:00:00"
    assert r["dt_abertura"].isoformat() == "2026-10-25T07:00:01"
    assert r["secoes_agregadas"] == "186" and r["biometria_sem_habilitacao"] == 11
    assert r["ds_tipo_urna"] == "apurada" and r["junta"] is None


def test_mantem_so_presidente_e_secao_sem_cargo(tmp_path):
    con = duckdb.connect()
    governador = LINHA_2026.replace(";1775;1;Presidente;", ";1775;3;Governador;")
    sem_cargo = LINHA_2026.replace(";1775;1;Presidente;", ";1775;-1;#NULO#;")
    assert governador != LINHA_2026 and sem_cargo != LINHA_2026
    z = _zip(tmp_path, "bweb_2t_RR_1.zip", CAB_2026, LINHA_2026, governador, sem_cargo)
    d = _destino(tmp_path, "ano=2026", "turno=2", "uf=RR")
    converter_boletim(con, z, d, 2026, 2, "RR")
    cargos = con.execute(f"SELECT cd_cargo FROM read_parquet('{d.as_posix()}', hive_partitioning=false) ORDER BY 1").fetchall()
    assert cargos == [(1,), (None,)]


def test_coluna_desconhecida_interrompe(tmp_path):
    z = _zip(tmp_path, "bweb_2t_RR_1.zip", CAB_2026 + ";COLUNA_NOVA", LINHA_2026 + ";x")
    d = _destino(tmp_path, "ano=2026", "turno=2", "uf=RR")
    with pytest.raises(FormatoInesperado, match="COLUNA_NOVA"):
        converter_boletim(duckdb.connect(), z, d, 2026, 2, "RR")


def test_arquivo_de_outra_uf_interrompe(tmp_path):
    z = _zip(tmp_path, "bweb_2t_RR_1.zip", CAB_2026, LINHA_2026)
    d = _destino(tmp_path, "ano=2026", "turno=2", "uf=AC")
    with pytest.raises(FormatoInesperado, match="não são de"):
        converter_boletim(duckdb.connect(), z, d, 2026, 2, "AC")
    assert not d.exists()


def test_data_em_formato_novo_interrompe(tmp_path):
    z = _zip(tmp_path, "bweb_2t_RR_1.zip", CAB_2026, LINHA_2026.replace("2026-09-24 08:17:00", "24.09.2026"))
    d = _destino(tmp_path, "ano=2026", "turno=2", "uf=RR")
    with pytest.raises(duckdb.Error):
        converter_boletim(duckdb.connect(), z, d, 2026, 2, "RR")


def test_generico_normaliza_coordenadas_de_ambos_os_formatos(tmp_path):
    con = duckdb.connect()
    cab = "DT_GERACAO;SG_UF;NR_LATITUDE;NR_LONGITUDE;NR_CEP;QT_ELEITOR_SECAO;NM_LOCAL_VOTACAO"
    z = _zip(tmp_path, "l.zip", cab, "1;AL;-9,5;-35,7;057084650;495;COLÉGIO", "1;AC;-9.8;-66.8;069945000;201;#NULO#")
    d = tmp_path / "pq" / "locais" / "ano=2026" / "dados.parquet"
    d.parent.mkdir(parents=True)
    converter_generico(con, z, d)
    linhas = con.execute(f"SELECT * FROM read_parquet('{d.as_posix()}', hive_partitioning=false) ORDER BY sg_uf DESC").fetchall()
    assert linhas == [("AL", -9.5, -35.7, "057084650", 495, "COLÉGIO"), ("AC", -9.8, -66.8, "069945000", 201, None)]


def test_contar_registros_com_e_sem_quebra_final(tmp_path):
    p = tmp_path / "a.csv"
    p.write_bytes(b"h\n1\n2\n")
    assert _contar_registros(p) == 2
    p.write_bytes(b"h\r\n1\r\n2")
    assert _contar_registros(p) == 2
