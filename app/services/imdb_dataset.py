# app/services/imdb_dataset.py
#
# Локальная база эпизодов IMDb, собранная из официальных некоммерческих датасетов
# (https://developer.imdb.com/non-commercial-datasets/). GraphQL API IMDb закрыт
# для сторонних клиентов (403), поэтому длительности берём отсюда.
#
# Сборка: python -m app.services.imdb_dataset
#   — скачивает title.episode.tsv.gz и title.basics.tsv.gz,
#   — собирает data/imdb.db во временный файл и атомарно подменяет старый.
# Веб-приложение запускает сборку в отдельном процессе раз в сутки (см. app/main.py),
# бот только читает готовый файл через общий volume. Бот без веба базу не соберёт —
# тогда запускать сборку вручную (см. README).

import asyncio
import gzip
import logging
import os
import signal
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import aiosqlite
import httpx

logger = logging.getLogger(__name__)

DATASETS_URL = "https://datasets.imdbws.com/"
EPISODE_FILE = "title.episode.tsv.gz"
BASICS_FILE = "title.basics.tsv.gz"

DB_PATH = Path("data/imdb.db")
MAX_AGE_SECONDS = 24 * 60 * 60   # датасеты IMDb обновляются раз в сутки
CHECK_INTERVAL_SECONDS = 60 * 60

SERIES_TYPES = {"tvSeries", "tvMiniSeries"}
BATCH_SIZE = 50_000


class ImdbDatasetNotReady(Exception):
    """Локальная база IMDb ещё не собрана (первый запуск или ошибка сборки)."""


# ── ID: храним числом, наружу отдаём строкой ────────────────────────────────

def _id_to_int(tconst: str) -> int:
    return int(tconst[2:])


def _int_to_id(n: int) -> str:
    return f"tt{n:07d}"


# ── Сборка ──────────────────────────────────────────────────────────────────

def _read_tsv(path: Path) -> Iterator[list[str]]:
    """Построчно читает gz-TSV IMDb, пропуская заголовок. \\N — пустое значение."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        next(f)
        for line in f:
            yield line.rstrip("\n").split("\t")


def _batched(rows: Iterator[tuple]) -> Iterator[list[tuple]]:
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= BATCH_SIZE:
            yield batch
            batch = []
    if batch:
        yield batch


def _episode_rows(path: Path) -> Iterator[tuple]:
    # tconst, parentTconst, seasonNumber, episodeNumber
    for cols in _read_tsv(path):
        if cols[2] == r"\N" or cols[3] == r"\N":
            continue
        yield _id_to_int(cols[0]), _id_to_int(cols[1]), int(cols[2]), int(cols[3])


def _basics_rows(path: Path) -> Iterator[tuple[str, int, str, Optional[int]]]:
    # tconst, titleType, primaryTitle, originalTitle, isAdult, startYear, endYear, runtimeMinutes, genres
    for cols in _read_tsv(path):
        title_type = cols[1]
        if title_type == "tvEpisode":
            if cols[7] == r"\N":
                continue
            yield "episode", _id_to_int(cols[0]), cols[2], int(cols[7])
        elif title_type in SERIES_TYPES:
            yield "series", _id_to_int(cols[0]), cols[2], None


def build_from_files(episode_path: Path, basics_path: Path, db_path: Path) -> None:
    """Собирает базу из скачанных файлов в tmp-файл и атомарно подменяет db_path."""
    # PID в имени — параллельные сборки (например, после рестарта uvicorn --reload) не портят друг другу файлы
    tmp_path = db_path.with_suffix(f".db.{os.getpid()}.tmp")
    tmp_path.unlink(missing_ok=True)
    try:
        _build_tmp(episode_path, basics_path, tmp_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    os.replace(tmp_path, db_path)


def _build_tmp(episode_path: Path, basics_path: Path, tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path)
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("CREATE TABLE episode_raw (id INTEGER PRIMARY KEY, parent INTEGER, season INTEGER, episode INTEGER)")
        conn.execute("CREATE TABLE episode_info (id INTEGER PRIMARY KEY, title TEXT, runtime INTEGER)")
        conn.execute("CREATE TABLE series (id INTEGER PRIMARY KEY, title TEXT NOT NULL)")

        for batch in _batched(_episode_rows(episode_path)):
            conn.executemany("INSERT OR IGNORE INTO episode_raw VALUES (?, ?, ?, ?)", batch)

        for batch in _batched(_basics_rows(basics_path)):
            conn.executemany(
                "INSERT OR IGNORE INTO episode_info VALUES (?, ?, ?)",
                [(i, t, r) for kind, i, t, r in batch if kind == "episode"],
            )
            conn.executemany(
                "INSERT OR IGNORE INTO series VALUES (?, ?)",
                [(i, t) for kind, i, t, _ in batch if kind == "series"],
            )

        # Итоговая таблица — только эпизоды с известной длительностью,
        # кластеризована по (сериал, сезон) для быстрых выборок
        conn.execute("""
            CREATE TABLE episodes (
                parent  INTEGER NOT NULL,
                season  INTEGER NOT NULL,
                episode INTEGER NOT NULL,
                id      INTEGER NOT NULL,
                title   TEXT,
                runtime INTEGER NOT NULL,
                PRIMARY KEY (parent, season, episode, id)
            ) WITHOUT ROWID
        """)
        conn.execute("""
            INSERT INTO episodes
            SELECT r.parent, r.season, r.episode, r.id, i.title, i.runtime
            FROM episode_raw r JOIN episode_info i ON i.id = r.id
        """)
        conn.execute("DROP TABLE episode_raw")
        conn.execute("DROP TABLE episode_info")
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO meta VALUES ('built_at', ?)", (str(int(time.time())),))
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()


def _download(name: str, dest: Path) -> None:
    with httpx.stream("GET", DATASETS_URL + name, timeout=60.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(1 << 20):
                f.write(chunk)


def download_and_build(db_path: Path = DB_PATH) -> None:
    """Скачивает датасеты и пересобирает базу. Синхронно, долго — запускать в отдельном процессе."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = db_path.parent
    episode_path = work_dir / f"{EPISODE_FILE}.{os.getpid()}"
    basics_path = work_dir / f"{BASICS_FILE}.{os.getpid()}"
    started = time.monotonic()
    try:
        logger.info("IMDb dataset: скачиваю %s, %s", EPISODE_FILE, BASICS_FILE)
        _download(EPISODE_FILE, episode_path)
        _download(BASICS_FILE, basics_path)
        logger.info("IMDb dataset: собираю %s", db_path)
        build_from_files(episode_path, basics_path, db_path)
    finally:
        episode_path.unlink(missing_ok=True)
        basics_path.unlink(missing_ok=True)
    logger.info("IMDb dataset: готово за %.0f с", time.monotonic() - started)


def is_fresh(db_path: Path = DB_PATH, max_age: float = MAX_AGE_SECONDS) -> bool:
    try:
        return time.time() - db_path.stat().st_mtime < max_age
    except FileNotFoundError:
        return False


async def refresh_loop(db_path: Path = DB_PATH) -> None:
    """Фоновая задача веб-приложения: раз в час проверяет, не пора ли пересобрать базу.
    Сборка идёт в отдельном процессе, чтобы не блокировать event loop и не упираться в GIL."""
    while True:
        # Любая ошибка итерации (диск, права на volume) не должна навсегда останавливать обновления
        try:
            if not is_fresh(db_path):
                code = await _run_build()
                if code != 0:
                    logger.error("IMDb dataset: сборка завершилась с кодом %d", code)
        except Exception:
            logger.exception("IMDb dataset: ошибка при обновлении базы, повтор через час")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def _run_build() -> int:
    proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "app.services.imdb_dataset")
    try:
        return await proc.wait()
    except asyncio.CancelledError:
        # Приложение останавливается — не оставляем сборку работать сиротой
        proc.terminate()
        await proc.wait()
        raise


# ── Чтение ──────────────────────────────────────────────────────────────────

@dataclass
class DatasetEpisode:
    season: int
    episode: int
    episode_id: str
    title: str
    runtime_min: int


class ImdbDataset:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path

    async def _fetch(self, sql: str, params: tuple) -> list[tuple]:
        if not self.db_path.exists():
            raise ImdbDatasetNotReady()
        # read-only: файл пересобирается подменой. На Linux (прод) открытые соединения дочитывают
        # старую версию. На Windows os.replace падает с PermissionError, пока файл открыт на чтение, —
        # сборка завершится ошибкой, следующая попытка будет через час (refresh_loop)
        async with aiosqlite.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True) as db:
            async with db.execute(sql, params) as cursor:
                return list(await cursor.fetchall())

    async def get_series_title(self, series_id: str) -> Optional[str]:
        rows = await self._fetch("SELECT title FROM series WHERE id = ?", (_id_to_int(series_id),))
        return rows[0][0] if rows else None

    async def get_seasons(self, series_id: str) -> list[int]:
        rows = await self._fetch(
            "SELECT DISTINCT season FROM episodes WHERE parent = ? ORDER BY season",
            (_id_to_int(series_id),),
        )
        return [r[0] for r in rows]

    async def get_episodes(self, series_id: str, season: int) -> list[DatasetEpisode]:
        rows = await self._fetch(
            "SELECT episode, id, title, runtime FROM episodes WHERE parent = ? AND season = ? ORDER BY episode",
            (_id_to_int(series_id), season),
        )
        return [
            DatasetEpisode(season=season, episode=ep, episode_id=_int_to_id(i), title=t or "", runtime_min=r)
            for ep, i, t, r in rows
        ]


if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", level=logging.INFO)
    # terminate() от родителя — через SystemExit, чтобы finally удалил временные файлы
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    download_and_build()
