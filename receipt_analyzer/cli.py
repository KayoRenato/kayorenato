"""
CLI entry point.

Usage:
    python -m receipt_analyzer.cli <access_key_or_url> [--normalize]

Examples:
    python -m receipt_analyzer.cli "2625 1120 3001 5700 4056 6511 3000 0437 7518 7066 2337"
    python -m receipt_analyzer.cli "https://nfce.sefaz.pe.gov.br/nfce/consulta?p=..." --normalize
"""

import argparse
import sys
from decimal import Decimal

from receipt_analyzer.scraper.nfce_scraper import scrape
from receipt_analyzer.scraper.normalizer import normalize


def _fmt(d: Decimal) -> str:
    return f"R$ {d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape and parse a Brazilian NFC-e receipt.")
    parser.add_argument("source", help="44-digit access key or full QR-code URL")
    parser.add_argument("--normalize", action="store_true", help="Normalize items with Claude API")
    parser.add_argument("--timeout", type=int, default=30000, help="Page load timeout in ms")
    args = parser.parse_args()

    print(f"\nFetching receipt from: {args.source[:60]}...\n")
    receipt = scrape(args.source, timeout_ms=args.timeout)

    print(f"Store   : {receipt.store.name}")
    print(f"CNPJ    : {receipt.store.cnpj}")
    print(f"Location: {receipt.store.city} / {receipt.store.state}")
    print(f"Date    : {receipt.issued_at.strftime('%d/%m/%Y %H:%M')}")
    print(f"Total   : {_fmt(receipt.total)}")
    print(f"Key     : {receipt.nfce_key}")
    print(f"\n{'─'*72}")
    print(f"{'#':<4} {'Description':<35} {'Qty':>6} {'Un':>4} {'Unit Price':>12} {'Total':>12}")
    print(f"{'─'*72}")

    for i, item in enumerate(receipt.raw_items, 1):
        print(
            f"{i:<4} {item.description[:34]:<35} {item.quantity:>6.3f} "
            f"{item.unit:>4} {_fmt(item.unit_price):>12} {_fmt(item.total_price):>12}"
        )

    print(f"{'─'*72}")
    print(f"{'Items found:':<50} {len(receipt.raw_items):>4}")

    if args.normalize:
        print(f"\nNormalizing {len(receipt.raw_items)} items with Claude API...\n")
        normalized = normalize(receipt.raw_items)
        receipt.normalized_items = normalized

        print(f"{'─'*90}")
        print(f"{'Canonical Name':<30} {'Brand':<15} {'Category':<14} {'Base Unit':>9} {'Price/Unit':>12}")
        print(f"{'─'*90}")
        for n in normalized:
            brand = n.brand or "—"
            print(
                f"{n.canonical_name[:29]:<30} {brand[:14]:<15} {n.category[:13]:<14} "
                f"{n.base_unit:>9} {_fmt(n.price_per_base_unit):>12}"
            )
        print(f"{'─'*90}")


if __name__ == "__main__":
    main()
