# core/controller.py
from drivers.discovery import scan_usb_usbtmc
from core.meter import Meter
from core.constants import DEFAULT_BACKENDS, SCPI_QUERY_POWER, SCPI_QUERY_FREQ, SCPI_SET_FREQ, SCPI_ZERO
from core.utils import parse_float


class MeasurementController:
    """
    Контроллер без изменения логики интерфейса:
    - скан устройств
    - подключение (в т.ч. 'FAKE')
    - чтение мощности/частоты
    - установка частоты
    - zero
    """

    def __init__(self):
        self.meter = Meter(DEFAULT_BACKENDS)
        self.resource = None

    # --- discovery ---
    def scan(self):
        addrs = scan_usb_usbtmc(DEFAULT_BACKENDS)
        # добавим симулятор
        if "FAKE" not in addrs:
            addrs = list(addrs) + ["FAKE"]
        return addrs

    # --- connect ---
    def connect(self, resource: str):
        self.meter.connect(resource)
        self.resource = resource
        return self.meter.idn()

    def is_connected(self) -> bool:
        return self.meter is not None and self.resource is not None

    def backend_in_use(self) -> str:
        return self.meter.backend_in_use or ""

    # --- operations ---
    def read_power(self) -> float:
        raw = self.meter.query(SCPI_QUERY_POWER)
        val = parse_float(raw)
        if val is None:
            raise RuntimeError(f"Parse error: {raw}")
        return val

    def get_freq(self) -> int:
        raw = self.meter.query(SCPI_QUERY_FREQ)
        val = parse_float(raw)
        if val is None:
            raise RuntimeError(f"Parse error: {raw}")
        return int(val)

    def set_freq(self, hz: int | float | str):
        self.meter.write(SCPI_SET_FREQ.format(freq=int(parse_float(str(hz)))))

    def zero(self):
        self.meter.write(SCPI_ZERO)

    # --- cleanup ---
    def close(self):
        if self.meter:
            self.meter.close()
            self.meter = None
            self.resource = None
