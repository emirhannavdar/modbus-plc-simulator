import tkinter as tk
from tkinter import ttk, messagebox
from pymodbus.client import ModbusTcpClient
from datetime import datetime
from collections import deque

PLC_IP = "127.0.0.1"
PLC_PORT = 5020
UNIT_ID = 1

POLL_INTERVAL_MS = 500

MAX_RPM = 3000
MAX_PRESSURE = 10.0
MAX_TEMPERATURE = 100.0
MAX_CURRENT = 30.0

TREND_POINTS = 120

# PLC internal 0-based address + 40001 = displayed address
REGISTER_BASE = 40001

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

    "AUTO_MIN_RPM": 21,
    "AUTO_MAX_RPM": 23,

    "AUTO_MIN_PRESSURE": 25,
    "AUTO_MAX_PRESSURE": 27,

    "AUTO_TARGET_INTERVAL": 29,
    "AUTO_TARGET_CHANGE_PERCENT": 30,

    "AUTO_TARGET_RPM": 31,
    "AUTO_TARGET_PRESSURE": 33,

    "SOLAR_IRRADIANCE": 35,
    "MOTOR_STATE": 36,
    "LOAD_FACTOR": 37,

    "AUTO_START_TARGET_RPM": 38,
    "AUTO_START_TARGET_PRESSURE": 40,
}

REGISTER_COUNT = 42

MOTOR_STATES = {
    0: "STOPPED",
    1: "STARTING",
    2: "RUNNING",
    3: "STOPPING",
    4: "FAULT",
}

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


class SmartPLCControlCenter:

    def __init__(self, root):

        self.root = root
        self.root.title("SMART PLC CONTROL CENTER")
        self.root.geometry("1450x900")
        self.root.minsize(1200, 760)

        self.client = None
        self.connected = False

        self.registers = [0] * REGISTER_COUNT

        self.rpm_history = deque(maxlen=TREND_POINTS)
        self.pressure_history = deque(maxlen=TREND_POINTS)
        self.temp_history = deque(maxlen=TREND_POINTS)

        self.alarm_history = deque(maxlen=50)

        self.last_alarm = 0
        self.last_fault = 0

        self.dark_bg = "#0b1220"
        self.panel_bg = "#111a2b"
        self.card_bg = "#172235"
        self.border = "#26344d"
        self.text = "#e8eef8"
        self.muted = "#8c9ab3"
        self.green = "#38d39f"
        self.red = "#ff5c6c"
        self.yellow = "#f5c451"
        self.blue = "#4da3ff"
        self.orange = "#ff9f43"

        self.configure_style()
        self.build_ui()

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.on_close,
        )

        self.connect()

        self.root.after(
            POLL_INTERVAL_MS,
            self.poll,
        )

    # ========================================================
    # STYLE
    # ========================================================

    def configure_style(self):

        self.root.configure(
            bg=self.dark_bg
        )

        style = ttk.Style()
        style.theme_use("clam")

        style.configure(
            ".",
            background=self.dark_bg,
            foreground=self.text,
            font=("Segoe UI", 10),
        )

        style.configure(
            "TNotebook",
            background=self.dark_bg,
            borderwidth=0,
        )

        style.configure(
            "TNotebook.Tab",
            background=self.panel_bg,
            foreground=self.muted,
            padding=(18, 10),
            borderwidth=0,
        )

        style.map(
            "TNotebook.Tab",
            background=[
                ("selected", self.card_bg)
            ],
            foreground=[
                ("selected", self.text)
            ],
        )

        style.configure(
            "TButton",
            background=self.card_bg,
            foreground=self.text,
            bordercolor=self.border,
            padding=(12, 8),
        )

        style.map(
            "TButton",
            background=[
                ("active", "#22324b")
            ],
        )

        style.configure(
            "TEntry",
            fieldbackground="#0f1828",
            foreground=self.text,
            insertcolor=self.text,
            bordercolor=self.border,
            padding=7,
        )

        style.configure(
            "Treeview",
            background="#0f1828",
            foreground=self.text,
            fieldbackground="#0f1828",
            bordercolor=self.border,
            rowheight=28,
        )

        style.configure(
            "Treeview.Heading",
            background=self.card_bg,
            foreground=self.text,
            bordercolor=self.border,
        )

    # ========================================================
    # UI
    # ========================================================

    def build_ui(self):

        header = tk.Frame(
            self.root,
            bg=self.dark_bg,
            height=72,
        )

        header.pack(
            fill="x",
            padx=22,
            pady=(18, 8),
        )

        header.pack_propagate(False)

        title_frame = tk.Frame(
            header,
            bg=self.dark_bg,
        )

        title_frame.pack(
            side="left",
            fill="y",
        )

        tk.Label(
            title_frame,
            text="SMART PLC CONTROL CENTER",
            bg=self.dark_bg,
            fg=self.text,
            font=("Segoe UI", 20, "bold"),
        ).pack(anchor="w")

        tk.Label(
            title_frame,
            text="Industrial Modbus TCP • PLC Simulation & HMI",
            bg=self.dark_bg,
            fg=self.muted,
            font=("Segoe UI", 9),
        ).pack(anchor="w")

        self.connection_label = tk.Label(
            header,
            text="● DISCONNECTED",
            bg=self.dark_bg,
            fg=self.red,
            font=("Segoe UI", 10, "bold"),
        )

        self.connection_label.pack(
            side="right",
            padx=8,
        )

        self.alarm_banner = tk.Frame(
            self.root,
            bg=self.panel_bg,
            height=42,
        )

        self.alarm_banner.pack(
            fill="x",
            padx=22,
            pady=(0, 12),
        )

        self.alarm_banner.pack_propagate(False)

        self.alarm_label = tk.Label(
            self.alarm_banner,
            text="SYSTEM READY",
            bg=self.panel_bg,
            fg=self.green,
            font=("Segoe UI", 10, "bold"),
        )

        self.alarm_label.pack(
            side="left",
            padx=16,
        )

        self.notebook = ttk.Notebook(
            self.root
        )

        self.notebook.pack(
            fill="both",
            expand=True,
            padx=22,
            pady=(0, 12),
        )

        self.overview_tab = tk.Frame(
            self.notebook,
            bg=self.dark_bg,
        )

        self.trends_tab = tk.Frame(
            self.notebook,
            bg=self.dark_bg,
        )

        self.registers_tab = tk.Frame(
            self.notebook,
            bg=self.dark_bg,
        )

        self.diagnostics_tab = tk.Frame(
            self.notebook,
            bg=self.dark_bg,
        )

        self.notebook.add(
            self.overview_tab,
            text="OVERVIEW",
        )

        self.notebook.add(
            self.trends_tab,
            text="TRENDS",
        )

        self.notebook.add(
            self.registers_tab,
            text="REGISTERS",
        )

        self.notebook.add(
            self.diagnostics_tab,
            text="DIAGNOSTICS",
        )

        self.build_overview()
        self.build_trends()
        self.build_registers()
        self.build_diagnostics()

        footer = tk.Frame(
            self.root,
            bg=self.dark_bg,
            height=30,
        )

        footer.pack(
            fill="x",
            padx=22,
        )

        footer.pack_propagate(False)

        self.footer_status = tk.Label(
            footer,
            text=f"PLC: {PLC_IP}:{PLC_PORT}",
            bg=self.dark_bg,
            fg=self.muted,
            font=("Segoe UI", 9),
        )

        self.footer_status.pack(
            side="left"
        )

        self.clock_label = tk.Label(
            footer,
            text="",
            bg=self.dark_bg,
            fg=self.muted,
            font=("Segoe UI", 9),
        )

        self.clock_label.pack(
            side="right"
        )

        self.update_clock()

    # ========================================================
    # OVERVIEW
    # ========================================================

    def build_overview(self):

        canvas = tk.Canvas(
            self.overview_tab,
            bg=self.dark_bg,
            highlightthickness=0,
        )

        scrollbar = ttk.Scrollbar(
            self.overview_tab,
            orient="vertical",
            command=canvas.yview,
        )

        canvas.pack(
            side="left",
            fill="both",
            expand=True,
        )

        scrollbar.pack(
            side="right",
            fill="y",
        )

        canvas.configure(
            yscrollcommand=scrollbar.set
        )

        content = tk.Frame(
            canvas,
            bg=self.dark_bg,
        )

        window_id = canvas.create_window(
            (0, 0),
            window=content,
            anchor="nw",
        )

        def configure_content(event):

            canvas.configure(
                scrollregion=canvas.bbox("all")
            )

            canvas.itemconfig(
                window_id,
                width=event.width,
            )

        content.bind(
            "<Configure>",
            configure_content,
        )

        canvas.bind(
            "<Configure>",
            lambda event:
            canvas.itemconfig(
                window_id,
                width=event.width,
            ),
        )

        metrics = tk.Frame(
            content,
            bg=self.dark_bg,
        )

        metrics.pack(
            fill="x",
            padx=4,
            pady=4,
        )

        self.rpm_card = self.create_metric_card(
            metrics,
            "ACTUAL RPM",
            "0",
            "rpm",
            self.blue,
        )

        self.pressure_card = self.create_metric_card(
            metrics,
            "PRESSURE",
            "0.0",
            "bar",
            self.green,
        )

        self.current_card = self.create_metric_card(
            metrics,
            "CURRENT",
            "0.0",
            "A",
            self.orange,
        )

        self.temperature_card = self.create_metric_card(
            metrics,
            "TEMPERATURE",
            "0.0",
            "°C",
            self.yellow,
        )

        self.rpm_card.pack(
            side="left",
            fill="both",
            expand=True,
            padx=(0, 6),
        )

        self.pressure_card.pack(
            side="left",
            fill="both",
            expand=True,
            padx=6,
        )

        self.current_card.pack(
            side="left",
            fill="both",
            expand=True,
            padx=6,
        )

        self.temperature_card.pack(
            side="left",
            fill="both",
            expand=True,
            padx=(6, 0),
        )

        secondary = tk.Frame(
            content,
            bg=self.dark_bg,
        )

        secondary.pack(
            fill="x",
            padx=4,
            pady=(8, 0),
        )

        self.auto_target_card = self.create_metric_card(
            secondary,
            "AUTO TARGET",
            "0",
            "rpm",
            self.blue,
        )

        self.solar_card = self.create_metric_card(
            secondary,
            "SOLAR IRRADIANCE",
            "0",
            "%",
            self.yellow,
        )

        self.load_card = self.create_metric_card(
            secondary,
            "LOAD FACTOR",
            "0",
            "%",
            self.orange,
        )

        self.runtime_card = self.create_metric_card(
            secondary,
            "RUNTIME",
            "0",
            "sec",
            self.green,
        )

        self.auto_target_card.pack(
            side="left",
            fill="both",
            expand=True,
            padx=(0, 6),
        )

        self.solar_card.pack(
            side="left",
            fill="both",
            expand=True,
            padx=6,
        )

        self.load_card.pack(
            side="left",
            fill="both",
            expand=True,
            padx=6,
        )

        self.runtime_card.pack(
            side="left",
            fill="both",
            expand=True,
            padx=(6, 0),
        )

        state_panel = self.create_panel(
            content,
            "MOTOR STATE",
        )

        state_panel.pack(
            fill="x",
            padx=4,
            pady=(12, 8),
        )

        state_inner = tk.Frame(
            state_panel,
            bg=self.panel_bg,
        )

        state_inner.pack(
            fill="x",
            padx=16,
            pady=14,
        )

        self.motor_state_label = tk.Label(
            state_inner,
            text="STOPPED",
            bg=self.panel_bg,
            fg=self.muted,
            font=("Segoe UI", 18, "bold"),
        )

        self.motor_state_label.pack(
            side="left"
        )

        self.mode_label = tk.Label(
            state_inner,
            text="MANUAL",
            bg=self.panel_bg,
            fg=self.blue,
            font=("Segoe UI", 10, "bold"),
        )

        self.mode_label.pack(
            side="left",
            padx=24,
        )

        self.fault_label = tk.Label(
            state_inner,
            text="FAULT: NONE",
            bg=self.panel_bg,
            fg=self.green,
            font=("Segoe UI", 10, "bold"),
        )

        self.fault_label.pack(
            side="right"
        )

        control_panel = self.create_panel(
            content,
            "CONTROL PANEL",
        )

        control_panel.pack(
            fill="x",
            padx=4,
            pady=8,
        )

        control = tk.Frame(
            control_panel,
            bg=self.panel_bg,
        )

        control.pack(
            fill="x",
            padx=16,
            pady=14,
        )

        button_row = tk.Frame(
            control,
            bg=self.panel_bg,
        )

        button_row.pack(
            fill="x",
            pady=(0, 12),
        )

        self.make_button(
            button_row,
            "START",
            self.start_motor,
            self.green,
        ).pack(
            side="left",
            padx=(0, 8),
        )

        self.make_button(
            button_row,
            "STOP",
            self.stop_motor,
            self.red,
        ).pack(
            side="left",
            padx=8,
        )

        self.make_button(
            button_row,
            "MANUAL",
            self.set_manual,
            self.blue,
        ).pack(
            side="left",
            padx=8,
        )

        self.make_button(
            button_row,
            "AUTO",
            self.set_auto,
            self.orange,
        ).pack(
            side="left",
            padx=8,
        )

        self.make_button(
            button_row,
            "E-STOP",
            self.emergency_stop,
            self.red,
        ).pack(
            side="right",
            padx=(8, 0),
        )

        self.make_button(
            button_row,
            "RESET E-STOP",
            self.reset_emergency_stop,
            self.yellow,
        ).pack(
            side="right",
            padx=8,
        )

        manual_frame = tk.Frame(
            control,
            bg=self.panel_bg,
        )

        manual_frame.pack(
            fill="x",
            pady=(2, 12),
        )

        self.create_label(
            manual_frame,
            "Speed RPM",
        ).pack(side="left")

        self.speed_entry = ttk.Entry(
            manual_frame,
            width=12,
        )

        self.speed_entry.insert(
            0,
            "1500",
        )

        self.speed_entry.pack(
            side="left",
            padx=(8, 20),
        )

        self.create_label(
            manual_frame,
            "Pressure bar",
        ).pack(side="left")

        self.pressure_entry = ttk.Entry(
            manual_frame,
            width=12,
        )

        self.pressure_entry.insert(
            0,
            "5.0",
        )

        self.pressure_entry.pack(
            side="left",
            padx=(8, 20),
        )

        self.make_button(
            manual_frame,
            "APPLY SETPOINTS",
            self.apply_manual_setpoints,
            self.blue,
        ).pack(
            side="left"
        )

        auto_title = tk.Label(
            control,
            text="AUTO MODE SETTINGS",
            bg=self.panel_bg,
            fg=self.text,
            font=("Segoe UI", 10, "bold"),
        )

        auto_title.pack(
            anchor="w",
            pady=(4, 10),
        )

        auto_grid = tk.Frame(
            control,
            bg=self.panel_bg,
        )

        auto_grid.pack(
            fill="x",
            pady=(0, 10),
        )

        self.auto_min_rpm_entry = self.create_auto_field(
            auto_grid,
            "Min RPM",
            "1200",
            0,
            0,
        )

        self.auto_max_rpm_entry = self.create_auto_field(
            auto_grid,
            "Max RPM",
            "2500",
            0,
            2,
        )

        self.auto_min_pressure_entry = self.create_auto_field(
            auto_grid,
            "Min Pressure",
            "3.0",
            0,
            4,
        )

        self.auto_max_pressure_entry = self.create_auto_field(
            auto_grid,
            "Max Pressure",
            "8.0",
            0,
            6,
        )

        self.auto_interval_entry = self.create_auto_field(
            auto_grid,
            "Target Interval",
            "10",
            2,
            0,
        )

        self.auto_change_entry = self.create_auto_field(
            auto_grid,
            "Target Change %",
            "15",
            2,
            2,
        )

        self.auto_target_rpm_entry = self.create_auto_field(
            auto_grid,
            "Target RPM",
            "1850",
            2,
            4,
        )

        self.auto_target_pressure_entry = self.create_auto_field(
            auto_grid,
            "Target Pressure",
            "5.5",
            2,
            6,
        )

        for column in range(7):
            auto_grid.columnconfigure(
                column,
                weight=1,
            )

        self.auto_target_rpm_label = tk.Label(
            control,
            text="Current Target RPM: 0",
            bg=self.panel_bg,
            fg=self.blue,
            font=("Segoe UI", 9, "bold"),
        )

        self.auto_target_rpm_label.pack(
            anchor="w"
        )

        self.auto_target_pressure_label = tk.Label(
            control,
            text="Current Target Pressure: 0.0 bar",
            bg=self.panel_bg,
            fg=self.green,
            font=("Segoe UI", 9, "bold"),
        )

        self.auto_target_pressure_label.pack(
            anchor="w"
        )

        self.make_button(
            control,
            "APPLY MOTOR SETTINGS",
            self.apply_auto_settings,
            self.orange,
        ).pack(
            anchor="w",
            pady=(8, 4),
        )

        status_panel = self.create_panel(
            content,
            "SYSTEM STATUS",
        )

        status_panel.pack(
            fill="x",
            padx=4,
            pady=8,
        )

        status_inner = tk.Frame(
            status_panel,
            bg=self.panel_bg,
        )

        status_inner.pack(
            fill="x",
            padx=16,
            pady=12,
        )

        self.status_labels = {}

        status_items = [
            ("Command", "STOP"),
            ("Mode", "MANUAL"),
            ("Alarm", "NONE"),
            ("Emergency Stop", "OFF"),
            ("Heartbeat", "0"),
            ("Status Word", "0"),
        ]

        for i, (name, value) in enumerate(
            status_items
        ):

            box = tk.Frame(
                status_inner,
                bg=self.panel_bg,
            )

            box.grid(
                row=0,
                column=i,
                padx=10,
                sticky="w",
            )

            tk.Label(
                box,
                text=name,
                bg=self.panel_bg,
                fg=self.muted,
                font=("Segoe UI", 8),
            ).pack(anchor="w")

            label = tk.Label(
                box,
                text=value,
                bg=self.panel_bg,
                fg=self.text,
                font=("Segoe UI", 10, "bold"),
            )

            label.pack(anchor="w")

            self.status_labels[name] = label

        alarm_panel = self.create_panel(
            content,
            "ALARM HISTORY",
        )

        alarm_panel.pack(
            fill="both",
            expand=True,
            padx=4,
            pady=(8, 16),
        )

        self.alarm_list = tk.Listbox(
            alarm_panel,
            bg="#0f1828",
            fg=self.text,
            selectbackground="#22324b",
            highlightthickness=0,
            borderwidth=0,
            height=6,
        )

        self.alarm_list.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=12,
        )

    # ========================================================
    # TRENDS
    # ========================================================

    def build_trends(self):

        top = tk.Frame(
            self.trends_tab,
            bg=self.dark_bg,
        )

        top.pack(
            fill="x",
            padx=8,
            pady=8,
        )

        tk.Label(
            top,
            text="REAL-TIME PROCESS TRENDS",
            bg=self.dark_bg,
            fg=self.text,
            font=("Segoe UI", 13, "bold"),
        ).pack(side="left")

        tk.Label(
            top,
            text="RPM / PRESSURE / TEMPERATURE",
            bg=self.dark_bg,
            fg=self.muted,
            font=("Segoe UI", 9),
        ).pack(side="right")

        self.trend_canvas = tk.Canvas(
            self.trends_tab,
            bg="#0f1828",
            highlightthickness=0,
        )

        self.trend_canvas.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=8,
        )

        self.trend_canvas.bind(
            "<Configure>",
            lambda event: self.draw_trends(),
        )

    # ========================================================
    # REGISTERS
    # ========================================================

    def build_registers(self):

        frame = tk.Frame(
            self.registers_tab,
            bg=self.dark_bg,
        )

        frame.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=8,
        )

        columns = (
            "address",
            "name",
            "value",
            "hex",
        )

        self.register_tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings",
        )

        self.register_tree.heading(
            "address",
            text="ADDRESS",
        )

        self.register_tree.heading(
            "name",
            text="REGISTER",
        )

        self.register_tree.heading(
            "value",
            text="VALUE",
        )

        self.register_tree.heading(
            "hex",
            text="HEX",
        )

        self.register_tree.column(
            "address",
            width=100,
            anchor="center",
        )

        self.register_tree.column(
            "name",
            width=340,
            anchor="w",
        )

        self.register_tree.column(
            "value",
            width=180,
            anchor="center",
        )

        self.register_tree.column(
            "hex",
            width=180,
            anchor="center",
        )

        self.register_tree.pack(
            fill="both",
            expand=True,
        )

        names = {
            0: "MOTOR_COMMAND",
            1: "MODE",
            2: "SPEED_SETPOINT HIGH",
            3: "SPEED_SETPOINT LOW",
            4: "ACTUAL_RPM HIGH",
            5: "ACTUAL_RPM LOW",
            6: "CURRENT HIGH",
            7: "CURRENT LOW",
            8: "PRESSURE_SETPOINT HIGH",
            9: "PRESSURE_SETPOINT LOW",
            10: "ACTUAL_PRESSURE HIGH",
            11: "ACTUAL_PRESSURE LOW",
            12: "TEMPERATURE HIGH",
            13: "TEMPERATURE LOW",
            14: "ALARM",
            15: "EMERGENCY_STOP",
            16: "HEARTBEAT",
            17: "STATUS_WORD",
            18: "FAULT_CODE",
            19: "RUNTIME HIGH",
            20: "RUNTIME LOW",
            21: "AUTO_MIN_RPM HIGH",
            22: "AUTO_MIN_RPM LOW",
            23: "AUTO_MAX_RPM HIGH",
            24: "AUTO_MAX_RPM LOW",
            25: "AUTO_MIN_PRESSURE HIGH",
            26: "AUTO_MIN_PRESSURE LOW",
            27: "AUTO_MAX_PRESSURE HIGH",
            28: "AUTO_MAX_PRESSURE LOW",
            29: "AUTO_TARGET_INTERVAL",
            30: "AUTO_TARGET_CHANGE_PERCENT",
            31: "AUTO_TARGET_RPM HIGH",
            32: "AUTO_TARGET_RPM LOW",
            33: "AUTO_TARGET_PRESSURE HIGH",
            34: "AUTO_TARGET_PRESSURE LOW",
            35: "SOLAR_IRRADIANCE",
            36: "MOTOR_STATE",
            37: "LOAD_FACTOR",
            38: "AUTO_START_TARGET_RPM HIGH",
            39: "AUTO_START_TARGET_RPM LOW",
            40: "AUTO_START_TARGET_PRESSURE HIGH",
            41: "AUTO_START_TARGET_PRESSURE LOW",
        }

        self.register_items = {}

        for address in range(
            REGISTER_COUNT
        ):

            item = self.register_tree.insert(
                "",
                "end",
                values=(
                    REGISTER_BASE + address,
                    names.get(address, ""),
                    0,
                    "0x0000",
                ),
            )

            self.register_items[address] = item

    # ========================================================
    # DIAGNOSTICS
    # ========================================================

    def build_diagnostics(self):

        frame = tk.Frame(
            self.diagnostics_tab,
            bg=self.dark_bg,
        )

        frame.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=8,
        )

        self.diagnostic_status = tk.Label(
            frame,
            text="PLC CONNECTION: DISCONNECTED",
            bg=self.dark_bg,
            fg=self.red,
            font=("Segoe UI", 14, "bold"),
        )

        self.diagnostic_status.pack(
            anchor="w",
            pady=(0, 16),
        )

        status_panel = self.create_panel(
            frame,
            "STATUS WORD BITS",
        )

        status_panel.pack(
            fill="x",
            pady=(0, 12),
        )

        self.status_bits_frame = tk.Frame(
            status_panel,
            bg=self.panel_bg,
        )

        self.status_bits_frame.pack(
            fill="x",
            padx=16,
            pady=14,
        )

        self.status_bit_labels = []

        bit_names = [
            "STOPPED",
            "RUNNING",
            "AUTO MODE",
            "MANUAL MODE",
            "ALARM ACTIVE",
            "EMERGENCY STOP",
            "FAULT ACTIVE",
            "READY",
        ]

        for i, name in enumerate(bit_names):

            item = tk.Frame(
                self.status_bits_frame,
                bg=self.panel_bg,
            )

            item.grid(
                row=0,
                column=i,
                padx=8,
            )

            indicator = tk.Label(
                item,
                text="●",
                bg=self.panel_bg,
                fg=self.muted,
                font=("Segoe UI", 16),
            )

            indicator.pack()

            tk.Label(
                item,
                text=name,
                bg=self.panel_bg,
                fg=self.muted,
                font=("Segoe UI", 8),
            ).pack()

            self.status_bit_labels.append(
                indicator
            )

        info_panel = self.create_panel(
            frame,
            "PLC INFORMATION",
        )

        info_panel.pack(
            fill="x",
            pady=12,
        )

        info = tk.Frame(
            info_panel,
            bg=self.panel_bg,
        )

        info.pack(
            fill="x",
            padx=16,
            pady=14,
        )

        self.diag_labels = {}

        diagnostics = [
            "PLC IP",
            "PLC PORT",
            "UNIT ID",
            "REGISTER COUNT",
            "ADDRESS RANGE",
            "HEARTBEAT",
            "FAULT CODE",
            "MOTOR STATE",
            "SOLAR IRRADIANCE",
            "LOAD FACTOR",
        ]

        for name in diagnostics:

            row = tk.Frame(
                info,
                bg=self.panel_bg,
            )

            row.pack(
                fill="x",
                pady=3,
            )

            tk.Label(
                row,
                text=name,
                width=24,
                anchor="w",
                bg=self.panel_bg,
                fg=self.muted,
            ).pack(side="left")

            value = tk.Label(
                row,
                text="-",
                anchor="w",
                bg=self.panel_bg,
                fg=self.text,
                font=("Segoe UI", 9, "bold"),
            )

            value.pack(side="left")

            self.diag_labels[name] = value

    # ========================================================
    # HELPERS
    # ========================================================

    def create_panel(self, parent, title):

        panel = tk.Frame(
            parent,
            bg=self.panel_bg,
            highlightbackground=self.border,
            highlightthickness=1,
        )

        tk.Label(
            panel,
            text=title,
            bg=self.panel_bg,
            fg=self.text,
            font=("Segoe UI", 10, "bold"),
        ).pack(
            anchor="w",
            padx=16,
            pady=(12, 0),
        )

        return panel

    def create_metric_card(
        self,
        parent,
        title,
        value,
        unit,
        accent,
    ):

        card = tk.Frame(
            parent,
            bg=self.card_bg,
            highlightbackground=self.border,
            highlightthickness=1,
        )

        tk.Label(
            card,
            text=title,
            bg=self.card_bg,
            fg=self.muted,
            font=("Segoe UI", 8, "bold"),
        ).pack(
            anchor="w",
            padx=14,
            pady=(12, 0),
        )

        value_frame = tk.Frame(
            card,
            bg=self.card_bg,
        )

        value_frame.pack(
            anchor="w",
            padx=14,
            pady=(4, 12),
        )

        value_label = tk.Label(
            value_frame,
            text=value,
            bg=self.card_bg,
            fg=accent,
            font=("Segoe UI", 22, "bold"),
        )

        value_label.pack(
            side="left"
        )

        tk.Label(
            value_frame,
            text=unit,
            bg=self.card_bg,
            fg=self.muted,
            font=("Segoe UI", 9),
        ).pack(
            side="left",
            padx=(5, 0),
            pady=(10, 0),
        )

        card.value_label = value_label

        return card

    def make_button(
        self,
        parent,
        text,
        command,
        accent,
    ):

        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=self.card_bg,
            fg=accent,
            activebackground="#22324b",
            activeforeground=accent,
            relief="flat",
            bd=0,
            padx=14,
            pady=8,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )

    def create_label(
        self,
        parent,
        text,
    ):

        return tk.Label(
            parent,
            text=text,
            bg=self.panel_bg,
            fg=self.muted,
            font=("Segoe UI", 9),
        )

    def create_auto_field(
        self,
        parent,
        label,
        default,
        row,
        column,
    ):

        frame = tk.Frame(
            parent,
            bg=self.panel_bg,
        )

        frame.grid(
            row=row,
            column=column,
            padx=8,
            pady=4,
            sticky="w",
        )

        tk.Label(
            frame,
            text=label,
            bg=self.panel_bg,
            fg=self.muted,
            font=("Segoe UI", 8),
        ).pack(anchor="w")

        entry = ttk.Entry(
            frame,
            width=13,
        )

        entry.insert(
            0,
            default,
        )

        entry.pack(
            pady=(3, 0),
        )

        return entry

    # ========================================================
    # CONNECTION
    # ========================================================

    def connect(self):

        try:

            if self.client:
                self.client.close()

            self.client = ModbusTcpClient(
                host=PLC_IP,
                port=PLC_PORT,
                timeout=2,
            )

            self.connected = self.client.connect()

            if self.connected:

                self.connection_label.config(
                    text="● CONNECTED",
                    fg=self.green,
                )

                self.footer_status.config(
                    text=(
                        f"PLC: {PLC_IP}:{PLC_PORT} "
                        "• CONNECTED"
                    ),
                )

                self.diagnostic_status.config(
                    text="PLC CONNECTION: CONNECTED",
                    fg=self.green,
                )

            else:

                self.connection_label.config(
                    text="● DISCONNECTED",
                    fg=self.red,
                )

        except Exception:

            self.connected = False

    # ========================================================
    # POLL
    # ========================================================

    def poll(self):

        try:

            if not self.connected:
                self.connect()

            if self.connected:

                result = self.client.read_holding_registers(
                    address=0,
                    count=REGISTER_COUNT,
                    device_id=UNIT_ID,
                )

                if result and not result.isError():

                    self.registers = list(
                        result.registers
                    )

                    self.update_from_registers()

                else:

                    self.connected = False

        except Exception:

            self.connected = False

        if not self.connected:

            self.connection_label.config(
                text="● DISCONNECTED",
                fg=self.red,
            )

            self.diagnostic_status.config(
                text="PLC CONNECTION: DISCONNECTED",
                fg=self.red,
            )

        self.root.after(
            POLL_INTERVAL_MS,
            self.poll,
        )

    # ========================================================
    # REGISTER HELPERS
    # ========================================================

    def read_uint32(self, address):

        return (
            (self.registers[address] << 16)
            | self.registers[address + 1]
        )

    def write_uint32(
        self,
        address,
        value,
    ):

        value = max(
            0,
            int(value),
        )

        high = (
            value >> 16
        ) & 0xFFFF

        low = value & 0xFFFF

        return self.client.write_registers(
            address=address,
            values=[high, low],
            device_id=UNIT_ID,
        )

    def write_register(
        self,
        address,
        value,
    ):

        return self.client.write_register(
            address=address,
            value=int(value),
            device_id=UNIT_ID,
        )

    # ========================================================
    # UPDATE
    # ========================================================

    def update_from_registers(self):

        r = self.registers

        command = r[REG["MOTOR_COMMAND"]]
        mode = r[REG["MODE"]]

        actual_rpm = self.read_uint32(
            REG["ACTUAL_RPM"]
        )

        current = (
            self.read_uint32(
                REG["CURRENT"]
            ) / 10.0
        )

        pressure = (
            self.read_uint32(
                REG["ACTUAL_PRESSURE"]
            ) / 10.0
        )

        temperature = (
            self.read_uint32(
                REG["TEMPERATURE"]
            ) / 10.0
        )

        alarm = r[REG["ALARM"]]
        emergency_stop = r[
            REG["EMERGENCY_STOP"]
        ]

        heartbeat = r[
            REG["HEARTBEAT"]
        ]

        status_word = r[
            REG["STATUS_WORD"]
        ]

        fault_code = r[
            REG["FAULT_CODE"]
        ]

        runtime = self.read_uint32(
            REG["RUNTIME"]
        )

        auto_min_rpm = self.read_uint32(
            REG["AUTO_MIN_RPM"]
        )

        auto_max_rpm = self.read_uint32(
            REG["AUTO_MAX_RPM"]
        )

        auto_min_pressure = (
            self.read_uint32(
                REG["AUTO_MIN_PRESSURE"]
            ) / 10.0
        )

        auto_max_pressure = (
            self.read_uint32(
                REG["AUTO_MAX_PRESSURE"]
            ) / 10.0
        )

        auto_interval = r[
            REG["AUTO_TARGET_INTERVAL"]
        ]

        auto_change = r[
            REG["AUTO_TARGET_CHANGE_PERCENT"]
        ]

        auto_target_rpm = self.read_uint32(
            REG["AUTO_TARGET_RPM"]
        )

        auto_target_pressure = (
            self.read_uint32(
                REG["AUTO_TARGET_PRESSURE"]
            ) / 10.0
        )

        start_target_rpm = self.read_uint32(
            REG["AUTO_START_TARGET_RPM"]
        )

        start_target_pressure = (
            self.read_uint32(
                REG["AUTO_START_TARGET_PRESSURE"]
            ) / 10.0
        )

        solar = (
            r[REG["SOLAR_IRRADIANCE"]]
            / 100.0
        )

        motor_state = r[
            REG["MOTOR_STATE"]
        ]

        load_factor = (
            r[REG["LOAD_FACTOR"]]
            / 100.0
        )

        self.rpm_card.value_label.config(
            text=f"{actual_rpm:,}"
        )

        self.pressure_card.value_label.config(
            text=f"{pressure:.1f}"
        )

        self.current_card.value_label.config(
            text=f"{current:.1f}"
        )

        self.temperature_card.value_label.config(
            text=f"{temperature:.1f}"
        )

        self.auto_target_card.value_label.config(
            text=f"{auto_target_rpm:,}"
        )

        self.solar_card.value_label.config(
            text=f"{solar * 100:.0f}"
        )

        self.load_card.value_label.config(
            text=f"{load_factor * 100:.0f}"
        )

        self.runtime_card.value_label.config(
            text=f"{runtime:,}"
        )

        state_text = MOTOR_STATES.get(
            motor_state,
            f"UNKNOWN ({motor_state})",
        )

        self.motor_state_label.config(
            text=state_text
        )

        self.mode_label.config(
            text=(
                "AUTO"
                if mode
                else "MANUAL"
            ),
            fg=(
                self.orange
                if mode
                else self.blue
            ),
        )

        fault_text = FAULT_CODES.get(
            fault_code,
            f"UNKNOWN ({fault_code})",
        )

        self.fault_label.config(
            text=f"FAULT: {fault_text}",
            fg=(
                self.red
                if fault_code
                else self.green
            ),
        )

        # MANUAL setpointler artık PLC'den okunur.
        if not mode:

            self.update_entry(
                self.speed_entry,
                str(
                    self.read_uint32(
                        REG["SPEED_SETPOINT"]
                    )
                ),
            )

            self.update_entry(
                self.pressure_entry,
                f"{self.read_uint32(REG['PRESSURE_SETPOINT']) / 10.0:.1f}",
            )

        self.update_entry(
            self.auto_min_rpm_entry,
            str(auto_min_rpm),
        )

        self.update_entry(
            self.auto_max_rpm_entry,
            str(auto_max_rpm),
        )

        self.update_entry(
            self.auto_min_pressure_entry,
            f"{auto_min_pressure:.1f}",
        )

        self.update_entry(
            self.auto_max_pressure_entry,
            f"{auto_max_pressure:.1f}",
        )

        self.update_entry(
            self.auto_interval_entry,
            str(auto_interval),
        )

        self.update_entry(
            self.auto_change_entry,
            str(auto_change),
        )

        self.update_entry(
            self.auto_target_rpm_entry,
            str(start_target_rpm),
        )

        self.update_entry(
            self.auto_target_pressure_entry,
            f"{start_target_pressure:.1f}",
        )

        self.auto_target_rpm_label.config(
            text=(
                f"Current Target RPM: "
                f"{auto_target_rpm:,}"
            )
        )

        self.auto_target_pressure_label.config(
            text=(
                f"Current Target Pressure: "
                f"{auto_target_pressure:.1f} bar"
            )
        )

        self.status_labels["Command"].config(
            text=(
                "START"
                if command
                else "STOP"
            )
        )

        self.status_labels["Mode"].config(
            text=(
                "AUTO"
                if mode
                else "MANUAL"
            )
        )

        self.status_labels["Alarm"].config(
            text=(
                "ACTIVE"
                if alarm
                else "NONE"
            ),
            fg=(
                self.red
                if alarm
                else self.green
            ),
        )

        self.status_labels[
            "Emergency Stop"
        ].config(
            text=(
                "ON"
                if emergency_stop
                else "OFF"
            ),
            fg=(
                self.red
                if emergency_stop
                else self.green
            ),
        )

        self.status_labels[
            "Heartbeat"
        ].config(
            text=str(heartbeat)
        )

        self.status_labels[
            "Status Word"
        ].config(
            text=f"0x{status_word:04X}"
        )

        if emergency_stop:

            self.alarm_label.config(
                text="EMERGENCY STOP ACTIVE",
                fg=self.red,
            )

        elif fault_code:

            self.alarm_label.config(
                text=f"FAULT: {fault_text}",
                fg=self.red,
            )

        elif alarm:

            self.alarm_label.config(
                text="WARNING / ALARM ACTIVE",
                fg=self.yellow,
            )

        else:

            self.alarm_label.config(
                text="SYSTEM READY",
                fg=self.green,
            )

        if (
            alarm != self.last_alarm
            or fault_code != self.last_fault
        ):

            if alarm or fault_code:

                timestamp = datetime.now().strftime(
                    "%H:%M:%S"
                )

                message = (
                    f"[{timestamp}] "
                    f"Alarm={alarm} | "
                    f"Fault={fault_text}"
                )

                self.alarm_history.appendleft(
                    message
                )

                self.alarm_list.delete(
                    0,
                    tk.END,
                )

                for item in self.alarm_history:

                    self.alarm_list.insert(
                        tk.END,
                        item,
                    )

            self.last_alarm = alarm
            self.last_fault = fault_code

        self.rpm_history.append(
            actual_rpm
        )

        self.pressure_history.append(
            pressure
        )

        self.temp_history.append(
            temperature
        )

        self.draw_trends()
        self.update_register_table()

        self.update_diagnostics(
            heartbeat,
            fault_code,
            motor_state,
            solar,
            load_factor,
            status_word,
        )

    # ========================================================
    # ENTRY
    # ========================================================

    def update_entry(
        self,
        entry,
        value,
        force=False,
    ):

        if not force:

            try:

                if (
                    self.root.focus_get()
                    == entry
                ):
                    return

            except Exception:
                pass

        if entry.get() == value:
            return

        entry.delete(
            0,
            tk.END,
        )

        entry.insert(
            0,
            value,
        )

    # ========================================================
    # REGISTER TABLE
    # ========================================================

    def update_register_table(self):

        names = {
            0: "MOTOR_COMMAND",
            1: "MODE",
            2: "SPEED_SETPOINT HIGH",
            3: "SPEED_SETPOINT LOW",
            4: "ACTUAL_RPM HIGH",
            5: "ACTUAL_RPM LOW",
            6: "CURRENT HIGH",
            7: "CURRENT LOW",
            8: "PRESSURE_SETPOINT HIGH",
            9: "PRESSURE_SETPOINT LOW",
            10: "ACTUAL_PRESSURE HIGH",
            11: "ACTUAL_PRESSURE LOW",
            12: "TEMPERATURE HIGH",
            13: "TEMPERATURE LOW",
            14: "ALARM",
            15: "EMERGENCY_STOP",
            16: "HEARTBEAT",
            17: "STATUS_WORD",
            18: "FAULT_CODE",
            19: "RUNTIME HIGH",
            20: "RUNTIME LOW",
            21: "AUTO_MIN_RPM HIGH",
            22: "AUTO_MIN_RPM LOW",
            23: "AUTO_MAX_RPM HIGH",
            24: "AUTO_MAX_RPM LOW",
            25: "AUTO_MIN_PRESSURE HIGH",
            26: "AUTO_MIN_PRESSURE LOW",
            27: "AUTO_MAX_PRESSURE HIGH",
            28: "AUTO_MAX_PRESSURE LOW",
            29: "AUTO_TARGET_INTERVAL",
            30: "AUTO_TARGET_CHANGE_PERCENT",
            31: "AUTO_TARGET_RPM HIGH",
            32: "AUTO_TARGET_RPM LOW",
            33: "AUTO_TARGET_PRESSURE HIGH",
            34: "AUTO_TARGET_PRESSURE LOW",
            35: "SOLAR_IRRADIANCE",
            36: "MOTOR_STATE",
            37: "LOAD_FACTOR",
            38: "AUTO_START_TARGET_RPM HIGH",
            39: "AUTO_START_TARGET_RPM LOW",
            40: "AUTO_START_TARGET_PRESSURE HIGH",
            41: "AUTO_START_TARGET_PRESSURE LOW",
        }

        for address in range(
            min(
                len(self.registers),
                REGISTER_COUNT,
            )
        ):

            item = self.register_items.get(
                address
            )

            if item:

                self.register_tree.item(
                    item,
                    values=(
                        REGISTER_BASE + address,
                        names.get(
                            address,
                            "",
                        ),
                        self.registers[address],
                        (
                            f"0x"
                            f"{self.registers[address]:04X}"
                        ),
                    ),
                )

    # ========================================================
    # DIAGNOSTICS
    # ========================================================

    def update_diagnostics(
        self,
        heartbeat,
        fault_code,
        motor_state,
        solar,
        load_factor,
        status_word,
    ):

        self.diag_labels[
            "PLC IP"
        ].config(
            text=PLC_IP
        )

        self.diag_labels[
            "PLC PORT"
        ].config(
            text=str(PLC_PORT)
        )

        self.diag_labels[
            "UNIT ID"
        ].config(
            text=str(UNIT_ID)
        )

        self.diag_labels[
            "REGISTER COUNT"
        ].config(
            text=str(REGISTER_COUNT)
        )

        self.diag_labels[
            "ADDRESS RANGE"
        ].config(
            text=(
                f"{REGISTER_BASE}-"
                f"{REGISTER_BASE + REGISTER_COUNT - 1}"
            )
        )

        self.diag_labels[
            "HEARTBEAT"
        ].config(
            text=str(heartbeat)
        )

        self.diag_labels[
            "FAULT CODE"
        ].config(
            text=(
                f"{fault_code} - "
                f"{FAULT_CODES.get(fault_code, 'UNKNOWN')}"
            )
        )

        self.diag_labels[
            "MOTOR STATE"
        ].config(
            text=(
                f"{motor_state} - "
                f"{MOTOR_STATES.get(motor_state, 'UNKNOWN')}"
            )
        )

        self.diag_labels[
            "SOLAR IRRADIANCE"
        ].config(
            text=f"{solar * 100:.1f}%"
        )

        self.diag_labels[
            "LOAD FACTOR"
        ].config(
            text=f"{load_factor * 100:.1f}%"
        )

        for bit in range(8):

            active = bool(
                status_word
                & (1 << bit)
            )

            self.status_bit_labels[
                bit
            ].config(
                fg=(
                    self.green
                    if active
                    else self.muted
                )
            )

    # ========================================================
    # TRENDS
    # ========================================================

    def draw_trends(self):

        if not hasattr(
            self,
            "trend_canvas",
        ):
            return

        canvas = self.trend_canvas
        canvas.delete("all")

        width = canvas.winfo_width()
        height = canvas.winfo_height()

        if width < 100 or height < 100:
            return

        left = 60
        right = 20
        top = 30
        bottom = 40

        plot_width = (
            width - left - right
        )

        plot_height = (
            height - top - bottom
        )

        for i in range(6):

            y = (
                top
                + (plot_height / 5) * i
            )

            canvas.create_line(
                left,
                y,
                width - right,
                y,
                fill=self.border,
            )

        for i in range(7):

            x = (
                left
                + (plot_width / 6) * i
            )

            canvas.create_line(
                x,
                top,
                x,
                height - bottom,
                fill=self.border,
            )

        series = [
            (
                list(self.rpm_history),
                MAX_RPM,
                self.blue,
            ),
            (
                list(self.pressure_history),
                MAX_PRESSURE,
                self.green,
            ),
            (
                list(self.temp_history),
                MAX_TEMPERATURE,
                self.yellow,
            ),
        ]

        for values, maximum, color in series:

            if len(values) < 2:
                continue

            points = []

            for i, value in enumerate(values):

                x = (
                    left
                    + (
                        i
                        / max(
                            len(values) - 1,
                            1,
                        )
                    )
                    * plot_width
                )

                normalized = min(
                    max(
                        value / maximum,
                        0,
                    ),
                    1,
                )

                y = (
                    top
                    + plot_height
                    - normalized
                    * plot_height
                )

                points.extend(
                    [x, y]
                )

            canvas.create_line(
                *points,
                fill=color,
                width=2,
                smooth=True,
            )

    # ========================================================
    # COMMANDS
    # ========================================================

    def ensure_connection(self):

        if not self.connected:
            self.connect()

        if not self.connected:

            messagebox.showerror(
                "PLC Connection",
                "PLC bağlantısı kurulamadı.",
            )

            return False

        return True

    def start_motor(self):

        if not self.ensure_connection():
            return

        try:

            self.write_register(
                REG["MOTOR_COMMAND"],
                1,
            )

        except Exception as exc:

            messagebox.showerror(
                "START",
                str(exc),
            )

    def stop_motor(self):

        if not self.ensure_connection():
            return

        try:

            self.write_register(
                REG["MOTOR_COMMAND"],
                0,
            )

        except Exception as exc:

            messagebox.showerror(
                "STOP",
                str(exc),
            )

    def set_manual(self):

        if not self.ensure_connection():
            return

        try:

            self.write_register(
                REG["MODE"],
                0,
            )

        except Exception as exc:

            messagebox.showerror(
                "MANUAL",
                str(exc),
            )

    def set_auto(self):

        if not self.ensure_connection():
            return

        try:

            self.write_register(
                REG["MODE"],
                1,
            )

        except Exception as exc:

            messagebox.showerror(
                "AUTO",
                str(exc),
            )

    # ========================================================
    # MANUAL
    # ========================================================

    def apply_manual_setpoints(self):

        if not self.ensure_connection():
            return

        try:

            rpm = int(
                float(
                    self.speed_entry.get()
                )
            )

            pressure = float(
                self.pressure_entry.get()
            )

            if rpm < 0 or rpm > MAX_RPM:

                raise ValueError(
                    f"RPM 0-{MAX_RPM} arasında olmalı."
                )

            if (
                pressure < 0
                or pressure > MAX_PRESSURE
            ):

                raise ValueError(
                    f"Pressure 0-{MAX_PRESSURE} arasında olmalı."
                )

            self.write_uint32(
                REG["SPEED_SETPOINT"],
                rpm,
            )

            self.write_uint32(
                REG["PRESSURE_SETPOINT"],
                int(
                    round(
                        pressure * 10
                    )
                ),
            )

        except Exception as exc:

            messagebox.showerror(
                "SETPOINT ERROR",
                str(exc),
            )

    # ========================================================
    # AUTO SETTINGS
    # ========================================================

    def apply_auto_settings(self):

        if not self.ensure_connection():
            return

        try:

            min_rpm = int(
                float(
                    self.auto_min_rpm_entry.get()
                )
            )

            max_rpm = int(
                float(
                    self.auto_max_rpm_entry.get()
                )
            )

            min_pressure = float(
                self.auto_min_pressure_entry.get()
            )

            max_pressure = float(
                self.auto_max_pressure_entry.get()
            )

            interval = int(
                float(
                    self.auto_interval_entry.get()
                )
            )

            change_percent = float(
                self.auto_change_entry.get()
            )

            target_rpm = int(
                float(
                    self.auto_target_rpm_entry.get()
                )
            )

            target_pressure = float(
                self.auto_target_pressure_entry.get()
            )

            if min_rpm < 0:
                raise ValueError(
                    "Minimum RPM negatif olamaz."
                )

            if max_rpm > MAX_RPM:
                raise ValueError(
                    f"Maximum RPM {MAX_RPM} değerini geçemez."
                )

            if min_rpm >= max_rpm:
                raise ValueError(
                    "Minimum RPM, Maximum RPM'den küçük olmalı."
                )

            if (
                min_pressure < 0
                or max_pressure > MAX_PRESSURE
            ):

                raise ValueError(
                    "Pressure sınırları geçersiz."
                )

            if min_pressure >= max_pressure:
                raise ValueError(
                    "Minimum pressure, Maximum pressure'dan küçük olmalı."
                )

            if not (
                min_rpm
                <= target_rpm
                <= max_rpm
            ):

                raise ValueError(
                    "Target RPM, Min-Max RPM aralığında olmalı."
                )

            if not (
                min_pressure
                <= target_pressure
                <= max_pressure
            ):

                raise ValueError(
                    "Target Pressure, Min-Max Pressure aralığında olmalı."
                )

            if interval < 1:
                raise ValueError(
                    "Target interval en az 1 saniye olmalı."
                )

            if (
                change_percent <= 0
                or change_percent > 100
            ):

                raise ValueError(
                    "Target Change % 0-100 arasında olmalı."
                )

            self.write_uint32(
                REG["AUTO_MIN_RPM"],
                min_rpm,
            )

            self.write_uint32(
                REG["AUTO_MAX_RPM"],
                max_rpm,
            )

            self.write_uint32(
                REG["AUTO_MIN_PRESSURE"],
                int(
                    round(
                        min_pressure * 10
                    )
                ),
            )

            self.write_uint32(
                REG["AUTO_MAX_PRESSURE"],
                int(
                    round(
                        max_pressure * 10
                    )
                ),
            )

            self.write_register(
                REG["AUTO_TARGET_INTERVAL"],
                interval,
            )

            self.write_register(
                REG["AUTO_TARGET_CHANGE_PERCENT"],
                int(
                    round(
                        change_percent
                    )
                ),
            )

            self.write_uint32(
                REG["AUTO_START_TARGET_RPM"],
                target_rpm,
            )

            self.write_uint32(
                REG["AUTO_START_TARGET_PRESSURE"],
                int(
                    round(
                        target_pressure * 10
                    )
                ),
            )

            # HMI'dan verilen target'ı hemen göster.
            self.write_uint32(
                REG["AUTO_TARGET_RPM"],
                target_rpm,
            )

            self.write_uint32(
                REG["AUTO_TARGET_PRESSURE"],
                int(
                    round(
                        target_pressure * 10
                    )
                )

            )

        except Exception as exc:

            messagebox.showerror(
                "AUTO SETTINGS ERROR",
                str(exc),
            )

    # ========================================================
    # E-STOP
    # ========================================================

    def emergency_stop(self):

        if not self.ensure_connection():
            return

        try:

            self.write_register(
                REG["EMERGENCY_STOP"],
                1,
            )

        except Exception as exc:

            messagebox.showerror(
                "E-STOP",
                str(exc),
            )

    def reset_emergency_stop(self):

        if not self.ensure_connection():
            return

        try:

            self.write_register(
                REG["EMERGENCY_STOP"],
                0,
            )

        except Exception as exc:

            messagebox.showerror(
                "RESET E-STOP",
                str(exc),
            )

    # ========================================================
    # CLOCK
    # ========================================================

    def update_clock(self):

        self.clock_label.config(
            text=datetime.now().strftime(
                "%d.%m.%Y %H:%M:%S"
            )
        )

        self.root.after(
            1000,
            self.update_clock,
        )

    # ========================================================
    # CLOSE
    # ========================================================

    def on_close(self):

        try:

            if self.client:
                self.client.close()

        except Exception:
            pass

        self.root.destroy()


if __name__ == "__main__":

    root = tk.Tk()

    app = SmartPLCControlCenter(
        root
    )

    root.mainloop()