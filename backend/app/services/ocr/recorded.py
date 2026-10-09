"""The AI's answers, recorded once and replayed - so the AI path can be tested.

A bill the free parser declines is read by Gemini, and everything after the
AI's answer is ours: merging page chunks, settling the parties, reconciling,
the checks, the gap-fill. None of that was regression-tested, because the
answer itself costs a call and is not repeatable. So each answer is recorded
once against a real key (scripts/corpus.py record) and kept in the private
invoice corpus beside the PDF; the corpus test replays them, offline and free,
and pins what the pipeline makes of them.

An answer is keyed by the question: the method, and the exact bytes, type and
text it was asked about. A question never asked before has no answer - for
the reading itself that is an error (re-record), while an optional follow-up
(the gap-fill, the AI review) simply goes unanswered, as when the AI is busy.
"""
import hashlib
import json
import pathlib
from typing import Any, Optional

from app.services.ocr.base import OCRError, OCRProvider


def question_key(method: str, *args: Any) -> str:
    digest = hashlib.sha256(method.encode("utf-8"))
    for arg in args:
        if isinstance(arg, bytes):
            data = arg
        elif isinstance(arg, str):
            data = arg.encode("utf-8")
        else:
            data = repr(arg).encode("utf-8")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return f"{method}-{digest.hexdigest()[:32]}"


class RecordingProvider(OCRProvider):
    """Asks the real provider, and keeps every answer it gives."""

    def __init__(self, inner: OCRProvider, store: pathlib.Path):
        self.inner = inner
        self.store = pathlib.Path(store)
        self.store.mkdir(parents=True, exist_ok=True)
        self.name = inner.name
        self.calls = getattr(inner, "calls", [])

    def _ask(self, method: str, *args):
        answer = getattr(self.inner, method)(*args)
        (self.store / f"{question_key(method, *args)}.json").write_text(
            json.dumps(answer, ensure_ascii=False, indent=1), encoding="utf-8")
        return answer

    def classify(self, file_bytes: bytes, content_type: str) -> str:
        return self._ask("classify", file_bytes, content_type)

    def extract(self, file_bytes: bytes, content_type: str, doc_type: str) -> dict:
        return self._ask("extract", file_bytes, content_type, doc_type)

    def complete_json(self, prompt: str) -> dict:
        return self._ask("complete_json", prompt)

    def review_json(self, prompt: str, file_bytes: bytes, content_type: str) -> Optional[dict]:
        return self._ask("review_json", prompt, file_bytes, content_type)


class ReplayProvider(OCRProvider):
    """Answers from the recordings only; never calls the AI."""

    def __init__(self, store: pathlib.Path, name: str = "gemini"):
        self.store = pathlib.Path(store)
        self.name = name
        self.calls: list = []
        self.unanswered: list = []

    def _answer(self, method: str, *args):
        path = self.store / f"{question_key(method, *args)}.json"
        if not path.exists():
            self.unanswered.append(method)
            return None, False
        return json.loads(path.read_text(encoding="utf-8")), True

    def classify(self, file_bytes: bytes, content_type: str) -> str:
        answer, found = self._answer("classify", file_bytes, content_type)
        return answer if found else "invoice"

    def extract(self, file_bytes: bytes, content_type: str, doc_type: str) -> dict:
        answer, found = self._answer("extract", file_bytes, content_type, doc_type)
        if not found:
            raise OCRError("no recorded answer for this reading - record it again "
                           "(scripts/corpus.py record)", kind="rejected")
        return answer

    def complete_json(self, prompt: str) -> dict:
        answer, found = self._answer("complete_json", prompt)
        return answer if found and isinstance(answer, dict) else {}

    def review_json(self, prompt: str, file_bytes: bytes, content_type: str) -> Optional[dict]:
        answer, _found = self._answer("review_json", prompt, file_bytes, content_type)
        return answer
