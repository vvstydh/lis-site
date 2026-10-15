#!/usr/bin/env bash
# Первичная настройка чистого сервера Ubuntu 22.04/24.04 под сайт. Запускать один раз от root:
#   ssh root@IP 'bash -s' < deploy/setup-server.sh
# Что делает: обновления, Docker, файрвол, пользователь deploy с SSH-ключом root,
# автоматические обновления безопасности, запрет входа по паролю (только если ключ точно есть).
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Запустите от root"; exit 1; fi
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a

echo "==> Обновляю систему"
apt-get update -q
apt-get -y -q upgrade
apt-get -y -q install ca-certificates curl ufw unattended-upgrades fail2ban rsync

echo "==> Ставлю Docker"
if ! command -v docker >/dev/null; then
    curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

echo "==> Файрвол: открыты только SSH, 80 и 443"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

echo "==> Автоматические обновления безопасности"
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> fail2ban: блокирует перебор паролей SSH"
systemctl enable --now fail2ban

echo "==> Пользователь deploy (sudo + docker)"
if ! id deploy >/dev/null 2>&1; then
    adduser --disabled-password --gecos "" deploy
fi
usermod -aG sudo,docker deploy
echo "deploy ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/deploy
chmod 440 /etc/sudoers.d/deploy

if [[ -s /root/.ssh/authorized_keys ]]; then
    install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
    install -m 600 -o deploy -g deploy /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
    mkdir -p /home/deploy/lis_site && chown deploy:deploy /home/deploy/lis_site

    echo "==> SSH: вход только по ключу, root не пускаем"
    # 00- — чтобы стоять раньше 50-cloud-init.conf: в sshd побеждает первое значение
    cat > /etc/ssh/sshd_config.d/00-lis-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
    sshd -t && (systemctl reload ssh 2>/dev/null || systemctl reload sshd)
    echo "Готово. Дальше входите так: ssh deploy@$(hostname -I | awk '{print $1}')"
else
    echo "!!! У root нет SSH-ключа (/root/.ssh/authorized_keys пуст)."
    echo "!!! Вход по паролю НЕ отключён, чтобы вы не потеряли доступ. Добавьте ключ (ssh-copy-id) и запустите скрипт ещё раз."
fi
