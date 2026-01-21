# services/split_engine.py

import re

class SplitError(Exception):
    pass

VALID_CODES = ["H", "D", "JR", "K"]

def parse_campus_string(raw: str):
    if not raw:
        raise SplitError("Campus veld is leeg.")

    text = raw.upper().replace(",", " ").strip()
    tokens = [t for t in text.split() if t.strip()]
    result = []
    has_percent = False

    for t in tokens:
        # %40H, 40H, H40, H%40
        m = re.match(r"%?(\d+)%?([A-Z]+)$", t)
        if m:
            percent = int(m.group(1))
            code = m.group(2)

            if code not in VALID_CODES:
                raise SplitError(f"Ongeldige campuscode: '{code}'")

            result.append({"code": code, "percent": percent})
            has_percent = True
            continue

        # Pure campus name: H JR K D
        if t in VALID_CODES:
            result.append({"code": t, "percent": None})
            continue

        raise SplitError(f"Ongeldig campus token: '{t}'")

    # If no percentages → equal split
    if not has_percent:
        count = len(result)
        equal = round(100 / count, 5)
        for r in result:
            r["percent"] = equal

    # Control sum
    total = sum(r["percent"] for r in result)
    if abs(total - 100) > 0.01:
        raise SplitError(f"Percentages moeten optellen tot 100%. Nu: {total}")

    return result
