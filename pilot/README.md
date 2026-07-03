# sayday 파일럿 ①: "실제로 되나" — STT 오류보존 실측

## 왜 이걸 먼저 하나
sayday의 교정 리포트는 **STT가 넘긴 텍스트 위에서** 문법을 채점한다. 그래서 제품의 급소는 정확도가 아니라 **오류 보존**이다:

- 학습자가 `"I have went there"` 라고 틀리게 말함
- STT가 `"I have went"` 로 받으면 → Claude가 교정 가능 ✅ (**preserved**)
- STT가 친절하게 `"I went"` 로 고쳐서 받으면 → **교정할 오류가 사라짐. 리포트 텅 빔** ❌ (**auto_corrected**)

대화가 잘 되는 것과 무관하다. 좋은 대화형 STT일수록 자동교정을 잘해서 오히려 위험하다. 이걸 코드 짜기 전에 깬다.

## 무엇을 측정
| 지표 | 의미 | 합격선 |
|------|------|--------|
| **EPR** (error-preservation rate) | 심은 오류를 살려서 전사한 비율 | **≥ ~85%** |
| **auto_correct%** | 자동교정해버린 비율 (제품 킬러) | **낮을수록. 15% 넘으면 위험** |
| WER | 단어오류율 (참고) | 낮을수록 |
| cost / latency | 실단가·지연 | Gemini vs GPT 비교 |

## 표본
- **학습자 5~10명**, 레벨 섞기(초/중/상 각 2~3명). 한국어 L1.
- 각자 `corpus.json`의 **planted_errors 15문장**을 소리내어 읽음(오류 포함된 그대로).
- 여유되면 free_speech_prompts 3개도 녹음(→ phase 2, 사람 참조전사 필요).

## 녹음 방법
1. 조용한 환경, 폰 기본 녹음 or 앱 예정 코덱.
2. 파일명 규칙(중요): `pilot/audio/<speaker>/<item_id>.wav`
   - 예: `pilot/audio/kim/PE01.wav`, `pilot/audio/kim/PE02.wav` …
   - m4a/mp3도 됨(ffprobe 있으면 단가 자동계산).
3. 문장 그대로 읽되, 자연스러운 속도로. 못 읽겠으면 스킵(그 파일만 없으면 됨).

## 돌리기
```bash
export OPENAI_API_KEY=...   GEMINI_API_KEY=...
pip install openai google-genai        # 설치된 쪽만 돌아감
python pilot/bench.py                   # 전체
python pilot/bench.py --provider openai # 하나만
```
→ `results.csv`(문장별 상세) + 콘솔 요약(provider별 EPR/auto%/WER/단가/지연).

## 해석 → 다음 행동
- **EPR ≥ 85%, auto% 낮음** → 그 provider로 진행. GPT/Gemini 중 싼 쪽(Gemini) 우선.
- **auto% 높음** → verbatim 프롬프트로도 못 막는 것. 대안:
  1. 다른 STT(Deepgram/AssemblyAi 등) 비교 추가
  2. STT는 정확도용, **오류 검출은 오디오→Claude 직접**(전사본 안 거치고) 경로 실험
- **특정 오류유형만 auto-correct** (results.csv의 target별로 확인) → 그 문형만 후처리/우회.

## 파일
- `corpus.json` — 심은-오류 15문장 + 자유발화 프롬프트. 지표의 ground truth.
- `bench.py` — 전사→WER·EPR·단가·지연 자동채점. 지표 로직은 정본, provider 어댑터는 설치 SDK로 검증.
- `results.csv` — 실행 결과(생성됨).

## 아직 안 재는 것 (phase 2)
- 자유발화 EPR: 사람 참조전사 필요.
- **실시간(대화) 경로의 STT**: 여기선 배치 전사로 STT 품질만 격리 측정. 실시간 스택(GPT-Realtime/Gemini Live)의 전사가 배치와 다를 수 있으니, 배치가 통과하면 실시간에서 재확인.
- 실시간 실단가(캐싱 전제) / CallKit 강제성 습관효과.
