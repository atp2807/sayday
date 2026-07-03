#!/usr/bin/env python3
"""
sayday STT 검증 하네스 — GPT vs Gemini의 '오류보존 전사' 능력을 실측한다.

핵심 질문: 학습자가 문법 틀린 문장을 말했을 때, STT가
  (a) 틀린 채로 받아쓰나 (preserved  → 제품 성립)
  (b) 친절하게 고쳐서 받아쓰나 (auto_corrected → 제품 킬러: 교정할 오류가 사라짐)

측정 지표:
  - WER            : 얼마나 정확히 받아쓰나 (참고 지표)
  - EPR            : error-preservation rate = preserved / 전체 (★ 핵심)
  - auto_correct%  : 자동교정 비율 (낮을수록 좋음)
  - cost, latency  : 실단가·지연

사용법:
  1) 녹음을 pilot/audio/<speaker>/<item_id>.wav 로 배치
     예: pilot/audio/kim/PE01.wav, pilot/audio/kim/PE02.wav ...
  2) export OPENAI_API_KEY=...  GEMINI_API_KEY=...
  3) python pilot/bench.py            # 전체
     python pilot/bench.py --provider openai   # 하나만
  4) results.csv + 콘솔 요약 확인

의존성(선택): pip install openai google-genai   (설치된 쪽만 돌아감)
지표 로직은 정본. 각 provider 어댑터는 설치된 SDK 버전에 맞춰 검증할 것(주석 표시).
"""
import argparse, csv, json, os, re, subprocess, sys, time, wave
from pathlib import Path

ROOT = Path(__file__).parent
AUDIO_DIR = ROOT / "audio"
CORPUS = json.loads((ROOT / "corpus.json").read_text())

# ── 단가(분당 USD). 최신 pricing으로 갱신할 것. 여기 값은 시작 추정치. ──
RATE_PER_MIN = {"openai": 0.006, "gemini": 0.004}  # gpt-4o-transcribe / gemini transcribe (배치 전사 기준, 실시간과 다름)

# ── 사용 모델. 설치 SDK/최신 모델ID로 검증할 것. ──
OPENAI_MODEL = "gpt-4o-transcribe"
GEMINI_MODEL = "gemini-2.5-flash"   # verify: 최신은 gemini-3.1-flash 계열일 수 있음

VERBATIM_INSTRUCTION = (
    "Transcribe this audio exactly as spoken, word for word. "
    "Preserve all grammatical errors, disfluencies, and non-native phrasing verbatim. "
    "Do NOT correct grammar, tense, articles, or word choice. Output only the transcription."
)

# ───────────────────────── 정규화 & 지표 ─────────────────────────
def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower())).strip()

def wer(ref: str, hyp: str) -> float:
    r, h = norm(ref).split(), norm(hyp).split()
    if not r:
        return 0.0
    # Levenshtein (token edit distance)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / len(r)

def classify(item: dict, hyp: str) -> str:
    """preserved(오류 살림) / auto_corrected(고쳐버림) / misheard(둘 다 아님=인식오류)"""
    nh = norm(hyp)
    if norm(item["error_ngram"]) in nh:
        return "preserved"
    if any(norm(c) in nh for c in item.get("corrected_ngram", [])):
        return "auto_corrected"
    return "misheard"

def audio_seconds(path: Path) -> float:
    if path.suffix.lower() == ".wav":
        with wave.open(str(path)) as w:
            return w.getnframes() / float(w.getframerate())
    try:  # ffprobe fallback for m4a/mp3
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nk=1:nw=1", str(path)],
            capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except Exception:
        return 0.0  # 단가 계산만 0, 나머지 지표는 정상

# ───────────────────────── provider 어댑터 ─────────────────────────
def transcribe_openai(path: Path) -> str:
    from openai import OpenAI  # verify: pip install openai
    client = OpenAI()
    with open(path, "rb") as f:
        r = client.audio.transcriptions.create(
            model=OPENAI_MODEL, file=f, prompt=VERBATIM_INSTRUCTION, response_format="text")
    return r if isinstance(r, str) else getattr(r, "text", str(r))

def transcribe_gemini(path: Path) -> str:
    from google import genai            # verify: pip install google-genai
    from google.genai import types
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    mime = {"wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4"}.get(path.suffix.lower().lstrip("."), "audio/wav")
    r = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[VERBATIM_INSTRUCTION, types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)])
    return (r.text or "").strip()

ADAPTERS = {"openai": transcribe_openai, "gemini": transcribe_gemini}

# ───────────────────────── 러너 ─────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=list(ADAPTERS), action="append",
                    help="지정 안 하면 전체")
    args = ap.parse_args()
    providers = args.provider or list(ADAPTERS)

    items = {it["id"]: it for it in CORPUS["planted_errors"]}
    if not AUDIO_DIR.exists():
        sys.exit(f"녹음 폴더 없음: {AUDIO_DIR}  (audio/<speaker>/<item_id>.wav 로 배치)")

    rows, agg = [], {p: {"n": 0, "wer": 0.0, "preserved": 0, "auto_corrected": 0,
                         "misheard": 0, "cost": 0.0, "latency": 0.0} for p in providers}

    for speaker_dir in sorted(p for p in AUDIO_DIR.iterdir() if p.is_dir()):
        for audio in sorted(speaker_dir.iterdir()):
            item = items.get(audio.stem)
            if not item:
                continue  # free-speech는 자동채점 제외(ground truth 필요 → phase2)
            secs = audio_seconds(audio)
            for prov in providers:
                try:
                    t0 = time.time()
                    hyp = ADAPTERS[prov](audio)
                    lat = time.time() - t0
                except Exception as e:
                    print(f"[skip] {prov} {audio.name}: {e}", file=sys.stderr)
                    continue
                w = wer(item["script"], hyp)
                cls = classify(item, hyp)
                cost = secs / 60.0 * RATE_PER_MIN[prov]
                rows.append({"speaker": speaker_dir.name, "id": item["id"], "target": item["target"],
                             "provider": prov, "wer": round(w, 3), "epr_class": cls,
                             "cost_usd": round(cost, 5), "latency_s": round(lat, 2),
                             "script": item["script"], "hyp": hyp})
                a = agg[prov]
                a["n"] += 1; a["wer"] += w; a[cls] += 1; a["cost"] += cost; a["latency"] += lat

    out = ROOT / "results.csv"
    with open(out, "w", newline="") as f:
        wcsv = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["provider"])
        wcsv.writeheader(); wcsv.writerows(rows)

    print(f"\n{'='*60}\n결과 {len(rows)}건 → {out}\n{'='*60}")
    print(f"{'provider':10} {'n':>3} {'WER':>6} {'EPR%':>6} {'auto%':>6} {'mishrd%':>7} {'$/전사':>8} {'지연s':>6}")
    for p, a in agg.items():
        if not a["n"]:
            print(f"{p:10}  (녹음 없음/전부 실패)"); continue
        n = a["n"]
        print(f"{p:10} {n:>3} {a['wer']/n:>6.2f} {a['preserved']/n*100:>5.0f}% "
              f"{a['auto_corrected']/n*100:>5.0f}% {a['misheard']/n*100:>6.0f}% "
              f"{a['cost']/n:>8.5f} {a['latency']/n:>6.2f}")
    print("\n판정: EPR ≥ ~85% (auto% 낮음) 이어야 리포트 신뢰 가능. "
          "auto%가 높으면 그 provider는 verbatim 강제해도 못 막는 것 → 대안 STT/후처리 필요.")

if __name__ == "__main__":
    main()
