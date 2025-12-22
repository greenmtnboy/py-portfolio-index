import csv
from io import StringIO
from typing import List
from decimal import Decimal
from datetime import date

from py_portfolio_index.models import Transaction
from py_portfolio_index.enums import Currency, OrderType


def transactions_to_csv(
    transactions: List[Transaction], include_fee: bool = True
) -> str:
    """
    Convert a list of Transaction objects to CSV format.

    Args:
        transactions: List of Transaction objects to convert
        include_fee: Whether to include fee column (defaults to 0 if not available on Transaction)

    Returns:
        CSV formatted string with header and transaction data
    """
    if not transactions:
        return "date,symbol,quantity,activityType,unitPrice,currency,fee\n"

    # Create StringIO buffer for CSV writing
    output = StringIO()

    # Define CSV headers to match your example
    fieldnames = [
        "date",
        "symbol",
        "quantity",
        "activityType",
        "unitPrice",
        "currency",
        "fee",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator='\n')

    # Write header
    writer.writeheader()

    # Write transaction rows
    for transaction in transactions:
        # Convert date to ISO format with time (defaulting to start of day)
        date_str = f"{transaction.date.isoformat()}T00:00:00.000Z"

        # Map your OrderType to activityType string
        activity_type = map_transaction_type_to_activity(transaction.type)

        # Extract unit price value (assuming Money has a value attribute)
        unit_price = (
            float(transaction.unitPrice.value)
            if hasattr(transaction.unitPrice, "value")
            else float(transaction.unitPrice)
        )

        # Get currency (assuming Currency enum has string values)
        currency_str = str(transaction.currency.name)

        # Get fee (default to 0 if not available on Transaction model)
        fee = getattr(transaction, "fee", 0)
        if hasattr(fee, "value"):
            fee = float(fee.value)
        else:
            fee = float(fee) if fee is not None else 0.0

        row = {
            "date": date_str,
            "symbol": transaction.ticker,
            "quantity": float(transaction.qty),
            "activityType": activity_type,
            "unitPrice": unit_price,
            "currency": currency_str,
            "fee": fee,
        }

        writer.writerow(row)

    # Get CSV string and clean up
    csv_content = output.getvalue()
    output.close()

    return csv_content


def map_transaction_type_to_activity(transaction_type) -> str:
    """
    Map your TransactionType/OrderType to the activityType strings used in CSV.

    Adjust this mapping based on your actual enum values.
    """
    # Handle string values directly
    if isinstance(transaction_type, str):
        return transaction_type.upper()

    # Handle enum types - adjust these mappings based on your actual enum values
    type_str = str(transaction_type).upper()

    # Common mappings - adjust as needed for your TransactionType enum
    mapping = {
        "BUY": "BUY",
        "SELL": "SELL",
        "DIVIDEND": "DIVIDEND",
        "INTEREST": "INTEREST",
        "DEPOSIT": "DEPOSIT",
        "WITHDRAWAL": "WITHDRAWAL",
        "FEE": "FEE",
        "SPLIT": "SPLIT",
        # Add more mappings as needed for your enum values
    }

    # Try exact match first
    for key, value in mapping.items():
        if key in type_str:
            return value

    # Fallback to string representation
    return type_str
