"""File-export connector: render and write to a server-accessible watched folder."""
import os
import tempfile

from app.services.connectors.base import FAILED, SUCCESS, BaseConnector, DeliveryResult
from app.services.connectors import mapping


class FileExportConnector(BaseConnector):
    type = "file_export"

    def __init__(self, config: dict):
        self.config = config or {}
        self.output_dir = self.config.get("output_dir")

    def deliver(self, payload: dict) -> DeliveryResult:
        if not self.output_dir:
            return DeliveryResult(
                status=FAILED,
                delivery_state="failed",
                response_body="No server-accessible output folder configured. Use the Tally download or a desktop agent for a shop PC.",
            )
        try:
            artifacts = mapping.render(payload, self.config)  # {ext: content}
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult(status=FAILED, response_body=f"Render failed: {exc}")

        written = []
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            stem = payload.get("document_id", "document")
            for ext, content in artifacts.items():
                path = os.path.join(self.output_dir, f"{stem}.{ext}")
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(content)
                written.append(path)
        except OSError as exc:
            return DeliveryResult(status=FAILED, delivery_state="failed", response_body=f"Write failed: {exc}")

        return DeliveryResult(
            status=SUCCESS,
            delivery_state="file_written",
            response_body=f"Generated {', '.join(artifacts)}"
            + (f"; wrote {len(written)} file(s)" if written else ""),
            detail={"written": written, "formats": list(artifacts.keys())},
        )

    def test(self) -> DeliveryResult:
        """Check folder access without dropping a synthetic import file in it."""
        if not self.output_dir:
            return DeliveryResult(status=FAILED, delivery_state="failed", response_body="No server-accessible output folder configured.")
        try:
            os.makedirs(self.output_dir, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.output_dir, prefix=".mediscan-test-", delete=True):
                pass
        except OSError as exc:
            return DeliveryResult(status=FAILED, delivery_state="failed", response_body=f"Folder is not writable: {exc}")
        return DeliveryResult(status=SUCCESS, response_body="Output folder is writable; no import file was created.")


# Back-compat helpers used elsewhere/tests.
def render_csv(payload: dict) -> str:
    return mapping.render_csv(payload, {})


def render_json(payload: dict) -> str:
    return mapping.render_json(payload, {})
