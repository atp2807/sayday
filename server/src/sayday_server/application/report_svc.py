"""report_svc — finalize_ring: A6→A8 파이프라인 (루프의 심장).

통화 종료(ENDED) 후: 발화 판정 → FSRS/recall_window 갱신 → recall_entry 적재 →
교정 리포트 생성 → REPORTED 전이. 멱등(이미 REPORTED면 no-op).

서비스 = 모듈레벨 async def. application 은 infrastructure 를 import 하지 않는다
(원칙 5) — UowFactory·TutorPort·CatalogPort 를 주입받는다. worker(admin) 경로라
admin uow 를 쓰고, 판정·저장이 한 트랜잭션 안에서 함께 커밋된다.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from ..domain import recall_calc, ring_state
from ..domain.pattern import PatternCard, RecallEntry
from ..domain.ring_state import RingStatus
from .errors import InvalidStateError, NotFoundError
from .ports import PatternSpec, TranscriptLine, TutorPort
from .repos import CatalogPort, UowFactory


async def _spec(catalog: CatalogPort, key: str) -> PatternSpec:
    """카탈로그 조회 — 미지 key(KeyError)는 application 에러로 번역."""
    try:
        return await catalog.get_spec(key)
    except KeyError as exc:
        raise NotFoundError(f"pattern spec 없음: {key}", error_code="LEARN_404") from exc


async def finalize_ring(
    uowf: UowFactory,
    tutor: TutorPort,
    catalog: CatalogPort,
    ring_id: UUID,
    now: datetime,
) -> None:
    """통화 1건의 판정·리포트를 확정 (멱등)."""
    async with uowf.admin() as uow:
        # 1) ring 확인 + 멱등 가드
        ring = await uow.call.get_ring(ring_id)
        if ring is None:
            raise NotFoundError(f"ring 없음: {ring_id}", error_code="RING_002")
        if ring.status_cd == RingStatus.REPORTED.value:
            return  # 이미 리포트됨 — no-op

        learner_id = ring.learner_id

        # 2) 발화 (seq순)
        utts = await uow.call.list_utterances(ring_id)

        # 학습자 발화가 실제로 겨눈 문형들 (판정/카드 갱신 대상)
        learner_keys: list[str] = []
        for utt in utts:
            if utt.speaker_cd == "LEARNER" and utt.target_pattern_key is not None:
                learner_keys.append(utt.target_pattern_key)

        # 3) 리포트 대상 문형: drill_plan 우선, 없으면 학습자 발화 대상
        if ring.drill_plan is not None and ring.drill_plan.pattern_keys:
            targets = list(ring.drill_plan.pattern_keys)
        else:
            targets = list(dict.fromkeys(learner_keys))

        # specs/카드는 리포트 대상 ∪ 실제 발화 대상 모두 필요
        all_keys = list(dict.fromkeys([*targets, *learner_keys]))
        specs: dict[str, PatternSpec] = {k: await _spec(catalog, k) for k in all_keys}

        # 4) 카드맵 — 기존 카드 or 신규
        cardmap: dict[str, PatternCard] = {}
        for key in all_keys:
            existing = await uow.learning.get_card(learner_id, key)
            cardmap[key] = existing or recall_calc.new_pattern_card(key, now)

        # 5) 학습자 발화 판정 + FSRS/window 갱신 + recall_entry (seq순)
        prev_tutor_text = ""
        for utt in utts:
            if utt.speaker_cd == "TUTOR":
                prev_tutor_text = utt.text  # 직전 TUTOR 질문 추적
                continue
            if utt.speaker_cd != "LEARNER" or utt.target_pattern_key is None:
                continue

            key = utt.target_pattern_key
            judgment = await tutor.judge_utterance(specs[key], prev_tutor_text, utt.text)
            await uow.call.set_utterance_verdict(utt.id, judgment.verdict.value)

            outcome = recall_calc.apply_recall(
                cardmap[key], judgment.verdict, utt.response_ms, now
            )
            cardmap[key] = outcome.card  # 같은 문형 반복 시 순차 진화 (FSRS 정상)
            pcid = await uow.learning.save_card(learner_id, outcome.card)  # upsert — 반복 안전
            await uow.learning.add_recall(
                learner_id,
                pcid,
                ring_id,
                RecallEntry(key, judgment.verdict, utt.response_ms, now),
                outcome.rating.name,  # fsrs.Rating enum명: Again/Hard/Good/Easy
            )

        # 6) 리포트 초안 (전사 전체 + 대상 문형 spec)
        transcript = tuple(TranscriptLine(u.speaker_cd, u.text) for u in utts)
        report = await tutor.write_ring_report(
            transcript, tuple(specs[k] for k in targets)
        )

        # 7) ring_report + correction 적재
        await uow.call.create_ring_report(ring_id, learner_id, report.summary_ko, None)
        for corr in report.corrections:
            await uow.call.add_correction(
                ring_id,
                learner_id,
                None,
                corr.severity_cd,
                corr.quote,
                corr.corrected,
                corr.explain_ko,
            )

        # 8) REPORTED 전이 (ENDED→REPORTED 만 허용)
        if not ring_state.can_transition(ring.status_cd, RingStatus.REPORTED.value):
            raise InvalidStateError(
                f"리포트 불가 상태: {ring.status_cd}", error_code="RING_001"
            )
        await uow.call.set_ring_status(ring_id, RingStatus.REPORTED.value)
        # 상태 이력 (admin uow — 같은 트랜잭션): from = 전이 전 상태(ENDED)
        await uow.ops.log_state(
            "RING", ring_id, ring.status_cd, RingStatus.REPORTED.value
        )
