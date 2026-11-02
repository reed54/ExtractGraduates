#!/usr/bin/env python3
"""Prototype for the 1976 and 1977 Golden Daily Transcript rosters, PDF page 4.

Install: python -m pip install pdfplumber
Run: python extract_graduates.py GOT_19770505.pdf --profile 1977 --out graduates

Produces candidates.csv, lines.json, and review.html. Every candidate needs
visual verification. This uses the existing OCR; it does not run new OCR.
Coordinates are PDF points, origin at top left. Profiles are issue-specific.
No automatic spelling correction or deduplication is performed.
"""
import argparse
import csv
import html
import json
import re
from pathlib import Path

import pdfplumber

# x0, y0, x1, y1, name/hometown divider. Inspect lines.json to tune.
REGIONS = [
    (20, 550, 218, 1165, 112),
    (220, 550, 418, 1165, 316),
    (423, 57, 615, 1165, 516),
    (620, 57, 815, 1165, 715),
]
DEGREES = ("BACHELOR OF SCIENCE", "MASTER OF SCIENCE",
           "MASTER OF ENGINEERING", "ENGINEER OF MINES",
           "GEOLOGICAL ENGINEER")


def compact(s):
    return re.sub(r"[^A-Z]", "", s.upper())


def line_groups(words, tolerance=2):
    """Cluster by word top, then sort horizontally; never sort page-wide."""
    result = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if not result or abs(word["top"] - result[-1][0]["top"]) > tolerance:
            result.append([])
        result[-1].append(word)
    return [sorted(group, key=lambda w: w["x0"]) for group in result]


def joined(words):
    return " ".join(w["text"] for w in words)



PROFILES = {
    "1976": {"size": (835, 1193), "regions": REGIONS},
    "1977": {"size": (838, 1216), "regions": [
        (25, 46, 218, 1173, 140),
        (225, 318, 420, 1173, 340),
        (425, 318, 617, 1173, 540),
        (623, 39, 820, 1173, 738),
    ]},
}
PROGRAMS = [
    "Mining Engineering", "Metallurgical Engineering", "Geological Engineering",
    "Petroleum Engineering", "Chemical and Petroleum-Refining Engineering",
    "Chemicals and Petroleum-Refining Engineering", "Geophysical Engineering",
    "Mineral Engineering-Chemistry", "Mineral Engineering-Mathematics",
    "Mineral Engineering-Physics", "Mineral Engineering Physics",
    "Mineral Engineering", "Professional-Mineral Engineering",
    "Metallurgy", "Geology", "Geophysics", "Chemistry", "Geochemistry",
    "Mathematics", "Physics", "Mineral Economics",
]


def heading_1977(text):
    """Return (type, value, subject), or None. Exact vocabulary avoids names."""
    key = compact(text)
    if key == "BACHELORSDEGREES":
        return ("group", "bachelor", "")
    if key == "MASTERSDEGREES":
        return ("group", "master", "")
    if key == "DOCTORSOFPHILOSOPHY":
        return ("group", "doctorate", "")
    date = re.fullmatch(r"(MAY|JUNE|AUGUST)\s*(\d{1,2})[., ]+1977", text.upper())
    if date:
        month = {"MAY": "05", "JUNE": "06", "AUGUST": "08"}[date[1]]
        return ("date", f"1977-{month}-{int(date[2]):02d}", "")
    for prefix, degree in [("MS", "Master of Science"), ("ME", "Master of Engineering")]:
        if key.startswith(prefix):
            subject = key[len(prefix):]
            for program in PROGRAMS + ["Petroleum Engineer", "Metallurgical Engineer",
                                        "Metallurgical Engineeer", "Geological Engineer",
                                        "Geophysical Engineer"]:
                if subject == compact(program):
                    return ("degree", degree, program)
    for degree in ["Engineer of Mines", "Geophysical Engineer"]:
        if key == compact(degree):
            return ("degree", degree, "Mining Engineering" if degree == "Engineer of Mines"
                    else "Geophysical Engineering")
    for program in PROGRAMS:
        if key == compact(program):
            return ("program", program, "")
    # Visible OCR damage in the Petroleum Engineering heading.
    if key == compact("P«trol«um Engineering"):
        return ("program", "Petroleum Engineering", "")
    return None


def split_lanes(group, divider, profile):
    if profile == "1977" and len(group) > 1:
        # Hometowns are right aligned in 1977, so their left edge varies.
        gaps = [(group[i+1]["x0"]-group[i]["x1"], i+1)
                for i in range(len(group)-1)]
        gap, cut = max(gaps)
        if gap >= 10:
            return joined(group[:cut]), joined(group[cut:])
    return (joined([w for w in group if w["x0"] < divider]),
            joined([w for w in group if w["x0"] >= divider]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--profile", choices=PROFILES, help="Defaults to year in PDF filename")
    parser.add_argument("--out", type=Path, default=Path("graduates"))
    args = parser.parse_args()
    if not args.profile:
        matches = [year for year in PROFILES if year in args.pdf.name]
        if len(matches) != 1:
            parser.error("Specify --profile 1976 or --profile 1977")
        args.profile = matches[0]
    profile = PROFILES[args.profile]
    args.out.mkdir(parents=True, exist_ok=True)
    rows, audit = [], []
    state = dict(degree="", department="", subject="", department_basis="",
                 completion_date="", section_note="", degree_group="", heading_raw="")
    pending = []
    heading = None

    def emit():
        nonlocal pending
        if not pending:
            return
        name = " ".join(x["name"] for x in pending if x["name"])
        town = " ".join(x["hometown"] for x in pending if x["hometown"])
        flags = ["UNVERIFIED"]
        if not name or not town:
            flags.append("MISSING_FIELD")
        if len(pending) > 1:
            flags.append("WRAPPED_OR_AMBIGUOUS")
        if re.search(r"[*!†‡|]", name) or re.search(r"\b111\b", name):
            flags.append("CHECK_OCR_OR_MARKER")
        if not state["degree"] or not state["subject"]:
            flags.append("CHECK_HEADING")
        rows.append(dict(record_id=len(rows)+1, **state,
                         graduate_name=name, hometown=town,
                         page=4, column=pending[0]["column"],
                         bbox=json.dumps([
                             min(x["bbox"][0] for x in pending),
                             min(x["bbox"][1] for x in pending),
                             max(x["bbox"][2] for x in pending),
                             max(x["bbox"][3] for x in pending)]),
                         flags=";".join(flags),
                         raw_text=" / ".join(x["text"] for x in pending)))
        pending = []

    with pdfplumber.open(args.pdf) as pdf:
        page = pdf.pages[3]
        if abs(page.width-profile["size"][0]) > 1 or abs(page.height-profile["size"][1]) > 1:
            raise ValueError("Unexpected page size; recalibrate REGIONS first")
        for column, (x0, y0, x1, y1, divider) in enumerate(profile["regions"], 1):
            words = page.crop((x0, y0, x1, y1)).extract_words(
                x_tolerance=1, y_tolerance=2)
            groups = line_groups(words, tolerance=3.2 if args.profile == "1977" else 2)
            skip = 0
            for index, group in enumerate(groups):
                if skip:
                    skip -= 1
                    continue
                text = joined(group)
                item = dict(column=column, text=text,
                            bbox=[min(w["x0"] for w in group),
                                  min(w["top"] for w in group),
                                  max(w["x1"] for w in group),
                                  max(w["bottom"] for w in group)],
                            name=joined([w for w in group if w["x0"] < divider]),
                            hometown=joined([w for w in group if w["x0"] >= divider]))
                item["name"], item["hometown"] = split_lanes(group, divider, args.profile)
                audit.append(item)
                if args.profile == "1977":
                    if "above list" in text.lower():
                        emit()
                        note = text
                        # Retroactive note applies to preceding May bachelor section only.
                        for row in rows:
                            if row["completion_date"] == "1977-05-06":
                                row["section_note"] = note
                        continue
                    found = None
                    for count in range(1, min(3, len(groups)-index)+1):
                        # Wrapped heading lines are close; do not bridge large blank gaps.
                        subset = groups[index:index+count]
                        if count > 1 and subset[-1][0]["top"]-subset[-2][0]["top"] > 16:
                            break
                        candidate = " ".join(joined(g) for g in subset)
                        found = heading_1977(candidate)
                        if found:
                            skip = count-1
                            break
                    if found:
                        emit()
                        kind, value, subject = found
                        state["heading_raw"] = candidate
                        if kind == "group":
                            state.update(degree_group=value, subject="", department="",
                                         completion_date="", section_note="", degree="")
                        elif kind == "date":
                            state.update(completion_date=value, section_note="")
                        elif kind == "degree":
                            state.update(degree=value, subject=subject, department=subject,
                                         department_basis="inferred from program")
                        else:
                            degree = "Doctor of Philosophy" if state["degree_group"] == "doctorate" else "Bachelor's degree"
                            state.update(degree=degree, subject=value, department=value,
                                         department_basis="inferred from program")
                        continue
                    if item["name"] and pending and any(x["hometown"] for x in pending):
                        emit()
                    pending.append(item)
                    continue
                key = compact(text)
                if key.startswith("CLASSOF"):
                    emit()
                    continue
                degree = next((d for d in DEGREES if key == compact(d)), None)
                if degree:
                    emit()
                    state["degree"] = degree.title()
                    state["subject"] = ""
                    if degree == "BACHELOR OF SCIENCE":
                        state["department"] = ""
                        state["department_basis"] = "inferred from program"
                    if degree in ("ENGINEER OF MINES", "GEOLOGICAL ENGINEER"):
                        state["subject"] = "Mining Engineering" if degree == "ENGINEER OF MINES" else "Geological Engineering"
                        state["department"] = state["subject"]
                        state["department_basis"] = "inferred from degree"
                    heading = "await_subject"
                    continue
                if key.startswith("DEPARTMENTOF"):
                    emit()
                    state["department"] = re.sub(
                        r"^DEPARTMENT\s*OF\s*", "", text, flags=re.I)
                    state["department_basis"] = "printed heading"
                    heading = "department"
                    continue
                # Some MS subjects are printed on the degree-heading line.
                if key.startswith("MASTEROFSCIENCE") and "(" in text:
                    emit()
                    state["degree"] = "Master Of Science"
                    state["subject"] = text[text.index("(")+1:].rstrip(")")
                    heading = None
                    continue
                if text.startswith("(") and heading == "await_subject":
                    emit()
                    state["subject"] = text[1:].rstrip(")")
                    if state["department_basis"] == "inferred from program":
                        state["department"] = state["subject"]
                    heading = "subject" if ")" not in text else None
                    continue
                if heading == "department":
                    state["department"] += " " + text
                    continue
                if heading == "subject":
                    state["subject"] += " " + text.rstrip(")")
                    heading = None if ")" in text else heading
                    continue
                # A hometown is usually aligned to the last line of a wrapped
                # name. A new name after a completed pair starts a new record.
                # Hometown-only lines remain with the preceding record.
                if item["name"] and pending and any(x["hometown"] for x in pending):
                    emit()
                pending.append(item)
            emit()  # Keep heading state across the column boundary.
    emit()
    if not rows:
        raise RuntimeError("No candidates: check text layer and regions")
    with (args.out / "candidates.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (args.out / "lines.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    counts = {}
    for row in rows:
        key = " | ".join(row[k] for k in ("degree", "subject", "completion_date"))
        counts[key] = counts.get(key, 0) + 1
    (args.out / "summary.json").write_text(json.dumps(dict(
        profile=args.profile, source=str(args.pdf.resolve()), candidates=len(rows),
        verified=False, section_counts=counts), indent=2), encoding="utf-8")
    body = ["<meta charset='utf-8'><title>Graduate extraction review</title>",
            "<style>td,th{border:1px solid #bbb;padding:6px}table{border-collapse:collapse}</style>",
            "<h1>Unverified extraction candidates</h1>",
            "<p>Compare every row with printed page 4. Bounding boxes use PDF points, top-left origin.</p>",
            "<table><tr>" + "".join("<th>"+html.escape(k)+"</th>" for k in rows[0])+"</tr>"]
    body.extend("<tr>"+"".join("<td>"+html.escape(str(v))+"</td>" for v in r.values())+"</tr>" for r in rows)
    body.append("</table>")
    (args.out / "review.html").write_text("\n".join(body), encoding="utf-8")
    print(f"Wrote {len(rows)} unverified candidates to {args.out}")


if __name__ == "__main__":
    main()

