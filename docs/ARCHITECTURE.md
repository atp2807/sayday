# sayday 아키텍처 (v1, 2026-07-04)

> 실시간 전화영어. **앱 중심** — 웹은 랜딩·소개만.
> 스택: React Native(앱) · React(웹 랜딩) · Python/FastAPI(서버).
> **설계 참조 우선순위: 링크로어(backend v2·RLS·ds-bundle) > 해드림(동기/비동기·인프라 사이징) > 모하더스(상태머신·로그·고아방지).**
> 네이밍 정본 = naming MCP 사전 (전 프로젝트 공유). 이 문서는 sayday 적용본.

---

## 0. 중앙화 원칙 (전 레이어 공통, 위반 = CI 실패)

| # | 원칙 | 구현 |
|---|------|------|
| 1 | **단일 진실 소스(SSOT)** | 디자인 토큰 = `ds-bundle/` 1곳, 네이밍 = naming MCP 사전, API 계약 = OpenAPI, enum = contracts |
| 2 | **프론트 직접 호출 금지** | 앱/웹은 외부 API(Gemini·Claude·PG·푸시) 절대 직접 호출 안 함. 전부 서버 경유. 유일 예외 = WebRTC 미디어(서버 발급 단기 토큰으로만) |
| 3 | **키는 서버에만** | AI/PG/푸시 키 전부 서버 Secrets. 앱 번들에 시크릿 0 |
| 4 | **일반 단어 금지** | `user, client, session, service, manager, handler, data, info, item, util` 엔티티명 금지 (naming `avoid_generic_words`). §5 용어집만 사용, 신규 용어는 naming MCP 선등록 |
| 5 | **레이어 의존 단방향** | presentation → application → domain / infrastructure는 application의 포트(Protocol) 구현. domain은 아무것도 import 안 함 |
| 6 | **하드 삭제 금지** | soft delete(상태 전이)만. FK CASCADE DELETE 금지. 단 auth 자격증명은 탈퇴 시 예외적 파기 |
| 7 | **미들웨어로 횡단 관심사** | 인증·로깅·rate limit·에러 포맷은 미들웨어 체인. 라우터/서비스 내 중복 구현 금지 |
| 8 | **DB 레벨 권한 격리(RLS)** | fail-closed RLS + DSN 분리 — 코드 버그가 나도 남의 데이터가 안 나가는 마지막 방어선 (링크로어 패턴) |

---

## 1. 모노레포 구조

```
sayday/
├─ apps/
│  ├─ mobile/                  # React Native(Expo) — 제품 전부
│  │  └─ src/
│  │     ├─ features/          # call / report / drill / onboarding / settings
│  │     ├─ screens/           # 화면 = features 조립만 (로직 금지)
│  │     ├─ navigation/
│  │     └─ lib/               # api-client 래핑, push, callkit 브릿지(ring_native/)
│  └─ web/                     # React — 랜딩·소개만. api-client 의존 금지
│                              #   (대기자 폼 1개만 /api/public/waitlist 호출 허용)
├─ server/
│  ├─ src/sayday_server/       # 링크로어 backend v2 구조 계승
│  │  ├─ domain/               #   엔티티·값객체·에러 — 외부 의존 0 (순수)
│  │  │  ├─ recall_calc.py     #     FSRS + latency-dial (엔진 심장)
│  │  │  ├─ drill_calc.py      #     통화 커리큘럼 배치 (복습→신규, 인터리빙)
│  │  │  └─ verdict_calc.py    #     USED/AVOIDED/ATTEMPTED 규칙
│  │  ├─ application/          #   서비스 = 모듈 레벨 async def (클래스 아님, 링크로어 룰)
│  │  │  ├─ *_svc.py           #     유스케이스, 트랜잭션 경계(UoW)
│  │  │  ├─ ports.py           #     Protocol 인터페이스 (speech/tutor/ring/push/pay)
│  │  │  └─ authz.py           #     역할·소유권 검사 단일 지점
│  │  ├─ infrastructure/
│  │  │  ├─ db/                #     SQLAlchemy 2.0 async ORM, UoW, *_repo.py
│  │  │  └─ gateway/           #     포트 구현체: gemini_speech.py, claude_tutor.py,
│  │  │                        #       livekit_ring.py, apns_fcm_push.py, pg_pay.py
│  │  └─ presentation/http/    #   FastAPI 라우터(learner/ carrot/ potato/) + mw/ + DTO
│  ├─ voice/                   # 실시간 음성 워커 — LiveKit agent + Gemini Live
│  │                           #   (별도 프로세스. domain/application 재사용, DB 직접 쓰기 금지)
│  └─ worker/                  # 비동기 잡 — 리포트 생성, 카드 갱신, 발신 스케줄 틱
├─ ds-bundle/                  # 디자인 시스템 SSOT (링크로어 구조 그대로, §9)
│  ├─ tokens/  components/  guidelines/  _ds_sync.json
├─ packages/
│  ├─ ui/                      # RN 컴포넌트 킷 — ds-bundle 토큰만 소비, hex 금지
│  ├─ api-client/              # OpenAPI 자동생성 JS 클라이언트(TS 아님) — 앱의 유일한 HTTP 통로
│  └─ contracts/               # enum·이벤트 스키마 Py 정본 → JS 코드젠 + sync 테스트로 CI 동기화 검증
├─ tools/guards/               # naming-guard · boundary-guard · token-guard (§10)
└─ docs/
```

**의존 방향 (강제):**
```
apps/mobile → packages/api-client → (HTTP) → presentation/http
apps/mobile → packages/ui → ds-bundle
server: presentation → application → domain
        infrastructure ─implements→ application/ports (application은 구현체를 모름, DI)
        domain → (아무것도 import 안 함)
voice/worker → application 서비스 경유 (repo/DB 직접 접근 금지)
```

---

## 2. 권한 분리 (역할 · 토큰 · DB 3중)

**레이어 1 — 역할·토큰:**

| 역할 | 코드 | 접근 | 토큰 |
|------|------|------|------|
| 학습자 | `LEARNER` | `/api/...` | JWT access(15m)+refresh(30d), aud=`app` |
| 운영자 | `CARROT` | `/api/carrot/...` | 별도 발급, aud=`carrot` |
| 개발자 | `POTATO` | `/api/potato/...` | 모니터링 read-only |
| 시스템 | `RINGER` | 내부 전용 | voice/worker ↔ 서버 internal 토큰, 외부 노출 경로 없음 |

- **ADMIN 단어 금지** (naming 룰). aud 불일치 = 미들웨어 즉시 401.
- authz는 `application/authz.py` 단일 지점 — 라우터/서비스에 흩어진 권한 체크 금지.

**레이어 2 — DB (링크로어 RLS 패턴):**
- learner 소유 테이블 전부 **fail-closed RLS**: `SET LOCAL ROLE app_user` + `app.current_learner_id`. 정책 없으면 0행.
- **DSN 분리**: `app_user`(RLS 적용, 서비스 기본) / `app_admin`(BYPASSRLS, carrot·worker 명시 경로만). UoW가 `as_admin=True`로 분기.
- 효과: 코드에서 `learner_id` 필터를 깜빡해도 DB가 남의 데이터를 안 내줌.

**레이어 3 — 감사:** 상태 전이 = `@audit` + `state_changed()` (모하더스). 이력 테이블 3분리(링크로어): `op_log`(작업), `audit_log`(권한·소유권 변경), `state_log`(엔티티 전이).

---

## 3. DB 스키마 분리 (특히 auth ↔ account)

PostgreSQL, 도메인별 스키마. PK=UUID v4 `id`, FK=`{테이블}_id`, `*_ts TIMESTAMPTZ`, `*_cd`, `*_yn`, `*_amt` (naming 사전).

| 스키마 | 소유 | 테이블 (단수형) | 원칙 |
|--------|------|----------------|------|
| `auth` | 인증만 | `identity`, `credential`, `refresh_token`, `otp` | **PII 없음.** auth svc는 `identity_id`만 반환. account 조인 금지 |
| `account` | 사람 | `learner`(프로필·레벨), `device`, `push_token`, `consent` | 자격증명 없음. `learner.identity_id → auth.identity.id` 단방향 |
| `learning` | 학습 상태 | `pattern_card`(FSRS: stability·difficulty·due_ts·**recall_window_ms**), `recall_log`, `drill_plan` | RLS: learner 본인만 |
| `call` | 통화 | `ring`, `utterance`, `transcript`, `verdict`, `correction`(severity_cd), `ring_report` | 오디오는 R2, `audio_url`만. 고아 파일 방지 패턴 |
| `billing` | 돈 | `plan`, `subscription`, `purchase` | 웹결제 |
| `ops` | 운영 | `op_log`, `audit_log`, `state_log`, `dev_alert` | RLS 제외(admin 전용) |

**auth↔account 분리 규칙:**
1. 로그인/토큰 갱신은 auth만 조회. 2. 탈퇴 = account 익명화(soft) + auth 자격증명 파기, 학습 이력은 익명 보존. 3. cross-schema는 application 서비스에서만 — repo는 자기 스키마만.

**상태머신** (모하더스 `can_*` + varchar `status_cd`):
- `ring`: `SCHEDULED → RINGING → IN_CALL → ENDED → REPORTED` / `MISSED`, `DECLINED`, `DROPPED`
- `subscription`: `TRIAL → ACTIVE → PAST_DUE → CANCELLED` / `EXPIRED`
- **위자드 추상화(해드림)**: 내부 상태 ≠ 학습자 표시. 앱에는 `오늘의 통화 → 통화 완료 → 리포트 도착` 3단계만. 내부 8상태를 앱에 노출 금지.

---

## 4. 도메인 용어집 (네이밍 중앙화 — naming MCP 등록 대상)

**이 표에 없는 엔티티명 신설 = naming MCP 선등록 후 사용.**

| 개념 | 용어 | 금지어 |
|------|------|--------|
| 학습자 | `learner` | ~~user, member, client~~ |
| 통화 1건 | `ring` | ~~call_session, session~~ |
| 목표 문형 | `pattern` / `pattern_card` | ~~item, card 단독~~ |
| 발화 1턴 | `utterance` | ~~message, input~~ |
| 강제인출 질문 | `elicit_prompt` | ~~question, task~~ |
| 사용 판정 | `verdict` (`USED/AVOIDED/ATTEMPTED`) | ~~result, status~~ |
| 교정 항목 | `correction` (severity: `BLOCKING/GRAMMAR/POLISH`) | ~~feedback, error~~ |
| 응답 허용시간 | `recall_window` (ms) | ~~timeout, limit~~ |
| 통화 리포트 | `ring_report` | ~~report 단독, summary~~ |
| 발신 스케줄 | `ring_slot` | ~~schedule, alarm~~ |

---

## 5. API 규격 (naming 사전 그대로)

- kebab-case·복수형·동사금지: `/api/rings/{id}`, `/api/pattern-cards`, `/api/ring-slots`
- 프리픽스: learner 리소스 직접 / `/api/carrot/...` / `/api/potato/...` / `/api/public/waitlist`
- 목록 `{items, total, page, page_size}` · 단건 직접 반환 · 에러 `{error_code: "RING_001", message}` (`AUTH_/ACCT_/LEARN_/RING_/BILL_`)
- OpenAPI = 계약 SSOT → api-client 자동생성 → 앱에서 fetch/axios 직접 사용 = lint 실패
- CORS `'*'` 금지 (링크로어 룰)

---

## 6. 실시간 음성 경로 + 동기/비동기 분리 (해드림 패턴)

**동기(사용자 대기 중, 즉시 응답)** vs **비동기(무거움, 백그라운드)** 를 명시 분리:

```
[동기] worker: ring_slot 틱 → push_gateway(VoIP push/CallKit) → 앱 수신
[동기] 앱: POST /api/rings/{id}/answer → 서버가 LiveKit 방 토큰(60s) 발급
[동기] voice 워커 room join: Gemini Live(키는 워커에만) ↔ 오디오
       drill_plan 로드 → elicit_prompt 진행 → utterance/transcript 실시간 적재
       자막 = LiveKit data channel push (앱은 표시만)
[비동기] 통화 종료 → voice가 application svc(internal 토큰) 경유 잡 enqueue
[비동기] worker: tutor_gateway(Claude) → verdict·correction·ring_report
         → recall_calc로 pattern_card 갱신 → drill_plan 재배치 → push "리포트 도착"
```

- 앱이 아는 외부 주소 = **우리 API + LiveKit endpoint 딱 2개.**
- 큐는 처음부터 SQS 아님 — **백그라운드 task로 시작, 트래픽 검증 후 큐 도입** (해드림: SQS 보류 패턴. 과설계 금지).
- 무압박 사고시간 = voice 워커 턴테이킹 설정(긴 endpoint + `recall_window` 주입). 앱 로직 아님.

---

## 7. 미들웨어 체인 (순서 고정)

```
request_id → access_log(폴링 제외·GET 성공 스킵) → auth(JWT) → role_guard(aud)
→ rate_limit(역할별) → [router] → error_envelope
```
- 에러는 전부 `{error_code, message}` 변환, raw 500 노출 금지. PII 마스킹은 로그 미들웨어 일괄.

---

## 8. 앱 내부 규칙 (React Native)

1. 화면은 조립만 — screens/에 로직·API 호출 금지
2. API는 api-client만 (React Query)
3. 스타일은 ds-bundle 토큰만 — hex/px 리터럴 lint 실패
4. feature 간 직접 import 금지 (eslint-boundaries)
5. CallKit/ConnectionService = `lib/ring_native/` 1곳 격리

웹(랜딩)은 ds-bundle만 공유, api-client 의존 금지.

---

## 9. 디자인 시스템 (ds-bundle — 링크로어 구조 그대로, Claude/DesignSync 연동)

```
ds-bundle/
├─ tokens/            # 토큰 정본 — 전 표면(앱·웹) 단일출처
├─ components/        # 컴포넌트 스펙/프리뷰
├─ guidelines/        # 사용 규칙
├─ _ds_sync.json      # DesignSync 상태 — Claude가 읽고 쓰는 연동 지점
└─ styles.css         # 웹 산출물
```
- 토큰 수정은 ds-bundle에서만 시작 → RN 테마·웹 CSS로 컴파일 전파. 산출물 수기 수정 = token-guard 실패.
- semantic 토큰만 참조(`color.surface.primary`), raw 팔레트 직접 참조 금지.
- 링크로어에서 이미 "토큰 단일출처 전 표면" 달성한 구조 — 그대로 계승.

---

## 10. 린트 가드 (tools/guards/ — 전부 CI 게이트)

| 가드 | 잡는 것 |
|------|---------|
| `naming-guard` | 금지 일반단어 엔티티 신설, 컬럼 접미사 위반(`_amount`→`_amt`), naming MCP 미등록 신조어 |
| `boundary-guard` (eslint-boundaries / import-linter) | 레이어 역방향 import, feature 간 직접 import, domain의 IO import |
| `fetch-guard` | api-client 밖 fetch/axios, 앱 내 외부 API 주소 리터럴 |
| `token-guard` | hex/rgb 리터럴, ds-bundle 산출물 수기 수정 |
| `schema-guard` | OpenAPI drift, enum/contract sync 테스트(test_enum_sync류) 실패, migration_history 추적 누락·비멱등 DDL(IF NOT EXISTS 없음), repo의 타 스키마 접근, RLS 정책 누락 테이블 |
| 기본 | ruff+mypy(strict), eslint+tsc, 커밋 prefix(feat/fix/…) |

---

## 11. 인프라 (링크로어 패턴 — 무도커, 비용의식 사이징)

- **컨테이너/Docker 사용 금지** (확정). 배포 = EC2 직접: python venv + **systemd 유닛**(api/voice/worker 프로세스별) + **nginx 리버스프록시**.
- **시작 스펙**: EC2 1대 + RDS PostgreSQL + Cloudflare DNS/TLS. Redis는 필요 시(큐 도입 시점에).
- **배포 파이프라인(MVP)**: 수동 배포 — git pull + `systemctl restart` 직접 실행. Secrets Manager/env 파일에 키. GitHub Actions 자동화는 배포 빈도 늘어날 때 도입(과설계 금지, 아래 스케일업 트리거와 같은 원칙).
- **스케일업 트리거 명문화** (해드림): API 느림→EC2 타입 변경 / DB 커넥션 80%→RDS 변경 / 리포트 잡 적체→worker 분리·큐 도입. **피크 대비 상시 과잉구매 금지.**
- 모니터링: CloudWatch 알람(CPU·커넥션·에러율) → 슬랙. potato 대시보드는 ops 로그 테이블 재활용(모하더스 dev dashboard).

---

## 12. 빌드 순서 (엔진 우선 — 세그먼트 무관)

| 단계 | 내용 | 상태 |
|------|------|------|
| E1 | `domain/` 순수 엔진: recall_calc(FSRS+latency-dial)·drill_calc·verdict_calc + 테스트 | 키 불필요, 즉시 가능 |
| E2 | infrastructure/db + auth·account 스키마 + RLS + 미들웨어 체인 | 표준 빌드 |
| E3 | gateway 구현: tutor(Claude)·speech(Gemini)·push·ring | 로직 검증 완료(파일럿) |
| E4 | voice 워커: LiveKit+Gemini Live — **실시간 STT 오류보존 스모크 겸행** | 유일한 잔여 검증 |
| E5 | 앱: 수신(CallKit)→통화→자막→리포트 | 표준 빌드 |
| E6 | 랜딩 웹 + 웹결제 + carrot 운영화면 | 표준 빌드 |
