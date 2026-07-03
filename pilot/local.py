#!/usr/bin/env python3
"""
로컬 Whisper 스모크 — API 키 0. 맥에서 바로 돎.
'음성 메모'로 문장 읽어 녹음 → 파일만 넘기면 오류 살았나 판정.

  pilot/.venv/bin/python pilot/local.py <audio> --id PE01
  pilot/.venv/bin/python pilot/local.py <audio> --text "I have went there"

주의: Whisper는 최종 모델(GPT/Gemini)이 아니라 대용 STT. '오늘 되나' 스모크용.
"""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from bench import norm, wer, classify, CORPUS

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio"); ap.add_argument("--id"); ap.add_argument("--text")
    ap.add_argument("--model", default="base.en")
    a = ap.parse_args()
    path = Path(a.audio)
    if not path.exists():
        sys.exit(f"파일 없음: {path}")

    if a.id:
        item = next((it for it in CORPUS["planted_errors"] if it["id"] == a.id), None)
        if not item: sys.exit(f"코퍼스에 {a.id} 없음")
    elif a.text:
        item = {"script": a.text, "error_ngram": a.text, "corrected_ngram": []}
    else:
        sys.exit("--id 또는 --text 필요")

    from faster_whisper import WhisperModel
    model = WhisperModel(a.model, device="cpu", compute_type="int8")
    segs, _ = model.transcribe(str(path), language="en", condition_on_previous_text=False)
    hyp = " ".join(s.text for s in segs).strip()

    w = wer(item["script"], hyp)
    cls = classify(item, hyp) if a.id else ("preserved" if norm(item["script"]) in norm(hyp) else "misheard")
    verdict = {"preserved": "✅ PRESERVED — 오류를 살려서 받아씀 (교정 가능)",
               "auto_corrected": "❌ AUTO-CORRECTED — 오류를 고쳐버림 (교정할 게 사라짐)",
               "misheard": "⚠️  MISHEARD — 단어 자체를 잘못 들음 (발음/정확도)"}
    print(f"\n{'─'*56}\nSTT      : local whisper ({a.model})")
    print(f"읽을 문장 : {item['script']}")
    print(f"STT 전사  : {hyp}")
    print(f"WER      : {w:.2f}\n판정     : {verdict[cls]}\n{'─'*56}")

if __name__ == "__main__":
    main()
