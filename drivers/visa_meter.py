import threading

try:
    import pyvisa
    HAS_VISA = True
except Exception:
    pyvisa = None
    HAS_VISA = False

class VisaMeter:
    """Реализация «как в исходнике»: query/write/idn/connect/is_connected_to/close."""
    def __init__(self, backend_order, read_term="\n", write_term="\n"):
        if not HAS_VISA:
            raise RuntimeError("PyVISA не установлен")
        self.backend_order = backend_order
        self.rm = None
        self.inst = None
        self.resource = None
        self.backend_in_use = None
        self.read_term = read_term
        self.write_term = write_term
        self._lock = threading.RLock()

    def _close_unlocked(self):
        if self.inst:
            try:
                self.inst.close()
            except Exception:
                pass
        self.inst = None
        self.rm = None
        self.resource = None
        self.backend_in_use = None

    def is_connected_to(self, resource: str) -> bool:
        return self.inst is not None and self.resource == resource

    def connect(self, resource: str, timeout_ms: int = 5000):
        """Идемпотентный connect: повтор на тот же ресурс — просто ОК."""
        with self._lock:
            if self.is_connected_to(resource):
                return
            self._close_unlocked()

            last_err = None
            for be in self.backend_order:
                try:
                    self.rm = pyvisa.ResourceManager(be) if be else pyvisa.ResourceManager()
                    self.inst = self.rm.open_resource(resource)
                    self.inst.timeout = timeout_ms
                    self.inst.read_termination = self.read_term
                    self.inst.write_termination = self.write_term
                    try:
                        self.inst.clear()
                    except Exception:
                        pass
                    self.backend_in_use = be or "default"
                    self.resource = resource
                    return
                except Exception as e:
                    last_err = e
                    self.rm = None
                    self.inst = None
            raise last_err or RuntimeError("Не удалось открыть ресурс")

    def close(self):
        with self._lock:
            self._close_unlocked()

    def query(self, cmd: str) -> str:
        with self._lock:
            if not self.inst:
                raise RuntimeError("Нет соединения")
            return self.inst.query(cmd)

    def write(self, cmd: str):
        with self._lock:
            if not self.inst:
                raise RuntimeError("Нет соединения")
            return self.inst.write(cmd)

    def idn(self) -> str:
        try:
            return self.query("*IDN?")
        except Exception:
            return ""
