import random
import threading

class FakeMeter:
    """Симулятор с тем же интерфейсом, что и VisaMeter.
       Адрес подключения: 'FAKE'.
       READ -> случайные значения в диапазоне [-50; -40] dBm.
    """
    def __init__(self, *_args, **_kwargs):
        self._lock = threading.RLock()
        self.inst = True
        self.resource = "FAKE"
        self.backend_in_use = "FAKE"
        self._freq_hz = 1_000_000_000  # 1 ГГц по умолчанию

    def is_connected_to(self, resource: str) -> bool:
        return self.inst is not None and resource.upper() == "FAKE"

    def connect(self, resource: str, timeout_ms: int = 0):
        if resource.upper() != "FAKE":
            raise RuntimeError("FakeMeter: неверный ресурс (ожидалось 'FAKE')")
        # уже «подключен»
        return

    def close(self):
        self.inst = None

    def _random_dbm(self) -> float:
        return random.uniform(-50.0, -40.0)

    def query(self, cmd: str) -> str:
        cmdu = cmd.strip().upper()
        if cmdu.startswith("*IDN?"):
            return "FAKE,USB Power Sensor Simulator,0,1.0"
        if cmdu.startswith("MEAS:POW?") or cmdu.startswith("READ?") or cmdu.startswith("FETC:POW?"):
            return f"{self._random_dbm():.3f}"
        if cmdu.startswith("SENS:FREQ?") or cmdu.startswith("FREQ?"):
            return str(self._freq_hz)
        # неизвестные запросы просто эхо
        return "0"

    def write(self, cmd: str):
        cmdu = cmd.strip().upper()
        if cmdu.startswith("SENS:POW:ZERO:IMM") or cmdu.startswith("CAL:ZERO"):
            return  # no-op
        if cmdu.startswith("SENS:FREQ ") or cmdu.startswith("FREQ "):
            # ожидаем '... {freq}'
            try:
                parts = cmdu.split()
                val = int(parts[-1])
                self._freq_hz = val
            except Exception:
                pass
        # иные команды игнорируем

    def idn(self) -> str:
        return "Фейкметрон,USB Power Sensor Simulator,0,1.0"
