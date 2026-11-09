"""Downloader com retomada, conferência de integridade e manifesto."""
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .catalogo import USER_AGENT, Recurso

BLOCO = 1024 * 1024
TIMEOUT = 60
TENTATIVAS = 6
ERROS_DE_REDE = (urllib.error.URLError, TimeoutError, ConnectionError, OSError)


class ArquivoCorrompido(Exception):
    pass


@dataclass
class Resultado:
    recurso: Recurso
    situacao: str  # "baixado", "ja_ok" ou "falhou"
    detalhe: str = ""


class Manifesto:
    """Registro em JSON de cada arquivo baixado, regravado de forma atômica."""

    def __init__(self, caminho: Path):
        self.caminho = caminho
        self._trava = threading.Lock()
        self.itens: dict[str, dict] = json.loads(caminho.read_text("utf-8")) if caminho.exists() else {}

    def registrar(self, chave: str, item: dict) -> None:
        with self._trava:
            self.itens[chave] = item
            tmp = self.caminho.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.itens, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")
            os.replace(tmp, self.caminho)


def extrair_sha512(texto: str, nome: str) -> str:
    m = re.fullmatch(r"([0-9a-fA-F]{128})\s+\*?(\S+)\s*", texto)
    if not m:
        raise ArquivoCorrompido(f"Arquivo de hash inválido para {nome}")
    if m.group(2) != nome:
        raise ArquivoCorrompido(f"Hash se refere a {m.group(2)}, esperava {nome}")
    return m.group(1).lower()


def sha512_de(caminho: Path) -> str:
    h = hashlib.sha512()
    with caminho.open("rb") as f:
        while bloco := f.read(BLOCO):
            h.update(bloco)
    return h.hexdigest()


def _abrir(url: str, inicio: int = 0):
    cabecalhos = {"User-Agent": USER_AGENT}
    if inicio:
        cabecalhos["Range"] = f"bytes={inicio}-"
    return urllib.request.urlopen(urllib.request.Request(url, headers=cabecalhos), timeout=TIMEOUT)


def _transferir(url: str, parcial: Path) -> None:
    """Baixa para o arquivo parcial, continuando do ponto em que parou."""
    inicio = parcial.stat().st_size if parcial.exists() else 0
    try:
        resposta = _abrir(url, inicio)
    except urllib.error.HTTPError as e:
        if e.code != 416:
            raise
        # O parcial já é do tamanho total ou é inválido; a conferência final decide.
        return
    with resposta:
        if resposta.status == 206:
            esperado = int(resposta.headers["Content-Range"].rsplit("/", 1)[1])
            modo = "ab"
        else:
            esperado = int(resposta.headers.get("Content-Length") or -1)
            modo = "wb"
        with parcial.open(modo) as f:
            while bloco := resposta.read(BLOCO):
                f.write(bloco)
    obtido = parcial.stat().st_size
    if esperado >= 0 and obtido != esperado:
        raise ConnectionError(f"Transferência incompleta: {obtido} de {esperado} bytes")


def _conferir(parcial: Path, nome: str, sha_oficial: str | None) -> tuple[str, str]:
    """Devolve o SHA-512 calculado e como o arquivo foi conferido."""
    sha = sha512_de(parcial)
    if sha_oficial:
        if sha != sha_oficial:
            raise ArquivoCorrompido(f"SHA-512 de {nome} difere do oficial")
        return sha, "sha512_oficial"
    try:
        with zipfile.ZipFile(parcial) as z:
            ruim = z.testzip()
    except zipfile.BadZipFile as e:
        raise ArquivoCorrompido(f"{nome} não é um ZIP válido") from e
    if ruim:
        raise ArquivoCorrompido(f"{nome}: membro corrompido {ruim}")
    return sha, "crc_do_zip"


def baixar_um(recurso: Recurso, raiz: Path, manifesto: Manifesto, pausa_base: float = 2.0) -> Resultado:
    relativo = Path("raw") / str(recurso.ano) / recurso.camada / recurso.nome
    chave = relativo.as_posix()
    destino = raiz / relativo
    item = manifesto.itens.get(chave)
    if item and item["url"] == recurso.url and destino.exists() and destino.stat().st_size == item["bytes"]:
        return Resultado(recurso, "ja_ok")

    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + ".part")
    ultimo = ""
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            sha_oficial = None
            if recurso.url_sha512:
                with _abrir(recurso.url_sha512) as r:
                    sha_oficial = extrair_sha512(r.read().decode("ascii"), recurso.nome)
            _transferir(recurso.url, parcial)
            sha, conferencia = _conferir(parcial, recurso.nome, sha_oficial)
            os.replace(parcial, destino)
            manifesto.registrar(
                chave,
                {
                    "url": recurso.url,
                    "bytes": destino.stat().st_size,
                    "sha512": sha,
                    "conferencia": conferencia,
                    "baixado_em": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                },
            )
            return Resultado(recurso, "baixado", conferencia)
        except ArquivoCorrompido as e:
            ultimo = str(e)
            parcial.unlink(missing_ok=True)  # recomeça do zero: retomar repetiria o dado ruim
        except ERROS_DE_REDE as e:
            ultimo = repr(e)
        if tentativa < TENTATIVAS:
            time.sleep(pausa_base * 2 ** (tentativa - 1))
    return Resultado(recurso, "falhou", ultimo)


def baixar_todos(recursos: list[Recurso], raiz: Path, paralelos: int = 4) -> list[Resultado]:
    raiz.mkdir(parents=True, exist_ok=True)
    manifesto = Manifesto(raiz / "manifesto.json")
    resultados = []
    with cf.ThreadPoolExecutor(paralelos) as ex:
        tarefas = [ex.submit(baixar_um, r, raiz, manifesto) for r in recursos]
        for n, tarefa in enumerate(cf.as_completed(tarefas), 1):
            res = tarefa.result()
            resultados.append(res)
            print(f"[{n}/{len(recursos)}] {res.situacao:7} {res.recurso.nome} {res.detalhe}", flush=True)
    return resultados
