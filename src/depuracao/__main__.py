"""Linha de comando: python -m depuracao <comando>."""
import argparse
import sys
from collections import Counter
from pathlib import Path

from . import base, baixar, catalogo, conferir, relatorio, unanimes


def cmd_baixar(args) -> int:
    recursos = catalogo.listar(tuple(args.anos))
    if args.ufs:
        recursos = [r for r in recursos if r.pertence_a(set(args.ufs))]
    if args.camadas:
        recursos = [r for r in recursos if r.camada in args.camadas]
    print(f"{len(recursos)} arquivos no catálogo selecionado", flush=True)
    resultados = baixar.baixar_todos(recursos, args.dados, args.paralelos)
    print(dict(Counter(r.situacao for r in resultados)))
    falhas = [r for r in resultados if r.situacao == "falhou"]
    for f in falhas:
        print(f"FALHOU {f.recurso.url}: {f.detalhe}", file=sys.stderr)
    return 1 if falhas else 0


def cmd_base(args) -> int:
    base.construir(args.dados, set(args.ufs) if args.ufs else None, args.refazer)
    return 0


def cmd_conferir(args) -> int:
    return conferir.conferir(args.dados, completo=not args.parcial)


def cmd_unanimes(args) -> int:
    return unanimes.relatorio(args.dados, args.candidatos)


def cmd_relatorio(args) -> int:
    saida = relatorio.gerar(args.dados, args.candidato, args.ano, args.turno, args.criterio)
    print(f"relatório em {saida}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="depuracao")
    parser.add_argument("--dados", type=Path, default=Path("data"), help="pasta dos dados")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("baixar", help="baixa CSVs de boletim, cadastro e arquivos de conferência")
    p.add_argument("--anos", type=int, nargs="+", default=list(catalogo.ANOS))
    p.add_argument("--ufs", nargs="+", help="limita às UFs informadas (arquivos nacionais sempre entram)")
    p.add_argument("--camadas", nargs="+", choices=["boletim", "locais", "perfil", "conferencia"])
    p.add_argument("--paralelos", type=int, default=4)
    p.set_defaults(func=cmd_baixar)

    p = sub.add_parser("base", help="converte os ZIPs baixados em Parquet e cria as views")
    p.add_argument("--ufs", nargs="+", help="limita às UFs informadas")
    p.add_argument("--refazer", action="store_true", help="reconverte mesmo o que já está atualizado")
    p.set_defaults(func=cmd_base)

    p = sub.add_parser("conferir", help="reconcilia a base com os arquivos oficiais")
    p.add_argument("--parcial", action="store_true", help="não exige todas as UFs")
    p.set_defaults(func=cmd_conferir)

    p = sub.add_parser("unanimes", help="compara entre 2022 e 2026 as seções com votos só para um candidato")
    p.add_argument("--candidatos", type=int, nargs="+", default=[13, 22], help="números dos candidatos")
    p.set_defaults(func=cmd_unanimes)

    p = sub.add_parser("relatorio", help="PDF das seções com votos só para um candidato, com histórico e vizinhas da escola")
    p.add_argument("--candidato", type=int, default=13)
    p.add_argument("--ano", type=int, default=2026, choices=list(catalogo.ANOS))
    p.add_argument("--turno", type=int, default=2, choices=[1, 2])
    p.add_argument("--criterio", default="todos_os_votos", choices=list(unanimes.CRITERIOS))
    p.set_defaults(func=cmd_relatorio)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
