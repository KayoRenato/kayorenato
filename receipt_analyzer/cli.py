"""
CLI entry point.

Usage:
    python -m receipt_analyzer.cli <access_key_or_url> [--normalize] [--save]

    python -m receipt_analyzer.cli history "Açúcar Cristal" [--brand União]
    python -m receipt_analyzer.cli stores  "Açúcar Cristal" [--brand União]
    python -m receipt_analyzer.cli trend   [--cnpj 12345678000100]

Examples:
    python -m receipt_analyzer.cli scan "2625 1120 ..."
    python -m receipt_analyzer.cli scan "2625 1120 ..." --normalize --save
    python -m receipt_analyzer.cli history "Açúcar Cristal" --brand União
    python -m receipt_analyzer.cli stores  "Óleo de Soja"
    python -m receipt_analyzer.cli trend
"""

import argparse
from decimal import Decimal

from receipt_analyzer.db.database import make_engine, make_session_factory, init_db
from receipt_analyzer.db.repository import (
    save_receipt,
    save_normalized_items,
    get_price_history,
    compare_stores,
    get_basket_trend,
)
from receipt_analyzer.scraper.nfce_scraper import scrape
from receipt_analyzer.scraper.normalizer import normalize


def _fmt(d: Decimal) -> str:
    return f"R$ {d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_unit(d: Decimal, unit: str) -> str:
    return f"R$ {d:,.4f}/{unit}".replace(",", "X").replace(".", ",").replace("X", ".")


def cmd_scan(args) -> None:
    print(f"\nFetching: {args.source[:70]}...\n")
    receipt = scrape(args.source, timeout_ms=args.timeout)

    print(f"Store   : {receipt.store.name}")
    print(f"CNPJ    : {receipt.store.cnpj}")
    print(f"Local   : {receipt.store.city} / {receipt.store.state}")
    print(f"Data    : {receipt.issued_at.strftime('%d/%m/%Y %H:%M')}")
    print(f"Total   : {_fmt(receipt.total)}")
    print(f"\n{'─'*72}")
    print(f"{'#':<4} {'Descrição':<35} {'Qtd':>6} {'Un':>4} {'Vl Unit':>12} {'Total':>12}")
    print(f"{'─'*72}")

    for i, item in enumerate(receipt.raw_items, 1):
        print(
            f"{i:<4} {item.description[:34]:<35} {item.quantity:>6.3f} "
            f"{item.unit:>4} {_fmt(item.unit_price):>12} {_fmt(item.total_price):>12}"
        )
    print(f"{'─'*72}")
    print(f"  {len(receipt.raw_items)} itens")

    normalized = None
    if args.normalize or args.save:
        print(f"\nNormalizando {len(receipt.raw_items)} itens com Claude API...")
        normalized = normalize(receipt.raw_items)
        receipt.normalized_items = normalized

        print(f"\n{'─'*90}")
        print(f"{'Nome Canônico':<30} {'Marca':<15} {'Categoria':<14} {'Un Base':>7} {'Preço/Un':>14}")
        print(f"{'─'*90}")
        for n in normalized:
            brand = n.brand or "—"
            print(
                f"{n.canonical_name[:29]:<30} {brand[:14]:<15} {n.category[:13]:<14} "
                f"{n.base_unit:>7} {_fmt_unit(n.price_per_base_unit, n.base_unit):>14}"
            )
        print(f"{'─'*90}")

    if args.save:
        engine = make_engine()
        init_db(engine)
        Session = make_session_factory(engine)
        with Session() as session:
            orm_r = save_receipt(session, receipt)
            if orm_r is None:
                print("\nCupom já salvo anteriormente (chave duplicada).")
            else:
                if normalized:
                    save_normalized_items(session, orm_r, normalized)
                session.commit()
                print(f"\nSalvo: receipt id={orm_r.id}")


def cmd_history(args) -> None:
    engine = make_engine()
    init_db(engine)
    Session = make_session_factory(engine)
    with Session() as session:
        history = get_price_history(session, args.product, brand=args.brand or None)

    if not history:
        print(f"Nenhum registro para '{args.product}'.")
        return

    print(f"\nHistórico de preços — {args.product}" + (f" [{args.brand}]" if args.brand else ""))
    print(f"\n{'─'*72}")
    print(f"{'Data':<12} {'Loja':<25} {'Cidade':<15} {'Preço/Un Base':>15}")
    print(f"{'─'*72}")
    for p in history:
        print(
            f"{p.date.strftime('%d/%m/%Y'):<12} {p.store_name[:24]:<25} "
            f"{p.store_city[:14]:<15} {_fmt_unit(p.price_per_base_unit, p.base_unit):>15}"
        )

    first = history[0].price_per_base_unit
    last = history[-1].price_per_base_unit
    if first > 0:
        change = ((last - first) / first) * 100
        arrow = "▲" if change > 0 else "▼"
        print(f"\n  Variação total: {arrow} {abs(change):.1f}%  ({_fmt_unit(first, history[0].base_unit)} → {_fmt_unit(last, history[-1].base_unit)})")


def cmd_stores(args) -> None:
    engine = make_engine()
    init_db(engine)
    Session = make_session_factory(engine)
    with Session() as session:
        stores = compare_stores(session, args.product, brand=args.brand or None)

    if not stores:
        print(f"Nenhum registro para '{args.product}'.")
        return

    print(f"\nComparação de lojas — {args.product}" + (f" [{args.brand}]" if args.brand else ""))
    print(f"\n{'─'*72}")
    print(f"{'Loja':<25} {'Cidade':<15} {'Última data':<13} {'Preço/Un Base':>15}")
    print(f"{'─'*72}")
    for i, s in enumerate(stores):
        tag = " ← mais barato" if i == 0 else ""
        print(
            f"{s.store_name[:24]:<25} {s.store_city[:14]:<15} "
            f"{s.latest_date.strftime('%d/%m/%Y'):<13} "
            f"{_fmt_unit(s.price_per_base_unit, s.base_unit):>15}{tag}"
        )

    if len(stores) > 1:
        diff = stores[-1].price_per_base_unit - stores[0].price_per_base_unit
        pct = (diff / stores[0].price_per_base_unit) * 100
        print(f"\n  Diferença mais caro vs. mais barato: +{pct:.1f}%")


def cmd_trend(args) -> None:
    engine = make_engine()
    init_db(engine)
    Session = make_session_factory(engine)
    with Session() as session:
        trend = get_basket_trend(session, store_cnpj=args.cnpj or None)

    if not trend:
        print("Nenhum dado de gasto encontrado.")
        return

    months_pt = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]
    print("\nTendência de gasto mensal")
    print(f"\n{'─'*50}")
    print(f"{'Mês':<10} {'Cupons':>7} {'Total':>14}")
    print(f"{'─'*50}")
    for t in trend:
        label = f"{months_pt[t.month - 1]}/{t.year}"
        print(f"{label:<10} {t.receipt_count:>7} {_fmt(t.total):>14}")

    if len(trend) > 1:
        first_total = trend[0].total
        last_total = trend[-1].total
        if first_total > 0:
            change = ((last_total - first_total) / first_total) * 100
            arrow = "▲" if change > 0 else "▼"
            print(f"\n  Gasto: {arrow} {abs(change):.1f}% do primeiro ao último mês registrado")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analisador de cupons fiscais NFC-e")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="Escanear cupom (chave ou URL do QR code)")
    p_scan.add_argument("source", help="Chave de acesso 44 dígitos ou URL")
    p_scan.add_argument("--normalize", action="store_true", help="Normalizar com Claude API")
    p_scan.add_argument("--save", action="store_true", help="Salvar no banco (normaliza automaticamente)")
    p_scan.add_argument("--timeout", type=int, default=30000)

    p_hist = sub.add_parser("history", help="Histórico de preços de um produto")
    p_hist.add_argument("product", help="Nome canônico do produto")
    p_hist.add_argument("--brand", default=None)

    p_stores = sub.add_parser("stores", help="Comparar preço do produto entre lojas")
    p_stores.add_argument("product", help="Nome canônico do produto")
    p_stores.add_argument("--brand", default=None)

    p_trend = sub.add_parser("trend", help="Tendência de gasto mensal")
    p_trend.add_argument("--cnpj", default=None, help="Filtrar por CNPJ da loja")

    args = parser.parse_args()
    {"scan": cmd_scan, "history": cmd_history, "stores": cmd_stores, "trend": cmd_trend}[args.cmd](args)


if __name__ == "__main__":
    main()
