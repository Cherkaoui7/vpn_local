from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .config_loader import Settings, load_settings, list_ovpn_configs, choose_config, measure_latency, extract_remote_host
from .vpn_manager import VpnManager, VpnState
from .credentials import fetch_vpnbook_credentials, update_auth_file
from .ip_check import get_public_ip

# Import cli helper functions for background process control
from .cli import _start_background, _stop_background, _is_pid_running, _read_rotator_state, _clear_rotator_state

# CustomTkinter styling settings
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


def format_server_display_name(filename: str) -> str:
    if not filename or filename == "None":
        return "None"
    name = filename
    if name.endswith(".ovpn"):
        name = name[:-5]
        
    parts = name.split("-")
    if len(parts) >= 2:
        cc_num = parts[1].lower()
        cc = ""
        num = ""
        for char in cc_num:
            if char.isalpha():
                cc += char
            elif char.isdigit():
                num += char
        
        country_map = {
            "ca": "Canada",
            "de": "Germany",
            "fr": "France",
            "uk": "United Kingdom",
            "us": "United States",
        }
        
        country = country_map.get(cc, cc.upper())
        if num:
            return f"{country} #{num}"
        return country
        
    return filename

class VpnPrivateApp(ctk.CTk):
    def __init__(self, settings_path: Path):
        super().__init__()
        
        self.settings_path = settings_path
        self.settings = load_settings(settings_path)
        
        # Set up a logger for the GUI
        self.logger = logging.getLogger("VpnPrivateGUI")
        self.logger.setLevel(logging.DEBUG)
        
        # Initialize VPN Manager
        self.manager = VpnManager(self.settings, self.logger)
        
        # App Configuration
        self.title("VpnPrivate")
        self.geometry("1000x700")
        self.minsize(900, 600)
        
        # Internal state variables
        self.is_connecting = False
        self.connecting_server_name = ""
        self.latency_results: dict[str, str] = {}
        self.log_file_pointer = None
        self.last_log_size = 0
        self.current_log_path = None
        
        # Grid Configuration
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0)  # Left Panel (Fixed Width)
        self.grid_columnconfigure(1, weight=1)  # Right Panel (Flexible Width)
        
        self._create_left_panel()
        self._create_right_panel()
        
        # Initial population of fields
        self._refresh_config_list()
        self._load_settings_into_ui()
        self._load_credentials_into_ui()
        
        # Start periodic update loops
        self.update_status_loop()
        self.update_logs_loop()

    def _create_left_panel(self):
        """Creates the left panel containing status cards and main control buttons."""
        left_frame = ctk.CTkFrame(self, width=320, corner_radius=0)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        left_frame.grid_propagate(False)
        
        # App Name & Subtitle
        header_label = ctk.CTkLabel(
            left_frame, 
            text="VpnPrivate", 
            font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"),
            text_color="#1FB5FF"
        )
        header_label.pack(padx=20, pady=(20, 5), anchor="w")
        
        subtitle_label = ctk.CTkLabel(
            left_frame, 
            text="VPN Rotator for Windows", 
            font=ctk.CTkFont(family="Segoe UI", size=12, slant="italic"),
            text_color="gray"
        )
        subtitle_label.pack(padx=20, pady=(0, 20), anchor="w")
        
        # --- STATUS CARD ---
        self.status_card = ctk.CTkFrame(left_frame, fg_color="#2B2B2B", border_width=1, border_color="#3F3F3F")
        self.status_card.pack(fill="x", padx=15, pady=10)
        
        self.status_indicator = ctk.CTkLabel(
            self.status_card, 
            text="Disconnected", 
            font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
            text_color="#FF4C4C"
        )
        self.status_indicator.pack(pady=(15, 10))
        
        # Info grid inside Status Card
        info_frame = ctk.CTkFrame(self.status_card, fg_color="transparent")
        info_frame.pack(fill="x", padx=15, pady=(0, 15))
        
        # Row 1: Active Server
        server_lbl = ctk.CTkLabel(info_frame, text="Active Server:", font=ctk.CTkFont(size=11, weight="bold"), text_color="gray")
        server_lbl.grid(row=0, column=0, sticky="w", pady=2)
        self.active_server_val = ctk.CTkLabel(info_frame, text="None", font=ctk.CTkFont(size=12))
        self.active_server_val.grid(row=0, column=1, sticky="w", padx=10, pady=2)
        
        # Row 2: Public IP
        ip_lbl = ctk.CTkLabel(info_frame, text="Public IP:", font=ctk.CTkFont(size=11, weight="bold"), text_color="gray")
        ip_lbl.grid(row=1, column=0, sticky="w", pady=2)
        self.public_ip_val = ctk.CTkLabel(info_frame, text="Unknown", font=ctk.CTkFont(size=12))
        self.public_ip_val.grid(row=1, column=1, sticky="w", padx=10, pady=2)
        
        # Row 3: Connection Uptime
        uptime_lbl = ctk.CTkLabel(info_frame, text="Uptime:", font=ctk.CTkFont(size=11, weight="bold"), text_color="gray")
        uptime_lbl.grid(row=2, column=0, sticky="w", pady=2)
        self.uptime_val = ctk.CTkLabel(info_frame, text="00:00:00", font=ctk.CTkFont(size=12))
        self.uptime_val.grid(row=2, column=1, sticky="w", padx=10, pady=2)
        
        # Row 4: Rotator Status
        rotator_lbl = ctk.CTkLabel(info_frame, text="Rotator:", font=ctk.CTkFont(size=11, weight="bold"), text_color="gray")
        rotator_lbl.grid(row=3, column=0, sticky="w", pady=2)
        self.rotator_val = ctk.CTkLabel(info_frame, text="Inactive", font=ctk.CTkFont(size=12, weight="bold"), text_color="gray")
        self.rotator_val.grid(row=3, column=1, sticky="w", padx=10, pady=2)
        
        # Row 5: Next Rotation Countdown
        self.countdown_lbl = ctk.CTkLabel(info_frame, text="Next Rotation:", font=ctk.CTkFont(size=11, weight="bold"), text_color="gray")
        self.countdown_lbl.grid(row=4, column=0, sticky="w", pady=2)
        self.countdown_val = ctk.CTkLabel(info_frame, text="N/A", font=ctk.CTkFont(size=12))
        self.countdown_val.grid(row=4, column=1, sticky="w", padx=10, pady=2)
        
        # Rotation progress bar
        self.rotation_progress = ctk.CTkProgressBar(self.status_card, height=6)
        self.rotation_progress.pack(fill="x", padx=15, pady=(0, 15))
        self.rotation_progress.set(0)
        
        # --- QUICK CONTROLS CARD ---
        controls_card = ctk.CTkFrame(left_frame, fg_color="transparent")
        controls_card.pack(fill="both", expand=True, padx=15, pady=10)
        
        # Server Selector Dropdown
        sel_label = ctk.CTkLabel(controls_card, text="Target Server", font=ctk.CTkFont(weight="bold"))
        sel_label.pack(anchor="w", pady=(10, 2))
        
        sel_inner_frame = ctk.CTkFrame(controls_card, fg_color="transparent")
        sel_inner_frame.pack(fill="x", pady=(0, 10))
        
        self.server_dropdown = ctk.CTkOptionMenu(
            sel_inner_frame,
            values=["Auto (Fastest Latency)", "Random"]
        )
        self.server_dropdown.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        refresh_btn = ctk.CTkButton(
            sel_inner_frame, 
            text="🔄", 
            width=36,
            command=self._refresh_config_list
        )
        refresh_btn.pack(side="right")
        
        # Action Buttons
        self.connect_once_btn = ctk.CTkButton(
            controls_card, 
            text="Connect Once", 
            fg_color="#1F85DE",
            hover_color="#1F65AE",
            font=ctk.CTkFont(weight="bold"),
            command=self.on_connect_once
        )
        self.connect_once_btn.pack(fill="x", pady=5)
        
        self.start_rotator_btn = ctk.CTkButton(
            controls_card, 
            text="Start Rotator", 
            fg_color="#2E7D32", 
            hover_color="#1B5E20",
            font=ctk.CTkFont(weight="bold"),
            command=self.on_start_rotator
        )
        self.start_rotator_btn.pack(fill="x", pady=5)
        
        self.rotate_now_btn = ctk.CTkButton(
            controls_card, 
            text="Rotate Server Now", 
            fg_color="#F57C00",
            hover_color="#E65100",
            font=ctk.CTkFont(weight="bold"),
            command=self.on_rotate_now
        )
        self.rotate_now_btn.pack(fill="x", pady=5)
        
        self.stop_vpn_btn = ctk.CTkButton(
            controls_card, 
            text="Stop VPN & Rotator", 
            fg_color="#C62828",
            hover_color="#B71C1C",
            font=ctk.CTkFont(weight="bold"),
            command=self.on_stop_all
        )
        self.stop_vpn_btn.pack(fill="x", pady=5)
        
        # Progress indicator / Connection Spinner text
        self.loading_lbl = ctk.CTkLabel(
            controls_card, 
            text="", 
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#1FB5FF"
        )
        self.loading_lbl.pack(pady=10)

    def _create_right_panel(self):
        """Creates the tabbed view on the right for logs, settings, credentials, and ping."""
        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=0, column=1, sticky="nsew", padx=15, pady=15)
        
        self.tab_logs = self.tabview.add("Live Logs")
        self.tab_settings = self.tabview.add("Settings")
        self.tab_creds = self.tabview.add("Credentials")
        self.tab_ping = self.tabview.add("Server Latency")
        
        self._build_logs_tab()
        self._build_settings_tab()
        self._build_creds_tab()
        self._build_ping_tab()

    def _build_logs_tab(self):
        """Builds the tab containing live log updates."""
        # Top toolbar
        toolbar = ctk.CTkFrame(self.tab_logs, fg_color="transparent")
        toolbar.pack(fill="x", padx=5, pady=5)
        
        log_sel_lbl = ctk.CTkLabel(toolbar, text="Log Source:")
        log_sel_lbl.pack(side="left", padx=5)
        
        self.log_source_dropdown = ctk.CTkOptionMenu(
            toolbar, 
            values=["Rotator Background Log", "Active VPN Log"],
            command=self._on_log_source_changed
        )
        self.log_source_dropdown.pack(side="left", padx=5)
        
        self.autoscroll_var = tk.BooleanVar(value=True)
        autoscroll_chk = ctk.CTkCheckBox(toolbar, text="Auto-scroll", variable=self.autoscroll_var)
        autoscroll_chk.pack(side="left", padx=15)
        
        clear_btn = ctk.CTkButton(toolbar, text="Clear Window", width=100, command=self._clear_log_window)
        clear_btn.pack(side="right", padx=5)
        
        # Log Text Box
        self.log_textbox = ctk.CTkTextbox(
            self.tab_logs, 
            font=ctk.CTkFont(family="Consolas", size=11), 
            text_color="#D4D4D4", 
            fg_color="#1E1E1E",
            wrap="none"
        )
        self.log_textbox.pack(fill="both", expand=True, padx=5, pady=5)
        self.log_textbox.configure(state="disabled")

    def _build_settings_tab(self):
        """Builds the Settings Tab containing input fields and file picker for settings."""
        scroll_frame = ctk.CTkScrollableFrame(self.tab_settings, fg_color="transparent")
        scroll_frame.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Row 1: OpenVPN Path
        path_lbl = ctk.CTkLabel(scroll_frame, text="OpenVPN Path (.exe):", font=ctk.CTkFont(weight="bold"))
        path_lbl.grid(row=0, column=0, sticky="w", padx=10, pady=10)
        
        path_inner = ctk.CTkFrame(scroll_frame, fg_color="transparent")
        path_inner.grid(row=0, column=1, sticky="we", padx=10, pady=10)
        scroll_frame.grid_columnconfigure(1, weight=1)
        
        self.setting_openvpn_path = ctk.CTkEntry(path_inner)
        self.setting_openvpn_path.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        browse_btn = ctk.CTkButton(path_inner, text="Browse...", width=80, command=self._browse_openvpn_path)
        browse_btn.pack(side="right")
        
        # Row 2: Rotation Interval
        rot_lbl = ctk.CTkLabel(scroll_frame, text="Rotation Interval (seconds):", font=ctk.CTkFont(weight="bold"))
        rot_lbl.grid(row=1, column=0, sticky="w", padx=10, pady=10)
        self.setting_rotation_sec = ctk.CTkEntry(scroll_frame, placeholder_text="1800")
        self.setting_rotation_sec.grid(row=1, column=1, sticky="w", padx=10, pady=10)
        
        # Row 3: Selection Mode
        mode_lbl = ctk.CTkLabel(scroll_frame, text="Selection Mode:", font=ctk.CTkFont(weight="bold"))
        mode_lbl.grid(row=2, column=0, sticky="w", padx=10, pady=10)
        self.setting_selection_mode = ctk.CTkOptionMenu(scroll_frame, values=["latency", "random"])
        self.setting_selection_mode.grid(row=2, column=1, sticky="w", padx=10, pady=10)
        
        # Row 4: Avoid Same Server Checkbox
        avoid_lbl = ctk.CTkLabel(scroll_frame, text="Avoid Same Server on Rotation:", font=ctk.CTkFont(weight="bold"))
        avoid_lbl.grid(row=3, column=0, sticky="w", padx=10, pady=10)
        self.setting_avoid_same = ctk.CTkCheckBox(scroll_frame, text="Enable")
        self.setting_avoid_same.grid(row=3, column=1, sticky="w", padx=10, pady=10)
        
        # Row 5: Force UDP Checkbox
        udp_lbl = ctk.CTkLabel(scroll_frame, text="Force UDP (with TCP fallback):", font=ctk.CTkFont(weight="bold"))
        udp_lbl.grid(row=4, column=0, sticky="w", padx=10, pady=10)
        self.setting_force_udp = ctk.CTkCheckBox(scroll_frame, text="Enable")
        self.setting_force_udp.grid(row=4, column=1, sticky="w", padx=10, pady=10)
        
        # Row 6: Public IP Check Checkbox
        ipchk_lbl = ctk.CTkLabel(scroll_frame, text="Verify Routing & Public IP:", font=ctk.CTkFont(weight="bold"))
        ipchk_lbl.grid(row=5, column=0, sticky="w", padx=10, pady=10)
        self.setting_ip_check = ctk.CTkCheckBox(scroll_frame, text="Enable")
        self.setting_ip_check.grid(row=5, column=1, sticky="w", padx=10, pady=10)
        
        # Row 7: Public IP Checker API URL
        url_lbl = ctk.CTkLabel(scroll_frame, text="IP Verification URL:", font=ctk.CTkFont(weight="bold"))
        url_lbl.grid(row=6, column=0, sticky="w", padx=10, pady=10)
        self.setting_ip_url = ctk.CTkEntry(scroll_frame, placeholder_text="https://api.ipify.org")
        self.setting_ip_url.grid(row=6, column=1, sticky="we", padx=10, pady=10)
        
        # Row 8: Connect Timeout
        timeout_lbl = ctk.CTkLabel(scroll_frame, text="TCP Timeout (seconds):", font=ctk.CTkFont(weight="bold"))
        timeout_lbl.grid(row=7, column=0, sticky="w", padx=10, pady=10)
        self.setting_timeout = ctk.CTkEntry(scroll_frame, placeholder_text="25")
        self.setting_timeout.grid(row=7, column=1, sticky="w", padx=10, pady=10)
        
        # Row 9: UDP Timeout
        udptimeout_lbl = ctk.CTkLabel(scroll_frame, text="UDP Timeout (seconds):", font=ctk.CTkFont(weight="bold"))
        udptimeout_lbl.grid(row=8, column=0, sticky="w", padx=10, pady=10)
        self.setting_udp_timeout = ctk.CTkEntry(scroll_frame, placeholder_text="8")
        self.setting_udp_timeout.grid(row=8, column=1, sticky="w", padx=10, pady=10)
        
        # Action Buttons frame
        actions_frame = ctk.CTkFrame(self.tab_settings, fg_color="transparent")
        actions_frame.pack(fill="x", padx=10, pady=10)
        
        save_btn = ctk.CTkButton(
            actions_frame, 
            text="Save Settings", 
            fg_color="#2E7D32", 
            hover_color="#1B5E20",
            font=ctk.CTkFont(weight="bold"),
            command=self._save_settings_from_ui
        )
        save_btn.pack(side="left", padx=5)
        
        reset_btn = ctk.CTkButton(
            actions_frame, 
            text="Reset UI Fields", 
            fg_color="#7F8C8D",
            hover_color="#6F7C7D",
            command=self._load_settings_into_ui
        )
        reset_btn.pack(side="left", padx=5)

    def _build_creds_tab(self):
        """Builds the tab for displaying and editing VPN credentials."""
        frame = ctk.CTkFrame(self.tab_creds, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        info_txt = (
            "These credentials are used by OpenVPN to connect. "
            "VPNBook updates passwords periodically. "
            "You can scrap the credentials automatically from vpnbook.com below."
        )
        info_lbl = ctk.CTkLabel(frame, text=info_txt, font=ctk.CTkFont(size=12), justify="left", wraplength=500)
        info_lbl.pack(anchor="w", pady=(0, 20))
        
        # Username Field
        usr_lbl = ctk.CTkLabel(frame, text="VPN Username:", font=ctk.CTkFont(weight="bold"))
        usr_lbl.pack(anchor="w", pady=2)
        self.creds_username = ctk.CTkEntry(frame, placeholder_text="vpnbook")
        self.creds_username.pack(fill="x", pady=(0, 15))
        
        # Password Field
        pwd_lbl = ctk.CTkLabel(frame, text="VPN Password:", font=ctk.CTkFont(weight="bold"))
        pwd_lbl.pack(anchor="w", pady=2)
        
        pwd_frame = ctk.CTkFrame(frame, fg_color="transparent")
        pwd_frame.pack(fill="x", pady=(0, 15))
        
        self.creds_password = ctk.CTkEntry(pwd_frame, show="*")
        self.creds_password.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        self.show_pwd_var = tk.BooleanVar(value=False)
        self.show_pwd_chk = ctk.CTkCheckBox(
            pwd_frame, 
            text="Show", 
            width=60, 
            variable=self.show_pwd_var,
            command=self._toggle_password_visibility
        )
        self.show_pwd_chk.pack(side="right")
        
        # Buttons
        btns_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btns_frame.pack(fill="x", pady=20)
        
        save_creds_btn = ctk.CTkButton(
            btns_frame, 
            text="Save Manually", 
            command=self._save_credentials_manually
        )
        save_creds_btn.pack(side="left", padx=(0, 10))
        
        self.scrape_creds_btn = ctk.CTkButton(
            btns_frame, 
            text="Fetch VPNBook Credentials Automatically", 
            fg_color="#1F85DE", 
            hover_color="#1F65AE",
            command=self.on_scrape_credentials
        )
        self.scrape_creds_btn.pack(side="left")

    def _build_ping_tab(self):
        """Builds the tab for testing server latencies."""
        frame = ctk.CTkFrame(self.tab_ping, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        desc = (
            "Select 'Test Latencies' to measure the latency of all server config files in configs/. "
            "Lower latency provides faster speeds. Selection mode 'latency' automatically uses the fastest."
        )
        desc_lbl = ctk.CTkLabel(frame, text=desc, font=ctk.CTkFont(size=12), justify="left", wraplength=500)
        desc_lbl.pack(anchor="w", pady=(0, 15))
        
        self.ping_btn = ctk.CTkButton(
            frame, 
            text="Test Server Latencies", 
            fg_color="#1F85DE", 
            hover_color="#1F65AE",
            command=self.on_test_latencies
        )
        self.ping_btn.pack(anchor="w", pady=(0, 15))
        
        # Scrollable textbox to show results
        self.ping_textbox = ctk.CTkTextbox(
            frame, 
            font=ctk.CTkFont(family="Consolas", size=11), 
            text_color="#D4D4D4", 
            fg_color="#1E1E1E"
        )
        self.ping_textbox.pack(fill="both", expand=True)
        self.ping_textbox.configure(state="disabled")

    # --- UI HELPERS & INTERACTION ACTIONS ---
    
    def _refresh_config_list(self):
        """Reads configuration files from settings.configs_dir and populates the dropdown."""
        configs = list_ovpn_configs(self.settings.configs_dir)
        options = ["Auto (Fastest Latency)", "Random"]
        for c in configs:
            options.append(format_server_display_name(c.name))
        
        current_selection = self.server_dropdown.get()
        self.server_dropdown.configure(values=options)
        if current_selection in options:
            self.server_dropdown.set(current_selection)
        else:
            self.server_dropdown.set("Auto (Fastest Latency)")
        
        self.logger.debug("Refreshed config list: %s files found.", len(configs))

    def _browse_openvpn_path(self):
        """Opens a file dialog to pick openvpn.exe."""
        initial_dir = r"C:\Program Files\OpenVPN\bin"
        if not os.path.exists(initial_dir):
            initial_dir = "C:\\"
        path = filedialog.askopenfilename(
            title="Select openvpn.exe",
            initialdir=initial_dir,
            filetypes=[("Executable Files", "*.exe")]
        )
        if path:
            self.setting_openvpn_path.delete(0, "end")
            self.setting_openvpn_path.insert(0, os.path.normpath(path))

    def _load_settings_into_ui(self):
        """Populates UI settings fields with values loaded from self.settings."""
        self.setting_openvpn_path.delete(0, "end")
        self.setting_openvpn_path.insert(0, str(self.settings.openvpn_path))
        
        self.setting_rotation_sec.delete(0, "end")
        self.setting_rotation_sec.insert(0, str(self.settings.rotation_seconds))
        
        self.setting_selection_mode.set(self.settings.selection_mode)
        
        if self.settings.avoid_same_server:
            self.setting_avoid_same.select()
        else:
            self.setting_avoid_same.deselect()
            
        if self.settings.force_udp:
            self.setting_force_udp.select()
        else:
            self.setting_force_udp.deselect()
            
        if self.settings.public_ip_check:
            self.setting_ip_check.select()
        else:
            self.setting_ip_check.deselect()
            
        self.setting_ip_url.delete(0, "end")
        self.setting_ip_url.insert(0, self.settings.public_ip_url)
        
        self.setting_timeout.delete(0, "end")
        self.setting_timeout.insert(0, str(self.settings.connect_timeout_seconds))
        
        self.setting_udp_timeout.delete(0, "end")
        self.setting_udp_timeout.insert(0, str(self.settings.udp_connect_timeout_seconds))

    def _save_settings_from_ui(self):
        """Reads settings inputs from UI, saves to settings.json, and reloads."""
        try:
            # Parse settings.json
            with open(self.settings_path, "r", encoding="utf-8") as f:
                raw_settings = json.load(f)
            
            # Update values
            raw_settings["openvpn_path"] = self.setting_openvpn_path.get().strip()
            raw_settings["rotation_seconds"] = int(self.setting_rotation_sec.get().strip())
            raw_settings["selection_mode"] = self.setting_selection_mode.get()
            raw_settings["avoid_same_server"] = bool(self.setting_avoid_same.get())
            raw_settings["force_udp"] = bool(self.setting_force_udp.get())
            raw_settings["public_ip_check"] = bool(self.setting_ip_check.get())
            raw_settings["public_ip_url"] = self.setting_ip_url.get().strip()
            raw_settings["connect_timeout_seconds"] = int(self.setting_timeout.get().strip())
            raw_settings["udp_connect_timeout_seconds"] = int(self.setting_udp_timeout.get().strip())
            
            # Save back
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump(raw_settings, f, indent=2)
            
            # Reload
            self.settings = load_settings(self.settings_path)
            self.manager = VpnManager(self.settings, self.logger)
            self.logger.info("Settings saved successfully!")
            messagebox.showinfo("Success", "Settings saved and loaded successfully!")
        except Exception as e:
            self.logger.error("Failed to save settings: %s", e)
            messagebox.showerror("Error", f"Failed to save settings: {e}")

    def _load_credentials_into_ui(self):
        """Loads credentials from auth.txt into credentials fields."""
        if self.settings.auth_file and self.settings.auth_file.exists():
            try:
                lines = self.settings.auth_file.read_text(encoding="utf-8").splitlines()
                if len(lines) >= 2:
                    self.creds_username.delete(0, "end")
                    self.creds_username.insert(0, lines[0].strip())
                    
                    self.creds_password.delete(0, "end")
                    self.creds_password.insert(0, lines[1].strip())
            except Exception as e:
                self.logger.warning("Failed to load credentials from file: %s", e)

    def _toggle_password_visibility(self):
        """Toggles password entry show/hide."""
        if self.show_pwd_var.get():
            self.creds_password.configure(show="")
        else:
            self.creds_password.configure(show="*")

    def _save_credentials_manually(self):
        """Saves manually entered credentials to auth.txt."""
        username = self.creds_username.get().strip()
        password = self.creds_password.get().strip()
        if not username or not password:
            messagebox.showerror("Error", "Username and Password cannot be empty.")
            return
        
        if not self.settings.auth_file:
            messagebox.showerror("Error", "Auth file is not configured in settings.")
            return
            
        if update_auth_file(self.settings.auth_file, username, password, self.logger):
            messagebox.showinfo("Success", "Credentials saved successfully to auth.txt.")
        else:
            messagebox.showerror("Error", "Failed to write credentials to auth.txt.")

    def _on_log_source_changed(self, choice):
        """Resets the log tracking when log source dropdown changes."""
        self.last_log_size = 0
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")
        self.log_textbox.configure(state="disabled")
        if self.log_file_pointer:
            try:
                self.log_file_pointer.close()
            except Exception:
                pass
            self.log_file_pointer = None
        self.current_log_path = None

    def _clear_log_window(self):
        """Clears the log textbox widget."""
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")
        self.log_textbox.configure(state="disabled")

    # --- ACTIONS RUNNING IN THREADS ---

    def _get_active_rotator_pid(self) -> int | None:
        try:
            state = _read_rotator_state(self.settings)
            if not state:
                return None
            pid_raw = state.get("pid")
            if pid_raw is None:
                return None
            return int(pid_raw)
        except (ValueError, TypeError):
            return None

    def on_connect_once(self):
        """Fires off the connect_once thread."""
        if self.is_connecting:
            return
        
        # Get selected configuration
        selection = self.server_dropdown.get()
        configs = list_ovpn_configs(self.settings.configs_dir)
        if not configs:
            messagebox.showerror("Error", f"No config files (.ovpn) found in {self.settings.configs_dir}")
            return
            
        # Check if auth file exists
        if not self.settings.auth_file or not self.settings.auth_file.exists():
            if messagebox.askyesno("Credentials Missing", "Credentials file (auth.txt) is missing. Try to fetch free credentials from VPNBook?"):
                self.on_scrape_credentials()
            return

        # Stop background rotator if it is running to prevent race conditions
        rotator_pid = self._get_active_rotator_pid()
        if rotator_pid and _is_pid_running(rotator_pid):
            if messagebox.askyesno("Stop Rotator", "The background rotator is active. We need to stop it before making a direct connection. Proceed?"):
                _stop_background(self.settings)
            else:
                return

        # Disable buttons
        self._set_ui_connecting_state(True, "Selecting server...")
        
        # Decide which config path to use
        threading.Thread(
            target=self._run_connect_once_thread,
            args=(selection, configs),
            daemon=True
        ).start()

    def _run_connect_once_thread(self, selection: str, configs: list[Path]):
        try:
            target_config = None
            if selection == "Random":
                target_config = choose_config(configs, None, False, "random")
            elif selection == "Auto (Fastest Latency)":
                self.after(0, lambda: self.loading_lbl.configure(text="Pinging servers to find the fastest..."))
                target_config = choose_config(configs, None, False, "latency")
            else:
                for c in configs:
                    if c.name == selection or format_server_display_name(c.name) == selection:
                        target_config = c
                        break
            
            if not target_config:
                raise FileNotFoundError(f"Configuration {selection} not found.")

            self.connecting_server_name = target_config.name
            self.after(0, lambda: self.loading_lbl.configure(text=f"Connecting to {format_server_display_name(target_config.name)}..."))
            
            # Disconnect active VPN first
            self.manager.disconnect()
            
            # Connect
            self.manager.connect(target_config, retry_on_auth_fail=True)
            self.logger.info("Connected to %s successfully!", target_config.name)
            self.after(0, lambda: messagebox.showinfo("Success", f"Connected to {target_config.name}!"))
        except Exception as e:
            self.logger.error("Connection failed: %s", e)
            self.after(0, lambda: messagebox.showerror("Connection Error", str(e)))
        finally:
            self.after(0, lambda: self._set_ui_connecting_state(False))

    def on_stop_all(self):
        """Disconnects OpenVPN and kills the background rotator."""
        self._set_ui_connecting_state(True, "Stopping all services...")
        threading.Thread(target=self._run_stop_all_thread, daemon=True).start()

    def _run_stop_all_thread(self):
        try:
            _stop_background(self.settings)
            self.manager.disconnect()
            self.logger.info("Stopped all VPN and Rotator processes.")
        except Exception as e:
            self.logger.error("Error stopping: %s", e)
        finally:
            self.after(0, lambda: self._set_ui_connecting_state(False))

    def on_start_rotator(self):
        """Launches the background rotator process."""
        # Check if already active
        rotator_pid = self._get_active_rotator_pid()
        if rotator_pid and _is_pid_running(rotator_pid):
            messagebox.showinfo("Info", "Background rotator is already active.")
            return

        # Start background worker
        self._set_ui_connecting_state(True, "Starting background rotator...")
        
        # Check if auth file exists
        if not self.settings.auth_file or not self.settings.auth_file.exists():
            # If credentials don't exist, prompt scraping or manually inputting
            if messagebox.askyesno("Credentials Missing", "Credentials file (auth.txt) is missing. Try to fetch free credentials from VPNBook?"):
                self.after(0, self.on_scrape_credentials)
                self._set_ui_connecting_state(False)
                return
            else:
                self._set_ui_connecting_state(False)
                return

        ret = _start_background(self.settings)
        if ret == 0:
            self.logger.info("Background rotator process launched.")
        else:
            self.logger.error("Failed to start background rotator.")
            messagebox.showerror("Error", "Failed to start background rotator.")
        
        self._set_ui_connecting_state(False)

    def on_rotate_now(self):
        """Forces an immediate rotation to another server."""
        rotator_pid = self._get_active_rotator_pid()
        if rotator_pid and _is_pid_running(rotator_pid):
            # If the background rotator is running, the best way to rotate is by stopping and starting it,
            # or we can let the rotator process manage it by running a disconnect.
            # Wait, if we disconnect the VPN state, the background rotator scheduler might wait for its next schedule,
            # or it might attempt to re-connect. Let's see: the background scheduler has time.sleep(rotation_seconds).
            # So a sleep is blocking it!
            # Therefore, to rotate *immediately* while background scheduler is running:
            # We can kill the background rotator, connect to the next server, and restart the background rotator!
            # This is extremely robust and ensures the schedule restarts.
            if messagebox.askyesno("Force Rotate", "The background rotator is active. We need to restart the rotator with a new server. Proceed?"):
                self._set_ui_connecting_state(True, "Restarting rotator on a new server...")
                threading.Thread(target=self._run_rotate_with_rotator_thread, daemon=True).start()
            return
            
        # If rotator is not running, we simply choose a new server and connect.
        current = self.manager.read_state()
        configs = list_ovpn_configs(self.settings.configs_dir)
        if not configs:
            messagebox.showerror("Error", "No config files found.")
            return
            
        self._set_ui_connecting_state(True, "Rotating to next server...")
        threading.Thread(
            target=self._run_rotate_single_thread, 
            args=(current, configs), 
            daemon=True
        ).start()

    def _run_rotate_with_rotator_thread(self):
        try:
            _stop_background(self.settings)
            self.manager.disconnect()
            
            # Select new config
            configs = list_ovpn_configs(self.settings.configs_dir)
            target_config = choose_config(configs, None, self.settings.avoid_same_server, self.settings.selection_mode)
            
            # Connect once to establish tunnel
            self.manager.connect(target_config)
            
            # Start background rotator
            _start_background(self.settings)
        except Exception as e:
            self.logger.error("Rotation failed: %s", e)
            self.after(0, lambda: messagebox.showerror("Rotation Error", str(e)))
        finally:
            self.after(0, lambda: self._set_ui_connecting_state(False))

    def _run_rotate_single_thread(self, current: VpnState | None, configs: list[Path]):
        try:
            previous = current.config if current else None
            config = choose_config(configs, previous, self.settings.avoid_same_server, self.settings.selection_mode)
            self.connecting_server_name = config.name
            
            self.manager.disconnect()
            self.manager.connect(config)
            self.logger.info("Rotated to %s successfully!", config.name)
        except Exception as e:
            self.logger.error("Rotation failed: %s", e)
            self.after(0, lambda: messagebox.showerror("Rotation Error", str(e)))
        finally:
            self.after(0, lambda: self._set_ui_connecting_state(False))

    def on_scrape_credentials(self):
        """Scrapes credentials from VPNBook in a background thread."""
        self._set_ui_connecting_state(True, "Scraping VPNBook credentials...")
        self.scrape_creds_btn.configure(state="disabled")
        threading.Thread(target=self._run_scrape_credentials_thread, daemon=True).start()

    def _run_scrape_credentials_thread(self):
        try:
            creds = fetch_vpnbook_credentials(self.logger)
            if creds:
                username, password = creds
                if not self.settings.auth_file:
                    raise ValueError("Auth file path not configured in settings.")
                
                # Write to auth file
                if update_auth_file(self.settings.auth_file, username, password, self.logger):
                    # Load back into GUI fields
                    self.after(0, lambda: self._update_credentials_fields(username, password))
                    self.after(0, lambda: messagebox.showinfo("Success", "VPNBook credentials fetched and updated!"))
                else:
                    raise OSError("Failed to write to auth file.")
            else:
                raise RuntimeError("Failed to fetch credentials from vpnbook.com. Is the site accessible?")
        except Exception as e:
            self.logger.error("Scraping failed: %s", e)
            self.after(0, lambda: messagebox.showerror("Scraping Error", str(e)))
        finally:
            self.after(0, lambda: self.scrape_creds_btn.configure(state="normal"))
            self.after(0, lambda: self._set_ui_connecting_state(False))

    def _update_credentials_fields(self, username, password):
        self.creds_username.delete(0, "end")
        self.creds_username.insert(0, username)
        self.creds_password.delete(0, "end")
        self.creds_password.insert(0, password)

    def on_test_latencies(self):
        """Measures latencies of all available configs in a background thread."""
        configs = list_ovpn_configs(self.settings.configs_dir)
        if not configs:
            messagebox.showerror("Error", "No config files found to test.")
            return
            
        self.ping_btn.configure(state="disabled")
        self.ping_textbox.configure(state="normal")
        self.ping_textbox.delete("1.0", "end")
        self.ping_textbox.insert("end", "Testing server ping responses...\n\n")
        self.ping_textbox.configure(state="disabled")
        
        threading.Thread(target=self._run_test_latencies_thread, args=(configs,), daemon=True).start()

    def _run_test_latencies_thread(self, configs: list[Path]):
        results = []
        for index, config in enumerate(configs):
            self.after(0, lambda idx=index: self._update_ping_status(f"Pinging server {idx+1}/{len(configs)}..."))
            remote = extract_remote_host(config)
            if remote:
                host, port = remote
                lat = measure_latency(host, port)
                if lat < 99.0:
                    results.append((config.name, f"{lat*1000:.1f} ms"))
                else:
                    results.append((config.name, "Timeout / Offline"))
            else:
                results.append((config.name, "No host info"))
                
        # Sort results: show fastest first
        results.sort(key=lambda x: float(x[1].split()[0]) if "ms" in x[1] else 9999.0)
        
        self.after(0, lambda: self._display_ping_results(results))

    def _update_ping_status(self, text):
        self.ping_textbox.configure(state="normal")
        self.ping_textbox.insert("end", f"{text}\n")
        self.ping_textbox.see("end")
        self.ping_textbox.configure(state="disabled")

    def _display_ping_results(self, results):
        self.ping_textbox.configure(state="normal")
        self.ping_textbox.delete("1.0", "end")
        self.ping_textbox.insert("end", f"{'Server Configuration File':<40} | {'Latency (Ping)':<15}\n")
        self.ping_textbox.insert("end", "-" * 60 + "\n")
        for server, latency in results:
            self.ping_textbox.insert("end", f"{server:<40} | {latency:<15}\n")
        self.ping_textbox.configure(state="disabled")
        self.ping_btn.configure(state="normal")

    def _set_ui_connecting_state(self, is_connecting: bool, label_text: str = ""):
        self.is_connecting = is_connecting
        if is_connecting:
            self.connect_once_btn.configure(state="disabled")
            self.start_rotator_btn.configure(state="disabled")
            self.rotate_now_btn.configure(state="disabled")
            self.stop_vpn_btn.configure(state="disabled")
            self.loading_lbl.configure(text=label_text)
        else:
            self.connect_once_btn.configure(state="normal")
            self.start_rotator_btn.configure(state="normal")
            self.rotate_now_btn.configure(state="normal")
            self.stop_vpn_btn.configure(state="normal")
            self.loading_lbl.configure(text="")

    # --- PERIODIC POLLING LOOPS ---

    def update_status_loop(self):
        """Polls state files and updates GUI labels every 1 second."""
        try:
            # 1. Read VPN State
            vpn_state = self.manager.read_state()
            is_vpn_active = False
            
            if vpn_state and _is_pid_running(vpn_state.pid):
                is_vpn_active = True
                self.active_server_val.configure(text=format_server_display_name(vpn_state.config))
                self.public_ip_val.configure(text=vpn_state.public_ip or "Connecting...")
                
                # Compute elapsed time (Uptime)
                try:
                    started = datetime.fromisoformat(vpn_state.started_at)
                    elapsed = datetime.now() - started
                    # Format as hh:mm:ss
                    sec = int(elapsed.total_seconds())
                    h, m = divmod(sec, 3600)
                    m, s = divmod(m, 60)
                    self.uptime_val.configure(text=f"{h:02d}:{m:02d}:{s:02d}")
                except Exception:
                    self.uptime_val.configure(text="Unknown")
            else:
                self.active_server_val.configure(text="None")
                self.public_ip_val.configure(text="Unknown")
                self.uptime_val.configure(text="00:00:00")
            
            # 2. Read Rotator State
            rotator_pid = self._get_active_rotator_pid()
            is_rotator_active = False
            
            if rotator_pid and _is_pid_running(rotator_pid):
                is_rotator_active = True
                self.rotator_val.configure(text=f"Active (PID {rotator_pid})", text_color="#2E7D32")
                self.start_rotator_btn.configure(text="Rotator Active", state="disabled")
            else:
                self.rotator_val.configure(text="Inactive", text_color="gray")
                if not self.is_connecting:
                    self.start_rotator_btn.configure(text="Start Rotator", state="normal")
            
            # 3. Update Status Indicator & Color
            if self.is_connecting:
                self.status_indicator.configure(text="Connecting...", text_color="#FFA726")
                self.status_card.configure(border_color="#FFA726")
                if self.connecting_server_name:
                    self.active_server_val.configure(text=format_server_display_name(self.connecting_server_name))
            elif is_vpn_active:
                self.status_indicator.configure(text="Connected", text_color="#66BB6A")
                self.status_card.configure(border_color="#66BB6A")
            else:
                self.status_indicator.configure(text="Disconnected", text_color="#EF5350")
                self.status_card.configure(border_color="#3F3F3F")
                
            # 4. Handle countdown progress
            if is_rotator_active and vpn_state and vpn_state.next_rotation_at:
                try:
                    next_rot = datetime.fromisoformat(vpn_state.next_rotation_at)
                    time_left = next_rot - datetime.now()
                    seconds_left = int(time_left.total_seconds())
                    
                    if seconds_left > 0:
                        m, s = divmod(seconds_left, 60)
                        self.countdown_val.configure(text=f"{m}m {s:02d}s")
                        
                        # Calculate progress bar percentage
                        total_time = self.settings.rotation_seconds
                        pct = max(0.0, min(1.0, 1.0 - (seconds_left / total_time)))
                        self.rotation_progress.set(pct)
                    else:
                        self.countdown_val.configure(text="Rotating...")
                        self.rotation_progress.set(1.0)
                except Exception:
                    self.countdown_val.configure(text="Unknown")
                    self.rotation_progress.set(0)
            else:
                self.countdown_val.configure(text="N/A")
                self.rotation_progress.set(0)

        except Exception as e:
            self.logger.warning("Error in status update loop: %s", e)
            
        # Re-schedule in 1000ms
        self.after(1000, self.update_status_loop)

    def update_logs_loop(self):
        """Tails the active log file and updates the text area in real-time."""
        try:
            # Determine which log file we are viewing
            selected_source = self.log_source_dropdown.get()
            target_path = None
            
            if selected_source == "Rotator Background Log":
                target_path = self.settings.logs_dir / "rotator_background.log"
            else:
                # Active VPN Log
                vpn_state = self.manager.read_state()
                if vpn_state and vpn_state.config:
                    target_path = self.settings.logs_dir / f"openvpn-{Path(vpn_state.config).stem}.log"
            
            if target_path and target_path.exists():
                # Check if path changed
                if self.current_log_path != target_path:
                    self._on_log_source_changed(selected_source)
                    self.current_log_path = target_path
                    self.last_log_size = 0
                
                # Check file size to see if it was appended
                current_size = target_path.stat().st_size
                if current_size < self.last_log_size:
                    # Log file was rotated/truncated, clear view
                    self._clear_log_window()
                    self.last_log_size = 0
                    
                if current_size > self.last_log_size:
                    with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(self.last_log_size)
                        new_content = f.read()
                        
                    self.last_log_size = current_size
                    
                    if new_content:
                        self.log_textbox.configure(state="normal")
                        self.log_textbox.insert("end", new_content)
                        if self.autoscroll_var.get():
                            self.log_textbox.see("end")
                        self.log_textbox.configure(state="disabled")
            else:
                # Log file doesn't exist
                if self.current_log_path is not None:
                    self._clear_log_window()
                    self.current_log_path = None
                    self.last_log_size = 0
                    
        except Exception as e:
            pass
            
        # Re-schedule in 1000ms
        self.after(1000, self.update_logs_loop)

    def destroy(self):
        """Called when closing the app."""
        if self.log_file_pointer:
            try:
                self.log_file_pointer.close()
            except Exception:
                pass
        super().destroy()

def run_gui(settings_path: Path):
    app = VpnPrivateApp(settings_path)
    app.mainloop()
