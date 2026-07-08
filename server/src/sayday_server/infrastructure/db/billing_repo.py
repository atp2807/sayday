"""BillingRepo 구현 — billing 스키마 전용 (raw SQL, ORM 모델 없음: E3.5 결정).

자기 스키마만 접근한다 (billing.*). plan 은 참조테이블(app 은 SELECT 만, 쓰기는 admin),
subscription·payment 는 learner 소유. 금액=_amt(integer KRW), 상태=_cd(varchar),
pg_tx_ref UNIQUE=웹훅 멱등키. call_repo.py 와 동일 스타일 (COALESCE 로 부분 UPDATE).
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import RowMapping
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from ...application.records import PaymentRecord, PlanRecord, SubscriptionRecord

_PLAN_COLS = "id, plan_key, name, price_amt, period_cd, active_yn"
_SUB_COLS = "id, learner_id, plan_id, status_cd, pg_ref, started_ts, current_period_end_ts"
_PAYMENT_COLS = "id, learner_id, subscription_id, amount_amt, status_cd, pg_tx_ref, paid_ts"


def _to_plan(m: RowMapping) -> PlanRecord:
    return PlanRecord(
        id=m["id"],
        plan_key=m["plan_key"],
        name=m["name"],
        price_amt=m["price_amt"],
        period_cd=m["period_cd"],
        active_yn=m["active_yn"],
    )


def _to_subscription(m: RowMapping) -> SubscriptionRecord:
    return SubscriptionRecord(
        id=m["id"],
        learner_id=m["learner_id"],
        plan_id=m["plan_id"],
        status_cd=m["status_cd"],
        pg_ref=m["pg_ref"],
        started_ts=m["started_ts"],
        current_period_end_ts=m["current_period_end_ts"],
    )


def _to_payment(m: RowMapping) -> PaymentRecord:
    return PaymentRecord(
        id=m["id"],
        learner_id=m["learner_id"],
        subscription_id=m["subscription_id"],
        amount_amt=m["amount_amt"],
        status_cd=m["status_cd"],
        pg_tx_ref=m["pg_tx_ref"],
        paid_ts=m["paid_ts"],
    )


class SqlBillingRepo:
    """billing 스키마 repo — AsyncSession 주입 (UoW 가 공유 세션 전달)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list_active_plans(self) -> list[PlanRecord]:
        rows = (
            await self._s.execute(
                sa_text(
                    f"SELECT {_PLAN_COLS} FROM billing.plan "
                    "WHERE active_yn ORDER BY price_amt"
                )
            )
        ).mappings().all()
        return [_to_plan(m) for m in rows]

    async def get_plan(self, plan_key: str) -> PlanRecord | None:
        m = (
            await self._s.execute(
                sa_text(f"SELECT {_PLAN_COLS} FROM billing.plan WHERE plan_key = :pk"),
                {"pk": plan_key},
            )
        ).mappings().first()
        return _to_plan(m) if m is not None else None

    async def get_plan_by_id(self, plan_id: UUID) -> PlanRecord | None:
        m = (
            await self._s.execute(
                sa_text(f"SELECT {_PLAN_COLS} FROM billing.plan WHERE id = :pid"),
                {"pid": plan_id},
            )
        ).mappings().first()
        return _to_plan(m) if m is not None else None

    async def create_plan(
        self, plan_key: str, name: str, price_amt: int, period_cd: str
    ) -> PlanRecord:
        m = (
            await self._s.execute(
                sa_text(
                    "INSERT INTO billing.plan (plan_key, name, price_amt, period_cd) "
                    "VALUES (:pk, :nm, :amt, :pcd) "
                    f"RETURNING {_PLAN_COLS}"
                ),
                {"pk": plan_key, "nm": name, "amt": price_amt, "pcd": period_cd},
            )
        ).mappings().one()
        return _to_plan(m)

    async def create_subscription(
        self, learner_id: UUID, plan_id: UUID, status_cd: str, pg_ref: str | None
    ) -> SubscriptionRecord:
        m = (
            await self._s.execute(
                sa_text(
                    "INSERT INTO billing.subscription "
                    "(learner_id, plan_id, status_cd, pg_ref) "
                    "VALUES (:lid, :pid, :st, :ref) "
                    f"RETURNING {_SUB_COLS}"
                ),
                {"lid": learner_id, "pid": plan_id, "st": status_cd, "ref": pg_ref},
            )
        ).mappings().one()
        return _to_subscription(m)

    async def get_current_subscription(self, learner_id: UUID) -> SubscriptionRecord | None:
        m = (
            await self._s.execute(
                sa_text(
                    f"SELECT {_SUB_COLS} FROM billing.subscription "
                    "WHERE learner_id = :lid ORDER BY created_ts DESC LIMIT 1"
                ),
                {"lid": learner_id},
            )
        ).mappings().first()
        return _to_subscription(m) if m is not None else None

    async def get_subscription_by_pg_ref(self, pg_ref: str) -> SubscriptionRecord | None:
        m = (
            await self._s.execute(
                sa_text(
                    f"SELECT {_SUB_COLS} FROM billing.subscription "
                    "WHERE pg_ref = :ref ORDER BY created_ts DESC LIMIT 1"
                ),
                {"ref": pg_ref},
            )
        ).mappings().first()
        return _to_subscription(m) if m is not None else None

    async def set_subscription_status(
        self,
        subscription_id: UUID,
        status_cd: str,
        *,
        started_ts: datetime | None = None,
        current_period_end_ts: datetime | None = None,
    ) -> None:
        # None 인 인자는 기존 값 유지 (COALESCE) — 상태만 바꾸는 호출을 지원.
        await self._s.execute(
            sa_text(
                "UPDATE billing.subscription SET status_cd = :st, "
                "started_ts = COALESCE(CAST(:started AS timestamptz), started_ts), "
                "current_period_end_ts = "
                "COALESCE(CAST(:cpe AS timestamptz), current_period_end_ts) "
                "WHERE id = :sid"
            ),
            {
                "st": status_cd,
                "started": started_ts,
                "cpe": current_period_end_ts,
                "sid": subscription_id,
            },
        )

    async def add_payment(
        self,
        learner_id: UUID,
        subscription_id: UUID | None,
        amount_amt: int,
        status_cd: str,
        pg_tx_ref: str | None,
        paid_ts: datetime | None,
    ) -> PaymentRecord:
        m = (
            await self._s.execute(
                sa_text(
                    "INSERT INTO billing.payment "
                    "(learner_id, subscription_id, amount_amt, status_cd, pg_tx_ref, paid_ts) "
                    "VALUES (:lid, :sid, :amt, :st, :tx, CAST(:paid AS timestamptz)) "
                    f"RETURNING {_PAYMENT_COLS}"
                ),
                {
                    "lid": learner_id,
                    "sid": subscription_id,
                    "amt": amount_amt,
                    "st": status_cd,
                    "tx": pg_tx_ref,
                    "paid": paid_ts,
                },
            )
        ).mappings().one()
        return _to_payment(m)

    async def get_payment_by_pg_tx(self, pg_tx_ref: str) -> PaymentRecord | None:
        m = (
            await self._s.execute(
                sa_text(
                    f"SELECT {_PAYMENT_COLS} FROM billing.payment WHERE pg_tx_ref = :tx"
                ),
                {"tx": pg_tx_ref},
            )
        ).mappings().first()
        return _to_payment(m) if m is not None else None
