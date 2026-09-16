import tkinter as tk
from tkinter import ttk, messagebox
from pymodbus.client import ModbusTcpClient
from datetime import datetime
import threading
from collections import deque


# ============================================================
# CONFIG
# ============================================================

PLC_IP = "127.0.0.1"
PLC_PORT = 5020
UNIT_ID = 1

POLL_INTERVAL_MS = 500

MAX_RPM = 3000
MAX_PRESSURE = 10.0
MAX_TEMPERATURE = 100.0
MAX_CURRENT = 30.0

TREND_POINTS = 120


# ============================================================
# REGISTER MAP
# ============================================================

REG = {
    "MOTOR_COMMAND": 0,
    "MODE": 1,
    "SPEED_SETPOINT": 2,
    "ACTUAL_RPM": 4,
    "CURRENT": 6,
    "PRESSURE_SETPOINT": 8,
    "ACTUAL_PRESSURE": 10,
    "TEMPERATURE": 12,
    "ALARM": 14,
    "EMERGENCY_STOP": 15,
    "HEARTBEAT": 16,
    "STATUS_WORD": 17,
    "FAULT_CODE": 18,
    "RUNTIME": 19,
}


# ============================================================
# FAULTS
# ============================================================

FAULT_CODES = {
    0: "NONE",
    1: "EMERGENCY STOP",
    2: "OVER TEMPERATURE",
    3: "OVER SPEED",
    4: "OVER CURRENT",
    5: "OVER PRESSURE",
    6: "WATCHDOG TIMEOUT",
    7: "INVALID SETPOINT",
}


# ============================================================
# STATUS BITS
# ============================================================

STATUS_BITS = [
    (0, "STOPPED"),
    (1, "RUNNING"),
    (2, "AUTO MODE"),
    (3, "MANUAL MODE"),
    (4, "ALARM ACTIVE"),
    (5, "EMERGENCY STOP"),
    (6, "FAULT ACTIVE"),
    (7, "READY"),
]


# ============================================================
# COLORS
# ============================================================

BG = "#0b1120"
PANEL = "#111827"
PANEL_2 = "#172033"
BORDER = "#263247"

TEXT = "#f1f5f9"
MUTED = "#94a3b8"

GREEN = "#22c55e"
GREEN_DARK = "#14532d"

BLUE = "#38bdf8"
BLUE_DARK = "#0c4a6e"

AMBER = "#f59e0b"
AMBER_DARK = "#78350f"

RED = "#ef4444"
RED_DARK = "#7f1d1d"


# ============================================================
# MODBUS MANAGER
# ============================================================

class ModbusManager:

    def __init__(self):
        self.client = None
        self.lock = threading.Lock()
        self.connected = False

    def connect(self):

        with self.lock:

            try:

                if self.client is not None:

                    try:
                        self.client.close()
                    except Exception:
                        pass

                self.client = ModbusTcpClient(
                    host=PLC_IP,
                    port=PLC_PORT,
                    timeout=2
                )

                print(
                    f"[MODBUS] Connecting to "
                    f"{PLC_IP}:{PLC_PORT}"
                )

                result = self.client.connect()

                self.connected = bool(result)

                if self.connected:

                    print(
                        f"[MODBUS] Connected to "
                        f"{PLC_IP}:{PLC_PORT}"
                    )

                else:

                    print(
                        "[MODBUS] Connection FAILED"
                    )

                return self.connected

            except Exception as exc:

                print(
                    f"[MODBUS] Connection ERROR: {exc}"
                )

                self.connected = False

                return False

    def ensure_connection(self):

        if self.connected and self.client is not None:
            return True

        return self.connect()

    def disconnect(self):

        with self.lock:

            try:

                if self.client:
                    self.client.close()

            except Exception:
                pass

            self.client = None
            self.connected = False

    def read_registers(self):

        with self.lock:

            if not self.connected:
                return None

            if self.client is None:
                return None

            try:

                result = self.client.read_holding_registers(
                    address=0,
                    count=21,
                    device_id=UNIT_ID
                )

                if result.isError():

                    print(
                        f"[MODBUS] Read error: {result}"
                    )

                    self.connected = False

                    return None

                return result.registers

            except Exception as exc:

                print(
                    f"[MODBUS] Read exception: {exc}"
                )

                self.connected = False

                return None

    def write_register(
        self,
        address,
        value
    ):

        if not self.ensure_connection():
            return False

        with self.lock:

            try:

                result = self.client.write_register(
                    address=address,
                    value=int(value),
                    device_id=UNIT_ID
                )

                if result.isError():

                    print(
                        f"[MODBUS] Write error: {result}"
                    )

                    self.connected = False

                    return False

                print(
                    f"[MODBUS] WRITE "
                    f"400{address + 1:02d} = {value}"
                )

                return True

            except Exception as exc:

                print(
                    f"[MODBUS] Write exception: {exc}"
                )

                self.connected = False

                return False

    def write_uint32(
        self,
        address,
        value
    ):

        if not self.ensure_connection():
            return False

        with self.lock:

            try:

                value = int(value)

                if value < 0 or value > 0xFFFFFFFF:

                    print(
                        f"[MODBUS] Invalid UINT32 value: {value}"
                    )

                    return False

                high = (value >> 16) & 0xFFFF
                low = value & 0xFFFF

                result = self.client.write_registers(
                    address=address,
                    values=[high, low],
                    device_id=UNIT_ID
                )

                if result.isError():

                    print(
                        f"[MODBUS] Write UINT32 error "
                        f"at {address}: {result}"
                    )

                    self.connected = False

                    return False

                print(
                    f"[MODBUS] WRITE UINT32 | "
                    f"Address={address} | "
                    f"Value={value} | "
                    f"Registers=[{high}, {low}]"
                )

                return True

            except Exception as exc:

                print(
                    f"[MODBUS] Write UINT32 exception: {exc}"
                )

                self.connected = False

                return False


# ============================================================
# MAIN HMI
# ============================================================

class PLCMonitor:

    def __init__(self, root):

        self.root = root

        self.root.title(
            "Smart PLC Control Center"
        )

        self.root.geometry(
            "1450x900"
        )

        self.root.minsize(
            1200,
            760
        )

        self.root.configure(
            bg=BG
        )

        self.modbus = ModbusManager()

        self.running = True

        self.last_data = None

        # ====================================================
        # ALARM STATE
        # ====================================================

        self.previous_alarm = False
        self.previous_fault_code = 0
        self.previous_emergency_stop = False

        self.alarm_history = deque(
            maxlen=50
        )

        # ====================================================
        # TREND DATA
        # ====================================================

        self.rpm_history = deque(
            maxlen=TREND_POINTS
        )

        self.pressure_history = deque(
            maxlen=TREND_POINTS
        )

        self.temperature_history = deque(
            maxlen=TREND_POINTS
        )

        self.setup_style()

        self.build_interface()

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.close
        )

        self.start_connection()

    # ========================================================
    # STYLE
    # ========================================================

    def setup_style(self):

        style = ttk.Style()

        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(
            "TNotebook",
            background=BG,
            borderwidth=0
        )

        style.configure(
            "TNotebook.Tab",
            background=PANEL,
            foreground=MUTED,
            padding=(18, 10),
            font=("Segoe UI", 10, "bold")
        )

        style.map(
            "TNotebook.Tab",
            background=[
                ("selected", PANEL_2)
            ],
            foreground=[
                ("selected", TEXT)
            ]
        )

        style.configure(
            "Treeview",
            background=PANEL,
            foreground=TEXT,
            fieldbackground=PANEL,
            rowheight=30,
            borderwidth=0,
            font=("Consolas", 9)
        )

        style.configure(
            "Treeview.Heading",
            background=PANEL_2,
            foreground=TEXT,
            font=("Segoe UI", 9, "bold")
        )

    # ========================================================
    # BUILD UI
    # ========================================================

    def build_interface(self):

        self.build_header()

        self.build_alarm_banner()

        self.notebook = ttk.Notebook(
            self.root
        )

        self.notebook.pack(
            fill="both",
            expand=True
        )

        self.overview_tab = tk.Frame(
            self.notebook,
            bg=BG
        )

        self.trend_tab = tk.Frame(
            self.notebook,
            bg=BG
        )

        self.register_tab = tk.Frame(
            self.notebook,
            bg=BG
        )

        self.diagnostics_tab = tk.Frame(
            self.notebook,
            bg=BG
        )

        self.notebook.add(
            self.overview_tab,
            text="  OVERVIEW  "
        )

        self.notebook.add(
            self.trend_tab,
            text="  TRENDS  "
        )

        self.notebook.add(
            self.register_tab,
            text="  REGISTERS  "
        )

        self.notebook.add(
            self.diagnostics_tab,
            text="  DIAGNOSTICS  "
        )

        self.build_overview()

        self.build_trends()

        self.build_registers()

        self.build_diagnostics()

        self.build_footer()

    # ========================================================
    # HEADER
    # ========================================================

    def build_header(self):

        header = tk.Frame(
            self.root,
            bg=PANEL,
            height=72
        )

        header.pack(
            fill="x"
        )

        header.pack_propagate(False)

        left = tk.Frame(
            header,
            bg=PANEL
        )

        left.pack(
            side="left",
            padx=25
        )

        tk.Label(
            left,
            text="SMART PLC",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 21, "bold")
        ).pack(
            side="left",
            pady=16
        )

        tk.Label(
            left,
            text="  CONTROL CENTER",
            bg=PANEL,
            fg=BLUE,
            font=("Segoe UI", 11, "bold")
        ).pack(
            side="left",
            pady=20
        )

        right = tk.Frame(
            header,
            bg=PANEL
        )

        right.pack(
            side="right",
            padx=25
        )

        self.connection_dot = tk.Label(
            right,
            text="●",
            bg=PANEL,
            fg=RED,
            font=("Segoe UI", 18)
        )

        self.connection_dot.pack(
            side="left"
        )

        self.connection_label = tk.Label(
            right,
            text="DISCONNECTED",
            bg=PANEL,
            fg=RED,
            font=("Segoe UI", 10, "bold")
        )

        self.connection_label.pack(
            side="left",
            padx=8
        )

        tk.Label(
            right,
            text=f"{PLC_IP}:{PLC_PORT} | UNIT {UNIT_ID}",
            bg=PANEL,
            fg=MUTED,
            font=("Consolas", 9)
        ).pack(
            side="left"
        )

    # ========================================================
    # ALARM BANNER
    # ========================================================

    def build_alarm_banner(self):

        self.alarm_banner = tk.Frame(
            self.root,
            bg=GREEN_DARK,
            height=38
        )

        self.alarm_banner.pack(
            fill="x"
        )

        self.alarm_banner.pack_propagate(
            False
        )

        self.alarm_text = tk.Label(
            self.alarm_banner,
            text="●  SYSTEM READY — NO ACTIVE ALARMS",
            bg=GREEN_DARK,
            fg=TEXT,
            font=("Segoe UI", 10, "bold")
        )

        self.alarm_text.pack(
            side="left",
            padx=25
        )

    # ========================================================
    # OVERVIEW
    # ========================================================

    def build_overview(self):

        # ====================================================
        # SCROLLABLE OVERVIEW
        # ====================================================

        self.overview_canvas = tk.Canvas(
            self.overview_tab,
            bg=BG,
            highlightthickness=0
        )

        scrollbar = ttk.Scrollbar(
            self.overview_tab,
            orient="vertical",
            command=self.overview_canvas.yview
        )

        self.overview_canvas.configure(
            yscrollcommand=scrollbar.set
        )

        scrollbar.pack(
            side="right",
            fill="y"
        )

        self.overview_canvas.pack(
            side="left",
            fill="both",
            expand=True
        )

        container = tk.Frame(
            self.overview_canvas,
            bg=BG
        )

        canvas_window = self.overview_canvas.create_window(
            (0, 0),
            window=container,
            anchor="nw"
        )

        def update_scroll_region(event=None):

            self.overview_canvas.configure(
                scrollregion=self.overview_canvas.bbox("all")
            )

        def resize_container(event):

            self.overview_canvas.itemconfigure(
                canvas_window,
                width=event.width
            )

        container.bind(
            "<Configure>",
            update_scroll_region
        )

        self.overview_canvas.bind(
            "<Configure>",
            resize_container
        )

        # ====================================================
        # MOUSE WHEEL
        # ====================================================

        self.root.bind(
            "<MouseWheel>",
            self.on_mousewheel
        )

        self.root.bind(
            "<Button-4>",
            self.on_mousewheel_linux
        )

        self.root.bind(
            "<Button-5>",
            self.on_mousewheel_linux
        )

        # ====================================================
        # MAIN CONTENT
        # ====================================================

        main = tk.Frame(
            container,
            bg=BG
        )

        main.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=10
        )

        left = tk.Frame(
            main,
            bg=BG
        )

        left.pack(
            side="left",
            fill="both",
            expand=True
        )

        right = tk.Frame(
            main,
            bg=BG,
            width=370
        )

        right.pack(
            side="right",
            fill="y",
            padx=(5, 0)
        )

        right.pack_propagate(
            False
        )

        self.build_process_panel(left)

        self.build_motor_panel(left)

        self.build_control_panel(right)

        self.build_status_panel(right)

        self.build_alarm_history(container)

    def on_mousewheel(self, event):

        try:

            widget = self.root.winfo_containing(
                event.x_root,
                event.y_root
            )

            if widget is None:
                return

            current = widget

            while current is not None:

                if current == self.overview_tab:

                    self.overview_canvas.yview_scroll(
                        int(-1 * (event.delta / 120)),
                        "units"
                    )

                    return

                parent_name = current.winfo_parent()

                if not parent_name:
                    break

                try:
                    current = current.nametowidget(
                        parent_name
                    )
                except Exception:
                    break

        except Exception:
            pass

    def on_mousewheel_linux(self, event):

        try:

            widget = self.root.winfo_containing(
                event.x_root,
                event.y_root
            )

            if widget is None:
                return

            current = widget

            while current is not None:

                if current == self.overview_tab:

                    if event.num == 4:
                        self.overview_canvas.yview_scroll(
                            -3,
                            "units"
                        )

                    elif event.num == 5:
                        self.overview_canvas.yview_scroll(
                            3,
                            "units"
                        )

                    return

                parent_name = current.winfo_parent()

                if not parent_name:
                    break

                try:
                    current = current.nametowidget(
                        parent_name
                    )
                except Exception:
                    break

        except Exception:
            pass

    # ========================================================
    # PROCESS
    # ========================================================

    def build_process_panel(self, parent):

        panel = tk.Frame(
            parent,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1
        )

        panel.pack(
            fill="both",
            expand=True,
            padx=5,
            pady=5
        )

        tk.Label(
            panel,
            text="PROCESS OVERVIEW",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 11, "bold")
        ).pack(
            anchor="w",
            padx=18,
            pady=(15, 5)
        )

        frame = tk.Frame(
            panel,
            bg=PANEL
        )

        frame.pack(
            fill="both",
            expand=True,
            padx=10
        )

        self.rpm_value = self.metric(
            frame,
            "ACTUAL RPM",
            "--",
            "RPM"
        )

        self.current_value = self.metric(
            frame,
            "CURRENT",
            "--",
            "A"
        )

        self.pressure_value = self.metric(
            frame,
            "PRESSURE",
            "--",
            "bar"
        )

        self.temperature_value = self.metric(
            frame,
            "TEMPERATURE",
            "--",
            "°C"
        )

    def metric(
        self,
        parent,
        title,
        value,
        unit
    ):

        card = tk.Frame(
            parent,
            bg=PANEL_2,
            highlightbackground=BORDER,
            highlightthickness=1
        )

        card.pack(
            side="left",
            fill="both",
            expand=True,
            padx=5,
            pady=10
        )

        tk.Label(
            card,
            text=title,
            bg=PANEL_2,
            fg=MUTED,
            font=("Segoe UI", 9, "bold")
        ).pack(
            anchor="w",
            padx=12,
            pady=(12, 0)
        )

        value_label = tk.Label(
            card,
            text=value,
            bg=PANEL_2,
            fg=TEXT,
            font=("Segoe UI", 25, "bold")
        )

        value_label.pack(
            anchor="w",
            padx=12,
            pady=5
        )

        tk.Label(
            card,
            text=unit,
            bg=PANEL_2,
            fg=MUTED,
            font=("Segoe UI", 9)
        ).pack(
            anchor="w",
            padx=12
        )

        return value_label

    # ========================================================
    # MOTOR PANEL
    # ========================================================

    def build_motor_panel(self, parent):

        panel = tk.Frame(
            parent,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1
        )

        panel.pack(
            fill="x",
            padx=5,
            pady=5
        )

        tk.Label(
            panel,
            text="MOTOR STATE",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold")
        ).pack(
            anchor="w",
            padx=18,
            pady=(12, 0)
        )

        self.motor_state_label = tk.Label(
            panel,
            text="STOPPED",
            bg=PANEL,
            fg=GREEN,
            font=("Segoe UI", 25, "bold")
        )

        self.motor_state_label.pack(
            anchor="w",
            padx=18
        )

        self.motor_progress = ttk.Progressbar(
            panel,
            orient="horizontal",
            mode="determinate",
            maximum=MAX_RPM
        )

        self.motor_progress.pack(
            fill="x",
            padx=18,
            pady=(5, 15)
        )

    # ========================================================
    # CONTROL
    # ========================================================

    def build_control_panel(self, parent):

        panel = tk.Frame(
            parent,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1
        )

        panel.pack(
            fill="x",
            padx=5,
            pady=5
        )

        tk.Label(
            panel,
            text="CONTROL",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 11, "bold")
        ).pack(
            anchor="w",
            padx=18,
            pady=(15, 12)
        )

        buttons = tk.Frame(
            panel,
            bg=PANEL
        )

        buttons.pack(
            fill="x",
            padx=15
        )

        tk.Button(
            buttons,
            text="▶  START",
            command=lambda: self.write_register(
                REG["MOTOR_COMMAND"],
                1
            ),
            bg=GREEN_DARK,
            fg=TEXT,
            activebackground=GREEN,
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
            height=2
        ).pack(
            side="left",
            fill="x",
            expand=True,
            padx=(0, 5)
        )

        tk.Button(
            buttons,
            text="■  STOP",
            command=lambda: self.write_register(
                REG["MOTOR_COMMAND"],
                0
            ),
            bg="#334155",
            fg=TEXT,
            activebackground="#475569",
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
            height=2
        ).pack(
            side="left",
            fill="x",
            expand=True,
            padx=(5, 0)
        )

        tk.Label(
            panel,
            text="OPERATING MODE",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8, "bold")
        ).pack(
            anchor="w",
            padx=18,
            pady=(18, 5)
        )

        modes = tk.Frame(
            panel,
            bg=PANEL
        )

        modes.pack(
            fill="x",
            padx=15
        )

        tk.Button(
            modes,
            text="AUTO",
            command=lambda: self.write_register(
                REG["MODE"],
                1
            ),
            bg=BLUE_DARK,
            fg=TEXT,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            cursor="hand2"
        ).pack(
            side="left",
            fill="x",
            expand=True,
            padx=(0, 5),
            ipady=5
        )

        tk.Button(
            modes,
            text="MANUAL",
            command=lambda: self.write_register(
                REG["MODE"],
                0
            ),
            bg="#334155",
            fg=TEXT,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            cursor="hand2"
        ).pack(
            side="left",
            fill="x",
            expand=True,
            padx=(5, 0),
            ipady=5
        )

        tk.Label(
            panel,
            text="SETPOINTS",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8, "bold")
        ).pack(
            anchor="w",
            padx=18,
            pady=(18, 5)
        )

        self.rpm_entry = self.entry_row(
            panel,
            "Speed RPM"
        )

        self.pressure_entry = self.entry_row(
            panel,
            "Pressure bar"
        )

        tk.Button(
            panel,
            text="APPLY SETPOINTS",
            command=self.apply_setpoints,
            bg=BLUE_DARK,
            fg=TEXT,
            activebackground=BLUE,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            cursor="hand2"
        ).pack(
            fill="x",
            padx=15,
            pady=10,
            ipady=5
        )

        tk.Button(
            panel,
            text="⚠  EMERGENCY STOP",
            command=lambda: self.write_register(
                REG["EMERGENCY_STOP"],
                1
            ),
            bg=RED_DARK,
            fg=TEXT,
            activebackground=RED,
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            cursor="hand2"
        ).pack(
            fill="x",
            padx=15,
            pady=4,
            ipady=7
        )

        tk.Button(
            panel,
            text="RESET E-STOP",
            command=lambda: self.write_register(
                REG["EMERGENCY_STOP"],
                0
            ),
            bg="#334155",
            fg=TEXT,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            cursor="hand2"
        ).pack(
            fill="x",
            padx=15,
            pady=(0, 15),
            ipady=5
        )

    def entry_row(
        self,
        parent,
        label
    ):

        frame = tk.Frame(
            parent,
            bg=PANEL
        )

        frame.pack(
            fill="x",
            padx=15,
            pady=3
        )

        tk.Label(
            frame,
            text=label,
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 9)
        ).pack(
            side="left"
        )

        entry = tk.Entry(
            frame,
            bg=PANEL_2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            justify="center",
            font=("Consolas", 10),
            width=12
        )

        entry.pack(
            side="right",
            ipady=5
        )

        return entry

    # ========================================================
    # STATUS
    # ========================================================

    def build_status_panel(self, parent):

        panel = tk.Frame(
            parent,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1
        )

        panel.pack(
            fill="both",
            expand=True,
            padx=5,
            pady=5
        )

        tk.Label(
            panel,
            text="PLC STATUS",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 11, "bold")
        ).pack(
            anchor="w",
            padx=18,
            pady=(15, 10)
        )

        self.status_items = {}

        items = [
            ("MODE", "--"),
            ("ALARM", "NO"),
            ("FAULT", "NONE"),
            ("E-STOP", "RELEASED"),
            ("HEARTBEAT", "--"),
            ("RUNTIME", "0 s"),
        ]

        for name, value in items:

            row = tk.Frame(
                panel,
                bg=PANEL
            )

            row.pack(
                fill="x",
                padx=18,
                pady=4
            )

            tk.Label(
                row,
                text=name,
                bg=PANEL,
                fg=MUTED,
                font=("Segoe UI", 8, "bold")
            ).pack(
                side="left"
            )

            label = tk.Label(
                row,
                text=value,
                bg=PANEL,
                fg=TEXT,
                font=("Consolas", 9, "bold")
            )

            label.pack(
                side="right"
            )

            self.status_items[name] = label

    # ========================================================
    # ALARM HISTORY
    # ========================================================

    def build_alarm_history(self, parent):

        panel = tk.Frame(
            parent,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1
        )

        panel.pack(
            fill="x",
            padx=10,
            pady=(5, 10)
        )

        header = tk.Frame(
            panel,
            bg=PANEL
        )

        header.pack(
            fill="x",
            padx=18,
            pady=(15, 8)
        )

        tk.Label(
            header,
            text="ALARM HISTORY",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 11, "bold")
        ).pack(
            side="left"
        )

        tk.Button(
            header,
            text="CLEAR HISTORY",
            command=self.clear_alarm_history,
            bg="#334155",
            fg=TEXT,
            activebackground="#475569",
            relief="flat",
            font=("Segoe UI", 8, "bold"),
            cursor="hand2"
        ).pack(
            side="right"
        )

        frame = tk.Frame(
            panel,
            bg=PANEL
        )

        frame.pack(
            fill="x",
            padx=15,
            pady=(0, 15)
        )

        columns = (
            "time",
            "type",
            "code",
            "message"
        )

        self.alarm_tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
            height=7
        )

        self.alarm_tree.heading(
            "time",
            text="TIME"
        )

        self.alarm_tree.heading(
            "type",
            text="TYPE"
        )

        self.alarm_tree.heading(
            "code",
            text="CODE"
        )

        self.alarm_tree.heading(
            "message",
            text="MESSAGE"
        )

        self.alarm_tree.column(
            "time",
            width=100,
            anchor="center"
        )

        self.alarm_tree.column(
            "type",
            width=100,
            anchor="center"
        )

        self.alarm_tree.column(
            "code",
            width=70,
            anchor="center"
        )

        self.alarm_tree.column(
            "message",
            width=500
        )

        alarm_scrollbar = ttk.Scrollbar(
            frame,
            orient="vertical",
            command=self.alarm_tree.yview
        )

        self.alarm_tree.configure(
            yscrollcommand=alarm_scrollbar.set
        )

        self.alarm_tree.pack(
            side="left",
            fill="both",
            expand=True
        )

        alarm_scrollbar.pack(
            side="right",
            fill="y"
        )

    def add_alarm_history(
        self,
        event_type,
        fault_code,
        message
    ):

        timestamp = datetime.now().strftime(
            "%H:%M:%S"
        )

        self.alarm_history.append(
            {
                "time": timestamp,
                "type": event_type,
                "code": fault_code,
                "message": message
            }
        )

        self.alarm_tree.insert(
            "",
            0,
            values=(
                timestamp,
                event_type,
                fault_code,
                message
            )
        )

        items = self.alarm_tree.get_children()

        if len(items) > 50:

            for item in items[50:]:
                self.alarm_tree.delete(item)

    def clear_alarm_history(self):

        for item in self.alarm_tree.get_children():
            self.alarm_tree.delete(item)

        self.alarm_history.clear()

        self.footer_status.config(
            text="Alarm history cleared"
        )

    # ========================================================
    # TRENDS
    # ========================================================

    def build_trends(self):

        tk.Label(
            self.trend_tab,
            text="REAL-TIME PROCESS TRENDS",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 13, "bold")
        ).pack(
            anchor="w",
            padx=20,
            pady=(18, 5)
        )

        self.canvas = tk.Canvas(
            self.trend_tab,
            bg=PANEL,
            highlightthickness=0
        )

        self.canvas.pack(
            fill="both",
            expand=True,
            padx=20,
            pady=20
        )

        self.canvas.bind(
            "<Configure>",
            lambda event: self.draw_trends()
        )

    # ========================================================
    # REGISTERS
    # ========================================================

    def build_registers(self):

        tk.Label(
            self.register_tab,
            text="MODBUS REGISTER MONITOR",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 13, "bold")
        ).pack(
            anchor="w",
            padx=20,
            pady=(18, 10)
        )

        frame = tk.Frame(
            self.register_tab,
            bg=PANEL
        )

        frame.pack(
            fill="both",
            expand=True,
            padx=20,
            pady=(0, 20)
        )

        columns = (
            "address",
            "name",
            "raw",
            "engineering"
        )

        self.register_tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings"
        )

        headings = {
            "address": "ADDRESS",
            "name": "REGISTER",
            "raw": "RAW",
            "engineering": "ENGINEERING VALUE"
        }

        for column, title in headings.items():

            self.register_tree.heading(
                column,
                text=title
            )

        self.register_tree.column(
            "address",
            width=100,
            anchor="center"
        )

        self.register_tree.column(
            "name",
            width=300
        )

        self.register_tree.column(
            "raw",
            width=150,
            anchor="center"
        )

        self.register_tree.column(
            "engineering",
            width=300
        )

        scrollbar = ttk.Scrollbar(
            frame,
            orient="vertical",
            command=self.register_tree.yview
        )

        self.register_tree.configure(
            yscrollcommand=scrollbar.set
        )

        self.register_tree.pack(
            side="left",
            fill="both",
            expand=True
        )

        scrollbar.pack(
            side="right",
            fill="y"
        )

        self.register_rows = {}

        register_names = [
            ("MOTOR_COMMAND", 40001),
            ("MODE", 40002),
            ("SPEED_SETPOINT", 40003),
            ("ACTUAL_RPM", 40005),
            ("CURRENT", 40007),
            ("PRESSURE_SETPOINT", 40009),
            ("ACTUAL_PRESSURE", 40011),
            ("TEMPERATURE", 40013),
            ("ALARM", 40015),
            ("EMERGENCY_STOP", 40016),
            ("HEARTBEAT", 40017),
            ("STATUS_WORD", 40018),
            ("FAULT_CODE", 40019),
            ("RUNTIME_SECONDS", 40020),
        ]

        for name, address in register_names:

            item = self.register_tree.insert(
                "",
                "end",
                values=(
                    address,
                    name,
                    "--",
                    "--"
                )
            )

            self.register_rows[name] = item

    # ========================================================
    # DIAGNOSTICS
    # ========================================================

    def build_diagnostics(self):

        top = tk.Frame(
            self.diagnostics_tab,
            bg=BG
        )

        top.pack(
            fill="x",
            padx=20,
            pady=20
        )

        self.diagnostic_connection = self.diagnostic_card(
            top,
            "CONNECTION",
            "DISCONNECTED"
        )

        self.diagnostic_heartbeat = self.diagnostic_card(
            top,
            "HEARTBEAT",
            "--"
        )

        self.diagnostic_runtime = self.diagnostic_card(
            top,
            "RUNTIME",
            "0 s"
        )

        self.diagnostic_fault = self.diagnostic_card(
            top,
            "FAULT",
            "NONE"
        )

        panel = tk.Frame(
            self.diagnostics_tab,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1
        )

        panel.pack(
            fill="both",
            expand=True,
            padx=20,
            pady=(0, 20)
        )

        tk.Label(
            panel,
            text="STATUS WORD",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 11, "bold")
        ).pack(
            anchor="w",
            padx=20,
            pady=(18, 10)
        )

        self.status_bit_labels = {}

        for bit, name in STATUS_BITS:

            row = tk.Frame(
                panel,
                bg=PANEL
            )

            row.pack(
                fill="x",
                padx=20,
                pady=5
            )

            indicator = tk.Label(
                row,
                text="●",
                bg=PANEL,
                fg="#475569",
                font=("Segoe UI", 15)
            )

            indicator.pack(
                side="left"
            )

            tk.Label(
                row,
                text=f"BIT {bit:02d}",
                bg=PANEL,
                fg=MUTED,
                font=("Consolas", 9)
            ).pack(
                side="left",
                padx=10
            )

            tk.Label(
                row,
                text=name,
                bg=PANEL,
                fg=TEXT,
                font=("Segoe UI", 9, "bold")
            ).pack(
                side="left"
            )

            self.status_bit_labels[bit] = indicator

    def diagnostic_card(
        self,
        parent,
        title,
        value
    ):

        card = tk.Frame(
            parent,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1
        )

        card.pack(
            side="left",
            fill="both",
            expand=True,
            padx=5
        )

        tk.Label(
            card,
            text=title,
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8, "bold")
        ).pack(
            anchor="w",
            padx=15,
            pady=(12, 3)
        )

        label = tk.Label(
            card,
            text=value,
            bg=PANEL,
            fg=TEXT,
            font=("Consolas", 13, "bold")
        )

        label.pack(
            anchor="w",
            padx=15,
            pady=(0, 12)
        )

        return label

    # ========================================================
    # FOOTER
    # ========================================================

    def build_footer(self):

        footer = tk.Frame(
            self.root,
            bg=PANEL,
            height=30
        )

        footer.pack(
            fill="x"
        )

        footer.pack_propagate(False)

        self.footer_status = tk.Label(
            footer,
            text="Connecting to PLC...",
            bg=PANEL,
            fg=MUTED,
            font=("Consolas", 8)
        )

        self.footer_status.pack(
            side="left",
            padx=15
        )

        self.clock_label = tk.Label(
            footer,
            text="--:--:--",
            bg=PANEL,
            fg=MUTED,
            font=("Consolas", 8)
        )

        self.clock_label.pack(
            side="right",
            padx=15
        )

        self.update_clock()

    # ========================================================
    # CONNECTION
    # ========================================================

    def start_connection(self):

        success = self.modbus.connect()

        self.set_connection_status(
            success
        )

        self.root.after(
            POLL_INTERVAL_MS,
            self.poll
        )

    def set_connection_status(
        self,
        connected
    ):

        if connected:

            self.connection_dot.config(
                fg=GREEN
            )

            self.connection_label.config(
                text="CONNECTED",
                fg=GREEN
            )

            self.diagnostic_connection.config(
                text="CONNECTED",
                fg=GREEN
            )

        else:

            self.connection_dot.config(
                fg=RED
            )

            self.connection_label.config(
                text="DISCONNECTED",
                fg=RED
            )

            self.diagnostic_connection.config(
                text="DISCONNECTED",
                fg=RED
            )

    # ========================================================
    # POLLING
    # ========================================================

    def poll(self):

        if not self.running:
            return

        data = self.modbus.read_registers()

        if data is None:

            self.set_connection_status(
                False
            )

            self.footer_status.config(
                text="PLC OFFLINE — reconnecting..."
            )

            self.root.after(
                1000,
                self.reconnect
            )

        else:

            self.set_connection_status(
                True
            )

            self.process_data(
                data
            )

        self.root.after(
            POLL_INTERVAL_MS,
            self.poll
        )

    def reconnect(self):

        if not self.running:
            return

        if not self.modbus.connected:

            success = self.modbus.connect()

            self.set_connection_status(
                success
            )

    # ========================================================
    # DATA
    # ========================================================

    def read_uint32(
        self,
        data,
        address
    ):

        if address + 1 >= len(data):
            return 0

        return (
            (data[address] << 16)
            | data[address + 1]
        )

    def process_data(
        self,
        data
    ):

        motor_command = data[
            REG["MOTOR_COMMAND"]
        ]

        mode = data[
            REG["MODE"]
        ]

        speed_setpoint = self.read_uint32(
            data,
            REG["SPEED_SETPOINT"]
        )

        actual_rpm = self.read_uint32(
            data,
            REG["ACTUAL_RPM"]
        )

        current = (
            self.read_uint32(
                data,
                REG["CURRENT"]
            )
            / 10.0
        )

        pressure_setpoint = (
            self.read_uint32(
                data,
                REG["PRESSURE_SETPOINT"]
            )
            / 10.0
        )

        pressure = (
            self.read_uint32(
                data,
                REG["ACTUAL_PRESSURE"]
            )
            / 10.0
        )

        temperature = (
            self.read_uint32(
                data,
                REG["TEMPERATURE"]
            )
            / 10.0
        )

        alarm = data[
            REG["ALARM"]
        ]

        emergency_stop = data[
            REG["EMERGENCY_STOP"]
        ]

        heartbeat = data[
            REG["HEARTBEAT"]
        ]

        status_word = data[
            REG["STATUS_WORD"]
        ]

        fault_code = data[
            REG["FAULT_CODE"]
        ]

        runtime = self.read_uint32(
            data,
            REG["RUNTIME"]
        )

        # ====================================================
        # VALUES
        # ====================================================

        self.rpm_value.config(
            text=f"{actual_rpm:,}"
        )

        self.current_value.config(
            text=f"{current:.1f}"
        )

        self.pressure_value.config(
            text=f"{pressure:.1f}"
        )

        self.temperature_value.config(
            text=f"{temperature:.1f}"
        )

        self.motor_progress["value"] = min(
            actual_rpm,
            MAX_RPM
        )

        # ====================================================
        # STATE
        # ====================================================

        state = self.get_motor_state(
            actual_rpm,
            speed_setpoint,
            motor_command,
            fault_code
        )

        self.motor_state_label.config(
            text=state[0],
            fg=state[1]
        )

        # ====================================================
        # STATUS
        # ====================================================

        self.status_items[
            "MODE"
        ].config(
            text="AUTO" if mode else "MANUAL",
            fg=BLUE if mode else TEXT
        )

        self.status_items[
            "ALARM"
        ].config(
            text="ACTIVE" if alarm else "NO",
            fg=RED if alarm else GREEN
        )

        fault_name = FAULT_CODES.get(
            fault_code,
            f"UNKNOWN ({fault_code})"
        )

        self.status_items[
            "FAULT"
        ].config(
            text=fault_name,
            fg=RED if fault_code else GREEN
        )

        self.status_items[
            "E-STOP"
        ].config(
            text=(
                "ACTIVE"
                if emergency_stop
                else "RELEASED"
            ),
            fg=RED if emergency_stop else GREEN
        )

        self.status_items[
            "HEARTBEAT"
        ].config(
            text=str(heartbeat)
        )

        self.status_items[
            "RUNTIME"
        ].config(
            text=f"{runtime:,} s"
        )

        # ====================================================
        # DIAGNOSTICS
        # ====================================================

        self.diagnostic_heartbeat.config(
            text=str(heartbeat)
        )

        self.diagnostic_runtime.config(
            text=f"{runtime:,} s"
        )

        self.diagnostic_fault.config(
            text=fault_name,
            fg=RED if fault_code else GREEN
        )

        # ====================================================
        # ALARM
        # ====================================================

        self.update_alarm(
            alarm,
            emergency_stop,
            fault_code
        )

        # ====================================================
        # TRENDS
        # ====================================================

        self.rpm_history.append(
            actual_rpm
        )

        self.pressure_history.append(
            pressure
        )

        self.temperature_history.append(
            temperature
        )

        self.draw_trends()

        # ====================================================
        # REGISTERS
        # ====================================================

        self.update_register_table(
            data,
            speed_setpoint,
            actual_rpm,
            current,
            pressure_setpoint,
            pressure,
            temperature,
            runtime
        )

        # ====================================================
        # STATUS BITS
        # ====================================================

        self.update_status_bits(
            status_word
        )

        # ====================================================
        # SETPOINTS
        # ====================================================

        self.update_entry(
            self.rpm_entry,
            str(speed_setpoint)
        )

        self.update_entry(
            self.pressure_entry,
            f"{pressure_setpoint:.1f}"
        )

        # ====================================================
        # FOOTER
        # ====================================================

        self.footer_status.config(
            text=(
                f"PLC ONLINE  |  "
                f"Heartbeat {heartbeat}  |  "
                f"{PLC_IP}:{PLC_PORT}"
            )
        )

    # ========================================================
    # MOTOR STATE
    # ========================================================

    def get_motor_state(
        self,
        actual_rpm,
        setpoint,
        command,
        fault
    ):

        if fault:
            return "FAULT", RED

        if actual_rpm == 0:

            if command:
                return "STARTING", AMBER

            return "STOPPED", GREEN

        if command:

            if actual_rpm < setpoint:
                return "STARTING", AMBER

            if actual_rpm > setpoint:
                return "STARTING", AMBER

            return "RUNNING", GREEN

        return "STOPPING", AMBER

    # ========================================================
    # ALARM SYSTEM
    # ========================================================

    def update_alarm(
        self,
        alarm,
        emergency_stop,
        fault_code
    ):

        fault_name = FAULT_CODES.get(
            fault_code,
            f"UNKNOWN FAULT ({fault_code})"
        )

        # ====================================================
        # VISUAL ALARM STATE
        # ====================================================

        if emergency_stop:

            bg = RED_DARK

            text = (
                "⚠  EMERGENCY STOP ACTIVE"
            )

        elif fault_code:

            bg = RED_DARK

            text = (
                "●  FAULT ACTIVE — "
                + fault_name
            )

        elif alarm:

            bg = AMBER_DARK

            text = (
                "●  WARNING — "
                "PROCESS ALARM ACTIVE"
            )

        else:

            bg = GREEN_DARK

            text = (
                "●  SYSTEM READY — "
                "NO ACTIVE ALARMS"
            )

        self.alarm_banner.config(
            bg=bg
        )

        self.alarm_text.config(
            bg=bg,
            text=text
        )

        # ====================================================
        # EMERGENCY STOP ACTIVATED
        # ====================================================

        if (
            emergency_stop
            and not self.previous_emergency_stop
        ):

            self.add_alarm_history(
                "E-STOP",
                1,
                "Emergency stop activated"
            )

            messagebox.showwarning(
                "EMERGENCY STOP",
                (
                    "⚠ EMERGENCY STOP ACTIVE\n\n"
                    "The motor has been stopped "
                    "by the emergency stop signal."
                ),
                parent=self.root
            )

        # ====================================================
        # EMERGENCY STOP RELEASED
        # ====================================================

        elif (
            not emergency_stop
            and self.previous_emergency_stop
        ):

            self.add_alarm_history(
                "CLEARED",
                0,
                "Emergency stop released"
            )

        # ====================================================
        # NEW FAULT
        # ====================================================

        if (
            fault_code
            and fault_code != self.previous_fault_code
            and not emergency_stop
        ):

            self.add_alarm_history(
                "FAULT",
                fault_code,
                fault_name
            )

            messagebox.showwarning(
                "PLC FAULT",
                (
                    "⚠ PLC FAULT DETECTED\n\n"
                    f"Fault: {fault_name}\n"
                    f"Fault Code: {fault_code}"
                ),
                parent=self.root
            )

        # ====================================================
        # FAULT CLEARED
        # ====================================================

        elif (
            fault_code == 0
            and self.previous_fault_code != 0
        ):

            previous_fault_name = FAULT_CODES.get(
                self.previous_fault_code,
                "UNKNOWN"
            )

            self.add_alarm_history(
                "CLEARED",
                self.previous_fault_code,
                f"Fault cleared: {previous_fault_name}"
            )

        # ====================================================
        # PROCESS ALARM ACTIVATED
        # ====================================================

        if (
            alarm
            and not self.previous_alarm
            and not fault_code
            and not emergency_stop
        ):

            self.add_alarm_history(
                "WARNING",
                0,
                "Process alarm became active"
            )

            messagebox.showwarning(
                "PROCESS ALARM",
                (
                    "⚠ PROCESS ALARM ACTIVE\n\n"
                    "The PLC reports an active "
                    "process alarm."
                ),
                parent=self.root
            )

        # ====================================================
        # PROCESS ALARM CLEARED
        # ====================================================

        elif (
            not alarm
            and self.previous_alarm
            and not fault_code
            and not emergency_stop
        ):

            self.add_alarm_history(
                "CLEARED",
                0,
                "Process alarm cleared"
            )

        # ====================================================
        # SAVE CURRENT STATE
        # ====================================================

        self.previous_alarm = bool(alarm)

        self.previous_fault_code = fault_code

        self.previous_emergency_stop = bool(
            emergency_stop
        )

    # ========================================================
    # STATUS BITS
    # ========================================================

    def update_status_bits(
        self,
        status_word
    ):

        for bit, _name in STATUS_BITS:

            active = bool(
                status_word & (1 << bit)
            )

            self.status_bit_labels[
                bit
            ].config(
                fg=GREEN if active else "#475569"
            )

    # ========================================================
    # REGISTER TABLE
    # ========================================================

    def update_register_table(
        self,
        data,
        speed,
        actual_rpm,
        current,
        pressure_setpoint,
        pressure,
        temperature,
        runtime
    ):

        values = {

            "MOTOR_COMMAND": (
                data[0],
                "START" if data[0] else "STOP"
            ),

            "MODE": (
                data[1],
                "AUTO" if data[1] else "MANUAL"
            ),

            "SPEED_SETPOINT": (
                speed,
                f"{speed} RPM"
            ),

            "ACTUAL_RPM": (
                actual_rpm,
                f"{actual_rpm} RPM"
            ),

            "CURRENT": (
                self.read_uint32(data, 6),
                f"{current:.1f} A"
            ),

            "PRESSURE_SETPOINT": (
                self.read_uint32(data, 8),
                f"{pressure_setpoint:.1f} bar"
            ),

            "ACTUAL_PRESSURE": (
                self.read_uint32(data, 10),
                f"{pressure:.1f} bar"
            ),

            "TEMPERATURE": (
                self.read_uint32(data, 12),
                f"{temperature:.1f} °C"
            ),

            "ALARM": (
                data[14],
                "ACTIVE" if data[14] else "NORMAL"
            ),

            "EMERGENCY_STOP": (
                data[15],
                "ACTIVE" if data[15] else "RELEASED"
            ),

            "HEARTBEAT": (
                data[16],
                str(data[16])
            ),

            "STATUS_WORD": (
                data[17],
                f"0x{data[17]:04X}"
            ),

            "FAULT_CODE": (
                data[18],
                FAULT_CODES.get(
                    data[18],
                    f"UNKNOWN ({data[18]})"
                )
            ),

            "RUNTIME_SECONDS": (
                runtime,
                f"{runtime:,} s"
            ),
        }

        for name, pair in values.items():

            item = self.register_rows.get(
                name
            )

            if item is None:
                continue

            old_values = self.register_tree.item(
                item,
                "values"
            )

            address = old_values[0]

            self.register_tree.item(
                item,
                values=(
                    address,
                    name,
                    pair[0],
                    pair[1]
                )
            )

    # ========================================================
    # TRENDS
    # ========================================================

    def draw_trends(self):

        if not hasattr(
            self,
            "canvas"
        ):
            return

        self.canvas.delete(
            "all"
        )

        width = self.canvas.winfo_width()

        height = self.canvas.winfo_height()

        if width < 100 or height < 100:
            return

        left = 60
        right = 25
        top = 35
        bottom = 40

        chart_width = (
            width - left - right
        )

        chart_height = (
            height - top - bottom
        )

        for i in range(6):

            y = (
                top
                + chart_height * i / 5
            )

            self.canvas.create_line(
                left,
                y,
                width - right,
                y,
                fill=BORDER
            )

            rpm_value = (
                MAX_RPM
                * (1 - i / 5)
            )

            self.canvas.create_text(
                left - 10,
                y,
                text=str(
                    int(rpm_value)
                ),
                fill=MUTED,
                anchor="e",
                font=("Consolas", 8)
            )

        self.draw_series(
            list(self.rpm_history),
            left,
            top,
            chart_width,
            chart_height,
            MAX_RPM,
            BLUE
        )

        self.draw_series(
            list(self.pressure_history),
            left,
            top,
            chart_width,
            chart_height,
            MAX_PRESSURE,
            GREEN
        )

        self.draw_series(
            list(self.temperature_history),
            left,
            top,
            chart_width,
            chart_height,
            MAX_TEMPERATURE,
            AMBER
        )

        legend = [
            ("RPM", BLUE),
            ("PRESSURE", GREEN),
            ("TEMPERATURE", AMBER),
        ]

        x = left

        for name, color in legend:

            self.canvas.create_line(
                x,
                height - 18,
                x + 18,
                height - 18,
                fill=color,
                width=3
            )

            self.canvas.create_text(
                x + 25,
                height - 18,
                text=name,
                fill=MUTED,
                anchor="w",
                font=("Segoe UI", 8)
            )

            x += 110

    def draw_series(
        self,
        values,
        x,
        y,
        width,
        height,
        maximum,
        color
    ):

        if len(values) < 2:
            return

        points = []

        for i, value in enumerate(values):

            px = (
                x
                + width
                * i
                / max(
                    len(values) - 1,
                    1
                )
            )

            normalized = min(
                max(
                    value / maximum,
                    0
                ),
                1
            )

            py = (
                y
                + height
                * (1 - normalized)
            )

            points.extend(
                [px, py]
            )

        self.canvas.create_line(
            *points,
            fill=color,
            width=2,
            smooth=True
        )

    # ========================================================
    # COMMANDS
    # ========================================================

    def write_register(
        self,
        address,
        value
    ):

        success = self.modbus.write_register(
            address,
            value
        )

        if success:

            self.set_connection_status(
                True
            )

            self.footer_status.config(
                text=(
                    f"Command sent — "
                    f"Register {40001 + address} = {value}"
                )
            )

        else:

            self.set_connection_status(
                False
            )

            self.footer_status.config(
                text="PLC connection unavailable"
            )

            messagebox.showerror(
                "Modbus Error",
                (
                    "PLC'ye bağlanılamadı.\n\n"
                    f"Target: {PLC_IP}:{PLC_PORT}\n"
                    f"Register: {40001 + address}\n"
                    f"Value: {value}"
                ),
                parent=self.root
            )

    # ========================================================
    # APPLY SETPOINTS
    # ========================================================

    def apply_setpoints(self):

        try:

            speed_text = self.rpm_entry.get().strip()

            pressure_text = self.pressure_entry.get().strip()

            if not speed_text:

                self.show_message(
                    "Speed RPM değeri boş bırakılamaz."
                )

                self.rpm_entry.focus_set()

                return

            if not pressure_text:

                self.show_message(
                    "Pressure bar değeri boş bırakılamaz."
                )

                self.pressure_entry.focus_set()

                return

            speed = int(speed_text)

            pressure = float(
                pressure_text.replace(",", ".")
            )

            if speed < 0 or speed > MAX_RPM:

                self.show_message(
                    f"Speed RPM 0 ile {MAX_RPM} "
                    f"arasında olmalıdır."
                )

                self.rpm_entry.focus_set()

                return

            if pressure < 0 or pressure > MAX_PRESSURE:

                self.show_message(
                    f"Pressure 0 ile "
                    f"{MAX_PRESSURE:.1f} bar "
                    f"arasında olmalıdır."
                )

                self.pressure_entry.focus_set()

                return

            # Pressure PLC'de x10 ölçekli tutuluyor.

            pressure_scaled = int(
                round(
                    pressure * 10
                )
            )

            print(
                f"[HMI] Applying setpoints | "
                f"SPEED={speed} RPM | "
                f"PRESSURE={pressure:.1f} bar"
            )

            # =================================================
            # SPEED
            # =================================================

            speed_ok = self.modbus.write_uint32(
                REG["SPEED_SETPOINT"],
                speed
            )

            if not speed_ok:

                self.show_message(
                    "Speed RPM PLC'ye yazılamadı."
                )

                return

            # =================================================
            # PRESSURE
            # =================================================

            pressure_ok = self.modbus.write_uint32(
                REG["PRESSURE_SETPOINT"],
                pressure_scaled
            )

            if not pressure_ok:

                self.show_message(
                    "Pressure bar PLC'ye yazılamadı."
                )

                return

            print(
                f"[HMI] SETPOINTS APPLIED | "
                f"SPEED={speed} RPM | "
                f"PRESSURE={pressure:.1f} bar"
            )

            # =================================================
            # KEEP WRITTEN VALUES IN UI
            # =================================================

            self.update_entry(
                self.rpm_entry,
                str(speed),
                force=True
            )

            self.update_entry(
                self.pressure_entry,
                f"{pressure:.1f}",
                force=True
            )

            self.footer_status.config(
                text=(
                    f"Setpoints applied | "
                    f"Speed {speed} RPM | "
                    f"Pressure {pressure:.1f} bar"
                )
            )

            self.show_message(
                f"Setpoints applied successfully:\n"
                f"{speed} RPM / {pressure:.1f} bar"
            )

        except ValueError:

            self.show_message(
                "Geçersiz değer. Speed için tam sayı, "
                "pressure için sayısal değer girin."
            )

        except Exception as exc:

            print(
                f"[HMI] Apply setpoints error: {exc}"
            )

            self.show_message(
                f"Setpoints uygulanamadı:\n{exc}"
            )

    # ========================================================
    # HELPERS
    # ========================================================

    def update_entry(
        self,
        entry,
        value,
        force=False
    ):

        if not force:

            try:

                if self.root.focus_get() == entry:
                    return

            except Exception:
                pass

        if entry.get() == value:
            return

        entry.delete(
            0,
            tk.END
        )

        entry.insert(
            0,
            value
        )

    def show_message(
        self,
        message
    ):

        messagebox.showinfo(
            "Smart PLC Control Center",
            message,
            parent=self.root
        )

    def update_clock(self):

        if not self.running:
            return

        self.clock_label.config(
            text=datetime.now().strftime(
                "%H:%M:%S"
            )
        )

        self.root.after(
            1000,
            self.update_clock
        )

    # ========================================================
    # CLOSE
    # ========================================================

    def close(self):

        self.running = False

        try:
            self.root.unbind(
                "<MouseWheel>"
            )

            self.root.unbind(
                "<Button-4>"
            )

            self.root.unbind(
                "<Button-5>"
            )

        except Exception:
            pass

        self.modbus.disconnect()

        self.root.destroy()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    root = tk.Tk()

    app = PLCMonitor(
        root
    )

    root.mainloop()