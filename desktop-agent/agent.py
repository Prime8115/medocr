"""MediScan Desktop Agent (Windows companion app).

Bridges the cloud backend to legacy pharmacy software that has no API: it polls
for approved documents queued for this shop and writes CSV/JSON files into a
folder the local software imports (or that staff open manually).

First run:  python agent.py --pair CODE
Then:       python agent.py         (polls forever)

Package for shop PCs with:  pyinstaller --onefile agent.py
"""
import argparse
import csv
import io
import json
import os
import sys
import time

import requests

CONFIG_PATH = os.environ.get("MEDISCAN_AGENT_CONFIG", "agent_config.json")


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def pair(base_url: str, code: str) -> None:
    resp = requests.post(f"{base_url}/v1/agent/pair", json={"code": code}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    cfg = load_config()
    cfg.update(
        {
            "base_url": base_url,
            "agent_token": data["agent_token"],
            "connector_id": data["connector_id"],
            "output_dir": cfg.get("output_dir", "./mediscan_out"),
            "poll_seconds": cfg.get("poll_seconds", 15),
        }
    )
    save_config(cfg)
    print(f"Paired successfully. Config written to {CONFIG_PATH}")


def _flatten(field) -> str:
    if isinstance(field, dict):
        return "" if field.get("value") is None else str(field.get("value"))
    return "" if field is None else str(field)


def _rows(payload: dict):
    data = payload.get("data", {}) or {}
    doc_type = payload.get("doc_type")
    doc_id = payload.get("document_id", "")
    if doc_type == "invoice":
        supplier = _flatten((data.get("supplier") or {}).get("name"))
        for item in data.get("line_items", []) or []:
            yield {
                "document_id": doc_id, "supplier": supplier,
                "description": _flatten(item.get("description")),
                "batch_no": _flatten(item.get("batch_no")),
                "expiry": _flatten(item.get("expiry")),
                "quantity": _flatten(item.get("quantity")),
                "mrp": _flatten(item.get("mrp")), "rate": _flatten(item.get("rate")),
                "amount": _flatten(item.get("amount")), "hsn": _flatten(item.get("hsn")),
                "gst_percent": _flatten(item.get("gst_percent")),
            }
    else:
        patient = _flatten((data.get("patient") or {}).get("name"))
        for med in data.get("medications", []) or []:
            yield {
                "document_id": doc_id, "patient": patient,
                "medication": _flatten(med.get("name")),
                "strength": _flatten(med.get("strength")),
                "frequency": _flatten(med.get("frequency")),
                "duration": _flatten(med.get("duration")),
            }


def write_files(output_dir: str, delivery: dict) -> None:
    os.makedirs(output_dir, exist_ok=True)
    payload = delivery["payload"]
    stem = delivery["document_id"]

    with open(os.path.join(output_dir, f"{stem}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    rows = list(_rows(payload))
    if rows:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        with open(os.path.join(output_dir, f"{stem}.csv"), "w", encoding="utf-8", newline="") as f:
            f.write(buf.getvalue())


def poll_once(cfg: dict) -> int:
    base_url = cfg["base_url"]
    headers = {"Authorization": f"Bearer {cfg['agent_token']}"}
    resp = requests.get(f"{base_url}/v1/agent/deliveries", headers=headers, timeout=30)
    resp.raise_for_status()
    deliveries = resp.json()

    handled = 0
    for delivery in deliveries:
        try:
            write_files(cfg["output_dir"], delivery)
            ack = requests.post(
                f"{base_url}/v1/agent/deliveries/{delivery['id']}/ack", headers=headers, timeout=30
            )
            ack.raise_for_status()
            handled += 1
            print(f"Delivered {delivery['document_id']} -> {cfg['output_dir']}")
        except requests.RequestException as exc:
            print(f"Failed to process {delivery.get('id')}: {exc}", file=sys.stderr)
    return handled


def run_loop(cfg: dict) -> None:
    interval = cfg.get("poll_seconds", 15)
    print(f"Polling every {interval}s. Output: {cfg['output_dir']}. Ctrl+C to stop.")
    while True:
        try:
            poll_once(cfg)
        except requests.RequestException as exc:
            print(f"Poll error (will retry): {exc}", file=sys.stderr)
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="MediScan Desktop Agent")
    parser.add_argument("--pair", metavar="CODE", help="Pair using a one-time code")
    parser.add_argument("--base-url", default=os.environ.get("MEDISCAN_BASE_URL", "http://localhost:8080"))
    parser.add_argument("--once", action="store_true", help="Poll a single time and exit")
    args = parser.parse_args()

    if args.pair:
        pair(args.base_url, args.pair)
        return

    cfg = load_config()
    if not cfg.get("agent_token"):
        print("Not paired. Run: python agent.py --pair CODE", file=sys.stderr)
        sys.exit(1)

    if args.once:
        poll_once(cfg)
    else:
        run_loop(cfg)


if __name__ == "__main__":
    main()
