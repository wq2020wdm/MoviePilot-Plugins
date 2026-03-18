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
    plugin_version = "1.4.0"
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
        logger.info(f"【115整理】>>> 开始处理: {fname}")

        # Step 1: 列出文件
        files = storage.list_files(folder) or []
        all_files = [f for f in files if f.type == "file"]
        logger.info(f"【115整理】[{fname}] {len(all_files)} 个文件: {[(f.name, self._fmt(f.size)) for f in all_files]}")
        if not all_files:
            logger.info(f"【115整理】[{fname}] 为空（下载中），跳过")
            return False

        # Step 2: 删除小文件
        small = [f for f in all_files if (f.size or 0) < threshold_bytes]
        large = [f for f in all_files if (f.size or 0) >= threshold_bytes]
        logger.info(f"【115整理】[{fname}] 阈值{self._size_threshold_mb}MB → 小{len(small)}个 大{len(large)}个")
        for sf in small:
            logger.info(f"【115整理】[{fname}] 删除: {sf.name} ({self._fmt(sf.size)})")
            try:
                ok = storage.delete_file(sf)
                logger.info(f"【115整理】[{fname}] 删除{'成功' if ok else '失败'}: {sf.name}")
            except Exception as e:
                logger.error(f"【115整理】[{fname}] 删除异常: {e}", exc_info=True)

        # Step 3: 确认无残留
        files_after = storage.list_files(folder) or []
        remaining = [f for f in files_after if f.type == "file"]
        still_small = [f for f in remaining if (f.size or 0) < threshold_bytes]
        logger.info(f"【115整理】[{fname}] 删除后剩余{len(remaining)}个，仍小于阈值:{len(still_small)}个")
        if not remaining:
            logger.info(f"【115整理】[{fname}] 删完为空，可能下载中，跳过")
            return False
        if still_small:
            logger.warning(f"【115整理】[{fname}] 小文件未删完，跳过: {[(f.name, self._fmt(f.size)) for f in still_small]}")
            return False

        # Step 4: 重命名文件（去 @ 前缀）
        for bf in [f for f in remaining if (f.size or 0) >= threshold_bytes]:
            if "@" in bf.name:
                new_fname = bf.name.split("@")[-1]
                logger.info(f"【115整理】[{fname}] 重命名: {bf.name!r} → {new_fname!r}")
                try:
                    ok = storage.rename_file(bf, new_fname)
                    logger.info(f"【115整理】[{fname}] 重命名{'成功' if ok else '失败'}")
                except Exception as e:
                    logger.error(f"【115整理】[{fname}] 重命名异常: {e}", exc_info=True)

        # Step 5: 移动
        target_path = self._target_path.rstrip("/")
        target_item = self._get_fileitem(storage, target_path)
        if not target_item:
            logger.error(f"【115整理】[{fname}] 无法获取目标路径: {target_path}")
            return False

        logger.info(f"【115整理】[{fname}] 开始移动 → {target_path}")
        ok = self._do_move(storage, folder, target_item)
        logger.info(f"【115整理】[{fname}] 移动结果: {'✅成功' if ok else '❌失败'}")
        return ok

    # ═══════════════════════════════════════════════════════════
    #  移动：四种方案依次尝试
    # ═══════════════════════════════════════════════════════════

    def _do_move(self, storage: StorageChain, src: FileItem, dst: FileItem) -> bool:

        # ── 方案A：StorageChain.run_module() 调底层 move_file ──
        # ChainBase.run_module 会把调用路由到实际实现模块
        logger.info("【115整理】方案A: StorageChain.run_module('move_file', ...)")
        try:
            result = storage.run_module("move_file", src, dst)
            if result:
                logger.info("【115整理】方案A 成功")
                return True
            logger.info(f"【115整理】方案A 返回: {result}")
        except Exception as e:
            logger.warning(f"【115整理】方案A 异常: {e}")

        # ── 方案B：直接调用 p115client 原生 move API ───────────
        logger.info("【115整理】方案B: 直接调用 p115client 原生 API")
        try:
            result = self._p115_move(src, dst)
            if result:
                logger.info("【115整理】方案B 成功")
                return True
        except Exception as e:
            logger.warning(f"【115整理】方案B 异常: {e}")

        # ── 方案C：通过 FileManagerModule 移动 ────────────────
        logger.info("【115整理】方案C: FileManagerModule")
        try:
            from app.modules.filemanager import FileManagerModule
            fm = FileManagerModule()
            # 探测可用方法
            fm_methods = [m for m in dir(fm) if "move" in m.lower() or "transfer" in m.lower()]
            logger.info(f"【115整理】FileManagerModule move相关方法: {fm_methods}")
            for mname in fm_methods:
                m = getattr(fm, mname, None)
                if not callable(m):
                    continue
                try:
                    ok = m(src, dst)
                    if ok:
                        logger.info(f"【115整理】方案C FileManagerModule.{mname}() 成功")
                        return True
                except Exception as e2:
                    logger.warning(f"【115整理】方案C {mname} 异常: {e2}")
        except Exception as e:
            logger.warning(f"【115整理】方案C 异常: {e}")

        # ── 方案D：打印所有可探测到的方法，彻底暴露 API 名 ───
        logger.error("【115整理】所有方案均失败，打印完整诊断信息：")
        try:
            sc_methods = sorted([m for m in dir(storage) if not m.startswith("_") and callable(getattr(storage, m))])
            logger.error(f"【115整理】StorageChain 全部方法: {sc_methods}")
        except Exception:
            pass
        try:
            src_attrs = {k: str(v) for k, v in vars(src).items() if not k.startswith("_")}
            logger.error(f"【115整理】src FileItem 属性: {src_attrs}")
        except Exception:
            pass
        try:
            dst_attrs = {k: str(v) for k, v in vars(dst).items() if not k.startswith("_")}
            logger.error(f"【115整理】dst FileItem 属性: {dst_attrs}")
        except Exception:
            pass
        return False

    def _p115_move(self, src: FileItem, dst: FileItem) -> bool:
        """
        直接通过 p115client 调用 115 原生移动接口。
        MoviePilot 内部用 p115client 库与 115 通信，这里绕过封装直接调用。
        """
        # 获取 src 和 dst 的 fileid（115 cid）
        src_id = getattr(src, "fileid", None) or getattr(src, "file_id", None) or getattr(src, "id", None)
        dst_id = getattr(dst, "fileid", None) or getattr(dst, "file_id", None) or getattr(dst, "id", None)
        logger.info(f"【115整理】p115 move: src_id={src_id}, dst_id={dst_id}")

        if not src_id or not dst_id:
            logger.error(f"【115整理】无法获取 fileid，src_id={src_id}, dst_id={dst_id}")
            return False

        # 方式1: 通过 u115 存储模块拿到 p115client 实例
        client = self._get_p115_client()
        if client is None:
            logger.error("【115整理】无法获取 p115client 实例")
            return False

        # p115client 中移动文件夹的方法名可能是:
        # client.fs_move / client.move / client.fs.move / client.fs.mv
        for attr_path in (
            "fs_move",       # p115client 直接方法
            "move",
            "fs.move",       # 通过 fs 子对象
            "fs.mv",
            "fs.rename",
        ):
            parts = attr_path.split(".")
            obj = client
            try:
                for p in parts:
                    obj = getattr(obj, p)
                logger.info(f"【115整理】尝试 client.{attr_path}([{src_id}], {dst_id})")
                # 115 move API：第一个参数是文件id列表，第二个是目标目录cid
                result = obj([src_id], dst_id)
                logger.info(f"【115整理】client.{attr_path}() 结果: {result}")
                return True
            except AttributeError:
                pass
            except Exception as e:
                logger.warning(f"【115整理】client.{attr_path}() 异常: {e}")

        # 打印 client 所有方法
        c_methods = sorted([m for m in dir(client) if not m.startswith("_") and ("move" in m.lower() or "mv" in m.lower() or "transfer" in m.lower())])
        c_all = sorted([m for m in dir(client) if not m.startswith("_")])
        logger.error(f"【115整理】p115client move相关方法: {c_methods}")
        logger.error(f"【115整理】p115client 全部方法(前50): {c_all[:50]}")
        return False

    def _get_p115_client(self):
        """
        尝试多种路径获取 MoviePilot 内部的 p115client 实例。
        """
        # 路径1: 从 u115 存储实例中获取 client 属性
        try:
            from app.modules.filemanager.storages.u115 import U115Storage
            u = U115Storage()
            for attr in ("client", "_client", "p115", "_p115", "api", "_api", "driver", "_driver"):
                c = getattr(u, attr, None)
                if c is not None:
                    logger.info(f"【115整理】从 U115Storage.{attr} 获取到 client: {type(c)}")
                    return c
            # 打印 U115Storage 全部属性帮助定位
            u_attrs = [a for a in dir(u) if not a.startswith("__")]
            logger.info(f"【115整理】U115Storage 属性列表: {u_attrs}")
        except Exception as e:
            logger.warning(f"【115整理】获取 U115Storage 失败: {e}")

        # 路径2: 直接 import p115client 并看是否有全局单例
        try:
            import p115client
            for attr in ("client", "default_client", "get_client"):
                c = getattr(p115client, attr, None)
                if c is not None:
                    if callable(c):
                        c = c()
                    logger.info(f"【115整理】从 p115client.{attr} 获取到 client: {type(c)}")
                    return c
        except ImportError:
            logger.warning("【115整理】p115client 模块未找到，尝试其他路径")

        # 路径3: 通过 app.core.config 或全局上下文
        try:
            from app import core
            c = getattr(core, "p115_client", None) or getattr(core, "client_115", None)
            if c:
                return c
        except Exception:
            pass

        return None

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
