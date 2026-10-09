"""The AI's answers, recorded once and replayed - so the AI path can be tested.

A bill the free parser declines is read by Gemini, and everything after the
AI's answer is ours: merging page chunks, settling the parties, reconciling,
the checks, the gap-fill. None of that was regression-tested, because the
answer itself costs a call and is not repeatable. So each answer is recorded
once against a real key (scripts/corpus.py record) and kept in the private
invoice corpus beside the PDF; the corpus test replays them, offline and free,
and pins what the pipeline makes of them.

Each bill keeps its answers in a folder of its own (answers_dir), each keyed
by the question: the method, and the exact bytes, type and text it was asked
about. A question never asked before has no answer - for
the reading itself that is an error (re-record), while an optional follow-up
(the gap-fill, the AI review) simply goes unanswered, as when the AI is busy.
"""
import hashlib
import json
import pathlib
import re
from typing import Any, Optional

from app.services.ocr.base import OCRError, OCRProvider


ORDER = "picture-order.json"


def answers_dir(root: pathlib.Path, file: str, part: Optional[int] = None) -> pathlib.Path:
    """The folder holding one bill's recorded answers."""
    # The batch folder is part of the name: the same bill can be in two batches.
    parts = [p for p in pathlib.PurePosixPath(file).parts if p != "pdfs"]
    name = "__".join(re.sub(r"[^A-Za-z0-9]+", "_", p).strip("_") for p in parts)
    return pathlib.Path(root) / (name + (f"__part{part}" if part is not None else ""))


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
        answer = self._ask("extract", file_bytes, content_type, doc_type)
        if content_type == "application/pdf":
            # The order page pictures were asked in (one at a time while
            # recording): their bytes are not the same from run to run.
            order = self.store / ORDER
            asked = json.loads(order.read_text(encoding="utf-8")) if order.exists() else []
            asked.append(question_key("extract", file_bytes, content_type, doc_type))
            order.write_text(json.dumps(asked, indent=1), encoding="utf-8")
        return answer

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
        self._pictures_asked = 0

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
        if content_type == "application/pdf":
            n, self._pictures_asked = self._pictures_asked, self._pictures_asked + 1
        if not found and content_type == "application/pdf":
            # A scan is sent as page pictures, whose bytes differ from run to
            # run and machine to machine. Its readings are served in the order
            # they were asked (chunks are read one at a time when recording
            # and replaying) - and a bill read in one piece has just one.
            order = self.store / ORDER
            asked = json.loads(order.read_text(encoding="utf-8")) if order.exists() else []
            only = sorted(self.store.glob("extract-*.json"))
            path = (self.store / f"{asked[n]}.json") if n < len(asked) else (only[0] if len(only) == 1 else None)
            if path is not None and path.exists():
                self.unanswered.pop()
                return json.loads(path.read_text(encoding="utf-8"))
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
