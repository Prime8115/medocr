"""File-export connector: render the payload via the configurable mapping engine
(CSV / JSON / Tally XML, profile- or column-driven) and optionally write to a
watched folder. The generated content is always returned for download.
"""
import os

from app.services.connectors.base import FAILED, SUCCESS, BaseConnector, DeliveryResult
from app.services.connectors import mapping


class FileExportConnector(BaseConnector):
    type = "file_export"

    def __init__(self, config: dict):
        self.config = config or {}
        self.output_dir = self.config.get("output_dir")

    def deliver(self, payload: dict) -> DeliveryResult:
        try:
            artifacts = mapping.render(payload, self.config)  # {ext: content}
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult(status=FAILED, response_body=f"Render failed: {exc}")

        written = []
        if self.output_dir:
            try:
                os.makedirs(self.output_dir, exist_ok=True)
                stem = payload.get("document_id", "document")
                for ext, content in artifacts.items():
                    path = os.path.join(self.output_dir, f"{stem}.{ext}")
                    with open(path, "w", encoding="utf-8", newline="") as f:
                        f.write(content)
                    written.append(path)
            except OSError as exc:
                return DeliveryResult(status=FAILED, response_body=f"Write failed: {exc}")

        return DeliveryResult(
            status=SUCCESS,
            response_body=f"Generated {', '.join(artifacts)}"
            + (f"; wrote {len(written)} file(s)" if written else ""),
            detail={"written": written, "formats": list(artifacts.keys()), "artifacts": artifacts},
        )


# Back-compat helpers used elsewhere/tests.
def render_csv(payload: dict) -> str:
    return mapping.render_csv(payload, {})


def render_json(payload: dict) -> str:
    return mapping.render_json(payload, {})
