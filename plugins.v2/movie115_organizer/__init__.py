import re
from datetime import datetime
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.chain.storage import StorageChain
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.schemas.file import FileItem
from app.schemas.types import EventType


class Movie115Organizer(_PluginBase):
    # ── 插件元信息 ──────────────────────────────────────────────
    plugin_name = "115目录洗白整理"
    plugin_desc = "监控115网盘目录，自动删除小文件、重命名（去除@前缀）、移动到目标路径。"
    plugin_version = "1.3"
    plugin_author = "wq2020wdm"
    plugin_config_prefix = "movie115organizer_"
    plugin_order = 30
    auth_level = 1

    # ── 运行时状态 ──────────────────────────────────────────────
    _scheduler: Optional[BackgroundScheduler] = None
    _lock = Lock()

    # ── 配置项 ──────────────────────────────────────────────────
    _enabled: bool = False
    _cron: str = "0 */2 * * *"          # 默认每 2 小时
    _monitor_paths: str = ""             # 换行分隔，支持 cid 或绝对路径
    _target_path: str = ""              # 移动目标路径（绝对路径）
    _size_threshold_mb: int = 500       # 小于该值(MB)的文件视为垃圾
    _notify: bool = True
    _run_once: bool = False

    # ═══════════════════════════════════════════════════════════
    #  初始化 / 卸载
    # ═══════════════════════════════════════════════════════════

    def init_plugin(self, config: dict = None):
        self._stop_scheduler()

        if config:
            self._enabled          = config.get("enabled", False)
            self._cron             = config.get("cron", "0 */2 * * *")
            self._monitor_paths    = config.get("monitor_paths", "")
            self._target_path      = config.get("target_path", "")
            self._size_threshold_mb = int(config.get("size_threshold_mb", 500))
            self._notify           = config.get("notify", True)
            self._run_once         = config.get("run_once", False)

        if self._run_once:
            self._run_once = False
            self.update_config({"run_once": False, **self._current_config()})
            self._execute()
            return

        if self._enabled and self._cron:
            self._start_scheduler()

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        self._stop_scheduler()

    # ═══════════════════════════════════════════════════════════
    #  调度器管理
    # ═══════════════════════════════════════════════════════════

    def _start_scheduler(self):
        try:
            self._scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
            self._scheduler.add_job(
                func=self._execute,
                trigger=CronTrigger.from_crontab(self._cron),
                name="115目录洗白整理",
                misfire_grace_time=60,
                coalesce=True,
            )
            self._scheduler.start()
            logger.info(f"[115整理] 定时任务已启动，Cron: {self._cron}")
        except Exception as e:
            logger.error(f"[115整理] 启动调度器失败: {e}")

    def _stop_scheduler(self):
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        self._scheduler = None

    # ═══════════════════════════════════════════════════════════
    #  核心逻辑
    # ═══════════════════════════════════════════════════════════

    def _execute(self):
        """主流程：扫描所有监控目录下的子文件夹，依次洗白。"""
        if not self._lock.acquire(blocking=False):
            logger.warning("[115整理] 上次任务尚未完成，跳过本次执行")
            return
        try:
            paths = [p.strip() for p in self._monitor_paths.splitlines() if p.strip()]
            if not paths:
                logger.warning("[115整理] 未配置监控路径，跳过")
                return
            if not self._target_path.strip():
                logger.warning("[115整理] 未配置目标路径，跳过")
                return

            storage = StorageChain()
            total_moved = 0

            for monitor_path in paths:
                logger.info(f"[115整理] 开始扫描监控目录: {monitor_path}")
                parent_item = self._get_fileitem(storage, monitor_path)
                if not parent_item:
                    logger.error(f"[115整理] 无法获取目录: {monitor_path}")
                    continue

                # 列出监控目录下的子目录
                children = storage.list_files(parent_item) or []
                subfolders = [c for c in children if c.type == "dir"]
                logger.info(f"[115整理] 发现 {len(subfolders)} 个子文件夹")

                for folder in subfolders:
                    moved = self._process_folder(storage, folder)
                    if moved:
                        total_moved += 1

            if self._notify and total_moved > 0:
                self.post_message(
                    mtype=NotificationType.Plugin,
                    title="115目录洗白整理完成",
                    text=f"本次共整理并移动了 {total_moved} 个文件夹到目标路径。",
                )
        finally:
            self._lock.release()

    def _process_folder(self, storage: StorageChain, folder: FileItem) -> bool:
        """
        处理单个子文件夹：
        1. 删除小于阈值的文件
        2. 若仍有垃圾 → 跳过
        3. 重命名（去除@及之前的字符）
        4. 移动到目标路径
        返回 True 表示成功移动。
        """
        logger.info(f"[115整理] 处理文件夹: {folder.path}")
        threshold_bytes = self._size_threshold_mb * 1024 * 1024

        # ── Step 1: 列出文件 ────────────────────────────────────
        files = storage.list_files(folder) or []
        all_files = [f for f in files if f.type == "file"]

        if not all_files:
            logger.info(f"[115整理] 文件夹为空，跳过: {folder.name}")
            return False

        # ── Step 2: 删除小文件 ──────────────────────────────────
        small_files = [f for f in all_files if (f.size or 0) < threshold_bytes]
        for sf in small_files:
            logger.info(f"[115整理] 删除小文件 ({self._fmt_size(sf.size)}): {sf.name}")
            result = storage.delete_file(sf)
            if not result:
                logger.warning(f"[115整理] 删除失败，跳过此文件夹: {sf.name}")
                return False

        # ── Step 3: 重新列出，检查是否只剩大文件 ────────────────
        files_after = storage.list_files(folder) or []
        remaining = [f for f in files_after if f.type == "file"]

        if not remaining:
            logger.info(f"[115整理] 删除后文件夹为空，跳过: {folder.name}")
            return False

        still_small = [f for f in remaining if (f.size or 0) < threshold_bytes]
        if still_small:
            logger.info(
                f"[115整理] 仍有 {len(still_small)} 个小文件，本轮跳过: {folder.name}"
            )
            return False

        # ── Step 4: 重命名文件夹 ────────────────────────────────
        new_name = self._compute_new_name(folder.name)
        if new_name != folder.name:
            logger.info(f"[115整理] 重命名: {folder.name!r} → {new_name!r}")
            ok = storage.rename_file(folder, new_name)
            if not ok:
                logger.error(f"[115整理] 重命名失败，跳过: {folder.name}")
                return False
            # 更新 folder 对象以获得最新状态
            folder = self._get_fileitem(storage, str(folder.path).rsplit("/", 1)[0] + "/" + new_name)
            if not folder:
                logger.error("[115整理] 重命名后无法获取新文件夹，跳过")
                return False

        # ── Step 5: 移动到目标路径 ──────────────────────────────
        target_item = self._get_fileitem(storage, self._target_path.rstrip("/"))
        if not target_item:
            logger.error(f"[115整理] 无法获取目标路径: {self._target_path}")
            return False

        logger.info(f"[115整理] 移动 {folder.name!r} → {self._target_path}")
        ok = storage.move_file(folder, target_item)
        if not ok:
            logger.error(f"[115整理] 移动失败: {folder.name}")
            return False

        logger.info(f"[115整理] ✅ 完成: {folder.name}")
        return True

    # ═══════════════════════════════════════════════════════════
    #  工具方法
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _compute_new_name(name: str) -> str:
        """若文件夹名含 @ 符号，则删除 @ 及其之前的所有字符。"""
        idx = name.find("@")
        if idx != -1:
            return name[idx + 1:]
        return name

    @staticmethod
    def _get_fileitem(storage: StorageChain, path: str) -> Optional[FileItem]:
        """通过路径获取 FileItem，兼容绝对路径与 cid。"""
        try:
            return storage.get_file_item(storage="u115", path=path)
        except Exception as e:
            logger.error(f"[115整理] get_file_item 出错 ({path}): {e}")
            return None

    @staticmethod
    def _fmt_size(size_bytes: Optional[int]) -> str:
        if not size_bytes:
            return "0 B"
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"

    def _current_config(self) -> dict:
        return {
            "enabled": self._enabled,
            "cron": self._cron,
            "monitor_paths": self._monitor_paths,
            "target_path": self._target_path,
            "size_threshold_mb": self._size_threshold_mb,
            "notify": self._notify,
        }

    # ═══════════════════════════════════════════════════════════
    #  Bot 指令
    # ═══════════════════════════════════════════════════════════

    def get_command(self) -> List[Dict[str, Any]]:
        return [
            {
                "cmd": "/run_115_clean",
                "event": EventType.PluginAction,
                "desc": "立即执行115目录洗白整理",
                "category": "整理",
                "data": {"action": "run_115_clean"},
            }
        ]

    @eventmanager.register(EventType.PluginAction)
    def handle_command(self, event: Event):
        if event.event_data.get("action") == "run_115_clean":
            logger.info("[115整理] 收到 Bot 指令，立即执行")
            self._execute()

    # ═══════════════════════════════════════════════════════════
    #  插件 API（面板"立即运行"按钮）
    # ═══════════════════════════════════════════════════════════

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/run",
                "endpoint": self.api_run,
                "methods": ["GET"],
                "summary": "立即执行115整理",
                "description": "手动触发一次全量扫描与整理",
            }
        ]

    def api_run(self) -> dict:
        import threading
        threading.Thread(target=self._execute, daemon=True).start()
        return {"code": 0, "message": "任务已在后台启动"}

    # ═══════════════════════════════════════════════════════════
    #  UI 配置页
    # ═══════════════════════════════════════════════════════════

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    # ── 行 1: 启用 + 通知 ──
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify",
                                            "label": "完成后发送通知",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "run_once",
                                            "label": "保存后立即运行一次",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    # ── 行 2: Cron + 阈值 ──
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "cron",
                                            "label": "Cron 表达式",
                                            "placeholder": "0 */2 * * *",
                                            "hint": "标准5字段 Cron，建议间隔≥30分钟",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "size_threshold_mb",
                                            "label": "垃圾文件阈值 (MB)",
                                            "placeholder": "500",
                                            "hint": "小于此大小的文件将被删除（如广告、预告片）",
                                            "type": "number",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    # ── 行 3: 监控路径 ──
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "monitor_paths",
                                            "label": "监控目录（每行一个）",
                                            "placeholder": "/网盘/下载\n/网盘/待整理",
                                            "hint": "支持115绝对路径（如 /网盘/下载），每行一个",
                                            "rows": 4,
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    # ── 行 4: 目标路径 ──
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "target_path",
                                            "label": "移动目标路径",
                                            "placeholder": "/网盘/电影",
                                            "hint": "洗白完成的文件夹将被移动到此路径下",
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    # ── 说明卡片 ──
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": (
                                                "工作流程：① 扫描监控目录下所有子文件夹 → "
                                                "② 删除小于阈值的垃圾文件（广告/预告片） → "
                                                "③ 若剩余文件均≥阈值：检测文件夹名是否含 @ → "
                                                "④ 含 @ 则去除 @ 及之前所有字符并重命名 → "
                                                "⑤ 将文件夹移动到目标路径。\n"
                                                "本插件直接调用 MoviePilot 内置 115 OpenAPI，"
                                                "无需单独填写 Cookie，请确保主程序已正确配置115账号。"
                                            ),
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "cron": "0 */2 * * *",
            "monitor_paths": "",
            "target_path": "",
            "size_threshold_mb": 500,
            "notify": True,
            "run_once": False,
        }

    # ═══════════════════════════════════════════════════════════
    #  数据面板
    # ═══════════════════════════════════════════════════════════

    def get_page(self) -> List[dict]:
        return []

    def get_dashboard(self, key: str, **kwargs):
        return None, None, None
