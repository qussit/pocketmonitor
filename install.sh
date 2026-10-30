#!/usr/bin/env bash
#
# second-monitor — установщик
# Телефон (Android) как второй монитор для Hyprland (Wayland), по USB.
#
set -euo pipefail

BIN="$HOME/.local/bin"
SHARE="$HOME/.local/share/second-monitor"
HERE="$(cd "$(dirname "$0")" && pwd)"

info() { printf '\033[1;36m[install]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[install]\033[0m %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# --- 1. зависимости ------------------------------------------------------

detect_pm() {
    have pacman && { echo pacman; return; }
    have apt-get && { echo apt; return; }
    have dnf && { echo dnf; return; }
    echo none
}

install_deps() {
    info "проверяю зависимости..."
    local pm
    pm="$(detect_pm)"

    # имя пакета зависит от дистрибутива
    local adb_pkg python_aiohttp_pkg
    case "$pm" in
        pacman) adb_pkg=android-tools      ; python_aiohttp_pkg=python-aiohttp ;;
        apt)    adb_pkg=adb                ; python_aiohttp_pkg=python3-aiohttp ;;
        dnf)    adb_pkg=android-tools      ; python_aiohttp_pkg=python3-aiohttp ;;
        *)      adb_pkg=android-tools      ; python_aiohttp_pkg=python3-aiohttp ;;
    esac

    local missing=()
    have hyprctl     || missing+=(hyprland)
    have wf-recorder || missing+=(wf-recorder)
    have jq          || missing+=(jq)
    have adb         || missing+=("$adb_pkg")
    have python3     || missing+=(python3)
    have gcc         || missing+=(gcc)

    if [ ${#missing[@]} -gt 0 ]; then
        info "устанавливаю: ${missing[*]}"
        case "$pm" in
            pacman) sudo pacman -S --needed "${missing[@]}" ;;
            apt)    sudo apt-get update && sudo apt-get install -y "${missing[@]}" ;;
            dnf)    sudo dnf install -y "${missing[@]}" ;;
            *)      die "не найден пакетный менеджер — установи вручную: ${missing[*]}" ;;
        esac
    fi

    # aiohttp (для тач-сервера)
    if ! python3 -c 'import aiohttp' 2>/dev/null; then
        if python3 -m pip install --user aiohttp 2>/dev/null; then
            info "aiohttp установлен через pip"
        elif [ "$pm" != none ]; then
            info "ставлю $python_aiohttp_pkg через пакетный менеджер"
            case "$pm" in
                pacman) sudo pacman -S --needed "$python_aiohttp_pkg" ;;
                apt)    sudo apt-get install -y "$python_aiohttp_pkg" ;;
                dnf)    sudo dnf install -y "$python_aiohttp_pkg" ;;
            esac
        else
            die "установи aiohttp вручную: python3 -m pip install --user aiohttp"
        fi
    fi
}

# --- 2. сборка uinput-inject --------------------------------------------

build_uinput() {
    if [ -x "$BIN/uinput-inject" ]; then
        info "uinput-inject уже собран"
        return
    fi
    info "собираю uinput-inject (виртуальная мышь)..."
    gcc -O2 -Wall -o "$BIN/uinput-inject" "$HERE/uinput-inject.c"
}

# --- 3. установка файлов -------------------------------------------------

install_files() {
    info "устанавливаю файлы в ~/.local ..."
    mkdir -p "$BIN" "$SHARE"
    install -m755 "$HERE/second-monitor"  "$BIN/second-monitor"
    install -m644 "$HERE/touch-server.py" "$SHARE/touch-server.py"
    install -m644 "$HERE/index.html"      "$SHARE/index.html"
    install -m644 "$HERE/manifest.json"   "$SHARE/manifest.json"
    install -m644 "$HERE/sw.js"           "$SHARE/sw.js"
    install -m644 "$HERE/icon-192.png"    "$SHARE/icon-192.png"
    install -m644 "$HERE/icon-512.png"    "$SHARE/icon-512.png"
}

# --- 4. доступ к /dev/uinput --------------------------------------------

check_uinput() {
    if [ -w /dev/uinput ]; then
        info "/dev/uinput доступен на запись — тач будет работать"
        return
    fi
    cat <<'EOF'
[install] ВНИМАНИЕ: /dev/uinput недоступен на запись.
          Видео будет работать, но тач — нет. Чтобы включить тач (нужен sudo):
            echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' | sudo tee /etc/udev/rules.d/99-uinput.rules
            sudo udevadm control --reload && sudo udevadm trigger
            sudo usermod -aG input "$USER"
          …и перелогинься. Либо проще:
            sudo setfacl -m "u:$USER:rw" /dev/uinput
EOF
}

main() {
    install_deps
    build_uinput
    install_files
    check_uinput
    echo
    info "ГОТОВО."
    info "Запуск:  second-monitor start"
    info "Автозапуск в Hyprland:"
    info "   cp $HERE/hyprland/second-monitor.conf ~/.config/hypr/config/"
    info "   и добавь строку в ~/.config/hypr/hyprland.conf:"
    info "   source = ~/.config/hypr/config/second-monitor.conf"
}

main
