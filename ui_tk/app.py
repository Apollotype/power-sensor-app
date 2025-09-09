import threading, queue, time
import tkinter as tk
from tkinter import ttk, messagebox
import tkinter.font as tkfont

from core.constants import (
    DEFAULT_BACKENDS, PREFERRED_USB, POLL_PERIOD_S,
    SCPI_QUERY_POWER, SCPI_ZERO, SCPI_QUERY_FREQ, SCPI_SET_FREQ,
)
from core.utils import parse_float

# --- optional import of parse_idn from core.utils if present
try:
    from core.utils import parse_idn as _parse_idn_external
except Exception:
    _parse_idn_external = None

from drivers.discovery import scan_usb_usbtmc
from core.meter import Meter


def _parse_idn_local(idn: str):
    """Fallback IDN parser: 'Vendor,Model,Serial,Firmware' -> dict."""
    if not idn:
        return {"vendor": "", "model": "", "serial": "", "firmware": ""}
    parts = [p.strip() for p in str(idn).split(",")]
    while len(parts) < 4:
        parts.append("")
    vendor, model, serial, firmware = parts[:4]
    return {
        "vendor": vendor,
        "model": model,
        "serial": serial,
        "firmware": firmware,
    }


def parse_idn(idn: str):
    """Use project-level parser if available, otherwise fallback."""
    if _parse_idn_external is not None:
        try:
            return _parse_idn_external(idn)
        except Exception:
            pass
    return _parse_idn_local(idn)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Power Meter")

        # --- полноэкранный режим, как в исходнике ---
        self.attributes("-fullscreen", True)
        self.bind("<F11>", self._toggle_fullscreen)
        self.bind("<Escape>", self._exit_fullscreen)

        self.BASE_W, self.BASE_H = 1280, 720
        self._font_objs = {}
        self._resize_job = None

        # логика/метр (теперь через гибридный фасад)
        self.meter = Meter(DEFAULT_BACKENDS)
        self.read_queue = queue.Queue()
        self.stop_flag = threading.Event()
        self.poll_thread = None
        self.peak_value = None

        # IDN state
        self.idn_vendor = tk.StringVar(value="")
        self.idn_model = tk.StringVar(value="")
        self.idn_serial = tk.StringVar(value="")
        self.idn_firmware = tk.StringVar(value="")

        # UI (без изменений внешнего вида + добавлен блок "Прибор")
        self._build_ui()

        self._apply_scale()
        self.bind("<Configure>", self._on_configure)

        # автоскан/автоподключение
        self.after(200, self._auto_scan_and_connect)

    # ---------- масштабирование ----------
    def _calc_scale(self):
        w = self.winfo_screenwidth()
        h = self.winfo_screenheight()
        return min(w / self.BASE_W, h / self.BASE_H)

    def set_controller(self, controller):
        self.controller = controller

    def _apply_scale(self):
        s = self._calc_scale()

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

        style = ttk.Style(self)
        style.configure("TLabel",   font=font_norm)
        style.configure("TLabelframe.Label", font=font_title)
        style.configure("TButton",  font=font_btn)
        style.configure("Value.TLabel", font=font_val)

        self.res_entry.configure(font=font_norm)
        self.cmb_found.configure(font=font_norm)
        self.ent_freq.configure(font=font_norm)
        # device info labels inherit style, no direct font set required

    def _on_configure(self, _evt):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(150, self._apply_scale)

    def _toggle_fullscreen(self, _=None):
        self.attributes("-fullscreen", not self.attributes("-fullscreen"))

    def _exit_fullscreen(self, _=None):
        self.attributes("-fullscreen", False)

    # ---------- UI ----------
    def _build_ui(self):
        pad = {"padx": 12, "pady": 8}

        # --- Connection
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
        frm_conn.columnconfigure(1, weight=1)

        # --- Device Info (IDN)
        frm_idn = ttk.LabelFrame(self, text="Прибор")
        frm_idn.pack(fill="x", expand=False, **pad)

        ttk.Label(frm_idn, text="Вендор:").grid(row=0, column=0, sticky="w")
        ttk.Label(frm_idn, textvariable=self.idn_vendor).grid(row=0, column=1, sticky="w", padx=6)

        ttk.Label(frm_idn, text="Модель:").grid(row=1, column=0, sticky="w")
        ttk.Label(frm_idn, textvariable=self.idn_model).grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(frm_idn, text="Серийный №:").grid(row=2, column=0, sticky="w")
        ttk.Label(frm_idn, textvariable=self.idn_serial).grid(row=2, column=1, sticky="w", padx=6)

        ttk.Label(frm_idn, text="Прошивка:").grid(row=3, column=0, sticky="w")
        ttk.Label(frm_idn, textvariable=self.idn_firmware).grid(row=3, column=1, sticky="w", padx=6)

        frm_idn.columnconfigure(1, weight=1)

        # --- Readings
        frm_vals = ttk.LabelFrame(self, text="Readings")
        frm_vals.pack(fill="x", expand=False, **pad)

        ttk.Label(frm_vals, text="Current Power:").grid(row=0, column=0, sticky="w")
        self.lbl_curr = ttk.Label(frm_vals, text="—", style="Value.TLabel")
        self.lbl_curr.grid(row=0, column=1, sticky="w")

        ttk.Label(frm_vals, text="Peak Power:").grid(row=1, column=0, sticky="w")
        self.lbl_peak = ttk.Label(frm_vals, text="—", style="Value.TLabel")
        self.lbl_peak.grid(row=1, column=1, sticky="w")

        # --- Controls
        frm_ctrl = ttk.LabelFrame(self, text="Controls")
        frm_ctrl.pack(fill="x", expand=False, **pad)

        ttk.Label(frm_ctrl, text="Freq (Hz):").grid(row=0, column=0, sticky="w")
        self.ent_freq = ttk.Entry(frm_ctrl, width=24)
        self.ent_freq.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Button(frm_ctrl, text="Get", command=self.on_get_freq).grid(row=0, column=2, padx=4)
        ttk.Button(frm_ctrl, text="Set", command=self.on_set_freq).grid(row=0, column=3, padx=4)
        ttk.Button(frm_ctrl, text="Zero", command=self.on_zero).grid(row=0, column=4, padx=12)

        # --- Status bar
        self.status = ttk.Label(self, text="Ready", relief="sunken", anchor="w")
        self.status.pack(fill="x", padx=12, pady=(0, 12))

    # ---------- helpers for IDN ----------
    def _clear_idn_fields(self):
        for var in (self.idn_vendor, self.idn_model, self.idn_serial, self.idn_firmware):
            var.set("")

    def _update_idn_fields(self, idn_str: str):
        info = parse_idn(idn_str)
        self.idn_vendor.set(info.get("vendor", ""))
        self.idn_model.set(info.get("model", ""))
        self.idn_serial.set(info.get("serial", ""))
        self.idn_firmware.set(info.get("firmware", ""))

    # ---------- логика кнопок (без визуальных изменений) ----------
    def on_scan(self):
        addrs = scan_usb_usbtmc(DEFAULT_BACKENDS)
        # Добавим «FAKE» в конец списка как опцию симулятора
        if "FAKE" not in addrs:
            addrs = list(addrs) + ["FAKE"]
        self.cmb_found["values"] = addrs
        if addrs:
            self.cmb_found.current(0)
            self.res_entry.delete(0, tk.END)
            self.res_entry.insert(0, addrs[0])
            self.status.configure(text=f"Found {len(addrs)} device(s). ('FAKE' = simulator)")
        else:
            self.status.configure(text="Devices not found.")

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
            self.status.configure(text="No devices. Press Scan or enter address manually.")

    def on_connect(self):
        res = self.res_entry.get().strip()
        if not res:
            messagebox.showwarning("Power Meter", "Введите адрес ресурса (USB0::...::INSTR или FAKE)." )
            return
        if self.meter.is_connected_to(res):
            self.status.configure(text=f"Already connected: {res}")
            return
        self._stop_polling()
        try:
            self.meter.connect(res)
            self.backend_label.configure(text=self.meter.backend_in_use or "default")
            idn = self.meter.idn().strip()
            self._update_idn_fields(idn)
            self.status.configure(text=f"Connected: {res}{(' | ' + idn) if idn else ''}")
            self.peak_value = None
            self.lbl_peak.configure(text="—")
            self._start_polling()
        except Exception as e:
            self._clear_idn_fields()
            self.status.configure(text=f"Connect error: {e}")
            messagebox.showerror("Connect error", str(e))

    def on_zero(self):
        try:
            from core.constants import SCPI_ZERO
            self.meter.write(SCPI_ZERO)
            self.status.configure(text="Zero sent")
            self.peak_value = None
            self.lbl_peak.configure(text="—")
        except Exception as e:
            self.status.configure(text=f"Zero error: {e}")
            messagebox.showerror("Zero error", str(e))

    def on_get_freq(self):
        try:
            from core.constants import SCPI_QUERY_FREQ
            resp = self.meter.query(SCPI_QUERY_FREQ).strip()
            self.ent_freq.delete(0, tk.END)
            self.ent_freq.insert(0, resp)
            self.status.configure(text=f"Freq: {resp}")
        except Exception as e:
            self.status.configure(text=f"Freq get error: {e}")
            messagebox.showerror("Freq get error", str(e))

    def on_set_freq(self):
        from core.constants import SCPI_SET_FREQ
        val = self.ent_freq.get().strip()
        if not val:
            messagebox.showwarning("Set Freq", "Введите частоту, можно с суффиксом (Hz/kHz/MHz/GHz)." )
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

    # ---------- поток опроса ----------
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
                if val is not None:
                    if self.peak_value is None or val > self.peak_value:
                        self.peak_value = val
                    self.read_queue.put(("ok", val, self.peak_value))
                else:
                    self.read_queue.put(("err", f"Parse error: {raw}"))
            except Exception as e:
                self.read_queue.put(("err", str(e)))
            finally:
                time.sleep(POLL_PERIOD_S)

    def _drain_queue(self):
        try:
            while True:
                tag, *payload = self.read_queue.get_nowait()
                if tag == "ok":
                    curr, peak = payload
                    self.lbl_curr.configure(text=f"{curr:.3f} dBm")
                    if peak is not None:
                        self.lbl_peak.configure(text=f"{peak:.3f} dBm")
                    self.status.configure(text="OK")
                else:
                    err_msg = payload[0] if payload else "Error"
                    self.status.configure(text=f"Read error: {err_msg}")
        except queue.Empty:
            pass

        if not self.stop_flag.is_set():
            self.after(100, self._drain_queue)

    def on_close(self):
        try:
            self._stop_polling()
        except Exception:
            pass
        try:
            self.meter.close()
        except Exception:
            pass
        self.destroy()
