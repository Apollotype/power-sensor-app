#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PowerMeter GUI (PyVISA, tkinter) с автообнаружением USBTMC:
- Scan: показывает адреса всех USB?*::INSTR (как в NI MAX).
- Поле выбора адреса (Combobox) + Connect.
- Текущая и пиковая мощность, Zero, Get/Set частоты.
- Работает через системный VISA и/или pyvisa-py (@py) — оба сканируются.
"""

import threading, queue, time, re, sys
from typing import Optional, List

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except Exception as e:
    print("tkinter недоступен:", e, file=sys.stderr)
    raise

# ===== Настройки =====
DEFAULT_BACKENDS = ["@py", ""]   # сперва pyvisa-py (без NI), затем системный VISA (если вдруг есть)
POLL_PERIOD_S = 0.3
READ_TERM = "\n"
WRITE_TERM = "\n"

# SCPI-заглушки — подставишь свои при необходимости
SCPI_QUERY_POWER = "MEAS:POW?"
SCPI_ZERO        = "SENS:POW:ZERO:IMM"
SCPI_QUERY_FREQ  = "SENS:FREQ?"
SCPI_SET_FREQ    = "SENS:FREQ {freq}"

# Если хочешь сразу подставлять свой адрес — впиши сюда (иначе оставить пустым "")
PREFERRED_USB = ""   # пример: "USB0::0x3399::0x3800::QWNJ013507::INSTR"

try:
    import pyvisa
    HAS_VISA = True
except Exception as e:
    HAS_VISA = False
    pyvisa = None
    print("[!] PyVISA не установлен:", e, file=sys.stderr)


# ===== Вспомогательные =====
def parse_float(text: str) -> Optional[float]:
    if text is None:
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text.strip())
    return float(m.group(0)) if m else None

def scan_usb_usbtmc(backends: List[str]) -> List[str]:
    """Сканируем ТОЛЬКО USBTMC (USB?*::INSTR) по нескольким бэкендам и объединяем без дублей."""
    addrs: List[str] = []
    seen = set()
    if not HAS_VISA:
        return addrs
    for be in backends:
        try:
            rm = pyvisa.ResourceManager(be) if be else pyvisa.ResourceManager()
            res = rm.list_resources("USB?*::INSTR")
            print(f"[scan] backend {be or 'default'} -> {res or 'нет'}")
            for r in res:
                if r not in seen:
                    seen.add(r)
                    addrs.append(r)
        except Exception as e:
            print(f"[scan] backend {be or 'default'} ошибка: {e}")
    return addrs


# ===== Класс прибора =====
class VisaMeter:
    def __init__(self, backend_order: List[str]):
        if not HAS_VISA:
            raise RuntimeError("pyvisa не установлен")
        self.backend_order = backend_order
        self.rm = None
        self.inst = None
        self.backend_in_use = None
        self.resource = None

    def connect(self, resource: str) -> None:
        last_err = None
        for be in self.backend_order:
            try:
                rm = pyvisa.ResourceManager(be) if be else pyvisa.ResourceManager()  # важно: без None
                inst = rm.open_resource(resource)
                inst.read_termination = READ_TERM
                inst.write_termination = WRITE_TERM
                inst.timeout = 5000
                self.rm = rm
                self.inst = inst
                self.backend_in_use = be
                self.resource = resource
                return
            except Exception as e:
                last_err = e
        raise last_err if last_err else RuntimeError("Не удалось подключиться")

    def close(self) -> None:
        try:
            if self.inst:
                self.inst.close()
        finally:
            self.inst = None
            self.rm = None
            self.resource = None

    def query(self, cmd: str) -> str:
        if not self.inst:
            raise RuntimeError("Не подключено")
        return self.inst.query(cmd)

    def write(self, cmd: str) -> None:
        if not self.inst:
            raise RuntimeError("Не подключено")
        self.inst.write(cmd)

    def idn(self) -> str:
        try:
            return self.query("*IDN?")
        except Exception:
            return ""


# ===== GUI =====
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Power Meter")
        self.geometry("520x290")
        self.resizable(False, False)

        self.meter = VisaMeter(DEFAULT_BACKENDS)
        self.read_queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.poll_thread = None
        self.peak_value = None

        self._build_ui()
        self.after(200, self._auto_scan_and_connect)

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        # Connection
        frm_conn = ttk.LabelFrame(self, text="Connection")
        frm_conn.pack(fill="x", **pad)

        ttk.Label(frm_conn, text="Resource:").grid(row=0, column=0, sticky="w")
        self.res_entry = ttk.Entry(frm_conn, width=48)
        self.res_entry.grid(row=0, column=1, sticky="we", padx=5)
        self.btn_connect = ttk.Button(frm_conn, text="Connect", command=self.on_connect)
        self.btn_connect.grid(row=0, column=2, sticky="e")

        ttk.Label(frm_conn, text="Found:").grid(row=1, column=0, sticky="w")
        self.cmb_found = ttk.Combobox(frm_conn, width=46, state="readonly", values=[])
        self.cmb_found.grid(row=1, column=1, sticky="we", padx=5)
        self.cmb_found.bind("<<ComboboxSelected>>", self.on_pick_found)
        self.btn_scan = ttk.Button(frm_conn, text="Scan USB", command=self.on_scan)
        self.btn_scan.grid(row=1, column=2, sticky="e")

        ttk.Label(frm_conn, text="Backend:").grid(row=2, column=0, sticky="w")
        self.backend_label = ttk.Label(frm_conn, text="—")
        self.backend_label.grid(row=2, column=1, sticky="w")

        # Readings
        frm_vals = ttk.LabelFrame(self, text="Readings")
        frm_vals.pack(fill="x", **pad)

        ttk.Label(frm_vals, text="Current Power:").grid(row=0, column=0, sticky="w")
        self.lbl_curr = ttk.Label(frm_vals, text="—", font=("Segoe UI", 14, "bold"))
        self.lbl_curr.grid(row=0, column=1, sticky="w")

        ttk.Label(frm_vals, text="Peak Power:").grid(row=1, column=0, sticky="w")
        self.lbl_peak = ttk.Label(frm_vals, text="—", font=("Segoe UI", 14))
        self.lbl_peak.grid(row=1, column=1, sticky="w")

        # Controls
        frm_ctrl = ttk.LabelFrame(self, text="Controls")
        frm_ctrl.pack(fill="x", **pad)

        ttk.Label(frm_ctrl, text="Freq (Hz):").grid(row=0, column=0, sticky="w")
        self.ent_freq = ttk.Entry(frm_ctrl, width=20)
        self.ent_freq.grid(row=0, column=1, sticky="w", padx=5)
        ttk.Button(frm_ctrl, text="Get", command=self.on_get_freq).grid(row=0, column=2, padx=4)
        ttk.Button(frm_ctrl, text="Set", command=self.on_set_freq).grid(row=0, column=3, padx=4)
        ttk.Button(frm_ctrl, text="Zero", command=self.on_zero).grid(row=0, column=4, padx=12)

        # Status
        self.status = ttk.Label(self, text="Ready", relief="sunken", anchor="w")
        self.status.pack(fill="x", padx=8, pady=(0, 8))

    # ---- Scan & Auto ----
    def on_scan(self):
        addrs = scan_usb_usbtmc(DEFAULT_BACKENDS)
        self.cmb_found["values"] = addrs
        if addrs:
            self.cmb_found.current(0)
            self.res_entry.delete(0, tk.END)
            self.res_entry.insert(0, addrs[0])
            self.status.configure(text=f"Found {len(addrs)} USBTMC device(s).")
        else:
            self.status.configure(text="USBTMC не найдены.")

    def on_pick_found(self, _evt=None):
        v = self.cmb_found.get().strip()
        if v:
            self.res_entry.delete(0, tk.END)
            self.res_entry.insert(0, v)

    def _auto_scan_and_connect(self):
        if PREFERRED_USB.strip():
            self.res_entry.delete(0, tk.END)
            self.res_entry.insert(0, PREFERRED_USB.strip())
            self.on_connect()
            return
        self.on_scan()
        if self.cmb_found["values"]:
            self.on_connect()
        else:
            self.status.configure(text="USBTMC не найдены. Нажми Scan или введи адрес вручную.")

    # ---- Connect / Controls ----
    def on_connect(self):
        res = self.res_entry.get().strip()
        if not res:
            messagebox.showwarning("Power Meter", "Введите строку ресурса (USB0::...::INSTR).")
            return
        self._stop_polling()
        try:
            self.meter.connect(res)
            self.backend_label.configure(text=self.meter.backend_in_use or "default")
            idn = self.meter.idn()
            self.status.configure(text=f"Connected: {res}{(' | IDN: ' + idn.strip()) if idn else ''}")
            self.peak_value = None
            self.lbl_peak.configure(text="—")
            self._start_polling()
        except Exception as e:
            self.status.configure(text=f"Connect error: {e}")
            messagebox.showerror("Connect error", str(e))

    def on_zero(self):
        try:
            self.meter.write(SCPI_ZERO)
            self.status.configure(text="Zero sent")
            self.peak_value = None
            self.lbl_peak.configure(text="—")
        except Exception as e:
            self.status.configure(text=f"Zero error: {e}")
            messagebox.showerror("Zero error", str(e))

    def on_get_freq(self):
        try:
            resp = self.meter.query(SCPI_QUERY_FREQ).strip()
            self.ent_freq.delete(0, tk.END)
            self.ent_freq.insert(0, resp)
            self.status.configure(text=f"Freq: {resp}")
        except Exception as e:
            self.status.configure(text=f"Freq get error: {e}")
            messagebox.showerror("Freq get error", str(e))

    def on_set_freq(self):
        val = self.ent_freq.get().strip()
        if not val:
            messagebox.showwarning("Set Freq", "Введите частоту (Гц).")
            return
        try:
            self.meter.write(SCPI_SET_FREQ.format(freq=val))
            self.status.configure(text=f"Freq set: {val}")
        except Exception as e:
            self.status.configure(text=f"Freq set error: {e}")
            messagebox.showerror("Freq set error", str(e))

    # ---- Polling ----
    def _start_polling(self):
        self.stop_flag.clear()
        self.poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.poll_thread.start()
        self.after(100, self._drain_queue)

    def _stop_polling(self):
        self.stop_flag.set()
        if self.poll_thread and self.poll_thread.is_alive():
            self.poll_thread.join(timeout=1.0)
        self.poll_thread = None

    def _poll_loop(self):
        while not self.stop_flag.is_set():
            try:
                raw = self.meter.query(SCPI_QUERY_POWER)
                val = parse_float(raw)
                self.read_queue.put(("ok", val, raw.strip()))
            except Exception as e:
                self.read_queue.put(("err", None, str(e)))
            time.sleep(POLL_PERIOD_S)

    def _drain_queue(self):
        try:
            while True:
                kind, val, payload = self.read_queue.get_nowait()
                if kind == "ok":
                    if val is not None:
                        self.lbl_curr.configure(text=f"{val}")
                        if self.peak_value is None or val > self.peak_value:
                            self.peak_value = val
                            self.lbl_peak.configure(text=f"{self.peak_value}")
                    else:
                        self.lbl_curr.configure(text=payload)
                else:
                    self.status.configure(text=f"Read error: {payload}")
        except queue.Empty:
            pass
        if not self.stop_flag.is_set():
            self.after(100, self._drain_queue)

    def destroy(self):
        self._stop_polling()
        try:
            self.meter.close()
        except Exception:
            pass
        super().destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
