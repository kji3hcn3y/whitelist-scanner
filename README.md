# CIDR Pinger — APK

Android-приложение: загружает файл с CIDR4-подсетями, пингует каждый IP
(настоящий ICMP raw socket — максимальная точность), показывает живые/мёртвые
и экспортирует результат.

---

## Как собрать APK (без Android Studio)

### Способ 1 — GitHub Actions (рекомендую, 0 настроек локально)

1. Создай репозиторий на GitHub и залей все файлы этого проекта.
2. Перейди на вкладку **Actions** → выбери **Build APK** → нажми **Run workflow**.
3. После успешной сборки (≈ 15–25 мин) скачай APK во вкладке **Artifacts**.

### Способ 2 — Локально на Linux/macOS

```bash
# 1. Зависимости (Ubuntu)
sudo apt install git zip unzip openjdk-17-jdk \
    autoconf automake libtool libffi-dev libssl-dev build-essential

# 2. Buildozer
pip install buildozer cython

# 3. Собрать
cd cidr-pinger
buildozer android debug
# APK появится в ./bin/
```

### Способ 3 — Termux прямо на телефоне (без сборки APK)

```bash
pkg install python
pip install kivy  # опционально, можно запустить и консольно
python main.py    # консольный режим работает без Kivy
```

---

## Как работает пинг

Используется **ICMP raw socket** (не `ping` из shell):

- Формирует Echo Request вручную (struct + checksum).
- `select()` с таймаутом ловит Echo Reply.
- На Android API 29+ raw ICMP-сокеты разрешены **без root** для приложений
  с permission `INTERNET` — это официальная особенность ядра Linux.
- Параллельные потоки (по умолчанию 64) + настраиваемый таймаут дают
  баланс скорости и точности.

### Почему именно raw socket, а не `subprocess ping`?

| Метод | Точность | Скорость | Зависимости |
|---|---|---|---|
| Raw ICMP socket | ✅ Максимальная | ✅ Быстро | Только INTERNET |
| subprocess ping | ⚠️ Средняя | ⚠️ Медленно | Наличие /bin/ping |
| TCP connect | ❌ Неполная | ✅ Быстро | Открытый порт |

---

## Формат входного файла

Простой текстовый файл, одна подсеть на строку:

```
51.250.100.0/24
51.250.120.0/24
10.0.0.0/28
```

Строки начинающиеся с `#` — комментарии, игнорируются.

---

## Формат вывода

```
ALIVE 51.250.100.1
ALIVE 51.250.100.2
DEAD  51.250.100.3
...
```

Или только живые IP — кнопка «Копировать живые IP».
Файл сохраняется в `/sdcard/cidr_ping_results.txt`.
