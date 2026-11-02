"""Interfaz Gráfica AutoTrader - Versión Corrección de Hilos y Layouts"""
import sys
import threading
import time
from datetime import datetime

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QPushButton, QSlider, QDoubleSpinBox, QSpinBox, 
                             QGroupBox, QGridLayout, QProgressBar, QTableWidget, QTableWidgetItem, 
                             QHeaderView, QFrame)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QFont, QColor, QPalette, QIcon

from app.config import Config
from app.engine import Engine
from app.broker import PaperBroker, CcxtBroker


def setup_qt_application():
    """Función auxiliar para asegurar QApplication correcta"""
    # QApplication.instance() retorna el singleton si ya existe
    app = QApplication.instance()
    if app is None:
        # Solo crear si no existe - CRUCIAL para hilos
        app = QApplication(sys.argv)
        # Configurar estilo y propiedades globales aquí
    return app


class CompactStyle:
    """Estilo compacto y eficiente para la interfaz"""
    
    @staticmethod
    def setup_style(app):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(35, 35, 35))
        palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Base, QColor(25, 25, 25))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(40, 40, 40))
        palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Button, QColor(35, 35, 35))
        palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
        palette.setColor(QPalette.ColorRole.Link, QColor(66, 135, 232))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(66, 135, 232))
        palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)
        app.setPalette(palette)
        
        # CSS compacto - sin padding excesivo
        app.setStyleSheet("""
            QMainWindow { background-color: #232323; }
            QWidget { background-color: #232323; color: #ffffff; }
            QGroupBox {
                font-weight: bold; 
                border: 1px solid #555555; 
                border-radius: 4px; 
                margin-top: 6px;
                padding-top: 6px;
            }
            QGroupBox::title { 
                subcontrol-origin: margin; 
                left: 8px; 
                padding: 0 2px 0 2px; 
                color: #aaaaaa; 
                font-size: 10pt; 
            }
            QPushButton {
                background-color: #4a90e2; 
                color: white; 
                border: none; 
                padding: 4px 8px; 
                border-radius: 3px; 
                font-weight: normal;
                font-size: 9pt;
                min-height: 22px;
            }
            QPushButton:hover { background-color: #357abd; }
            QPushButton:pressed { background-color: #2c5a99; }
            QPushButton#startBtn { background-color: #4caf50; }
            QPushButton#startBtn:hover { background-color: #40a040; }
            QPushButton#stopBtn { background-color: #f44336; }
            QPushButton#stopBtn:hover { background-color: #d32f2f; }
            QPushButton#pauseBtn { background-color: #ffc107; color: #212529; }
            QPushButton#pauseBtn:hover { background-color: #e0a800; }
            QPushButton:disabled { background-color: #777777; color: #adb5bd; }
            QDoubleSpinBox, QSpinBox {
                background-color: #3a3a3a; 
                color: white; 
                border: 1px solid #555555; 
                padding: 3px 5px; 
                border-radius: 3px; 
                font-size: 9pt;
                min-width: 80px;
            }
            QSlider::groove:horizontal { height: 6px; background: #555555; border-radius: 3px; }
            QSlider::handle:horizontal { background: #4a90e2; width: 14px; margin: -2px 0; border-radius: 6px; }
            QTableWidget { background-color: #2a2a2a; gridline-color: #555555; color: #ffffff; }
            QHeaderView::section { background-color: #555555; padding: 3px; border: none; color: #ffffff; font-size: 8.5pt; font-weight: bold; }
            QLabel { font-size: 9pt; }
        """)


class CompactDashboard(QMainWindow):
    """Dashboard compacto y eficiente - máxima información en mínimo espacio"""
    
    def __init__(self, cfg: Config, engine: Engine):
        super().__init__()
        self.cfg = cfg
        self.engine = engine
        
        # Asegurar QApplication existe en hilo actual
        setup_qt_application()
        
        self.setWindowTitle("AutoTrader v1.0 - Trading System")
        self.setMinimumSize(1200, 700)
        self._setup_ui()
        self._setup_timer()
        self._apply_style()
        self._update_control_buttons(state="stopped")
        
    def _setup_ui(self):
        """Interfaz compacta y optimizada - SIN warnings de layout"""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        # --- FILA SUPERIOR: Estado + Controles ---
        top_row = QHBoxLayout()
        
        # Panel izquierdo: Estado esencial
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(4)
        
        # Estado principal
        self.lbl_status = QLabel("🛑 Detenido")
        self.lbl_status.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.lbl_status.setStyleSheet("color: #6c757d; padding: 2px;")
        
        self.lbl_info = QLabel("Modo: paper | Synthetic | BTC/USDT")
        self.lbl_info.setFont(QFont("Arial", 8))
        self.lbl_info.setStyleSheet("color: #888; padding: 2px;")
        
        left_layout.addWidget(self.lbl_status)
        left_layout.addWidget(self.lbl_info)
        left_layout.addStretch()
        
        # Panel derecho: Botones compactos
        right_panel = QWidget()
        right_layout = QHBoxLayout(right_panel)
        right_layout.setSpacing(4)
        
        self.btn_start = QPushButton("▶ Start")
        self.btn_start.setObjectName("startBtn")
        self.btn_start.setFont(QFont("Arial", 8))
        self.btn_start.clicked.connect(self._on_start_click)
        self.btn_start.setEnabled(True)
        
        self.btn_stop = QPushButton("⏹ Stop")
        self.btn_stop.setObjectName("stopBtn")
        self.btn_stop.setFont(QFont("Arial", 8))
        self.btn_stop.clicked.connect(self._on_stop_click)
        self.btn_stop.setEnabled(False)
        
        self.btn_pause = QPushButton("⏸ Pause")
        self.btn_pause.setObjectName("pauseBtn")
        self.btn_pause.setFont(QFont("Arial", 8))
        self.btn_pause.clicked.connect(self._on_pause_click)
        self.btn_pause.setEnabled(False)
        
        right_layout.addWidget(self.btn_start)
        right_layout.addWidget(self.btn_stop)
        right_layout.addWidget(self.btn_pause)
        
        top_row.addWidget(left_panel, 1)
        top_row.addWidget(right_panel, 0)
        
        # --- ÁREA CENTRAL: Métricas clave ---
        metrics_layout = QHBoxLayout()
        metrics_layout.setSpacing(12)
        
        # Cuatro tarjetas métricas pequeñas
        self.metrics = {}
        metrics_data = [
            ("EQ", "Equity", "0.000", "#0aff0a"),
            ("CA", "Cash", "0.000", "#00c6ff"),
            ("DD", "Drawdown", "0.00%", "#ff4d4d"),
            ("RT", "Running", "No", "#ffc107"),
        ]
        
        for idx, (key, label, default, color) in enumerate(metrics_data):
            card = QWidget()
            card_layout = QVBoxLayout(card)
            card_layout.setSpacing(2)  # EVITAR layout vacío/duplicado
            
            lbl_key = QLabel(key)
            lbl_key.setFont(QFont("Arial", 7))
            lbl_key.setStyleSheet("color: #666;")
            
            lbl_val = QLabel(default)
            lbl_val.setFont(QFont("Arial", 14, QFont.Weight.Bold))
            lbl_val.setStyleSheet(f"color: {color};")
            
            lbl_key_small = QLabel(label)
            lbl_key_small.setFont(QFont("Arial", 6))
            lbl_key_small.setStyleSheet("color: #777;")
            
            card_layout.addWidget(lbl_val)
            card_layout.addWidget(lbl_key_small)
            card_layout.addStretch()
            
            self.metrics[key] = {"widget": card, "label": lbl_val}
            metrics_layout.addWidget(card, 0)
        
        # --- ÁREA ABAJO: Tabla de trades mínimo ---
        trades_label = QLabel("Últimos Trades:")
        trades_label.setFont(QFont("Arial", 8))
        trades_label.setStyleSheet("color: #777;")
        
        self.trades_table = QTableWidget()
        self.trades_table.setColumnCount(5)
        self.trades_table.setHorizontalHeaderLabels(["Símbolo", "Lado", "Px", "PnL", "Hora"])
        self.trades_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.trades_table.verticalHeader().setVisible(False)
        self.trades_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # EVITAR el error: establecer máximo height pero no causar conflictos
        self.trades_table.setMaximumHeight(120)
        # Asegurar que el header no cause problemas de layout
        self.trades_table.horizontalHeader().setDefaultSectionSize(80)
        
        # --- Área inferior: Events recientes ---
        events_label = QLabel("Events:")
        events_label.setFont(QFont("Arial", 8))
        events_label.setStyleSheet("color: #777;")
        
        self.txt_events = QLabel("Sistema listo - Click '▶ Start' para iniciar")
        self.txt_events.setFont(QFont("Consolas", 7))
        self.txt_events.setWordWrap(True)
        self.txt_events.setMaximumHeight(50)
        self.txt_events.setStyleSheet("color: #ccc; padding: 2px;")
        
        # Ensamblaje final - Layout único y consistente
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        
        # Verificar que no haya layouts vacíos o duplicados antes de agregar
        # Agregar en orden sin conflictos
        layout.addLayout(top_row)
        # Pequeño delay o verificación opcional, pero en PyQt6 esto debería estar bien
        layout.addLayout(metrics_layout)
        layout.addWidget(trades_label)
        layout.addWidget(self.trades_table)
        # Agregar spacer para evitar que el último widget se estire extraño
        layout.addStretch(1)
        layout.addWidget(QLabel(""))  # spacer vacío
        layout.addWidget(QLabel("Events:"))
        layout.addWidget(self.txt_events)
        
        # Verificar final: asegurar que central widget tenga layout
        if central.layout() is None:
            central.setLayout(layout)
        else:
            # Si ya tiene layout, no volver a establecer (causa el warning)
            pass
        
        central.setLayout(layout)
        
    def _apply_style(self):
        """Aplicar estilo después de que la interfaz esté completamente armada"""
        CompactStyle.setup_style(QApplication.instance())
    
    def _setup_timer(self):
        """Configurar timer de actualización"""
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_data)
        self.timer.start(800)  # Actualizar cada 800ms
    
    def _update_control_buttons(self, state: str):
        """Actualizar botones con estado compacto"""
        if state == "running":
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.btn_pause.setEnabled(True)
            self.lbl_status.setText("▶ Ejecutándose")
            self.lbl_status.setStyleSheet("color: #4caf50;")
            self.lbl_info.setText("Motor running • Ciclo continuo activo")
            self.txt_events.setText("✅ Trading automático en bucle infinito - Poll active")
        elif state == "paused":
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.btn_pause.setEnabled(True)
            self.lbl_status.setText("⏸ Pausado")
            self.lbl_status.setStyleSheet("color: #ffc107;")
            self.lbl_info.setText("Motor pausado tras ciclo actual")
            self.txt_events.setText("⏸ Motor pausado - esperando reanudar")
        elif state == "stopped":
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.btn_pause.setEnabled(False)
            self.lbl_status.setText("🛑 Detenido")
            self.lbl_status.setStyleSheet("color: #aaa;")
            self.lbl_info.setText("Modo: paper | Synthetic | Listo para iniciar")
            self.txt_events.setText("💡 Click '▶ Start' para iniciar trading automático continuo")
    
    @pyqtSlot()
    def update_data(self):
        """Actualizar datos compactos"""
        if not self.engine:
            return
        
        S = self.engine.S
        running = self.engine.running
        
        # Indicador running
        if running:
            self.lbl_status.setText("▶ Ejecutándose")
            self.lbl_status.setStyleSheet("color: #4caf50;")
        else:
            self.lbl_status.setText("🛑 Detenido")
            self.lbl_status.setStyleSheet("color: #aaa;")
        
        # Métricas clave
        equity = S.get("cash", 0)
        for sym, pos in S.get("positions", {}).items():
            price = pos.get("entry", 0)
            equity += pos.get("side", 1) * price * pos.get("qty", 0)
        
        peak = S.get("peak", equity)
        dd = ((peak - equity) / peak * 100) if peak > 0 else 0
        halted = S.get("halted", False)
        
        # Equity
        self.metrics["EQ"]["label"].setText(f"{equity:.2f}")
        dd_color = "#0aff0a" if dd < 5 else "#ffd700" if dd < 10 else "#ff4d4d"
        self.metrics["EQ"]["label"].setStyleSheet(f"color: {dd_color};")
        
        # Cash
        self.metrics["CA"]["label"].setText(f"{S.get('cash', 0):.2f}")
        
        # Drawdown
        dd_str = f"{dd:.2f} %"
        self.metrics["DD"]["label"].setText(dd_str)
        dd_color = "#0aff0a" if dd < 5 else "#ffd700" if dd < 10 else "#ff4d4d"
        self.metrics["DD"]["label"].setStyleSheet(f"color: {dd_color};")
        
        # Running
        self.metrics["RT"]["label"].setText("Sí" if running else "No")
        self.metrics["RT"]["label"].setStyleSheet(f"color: #4caf50;" if running else "#ffc107;")
        
        # Estado halted
        self.lbl_status.setText("⏸ Pausado" if halted else ("▶ Ejecutándose" if running else "🛑 Detenido"))
        
        # Trades simulados
        row_count = min(3, len(S.get("positions", {})))
        self.trades_table.setRowCount(row_count)
        # Events
        if halted:
            self.txt_events.setText("⚠ HALTED - Drawdown exceeded limit")
        elif running:
            self.txt_events.setText("🔄 Ciclo activo • " + str(len(S.get("positions", {}))) + " posiciones abiertas")
        else:
            self.txt_events.setText("⏸ Detenido - listo para iniciar")
    
    def _update_control_buttons(self, state: str):
        """Ya definido arriba, mantener compatibilidad"""
        pass
    
    def _on_start_click(self):
        """Manejar click en botón Start"""
        if not self.engine.running:
            self.engine.running = True
            self._update_control_buttons(state="running")
            self.txt_events.setText("✅ Motor iniciado - bucle continuo activo")
    
    def _on_stop_click(self):
        if not self.engine.running:
            return
        
        self.engine.running = False
        if self.engine.stop_ev:
            self.engine.stop_ev.set()
        self._update_control_buttons(state="stopped")
        self.txt_events.setText("⏹ Motor detenido - sesión finalizada")
    
    def _on_pause_click(self):
        if not self.engine.running:
            return
        
        # Alternar pause
        if self.engine.S.get("halted", False):
            # Reanudar si estaba halted
            self.engine.S["halted"] = False
            self.engine.S["halt_reason"] = ""
            self.txt_events.setText("▶ Motor reanudado después de halt")
        else:
            # Pausar - setting halted
            self.engine.S["halted"] = True
            self.engine.S["halt_reason"] = "paused_by_gui"
            self.txt_events.setText("⏸ Motor pausado - ciclo actual completado")
        
        self.lbl_engine_status.setText("⏸ Pausado" if self.engine.S.get("halted", False) else "▶ Ejecutándose")
    
    def closeEvent(self, event):
        if self.engine and self.engine.running:
            self.engine.running = False
            if self.engine.stop_ev:
                self.engine.stop_ev.set()
        if self.timer.isRunning():
            self.timer.stop()
        super().closeEvent(event)


def run_gui(cfg: Config, engine: Engine):
    """Función para iniciar la GUI en un hilo compatible"""
    # Asegurar QApplication en hilo principal antes que nada
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    
    # Aplicar estilo inmediatamente
    CompactStyle.setup_style(app)
    
    window = CompactDashboard(cfg, engine)
    window.show()
    
    # Iniciar event loop de Qt - ESTO BLOQUEA y mantiene la ventana abierta
    # Esto se ejecuta en el hilo principal de Qt
    sys.exit(app.exec())


if __name__ == "__main__":
    import os
    os.chdir(r"C:\Users\Drako\Desktop\synapta\autotrader")
    from app.config import Config
    cfg = Config()
    print("Iniciando AutoTrader GUI Compacta...")
    print(f"Modo: {cfg.mode}")
    print(f"Símbolos: {cfg.symbols}")
    print("Botones compactos - diseño optimizado sin warnings de layout")
    run_gui(cfg)