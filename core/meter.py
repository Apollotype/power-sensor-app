from core.constants import READ_TERM, WRITE_TERM
from drivers.visa_meter import VisaMeter
from drivers.fake_meter import FakeMeter

class Meter:
    """Класс с тем же внешним интерфейсом, что и VisaMeter,
       но автоматически включает симулятор при ресурсе 'FAKE'."""
    def __init__(self, backend_order):
        self.backend_order = backend_order
        self.backend_in_use = None
        self._impl = None   # экземпляр VisaMeter или FakeMeter
        self.resource = None

    def is_connected_to(self, resource: str) -> bool:
        return self._impl is not None and self.resource == resource

    def connect(self, resource: str, timeout_ms: int = 5000):
        res_upper = (resource or "").strip().upper()
        if self.is_connected_to(resource):
            return
        self.close()
        if res_upper == "FAKE":
            self._impl = FakeMeter()
            self._impl.connect("FAKE", 0)
            self.backend_in_use = "FAKE"
            self.resource = "FAKE"
            return
        # реальный прибор через VISA
        impl = VisaMeter(self.backend_order, read_term=READ_TERM, write_term=WRITE_TERM)
        impl.connect(resource, timeout_ms=timeout_ms)
        self._impl = impl
        self.backend_in_use = impl.backend_in_use
        self.resource = resource

    def close(self):
        try:
            if self._impl:
                self._impl.close()
        finally:
            self._impl = None
            self.backend_in_use = None
            self.resource = None

    # прокси-методы
    def query(self, cmd: str) -> str:
        if not self._impl:
            raise RuntimeError("Нет соединения")
        return self._impl.query(cmd)

    def write(self, cmd: str):
        if not self._impl:
            raise RuntimeError("Нет соединения")
        return self._impl.write(cmd)

    def idn(self) -> str:
        if not self._impl:
            return "Фейкометр 1.0"
        return self._impl.idn()
