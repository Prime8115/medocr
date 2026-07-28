# MediScan Desktop Agent (Windows)

A tiny companion app for pharmacy PCs. It receives approved documents from the
MediScan cloud and drops **CSV + JSON** files into a folder your billing/inventory
software imports — no API needed on the software's side.

## How it works

```
Cloud backend  ──(agent polls, authenticated)──►  Desktop Agent  ──writes files──►  C:\PharmacySoftware\import
```

The owner creates a **Desktop Agent** connector in MediScan Settings and gets a
one-time **pairing code**. You pair the agent once; it then polls for new
documents and writes files until stopped.

## Install

1. Install Python 3.10+ (or use the packaged `agent.exe`).
2. `pip install -r requirements.txt`
3. Copy `agent_config.example.json` to `agent_config.json` and set `output_dir`
   to your software's import folder.

## Pair (one time)

```powershell
python agent.py --pair ABCD1234 --base-url https://api.mediscan.example.com
```

This stores a long-lived agent token in `agent_config.json`. **Keep this file
private** — it authorizes this PC to receive your shop's data.

## Run

```powershell
python agent.py            # polls forever
python agent.py --once     # single poll (useful for testing)
```

## Run automatically at startup (recommended)

Register a Windows Scheduled Task that runs `python agent.py` (or `agent.exe`)
"At log on" with "Restart on failure". Point "Start in" at this folder.

## Package as a single .exe

```powershell
pip install pyinstaller
pyinstaller --onefile agent.py
# dist\agent.exe is self-contained
```

## Output

For each approved document the agent writes:
- `<document_id>.json` — the full structured payload
- `<document_id>.csv` — flattened rows (one per medication / invoice line item)

## Security notes

- The agent token is scoped to a single connector/shop and can be revoked by
  deleting/disabling the connector in MediScan.
- The agent only makes **outbound** HTTPS calls; no inbound ports are opened.
