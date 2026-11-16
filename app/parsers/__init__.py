"""Bank registry. To add a bank: write parsers/<bank>.py and add one line below."""
from .base import BankParser, ParsedStatement, Transaction
from .sbi import SBIParser

PARSERS: dict[str, type[BankParser]] = {
    SBIParser.bank_id: SBIParser,
    # "hdfc": HDFCParser,   <- add new banks here
}


def get_parser(bank_id: str) -> BankParser:
    try:
        return PARSERS[bank_id]()
    except KeyError:
        raise ValueError(f"Unsupported bank: {bank_id}")


def list_banks() -> list[dict]:
    return [{"id": k, "name": v.bank_name} for k, v in PARSERS.items()]
