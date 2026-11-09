"""Lista os arquivos a baixar a partir da API CKAN do Portal de Dados Abertos do TSE."""
import json
import re
import urllib.request
from dataclasses import dataclass

API = "https://dadosabertos.tse.jus.br/api/3/action/package_show?id={}"
USER_AGENT = "depuracao-eleicoes/0.1 (dados abertos)"
ANOS = (2022, 2026)
QTD_UFS = 28  # 26 estados + DF + exterior (ZZ)


@dataclass(frozen=True)
class Recurso:
    camada: str
    ano: int
    url: str
    url_sha512: str | None = None

    @property
    def nome(self) -> str:
        return self.url.rsplit("/", 1)[-1]

    def pertence_a(self, ufs: set[str]) -> bool:
        """Arquivos nacionais sempre pertencem; os de UF só se a UF estiver no filtro."""
        m = re.search(r"_([A-Z]{2})(?:_\d+)?\.zip$", self.nome) or re.search(r"_\dt_([A-Z]{2})_", self.nome)
        return m is None or m.group(1) == "BR" or m.group(1) in ufs


def _pacote(nome: str) -> list[dict]:
    req = urllib.request.Request(API.format(nome), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["result"]["resources"]


def selecionar(
    ano: int, boletim: list[dict], eleitorado: list[dict], resultados: list[dict], candidatos: list[dict]
) -> list[Recurso]:
    """Filtra os recursos de interesse e falha se o catálogo não tiver o tamanho esperado."""
    urls_boletim = {r["url"] for r in boletim}
    recursos = [
        Recurso("boletim", ano, u, f"{u}.sha512" if f"{u}.sha512" in urls_boletim else None)
        for u in sorted(urls_boletim)
        if u.endswith(".zip")
    ]
    _exigir("boletim", sum(1 for _ in recursos), QTD_UFS * 2, ano)
    _exigir("hash de boletim", sum(1 for r in recursos if r.url_sha512), QTD_UFS * 2, ano)

    locais = [r["url"] for r in eleitorado if f"eleitorado_local_votacao_{ano}.zip" in r["url"]]
    _exigir("locais", len(locais), 1, ano)
    perfil = [r["url"] for r in eleitorado if re.search(rf"perfil_eleitor_secao_{ano}_[A-Z]{{2}}\.zip$", r["url"])]
    _exigir("perfil", len(perfil), QTD_UFS, ano)

    munzona = [r["url"] for r in resultados if re.search(rf"votacao_candidato_munzona_{ano}\.zip$", r["url"])]
    _exigir("votação por município e zona", len(munzona), 1, ano)
    secao_br = [r["url"] for r in resultados if r["url"].endswith(f"votacao_secao_{ano}_BR.zip")]
    _exigir("votação por seção (presidente)", len(secao_br), 1, ano)

    consulta = [r["url"] for r in candidatos if r["url"].endswith((f"consulta_cand_{ano}.zip", f"consulta_cand_complementar_{ano}.zip"))]
    _exigir("cadastro de candidatos e complementar", len(consulta), 2, ano)

    recursos += [Recurso("locais", ano, u) for u in locais]
    recursos += [Recurso("perfil", ano, u) for u in sorted(perfil)]
    recursos += [Recurso("conferencia", ano, u) for u in munzona + secao_br + consulta]
    return recursos


def _exigir(oque: str, obtido: int, esperado: int, ano: int) -> None:
    if obtido != esperado:
        raise RuntimeError(f"Catálogo {ano}: esperava {esperado} arquivo(s) de {oque}, encontrei {obtido}")


def listar(anos=ANOS) -> list[Recurso]:
    recursos = []
    for ano in anos:
        recursos += selecionar(
            ano,
            _pacote(f"resultados-{ano}-boletim-de-urna"),
            _pacote(f"eleitorado-{ano}"),
            _pacote(f"resultados-{ano}"),
            _pacote(f"candidatos-{ano}"),
        )
    return recursos
