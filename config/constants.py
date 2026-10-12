"""
Shared domain constants. Money is stored everywhere as an integer number of
*kobo* (1 naira = 100 kobo) to avoid floating-point rounding on money.
"""

# --- KYC / receiving cap (locked decision) -----------------------------------
# A host may receive up to this cumulative amount on phone+email alone. Once
# cumulative received crosses it, NEW incoming gifts are blocked until NIN
# verification clears. The single gift that crosses the line is allowed through.
KYC_CAP_KOBO = 100_000_00          # ₦100,000
KYC_NUDGE_KOBO = 70_000_00         # ₦70,000 — prompt verification early

# --- Withdrawal --------------------------------------------------------------
MIN_WITHDRAWAL_KOBO = 2_000_00     # ₦2,000 floor, stops fee-stacking on tiny pulls


def naira(kobo: int) -> str:
    """Human display helper: 12_500_00 -> '₦12,500.00'."""
    return f"₦{kobo / 100:,.2f}"
