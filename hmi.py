"""
Professional Modbus TCP PLC HMI
================================

Compatible with:
    PyModbus 3.x
    Python 3.x

Connects to:
    127.0.0.1:5020
    Unit ID: 1

This HMI does NOT modify server.py.
It communicates with the PLC simulator only through Modbus TCP.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from pymodbus.client import ModbusTcpClient


# ============================================================
# CONFIGURATION
# ============================================================

PLC_IP = "127.0.0.1"
PLC_PORT = 5020
UNIT_ID = 1

REFRESH_MS = 500


# ============================================================
# REGISTER MAP
# ============================================================

MOTOR_COMMAND = 0
MODE = 1

SPEED_SETPOINT_HI = 2
SPEED_SETPOINT_LO = 3

ACTUAL_RPM_HI = 4
ACTUAL_RPM_LO = 5

CURRENT_HI = 6
CURRENT_LO = 7

PRESSURE_SETPOINT_HI = 8
PRESSURE_SETPOINT_LO = 9

ACTUAL_PRESSURE_HI = 10
ACTUAL_PRESSURE_LO = 11

TEMPERATURE_HI = 12
TEMPERATURE_LO = 13

ALARM = 14
EMERGENCY_STOP = 15

HEARTBEAT = 16
STATUS_WORD = 17
FAULT_CODE = 18

RUNTIME_SECONDS_HI = 19
RUNTIME_SECONDS_LO = 20

REGISTER_COUNT = 21


# ============================================================
# FAULT NAMES
# ============================================================

FAULT_NAMES = {
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
# REGISTER NAMES
# ============================================================

REGISTER_NAMES = {
    0: "MOTOR_COMMAND",
    1: "MODE",
    2: "SPEED_SETPOINT_HI",
    3: "SPEED_SETPOINT_LO",
    4: "ACTUAL_RPM_HI",
    5: "ACTUAL_RPM_LO",
    6: "CURRENT_HI",
    7: "CURRENT_LO",
    8: "PRESSURE_SETPOINT_HI",
    9: "PRESSURE_SETPOINT_LO",
    10: "ACTUAL_PRESSURE_HI",
    11: "ACTUAL_PRESSURE_LO",
    12: "TEMPERATURE_HI",
    13: "TEMPERATURE_LO",
    14: "ALARM",
    15: "EMERGENCY_STOP",
    16: "HEARTBEAT",
    17: "STATUS_WORD",
    18: "FAULT_CODE",
    19: "RUNTIME_SECONDS_HI",
    20: "RUNTIME_SECONDS_LO",
}


# ============================================================
# STATUS WORD
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
# HELPERS
# ============================================================

def uint32_from_registers(
    high: int,
    low: int,
) -> int:

    return (
        (high << 16)
        | low
    )


def safe_register(
    registers: list[int],
    index: int,
) -> int:

    if 0 <= index < len(registers):
        return registers[index]

    return 0


# ============================================================
# HMI
# ============================================================

class PLC_HMI:

    def __init__(
        self,
        root: tk.Tk,
    ) -> None:

        self.root = root

        self.root.title(
            "Professional Modbus PLC - HMI"
        )

        self.root.geometry(
            "1100x850"
        )

        self.root.minsize(
            850,
            650
        )

        self.root.configure(
            bg="#0d1117"
        )

        self.client = ModbusTcpClient(
            PLC_IP,
            port=PLC_PORT,
            timeout=1,
        )

        self.connected = False

        self.create_styles()
        self.create_variables()
        self.create_ui()

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.on_close,
        )

        self.update_connection()

    # ========================================================
    # STYLES
    # ========================================================

    def create_styles(self) -> None:

        style = ttk.Style()

        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "TFrame",
            background="#0d1117",
        )

        style.configure(
            "Card.TFrame",
            background="#161b22",
        )

        style.configure(
            "TLabel",
            background="#0d1117",
            foreground="#e6edf3",
            font=("Segoe UI", 10),
        )

        style.configure(
            "Card.TLabel",
            background="#161b22",
            foreground="#e6edf3",
            font=("Segoe UI", 10),
        )

        style.configure(
            "Title.TLabel",
            background="#0d1117",
            foreground="#ffffff",
            font=("Segoe UI", 21, "bold"),
        )

        style.configure(
            "Subtitle.TLabel",
            background="#0d1117",
            foreground="#8b949e",
            font=("Segoe UI", 10),
        )

        style.configure(
            "CardTitle.TLabel",
            background="#161b22",
            foreground="#8b949e",
            font=("Segoe UI", 9, "bold"),
        )

        style.configure(
            "Value.TLabel",
            background="#161b22",
            foreground="#ffffff",
            font=("Segoe UI", 19, "bold"),
        )

        style.configure(
            "Status.TLabel",
            background="#161b22",
            foreground="#58a6ff",
            font=("Segoe UI", 13, "bold"),
        )

        style.configure(
            "Section.TLabel",
            background="#161b22",
            foreground="#58a6ff",
            font=("Segoe UI", 12, "bold"),
        )

        style.configure(
            "TButton",
            font=("Segoe UI", 10, "bold"),
            padding=8,
        )

        style.configure(
            "Treeview",
            background="#0d1117",
            foreground="#e6edf3",
            fieldbackground="#0d1117",
            rowheight=27,
            font=("Consolas", 9),
        )

        style.configure(
            "Treeview.Heading",
            background="#21262d",
            foreground="#ffffff",
            font=("Segoe UI", 9, "bold"),
        )

        style.map(
            "Treeview",
            background=[
                ("selected", "#1f6feb")
            ],
        )

    # ========================================================
    # VARIABLES
    # ========================================================

    def create_variables(self) -> None:

        self.connection_var = tk.StringVar(
            value="DISCONNECTED"
        )

        self.motor_var = tk.StringVar(
            value="OFF"
        )

        self.mode_var = tk.StringVar(
            value="MANUAL"
        )

        self.rpm_var = tk.StringVar(
            value="0 RPM"
        )

        self.speed_setpoint_var = tk.StringVar(
            value="0 RPM"
        )

        self.current_var = tk.StringVar(
            value="0.0 A"
        )

        self.pressure_var = tk.StringVar(
            value="0.0 bar"
        )

        self.pressure_setpoint_var = tk.StringVar(
            value="0.0 bar"
        )

        self.temperature_var = tk.StringVar(
            value="25.0 °C"
        )

        self.alarm_var = tk.StringVar(
            value="NORMAL"
        )

        self.fault_var = tk.StringVar(
            value="NONE"
        )

        self.heartbeat_var = tk.StringVar(
            value="0"
        )

        self.runtime_var = tk.StringVar(
            value="0 s"
        )

        self.status_word_var = tk.StringVar(
            value="0x0000"
        )

        self.estop_var = tk.StringVar(
            value="RELEASED"
        )

        self.ready_var = tk.StringVar(
            value="NOT READY"
        )

        self.rpm_entry_var = tk.StringVar(
            value="1000"
        )

        self.pressure_entry_var = tk.StringVar(
            value="5.0"
        )

        self.connection_detail_var = tk.StringVar(
            value="127.0.0.1:5020"
        )

        self.register_count_var = tk.StringVar(
            value="21"
        )

        self.status_bit_vars = {}

    # ========================================================
    # SCROLLABLE UI
    # ========================================================

    def create_ui(self) -> None:

        container = tk.Frame(
            self.root,
            bg="#0d1117",
        )

        container.pack(
            fill="both",
            expand=True,
        )

        canvas = tk.Canvas(
            container,
            bg="#0d1117",
            highlightthickness=0,
            bd=0,
        )

        scrollbar = ttk.Scrollbar(
            container,
            orient="vertical",
            command=canvas.yview,
        )

        canvas.configure(
            yscrollcommand=scrollbar.set,
        )

        scrollbar.pack(
            side="right",
            fill="y",
        )

        canvas.pack(
            side="left",
            fill="both",
            expand=True,
        )

        content = tk.Frame(
            canvas,
            bg="#0d1117",
        )

        canvas_window = canvas.create_window(
            (0, 0),
            window=content,
            anchor="nw",
        )

        def update_scroll_region(event=None):

            canvas.configure(
                scrollregion=canvas.bbox("all")
            )

        content.bind(
            "<Configure>",
            update_scroll_region,
        )

        def resize_content(event):

            canvas.itemconfigure(
                canvas_window,
                width=event.width,
            )

        canvas.bind(
            "<Configure>",
            resize_content,
        )

        def on_mousewheel(event):

            canvas.yview_scroll(
                int(-1 * (event.delta / 120)),
                "units",
            )

        canvas.bind_all(
            "<MouseWheel>",
            on_mousewheel,
        )

        self.create_header(content)
        self.create_process_panel(content)
        self.create_status_panel(content)
        self.create_control_panel(content)
        self.create_setpoint_panel(content)
        self.create_status_bits_panel(content)
        self.create_register_panel(content)

    # ========================================================
    # HEADER
    # ========================================================

    def create_header(
        self,
        parent,
    ) -> None:

        header = tk.Frame(
            parent,
            bg="#0d1117",
        )

        header.pack(
            fill="x",
            padx=25,
            pady=(20, 10),
        )

        left = tk.Frame(
            header,
            bg="#0d1117",
        )

        left.pack(
            side="left",
            fill="x",
            expand=True,
        )

        tk.Label(
            left,
            text="PROFESSIONAL MODBUS PLC",
            bg="#0d1117",
            fg="#ffffff",
            font=("Segoe UI", 21, "bold"),
        ).pack(
            anchor="w"
        )

        tk.Label(
            left,
            text="Industrial Motor / Pump Simulator",
            bg="#0d1117",
            fg="#8b949e",
            font=("Segoe UI", 10),
        ).pack(
            anchor="w"
        )

        right = tk.Frame(
            header,
            bg="#0d1117",
        )

        right.pack(
            side="right"
        )

        self.connection_label = tk.Label(
            right,
            textvariable=self.connection_var,
            bg="#0d1117",
            fg="#f85149",
            font=("Segoe UI", 11, "bold"),
        )

        self.connection_label.pack(
            anchor="e"
        )

        tk.Label(
            right,
            textvariable=self.connection_detail_var,
            bg="#0d1117",
            fg="#8b949e",
            font=("Consolas", 9),
        ).pack(
            anchor="e",
            pady=(3, 0),
        )

    # ========================================================
    # PROCESS PANEL
    # ========================================================

    def create_process_panel(
        self,
        parent,
    ) -> None:

        frame = tk.Frame(
            parent,
            bg="#0d1117",
        )

        frame.pack(
            fill="x",
            padx=25,
            pady=8,
        )

        cards = [
            ("MOTOR", self.motor_var),
            ("RPM", self.rpm_var),
            ("CURRENT", self.current_var),
            ("PRESSURE", self.pressure_var),
            ("TEMPERATURE", self.temperature_var),
            ("MODE", self.mode_var),
        ]

        for index, (
            title,
            variable,
        ) in enumerate(cards):

            card = tk.Frame(
                frame,
                bg="#161b22",
                highlightbackground="#30363d",
                highlightthickness=1,
            )

            card.grid(
                row=index // 3,
                column=index % 3,
                padx=5,
                pady=5,
                sticky="nsew",
            )

            frame.grid_columnconfigure(
                index % 3,
                weight=1,
            )

            tk.Label(
                card,
                text=title,
                bg="#161b22",
                fg="#8b949e",
                font=("Segoe UI", 9, "bold"),
            ).pack(
                anchor="w",
                padx=15,
                pady=(12, 2),
            )

            tk.Label(
                card,
                textvariable=variable,
                bg="#161b22",
                fg="#ffffff",
                font=("Segoe UI", 19, "bold"),
            ).pack(
                anchor="w",
                padx=15,
                pady=(2, 14),
            )

    # ========================================================
    # STATUS PANEL
    # ========================================================

    def create_status_panel(
        self,
        parent,
    ) -> None:

        frame = tk.Frame(
            parent,
            bg="#161b22",
            highlightbackground="#30363d",
            highlightthickness=1,
        )

        frame.pack(
            fill="x",
            padx=25,
            pady=8,
        )

        tk.Label(
            frame,
            text="PLC STATUS",
            bg="#161b22",
            fg="#58a6ff",
            font=("Segoe UI", 12, "bold"),
        ).grid(
            row=0,
            column=0,
            columnspan=6,
            sticky="w",
            padx=15,
            pady=(12, 10),
        )

        items = [
            ("ALARM", self.alarm_var),
            ("FAULT", self.fault_var),
            ("E-STOP", self.estop_var),
            ("HEARTBEAT", self.heartbeat_var),
            ("RUNTIME", self.runtime_var),
            ("STATUS WORD", self.status_word_var),
        ]

        for column, (
            title,
            variable,
        ) in enumerate(items):

            tk.Label(
                frame,
                text=title,
                bg="#161b22",
                fg="#8b949e",
                font=("Segoe UI", 8, "bold"),
            ).grid(
                row=1,
                column=column,
                padx=12,
                pady=(5, 2),
            )

            label = tk.Label(
                frame,
                textvariable=variable,
                bg="#161b22",
                fg="#ffffff",
                font=("Segoe UI", 10, "bold"),
            )

            label.grid(
                row=2,
                column=column,
                padx=12,
                pady=(2, 14),
            )

            if title == "ALARM":
                self.alarm_label = label

            if title == "E-STOP":
                self.estop_label = label

            if title == "FAULT":
                self.fault_label = label

    # ========================================================
    # CONTROL PANEL
    # ========================================================

    def create_control_panel(
        self,
        parent,
    ) -> None:

        frame = tk.Frame(
            parent,
            bg="#161b22",
            highlightbackground="#30363d",
            highlightthickness=1,
        )

        frame.pack(
            fill="x",
            padx=25,
            pady=8,
        )

        tk.Label(
            frame,
            text="MOTOR CONTROL",
            bg="#161b22",
            fg="#58a6ff",
            font=("Segoe UI", 12, "bold"),
        ).grid(
            row=0,
            column=0,
            columnspan=4,
            sticky="w",
            padx=15,
            pady=(14, 10),
        )

        self.make_button(
            frame,
            "START",
            self.start_motor,
            "#238636",
            "#2ea043",
        ).grid(
            row=1,
            column=0,
            padx=10,
            pady=8,
            sticky="ew",
        )

        self.make_button(
            frame,
            "STOP",
            self.stop_motor,
            "#da3633",
            "#f85149",
        ).grid(
            row=1,
            column=1,
            padx=10,
            pady=8,
            sticky="ew",
        )

        self.make_button(
            frame,
            "AUTO",
            self.set_auto,
            "#1f6feb",
            "#388bfd",
        ).grid(
            row=1,
            column=2,
            padx=10,
            pady=8,
            sticky="ew",
        )

        self.make_button(
            frame,
            "MANUAL",
            self.set_manual,
            "#8957e5",
            "#a371f7",
        ).grid(
            row=1,
            column=3,
            padx=10,
            pady=8,
            sticky="ew",
        )

        for column in range(4):

            frame.grid_columnconfigure(
                column,
                weight=1,
            )

        self.make_button(
            frame,
            "⚠ EMERGENCY STOP",
            self.emergency_stop,
            "#b62324",
            "#f85149",
        ).grid(
            row=2,
            column=0,
            columnspan=2,
            padx=10,
            pady=(8, 15),
            sticky="ew",
        )

        self.make_button(
            frame,
            "RESET E-STOP",
            self.reset_emergency_stop,
            "#9e6a03",
            "#d29922",
        ).grid(
            row=2,
            column=2,
            columnspan=2,
            padx=10,
            pady=(8, 15),
            sticky="ew",
        )

    # ========================================================
    # SETPOINT PANEL
    # ========================================================

    def create_setpoint_panel(
        self,
        parent,
    ) -> None:

        frame = tk.Frame(
            parent,
            bg="#161b22",
            highlightbackground="#30363d",
            highlightthickness=1,
        )

        frame.pack(
            fill="x",
            padx=25,
            pady=8,
        )

        tk.Label(
            frame,
            text="SETPOINT CONTROL",
            bg="#161b22",
            fg="#58a6ff",
            font=("Segoe UI", 12, "bold"),
        ).grid(
            row=0,
            column=0,
            columnspan=4,
            sticky="w",
            padx=15,
            pady=(14, 10),
        )

        # RPM
        tk.Label(
            frame,
            text="CURRENT RPM SETPOINT",
            bg="#161b22",
            fg="#8b949e",
            font=("Segoe UI", 9, "bold"),
        ).grid(
            row=1,
            column=0,
            padx=15,
            pady=8,
            sticky="w",
        )

        tk.Label(
            frame,
            textvariable=self.speed_setpoint_var,
            bg="#161b22",
            fg="#ffffff",
            font=("Segoe UI", 11, "bold"),
        ).grid(
            row=1,
            column=1,
            padx=10,
            pady=8,
        )

        ttk.Entry(
            frame,
            textvariable=self.rpm_entry_var,
            width=14,
        ).grid(
            row=1,
            column=2,
            padx=10,
            pady=8,
        )

        self.make_button(
            frame,
            "WRITE RPM",
            self.write_rpm,
            "#1f6feb",
            "#388bfd",
        ).grid(
            row=1,
            column=3,
            padx=10,
            pady=8,
        )

        # Pressure
        tk.Label(
            frame,
            text="CURRENT PRESSURE SETPOINT",
            bg="#161b22",
            fg="#8b949e",
            font=("Segoe UI", 9, "bold"),
        ).grid(
            row=2,
            column=0,
            padx=15,
            pady=(8, 15),
            sticky="w",
        )

        tk.Label(
            frame,
            textvariable=self.pressure_setpoint_var,
            bg="#161b22",
            fg="#ffffff",
            font=("Segoe UI", 11, "bold"),
        ).grid(
            row=2,
            column=1,
            padx=10,
            pady=(8, 15),
        )

        ttk.Entry(
            frame,
            textvariable=self.pressure_entry_var,
            width=14,
        ).grid(
            row=2,
            column=2,
            padx=10,
            pady=(8, 15),
        )

        self.make_button(
            frame,
            "WRITE PRESSURE",
            self.write_pressure,
            "#1f6feb",
            "#388bfd",
        ).grid(
            row=2,
            column=3,
            padx=10,
            pady=(8, 15),
        )

    # ========================================================
    # STATUS BITS
    # ========================================================

    def create_status_bits_panel(
        self,
        parent,
    ) -> None:

        frame = tk.Frame(
            parent,
            bg="#161b22",
            highlightbackground="#30363d",
            highlightthickness=1,
        )

        frame.pack(
            fill="x",
            padx=25,
            pady=8,
        )

        tk.Label(
            frame,
            text="STATUS WORD / PLC FLAGS",
            bg="#161b22",
            fg="#58a6ff",
            font=("Segoe UI", 12, "bold"),
        ).grid(
            row=0,
            column=0,
            columnspan=4,
            sticky="w",
            padx=15,
            pady=(14, 10),
        )

        for index, (
            bit,
            name,
        ) in enumerate(STATUS_BITS):

            variable = tk.StringVar(
                value="OFF"
            )

            self.status_bit_vars[bit] = variable

            row = 1 + index // 4
            column = index % 4

            card = tk.Frame(
                frame,
                bg="#0d1117",
            )

            card.grid(
                row=row,
                column=column,
                padx=6,
                pady=6,
                sticky="ew",
            )

            frame.grid_columnconfigure(
                column,
                weight=1,
            )

            tk.Label(
                card,
                text=f"BIT {bit}",
                bg="#0d1117",
                fg="#8b949e",
                font=("Consolas", 8),
            ).pack(
                anchor="w",
                padx=10,
                pady=(6, 0),
            )

            tk.Label(
                card,
                text=name,
                bg="#0d1117",
                fg="#ffffff",
                font=("Segoe UI", 9, "bold"),
            ).pack(
                anchor="w",
                padx=10,
            )

            label = tk.Label(
                card,
                textvariable=variable,
                bg="#0d1117",
                fg="#f85149",
                font=("Segoe UI", 9, "bold"),
            )

            label.pack(
                anchor="w",
                padx=10,
                pady=(0, 6),
            )

            variable._label = label

    # ========================================================
    # REGISTER MONITOR
    # ========================================================

    def create_register_panel(
        self,
        parent,
    ) -> None:

        frame = tk.Frame(
            parent,
            bg="#161b22",
            highlightbackground="#30363d",
            highlightthickness=1,
        )

        frame.pack(
            fill="both",
            padx=25,
            pady=(8, 25),
        )

        tk.Label(
            frame,
            text="MODBUS REGISTER MONITOR",
            bg="#161b22",
            fg="#58a6ff",
            font=("Segoe UI", 12, "bold"),
        ).pack(
            anchor="w",
            padx=15,
            pady=(14, 10),
        )

        tree_frame = tk.Frame(
            frame,
            bg="#161b22",
        )

        tree_frame.pack(
            fill="both",
            expand=True,
            padx=12,
            pady=(0, 12),
        )

        columns = (
            "address",
            "name",
            "value",
            "hex",
        )

        self.register_tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            height=21,
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
            width=80,
            anchor="center",
        )

        self.register_tree.column(
            "name",
            width=250,
            anchor="w",
        )

        self.register_tree.column(
            "value",
            width=150,
            anchor="center",
        )

        self.register_tree.column(
            "hex",
            width=150,
            anchor="center",
        )

        tree_scroll = ttk.Scrollbar(
            tree_frame,
            orient="vertical",
            command=self.register_tree.yview,
        )

        self.register_tree.configure(
            yscrollcommand=tree_scroll.set,
        )

        self.register_tree.pack(
            side="left",
            fill="both",
            expand=True,
        )

        tree_scroll.pack(
            side="right",
            fill="y",
        )

        for address in range(
            REGISTER_COUNT
        ):

            self.register_tree.insert(
                "",
                "end",
                iid=str(address),
                values=(
                    address,
                    REGISTER_NAMES.get(
                        address,
                        "UNKNOWN",
                    ),
                    0,
                    "0x0000",
                ),
            )

    # ========================================================
    # BUTTON FACTORY
    # ========================================================

    def make_button(
        self,
        parent,
        text,
        command,
        background,
        active_background,
    ):

        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=background,
            fg="white",
            activebackground=active_background,
            activeforeground="white",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            bd=0,
            padx=18,
            pady=10,
            cursor="hand2",
        )

    # ========================================================
    # MODBUS CONNECTION
    # ========================================================

    def connect(self) -> bool:

        try:

            if self.client.is_socket_open():

                self.connected = True

                return True

            result = self.client.connect()

            self.connected = bool(result)

            return self.connected

        except Exception:

            self.connected = False

            return False

    # ========================================================
    # READ REGISTERS
    # ========================================================

    def read_registers(
        self,
    ):

        if not self.connect():

            return None

        try:

            result = self.client.read_holding_registers(
                address=0,
                count=REGISTER_COUNT,
                device_id=UNIT_ID,
            )

            if result.isError():

                self.connected = False

                return None

            if len(result.registers) < REGISTER_COUNT:

                self.connected = False

                return None

            return result.registers

        except Exception:

            self.connected = False

            return None

    # ========================================================
    # CONNECTION UPDATE
    # ========================================================

    def update_connection(self):

        registers = self.read_registers()

        if registers is None:

            self.connection_var.set(
                "● DISCONNECTED"
            )

            self.connection_label.configure(
                fg="#f85149"
            )

            self.root.after(
                REFRESH_MS,
                self.update_connection,
            )

            return

        self.connection_var.set(
            "● CONNECTED"
        )

        self.connection_label.configure(
            fg="#3fb950"
        )

        self.update_values(
            registers
        )

        self.root.after(
            REFRESH_MS,
            self.update_connection,
        )

    # ========================================================
    # UPDATE VALUES
    # ========================================================

    def update_values(
        self,
        registers: list[int],
    ) -> None:

        motor_command = safe_register(
            registers,
            MOTOR_COMMAND,
        )

        mode = safe_register(
            registers,
            MODE,
        )

        speed_setpoint = uint32_from_registers(
            safe_register(
                registers,
                SPEED_SETPOINT_HI,
            ),
            safe_register(
                registers,
                SPEED_SETPOINT_LO,
            ),
        )

        actual_rpm = uint32_from_registers(
            safe_register(
                registers,
                ACTUAL_RPM_HI,
            ),
            safe_register(
                registers,
                ACTUAL_RPM_LO,
            ),
        )

        current_raw = uint32_from_registers(
            safe_register(
                registers,
                CURRENT_HI,
            ),
            safe_register(
                registers,
                CURRENT_LO,
            ),
        )

        pressure_setpoint_raw = uint32_from_registers(
            safe_register(
                registers,
                PRESSURE_SETPOINT_HI,
            ),
            safe_register(
                registers,
                PRESSURE_SETPOINT_LO,
            ),
        )

        actual_pressure_raw = uint32_from_registers(
            safe_register(
                registers,
                ACTUAL_PRESSURE_HI,
            ),
            safe_register(
                registers,
                ACTUAL_PRESSURE_LO,
            ),
        )

        temperature_raw = uint32_from_registers(
            safe_register(
                registers,
                TEMPERATURE_HI,
            ),
            safe_register(
                registers,
                TEMPERATURE_LO,
            ),
        )

        alarm = safe_register(
            registers,
            ALARM,
        )

        emergency_stop = safe_register(
            registers,
            EMERGENCY_STOP,
        )

        heartbeat = safe_register(
            registers,
            HEARTBEAT,
        )

        status_word = safe_register(
            registers,
            STATUS_WORD,
        )

        fault_code = safe_register(
            registers,
            FAULT_CODE,
        )

        runtime = uint32_from_registers(
            safe_register(
                registers,
                RUNTIME_SECONDS_HI,
            ),
            safe_register(
                registers,
                RUNTIME_SECONDS_LO,
            ),
        )

        current = (
            current_raw / 10.0
        )

        pressure_setpoint = (
            pressure_setpoint_raw / 10.0
        )

        actual_pressure = (
            actual_pressure_raw / 10.0
        )

        temperature = (
            temperature_raw / 10.0
        )

        # ----------------------------------------------------
        # MAIN VALUES
        # ----------------------------------------------------

        self.motor_var.set(
            "ON"
            if motor_command == 1
            else "OFF"
        )

        self.mode_var.set(
            "AUTO"
            if mode == 1
            else "MANUAL"
        )

        self.speed_setpoint_var.set(
            f"{speed_setpoint} RPM"
        )

        self.rpm_var.set(
            f"{actual_rpm} RPM"
        )

        self.current_var.set(
            f"{current:.1f} A"
        )

        self.pressure_setpoint_var.set(
            f"{pressure_setpoint:.1f} bar"
        )

        self.pressure_var.set(
            f"{actual_pressure:.1f} bar"
        )

        self.temperature_var.set(
            f"{temperature:.1f} °C"
        )

        # ----------------------------------------------------
        # ALARM
        # ----------------------------------------------------

        if alarm:

            self.alarm_var.set(
                "ALARM ACTIVE"
            )

            self.alarm_label.configure(
                fg="#f85149"
            )

        else:

            self.alarm_var.set(
                "NORMAL"
            )

            self.alarm_label.configure(
                fg="#3fb950"
            )

        # ----------------------------------------------------
        # E-STOP
        # ----------------------------------------------------

        if emergency_stop:

            self.estop_var.set(
                "ACTIVE"
            )

            self.estop_label.configure(
                fg="#f85149"
            )

        else:

            self.estop_var.set(
                "RELEASED"
            )

            self.estop_label.configure(
                fg="#3fb950"
            )

        # ----------------------------------------------------
        # FAULT
        # ----------------------------------------------------

        self.fault_var.set(
            FAULT_NAMES.get(
                fault_code,
                f"UNKNOWN ({fault_code})",
            )
        )

        if fault_code != 0:

            self.fault_label.configure(
                fg="#f85149"
            )

        else:

            self.fault_label.configure(
                fg="#3fb950"
            )

        # ----------------------------------------------------
        # OTHER STATUS
        # ----------------------------------------------------

        self.heartbeat_var.set(
            str(heartbeat)
        )

        self.runtime_var.set(
            f"{runtime} s"
        )

        self.status_word_var.set(
            f"0x{status_word:04X}"
        )

        # ----------------------------------------------------
        # STATUS BITS
        # ----------------------------------------------------

        for bit, name in STATUS_BITS:

            active = bool(
                status_word
                & (1 << bit)
            )

            variable = self.status_bit_vars[
                bit
            ]

            variable.set(
                "ON"
                if active
                else "OFF"
            )

            label = getattr(
                variable,
                "_label",
                None,
            )

            if label is not None:

                label.configure(
                    fg=(
                        "#3fb950"
                        if active
                        else "#f85149"
                    )
                )

        # ----------------------------------------------------
        # REGISTER TABLE
        # ----------------------------------------------------

        self.update_register_table(
            registers
        )

    # ========================================================
    # REGISTER TABLE
    # ========================================================

    def update_register_table(
        self,
        registers: list[int],
    ) -> None:

        for address in range(
            min(
                REGISTER_COUNT,
                len(registers),
            )
        ):

            value = registers[
                address
            ]

            self.register_tree.item(
                str(address),
                values=(
                    address,
                    REGISTER_NAMES.get(
                        address,
                        "UNKNOWN",
                    ),
                    value,
                    f"0x{value:04X}",
                ),
            )

    # ========================================================
    # WRITE REGISTER
    # ========================================================

    def write_register(
        self,
        address: int,
        value: int,
    ) -> bool:

        if not self.connect():

            messagebox.showerror(
                "Connection Error",
                "PLC server'a bağlanılamadı.",
            )

            return False

        try:

            result = self.client.write_register(
                address=address,
                value=value & 0xFFFF,
                device_id=UNIT_ID,
            )

            if result.isError():

                messagebox.showerror(
                    "Modbus Error",
                    str(result),
                )

                return False

            return True

        except Exception as exc:

            messagebox.showerror(
                "Write Error",
                str(exc),
            )

            return False

    # ========================================================
    # WRITE UINT32
    # ========================================================

    def write_uint32(
        self,
        high_address: int,
        value: int,
    ) -> bool:

        if not self.connect():

            messagebox.showerror(
                "Connection Error",
                "PLC server'a bağlanılamadı.",
            )

            return False

        if (
            value < 0
            or value > 0xFFFFFFFF
        ):

            messagebox.showerror(
                "Invalid Value",
                "UINT32 değeri 0 ile "
                "4294967295 arasında olmalı.",
            )

            return False

        high = (
            value >> 16
        ) & 0xFFFF

        low = (
            value
            & 0xFFFF
        )

        try:

            result = self.client.write_registers(
                address=high_address,
                values=[
                    high,
                    low,
                ],
                device_id=UNIT_ID,
            )

            if result.isError():

                messagebox.showerror(
                    "Modbus Error",
                    str(result),
                )

                return False

            return True

        except Exception as exc:

            messagebox.showerror(
                "Write Error",
                str(exc),
            )

            return False

    # ========================================================
    # MOTOR COMMANDS
    # ========================================================

    def start_motor(self):

        if self.write_register(
            MOTOR_COMMAND,
            1,
        ):

            self.show_info(
                "Motor",
                "START komutu PLC'ye gönderildi.",
            )

    def stop_motor(self):

        if self.write_register(
            MOTOR_COMMAND,
            0,
        ):

            self.show_info(
                "Motor",
                "STOP komutu PLC'ye gönderildi.",
            )

    # ========================================================
    # MODE
    # ========================================================

    def set_auto(self):

        if self.write_register(
            MODE,
            1,
        ):

            self.show_info(
                "Mode",
                "AUTO modu seçildi.",
            )

    def set_manual(self):

        if self.write_register(
            MODE,
            0,
        ):

            self.show_info(
                "Mode",
                "MANUAL modu seçildi.",
            )

    # ========================================================
    # EMERGENCY STOP
    # ========================================================

    def emergency_stop(self):

        confirmed = messagebox.askyesno(
            "EMERGENCY STOP",
            "Emergency Stop aktif edilsin mi?\n\n"
            "Motor durdurulacaktır.",
            icon="warning",
        )

        if not confirmed:
            return

        if self.write_register(
            EMERGENCY_STOP,
            1,
        ):

            messagebox.showwarning(
                "EMERGENCY STOP",
                "Emergency Stop aktif edildi.",
            )

    # ========================================================
    # RESET EMERGENCY STOP
    # ========================================================

    def reset_emergency_stop(self):

        confirmed = messagebox.askyesno(
            "RESET E-STOP",
            "Emergency Stop resetlensin mi?\n\n"
            "Motor otomatik olarak başlamayacaktır.\n"
            "Gerekirse ayrıca START komutu verilmelidir.",
        )

        if not confirmed:
            return

        if self.write_register(
            EMERGENCY_STOP,
            0,
        ):

            self.show_info(
                "E-STOP RESET",
                "Emergency Stop resetlendi.\n"
                "Motor yeniden başlatılmak için START bekliyor.",
            )

    # ========================================================
    # SETPOINTS
    # ========================================================

    def write_rpm(self):

        try:

            value = int(
                self.rpm_entry_var.get()
            )

        except ValueError:

            messagebox.showerror(
                "Invalid RPM",
                "RPM değeri tam sayı olmalı.",
            )

            return

        if value < 0 or value > 3000:

            messagebox.showerror(
                "Invalid RPM",
                "RPM 0-3000 arasında olmalı.",
            )

            return

        if self.write_uint32(
            SPEED_SETPOINT_HI,
            value,
        ):

            self.show_info(
                "RPM",
                f"RPM setpoint {value} olarak yazıldı.",
            )

    def write_pressure(self):

        try:

            value = float(
                self.pressure_entry_var.get()
            )

        except ValueError:

            messagebox.showerror(
                "Invalid Pressure",
                "Pressure değeri sayı olmalı.",
            )

            return

        if value < 0 or value > 10:

            messagebox.showerror(
                "Invalid Pressure",
                "Pressure 0-10 bar arasında olmalı.",
            )

            return

        raw = int(
            value * 10
        )

        if self.write_uint32(
            PRESSURE_SETPOINT_HI,
            raw,
        ):

            self.show_info(
                "Pressure",
                f"Pressure setpoint "
                f"{value:.1f} bar olarak yazıldı.",
            )

    # ========================================================
    # MESSAGE HELPER
    # ========================================================

    def show_info(
        self,
        title: str,
        message: str,
    ) -> None:

        # Normal çalışma sırasında sürekli popup
        # çıkmaması için kısa bir status mesajı.
        self.connection_detail_var.set(
            f"{PLC_IP}:{PLC_PORT} | {message}"
        )

        self.root.after(
            2500,
            lambda: self.connection_detail_var.set(
                f"{PLC_IP}:{PLC_PORT}"
            ),
        )

    # ========================================================
    # CLOSE
    # ========================================================

    def on_close(self):

        try:

            self.client.close()

        except Exception:

            pass

        self.root.destroy()


# ============================================================
# MAIN
# ============================================================

def main():

    root = tk.Tk()

    app = PLC_HMI(
        root
    )

    root.mainloop()


if __name__ == "__main__":

    main()
