from typing import List

try:
    import pyvisa
    HAS_VISA = True
except Exception:
    pyvisa = None
    HAS_VISA = False

def scan_usb_usbtmc(backends: List[str]) -> List[str]:
    """Сканируем USB?*::INSTR по указанным backend'ам и объединяем без дублей."""
    addrs: List[str] = []
    seen = set()
    if not HAS_VISA:
        return addrs
    for be in backends:
        try:
            rm = pyvisa.ResourceManager(be) if be else pyvisa.ResourceManager()
            res = rm.list_resources("USB?*::INSTR")
            for r in res:
                if r not in seen:
                    seen.add(r)
                    addrs.append(r)
        except Exception:
            pass
    return addrs
