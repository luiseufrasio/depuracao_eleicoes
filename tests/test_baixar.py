import hashlib
import http.server
import io
import threading
import zipfile

import pytest

from depuracao.baixar import ArquivoCorrompido, Manifesto, baixar_um, extrair_sha512
from depuracao.catalogo import Recurso

CONTEUDO = b"urna" * 500_000
SHA = hashlib.sha512(CONTEUDO).hexdigest()


class Servidor(http.server.BaseHTTPRequestHandler):
    """Serve CONTEUDO com suporte a Range; a primeira resposta pode ser cortada no meio."""

    cortar_primeira = False
    sha = SHA
    pedidos_range: list[str] = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.endswith(".sha512"):
            corpo = f"{type(self).sha}  a.zip\n".encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
            return
        inicio = 0
        if rng := self.headers.get("Range"):
            type(self).pedidos_range.append(rng)
            inicio = int(rng.removeprefix("bytes=").rstrip("-"))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {inicio}-{len(CONTEUDO)-1}/{len(CONTEUDO)}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(CONTEUDO) - inicio))
        self.end_headers()
        if type(self).cortar_primeira:
            type(self).cortar_primeira = False
            self.wfile.write(CONTEUDO[: len(CONTEUDO) // 2])
            self.wfile.flush()
            self.connection.close()
            return
        self.wfile.write(CONTEUDO[inicio:])


@pytest.fixture
def servidor():
    class H(Servidor):
        pedidos_range = []

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield H, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _recurso(base):
    return Recurso("boletim", 2022, f"{base}/a.zip", f"{base}/a.zip.sha512")


def test_baixa_confere_e_registra(servidor, tmp_path):
    _, base = servidor
    m = Manifesto(tmp_path / "m.json")
    res = baixar_um(_recurso(base), tmp_path, m, pausa_base=0)
    assert (res.situacao, res.detalhe) == ("baixado", "sha512_oficial")
    assert (tmp_path / "raw/2022/boletim/a.zip").read_bytes() == CONTEUDO
    assert m.itens["raw/2022/boletim/a.zip"]["sha512"] == SHA


def test_retoma_apos_queda_de_conexao(servidor, tmp_path):
    h, base = servidor
    h.cortar_primeira = True
    res = baixar_um(_recurso(base), tmp_path, Manifesto(tmp_path / "m.json"), pausa_base=0)
    assert res.situacao == "baixado"
    assert (tmp_path / "raw/2022/boletim/a.zip").read_bytes() == CONTEUDO
    assert len(h.pedidos_range) == 1


def test_segunda_execucao_nao_baixa_de_novo(servidor, tmp_path):
    _, base = servidor
    m = Manifesto(tmp_path / "m.json")
    baixar_um(_recurso(base), tmp_path, m, pausa_base=0)
    assert baixar_um(_recurso(base), tmp_path, Manifesto(tmp_path / "m.json"), pausa_base=0).situacao == "ja_ok"


def test_hash_divergente_falha_e_nao_deixa_arquivo(servidor, tmp_path):
    h, base = servidor
    h.sha = "0" * 128
    res = baixar_um(_recurso(base), tmp_path, Manifesto(tmp_path / "m.json"), pausa_base=0)
    assert res.situacao == "falhou"
    assert "difere do oficial" in res.detalhe
    assert not (tmp_path / "raw/2022/boletim/a.zip").exists()


def test_sem_hash_oficial_exige_zip_valido(tmp_path):
    from depuracao.baixar import _conferir

    p = tmp_path / "x.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.csv", "1;2")
    p.write_bytes(buf.getvalue())
    assert _conferir(p, "x.zip", None)[1] == "crc_do_zip"
    p.write_bytes(b"nao e zip")
    with pytest.raises(ArquivoCorrompido):
        _conferir(p, "x.zip", None)


def test_extrair_sha512():
    assert extrair_sha512(f"{SHA}  a.zip\n", "a.zip") == SHA
    with pytest.raises(ArquivoCorrompido):
        extrair_sha512(f"{SHA}  b.zip\n", "a.zip")


@pytest.mark.parametrize(
    "nome,esperado",
    [
        ("bweb_1t_AC_051020221321.zip", True),
        ("bweb_1t_SP_051020221321.zip", False),
        ("perfil_eleitor_secao_2022_AC.zip", True),
        ("perfil_eleitor_secao_2022_SP.zip", False),
        ("votacao_secao_2022_BR.zip", True),
        ("eleitorado_local_votacao_2022.zip", True),
    ],
)
def test_filtro_por_uf(nome, esperado):
    assert Recurso("x", 2022, f"http://h/{nome}").pertence_a({"AC"}) is esperado
