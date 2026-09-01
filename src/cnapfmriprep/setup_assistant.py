"""Local browser assistant for selecting DICOM series and generating study YAML."""

from __future__ import annotations

import csv
import html
import json
import re
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

from .config import load_config
from .errors import ValidationError

_BIDS_LABEL = re.compile(r"^[A-Za-z0-9]+$")
_B0_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]+$")
_PE_DIRECTIONS = {"i", "i-", "j", "j-", "k", "k-"}
_ROLES = {"ignore", "bold", "fmap_ap", "fmap_pa", "anat"}


def load_inventory_tsv(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    try:
        with source.open(newline="") as stream:
            rows = [dict(row) for row in csv.DictReader(stream, delimiter="\t")]
    except OSError as error:
        raise ValidationError(f"Could not read DICOM inventory {source}: {error}") from error
    if not rows or any(not row.get("SeriesInstanceUID") for row in rows):
        raise ValidationError(
            f"Inventory must contain at least one row and a SeriesInstanceUID column: {source}"
        )
    return rows


def _file_count(row: dict[str, Any]) -> int:
    try:
        return int(row.get("NumberOfFiles", 0))
    except (TypeError, ValueError):
        return 0


def suggest_role(row: dict[str, Any]) -> str:
    """Make a conservative suggestion; the browser always requires user review."""
    description = " ".join(
        str(row.get(field, ""))
        for field in ("SeriesDescription", "ProtocolName", "SequenceName", "ImageType")
    ).lower()
    count = _file_count(row)
    if any(word in description for word in ("scout", "localizer", "survey")):
        return "ignore"
    if any(word in description for word in ("mprage", "t1w", "t1_mpr", "t1-")):
        return "anat"
    if count <= 50 and any(word in description for word in ("bliprev", "blip_rev", "reverse", "revpe", "_pa", "-pa")):
        return "fmap_pa"
    if count <= 50 and any(word in description for word in ("topup", "fieldmap", "run", "bold", "ret")):
        return "fmap_ap"
    if count >= 50 and any(word in description for word in ("run", "bold", "fmri", "ret", "task")):
        return "bold"
    return "ignore"


def _sort_value(value: Any) -> tuple[int, float | str]:
    try:
        return (0, float(str(value)))
    except ValueError:
        return (1, str(value))


def _series_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _sort_value(row.get("SeriesNumber", "")),
        _sort_value(row.get("AcquisitionTime", "")),
        str(row.get("SeriesInstanceUID", "")),
    )


def _exact_uid_expression(uids: list[str]) -> str:
    return "^(?:" + "|".join(re.escape(uid) for uid in uids) + ")$"


def _required_label(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key, "")).strip()
    if not value or not _BIDS_LABEL.fullmatch(value):
        raise ValidationError(
            f"{key} must contain only letters and digits and cannot be empty"
        )
    return value


def _required_b0_identifier(payload: dict[str, Any]) -> str:
    value = str(payload.get("b0_identifier", "")).strip()
    if not value or not _B0_IDENTIFIER.fullmatch(value):
        raise ValidationError(
            "b0_identifier must contain only letters, digits, underscores, or hyphens"
        )
    return value


def _required_pe(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key, "")).strip()
    if value not in _PE_DIRECTIONS:
        raise ValidationError(f"{key} must be one of {sorted(_PE_DIRECTIONS)}")
    return value


def build_generated_config(
    template: dict[str, Any],
    inventory_rows: list[dict[str, Any]],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Replace session mapping rules while retaining stable processing settings."""
    if not bool(payload.get("confirmed")):
        raise ValidationError("Review the series roles and confirm the selections first")
    by_uid = {str(row["SeriesInstanceUID"]): row for row in inventory_rows}
    roles = payload.get("roles")
    if not isinstance(roles, dict):
        raise ValidationError("The setup selection is missing its series roles")
    unknown = sorted(set(roles) - set(by_uid))
    if unknown:
        raise ValidationError(f"Unknown SeriesInstanceUID selection(s): {unknown}")
    invalid_roles = sorted({str(role) for role in roles.values()} - _ROLES)
    if invalid_roles:
        raise ValidationError(f"Unknown series role(s): {invalid_roles}")

    selected: dict[str, list[dict[str, Any]]] = {role: [] for role in _ROLES}
    for uid, row in by_uid.items():
        role = str(roles.get(uid, "ignore"))
        selected[role].append(row)
    bold_rows = sorted(selected["bold"], key=_series_sort_key)
    ap_rows = selected["fmap_ap"]
    pa_rows = selected["fmap_pa"]
    if not bold_rows:
        raise ValidationError("Select at least one BOLD series")
    if len(ap_rows) != 1 or len(pa_rows) != 1:
        raise ValidationError(
            "Select exactly one normal-polarity AP series and one reversed-polarity PA series"
        )

    task = _required_label(payload, "task")
    acquisition = _required_label(payload, "acquisition")
    b0_identifier = _required_b0_identifier(payload)
    bold_pe = _required_pe(payload, "bold_phase_encoding_direction")
    ap_pe = _required_pe(payload, "ap_phase_encoding_direction")
    pa_pe = _required_pe(payload, "pa_phase_encoding_direction")
    if ap_pe.rstrip("-") != pa_pe.rstrip("-") or ap_pe.endswith("-") == pa_pe.endswith("-"):
        raise ValidationError("The AP and PA phase-encoding directions must be opposite polarities")

    reference_uid = str(payload.get("reference_uid", "")).strip()
    bold_uids = [str(row["SeriesInstanceUID"]) for row in bold_rows]
    if not reference_uid:
        reference_uid = bold_uids[0]
    if reference_uid not in bold_uids:
        raise ValidationError("The motion-reference series must be one of the selected BOLD runs")
    reference_run = bold_uids.index(reference_uid) + 1

    rules: list[dict[str, Any]] = [
        {
            "name": "selected_bold_runs",
            "kind": "bold_with_norf",
            "match": {"SeriesInstanceUID": _exact_uid_expression(bold_uids)},
            "task": task,
            "acquisition": acquisition,
            "run": "auto",
            "run_start": 1,
            "run_sort_by": "SeriesNumber",
            "b0_identifier": b0_identifier,
            "phase_encoding_direction": bold_pe,
            "expected_matches": len(bold_rows),
        },
        {
            "name": "selected_topup_ap",
            "kind": "fmap_epi",
            "match": {
                "SeriesInstanceUID": _exact_uid_expression(
                    [str(ap_rows[0]["SeriesInstanceUID"])]
                )
            },
            "acquisition": "bold",
            "direction": "AP",
            "b0_identifier": b0_identifier,
            "phase_encoding_direction": ap_pe,
            "expected_matches": 1,
        },
        {
            "name": "selected_topup_pa",
            "kind": "fmap_epi",
            "match": {
                "SeriesInstanceUID": _exact_uid_expression(
                    [str(pa_rows[0]["SeriesInstanceUID"])]
                )
            },
            "acquisition": "bold",
            "direction": "PA",
            "b0_identifier": b0_identifier,
            "phase_encoding_direction": pa_pe,
            "expected_matches": 1,
        },
    ]
    anat_rows = sorted(selected["anat"], key=_series_sort_key)
    for index, row in enumerate(anat_rows, 1):
        rule = {
                "name": f"selected_anat_{index:02d}",
                "kind": "anat",
                "match": {
                    "SeriesInstanceUID": _exact_uid_expression(
                        [str(row["SeriesInstanceUID"])]
                    )
                },
                "suffix": "T1w",
                "expected_matches": 1,
            }
        if len(anat_rows) > 1:
            rule["run"] = index
        rules.append(rule)

    generated = dict(template)
    ingest = dict(generated.get("ingest") or {})
    ingest["dataset_name"] = str(payload.get("dataset_name", "")).strip() or str(
        ingest.get("dataset_name", "CNAP fMRI Prep study")
    )
    ingest["series_rules"] = rules
    generated["ingest"] = ingest
    multi_run = dict(generated.get("multi_run") or {})
    multi_run.update(
        {
            "shared_topup": True,
            "shared_motion_reference": True,
            "reference_task": task,
            "reference_run": reference_run,
        }
    )
    generated["multi_run"] = multi_run
    return generated


def write_generated_config(
    template_file: str | Path,
    output_file: str | Path,
    inventory_rows: list[dict[str, Any]],
    payload: dict[str, Any],
    *,
    overwrite: bool = False,
) -> Path:
    template_path = Path(template_file).expanduser().resolve()
    target = Path(output_file).expanduser().resolve()
    if target.exists() and not overwrite:
        raise ValidationError(f"Configuration already exists; use --overwrite: {target}")
    try:
        template = yaml.safe_load(template_path.read_text())
    except (OSError, yaml.YAMLError) as error:
        raise ValidationError(f"Could not read template {template_path}: {error}") from error
    if not isinstance(template, dict):
        raise ValidationError(f"Template must contain a YAML mapping: {template_path}")
    generated = build_generated_config(template, inventory_rows, payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        "# Generated locally by `cnapfmriprep setup`. Review before ingestion.\n"
        + yaml.safe_dump(generated, sort_keys=False)
    )
    temporary.replace(target)
    load_config(target)
    return target


def _role_options(selected: str) -> str:
    options = [
        ("ignore", "Ignore"),
        ("bold", "BOLD + no-RF"),
        ("fmap_ap", "TOPUP normal (AP)"),
        ("fmap_pa", "TOPUP reversed (PA)"),
        ("anat", "Anatomical T1w"),
    ]
    return "".join(
        f'<option value="{value}"{" selected" if value == selected else ""}>{label}</option>'
        for value, label in options
    )


def render_setup_page(rows: list[dict[str, Any]], token: str) -> str:
    table_rows = []
    for row in rows:
        uid = str(row["SeriesInstanceUID"])
        suggestion = suggest_role(row)
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('SeriesNumber', '')))}</td>"
            f"<td>{html.escape(str(row.get('AcquisitionTime', '')))}</td>"
            f"<td>{html.escape(str(row.get('SeriesDescription', '')))}</td>"
            f"<td>{html.escape(str(row.get('ProtocolName', '')))}</td>"
            f"<td>{html.escape(str(row.get('NumberOfFiles', '')))}</td>"
            f'<td><select class="role" data-uid="{html.escape(uid)}">{_role_options(suggestion)}</select></td>'
            f'<td><input class="reference" type="radio" name="reference" value="{html.escape(uid)}" disabled></td>'
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>CNAP fMRI Prep session setup</title>
<style>
body{{font:15px system-ui,sans-serif;margin:2rem;color:#17202a;background:#f6f8fa}}
main{{max-width:1500px;margin:auto;background:white;padding:1.5rem;border-radius:12px;box-shadow:0 2px 14px #0002}}
h1{{margin-top:0}} .grid{{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:1rem}}
label{{display:flex;flex-direction:column;gap:.35rem;font-weight:600}} input,select{{padding:.45rem;font:inherit}}
table{{border-collapse:collapse;width:100%;margin:1.5rem 0}} th,td{{border:1px solid #ccd1d7;padding:.45rem;text-align:left}}
th{{background:#eef2f5;position:sticky;top:0}} .scroll{{max-height:58vh;overflow:auto}}
.confirm{{display:block;margin:1rem 0}} button{{padding:.7rem 1rem;font-weight:700}} #status{{margin-left:1rem}}
</style></head><body><main>
<h1>CNAP fMRI Prep session setup</h1>
<p>This page is served only from your Mac. Review every suggested role; no metadata leaves this computer.</p>
<div class="grid">
<label>Dataset name<input id="dataset" value="CNAP fMRI Prep study"></label>
<label>BIDS task<input id="task" value="retinotopy" pattern="[A-Za-z0-9]+"></label>
<label>BIDS acquisition<input id="acquisition" value="hires7T" pattern="[A-Za-z0-9]+"></label>
<label>Shared field identifier<input id="b0" value="pepolar_session01" pattern="[A-Za-z0-9_-]+"></label>
<label>BOLD phase direction<select id="boldpe"><option>j-</option><option>j</option><option>i-</option><option>i</option><option>k-</option><option>k</option></select></label>
<label>Normal/AP direction<select id="appe"><option>j-</option><option>j</option><option>i-</option><option>i</option><option>k-</option><option>k</option></select></label>
<label>Reversed/PA direction<select id="pape"><option>j</option><option>j-</option><option>i</option><option>i-</option><option>k</option><option>k-</option></select></label>
</div>
<div class="scroll"><table><thead><tr><th>Series</th><th>Time</th><th>Description</th><th>Protocol</th><th>Files</th><th>Role</th><th>Motion reference</th></tr></thead>
<tbody>{''.join(table_rows)}</tbody></table></div>
<label class="confirm"><span><input type="checkbox" id="confirmed"> I reviewed all roles, the TOPUP pair, phase directions, run ordering, and motion reference.</span></label>
<button id="generate">Generate configuration</button><span id="status"></span>
<script>
const token={json.dumps(token)};
function updateReferences(){{
  const bold=[]; document.querySelectorAll('.role').forEach(s=>{{if(s.value==='bold') bold.push(s.dataset.uid)}});
  document.querySelectorAll('.reference').forEach(r=>{{r.disabled=!bold.includes(r.value); if(r.disabled)r.checked=false}});
  if(!document.querySelector('.reference:checked') && bold.length){{const r=[...document.querySelectorAll('.reference')].find(x=>x.value===bold[0]);if(r)r.checked=true}}
}}
document.querySelectorAll('.role').forEach(s=>s.addEventListener('change',updateReferences)); updateReferences();
document.getElementById('generate').onclick=async()=>{{
 const status=document.getElementById('status'); status.textContent='Validating…';
 const value=id=>document.getElementById(id).value;
 const roles={{}}; document.querySelectorAll('.role').forEach(s=>roles[s.dataset.uid]=s.value);
 const ref=document.querySelector('.reference:checked');
 const payload={{dataset_name:value('dataset'),task:value('task'),acquisition:value('acquisition'),b0_identifier:value('b0'),
 bold_phase_encoding_direction:value('boldpe'),ap_phase_encoding_direction:value('appe'),pa_phase_encoding_direction:value('pape'),
 roles:roles,reference_uid:ref?ref.value:'',confirmed:document.getElementById('confirmed').checked}};
 try{{const response=await fetch('/generate?token='+encodeURIComponent(token),{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}});
 const result=await response.json(); if(!response.ok)throw new Error(result.error||'Generation failed'); status.textContent='Saved: '+result.output; document.getElementById('generate').disabled=true;
 }}catch(error){{status.textContent=error.message}}
}};
</script></main></body></html>"""


def run_setup_server(
    rows: list[dict[str, Any]],
    *,
    template_file: str | Path,
    output_file: str | Path,
    overwrite: bool = False,
    open_browser: bool = True,
) -> dict[str, Any]:
    """Serve one token-protected local setup form and stop after successful save."""
    token = secrets.token_urlsafe(24)
    page = render_setup_page(rows, token).encode()
    result: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def _headers(self, status: HTTPStatus, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'",
            )
            self.end_headers()

        def _authorized(self) -> bool:
            query = parse_qs(urlparse(self.path).query)
            return secrets.compare_digest(query.get("token", [""])[0], token)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if urlparse(self.path).path != "/" or not self._authorized():
                self._headers(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8")
                self.wfile.write(b"Not found")
                return
            self._headers(HTTPStatus.OK, "text/html; charset=utf-8")
            self.wfile.write(page)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if urlparse(self.path).path != "/generate" or not self._authorized():
                self._headers(HTTPStatus.NOT_FOUND, "application/json")
                self.wfile.write(b'{"error":"Not found"}')
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 2_000_000:
                    raise ValidationError("Invalid request size")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValidationError("Expected a JSON selection object")
                output = write_generated_config(
                    template_file,
                    output_file,
                    rows,
                    payload,
                    overwrite=overwrite,
                )
                result["output"] = str(output)
                body = json.dumps({"output": str(output)}).encode()
                self._headers(HTTPStatus.OK, "application/json")
                self.wfile.write(body)
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            except (ValidationError, json.JSONDecodeError) as error:
                body = json.dumps({"error": str(error)}).encode()
                self._headers(HTTPStatus.BAD_REQUEST, "application/json")
                self.wfile.write(body)

        def log_message(self, *_: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_port}/?token={token}"
    print(f"CNAP fMRI Prep setup is available locally at:\n{url}\n")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return {"url": url, **result}
