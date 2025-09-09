import re

_UNIT = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}

def parse_float(text: str):
    """Достаёт число из строки + применяет суффиксы Hz/kHz/MHz/GHz.
    dBm/прочие единицы не трогаем: просто число."""
    if not text:
        return None
    t = str(text).strip()
    m = re.search(r'([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([a-zA-Z]+)?', t)
    if not m:
        return None
    val = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if unit in _UNIT:
        val *= _UNIT[unit]
    return val
