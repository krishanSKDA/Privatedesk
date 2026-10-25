import re

SAFE_LABEL = re.compile(r"[A-Za-z_]{1,40}")
LEGAL_SUFFIXES = {"ltd", "llc", "gmbh", "pte", "co", "inc", "plc", "limited", "company", "corp"}


def normalise_supplier(name):
    return " ".join(token for token in re.findall(r"[a-z0-9]+", (name or "").lower()) if token not in LEGAL_SUFFIXES)


def human_size(num_bytes):
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes} bytes"


def describe_api_error(error):
    details = []
    code = getattr(error, "code", None)
    status = getattr(error, "status_code", None) or (code if isinstance(code, int) else None)
    if status:
        details.append(f"HTTP {status}")
    for label in (code, getattr(error, "status", None)):
        if isinstance(label, str) and SAFE_LABEL.fullmatch(label) and label not in details:
            details.append(label)
    return error.__class__.__name__ + (f" ({', '.join(details)})" if details else "")


def relative_to_root(path, root):
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name
