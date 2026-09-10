import os
import sys
import json
import time
import winreg
import winsound
import ctypes
from ctypes import wintypes
import threading
from datetime import datetime
from PIL import Image, ImageDraw
import pystray
import customtkinter as ctk
from tkinter import messagebox

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

MUTEX_NAME = "Global\\TimeFocus_SingleInstance_Mutex"
RUN_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "TimeFocusApp"

APPDATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "TimeFocus")
os.makedirs(APPDATA_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(APPDATA_DIR, "time_focus_config.json")
HISTORY_FILE = os.path.join(APPDATA_DIR, "time_focus_history.json")

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

DEFAULT_SCHEDULE = {
    day: {"enabled": (day not in ["Sat", "Sun"]), "start_h": 9, "start_m": 0, "end_h": 12, "end_m": 0}
    for day in DAYS
}

DEFAULT_CONFIG = {
    "app_keywords": ["krita", "photoshop", "clip studio", "pureref"],
    "website_keywords": ["quickposes", "figure and anatomy", "adorkastock", "line of action", "posemaniacs", "pinterest", "artstation"],
    "default_duration_mins": 25,
    "pomodoro_mode": False,
    "schedule_master_enabled": True,
    "run_at_startup": False,
    "auto_lock_on_startup": True,
    "daily_schedules": DEFAULT_SCHEDULE
}

WINDOWS_SHELL_CLASSES = [
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.AppFrameWindow",
    "SearchPane",
    "TaskSwitcherWnd",
    "CabinetWClass",
    "ExploreWClass",
    "#32770",
    "WorkerW",
    "Progman",
    "XamlExplorerHostIslandWindow",
    "ApplicationFrameWindow"
]

class SingleInstance:
    def __init__(self):
        self.mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        self.last_error = kernel32.GetLastError()

    def is_already_running(self):
        return self.last_error == 183

def bring_existing_window_to_front():
    hwnd = user32.FindWindowW(None, "TimeFocus")
    if hwnd:
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)

VK_CONTROL = 0x11
VK_TAB = 0x09
KEYEVENTF_KEYUP = 0x0002

def release_stuck_keys():
    user32.keybd_event(VK_TAB, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

def fast_ctrl_tab():
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_TAB, 0, 0, 0)
    user32.keybd_event(VK_TAB, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in default.items():
                    if k not in data:
                        data[k] = v
                return data
        except Exception:
            pass
    save_json(path, default)
    return default.copy()

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception:
        pass

def set_windows_startup(enable=True):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_KEY, 0, winreg.KEY_ALL_ACCESS)
        if enable:
            if getattr(sys, 'frozen', False):
                exe_path = f'"{sys.executable}" --minimized'
            else:
                exe_path = f'"{sys.executable.replace("python.exe", "pythonw.exe")}" "{os.path.abspath(__file__)}" --minimized'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_path)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception:
        return False

def get_active_window_handle():
    return user32.GetForegroundWindow()

def get_active_window_title():
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    if length > 0:
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        return buff.value
    return ""

def get_active_window_class(hwnd):
    buff = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buff, 256)
    return buff.value

def minimize_window(hwnd):
    user32.ShowWindow(hwnd, 6)

def create_tray_icon_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill="#0F172A", outline="#38BDF8", width=4)
    draw.rectangle((28, 14, 36, 34), fill="#38BDF8")
    draw.rectangle((28, 28, 44, 34), fill="#10B981")
    return img

class TimeFocusApp(ctk.CTk):
    def __init__(self, start_minimized=False):
        super().__init__()
        self.config_data = load_json(CONFIG_FILE, DEFAULT_CONFIG)
        self.history_data = load_json(HISTORY_FILE, {})

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")

        self.title("TimeFocus")
        self.geometry("640x740")
        self.minsize(540, 650)
        self.resizable(True, True)

        self.c_bg = "#0F172A"
        self.c_card = "#1E293B"
        self.c_accent = "#38BDF8"
        self.c_active = "#10B981"
        self.c_danger = "#EF4444"
        self.c_break = "#F59E0B"

        self.configure(fg_color=self.c_bg)

        self.is_running = False
        self.time_left = 0
        self.auto_scheduled_active = False
        self.app_terminating = False

        # CRITICAL OVERRIDE FLAG: Prevents the 5s loop from re-locking when you hit Stop
        self.schedule_manual_override = False

        self.pomo_is_break = False
        self.pomo_cycle_count = 0

        self.create_widgets()
        self.protocol("WM_DELETE_WINDOW", self.exit_app)

        self.tray_icon = None
        self.init_tray_icon()

        if start_minimized:
            self.withdraw()

        self.check_and_activate_schedule(force_start=True)

        self.tracker_thread = threading.Thread(target=self.background_worker, daemon=True)
        self.tracker_thread.start()

    def init_tray_icon(self):
        icon_img = create_tray_icon_image()
        menu = pystray.Menu(
            pystray.MenuItem("Open TimeFocus", self.restore_from_tray, default=True),
            pystray.MenuItem("Minimize to Tray", self.hide_to_tray),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit Completely", self.exit_app)
        )
        self.tray_icon = pystray.Icon("TimeFocus", icon_img, "TimeFocus", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def hide_to_tray(self):
        self.withdraw()

    def restore_from_tray(self, icon=None, item=None):
        self.after(0, self._restore_ui)

    def _restore_ui(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def exit_app(self, icon=None, item=None):
        self.app_terminating = True
        self.is_running = False
        release_stuck_keys()
        if self.tray_icon:
            self.tray_icon.stop()
        self.destroy()
        sys.exit(0)

    def create_widgets(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.tabview = ctk.CTkTabview(
            self, 
            fg_color=self.c_card, 
            segmented_button_fg_color="#0F172A",
            segmented_button_selected_color=self.c_accent,
            segmented_button_selected_hover_color="#0284C7"
        )
        self.tabview.grid(row=0, column=0, padx=16, pady=16, sticky="nsew")

        self.tab_timer = self.tabview.add("Timer")
        self.tab_schedule = self.tabview.add("Schedule")
        self.tab_analytics = self.tabview.add("Analytics")
        self.tab_settings = self.tabview.add("Settings")

        # ================= TAB 1: TIMER =================
        self.tab_timer.grid_columnconfigure(0, weight=1)
        
        timer_card = ctk.CTkFrame(self.tab_timer, fg_color="#182234", corner_radius=16)
        timer_card.pack(fill="x", padx=20, pady=15)

        self.lbl_mode_header = ctk.CTkLabel(timer_card, text="Focus Session", font=("Segoe UI", 24, "bold"), text_color="#F8FAFC")
        self.lbl_mode_header.pack(pady=(15, 2))

        self.lbl_info = ctk.CTkLabel(
            timer_card, 
            text="Locks active screen focus to your selected drawing tools,\nreference folders, and approved browser tabs.",
            font=("Segoe UI", 12),
            text_color="#94A3B8"
        )
        self.lbl_info.pack(pady=(0, 10))

        self.switch_pomodoro = ctk.CTkSwitch(
            timer_card, text="Pomodoro Mode (25m Focus / 5m Break)",
            font=("Segoe UI", 12, "bold"), progress_color=self.c_accent,
            command=self.toggle_pomodoro_ui
        )
        if self.config_data.get("pomodoro_mode", False):
            self.switch_pomodoro.select()
        self.switch_pomodoro.pack(pady=(0, 10))

        self.duration_frame = ctk.CTkFrame(timer_card, fg_color="transparent")
        self.duration_frame.pack(pady=2)

        ctk.CTkLabel(self.duration_frame, text="Custom Duration (Mins):", font=("Segoe UI", 13, "bold"), text_color="#E2E8F0").pack(side="left", padx=10)
        self.entry_duration = ctk.CTkEntry(self.duration_frame, width=75, height=36, justify="center", font=("Segoe UI", 14, "bold"), corner_radius=8)
        self.entry_duration.insert(0, str(self.config_data.get("default_duration_mins", 25)))
        self.entry_duration.pack(side="left")

        self.lbl_countdown = ctk.CTkLabel(timer_card, text="00:00", font=("Segoe UI", 68, "bold"), text_color=self.c_accent)
        self.lbl_countdown.pack(pady=(10, 5))

        self.lbl_sched_status = ctk.CTkLabel(timer_card, text="● Schedule: Inactive", font=("Segoe UI", 12, "bold"), text_color="#64748B")
        self.lbl_sched_status.pack(pady=(0, 15))

        self.btn_frame = ctk.CTkFrame(self.tab_timer, fg_color="transparent")
        self.btn_frame.pack(pady=10)

        self.btn_start = ctk.CTkButton(
            self.btn_frame, text="Start Lock", width=150, height=44, 
            font=("Segoe UI", 14, "bold"), corner_radius=10, 
            fg_color=self.c_accent, hover_color="#0284C7", text_color="#0F172A",
            command=self.start_manual_session
        )
        self.btn_start.pack(side="left", padx=10)

        self.btn_stop = ctk.CTkButton(
            self.btn_frame, text="Stop", width=110, height=44, 
            font=("Segoe UI", 14, "bold"), corner_radius=10, 
            fg_color=self.c_danger, hover_color="#DC2626", state="disabled", 
            command=self.stop_manual_session
        )
        self.btn_stop.pack(side="left", padx=10)

        self.toggle_pomodoro_ui()

        # ================= TAB 2: 7-DAY SCHEDULE =================
        sched_header = ctk.CTkFrame(self.tab_schedule, fg_color="transparent")
        sched_header.pack(fill="x", padx=15, pady=(10, 5))

        self.switch_master_sched = ctk.CTkSwitch(
            sched_header, text="Enable Weekly Automated Schedule", 
            font=("Segoe UI", 13, "bold"), progress_color=self.c_active
        )
        if self.config_data.get("schedule_master_enabled", True):
            self.switch_master_sched.select()
        self.switch_master_sched.pack(side="left")

        self.switch_auto_startup = ctk.CTkSwitch(
            self.tab_schedule, text="Auto-activate focus on launch if inside scheduled hours", 
            font=("Segoe UI", 12, "bold"), progress_color=self.c_accent
        )
        if self.config_data.get("auto_lock_on_startup", True):
            self.switch_auto_startup.select()
        self.switch_auto_startup.pack(anchor="w", padx=15, pady=(5, 10))

        self.sched_scroll = ctk.CTkScrollableFrame(self.tab_schedule, fg_color="#182234", corner_radius=14)
        self.sched_scroll.pack(padx=15, pady=5, fill="both", expand=True)

        self.day_inputs = {}
        daily_data = self.config_data.get("daily_schedules", DEFAULT_SCHEDULE)

        for d in DAYS:
            d_data = daily_data.get(d, {"enabled": True, "start_h": 9, "start_m": 0, "end_h": 12, "end_m": 0})
            row = ctk.CTkFrame(self.sched_scroll, fg_color="#1E293B", corner_radius=10)
            row.pack(fill="x", padx=8, pady=5)

            cb = ctk.CTkCheckBox(row, text=d, width=70, font=("Segoe UI", 12, "bold"), checkmark_color="#0F172A", fg_color=self.c_accent)
            if d_data.get("enabled", False):
                cb.select()
            cb.pack(side="left", padx=12, pady=10)

            ctk.CTkLabel(row, text="From:", font=("Segoe UI", 11), text_color="#94A3B8").pack(side="left", padx=(10, 4))
            sh = ctk.CTkEntry(row, width=42, height=30, justify="center", corner_radius=6)
            sh.insert(0, f"{int(d_data.get('start_h', 9)):02d}")
            sh.pack(side="left")
            ctk.CTkLabel(row, text=":", text_color="#94A3B8").pack(side="left")
            sm = ctk.CTkEntry(row, width=42, height=30, justify="center", corner_radius=6)
            sm.insert(0, f"{int(d_data.get('start_m', 0)):02d}")
            sm.pack(side="left")

            ctk.CTkLabel(row, text="To:", font=("Segoe UI", 11), text_color="#94A3B8").pack(side="left", padx=(16, 4))
            eh = ctk.CTkEntry(row, width=42, height=30, justify="center", corner_radius=6)
            eh.insert(0, f"{int(d_data.get('end_h', 12)):02d}")
            eh.pack(side="left")
            ctk.CTkLabel(row, text=":", text_color="#94A3B8").pack(side="left")
            em = ctk.CTkEntry(row, width=42, height=30, justify="center", corner_radius=6)
            em.insert(0, f"{int(d_data.get('end_m', 0)):02d}")
            em.pack(side="left")

            self.day_inputs[d] = {"cb": cb, "sh": sh, "sm": sm, "eh": eh, "em": em}

        self.btn_save_sched = ctk.CTkButton(
            self.tab_schedule, text="Save Schedule Settings", width=180, height=38, 
            font=("Segoe UI", 13, "bold"), corner_radius=8, 
            fg_color=self.c_accent, hover_color="#0284C7", text_color="#0F172A",
            command=self.save_schedule
        )
        self.btn_save_sched.pack(pady=10)

        # ================= TAB 3: VISUAL ANALYTICS =================
        self.tab_analytics.grid_columnconfigure(0, weight=1)
        self.tab_analytics.grid_rowconfigure(1, weight=1)

        an_controls = ctk.CTkFrame(self.tab_analytics, fg_color="transparent")
        an_controls.grid(row=0, column=0, sticky="ew", padx=15, pady=(10, 8))

        self.view_mode = ctk.CTkSegmentedButton(
            an_controls, values=["Today", "All-Time"], 
            selected_color=self.c_accent, selected_hover_color="#0284C7",
            command=lambda v: self.render_graph()
        )
        self.view_mode.set("Today")
        self.view_mode.pack(side="left")

        self.btn_clear_stats = ctk.CTkButton(
            an_controls, text="Reset History", width=90, height=32, 
            fg_color="#7F1D1D", hover_color=self.c_danger, corner_radius=6,
            command=self.reset_history
        )
        self.btn_clear_stats.pack(side="right", padx=(5, 0))

        self.btn_refresh_graph = ctk.CTkButton(
            an_controls, text="Refresh", width=80, height=32, 
            fg_color="#334155", hover_color="#475569", corner_radius=6,
            command=self.render_graph
        )
        self.btn_refresh_graph.pack(side="right", padx=5)

        self.canvas_chart = ctk.CTkCanvas(self.tab_analytics, bg="#111827", highlightthickness=0)
        self.canvas_chart.grid(row=1, column=0, sticky="nsew", padx=15, pady=(0, 15))
        self.canvas_chart.bind("<Configure>", lambda event: self.render_graph())

        # ================= TAB 4: SETTINGS =================
        settings_card = ctk.CTkFrame(self.tab_settings, fg_color="#182234", corner_radius=14)
        settings_card.pack(fill="both", expand=True, padx=15, pady=15)

        self.switch_startup = ctk.CTkSwitch(
            settings_card, text="Start silently on Windows Boot", 
            font=("Segoe UI", 12, "bold"), progress_color=self.c_active
        )
        if self.config_data.get("run_at_startup", False):
            self.switch_startup.select()
        self.switch_startup.pack(anchor="w", padx=20, pady=(20, 15))

        ctk.CTkLabel(settings_card, text="Whitelisted App Keywords (comma-separated):", font=("Segoe UI", 12, "bold"), text_color="#F8FAFC").pack(anchor="w", padx=20, pady=(5, 5))
        self.txt_apps = ctk.CTkEntry(settings_card, corner_radius=8, height=36)
        self.txt_apps.insert(0, ", ".join(self.config_data.get("app_keywords", [])))
        self.txt_apps.pack(fill="x", padx=20, pady=(0, 15))

        ctk.CTkLabel(settings_card, text="Whitelisted Tab / Website Keywords (comma-separated):", font=("Segoe UI", 12, "bold"), text_color="#F8FAFC").pack(anchor="w", padx=20, pady=(5, 5))
        self.txt_websites = ctk.CTkEntry(settings_card, corner_radius=8, height=36)
        self.txt_websites.insert(0, ", ".join(self.config_data.get("website_keywords", [])))
        self.txt_websites.pack(fill="x", padx=20, pady=(0, 25))

        self.btn_save_settings = ctk.CTkButton(
            settings_card, text="Save Settings", width=160, height=38, 
            font=("Segoe UI", 13, "bold"), corner_radius=8, 
            fg_color=self.c_accent, hover_color="#0284C7", text_color="#0F172A",
            command=self.save_settings
        )
        self.btn_save_settings.pack(pady=10)

        self.after(500, self.render_graph)

    def toggle_pomodoro_ui(self):
        is_pomo = bool(self.switch_pomodoro.get())
        if is_pomo:
            self.entry_duration.configure(state="disabled")
            self.lbl_info.configure(text="Pomodoro Mode: 25m focus lock -> 5m free break.\nRestrictions automatically lift during break intervals.")
        else:
            self.entry_duration.configure(state="normal")
            self.lbl_info.configure(text="Locks active screen focus to your selected drawing suite,\nreference folders, and approved browser tabs.")

    def save_settings(self):
        apps = [x.strip().lower() for x in self.txt_apps.get().split(",") if x.strip()]
        websites = [x.strip().lower() for x in self.txt_websites.get().split(",") if x.strip()]
        self.config_data["app_keywords"] = apps
        self.config_data["website_keywords"] = websites
        
        startup_enabled = bool(self.switch_startup.get())
        self.config_data["run_at_startup"] = startup_enabled
        set_windows_startup(startup_enabled)

        self.config_data["pomodoro_mode"] = bool(self.switch_pomodoro.get())

        try:
            mins = int(self.entry_duration.get())
            self.config_data["default_duration_mins"] = mins
        except Exception:
            pass
        save_json(CONFIG_FILE, self.config_data)
        messagebox.showinfo("Saved", "Settings updated!")

    def save_schedule(self):
        daily_dict = {}
        for d, inputs in self.day_inputs.items():
            try:
                sh = int(inputs["sh"].get())
                sm = int(inputs["sm"].get())
                eh = int(inputs["eh"].get())
                em = int(inputs["em"].get())
                if not (0 <= sh <= 23 and 0 <= eh <= 23 and 0 <= sm <= 59 and 0 <= em <= 59):
                    raise ValueError
            except ValueError:
                messagebox.showerror("Error", f"Invalid time on {d}.")
                return

            daily_dict[d] = {
                "enabled": bool(inputs["cb"].get()),
                "start_h": sh,
                "start_m": sm,
                "end_h": eh,
                "end_m": em
            }

        self.config_data["schedule_master_enabled"] = bool(self.switch_master_sched.get())
        self.config_data["auto_lock_on_startup"] = bool(self.switch_auto_startup.get())
        self.config_data["daily_schedules"] = daily_dict
        save_json(CONFIG_FILE, self.config_data)
        
        # Reset override if user explicitly saves schedule
        self.schedule_manual_override = False
        messagebox.showinfo("Saved", "Weekly schedule permanently saved!")
        self.check_and_activate_schedule()

    def start_manual_session(self):
        # User manually started: reset the manual override flag
        self.schedule_manual_override = False

        is_pomo = bool(self.switch_pomodoro.get())
        if is_pomo:
            self.pomo_is_break = False
            self.pomo_cycle_count = 1
            self.time_left = 25 * 60
            self.lbl_mode_header.configure(text="Pomodoro Focus #1", text_color=self.c_active)
        else:
            try:
                mins = float(self.entry_duration.get())
                if mins <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Error", "Enter valid minutes.")
                return
            self.time_left = int(mins * 60)
            self.lbl_mode_header.configure(text="Focus Session", text_color="#F8FAFC")

        self.is_running = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.entry_duration.configure(state="disabled")
        self.switch_pomodoro.configure(state="disabled")

        self.update_timer()

    def stop_manual_session(self):
        # Stop session completely and prevent schedule from restarting it
        self.is_running = False
        self.auto_scheduled_active = False
        self.schedule_manual_override = True

        self.pomo_is_break = False
        release_stuck_keys()
        
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.entry_duration.configure(state="normal")
        self.switch_pomodoro.configure(state="normal")
        self.lbl_mode_header.configure(text="Focus Session", text_color="#F8FAFC")
        self.lbl_countdown.configure(text="00:00", text_color=self.c_accent)
        self.lbl_sched_status.configure(text="● Schedule: Paused (Manual Stop)", text_color="#F59E0B")

    def update_timer(self):
        if self.is_running and self.time_left > 0:
            m, s = divmod(self.time_left, 60)
            color = self.c_break if self.pomo_is_break else self.c_active
            self.lbl_countdown.configure(text=f"{m:02d}:{s:02d}", text_color=color)
            self.time_left -= 1
            self.after(1000, self.update_timer)
        elif self.time_left <= 0 and self.is_running:
            if bool(self.switch_pomodoro.get()):
                self.advance_pomodoro_cycle()
            else:
                self.stop_manual_session()
                winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS)
                messagebox.showinfo("Done", "TimeFocus session completed!")

    def advance_pomodoro_cycle(self):
        winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS)
        release_stuck_keys()
        if not self.pomo_is_break:
            self.pomo_is_break = True
            is_long = (self.pomo_cycle_count % 4 == 0)
            break_mins = 15 if is_long else 5
            self.time_left = break_mins * 60
            self.lbl_mode_header.configure(
                text=f"{'Long' if is_long else 'Short'} Break (Free)", 
                text_color=self.c_break
            )
            if self.tray_icon:
                self.tray_icon.notify(f"Time for a {break_mins}m break! Focus lock lifted.", "TimeFocus")
        else:
            self.pomo_is_break = False
            self.pomo_cycle_count += 1
            self.time_left = 25 * 60
            self.lbl_mode_header.configure(
                text=f"Pomodoro Focus #{self.pomo_cycle_count}", 
                text_color=self.c_active
            )
            if self.tray_icon:
                self.tray_icon.notify("Break over! Focus lock re-engaged.", "TimeFocus")
        self.update_timer()

    def check_and_activate_schedule(self, force_start=False):
        now = datetime.now()
        cur_day = DAYS[now.weekday()]
        cur_mins = now.hour * 60 + now.minute

        master_on = self.config_data.get("schedule_master_enabled", True)
        daily_dict = self.config_data.get("daily_schedules", DEFAULT_SCHEDULE)
        day_cfg = daily_dict.get(cur_day, {})

        is_active_now = False
        remaining_seconds = 0

        if master_on and day_cfg.get("enabled", False):
            start_m = int(day_cfg.get("start_h", 0)) * 60 + int(day_cfg.get("start_m", 0))
            end_m = int(day_cfg.get("end_h", 0)) * 60 + int(day_cfg.get("end_m", 0))
            if start_m <= cur_mins < end_m:
                is_active_now = True
                remaining_seconds = (end_m - cur_mins) * 60

        # If the scheduled time window has passed, reset the manual override flag
        if not is_active_now:
            self.schedule_manual_override = False
            self.auto_scheduled_active = False
            self.lbl_sched_status.configure(text="● Schedule: Inactive", text_color="#64748B")
            if self.is_running and self.lbl_mode_header.cget("text") == "Scheduled Focus":
                self.after(0, self.stop_manual_session)
            return

        # If we are inside schedule hours, check if the user manually hit Stop
        if self.schedule_manual_override:
            self.lbl_sched_status.configure(text="● Schedule: Paused (Manual Stop)", text_color="#F59E0B")
            return

        # Otherwise, engage the schedule lock normally
        self.auto_scheduled_active = True
        self.lbl_sched_status.configure(text=f"● Schedule: ACTIVE ({cur_day})", text_color=self.c_active)

        if (force_start or self.config_data.get("auto_lock_on_startup", True)) and not self.is_running:
            if self.config_data.get("pomodoro_mode", False):
                self.after(0, self.start_manual_session)
            else:
                self.time_left = remaining_seconds
                self.is_running = True
                self.btn_start.configure(state="disabled")
                self.btn_stop.configure(state="normal")
                self.lbl_mode_header.configure(text="Scheduled Focus", text_color=self.c_active)
                self.after(0, self.update_timer)

    def render_graph(self):
        self.canvas_chart.delete("all")
        mode = self.view_mode.get()
        today_str = datetime.now().strftime("%Y-%m-%d")

        data_to_plot = {}
        if mode == "Today":
            data_to_plot = self.history_data.get(today_str, {})
        else:
            for day, records in self.history_data.items():
                for title, secs in records.items():
                    data_to_plot[title] = data_to_plot.get(title, 0) + secs

        width = self.canvas_chart.winfo_width()
        height = self.canvas_chart.winfo_height()

        if not data_to_plot:
            self.canvas_chart.create_text(
                width // 2, height // 2, 
                text="No activity tracked yet.\nWork normally and refresh to see bars.",
                fill="#64748B", font=("Segoe UI", 12), justify="center"
            )
            return

        top_items = sorted(data_to_plot.items(), key=lambda x: x[1], reverse=True)[:8]
        max_secs = top_items[0][1] if top_items[0][1] > 0 else 1

        y_start = 30
        bar_height = 20
        gap = 48
        bar_max_width = max(100, width - 260)

        for idx, (name, secs) in enumerate(top_items):
            y = y_start + (idx * gap)
            bar_w = max(12, int((secs / max_secs) * bar_max_width))

            m, s = divmod(secs, 60)
            h, m = divmod(m, 60)
            dur_str = f"{h}h {m}m" if h > 0 else (f"{m}m" if m > 0 else f"{s}s")

            display_name = (name[:20] + "..") if len(name) > 22 else name

            self.canvas_chart.create_text(20, y + 10, text=display_name, fill="#E2E8F0", anchor="w", font=("Segoe UI", 10, "bold"))
            self.canvas_chart.create_rectangle(170, y, 170 + bar_w, y + bar_height, fill=self.c_accent, outline="")
            self.canvas_chart.create_text(180 + bar_w, y + 10, text=dur_str, fill=self.c_active, anchor="w", font=("Segoe UI", 10, "bold"))

    def reset_history(self):
        if messagebox.askyesno("Confirm", "Reset all tracked usage history?"):
            self.history_data = {}
            save_json(HISTORY_FILE, {})
            self.render_graph()

    def background_worker(self):
        tracker_accumulator = 0.0
        schedule_check_accumulator = 0.0
        save_counter = 0

        while not self.app_terminating:
            time.sleep(0.05)
            tracker_accumulator += 0.05
            schedule_check_accumulator += 0.05

            hwnd = get_active_window_handle()
            if not hwnd:
                continue

            title = get_active_window_title().strip()
            win_class = get_active_window_class(hwnd)

            # 1. TRACK USAGE
            if tracker_accumulator >= 1.0:
                tracker_accumulator = 0.0
                today_str = datetime.now().strftime("%Y-%m-%d")

                if title and hwnd != 0 and win_class not in WINDOWS_SHELL_CLASSES:
                    short_title = title.split(" - ")[0].strip() if " - " in title else title
                    if today_str not in self.history_data:
                        self.history_data[today_str] = {}

                    self.history_data[today_str][short_title] = self.history_data[today_str].get(short_title, 0) + 1
                    save_counter += 1

                    if save_counter >= 30:
                        save_json(HISTORY_FILE, self.history_data)
                        save_counter = 0

            # 2. EVALUATE SCHEDULE EVERY 5 SECONDS
            if schedule_check_accumulator >= 5.0:
                schedule_check_accumulator = 0.0
                self.after(0, self.check_and_activate_schedule)

            # 3. ENFORCE LOCK
            if self.is_running and self.pomo_is_break:
                continue

            # If user stopped the session, should_lock will be False
            should_lock = self.is_running or (self.auto_scheduled_active and not self.schedule_manual_override)
            if not should_lock or not title:
                continue

            if win_class in WINDOWS_SHELL_CLASSES:
                continue

            title_lower = title.lower()

            if "timefocus" in title_lower:
                continue

            app_keywords = [k.lower() for k in self.config_data.get("app_keywords", [])]
            if any(app in title_lower for app in app_keywords):
                continue

            web_keywords = [k.lower() for k in self.config_data.get("website_keywords", [])]
            is_browser = any(b in title_lower for b in ["edge", "chrome", "firefox", "brave"])

            if is_browser:
                is_permitted_tab = any(k in title_lower for k in web_keywords)
                if not is_permitted_tab:
                    fast_ctrl_tab()
                    time.sleep(0.04)
                continue

            minimize_window(hwnd)

if __name__ == "__main__":
    single_inst = SingleInstance()
    if single_inst.is_already_running():
        bring_existing_window_to_front()
        sys.exit(0)

    is_minimized_boot = "--minimized" in sys.argv
    app = TimeFocusApp(start_minimized=is_minimized_boot)
    app.mainloop()