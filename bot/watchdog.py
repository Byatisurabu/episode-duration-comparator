# bot/watchdog.py
#
# Сторож процесса бота. Ловит "тихие" зависания, при которых процесс жив, но
# апдейты не обрабатываются — на них Docker-овский restart: unless-stopped не
# срабатывает, потому что контейнер не падает.
#
# Два уровня:
#   1. Корутина в event loop раз в HEARTBEAT_INTERVAL обновляет метку времени.
#   2. Отдельный поток проверяет эту метку. Если event loop не отвечает дольше
#      STALL_TIMEOUT (кто-то заблокировал его синхронным вызовом) или polling
#      остановился сам по себе — завершает процесс с кодом 1. Docker поднимает
#      контейнер заново.
#
# Поток нужен именно потому, что при заблокированном event loop никакая
# корутина уже не выполнится.

import asyncio
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 10   # сек — как часто event loop отмечается
STALL_TIMEOUT = 120       # сек — сколько event loop может молчать до перезапуска
CHECK_INTERVAL = 15       # сек — как часто поток-сторож проверяет метку


class Watchdog:
    def __init__(self, application, stall_timeout: float = STALL_TIMEOUT):
        self._application = application
        self._stall_timeout = stall_timeout
        self._last_beat = time.monotonic()
        self._stopped = threading.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._last_beat = time.monotonic()
        self._task = asyncio.get_running_loop().create_task(self._heartbeat())
        threading.Thread(target=self._watch, name="bot-watchdog", daemon=True).start()
        logger.info("Watchdog запущен (порог зависания %d с)", self._stall_timeout)

    def stop(self) -> None:
        """Штатная остановка (в т.ч. /restart) — сторож больше не вмешивается."""
        self._stopped.set()
        if self._task:
            self._task.cancel()

    async def _heartbeat(self) -> None:
        while True:
            self._last_beat = time.monotonic()
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    def check(self) -> str | None:
        """Возвращает причину для перезапуска или None, если всё в порядке."""
        stalled_for = time.monotonic() - self._last_beat
        if stalled_for > self._stall_timeout:
            return f"event loop не отвечает {stalled_for:.0f} с"
        updater = self._application.updater
        if self._application.running and updater is not None and not updater.running:
            return "polling остановился"
        return None

    def _watch(self) -> None:
        while not self._stopped.wait(CHECK_INTERVAL):
            reason = self.check()
            if reason and not self._stopped.is_set():
                logger.critical("Watchdog: %s — перезапускаю процесс", reason)
                logging.shutdown()
                # os._exit, а не sys.exit: из стороннего потока sys.exit завершит
                # только этот поток, а event loop так и останется висеть
                os._exit(1)
