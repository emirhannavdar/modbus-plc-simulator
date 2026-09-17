import json
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from urllib.request import urlopen
from urllib.error import URLError


# ============================================================
# CONFIGURATION
# ============================================================

API_URL = "http://127.0.0.1:8000"

UPDATE_INTERVAL = 1000
TREND_POINTS = 60


# ============================================================
# COLORS
# ============================================================

BG = "#101820"
PANEL = "#17232d"
PANEL_LIGHT = "#1d2d38"

TEXT = "#f2f5f7"
TEXT_SECONDARY = "#9eabb5"

GREEN = "#20c77a"
RED = "#ef5350"
YELLOW = "#f5c542"
BLUE = "#4da3ff"
CYAN = "#37d5ff"

GRID = "#293943"


# ============================================================
# API CLIENT
# ============================================================

class APIClient:

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def get(self, endpoint: str):

        url = f"{self.base_url}{endpoint}"

        with urlopen(url, timeout=3) as response:
            data = response.read().decode("utf-8")
            return json.loads(data)

    def get_status(self):
        return self.get("/api/v1/status")

    def get_health(self):
        return self.get("/api/v1/health")

    def get_history(self, limit=20):
        return self.get(f"/api/v1/history?limit={limit}")

    def get_latest(self):
        return self.get("/api/v1/latest")


# ============================================================
# MAIN APPLICATION
# ============================================================

class ScadaApplication(tk.Tk):

    def __init__(self):

        super().__init__()

        self.title("ScadaWatt Industrial SCADA")

        self.geometry("1280x820")
        self.minsize(1100, 720)

        self.configure(bg=BG)

        self.api = APIClient(API_URL)

        self.running = True
        self.data_lock = threading.Lock()

        self.latest_data = None

        self.rpm_history = []
        self.pressure_history = []
        self.temperature_history = []

        self.last_update_time = None

        self.protocol(
            "WM_DELETE_WINDOW",
            self.close_application,
        )

        self.create_styles()
        self.create_interface()

        self.after(
            500,
            self.start_update_thread,
        )


    # ========================================================
    # STYLES
    # ========================================================

    def create_styles(self):

        style = ttk.Style()

        style.theme_use("clam")

        style.configure(
            "TFrame",
            background=BG,
        )

        style.configure(
            "Panel.TFrame",
            background=PANEL,
        )

        style.configure(
            "TLabel",
            background=BG,
            foreground=TEXT,
            font=("Segoe UI", 10),
        )

        style.configure(
            "Title.TLabel",
            background=BG,
            foreground=TEXT,
            font=("Segoe UI", 20, "bold"),
        )

        style.configure(
            "Subtitle.TLabel",
            background=BG,
            foreground=TEXT_SECONDARY,
            font=("Segoe UI", 9),
        )

        style.configure(
            "PanelTitle.TLabel",
            background=PANEL,
            foreground=TEXT_SECONDARY,
            font=("Segoe UI", 9, "bold"),
        )

        style.configure(
            "Value.TLabel",
            background=PANEL,
            foreground=TEXT,
            font=("Segoe UI", 18, "bold"),
        )

        style.configure(
            "SmallValue.TLabel",
            background=PANEL,
            foreground=TEXT,
            font=("Segoe UI", 11, "bold"),
        )

        style.configure(
            "Status.TLabel",
            background=PANEL,
            foreground=GREEN,
            font=("Segoe UI", 11, "bold"),
        )

        style.configure(
            "Header.TFrame",
            background=BG,
        )


    # ========================================================
    # INTERFACE
    # ========================================================

    def create_interface(self):

        # ----------------------------------------------------
        # HEADER
        # ----------------------------------------------------

        header = ttk.Frame(
            self,
            style="Header.TFrame",
        )

        header.pack(
            fill="x",
            padx=24,
            pady=(20, 10),
        )

        title_frame = ttk.Frame(
            header,
            style="Header.TFrame",
        )

        title_frame.pack(side="left")

        ttk.Label(
            title_frame,
            text="SCADAWATT",
            style="Title.TLabel",
        ).pack(anchor="w")

        ttk.Label(
            title_frame,
            text="Industrial SCADA Monitoring System",
            style="Subtitle.TLabel",
        ).pack(anchor="w")

        self.system_status = tk.Label(
            header,
            text="● SYSTEM OFFLINE",
            bg=BG,
            fg=RED,
            font=("Segoe UI", 11, "bold"),
        )

        self.system_status.pack(
            side="right",
            pady=8,
        )

        # ----------------------------------------------------
        # STATUS BAR
        # ----------------------------------------------------

        status_frame = tk.Frame(
            self,
            bg=BG,
        )

        status_frame.pack(
            fill="x",
            padx=24,
            pady=5,
        )

        self.plc_status = self.create_status_card(
            status_frame,
            "PLC STATUS",
            "OFFLINE",
        )

        self.modbus_status = self.create_status_card(
            status_frame,
            "MODBUS TCP",
            "DISCONNECTED",
        )

        self.database_status = self.create_status_card(
            status_frame,
            "DATABASE",
            "DISCONNECTED",
        )

        self.heartbeat_value = self.create_status_card(
            status_frame,
            "HEARTBEAT",
            "--",
        )

        # ----------------------------------------------------
        # PROCESS VALUES
        # ----------------------------------------------------

        ttk.Label(
            self,
            text="PROCESS VALUES",
            font=("Segoe UI", 10, "bold"),
            foreground=TEXT_SECONDARY,
            background=BG,
        ).pack(
            anchor="w",
            padx=24,
            pady=(18, 6),
        )

        values_frame = tk.Frame(
            self,
            bg=BG,
        )

        values_frame.pack(
            fill="x",
            padx=24,
        )

        self.motor_value = self.create_value_card(
            values_frame,
            "MOTOR",
            "OFF",
            "state",
        )

        self.mode_value = self.create_value_card(
            values_frame,
            "MODE",
            "MANUAL",
            "state",
        )

        self.speed_value = self.create_value_card(
            values_frame,
            "SPEED SETPOINT",
            "0.00 RPM",
            "value",
        )

        self.rpm_value = self.create_value_card(
            values_frame,
            "ACTUAL RPM",
            "0.00 RPM",
            "value",
        )

        self.current_value = self.create_value_card(
            values_frame,
            "CURRENT",
            "0.00 A",
            "value",
        )

        self.pressure_sp_value = self.create_value_card(
            values_frame,
            "PRESSURE SETPOINT",
            "0.00",
            "value",
        )

        self.pressure_value = self.create_value_card(
            values_frame,
            "ACTUAL PRESSURE",
            "0.00",
            "value",
        )

        self.temperature_value = self.create_value_card(
            values_frame,
            "TEMPERATURE",
            "0.00 °C",
            "value",
        )

        # ----------------------------------------------------
        # TREND
        # ----------------------------------------------------

        ttk.Label(
            self,
            text="LIVE PROCESS TREND",
            font=("Segoe UI", 10, "bold"),
            foreground=TEXT_SECONDARY,
            background=BG,
        ).pack(
            anchor="w",
            padx=24,
            pady=(18, 6),
        )

        chart_frame = tk.Frame(
            self,
            bg=PANEL,
            highlightbackground=GRID,
            highlightthickness=1,
        )

        chart_frame.pack(
            fill="both",
            expand=True,
            padx=24,
        )

        self.chart = tk.Canvas(
            chart_frame,
            bg=PANEL,
            highlightthickness=0,
        )

        self.chart.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=10,
        )

        # ----------------------------------------------------
        # FOOTER
        # ----------------------------------------------------

        footer = tk.Frame(
            self,
            bg=BG,
        )

        footer.pack(
            fill="x",
            padx=24,
            pady=15,
        )

        self.connection_label = tk.Label(
            footer,
            text="API: http://127.0.0.1:8000",
            bg=BG,
            fg=TEXT_SECONDARY,
            font=("Segoe UI", 9),
        )

        self.connection_label.pack(
            side="left",
        )

        self.update_label = tk.Label(
            footer,
            text="Last update: --",
            bg=BG,
            fg=TEXT_SECONDARY,
            font=("Segoe UI", 9),
        )

        self.update_label.pack(
            side="left",
            padx=30,
        )

        refresh_button = tk.Button(
            footer,
            text="REFRESH",
            command=self.manual_refresh,
            bg=PANEL_LIGHT,
            fg=TEXT,
            activebackground=BLUE,
            activeforeground="white",
            relief="flat",
            padx=18,
            pady=7,
            font=("Segoe UI", 9, "bold"),
            cursor="hand2",
        )

        refresh_button.pack(
            side="right",
        )


    # ========================================================
    # STATUS CARD
    # ========================================================

    def create_status_card(
        self,
        parent,
        title,
        value,
    ):

        frame = tk.Frame(
            parent,
            bg=PANEL,
            highlightbackground=GRID,
            highlightthickness=1,
        )

        frame.pack(
            side="left",
            fill="both",
            expand=True,
            padx=4,
        )

        tk.Label(
            frame,
            text=title,
            bg=PANEL,
            fg=TEXT_SECONDARY,
            font=("Segoe UI", 8, "bold"),
        ).pack(
            anchor="w",
            padx=12,
            pady=(9, 2),
        )

        label = tk.Label(
            frame,
            text=value,
            bg=PANEL,
            fg=RED,
            font=("Segoe UI", 12, "bold"),
        )

        label.pack(
            anchor="w",
            padx=12,
            pady=(0, 9),
        )

        return label


    # ========================================================
    # VALUE CARD
    # ========================================================

    def create_value_card(
        self,
        parent,
        title,
        value,
        value_type,
    ):

        frame = tk.Frame(
            parent,
            bg=PANEL,
            highlightbackground=GRID,
            highlightthickness=1,
        )

        frame.pack(
            side="left",
            fill="both",
            expand=True,
            padx=4,
            pady=4,
        )

        tk.Label(
            frame,
            text=title,
            bg=PANEL,
            fg=TEXT_SECONDARY,
            font=("Segoe UI", 8, "bold"),
        ).pack(
            anchor="w",
            padx=12,
            pady=(10, 3),
        )

        label = tk.Label(
            frame,
            text=value,
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 16, "bold"),
        )

        label.pack(
            anchor="w",
            padx=12,
            pady=(0, 10),
        )

        return label


    # ========================================================
    # UPDATE THREAD
    # ========================================================

    def start_update_thread(self):

        thread = threading.Thread(
            target=self.data_worker,
            daemon=True,
        )

        thread.start()


    def data_worker(self):

        while self.running:

            try:

                status = self.api.get_status()

                health = self.api.get_health()

                with self.data_lock:

                    self.latest_data = {
                        "status": status,
                        "health": health,
                    }

                self.after(
                    0,
                    self.update_interface,
                )

            except Exception as exc:

                self.after(
                    0,
                    lambda error=str(exc): self.show_connection_error(error),
                )

            time.sleep(
                UPDATE_INTERVAL / 1000
            )


    # ========================================================
    # UPDATE INTERFACE
    # ========================================================

    def update_interface(self):

        if not self.latest_data:
            return

        status = self.latest_data["status"]
        health = self.latest_data["health"]

        data = status.get(
            "data",
            {},
        )

        services = health.get(
            "services",
            {},
        )

        modbus = services.get(
            "modbus",
            {},
        )

        database = services.get(
            "database",
            {},
        )

        # ----------------------------------------------------
        # SYSTEM STATUS
        # ----------------------------------------------------

        overall_status = health.get(
            "status",
            "unhealthy",
        )

        if overall_status == "healthy":

            self.system_status.config(
                text="● SYSTEM ONLINE",
                fg=GREEN,
            )

        elif overall_status == "degraded":

            self.system_status.config(
                text="● SYSTEM DEGRADED",
                fg=YELLOW,
            )

        else:

            self.system_status.config(
                text="● SYSTEM OFFLINE",
                fg=RED,
            )

        # ----------------------------------------------------
        # PLC
        # ----------------------------------------------------

        if modbus.get("status") == "connected":

            self.plc_status.config(
                text="ONLINE",
                fg=GREEN,
            )

            self.modbus_status.config(
                text="CONNECTED",
                fg=GREEN,
            )

        else:

            self.plc_status.config(
                text="OFFLINE",
                fg=RED,
            )

            self.modbus_status.config(
                text="DISCONNECTED",
                fg=RED,
            )

        # ----------------------------------------------------
        # DATABASE
        # ----------------------------------------------------

        if database.get("status") == "connected":

            self.database_status.config(
                text=f"CONNECTED ({database.get('record_count', 0)})",
                fg=GREEN,
            )

        else:

            self.database_status.config(
                text="DISCONNECTED",
                fg=RED,
            )

        # ----------------------------------------------------
        # HEARTBEAT
        # ----------------------------------------------------

        heartbeat = data.get(
            "Heartbeat",
            modbus.get("heartbeat"),
        )

        if heartbeat is not None:

            self.heartbeat_value.config(
                text=str(heartbeat),
                fg=GREEN,
            )

        # ----------------------------------------------------
        # PROCESS VALUES
        # ----------------------------------------------------

        motor = data.get(
            "MotorCommand",
            0,
        )

        mode = data.get(
            "Mode",
            0,
        )

        speed = data.get(
            "SpeedSetpoint",
            0,
        )

        rpm = data.get(
            "ActualRPM",
            0,
        )

        current = data.get(
            "Current",
            0,
        )

        pressure_sp = data.get(
            "PressureSetpoint",
            0,
        )

        pressure = data.get(
            "ActualPressure",
            0,
        )

        temperature = data.get(
            "Temperature",
            0,
        )

        # ----------------------------------------------------
        # MOTOR
        # ----------------------------------------------------

        if motor == 1:

            self.motor_value.config(
                text="RUNNING",
                fg=GREEN,
            )

        else:

            self.motor_value.config(
                text="STOPPED",
                fg=RED,
            )

        # ----------------------------------------------------
        # MODE
        # ----------------------------------------------------

        if mode == 1:

            self.mode_value.config(
                text="AUTO",
                fg=CYAN,
            )

        else:

            self.mode_value.config(
                text="MANUAL",
                fg=YELLOW,
            )

        # ----------------------------------------------------
        # VALUES
        # ----------------------------------------------------

        self.speed_value.config(
            text=f"{speed:,.2f} RPM",
        )

        self.rpm_value.config(
            text=f"{rpm:,.2f} RPM",
        )

        self.current_value.config(
            text=f"{current:.2f} A",
        )

        self.pressure_sp_value.config(
            text=f"{pressure_sp:.2f}",
        )

        self.pressure_value.config(
            text=f"{pressure:.2f}",
        )

        self.temperature_value.config(
            text=f"{temperature:.2f} °C",
        )

        # ----------------------------------------------------
        # TREND
        # ----------------------------------------------------

        self.add_trend_point(
            rpm,
            pressure,
            temperature,
        )

        self.draw_chart()

        # ----------------------------------------------------
        # TIME
        # ----------------------------------------------------

        current_time = time.strftime(
            "%H:%M:%S",
        )

        self.update_label.config(
            text=f"Last update: {current_time}",
        )

        self.last_update_time = current_time


    # ========================================================
    # TREND DATA
    # ========================================================

    def add_trend_point(
        self,
        rpm,
        pressure,
        temperature,
    ):

        self.rpm_history.append(
            float(rpm),
        )

        self.pressure_history.append(
            float(pressure),
        )

        self.temperature_history.append(
            float(temperature),
        )

        self.rpm_history = self.rpm_history[-TREND_POINTS:]
        self.pressure_history = self.pressure_history[-TREND_POINTS:]
        self.temperature_history = self.temperature_history[-TREND_POINTS:]


    # ========================================================
    # DRAW CHART
    # ========================================================

    def draw_chart(self):

        self.chart.delete("all")

        width = self.chart.winfo_width()
        height = self.chart.winfo_height()

        if width < 100 or height < 100:
            return

        margin_left = 60
        margin_right = 20
        margin_top = 25
        margin_bottom = 35

        chart_width = (
            width
            - margin_left
            - margin_right
        )

        chart_height = (
            height
            - margin_top
            - margin_bottom
        )

        # ----------------------------------------------------
        # GRID
        # ----------------------------------------------------

        for i in range(6):

            y = (
                margin_top
                + chart_height * i / 5
            )

            self.chart.create_line(
                margin_left,
                y,
                width - margin_right,
                y,
                fill=GRID,
            )

        for i in range(7):

            x = (
                margin_left
                + chart_width * i / 6
            )

            self.chart.create_line(
                x,
                margin_top,
                x,
                height - margin_bottom,
                fill=GRID,
            )

        # ----------------------------------------------------
        # LABELS
        # ----------------------------------------------------

        self.chart.create_text(
            20,
            margin_top,
            text="RPM",
            fill=BLUE,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )

        self.chart.create_text(
            width - 20,
            15,
            text="LIVE",
            fill=GREEN,
            font=("Segoe UI", 9, "bold"),
            anchor="e",
        )

        # ----------------------------------------------------
        # DRAW SERIES
        # ----------------------------------------------------

        if len(self.rpm_history) < 2:
            return

        self.draw_series(
            self.rpm_history,
            margin_left,
            margin_top,
            chart_width,
            chart_height,
            BLUE,
            "RPM",
        )

        self.draw_series(
            self.pressure_history,
            margin_left,
            margin_top,
            chart_width,
            chart_height,
            CYAN,
            "PRESSURE",
        )

        self.draw_series(
            self.temperature_history,
            margin_left,
            margin_top,
            chart_width,
            chart_height,
            YELLOW,
            "TEMP",
        )

        # ----------------------------------------------------
        # LEGEND
        # ----------------------------------------------------

        legend_x = margin_left + 10

        self.chart.create_text(
            legend_x,
            height - 15,
            text="● RPM",
            fill=BLUE,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )

        self.chart.create_text(
            legend_x + 80,
            height - 15,
            text="● PRESSURE",
            fill=CYAN,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )

        self.chart.create_text(
            legend_x + 190,
            height - 15,
            text="● TEMPERATURE",
            fill=YELLOW,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )


    # ========================================================
    # DRAW SERIES
    # ========================================================

    def draw_series(
        self,
        values,
        x,
        y,
        width,
        height,
        color,
        name,
    ):

        if len(values) < 2:
            return

        minimum = min(values)
        maximum = max(values)

        if maximum == minimum:
            maximum += 1
            minimum -= 1

        points = []

        for index, value in enumerate(values):

            px = (
                x
                + width
                * index
                / (TREND_POINTS - 1)
            )

            normalized = (
                (value - minimum)
                / (maximum - minimum)
            )

            py = (
                y
                + height
                * (1 - normalized)
            )

            points.extend(
                [px, py]
            )

        self.chart.create_line(
            *points,
            fill=color,
            width=2,
            smooth=True,
        )


    # ========================================================
    # MANUAL REFRESH
    # ========================================================

    def manual_refresh(self):

        try:

            status = self.api.get_status()
            health = self.api.get_health()

            self.latest_data = {
                "status": status,
                "health": health,
            }

            self.update_interface()

        except Exception as exc:

            messagebox.showerror(
                "SCADA Connection Error",
                str(exc),
            )


    # ========================================================
    # CONNECTION ERROR
    # ========================================================

    def show_connection_error(
        self,
        error,
    ):

        self.system_status.config(
            text="● SYSTEM OFFLINE",
            fg=RED,
        )

        self.plc_status.config(
            text="OFFLINE",
            fg=RED,
        )

        self.modbus_status.config(
            text="DISCONNECTED",
            fg=RED,
        )

        self.database_status.config(
            text="UNKNOWN",
            fg=YELLOW,
        )

        self.heartbeat_value.config(
            text="--",
            fg=RED,
        )


    # ========================================================
    # CLOSE
    # ========================================================

    def close_application(self):

        self.running = False

        self.destroy()


# ============================================================
# APPLICATION START
# ============================================================

if __name__ == "__main__":

    application = ScadaApplication()

    application.mainloop()