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
    plugin_version = "1.3.9"
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
    #  获取底层 u115 存储模块实例（每次都重新获取，避免缓存问题）
    # ═══════════════════════════════════════════════════════════

    def _get_u115_module(self):
        """
        从 StorageChain 或模块系统中获取底层 u115 存储实例，
        并打印其所有可用方法，便于调试。
        """
        try:
            # 方式1：通过 StorageChain 的 storages 字典
            storage_chain = StorageChain()
            for attr in ("storages", "_storages", "storage_dict", "modules"):
                container = getattr(storage_chain, attr, None)
                if container and isinstance(container, dict):
                    for key, module in container.items():
                        if "115" in str(key) or "u115" in str(key).lower():
                            methods = [m for m in dir(module) if not m.startswith("_")]
                            logger.info(f"【115整理】找到 u115 模块(via {attr}[{key!r}])，可用方法: {methods}")
                            return module

            # 方式2：直接 import u115 模块类
            try:
                from app.modules.filemanager.storages.u115 import U115Storage
                inst = U115Storage()
                methods = [m for m in dir(inst) if not m.startswith("_")]
                logger.info(f"【115整理】直接实例化 U115Storage，可用方法: {methods}")
                return inst
            except ImportError:
                pass

            # 方式3：通过 run_module 查询
            try:
                from app.core.module import ModuleManager
                mm = ModuleManager()
                mod = mm.get_module("U115Storage") or mm.get_module("u115")
                if mod:
                    methods = [m for m in dir(mod) if not m.startswith("_")]
                    logger.info(f"【115整理】通过 ModuleManager 获取 u115，可用方法: {methods}")
                    return mod
            except Exception:
                pass

            # 兜底：打印 StorageChain 自身方法供分析
            sc_methods = [m for m in dir(storage_chain) if not m.startswith("_")]
            logger.error(
                f"【115整理】无法获取底层 u115 模块。\n"
                f"StorageChain 可用方法: {sc_methods}\n"
                f"请将此日志发给开发者。"
            )
            return None

        except Exception as e:
            logger.error(f"【115整理】_get_u115_module 异常: {e}", exc_info=True)
            return None

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
            # 每次执行都探测一次底层模块，确保方法名被打印到日志
            u115 = self._get_u115_module()

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
                    if self._process_folder(storage, u115, folder):
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

    def _process_folder(self, storage: StorageChain, u115, folder: FileItem) -> bool:
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
            logger.info(f"【115整理】[{fname}] 文件夹为空（可能下载中），跳过")
            return False

        # ── Step 2: 删除小文件 ──────────────────────────────
        small = [f for f in all_files if (f.size or 0) < threshold_bytes]
        large = [f for f in all_files if (f.size or 0) >= threshold_bytes]
        logger.info(
            f"【115整理】[{fname}] 阈值 {self._size_threshold_mb}MB → "
            f"小文件 {len(small)} 个，大文件 {len(large)} 个"
        )
        for sf in small:
            logger.info(f"【115整理】[{fname}] 删除: {sf.name} ({self._fmt(sf.size)})")
            try:
                ok = storage.delete_file(sf)
                logger.info(f"【115整理】[{fname}] 删除{'成功' if ok else '失败'}: {sf.name}")
            except Exception as e:
                logger.error(f"【115整理】[{fname}] 删除异常: {sf.name} → {e}", exc_info=True)

        # ── Step 3: 再次确认 ────────────────────────────────
        files_after = storage.list_files(folder) or []
        remaining = [f for f in files_after if f.type == "file"]
        still_small = [f for f in remaining if (f.size or 0) < threshold_bytes]
        logger.info(f"【115整理】[{fname}] 删除后剩余 {len(remaining)} 个，仍小于阈值: {len(still_small)} 个")

        if not remaining:
            logger.info(f"【115整理】[{fname}] 删除后为空，可能仍在下载中，跳过")
            return False
        if still_small:
            logger.warning(f"【115整理】[{fname}] 仍有小文件未删除，跳过: {[(f.name, self._fmt(f.size)) for f in still_small]}")
            return False

        # ── Step 4: 重命名大文件（去除 @ 前缀）────────────
        big_files = [f for f in remaining if (f.size or 0) >= threshold_bytes]
        for bf in big_files:
            if "@" in bf.name:
                new_fname = bf.name.split("@")[-1]
                logger.info(f"【115整理】[{fname}] 重命名: {bf.name!r} → {new_fname!r}")
                try:
                    ok = storage.rename_file(bf, new_fname)
                    logger.info(f"【115整理】[{fname}] 重命名{'成功' if ok else '失败'}")
                except Exception as e:
                    logger.error(f"【115整理】[{fname}] 重命名异常: {e}", exc_info=True)
            else:
                logger.info(f"【115整理】[{fname}] 文件无需重命名: {bf.name}")

        # ── Step 5: 移动文件夹 ──────────────────────────────
        target_path = self._target_path.rstrip("/")
        target_item = self._get_fileitem(storage, target_path)
        if not target_item:
            logger.error(f"【115整理】[{fname}] 无法获取目标路径: {target_path}")
            return False

        logger.info(f"【115整理】[{fname}] 开始移动 → {target_path}")
        ok = self._do_move(storage, u115, folder, target_item)
        logger.info(f"【115整理】[{fname}] 移动结果: {'成功 ✅' if ok else '失败 ❌'}")
        return ok

    # ═══════════════════════════════════════════════════════════
    #  移动实现：优先用底层模块，其次 StorageChain，最后报错
    # ═══════════════════════════════════════════════════════════

    def _do_move(self, storage: StorageChain, u115, src: FileItem, dst_dir: FileItem) -> bool:
        """
        按优先级尝试所有已知 move 相关方法。
        src.fileid  = 源文件夹的 115 cid
        dst_dir.fileid = 目标目录的 115 cid
        """
        # ── 候选1：底层 u115 模块的各种可能方法名 ──────────
        if u115 is not None:
            for mname in ("move", "move_file", "move_dir", "moveto",
                          "move_to", "transfer", "rename_move"):
                m = getattr(u115, mname, None)
                if m is None:
                    continue
                logger.info(f"【115整理】尝试 u115.{mname}(src, dst_dir)")
                try:
                    # 优先用 fileid（115 原生接口），其次传 FileItem
                    src_id  = getattr(src, "fileid", None) or getattr(src, "file_id", None)
                    dst_id  = getattr(dst_dir, "fileid", None) or getattr(dst_dir, "file_id", None)
                    if src_id and dst_id:
                        ok = m(src_id, dst_id)
                    else:
                        ok = m(src, dst_dir)
                    if ok:
                        logger.info(f"【115整理】u115.{mname}() 成功")
                        return True
                    logger.warning(f"【115整理】u115.{mname}() 返回失败")
                except Exception as e:
                    logger.warning(f"【115整理】u115.{mname}() 异常: {e}")

        # ── 候选2：StorageChain 的各种可能方法名 ────────────
        for mname in ("move_file", "move", "transfer_file", "copy_file", "copy"):
            m = getattr(storage, mname, None)
            if m is None:
                continue
            logger.info(f"【115整理】尝试 StorageChain.{mname}(src, dst_dir)")
            try:
                ok = m(src, dst_dir)
                if ok:
                    logger.info(f"【115整理】StorageChain.{mname}() 成功")
                    return True
                logger.warning(f"【115整理】StorageChain.{mname}() 返回失败")
            except Exception as e:
                logger.warning(f"【115整理】StorageChain.{mname}() 异常: {e}")

        # ── 全部失败：打印完整方法列表供定位 ───────────────
        sc_methods = [m for m in dir(storage) if not m.startswith("_") and callable(getattr(storage, m))]
        u115_methods = ([m for m in dir(u115) if not m.startswith("_") and callable(getattr(u115, m))]
                        if u115 else [])
        logger.error(
            f"【115整理】所有 move 方法均失败。\n"
            f"  StorageChain 方法: {sc_methods}\n"
            f"  u115 模块方法:     {u115_methods}\n"
            f"  请将此日志提交给开发者。"
        )
        return False

    # ═══════════════════════════════════════════════════════════
    #  工具方法
    # ═══════════════════════════════════════════════════════════

    def _get_fileitem(self, storage: StorageChain, path: str) -> Optional[FileItem]:
        try:
            parts = [p for p in path.strip("/").split("/") if p]
            if not parts:
                return None
            root_item = FileItem(storage="u115", fileid="0", path="/", type="dir", name="")
            current_items = storage.list_files(root_item) or []
            if not current_items:
                logger.error("【115整理】无法列出 115 根目录")
                return None
            current_item = None
            for i, part in enumerate(parts):
                matched = next((item for item in current_items if item.name == part), None)
                if not matched:
                    logger.error(
                        f"【115整理】路径段 '{part}' 未找到，"
                        f"当前层: {[item.name for item in current_items[:10]]}，"
                        f"目标: {path}"
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
