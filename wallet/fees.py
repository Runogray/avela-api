"""
Withdrawal fee strategy.

This is OPEN DECISION D1: flat vs percentage. Both are implemented; the active
one is chosen by ACTIVE_FEE below. Swapping models is a one-line change and the
rest of the system (wallet, serializers) is fee-agnostic.

  FlatFee  — ₦1,000 under ₦100k, ₦2,000 at/above. Predictable, host-friendly,
             caps platform upside. (Your earlier locked decision.)
  PercentFee — 1% of the withdrawal. Scales platform revenue with volume,
             takes more from large celebrants. (Your latest draft.)
"""
from abc import ABC, abstractmethod

from config.constants import KYC_CAP_KOBO


class FeeStrategy(ABC):
    @abstractmethod
    def fee_kobo(self, amount_kobo: int) -> int:
        ...


class FlatFee(FeeStrategy):
    UNDER = 1_000_00
    OVER = 2_000_00

    def fee_kobo(self, amount_kobo: int) -> int:
        return self.OVER if amount_kobo >= KYC_CAP_KOBO else self.UNDER


class PercentFee(FeeStrategy):
    def __init__(self, rate: float = 0.01):
        self.rate = rate

    def fee_kobo(self, amount_kobo: int) -> int:
        return round(amount_kobo * self.rate)


# active strategy — flip to PercentFee() to switch the whole platform
ACTIVE_FEE: FeeStrategy = FlatFee()


def quote_withdrawal(amount_kobo: int) -> dict:
    fee = ACTIVE_FEE.fee_kobo(amount_kobo)
    return {"amount_kobo": amount_kobo, "fee_kobo": fee,
            "net_kobo": amount_kobo - fee}
