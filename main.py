import os
import socket
import struct
import select
import time
import threading
import ipaddress
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.progressbar import ProgressBar
from kivy.clock import Clock
from kivy.uix.popup import Popup
from kivy.core.window import Window
from android.permissions import request_permissions, Permission  # noqa: F401
from android.storage import primary_external_storage_path       # noqa: F401

# ── ICMP ping (raw socket — требует INTERNET permission) ──────────────────────

def checksum(data: bytes) -> int:
    s = 0
    for i in range(0, len(data) - 1, 2):
        s += (data[i] << 8) + data[i + 1]
    if len(data) % 2:
        s += data[-1] << 8
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


def icmp_ping(host: str, timeout: float = 1.5) -> bool:
    """
    True = хост ответил на ICMP echo-request.
    Использует raw socket — максимально точно, не зависит от утилит.
    На Android требует только INTERNET permission (не ROOT) начиная с API 29+
    благодаря CAP_NET_RAW для unprivileged ICMP sockets.
    """
    ICMP_ECHO_REQUEST = 8
    pid = os.getpid() & 0xFFFF
    seq = 1

    # Заголовок: type(1) code(1) checksum(2) id(2) seq(2) + payload
    header = struct.pack("bbHHh", ICMP_ECHO_REQUEST, 0, 0, pid, seq)
    payload = b"abcdefgh"
    cs = checksum(header + payload)
    header = struct.pack("bbHHh", ICMP_ECHO_REQUEST, 0, socket.htons(cs), pid, seq)
    packet = header + payload

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
        sock.settimeout(timeout)
        sock.connect((host, 1))
        sock.send(packet)
        start = time.time()
        while True:
            ready = select.select([sock], [], [], max(0, timeout - (time.time() - start)))
            if not ready[0]:
                return False
            recv, _ = sock.recvfrom(1024)
            # IP-заголовок: 20 байт; ICMP начинается с 20
            icmp_type = recv[20]
            icmp_id   = struct.unpack("H", recv[24:26])[0]
            if icmp_type == 0 and icmp_id == socket.htons(pid):
                return True
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


# ── Парсинг CIDR ──────────────────────────────────────────────────────────────

def parse_cidrs(text: str):
    """Возвращает список ipaddress.IPv4Network."""
    nets = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            nets.append(ipaddress.IPv4Network(line, strict=False))
        except ValueError:
            pass
    return nets


def expand_networks(networks):
    """Разворачивает все сети в плоский список IP-строк."""
    ips = []
    for net in networks:
        ips.extend(str(ip) for ip in net.hosts())
    return ips


# ── Главный Layout ────────────────────────────────────────────────────────────

class CIDRPingerLayout(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=10, spacing=8, **kwargs)

        self._scan_thread = None
        self._stop_event  = threading.Event()
        self._results     = []          # список (ip, alive)
        self._total       = 0
        self._done        = 0
        self._lock        = threading.Lock()

        # ── Кнопка «Загрузить файл» ──
        btn_load = Button(text="📂 Загрузить файл CIDR", size_hint_y=None, height=48)
        btn_load.bind(on_release=self._load_file)
        self.add_widget(btn_load)

        # ── Текстовое поле для CIDR ──
        lbl_cidr = Label(text="Или вставьте CIDR вручную:", size_hint_y=None,
                         height=24, halign="left", valign="middle")
        lbl_cidr.bind(size=lbl_cidr.setter("text_size"))
        self.add_widget(lbl_cidr)

        self.cidr_input = TextInput(
            hint_text="51.250.100.0/24\n51.250.120.0/24\n...",
            size_hint_y=None, height=140, font_name="RobotoMono"
        )
        self.add_widget(self.cidr_input)

        # ── Параметры сканирования ──
        params_row = BoxLayout(size_hint_y=None, height=44, spacing=8)

        self.threads_input = TextInput(
            text="64", multiline=False,
            hint_text="Потоки", size_hint_x=0.3
        )
        self.timeout_input = TextInput(
            text="1.5", multiline=False,
            hint_text="Таймаут сек", size_hint_x=0.35
        )
        params_row.add_widget(Label(text="Потоки:", size_hint_x=0.2))
        params_row.add_widget(self.threads_input)
        params_row.add_widget(Label(text="Таймаут:", size_hint_x=0.25))
        params_row.add_widget(self.timeout_input)
        self.add_widget(params_row)

        # ── Кнопки СТАРТ / СТОП ──
        btn_row = BoxLayout(size_hint_y=None, height=48, spacing=8)
        self.btn_start = Button(text="▶ Начать сканирование",
                                background_color=(0.18, 0.7, 0.35, 1))
        self.btn_start.bind(on_release=self._start_scan)
        self.btn_stop = Button(text="■ Стоп",
                               background_color=(0.8, 0.2, 0.2, 1),
                               disabled=True)
        self.btn_stop.bind(on_release=self._stop_scan)
        btn_row.add_widget(self.btn_start)
        btn_row.add_widget(self.btn_stop)
        self.add_widget(btn_row)

        # ── Прогресс-бар ──
        self.progress = ProgressBar(max=100, value=0, size_hint_y=None, height=20)
        self.add_widget(self.progress)

        self.lbl_status = Label(text="Готов к работе", size_hint_y=None, height=24,
                                halign="center")
        self.add_widget(self.lbl_status)

        # ── Результаты ──
        lbl_res = Label(text="Результаты:", size_hint_y=None, height=24,
                        halign="left", valign="middle")
        lbl_res.bind(size=lbl_res.setter("text_size"))
        self.add_widget(lbl_res)

        scroll = ScrollView()
        self.result_text = TextInput(
            readonly=True, multiline=True,
            hint_text="Результаты появятся здесь...",
            font_name="RobotoMono", font_size=13
        )
        scroll.add_widget(self.result_text)
        self.add_widget(scroll)

        # ── Кнопки экспорта ──
        export_row = BoxLayout(size_hint_y=None, height=48, spacing=8)
        btn_copy_alive = Button(text="📋 Копировать живые IP")
        btn_copy_alive.bind(on_release=lambda *_: self._copy_results(only_alive=True))
        btn_copy_all = Button(text="📋 Копировать все")
        btn_copy_all.bind(on_release=lambda *_: self._copy_results(only_alive=False))
        btn_save = Button(text="💾 Сохранить файл")
        btn_save.bind(on_release=self._save_file)
        export_row.add_widget(btn_copy_alive)
        export_row.add_widget(btn_copy_all)
        export_row.add_widget(btn_save)
        self.add_widget(export_row)

    # ──────────────────────────────────────────────────────────────────────────
    # Загрузка файла
    # ──────────────────────────────────────────────────────────────────────────

    def _load_file(self, *_):
        """Простой file-picker через Popup."""
        from android.storage import primary_external_storage_path
        base = primary_external_storage_path()

        layout = BoxLayout(orientation="vertical", spacing=4, padding=8)
        scroll = ScrollView()
        file_list = BoxLayout(orientation="vertical", size_hint_y=None, spacing=2)
        file_list.bind(minimum_height=file_list.setter("height"))

        popup = Popup(title="Выберите файл", content=layout,
                      size_hint=(0.95, 0.9))

        def populate(path):
            file_list.clear_widgets()
            try:
                entries = sorted(os.listdir(path))
            except PermissionError:
                return
            for name in entries:
                full = os.path.join(path, name)
                btn = Button(text=("📁 " if os.path.isdir(full) else "📄 ") + name,
                             size_hint_y=None, height=44, halign="left",
                             text_size=(Window.width * 0.85, None))
                if os.path.isdir(full):
                    btn.bind(on_release=lambda b, p=full: populate(p))
                else:
                    btn.bind(on_release=lambda b, p=full: self._on_file_chosen(p, popup))
                file_list.add_widget(btn)

        populate(base)
        scroll.add_widget(file_list)
        layout.add_widget(scroll)
        btn_close = Button(text="Отмена", size_hint_y=None, height=44)
        btn_close.bind(on_release=popup.dismiss)
        layout.add_widget(btn_close)
        popup.open()

    def _on_file_chosen(self, path, popup):
        popup.dismiss()
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                self.cidr_input.text = f.read()
        except Exception as e:
            self._show_error(str(e))

    # ──────────────────────────────────────────────────────────────────────────
    # Сканирование
    # ──────────────────────────────────────────────────────────────────────────

    def _start_scan(self, *_):
        text = self.cidr_input.text.strip()
        if not text:
            self._show_error("Введите хотя бы один CIDR!")
            return

        networks = parse_cidrs(text)
        if not networks:
            self._show_error("Не удалось распознать ни одного CIDR.")
            return

        ips = expand_networks(networks)
        if not ips:
            self._show_error("Сети не содержат хостов (возможно, /32 без хостов).")
            return

        try:
            threads = max(1, min(256, int(self.threads_input.text)))
        except ValueError:
            threads = 64
        try:
            timeout = max(0.1, min(10.0, float(self.timeout_input.text)))
        except ValueError:
            timeout = 1.5

        self._results  = []
        self._total    = len(ips)
        self._done     = 0
        self._stop_event.clear()
        self.result_text.text = ""
        self.progress.value   = 0
        self.btn_start.disabled = True
        self.btn_stop.disabled  = False
        self.lbl_status.text = f"Сканирую {self._total} IP..."

        self._scan_thread = threading.Thread(
            target=self._scan_worker,
            args=(ips, threads, timeout),
            daemon=True
        )
        self._scan_thread.start()
        Clock.schedule_interval(self._update_ui, 0.5)

    def _scan_worker(self, ips, threads, timeout):
        sem = threading.Semaphore(threads)

        def probe(ip):
            if self._stop_event.is_set():
                sem.release()
                return
            alive = icmp_ping(ip, timeout)
            with self._lock:
                self._results.append((ip, alive))
                self._done += 1
            sem.release()

        ts = []
        for ip in ips:
            if self._stop_event.is_set():
                break
            sem.acquire()
            t = threading.Thread(target=probe, args=(ip,), daemon=True)
            t.start()
            ts.append(t)

        for t in ts:
            t.join()

    def _update_ui(self, dt):
        with self._lock:
            done  = self._done
            total = self._total
            snap  = list(self._results)

        alive_count = sum(1 for _, a in snap if a)
        pct = int(done / total * 100) if total else 0
        self.progress.value  = pct
        self.lbl_status.text = (
            f"{done}/{total} проверено  |  "
            f"{alive_count} живых  |  "
            f"{done - alive_count} нет ответа"
        )

        # Обновляем текст результатов
        lines = [
            f"{'✅' if a else '❌'}  {ip}"
            for ip, a in sorted(snap, key=lambda x: list(map(int, x[0].split("."))))
        ]
        self.result_text.text = "\n".join(lines)

        if self._scan_thread and not self._scan_thread.is_alive():
            Clock.unschedule(self._update_ui)
            self.btn_start.disabled = False
            self.btn_stop.disabled  = True
            self.lbl_status.text = (
                f"Готово! {alive_count} живых из {total}."
            )

    def _stop_scan(self, *_):
        self._stop_event.set()
        self.lbl_status.text = "Останавливаем..."

    # ──────────────────────────────────────────────────────────────────────────
    # Экспорт
    # ──────────────────────────────────────────────────────────────────────────

    def _copy_results(self, only_alive=True):
        with self._lock:
            snap = list(self._results)
        if only_alive:
            text = "\n".join(ip for ip, a in snap if a)
        else:
            text = "\n".join(
                f"{'ALIVE' if a else 'DEAD'} {ip}"
                for ip, a in sorted(snap, key=lambda x: list(map(int, x[0].split("."))))
            )
        from kivy.core.clipboard import Clipboard
        Clipboard.copy(text)
        self.lbl_status.text = "Скопировано в буфер обмена!"

    def _save_file(self, *_):
        from android.storage import primary_external_storage_path
        base = primary_external_storage_path()
        path = os.path.join(base, "cidr_ping_results.txt")
        with self._lock:
            snap = list(self._results)
        lines = [
            f"{'ALIVE' if a else 'DEAD'} {ip}"
            for ip, a in sorted(snap, key=lambda x: list(map(int, x[0].split("."))))
        ]
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self.lbl_status.text = f"Сохранено: {path}"
        except Exception as e:
            self._show_error(str(e))

    def _show_error(self, msg):
        popup = Popup(title="Ошибка",
                      content=Label(text=msg, text_size=(Window.width * 0.8, None),
                                    halign="center"),
                      size_hint=(0.85, 0.35))
        popup.open()


# ── App ───────────────────────────────────────────────────────────────────────

class CIDRPingerApp(App):
    def build(self):
        self.title = "CIDR Pinger"
        request_permissions([
            Permission.INTERNET,
            Permission.READ_EXTERNAL_STORAGE,
            Permission.WRITE_EXTERNAL_STORAGE,
        ])
        Window.clearcolor = (0.1, 0.1, 0.12, 1)
        return CIDRPingerLayout()


if __name__ == "__main__":
    CIDRPingerApp().run()
