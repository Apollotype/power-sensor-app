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
import tkinter.font as tkfont
import threading, re



try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except Exception as e:
    print("tkinter недоступен:", e, file=sys.stderr)
    raise

# ===== Настройки =====
DEFAULT_BACKENDS = ["@py", ""]   # сперва pyvisa-py (без NI), затем системный VISA (если вдруг есть)
POLL_PERIOD_S = 5
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
_UNIT = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}

def parse_float(text: str):
    """Достаёт число из строки + применяет суффиксы Hz/kHz/MHz/GHz.
    НИЧЕГО не делает с dBm — просто вернёт число как есть."""
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
    def __init__(self, backend_order):
        if not HAS_VISA:
            raise RuntimeError("PyVISA не установлен")
        self.backend_order = backend_order
        self.rm = None
        self.inst = None
        self.resource = None
        self.backend_in_use = None
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

    def is_connected_to(self, resource: str) -> bool:
        return self.inst is not None and self.resource == resource

    def connect(self, resource: str, timeout_ms: int = 5000):
        """Идемпотентный connect: повтор на тот же ресурс — просто ОК."""
        with self._lock:
            if self.is_connected_to(resource):
                return
            # закрыть предыдущее соединение, если было
            self._close_unlocked()

            # выбрать первый доступный бэкенд
            last_err = None
            for be in self.backend_order:
                try:
                    self.rm = pyvisa.ResourceManager(be) if be else pyvisa.ResourceManager()
                    self.inst = self.rm.open_resource(resource)
                    self.inst.timeout = timeout_ms
                    self.inst.read_termination = "\n"
                    self.inst.write_termination = "\n"
                    try:
                        self.inst.clear()   # очистим буферы на всякий
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


# ===== GUI =====
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Power Meter")

        # --- полноэкранный режим по умолчанию ---
        self.attributes("-fullscreen", True)      # F11 — включ/выключ, Esc — выйти
        self.bind("<F11>", self._toggle_fullscreen)
        self.bind("<Escape>", self._exit_fullscreen)

        # --- базовая «дизайн»-размерность для расчёта масштаба ---
        self.BASE_W, self.BASE_H = 1280, 720
        self._font_objs = {}          # сюда положим шрифты
        self._resize_job = None       # дебаунс на ресайз

        # логика
        self.meter = VisaMeter(DEFAULT_BACKENDS)
        self.read_queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.poll_thread = None
        self.peak_value = None

        # UI
        self._build_ui()

        # первичная установка масштаба и реакция на ресайз
        self._apply_scale()
        self.bind("<Configure>", self._on_configure)

        # автоскан/автоподключение
        self.after(200, self._auto_scan_and_connect)

    # ---------- масштабирование ----------
    def _calc_scale(self):
        w = self.winfo_screenwidth()
        h = self.winfo_screenheight()
        return min(w / self.BASE_W, h / self.BASE_H)

    def _apply_scale(self):
        s = self._calc_scale()

        # создаём/обновляем шрифты
        def mk(name, **kw):
            f = self._font_objs.get(name)
            if f is None:
                f = tkfont.Font(**kw)
                self._font_objs[name] = f
            else:
                for k, v in kw.items():
                    f[k] = v
            return f

        font_norm  = mk("norm",  family="Segoe UI", size=max(11, int(12 * s)))
        font_val   = mk("value", family="Segoe UI", size=max(16, int(22 * s)), weight="bold")
        font_btn   = mk("btn",   family="Segoe UI", size=max(11, int(12 * s)))
        font_title = mk("title", family="Segoe UI", size=max(12, int(13 * s)))

        # применяем к стилям ttk
        style = ttk.Style(self)
        style.configure("TLabel",   font=font_norm)
        style.configure("TLabelframe.Label", font=font_title)
        style.configure("TButton",  font=font_btn)
        style.configure("Value.TLabel", font=font_val)

        # поля ввода пусть тоже крупнее
        self.res_entry.configure(font=font_norm)
        self.cmb_found.configure(font=font_norm)
        self.ent_freq.configure(font=font_norm)

    def _on_configure(self, _evt):
        # дебаунс, чтобы не пересчитывать шрифты на каждое движение
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(150, self._apply_scale)

    def _toggle_fullscreen(self, _=None):
        self.attributes("-fullscreen", not self.attributes("-fullscreen"))

    def _exit_fullscreen(self, _=None):
        self.attributes("-fullscreen", False)

    # ---------- построение UI ----------
    def _build_ui(self):
        pad = {"padx": 12, "pady": 8}

        # Connection
        frm_conn = ttk.LabelFrame(self, text="Connection")
        frm_conn.pack(fill="x", expand=False, **pad)

        ttk.Label(frm_conn, text="Resource:").grid(row=0, column=0, sticky="w")
        self.res_entry = ttk.Entry(frm_conn)
        self.res_entry.grid(row=0, column=1, sticky="we", padx=6)
        self.btn_connect = ttk.Button(frm_conn, text="Connect", command=self.on_connect)
        self.btn_connect.grid(row=0, column=2, sticky="e")

        ttk.Label(frm_conn, text="Found:").grid(row=1, column=0, sticky="w")
        self.cmb_found = ttk.Combobox(frm_conn, state="readonly", values=[])
        self.cmb_found.grid(row=1, column=1, sticky="we", padx=6)
        self.cmb_found.bind("<<ComboboxSelected>>", self.on_pick_found)
        self.btn_scan = ttk.Button(frm_conn, text="Scan USB", command=self.on_scan)
        self.btn_scan.grid(row=1, column=2, sticky="e")

        ttk.Label(frm_conn, text="Backend:").grid(row=2, column=0, sticky="w")
        self.backend_label = ttk.Label(frm_conn, text="—")
        self.backend_label.grid(row=2, column=1, sticky="w")

        # растягивание колонок в блоке Connection
        frm_conn.columnconfigure(1, weight=1)

        # Readings
        frm_vals = ttk.LabelFrame(self, text="Readings")
        frm_vals.pack(fill="x", expand=False, **pad)

        ttk.Label(frm_vals, text="Current Power:").grid(row=0, column=0, sticky="w")
        self.lbl_curr = ttk.Label(frm_vals, text="—", style="Value.TLabel")
        self.lbl_curr.grid(row=0, column=1, sticky="w")

        ttk.Label(frm_vals, text="Peak Power:").grid(row=1, column=0, sticky="w")
        self.lbl_peak = ttk.Label(frm_vals, text="—", style="Value.TLabel")
        self.lbl_peak.grid(row=1, column=1, sticky="w")

        # Controls
        frm_ctrl = ttk.LabelFrame(self, text="Controls")
        frm_ctrl.pack(fill="x", expand=False, **pad)

        ttk.Label(frm_ctrl, text="Freq (Hz):").grid(row=0, column=0, sticky="w")
        self.ent_freq = ttk.Entry(frm_ctrl, width=24)
        self.ent_freq.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Button(frm_ctrl, text="Get", command=self.on_get_freq).grid(row=0, column=2, padx=4)
        ttk.Button(frm_ctrl, text="Set", command=self.on_set_freq).grid(row=0, column=3, padx=4)
        ttk.Button(frm_ctrl, text="Zero", command=self.on_zero).grid(row=0, column=4, padx=12)

        # Status
        self.status = ttk.Label(self, text="Ready", relief="sunken", anchor="w")
        self.status.pack(fill="x", padx=12, pady=(0, 12))

    # ---------- остальной код (скан/коннект/опрос) ----------
    # НИЖЕ — без изменений относительно твоей текущей версии
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

    def on_connect(self):
        res = self.res_entry.get().strip()
        if not res:
            messagebox.showwarning("Power Meter", "Введите USB-адрес ресурса (USB0::...::INSTR).")
            return

    # если мы уже на этом ресурсе — просто ничего не делаем
        if self.meter.is_connected_to(res):
            self.status.configure(text=f"Already connected: {res}")
            return

    # на другой ресурс — аккуратно перезапустим опрос
        self._stop_polling()
        try:
            self.meter.connect(res)
            self.backend_label.configure(text=self.meter.backend_in_use or "default")
            idn = self.meter.idn().strip()
            self.status.configure(text=f"Connected: {res}{(' | ' + idn) if idn else ''}")
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
            messagebox.showwarning("Set Freq", "Введите частоту, можно с суффиксом (Hz/kHz/MHz/GHz).")
            return
        try:
            hz = parse_float(val)
            if hz is None:
                raise ValueError(f"Не удалось распознать частоту: {val}")
            self.meter.write(SCPI_SET_FREQ.format(freq=int(hz)))
            self.status.configure(text=f"Freq set: {val} → {int(hz)} Hz")
        except Exception as e:
            self.status.configure(text=f"Freq set error: {e}")
            messagebox.showerror("Freq set error", str(e))

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
