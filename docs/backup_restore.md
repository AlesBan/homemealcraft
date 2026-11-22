# Бэкап и восстановление SQLite

## Автобэкап при деплое

`deploy/deploy.py` перед рестартом копирует `data/*.sqlite` в:

`data/backup-YYYYMMDD-HHMMSS-<имя>.sqlite`

Хранятся последние **10** файлов, старые удаляются.

## Ручной бэкап на VPS

```bash
ssh ales@ssh.ales-cloud.com
cd /home/ales/homemealcraft
sudo systemctl stop homemealcraft-bot.service
cp data/homemealcraft.sqlite "data/manual-$(date +%Y%m%d-%H%M%S).sqlite"
sudo systemctl start homemealcraft-bot.service
```

Останавливать сервис нужно, чтобы не копировать файл во время записи.

## Восстановление

```bash
cd /home/ales/homemealcraft
sudo systemctl stop homemealcraft-bot.service
# выбрать нужный backup-* файл
cp data/backup-YYYYMMDD-HHMMSS-homemealcraft.sqlite data/homemealcraft.sqlite
sudo systemctl start homemealcraft-bot.service
sudo journalctl -u homemealcraft-bot.service -n 20 --no-pager
```

## Диск

Если место кончается, деплой/бэкапы начнут падать. Проверка:

```bash
df -h /home/ales
du -sh /home/ales/homemealcraft/data
```

При заполнении >85% — удалите старые `data/backup-*` вручную или увеличьте диск.