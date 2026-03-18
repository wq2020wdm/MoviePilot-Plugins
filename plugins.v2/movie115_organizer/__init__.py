import re
from datetime import datetime
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

from app.chain.storage import StorageChain
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.schemas.file import FileItem

class movie115_organizer(_PluginBase):
    # ── 插件元信息 ──────────────────────────────────────────────
    plugin_id = "movie115_organizer"
    plugin_name = "115 目录洗白整理"
    plugin_desc = "监控115网盘目录，自动删除小文件、去除@前缀重命名、移动到目标路径。"
    plugin_icon = "Folder" 
    plugin_version = "1.3.5"
    plugin_author = "wq2020wdm"
    plugin_order = 30
    auth_level = 1

    # ── 运行时状态 ──────────────────────────────────────────────
    _lock = Lock()

    # ── 配置项 ──────────────────────────────────────────────────
    _enabled: bool = False
    _cron: str = "0 */2 * * *"
    _monitor_paths: str = ""
    _target_path: str = ""
    _size_threshold_mb: int = 500
    _notify: bool = True
    _run_once: bool = False

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled          = config.get("enabled", False)
            self._cron             = config.get("cron", "0 */2 * * *")
            self._monitor_paths    = config.get("monitor_paths", "")
            self._target_path      = config.get("target_path", "")
            try:
                self._size_threshold_mb = int(config.get("size_threshold_mb", 500))
            except:
                self._size_threshold_mb = 500
            self._notify           = config.get("notify", True)
            self._run_once         = config.get("run_once", False)

        if self._run_once:
            self._run_once = False
            self.update_config({"run_once": False, **self._current_config()})
            self.execute()

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        pass

    # ═══════════════════════════════════════════════════════════
    #  核心逻辑
    # ═══════════════════════════════════════════════════════════

    def execute(self, **kwargs):
        """主流程：扫描所有监控目录下的子文件夹，依次洗白。"""
        if not self._lock.acquire(blocking=False):
            logger.warning("【115整理】上次任务尚未完成，跳过本次执行")
            return
        try:
            paths = [p.strip() for p in self._monitor_paths.splitlines() if p.strip()]
            if not paths:
                logger.warning("【115整理】未配置监控路径，跳过")
                return
            if not self._target_path.strip():
                logger.warning("【115整理】未配置目标路径，跳过")
                return

            storage = StorageChain()
            total_moved = 0

            for monitor_path in paths:
                logger.info(f"【115整理】开始扫描 115 目录: {monitor_path}")
                parent_item = self._get_fileitem(storage, monitor_path)
                if not parent_item:
                    logger.error(f"【115整理】无法获取目录，已跳过: {monitor_path}")
                    continue

                children = storage.list_files(parent_item) or []
                subfolders = [c for c in children if c.type == "dir"]
                logger.info(f"【115整理】发现 {len(subfolders)} 个子文件夹: {[f.name for f in subfolders]}")

                for folder in subfolders:
                    if self._process_folder(storage, folder):
                        total_moved += 1

            if self._notify and total_moved > 0:
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title="115目录洗白整理完成",
                    text=f"本次共整理并移动了 {total_moved} 个文件夹。"
                )
        finally:
            self._lock.release()

    def _process_folder(self, storage: StorageChain, folder: FileItem) -> bool:
        threshold_bytes = self._size_threshold_mb * 1024 * 1024
        files = storage.list_files(folder) or []
        all_files = [f for f in files if f.type == "file"]

        if not all_files: return False

        # 删除小文件
        for sf in [f for f in all_files if (f.size or 0) < threshold_bytes]:
            storage.delete_file(sf)

        # 再次检查
        files_after = storage.list_files(folder) or []
        remaining = [f for f in files_after if f.type == "file"]
        if not remaining or any((f.size or 0) < threshold_bytes for f in remaining):
            return False

        # 重命名
        new_name = folder.name.split('@')[-1] if '@' in folder.name else folder.name
        if new_name != folder.name:
            if not storage.rename_file(folder, new_name): return False
            folder = self._get_fileitem(storage, str(folder.path).rsplit("/", 1)[0] + "/" + new_name)

        # 移动
        target_item = self._get_fileitem(storage, self._target_path.rstrip("/"))
        if target_item and storage.move_file(folder, target_item):
            logger.info(f"【115整理】归档成功: {new_name}")
            return True
        return False

    def _get_fileitem(self, storage: StorageChain, path: str) -> Optional[FileItem]:
        """
        通过逐级 list_files 遍历路径获取目标 FileItem。
        path 示例: /CloudNAS/temp/小姐姐
        """
        try:
            parts = [p for p in path.strip("/").split("/") if p]
            if not parts:
                logger.error("【115整理】路径为空")
                return None

            # 115 根目录 fileid 固定为 "0"
            root_item = FileItem(
                storage="u115",
                fileid="0",
                path="/",
                type="dir",
                name=""
            )

            current_items = storage.list_files(root_item) or []
            if not current_items:
                logger.error("【115整理】无法列出 115 根目录，请确认 115 账号已在主程序配置")
                return None

            current_item = None
            for i, part in enumerate(parts):
                matched = next((item for item in current_items if item.name == part), None)
                if not matched:
                    logger.error(
                        f"【115整理】路径段 '{part}' 未找到，"
                        f"当前层可用名称: {[item.name for item in current_items[:10]]}，"
                        f"目标路径: {path}"
                    )
                    return None
                current_item = matched
                if i < len(parts) - 1:
                    current_items = storage.list_files(current_item) or []

            return current_item

        except Exception as e:
            logger.error(f"【115整理】_get_fileitem 异常 ({path}): {e}", exc_info=True)
            return None

    def _current_config(self) -> dict:
        return {
            "enabled": self._enabled, "cron": self._cron, "monitor_paths": self._monitor_paths,
            "target_path": self._target_path, "size_threshold_mb": self._size_threshold_mb, "notify": self._notify
        }

    # ═══════════════════════════════════════════════════════════
    #  V2 UI & Bot 支持
    # ═══════════════════════════════════════════════════════════

    def get_command(self) -> List[Dict[str, Any]]:
        return [
            {
                "command": "run_115_clean",
                "data": "run_115_clean",
                "description": "立即整理 115 目录",
                "handler": self.execute,
                "icon": "PlayArrow"
            }
        ]

    def get_api(self) -> List[dict]: return []
    def get_page(self) -> List[dict]: return []

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._cron:
            from apscheduler.triggers.cron import CronTrigger
            return [{
                "id": "movie115_organizer_task",
                "name": "115整理服务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.execute
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "notify", "label": "发送通知"}}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VSwitch", "props": {"model": "run_once", "label": "保存后运行一次"}}]}
                    ]},
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "cron", "label": "Cron 表达式"}}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "size_threshold_mb", "label": "阈值 (MB)", "type": "number"}}]}
                    ]},
                    {"component": "VRow", "content": [{"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextarea", "props": {"model": "monitor_paths", "label": "监控目录", "rows": 3}}]}]},
                    {"component": "VRow", "content": [{"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextField", "props": {"model": "target_path", "label": "目标路径"}}]}]}
                ]
            }
        ], {
            "enabled": False, "cron": "0 */2 * * *", "monitor_paths": "", "target_path": "", "size_threshold_mb": 500, "notify": True, "run_once": False
        }
