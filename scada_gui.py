import json
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from collections import deque
from datetime import datetime


# ============================================================
# CONFIGURATION
# ============================================================

API_URL = "http://127.0.0.1:8000"
UPDATE_INTERVAL = 1000
TREND_POINTS = 60


# ============================================================
# COLORS
# ============================================================

BG = "#0b1117"
PANEL = "#111923"
PANEL_2 = "#16212c"
BORDER = "#263442"

TEXT = "#e6edf3"
MUTED = "#8b9bab"

GREEN = "#3fb950"
RED = "#f85149"
YELLOW = "#d29922"
BLUE = "#58a6ff"
CYAN = "#39c5cf"
ORANGE = "#f0883e"


# ============================================================
# API CLIENT
# ============================================================

class APIClient:

    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")

    def request(self, method, path, data=None, timeout=3):
        url = f"{self.base_url}{path}"

        headers = {
            "Accept": "application/json"
        }

        body = None

        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = Request(
            url,
            data=body,
            headers=headers,
            method=method
        )

        with urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")

            if not raw:
                return None

            return json.loads(raw)

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, data=None):
        return self.request("POST", path, data)

    def put(self, path, data=None):
        return self.request("PUT", path, data)

    def delete(self, path):
        return self.request("DELETE", path)


# ============================================================
# MAIN APPLICATION
# ============================================================

class SCADAGUI:

    def __init__(self, root):
        self.root = root

        self.root.title("SCADAWATT - Industrial SCADA")
        self.root.geometry("1450x900")
        self.root.minsize(1200, 760)
        self.root.configure(bg=BG)

        self.api = APIClient(API_URL)

        self.running = True
        self.first_load = True

        self.latest_data = {}
        self.registers = []

        self.speed_history = deque(maxlen=TREND_POINTS)
        self.pressure_history = deque(maxlen=TREND_POINTS)
        self.temperature_history = deque(maxlen=TREND_POINTS)

        self.last_update = None

        self.create_styles()
        self.create_variables()
        self.create_ui()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.refresh_data()

    # ========================================================
    # STYLES
    # ========================================================

    def create_styles(self):
        style = ttk.Style()

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "Treeview",
            background=PANEL,
            foreground=TEXT,
            fieldbackground=PANEL,
            borderwidth=0,
            rowheight=34,
            font=("Segoe UI", 10)
        )

        style.configure(
            "Treeview.Heading",
            background=PANEL_2,
            foreground=TEXT,
            borderwidth=0,
            font=("Segoe UI Semibold", 10)
        )

        style.map(
            "Treeview",
            background=[("selected", "#1f6feb")],
            foreground=[("selected", "#ffffff")]
        )

        style.configure(
            "TCombobox",
            fieldbackground=PANEL_2,
            background=PANEL_2,
            foreground=TEXT,
            arrowcolor=TEXT
        )

    # ========================================================
    # VARIABLES
    # ========================================================

    def create_variables(self):
        self.motor_var = tk.StringVar(value="STOPPED")
        self.mode_var = tk.StringVar(value="MANUAL")
        self.speed_var = tk.StringVar(value="0")
        self.actual_rpm_var = tk.StringVar(value="0")
        self.current_var = tk.StringVar(value="0")
        self.pressure_var = tk.StringVar(value="0")
        self.temperature_var = tk.StringVar(value="0")

        self.api_status_var = tk.StringVar(value="OFFLINE")
        self.database_status_var = tk.StringVar(value="OFFLINE")
        self.scada_status_var = tk.StringVar(value="OFFLINE")
        self.plc_status_var = tk.StringVar(value="OFFLINE")
        self.modbus_status_var = tk.StringVar(value="OFFLINE")
        self.heartbeat_var = tk.StringVar(value="0")

        self.last_update_var = tk.StringVar(value="Waiting for data...")
        self.register_count_var = tk.StringVar(value="0 registers")

        self.speed_command_var = tk.StringVar(value="1500")
        self.pressure_command_var = tk.StringVar(value="50")

    # ========================================================
    # UI
    # ========================================================

    def create_ui(self):
        main = tk.Frame(self.root, bg=BG)
        main.pack(fill="both", expand=True)

        self.create_header(main)
        self.create_status_bar(main)

        content = tk.Frame(main, bg=BG)
        content.pack(fill="both", expand=True, padx=18, pady=(8, 0))

        self.create_process_section(content)
        self.create_control_section(content)
        self.create_trend_section(content)
        self.create_register_section(content)

        self.create_footer(main)

    # ========================================================
    # HEADER
    # ========================================================

    def create_header(self, parent):
        header = tk.Frame(
            parent,
            bg=PANEL,
            height=76,
            highlightbackground=BORDER,
            highlightthickness=1
        )
        header.pack(fill="x")
        header.pack_propagate(False)

        left = tk.Frame(header, bg=PANEL)
        left.pack(side="left", padx=24)

        title = tk.Label(
            left,
            text="SCADAWATT",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 24, "bold")
        )
        title.pack(side="left", pady=15)

        subtitle = tk.Label(
            left,
            text="  INDUSTRIAL SCADA CONTROL SYSTEM",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 10)
        )
        subtitle.pack(side="left", pady=(20, 0))

        right = tk.Frame(header, bg=PANEL)
        right.pack(side="right", padx=24)

        tk.Label(
            right,
            text="API",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9)
        ).pack(side="left", padx=(0, 8))

        self.header_api_status = tk.Label(
            right,
            text="OFFLINE",
            bg=RED,
            fg="white",
            padx=12,
            pady=6,
            font=("Segoe UI", 9, "bold")
        )
        self.header_api_status.pack(side="left")

    # ========================================================
    # STATUS BAR
    # ========================================================

    def create_status_bar(self, parent):
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", padx=18, pady=(14, 8))

        self.status_api = self.create_status_card(
            bar,
            "API",
            self.api_status_var
        )

        self.status_database = self.create_status_card(
            bar,
            "DATABASE",
            self.database_status_var
        )

        self.status_scada = self.create_status_card(
            bar,
            "SCADA",
            self.scada_status_var
        )

        self.status_plc = self.create_status_card(
            bar,
            "PLC",
            self.plc_status_var
        )

        self.status_modbus = self.create_status_card(
            bar,
            "MODBUS TCP",
            self.modbus_status_var
        )

        self.status_heartbeat = self.create_status_card(
            bar,
            "HEARTBEAT",
            self.heartbeat_var
        )

    def create_status_card(self, parent, title, variable):
        frame = tk.Frame(
            parent,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1
        )

        frame.pack(
            side="left",
            fill="x",
            expand=True,
            padx=4
        )

        tk.Label(
            frame,
            text=title,
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8, "bold")
        ).pack(pady=(9, 2))

        value = tk.Label(
            frame,
            textvariable=variable,
            bg=PANEL,
            fg=RED,
            font=("Segoe UI", 10, "bold")
        )
        value.pack(pady=(0, 9))

        frame.value_label = value

        return frame

    # ========================================================
    # PROCESS SECTION
    # ========================================================

    def create_process_section(self, parent):
        section_title = tk.Label(
            parent,
            text="PROCESS OVERVIEW",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 10, "bold")
        )
        section_title.pack(anchor="w", pady=(2, 6))

        frame = tk.Frame(parent, bg=BG)
        frame.pack(fill="x")

        self.motor_card = self.create_process_card(
            frame,
            "MOTOR",
            self.motor_var,
            GREEN
        )

        self.mode_card = self.create_process_card(
            frame,
            "MODE",
            self.mode_var,
            BLUE
        )

        self.speed_card = self.create_process_card(
            frame,
            "SPEED SETPOINT",
            self.speed_var,
            CYAN,
            "RPM"
        )

        self.rpm_card = self.create_process_card(
            frame,
            "ACTUAL RPM",
            self.actual_rpm_var,
            GREEN,
            "RPM"
        )

        self.current_card = self.create_process_card(
            frame,
            "CURRENT",
            self.current_var,
            ORANGE,
            "A"
        )

        self.pressure_card = self.create_process_card(
            frame,
            "PRESSURE",
            self.pressure_var,
            CYAN,
            "bar"
        )

        self.temperature_card = self.create_process_card(
            frame,
            "TEMPERATURE",
            self.temperature_var,
            YELLOW,
            "°C"
        )

    def create_process_card(
        self,
        parent,
        title,
        variable,
        accent,
        unit=""
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
            padx=4
        )

        tk.Label(
            card,
            text=title,
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8, "bold")
        ).pack(anchor="w", padx=12, pady=(10, 2))

        value_frame = tk.Frame(card, bg=PANEL)
        value_frame.pack(anchor="w", padx=12, pady=(0, 10))

        value_label = tk.Label(
            value_frame,
            textvariable=variable,
            bg=PANEL,
            fg=accent,
            font=("Segoe UI", 18, "bold")
        )
        value_label.pack(side="left")

        if unit:
            tk.Label(
                value_frame,
                text=f" {unit}",
                bg=PANEL,
                fg=MUTED,
                font=("Segoe UI", 9)
            ).pack(side="left", pady=(7, 0))

        card.value_label = value_label

        return card

    # ========================================================
    # CONTROL SECTION
    # ========================================================

    def create_control_section(self, parent):
        section_title = tk.Label(
            parent,
            text="OPERATOR CONTROLS",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 10, "bold")
        )
        section_title.pack(anchor="w", pady=(16, 6))

        frame = tk.Frame(
            parent,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1
        )
        frame.pack(fill="x")

        # Motor
        motor_frame = tk.Frame(frame, bg=PANEL)
        motor_frame.pack(side="left", padx=16, pady=14)

        tk.Label(
            motor_frame,
            text="MOTOR",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold")
        ).pack(anchor="w", pady=(0, 6))

        button_frame = tk.Frame(motor_frame, bg=PANEL)
        button_frame.pack()

        tk.Button(
            button_frame,
            text="START",
            command=lambda: self.send_named_command(
                "MotorCommand",
                1
            ),
            bg=GREEN,
            fg="white",
            activebackground=GREEN,
            activeforeground="white",
            relief="flat",
            padx=18,
            pady=7,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2"
        ).pack(side="left", padx=(0, 5))

        tk.Button(
            button_frame,
            text="STOP",
            command=lambda: self.send_named_command(
                "MotorCommand",
                0
            ),
            bg=RED,
            fg="white",
            activebackground=RED,
            activeforeground="white",
            relief="flat",
            padx=18,
            pady=7,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2"
        ).pack(side="left")

        # Mode
        mode_frame = tk.Frame(frame, bg=PANEL)
        mode_frame.pack(side="left", padx=16, pady=14)

        tk.Label(
            mode_frame,
            text="MODE",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold")
        ).pack(anchor="w", pady=(0, 6))

        self.mode_combo = ttk.Combobox(
            mode_frame,
            values=["MANUAL", "AUTO"],
            state="readonly",
            width=12
        )
        self.mode_combo.set("MANUAL")
        self.mode_combo.pack()
        self.mode_combo.bind(
            "<<ComboboxSelected>>",
            self.mode_changed
        )

        # Speed
        speed_frame = tk.Frame(frame, bg=PANEL)
        speed_frame.pack(side="left", padx=16, pady=14)

        tk.Label(
            speed_frame,
            text="SPEED SETPOINT",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold")
        ).pack(anchor="w", pady=(0, 6))

        speed_input = tk.Frame(speed_frame, bg=PANEL)
        speed_input.pack()

        self.speed_entry = tk.Entry(
            speed_input,
            textvariable=self.speed_command_var,
            bg=PANEL_2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            width=12,
            font=("Segoe UI", 10)
        )
        self.speed_entry.pack(side="left", ipady=6)

        tk.Button(
            speed_input,
            text="SET",
            command=self.set_speed,
            bg=BLUE,
            fg="white",
            activebackground=BLUE,
            activeforeground="white",
            relief="flat",
            padx=12,
            pady=6,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2"
        ).pack(side="left", padx=(5, 0))

        # Pressure
        pressure_frame = tk.Frame(frame, bg=PANEL)
        pressure_frame.pack(side="left", padx=16, pady=14)

        tk.Label(
            pressure_frame,
            text="PRESSURE SETPOINT",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold")
        ).pack(anchor="w", pady=(0, 6))

        pressure_input = tk.Frame(pressure_frame, bg=PANEL)
        pressure_input.pack()

        self.pressure_entry = tk.Entry(
            pressure_input,
            textvariable=self.pressure_command_var,
            bg=PANEL_2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            width=12,
            font=("Segoe UI", 10)
        )
        self.pressure_entry.pack(side="left", ipady=6)

        tk.Button(
            pressure_input,
            text="SET",
            command=self.set_pressure,
            bg=CYAN,
            fg="white",
            activebackground=CYAN,
            activeforeground="white",
            relief="flat",
            padx=12,
            pady=6,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2"
        ).pack(side="left", padx=(5, 0))

    # ========================================================
    # TREND
    # ========================================================

    def create_trend_section(self, parent):
        title = tk.Label(
            parent,
            text="LIVE PROCESS TREND",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 10, "bold")
        )
        title.pack(anchor="w", pady=(16, 6))

        frame = tk.Frame(
            parent,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1
        )
        frame.pack(fill="x")

        self.trend_canvas = tk.Canvas(
            frame,
            bg=PANEL,
            highlightthickness=0,
            height=190
        )
        self.trend_canvas.pack(
            fill="x",
            padx=10,
            pady=10
        )

        self.trend_canvas.bind(
            "<Configure>",
            lambda event: self.draw_trend()
        )

    def draw_trend(self):
        canvas = self.trend_canvas

        width = canvas.winfo_width()
        height = canvas.winfo_height()

        if width <= 20 or height <= 20:
            return

        canvas.delete("all")

        # Grid
        for i in range(5):
            y = 15 + i * (height - 30) / 4

            canvas.create_line(
                10,
                y,
                width - 10,
                y,
                fill=BORDER
            )

        for i in range(7):
            x = 10 + i * (width - 20) / 6

            canvas.create_line(
                x,
                10,
                x,
                height - 10,
                fill=BORDER
            )

        series = [
            ("RPM", list(self.speed_history), BLUE),
            ("Pressure", list(self.pressure_history), CYAN),
            ("Temperature", list(self.temperature_history), ORANGE)
        ]

        all_values = []

        for _, values, _ in series:
            all_values.extend(values)

        if not all_values:
            canvas.create_text(
                width / 2,
                height / 2,
                text="Waiting for process data...",
                fill=MUTED,
                font=("Segoe UI", 10)
            )
            return

        min_value = min(all_values)
        max_value = max(all_values)

        if max_value == min_value:
            max_value += 1

        padding = (max_value - min_value) * 0.1

        min_value -= padding
        max_value += padding

        for name, values, color in series:
            if len(values) < 2:
                continue

            points = []

            for i, value in enumerate(values):
                x = 10 + (
                    i / max(1, TREND_POINTS - 1)
                ) * (width - 20)

                normalized = (
                    value - min_value
                ) / (
                    max_value - min_value
                )

                y = height - 10 - normalized * (
                    height - 20
                )

                points.extend([x, y])

            canvas.create_line(
                points,
                fill=color,
                width=2,
                smooth=True
            )

            canvas.create_text(
                20,
                18 + (
                    ["RPM", "Pressure", "Temperature"].index(name) * 18
                ),
                text=name,
                anchor="w",
                fill=color,
                font=("Segoe UI", 9, "bold")
            )

    # ========================================================
    # REGISTER TABLE
    # ========================================================

    def create_register_section(self, parent):
        title_frame = tk.Frame(parent, bg=BG)
        title_frame.pack(
            fill="x",
            pady=(16, 6)
        )

        tk.Label(
            title_frame,
            text="REGISTER MONITOR",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 10, "bold")
        ).pack(side="left")

        tk.Label(
            title_frame,
            textvariable=self.register_count_var,
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9)
        ).pack(side="right")

        table_frame = tk.Frame(
            parent,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1
        )
        table_frame.pack(
            fill="both",
            expand=True
        )

        columns = (
            "id",
            "name",
            "address",
            "type",
            "access",
            "unit",
            "value",
            "timestamp"
        )

        self.register_tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=9
        )

        headings = {
            "id": "ID",
            "name": "REGISTER",
            "address": "ADDRESS",
            "type": "TYPE",
            "access": "ACCESS",
            "unit": "UNIT",
            "value": "VALUE",
            "timestamp": "TIMESTAMP"
        }

        widths = {
            "id": 50,
            "name": 180,
            "address": 80,
            "type": 100,
            "access": 100,
            "unit": 80,
            "value": 150,
            "timestamp": 210
        }

        for column in columns:
            self.register_tree.heading(
                column,
                text=headings[column]
            )

            self.register_tree.column(
                column,
                width=widths[column],
                anchor="center"
            )

        scrollbar = ttk.Scrollbar(
            table_frame,
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

        self.register_tree.bind(
            "<Double-1>",
            self.register_double_click
        )

    # ========================================================
    # FOOTER
    # ========================================================

    def create_footer(self, parent):
        footer = tk.Frame(
            parent,
            bg=PANEL,
            height=32,
            highlightbackground=BORDER,
            highlightthickness=1
        )
        footer.pack(
            fill="x",
            pady=(10, 0)
        )
        footer.pack_propagate(False)

        tk.Label(
            footer,
            text=f"API: {API_URL}",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8)
        ).pack(
            side="left",
            padx=15
        )

        tk.Label(
            footer,
            textvariable=self.last_update_var,
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 8)
        ).pack(
            side="right",
            padx=15
        )

    # ========================================================
    # DATA REFRESH
    # ========================================================

    def refresh_data(self):
        if not self.running:
            return

        thread = threading.Thread(
            target=self.fetch_data,
            daemon=True
        )
        thread.start()

        self.root.after(
            UPDATE_INTERVAL,
            self.refresh_data
        )

    def fetch_data(self):
        try:
            status = self.api.get("/api/v1/status")
            latest = self.api.get("/api/v1/latest")
            registers = self.api.get("/api/v1/registers")

            self.root.after(
                0,
                lambda: self.update_interface(
                    status,
                    latest,
                    registers
                )
            )

        except Exception as exc:
            self.root.after(
                0,
                lambda: self.handle_connection_error(
                    str(exc)
                )
            )

    # ========================================================
    # INTERFACE UPDATE
    # ========================================================

    def update_interface(
        self,
        status,
        latest,
        registers
    ):
        self.last_update = datetime.now()

        api_status = status.get(
            "api",
            {}
        ).get(
            "status",
            "offline"
        )

        database_status = status.get(
            "database",
            {}
        ).get(
            "status",
            "offline"
        )

        scada_status = status.get(
            "scada",
            {}
        ).get(
            "status",
            "offline"
        )

        self.api_status_var.set(
            str(api_status).upper()
        )

        self.database_status_var.set(
            str(database_status).upper()
        )

        self.scada_status_var.set(
            str(scada_status).upper()
        )

        # PLC and Modbus are considered healthy
        # when SCADA is successfully connected.
        if str(scada_status).lower() == "online":
            self.plc_status_var.set("ONLINE")
            self.modbus_status_var.set("ONLINE")
        else:
            self.plc_status_var.set("OFFLINE")
            self.modbus_status_var.set("OFFLINE")

        self.update_status_colors()

        values = []

        if isinstance(latest, dict):
            values = latest.get(
                "values",
                []
            )

        self.latest_data = {}

        for item in values:
            name = item.get("name")

            if name:
                self.latest_data[name] = item

        self.registers = []

        if isinstance(registers, list):
            self.registers = registers

        elif isinstance(registers, dict):
            self.registers = registers.get(
                "registers",
                registers.get("values", [])
            )

        self.update_process_values()
        self.update_register_table()
        self.update_trends()

        count = len(values)

        self.register_count_var.set(
            f"{count} registers"
        )

        self.last_update_var.set(
            "Last update: " +
            self.last_update.strftime(
                "%H:%M:%S"
            )
        )

        self.first_load = False

    def handle_connection_error(self, error):
        self.api_status_var.set("OFFLINE")
        self.database_status_var.set("UNKNOWN")
        self.scada_status_var.set("OFFLINE")
        self.plc_status_var.set("OFFLINE")
        self.modbus_status_var.set("OFFLINE")

        self.update_status_colors()

        self.last_update_var.set(
            f"Connection error: {error}"
        )

    # ========================================================
    # STATUS COLORS
    # ========================================================

    def update_status_colors(self):
        status_widgets = [
            (
                self.status_api,
                self.api_status_var.get()
            ),
            (
                self.status_database,
                self.database_status_var.get()
            ),
            (
                self.status_scada,
                self.scada_status_var.get()
            ),
            (
                self.status_plc,
                self.plc_status_var.get()
            ),
            (
                self.status_modbus,
                self.modbus_status_var.get()
            ),
            (
                self.status_heartbeat,
                self.heartbeat_var.get()
            )
        ]

        for frame, status in status_widgets:
            label = frame.value_label

            normalized = str(status).lower()

            if normalized in (
                "online",
                "connected"
            ):
                label.configure(
                    fg=GREEN
                )

            elif normalized in (
                "offline",
                "error",
                "unknown"
            ):
                label.configure(
                    fg=RED
                )

            else:
                label.configure(
                    fg=YELLOW
                )

        api_online = (
            self.api_status_var.get().lower()
            == "online"
        )

        self.header_api_status.configure(
            text=(
                "ONLINE"
                if api_online
                else "OFFLINE"
            ),
            bg=(
                GREEN
                if api_online
                else RED
            )
        )

    # ========================================================
    # PROCESS VALUES
    # ========================================================

    def get_value(self, name, default=0):
        item = self.latest_data.get(name)

        if not item:
            return default

        return item.get(
            "value",
            default
        )

    def update_process_values(self):
        motor = self.get_value(
            "MotorCommand",
            0
        )

        mode = self.get_value(
            "Mode",
            0
        )

        speed = self.get_value(
            "SpeedSetpoint",
            0
        )

        rpm = self.get_value(
            "ActualRPM",
            0
        )

        current = self.get_value(
            "Current",
            0
        )

        pressure = self.get_value(
            "ActualPressure",
            0
        )

        temperature = self.get_value(
            "Temperature",
            0
        )

        heartbeat = self.get_value(
            "Heartbeat",
            0
        )

        self.motor_var.set(
            "RUNNING"
            if self.to_number(motor) > 0
            else "STOPPED"
        )

        self.mode_var.set(
            "AUTO"
            if self.to_number(mode) > 0
            else "MANUAL"
        )

        self.speed_var.set(
            self.format_value(speed)
        )

        self.actual_rpm_var.set(
            self.format_value(rpm)
        )

        self.current_var.set(
            self.format_value(current)
        )

        self.pressure_var.set(
            self.format_value(pressure)
        )

        self.temperature_var.set(
            self.format_value(temperature)
        )

        self.heartbeat_var.set(
            str(heartbeat)
        )

        # Do not overwrite operator input while typing.
        if self.root.focus_get() != self.speed_entry:
            self.speed_command_var.set(
                self.format_value(speed)
            )

        pressure_setpoint = self.get_value(
            "PressureSetpoint",
            pressure
        )

        if self.root.focus_get() != self.pressure_entry:
            self.pressure_command_var.set(
                self.format_value(
                    pressure_setpoint
                )
            )

        if self.mode_combo["state"] != "disabled":
            self.mode_combo.set(
                "AUTO"
                if self.to_number(mode) > 0
                else "MANUAL"
            )

    # ========================================================
    # REGISTER TABLE UPDATE
    # ========================================================

    def update_register_table(self):
        for item in self.register_tree.get_children():
            self.register_tree.delete(item)

        for item in self.latest_data.values():
            self.register_tree.insert(
                "",
                "end",
                values=(
                    item.get("register_id", ""),
                    item.get("name", ""),
                    item.get("address", ""),
                    item.get("data_type", ""),
                    item.get("access", ""),
                    item.get("unit", ""),
                    self.format_value(
                        item.get("value")
                    ),
                    self.format_timestamp(
                        item.get("timestamp")
                    )
                ),
                tags=(
                    "writable"
                    if "write" in str(
                        item.get("access", "")
                    ).lower()
                    else "readonly",
                )
            )

        self.register_tree.tag_configure(
            "writable",
            foreground=TEXT
        )

        self.register_tree.tag_configure(
            "readonly",
            foreground=MUTED
        )

    # ========================================================
    # TREND DATA
    # ========================================================

    def update_trends(self):
        speed = self.to_number(
            self.get_value(
                "ActualRPM",
                0
            )
        )

        pressure = self.to_number(
            self.get_value(
                "ActualPressure",
                0
            )
        )

        temperature = self.to_number(
            self.get_value(
                "Temperature",
                0
            )
        )

        self.speed_history.append(speed)
        self.pressure_history.append(pressure)
        self.temperature_history.append(temperature)

        self.draw_trend()

    # ========================================================
    # COMMANDS
    # ========================================================

    def find_register(self, name):
        for item in self.latest_data.values():
            if item.get("name") == name:
                return item

        for item in self.registers:
            if item.get("name") == name:
                return item

        return None

    def send_named_command(self, name, value):
        register = self.find_register(name)

        if not register:
            messagebox.showerror(
                "Register Error",
                f"Register '{name}' was not found."
            )
            return

        self.send_command(
            register.get("register_id"),
            value,
            name
        )

    def send_command(
        self,
        register_id,
        value,
        display_name=None
    ):
        try:
            self.api.post(
                "/api/v1/commands",
                {
                    "register_id": int(
                        register_id
                    ),
                    "value": value
                }
            )

            if display_name:
                self.last_update_var.set(
                    f"Command queued: "
                    f"{display_name} = {value}"
                )

        except Exception as exc:
            messagebox.showerror(
                "Command Error",
                str(exc)
            )

    def mode_changed(self, event=None):
        mode = self.mode_combo.get()

        value = (
            1
            if mode == "AUTO"
            else 0
        )

        self.send_named_command(
            "Mode",
            value
        )

    def set_speed(self):
        try:
            value = float(
                self.speed_command_var.get()
            )

            self.send_named_command(
                "SpeedSetpoint",
                value
            )

        except ValueError:
            messagebox.showerror(
                "Invalid Value",
                "Speed must be a numeric value."
            )

    def set_pressure(self):
        try:
            value = float(
                self.pressure_command_var.get()
            )

            self.send_named_command(
                "PressureSetpoint",
                value
            )

        except ValueError:
            messagebox.showerror(
                "Invalid Value",
                "Pressure must be a numeric value."
            )

    # ========================================================
    # REGISTER DOUBLE CLICK
    # ========================================================

    def register_double_click(self, event):
        selection = self.register_tree.selection()

        if not selection:
            return

        item_id = selection[0]

        values = self.register_tree.item(
            item_id,
            "values"
        )

        if not values:
            return

        register_id = int(values[0])
        name = values[1]
        access = str(values[4]).lower()

        if "write" not in access:
            messagebox.showinfo(
                "Read Only",
                f"{name} is a read-only register."
            )
            return

        self.open_write_dialog(
            register_id,
            name,
            values[3],
            values[6]
        )

    def open_write_dialog(
        self,
        register_id,
        name,
        data_type,
        current_value
    ):
        dialog = tk.Toplevel(self.root)

        dialog.title(
            f"Write Register - {name}"
        )

        dialog.geometry("400x230")
        dialog.resizable(False, False)
        dialog.configure(bg=PANEL)

        tk.Label(
            dialog,
            text="WRITE REGISTER",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9, "bold")
        ).pack(
            pady=(18, 4)
        )

        tk.Label(
            dialog,
            text=name,
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 16, "bold")
        ).pack()

        tk.Label(
            dialog,
            text=f"Type: {data_type}",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9)
        ).pack(
            pady=(4, 12)
        )

        entry = tk.Entry(
            dialog,
            bg=PANEL_2,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            font=("Segoe UI", 11),
            width=25
        )
        entry.pack(
            ipady=7
        )

        entry.insert(
            0,
            str(current_value)
        )

        def submit():
            raw = entry.get().strip()

            try:
                if data_type == "UINT16":
                    value = int(raw)

                elif data_type == "FLOAT32":
                    value = float(raw)

                else:
                    value = raw

            except ValueError:
                messagebox.showerror(
                    "Invalid Value",
                    "Please enter a valid value.",
                    parent=dialog
                )
                return

            self.send_command(
                register_id,
                value,
                name
            )

            dialog.destroy()

        button_frame = tk.Frame(
            dialog,
            bg=PANEL
        )
        button_frame.pack(
            pady=18
        )

        tk.Button(
            button_frame,
            text="WRITE",
            command=submit,
            bg=GREEN,
            fg="white",
            activebackground=GREEN,
            relief="flat",
            padx=24,
            pady=7,
            font=("Segoe UI", 9, "bold")
        ).pack(
            side="left",
            padx=5
        )

        tk.Button(
            button_frame,
            text="CANCEL",
            command=dialog.destroy,
            bg=BORDER,
            fg=TEXT,
            activebackground=BORDER,
            relief="flat",
            padx=20,
            pady=7,
            font=("Segoe UI", 9, "bold")
        ).pack(
            side="left",
            padx=5
        )

        entry.focus_set()
        dialog.grab_set()

    # ========================================================
    # HELPERS
    # ========================================================

    @staticmethod
    def to_number(value):
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def format_value(value):
        if value is None:
            return "-"

        if isinstance(value, float):
            if abs(value - round(value)) < 0.0001:
                return str(int(round(value)))

            return f"{value:.2f}"

        return str(value)

    @staticmethod
    def format_timestamp(value):
        if not value:
            return "-"

        try:
            dt = datetime.fromisoformat(
                str(value).replace(
                    "Z",
                    "+00:00"
                )
            )

            return dt.strftime(
                "%H:%M:%S"
            )

        except Exception:
            return str(value)

    # ========================================================
    # CLOSE
    # ========================================================

    def on_close(self):
        self.running = False
        self.root.destroy()


# ============================================================
# APPLICATION ENTRY POINT
# ============================================================

def main():
    root = tk.Tk()

    app = SCADAGUI(root)

    root.mainloop()


if __name__ == "__main__":
    main()

