#!/usr/bin/env python3
"""
sayday 즉석 스모크: 오디오 파일 1개 → 전사 → 오류 살았나 즉석 판정.
녹음기 불필요 — 맥 '음성 메모'로 문장 읽어 녹음 → 파일 경로만 넘기면 됨.

사용:
  python pilot/try.py <audio파일> --id PE01              # 코퍼스 문장
  python pilot/try.py <audio파일> --text "I have went there"  # 직접 문장
  --provider gemini|openai   (기본: 키 있는 쪽 자동)

키: export GEMINI_API_KEY=...   또는   OPENAI_API_KEY=...
SDK: pip install google-genai  또는  openai
"""
import argparse, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from bench import norm, wer, classify, VERBATIM_INSTRUCTION, CORPUS  # 지표 로직 재사용

def transcribe_gemini(path: Path) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    mime = {"wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4", "caf": "audio/x-caf"}.get(
        path.suffix.lower().lstrip("."), "audio/mp4")
    r = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        contents=[VERBATIM_INSTRUCTION, types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)])
    return (r.text or "").strip()

def transcribe_openai(path: Path) -> str:
    from openai import OpenAI
    with open(path, "rb") as f:
        return OpenAI().audio.transcriptions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-transcribe"),
            file=f, prompt=VERBATIM_INSTRUCTION, response_format="text")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--id"); ap.add_argument("--text"); ap.add_argument("--provider")
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

    prov = a.provider or ("gemini" if (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
                          else "openai" if os.environ.get("OPENAI_API_KEY") else None)
    if not prov: sys.exit("API 키 없음: GEMINI_API_KEY 또는 OPENAI_API_KEY 설정")

    hyp = {"gemini": transcribe_gemini, "openai": transcribe_openai}[prov](path)
    w = wer(item["script"], hyp)
    verdict = {"preserved": "✅ PRESERVED — 오류를 살려서 받아씀 (교정 가능, 제품 OK)",
               "auto_corrected": "❌ AUTO-CORRECTED — STT가 오류를 고쳐버림 (교정할 게 사라짐, 위험)",
               "misheard": "⚠️  MISHEARD — 단어 자체를 잘못 들음 (발음/정확도 이슈, 자동교정과는 다름)"}
    cls = classify(item, hyp) if a.id else ("preserved" if norm(item["script"]) in norm(hyp) else "misheard")

    print(f"\n{'─'*56}\nprovider : {prov}")
    print(f"읽을 문장 : {item['script']}")
    print(f"STT 전사  : {hyp}")
    print(f"WER      : {w:.2f}")
    print(f"판정     : {verdict[cls]}\n{'─'*56}")
    if a.id:
        print("팁: WER이 낮아도 AUTO-CORRECTED면 제품 위험. 여러 문장(PE01~PE15) 돌려서 auto% 볼 것.")

if __name__ == "__main__":
    main()
