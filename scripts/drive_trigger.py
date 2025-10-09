#!/usr/bin/env python3
"""Drive trigger utility.

Usage (polling mode):
  python scripts/drive_trigger.py --api-base http://localhost:8080 --interval 30

This script polls the input Drive folder (DRIVE_INPUT_FOLDER_ID) for *new* PDF
files (by ID) and POSTs them to the /process_drive endpoint. It keeps a small
in-memory cache of processed IDs (optionally persisted to a state file) to avoid
reprocessing.

Environment variables required (or pass via flags):
  GOOGLE_APPLICATION_CREDENTIALS  (service account with Drive file read & create)
  DRIVE_INPUT_FOLDER_ID

Flags:
  --api-base   Base URL of deployed service (default http://localhost:8080)
  --interval   Poll interval seconds (default 60)
  --state-file Optional path to persist processed IDs across restarts

Apps Script Alternative (paste into script.google.com attached to Drive):
-----------------------------------------------------------------------
function onDriveTrigger(e) {
  // Install a time-based trigger to run this periodically (e.g., every 5 min)
  const folderId = 'YOUR_INPUT_FOLDER_ID';
  const apiBase = 'https://YOUR_CLOUD_RUN_URL';
  const processedProp = PropertiesService.getScriptProperties();
  const processedRaw = processedProp.getProperty('processed_ids') || '';
  const processed = new Set(processedRaw.split(',').filter(Boolean));
  const folder = DriveApp.getFolderById(folderId);
  const files = folder.getFiles();
  const newIds = [];
  while (files.hasNext()) {
    const f = files.next();
    if (f.getMimeType() === 'application/pdf' && !processed.has(f.getId())) {
      try {
        const resp = UrlFetchApp.fetch(`${apiBase}/process_drive?file_id=${f.getId()}`, { 'method': 'get', 'muteHttpExceptions': true });
        if (resp.getResponseCode() === 200) {
          processed.add(f.getId());
          newIds.push(f.getId());
        }
      } catch (err) {
        Logger.log('Error processing file ' + f.getId() + ': ' + err);
      }
    }
  }
  if (newIds.length) {
    processedProp.setProperty('processed_ids', Array.from(processed).join(','));
    Logger.log('Processed: ' + newIds.join(','));
  }
}
-----------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import os
import time
import json
from typing import Set
from dataclasses import dataclass

from googleapiclient.discovery import build  # type: ignore
from google.oauth2 import service_account  # type: ignore
import httpx

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


@dataclass
class DrivePollerConfig:
    input_folder: str
    api_base: str
    interval: int = 60
    state_file: str | None = None


def _drive_service():  # pragma: no cover - utility IO
    gac = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if not gac or not os.path.exists(gac):
        raise SystemExit('GOOGLE_APPLICATION_CREDENTIALS not set or file missing')
    creds = service_account.Credentials.from_service_account_file(gac, scopes=SCOPES)  # type: ignore[arg-type]
    return build('drive', 'v3', credentials=creds, cache_discovery=False)


def _list_pdfs(service, folder_id: str):  # pragma: no cover - external IO
    q = f"'{folder_id}' in parents and mimeType = 'application/pdf' and trashed = false"
    resp = service.files().list(q=q, fields="files(id,name,modifiedTime)").execute()  # type: ignore[attr-defined]
    return resp.get('files', [])


def _load_state(path: str | None) -> Set[str]:
    if not path or not os.path.exists(path):
        return set()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    except Exception:
        return set()


def _save_state(path: str | None, ids: Set[str]):  # pragma: no cover - file IO
    if not path:
        return
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(sorted(ids), f)
    os.replace(tmp, path)


def poll_loop(cfg: DrivePollerConfig):  # pragma: no cover - long-running loop
    service = _drive_service()
    processed = _load_state(cfg.state_file)
    print(f"[drive-trigger] Starting poll loop. Already have {len(processed)} processed IDs.")
    while True:
        try:
            files = _list_pdfs(service, cfg.input_folder)
            new = [f for f in files if f['id'] not in processed]
            if new:
                print(f"[drive-trigger] Found {len(new)} new pdf(s). Processing...")
            for f in new:
                fid = f['id']
                url = f"{cfg.api_base.rstrip('/')}/process_drive"
                try:
                    r = httpx.get(url, params={'file_id': fid}, timeout=120)
                    if r.status_code == 200:
                        processed.add(fid)
                        print(f"[drive-trigger] Processed {fid} -> {r.json().get('report_file_id')}")
                    else:
                        print(f"[drive-trigger] Failed {fid}: {r.status_code} {r.text[:200]}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[drive-trigger] Error {fid}: {exc}")
            if new:
                _save_state(cfg.state_file, processed)
            time.sleep(cfg.interval)
        except KeyboardInterrupt:
            print("[drive-trigger] Exiting on Ctrl+C")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[drive-trigger] Loop error: {exc}; sleeping {cfg.interval}s")
            time.sleep(cfg.interval)


def main():  # pragma: no cover
    ap = argparse.ArgumentParser(description="Poll Drive folder and trigger summarisation")
    ap.add_argument('--api-base', default=os.environ.get('API_BASE', 'http://localhost:8080'))
    ap.add_argument('--interval', type=int, default=int(os.environ.get('POLL_INTERVAL', '60')))
    ap.add_argument('--state-file', default=os.environ.get('STATE_FILE'))
    ap.add_argument('--folder', default=os.environ.get('DRIVE_INPUT_FOLDER_ID'))
    args = ap.parse_args()
    if not args.folder:
        ap.error('Must provide --folder or set DRIVE_INPUT_FOLDER_ID')
    cfg = DrivePollerConfig(input_folder=args.folder, api_base=args.api_base, interval=args.interval, state_file=args.state_file)
    main_loop = poll_loop
    main_loop(cfg)


if __name__ == '__main__':  # pragma: no cover
    main()
