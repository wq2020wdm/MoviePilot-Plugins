import re
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
    plugin_version = "1.3.7"
    plugin_author = "wq2020wdm"
    plugin_order = 30
    auth_level = 1

    _lock = Lock()

    _enabled: bool = False
    _cron: str = "0 */2 * * *"
    _monitor_paths: str = ""
    _target_path: str = ""
    _size_threshold_mb: int = 500
    _notify: bool = True
    _run_once: bool = False

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled       = config.get("enabled", False)
            self._cron          = config.get("cron", "0 */2 * * *")
            self._monitor_paths = config.get("monitor_paths", "")
            self._target_path   = config.get("target_path", "")
            try:
                self._size_threshold_mb = int(config.get("size_threshold_mb", 500))
            except Exception:
                self._size_threshold_mb = 500
            self._notify   = config.get("notify", True)
            self._run_once = config.get("run_once", False)

        if self._run_once:
            self._run_once = False
            self.update_config({"run_once": False, **self._current_config()})
            self.execute()

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        pass

    # ═══════════════════════════════════════════════════════════
    #  主流程
    # ═══════════════════════════════════════════════════════════

    def execute(self, **kwargs):
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

            logger.info(f"【115整理】本次共归档 {total_moved} 个文件夹")
            if self._notify and total_moved > 0:
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title="115目录洗白整理完成",
                    text=f"本次共整理并移动了 {total_moved} 个文件夹。"
                )
        finally:
            self._lock.release()

    # ═══════════════════════════════════════════════════════════
    #  单文件夹处理
    # ═══════════════════════════════════════════════════════════

    def _process_folder(self, storage: StorageChain, folder: FileItem) -> bool:
        fname = folder.name
        threshold_bytes = self._size_threshold_mb * 1024 * 1024
        logger.info(f"【115整理】>>> 开始处理文件夹: {fname}")

        # ── Step 1: 列出所有文件 ────────────────────────────
        files = storage.list_files(folder) or []
        all_files = [f for f in files if f.type == "file"]
        logger.info(
            f"【115整理】[{fname}] 共 {len(all_files)} 个文件: "
            f"{[(f.name, self._fmt(f.size)) for f in all_files]}"
        )
        if not all_files:
            logger.info(f"【115整理】[{fname}] 无文件，跳过")
            return False

        # ── Step 2: 删除小文件 ──────────────────────────────
        small = [f for f in all_files if (f.size or 0) < threshold_bytes]
        large = [f for f in all_files if (f.size or 0) >= threshold_bytes]
        logger.info(
            f"【115整理】[{fname}] 阈值 {self._size_threshold_mb}MB → "
            f"小文件 {len(small)} 个，大文件 {len(large)} 个"
        )
        for sf in small:
            logger.info(f"【115整理】[{fname}] 删除小文件: {sf.name} ({self._fmt(sf.size)})")
            try:
                ok = storage.delete_file(sf)
                logger.info(f"【115整理】[{fname}] 删除结果: {'成功' if ok else '失败'} → {sf.name}")
            except Exception as e:
                logger.error(f"【115整理】[{fname}] 删除异常: {sf.name} → {e}", exc_info=True)

        # ── Step 3: 确认无小文件残留 ────────────────────────
        files_after = storage.list_files(folder) or []
        remaining = [f for f in files_after if f.type == "file"]
        still_small = [f for f in remaining if (f.size or 0) < threshold_bytes]
        logger.info(
            f"【115整理】[{fname}] 删除后剩余 {len(remaining)} 个文件，"
            f"仍小于阈值: {len(still_small)} 个"
        )
        if not remaining:
            logger.info(f"【115整理】[{fname}] 删除后为空，跳过")
            return False
        if still_small:
            logger.warning(
                f"【115整理】[{fname}] 仍有小文件（删除失败），跳过: "
                f"{[(f.name, self._fmt(f.size)) for f in still_small]}"
            )
            return False

        # ── Step 4: 重命名文件夹内的大文件（去除@前缀）──────
        # 注意：@在文件名里（如 489155.com@CAWD-931-C.mp4），不在文件夹名里
        big_files = [f for f in remaining if (f.size or 0) >= threshold_bytes]
        for bf in big_files:
            if "@" in bf.name:
                # 取 @ 后面的部分作为新文件名（保留扩展名）
                new_fname = bf.name.split("@")[-1]
                logger.info(f"【115整理】[{fname}] 重命名文件: {bf.name!r} → {new_fname!r}")
                try:
                    ok = storage.rename_file(bf, new_fname)
                    logger.info(f"【115整理】[{fname}] 文件重命名结果: {'成功' if ok else '失败'}")
                except Exception as e:
                    logger.error(f"【115整理】[{fname}] 文件重命名异常: {e}", exc_info=True)
            else:
                logger.info(f"【115整理】[{fname}] 文件无需重命名: {bf.name}")

        # ── Step 5: 移动文件夹到目标路径 ────────────────────
        target_path = self._target_path.rstrip("/")
        logger.info(f"【115整理】[{fname}] 获取目标路径 FileItem: {target_path}")
        target_item = self._get_fileitem(storage, target_path)
        if not target_item:
            logger.error(f"【115整理】[{fname}] 无法获取目标路径，跳过: {target_path}")
            return False

        logger.info(f"【115整理】[{fname}] 开始移动 → {target_path}")
        try:
            ok = storage.move_file(folder, target_item)
            logger.info(f"【115整理】[{fname}] 移动结果: {'成功 ✅' if ok else '失败 ❌'}")
            return bool(ok)
        except Exception as e:
            logger.error(f"【115整理】[{fname}] 移动异常: {e}", exc_info=True)
            return False

    # ═══════════════════════════════════════════════════════════
    #  工具方法
    # ═══════════════════════════════════════════════════════════

    def _get_fileitem(self, storage: StorageChain, path: str) -> Optional[FileItem]:
        """逐级 list_files 遍历路径获取 FileItem。"""
        try:
            parts = [p for p in path.strip("/").split("/") if p]
            if not parts:
                logger.error("【115整理】_get_fileitem: 路径为空")
                return None

            root_item = FileItem(
                storage="u115",
                fileid="0",
                path="/",
                type="dir",
                name=""
            )

            current_items = storage.list_files(root_item) or []
            if not current_items:
                logger.error("【115整理】无法列出 115 根目录，请确认账号已配置")
                return None

            current_item = None
            for i, part in enumerate(parts):
                matched = next((item for item in current_items if item.name == part), None)
                if not matched:
                    logger.error(
                        f"【115整理】路径段 '{part}' 未找到，"
                        f"当前层: {[item.name for item in current_items[:10]]}，"
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

    @staticmethod
    def _fmt(size_bytes: Optional[int]) -> str:
        if not size_bytes:
            return "0B"
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f}{unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f}TB"

    def _current_config(self) -> dict:
        return {
            "enabled": self._enabled, "cron": self._cron,
            "monitor_paths": self._monitor_paths, "target_path": self._target_path,
            "size_threshold_mb": self._size_threshold_mb, "notify": self._notify
        }

    # ═══════════════════════════════════════════════════════════
    #  V2 接口
    # ═══════════════════════════════════════════════════════════

    def get_command(self) -> List[Dict[str, Any]]:
        return [{
            "command": "run_115_clean",
            "data": "run_115_clean",
            "description": "立即整理 115 目录",
            "handler": self.execute,
            "icon": "PlayArrow"
        }]

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
            "enabled": False, "cron": "0 */2 * * *", "monitor_paths": "", "target_path": "",
            "size_threshold_mb": 500, "notify": True, "run_once": False
        }
