#!/usr/bin/env python3
"""Deterministic extractor — FY2027 ICD-10-CM registry entry from CMS source ZIPs.

Derives the FY2027 ICD-10-CM fiscal-year vintage row for
``src/medecon_verify/data/registries/icd10cm_fy.json`` *entirely* from the
official CMS FY2027 ICD-10-CM release ZIPs downloaded under
``~/.gstack/projects/medecon-stack/cms-sources/icd10cm-fy2027/``.

Anti-fabrication contract
-------------------------
Every value this script writes to the registry is derived here from a real
downloaded file whose SHA-256 is verified against the recorded manifest *before*
the file is read. If any source ZIP is missing, its checksum mismatches, or a
cross-check fails (billable count vs. the codes file; the conversion-table
filename's effective date vs. the statutory Oct-01 boundary), the script raises
and writes nothing. No value is ever hand-typed or guessed.

Idempotent + re-runnable
------------------------
Re-running overwrites the FY2027 row with the same derived values and leaves the
other rows byte-identical. ``--check`` derives and prints without writing.

MS-DRG v44 deferral
-------------------
This script intentionally does NOT emit an MS-DRG v44 row. As of extraction the
FY2027 IPPS Final Rule is unpublished; MS-DRG v44 exists only as a proposed-rule
"Test GROUPER" whose weights/logic can change before the final rule. See
``extract_ms_drg_v44`` (a ready-to-implement stub) and the workpaper
``docs/workpapers/fy2027-registry-sources.md`` for the CMS re-check URL.

Usage
-----
    python3 tools/extract_icd10cm_fy2027.py            # derive + write registry
    python3 tools/extract_icd10cm_fy2027.py --check    # derive + print, no write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from datetime import date
from pathlib import Path

# --------------------------------------------------------------------------- #
# Source manifest — paths + expected SHA-256 (from the acquisition MANIFEST.md).
# The script verifies every one of these before reading anything; the two files
# actually parsed for derivation are flagged `parsed=True`.
# --------------------------------------------------------------------------- #
DEFAULT_SOURCE_DIR = (
    Path.home()
    / ".gstack/projects/medecon-stack/cms-sources/icd10cm-fy2027"
)

# name -> (sha256, source_url, parsed?)
SOURCES: dict[str, tuple[str, str, bool]] = {
    "2027-code-descriptions-tabular-order.zip": (
        "91c6c9d1117764ce72375a0f3a5493b1725dafbc1ab283b55799076c9e194965",
        "https://www.cms.gov/files/zip/2027-code-descriptions-tabular-order.zip",
        True,
    ),
    "2027-code-tables-tabular-index.zip": (
        "37baa476323714be16f95c9b2c96812bf6f3623d9f15188866e584dc529b0298",
        "https://www.cms.gov/files/zip/2027-code-tables-tabular-index.zip",
        False,
    ),
    "2027-conversion-table.zip": (
        "9478b8f95e137177e18be3df906c35566674b16313b12740d9e8e3778cb0e67a",
        "https://www.cms.gov/files/zip/2027-conversion-table.zip",
        True,
    ),
    "2027-icd-10-addendum.zip": (
        "16986a34d1458e549217229686f1a487cf6419ee6acb1043fbc770f8fabdd026",
        "https://www.cms.gov/files/zip/2027-icd-10-addendum.zip",
        False,
    ),
    "2027-poa-exempt-codes.zip": (
        "03f55091045024d07c99860470b9069ef2db4c19242d4c2ba0e9f30905bea904",
        "https://www.cms.gov/files/zip/2027-poa-exempt-codes.zip",
        False,
    ),
    "2027-version-update-summary.zip": (
        "c26b19c9246f24ecaccf056eb21640bf1cdded07527910e24bb1d006494f2860",
        "https://www.cms.gov/files/zip/2027-version-update-summary.zip",
        False,
    ),
}

# Inner ZIP members read for derivation (matched by basename, any directory).
ORDER_FILE_RE = re.compile(r"icd10cm_order_(\d{4})\.txt$")
CODES_FILE_RE = re.compile(r"icd10cm_codes_(\d{4})\.txt$")
# Conversion-table filename encodes the FY + effective date + FINAL/PROPOSED
# status, e.g. "ICD-10-CM-CONVERSION-TABLE-FY2027-October 1 2026 - FINAL-.xlsx".
CONVERSION_RE = re.compile(
    r"CONVERSION-TABLE-FY(?P<fy>\d{4})-"
    r"(?P<month>[A-Za-z]+) (?P<day>\d{1,2}) (?P<year>\d{4})"
    r" - (?P<status>FINAL|PROPOSED)",
    re.IGNORECASE,
)
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

# The billable-flag column in the fixed-width CMS order file (1-indexed col 15,
# i.e. offset 14). "1" = valid billable code; "0" = non-billable header/category.
_ORDER_FLAG_OFFSET = 14

REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent
    / "src/medecon_verify/data/registries/icd10cm_fy.json"
)


class ExtractionError(RuntimeError):
    """A source file is missing, a checksum mismatches, or a cross-check fails."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sources(source_dir: Path) -> dict[str, str]:
    """Verify every manifested ZIP exists and matches its recorded SHA-256.

    Returns {name: sha256}. Raises ExtractionError on the first problem — the
    script must never derive a value from an unverified or wrong file.
    """
    checksums: dict[str, str] = {}
    for name, (expected, _url, _parsed) in SOURCES.items():
        path = source_dir / name
        if not path.is_file():
            raise ExtractionError(
                f"source file missing: {path} — cannot derive FY2027 registry "
                "without the verified CMS release. Re-run the acquisition."
            )
        actual = _sha256(path)
        if actual != expected:
            raise ExtractionError(
                f"SHA-256 mismatch for {name}:\n  expected {expected}\n  actual   "
                f"{actual}\nRefusing to derive from a file that does not match the "
                "recorded manifest."
            )
        checksums[name] = actual
    return checksums


def _read_member(zip_path: Path, name_re: re.Pattern) -> tuple[str, bytes]:
    """Return (member_name, raw_bytes) for the single member matching name_re."""
    with zipfile.ZipFile(zip_path) as zf:
        matches = [n for n in zf.namelist() if name_re.search(n)]
        if len(matches) != 1:
            raise ExtractionError(
                f"expected exactly one member matching {name_re.pattern!r} in "
                f"{zip_path.name}, found {len(matches)}: {matches}"
            )
        return matches[0], zf.read(matches[0])


def _count_order_lines(raw: bytes) -> tuple[int, int, int]:
    """Return (total, billable, header) code counts from the CMS order file.

    The order file is one record per line; column 15 (offset 14) is the billable
    flag: "1" = valid HIPAA billable code, "0" = category header / non-billable.
    Lines that are blank or too short to carry the flag are rejected loudly.
    """
    text = raw.decode("latin-1")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    billable = 0
    header = 0
    for ln in lines:
        if len(ln) <= _ORDER_FLAG_OFFSET:
            raise ExtractionError(
                f"order-file line too short to carry a billable flag: {ln!r}"
            )
        flag = ln[_ORDER_FLAG_OFFSET]
        if flag == "1":
            billable += 1
        elif flag == "0":
            header += 1
        else:
            raise ExtractionError(
                f"unexpected billable flag {flag!r} (expected '0'/'1') in line: "
                f"{ln!r}"
            )
    return len(lines), billable, header


def _count_codes_lines(raw: bytes) -> int:
    """Return the number of billable-code records in icd10cm_codes_YYYY.txt."""
    text = raw.decode("latin-1")
    return sum(1 for ln in text.split("\n") if ln.strip())


def _derive_effective_from_conversion(zip_path: Path) -> tuple[int, date, str]:
    """Derive (fy, effective_date, status) from the conversion-table filename(s).

    CMS encodes the fiscal year, the Oct-01 effective date, and the FINAL vs.
    PROPOSED status directly in the conversion-table member names. This gives an
    independent, file-sourced corroboration of the effective date rather than a
    hand-typed constant.

    The real CMS ZIP ships the same table under *multiple* members (a 508-
    compliant CSV and an XLSX), so this parses **every** matching member and
    requires they all agree on (fy, effective_date, status). Returning the first
    match by archive order would let a ZIP that mixed FINAL and PROPOSED — or
    conflicting effective dates — pass on member ordering alone, defeating the
    fail-closed guarantee.
    """
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    parsed: list[tuple[str, tuple[int, date, str]]] = []
    for n in names:
        m = CONVERSION_RE.search(n)
        if m is None:
            continue
        month = _MONTHS.get(m.group("month").lower())
        if month is None:
            raise ExtractionError(f"unparseable month in {n!r}")
        eff = date(int(m.group("year")), month, int(m.group("day")))
        parsed.append((n, (int(m.group("fy")), eff, m.group("status").upper())))
    if not parsed:
        raise ExtractionError(
            f"no CONVERSION-TABLE-FY####-<date> - FINAL member found in "
            f"{zip_path.name}; members: {names}"
        )
    distinct = {value for _n, value in parsed}
    if len(distinct) != 1:
        detail = ", ".join(f"{n!r}->{value}" for n, value in parsed)
        raise ExtractionError(
            f"conversion-table members disagree on (fy, effective, status) in "
            f"{zip_path.name}: {detail} — refusing to derive a vintage whose own "
            "source members contradict each other."
        )
    return parsed[0][1]


def derive_fy2027(source_dir: Path) -> dict:
    """Derive the FY2027 registry row + full provenance from verified sources.

    Every returned value is computed from a downloaded, checksum-verified CMS
    file. Cross-checks (raise on any inconsistency):
      * billable count from the order file == line count of the codes file;
      * the FY parsed from the order/codes/conversion filenames all agree;
      * the conversion-table filename is FINAL (not PROPOSED);
      * the conversion-table effective date == the statutory Oct-01 boundary
        date for that FY (date(FY-1, 10, 1)).
    """
    checksums = verify_sources(source_dir)

    desc_zip = source_dir / "2027-code-descriptions-tabular-order.zip"
    conv_zip = source_dir / "2027-conversion-table.zip"

    order_name, order_raw = _read_member(desc_zip, ORDER_FILE_RE)
    codes_name, codes_raw = _read_member(desc_zip, CODES_FILE_RE)
    order_fy = int(ORDER_FILE_RE.search(order_name).group(1))
    codes_fy = int(CODES_FILE_RE.search(codes_name).group(1))

    total, billable, header = _count_order_lines(order_raw)
    codes_lines = _count_codes_lines(codes_raw)

    conv_fy, conv_effective, conv_status = _derive_effective_from_conversion(conv_zip)

    # --- cross-checks -----------------------------------------------------
    if not (order_fy == codes_fy == conv_fy):
        raise ExtractionError(
            f"FY disagreement across source filenames: order={order_fy}, "
            f"codes={codes_fy}, conversion={conv_fy}"
        )
    fy = order_fy
    if billable != codes_lines:
        raise ExtractionError(
            f"billable count from order file ({billable}) != codes-file line "
            f"count ({codes_lines}); refusing to emit a count that its own "
            "sources disagree on."
        )
    if billable + header != total:
        raise ExtractionError(
            f"billable({billable}) + header({header}) != total({total})"
        )
    if conv_status != "FINAL":
        raise ExtractionError(
            f"conversion table is {conv_status}, not FINAL — refusing to stamp a "
            "non-final vintage into the registry."
        )
    # Statutory Oct-01 fiscal-year boundary: FY{Y} runs {Y-1}-10-01..{Y}-09-30.
    rule_effective = date(fy - 1, 10, 1)
    rule_obsolete = date(fy, 9, 30)
    if conv_effective != rule_effective:
        raise ExtractionError(
            f"conversion-table effective date {conv_effective.isoformat()} != "
            f"statutory Oct-01 boundary {rule_effective.isoformat()} for FY{fy}"
        )

    return {
        "fy": fy,
        "fy_key": f"FY{fy}",
        "effective": rule_effective.isoformat(),
        "obsolete": rule_obsolete.isoformat(),
        "counts": {
            "billable": billable,
            "header_non_billable": header,
            "total_order_entries": total,
            "codes_file_lines": codes_lines,
        },
        "source_members": {
            "order_file": order_name,
            "codes_file": codes_name,
        },
        "source_checksums": checksums,
        "conversion_table_status": conv_status,
    }


def _serialize_registry(reg: dict) -> str:
    """Serialize the registry in the repo's one-line-per-FY style, FY-sorted."""
    def fy_num(key: str) -> int:
        return int(key[2:]) if key.startswith("FY") and key[2:].isdigit() else 0

    keys = sorted(reg, key=fy_num)
    lines = ["{"]
    for i, k in enumerate(keys):
        entry = reg[k]
        body = json.dumps(entry, separators=(", ", ": "))
        comma = "," if i < len(keys) - 1 else ""
        lines.append(f"  {json.dumps(k)}: {body}{comma}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def apply_to_registry(derived: dict, registry_path: Path = REGISTRY_PATH) -> bool:
    """Write the derived FY row into the registry JSON. Returns True if changed."""
    reg = json.loads(registry_path.read_text(encoding="utf-8"))
    new_row = {"effective": derived["effective"], "obsolete": derived["obsolete"]}
    before = reg.get(derived["fy_key"])
    reg[derived["fy_key"]] = new_row
    text = _serialize_registry(reg)
    changed = before != new_row or registry_path.read_text(encoding="utf-8") != text
    registry_path.write_text(text, encoding="utf-8")
    return changed


# --------------------------------------------------------------------------- #
# MS-DRG v44 — DEFERRED (FY2027 IPPS Final Rule unpublished at extraction).
# --------------------------------------------------------------------------- #
MS_DRG_V44_DEFERRAL = (
    "MS-DRG v44 is DEFERRED. As of the FY2027 acquisition the FY2027 IPPS Final "
    "Rule is unpublished; v44 exists only as a proposed-rule 'Test GROUPER' whose "
    "weights and grouping logic can change before the final rule. Do NOT add a "
    "v44 registry row from proposed-rule content. Re-check for the FINAL rule at: "
    "https://www.cms.gov/medicare/payment/prospective-payment-systems/"
    "acute-inpatient-pps/fy-2027-ipps-final-rule-home-page  and the MS-DRG "
    "Classifications and Software page: https://www.cms.gov/medicare/payment/"
    "prospective-payment-systems/acute-inpatient-pps/ms-drg-classifications-and-software"
)


def extract_ms_drg_v44(final_rule_dir: Path) -> dict:
    """Ready-to-implement stub: ingest MS-DRG v44 from the FINAL-rule docs.

    Intentionally unimplemented while v44 is proposed-rule-only. When CMS posts
    the FY2027 IPPS Final Rule, wire this to derive the v44 row (fy=FY2027,
    effective 2026-10-01) from the final Definitions Manual / Table 5, then add
    ``{"v44": {"fy": "FY2027", "effective": "2026-10-01"}}`` to ms_drg.json via a
    serializer mirroring ``apply_to_registry``. Fails loudly until then.
    """
    raise NotImplementedError(MS_DRG_V44_DEFERRAL)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir", type=Path, default=DEFAULT_SOURCE_DIR,
        help="directory holding the verified FY2027 ICD-10-CM release ZIPs",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="derive + print the JSON summary without writing the registry",
    )
    args = parser.parse_args(argv)

    try:
        derived = derive_fy2027(args.source_dir)
    except ExtractionError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(derived, indent=2))
    if args.check:
        print("\n--check: registry NOT written.", file=sys.stderr)
        return 0

    changed = apply_to_registry(derived)
    print(
        f"\nRegistry {'updated' if changed else 'already current'}: "
        f"{REGISTRY_PATH} (FY{derived['fy']} = {derived['effective']} .. "
        f"{derived['obsolete']})",
        file=sys.stderr,
    )
    print(f"\nNOTE: {MS_DRG_V44_DEFERRAL}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
