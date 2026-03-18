import inspect
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

from app.chain.storage import StorageChain
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.schemas.file import FileItem


class movie115_organizer(_PluginBase):
    plugin_id = "movie115_organizer"
    plugin_name = "115 目录洗白整理"
    plugin_desc = "监控115网盘目录，自动删除小文件、去除@前缀重命名、移动到目标路径。"
    plugin_icon = "Folder"
    plugin_version = "1.4.1"
    plugin_author = "wq2020wdm"
    plugin_order = 30
    auth_level = 1

    _lock = Lock()
    _u115_instance = None   # 缓存底层模块实例

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
    #  获取底层 u115 实例（动态探测类名）
    # ═══════════════════════════════════════════════════════════

    def _get_u115_instance(self):
        if movie115_organizer._u115_instance is not None:
            return movie115_organizer._u115_instance
        try:
            import app.modules.filemanager.storages.u115 as u115_mod

            # 找出模块中所有类
            all_classes = [
                (name, cls) for name, cls in inspect.getmembers(u115_mod, inspect.isclass)
                if cls.__module__ == u115_mod.__name__
            ]
            logger.info(f"【115整理】u115 模块中的类: {[n for n, _ in all_classes]}")

            if not all_classes:
                logger.error("【115整理】u115 模块中没有找到任何类")
                return None

            # 取第一个（通常只有一个主类）
            cls_name, cls = all_classes[0]
            inst = cls()
            methods = sorted([m for m in dir(inst) if not m.startswith("_")])
            move_methods = [m for m in methods if any(k in m.lower() for k in ("move", "mv", "transfer", "copy"))]
            logger.info(f"【115整理】{cls_name} 实例化成功，move相关方法: {move_methods}")
            logger.info(f"【115整理】{cls_name} 全部方法: {methods}")

            movie115_organizer._u115_instance = inst
            return inst
        except Exception as e:
            logger.error(f"【115整理】获取 u115 实例失败: {e}", exc_info=True)
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

        # Step 5: 移动文件夹
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
    #  移动：用 fileid 直接调底层模块
    # ═══════════════════════════════════════════════════════════

    def _do_move(self, storage: StorageChain, src: FileItem, dst: FileItem) -> bool:
        src_id = getattr(src, "fileid", None)
        dst_id = getattr(dst, "fileid", None)
        logger.info(f"【115整理】move fileid: src={src_id}  dst={dst_id}")

        if not src_id or not dst_id:
            logger.error(f"【115整理】fileid 为空，无法移动")
            return False

        # ── 方案1：StorageChain.run_module("move_file") ─────
        logger.info("【115整理】方案1: run_module('move_file')")
        try:
            result = storage.run_module("move_file", src, dst)
            if result is not None and result is not False:
                logger.info(f"【115整理】方案1成功，结果: {result}")
                return True
            logger.info(f"【115整理】方案1返回: {result}")
        except Exception as e:
            logger.warning(f"【115整理】方案1异常: {e}")

        # ── 方案2：动态探测 u115 底层类，用 fileid 调 move ──
        logger.info("【115整理】方案2: 动态探测 u115 实例")
        u115 = self._get_u115_instance()
        if u115 is not None:
            # 尝试所有 move 相关方法，每种参数形式都试
            move_methods = [m for m in dir(u115)
                            if not m.startswith("_")
                            and any(k in m.lower() for k in ("move", "mv"))]
            logger.info(f"【115整理】u115 move方法候选: {move_methods}")

            for mname in move_methods:
                method = getattr(u115, mname, None)
                if not callable(method):
                    continue
                # 115 move API 通常是 move([fileid_list], dst_cid)
                for call_args in (
                    ([src_id], dst_id),          # 列表形式
                    (src_id, dst_id),             # 单值形式
                    (src, dst),                   # FileItem 形式
                ):
                    try:
                        logger.info(f"【115整理】尝试 u115.{mname}{call_args}")
                        result = method(*call_args)
                        logger.info(f"【115整理】u115.{mname} 结果: {result}")
                        # 判断成功：非 None、非 False、非空 dict/list
                        if result not in (None, False, {}, []):
                            return True
                        # 有些 API 成功返回空 dict，再检查一下
                        if result == {} or result == []:
                            logger.info(f"【115整理】u115.{mname} 返回空对象，视为成功")
                            return True
                    except TypeError:
                        continue  # 参数不匹配，换下一种形式
                    except Exception as e:
                        logger.warning(f"【115整理】u115.{mname}{call_args} 异常: {e}")
                        break  # 同一方法不同参数都失败，换方法

        # ── 方案3：直接用 requests 调 115 WebAPI ───────────
        logger.info("【115整理】方案3: 直接调用 115 WebAPI /files/move")
        try:
            result = self._webapi_move(src_id, dst_id)
            if result:
                logger.info("【115整理】方案3成功")
                return True
        except Exception as e:
            logger.warning(f"【115整理】方案3异常: {e}")

        return False

    def _webapi_move(self, src_id: str, dst_id: str) -> bool:
        """
        从 MoviePilot 已有的 115 认证信息里取 cookie，
        直接调 115 官方 WebAPI 执行移动。
        """
        try:
            # 从 MoviePilot 配置里获取 115 cookie
            cookie = self._get_115_cookie()
            if not cookie:
                logger.error("【115整理】无法获取 115 cookie，跳过方案3")
                return False

            import requests
            url = "https://webapi.115.com/files/move"
            data = {
                "pid": dst_id,
                "fid[0]": src_id,
            }
            headers = {
                "Cookie": cookie,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://115.com",
            }
            resp = requests.post(url, data=data, headers=headers, timeout=15)
            resp_json = resp.json()
            logger.info(f"【115整理】115 WebAPI move 响应: {resp_json}")

            if resp_json.get("state") or resp_json.get("errno") == 0:
                return True
            logger.error(f"【115整理】115 WebAPI move 失败: {resp_json}")
            return False

        except Exception as e:
            logger.error(f"【115整理】_webapi_move 异常: {e}", exc_info=True)
            return False

    def _get_115_cookie(self) -> Optional[str]:
        """从 MoviePilot 存储配置中读取 115 cookie。"""
        try:
            # 方式1：从 app.core.config 的 Settings 读取
            from app.core.config import settings
            for attr in ("P115_COOKIE", "p115_cookie", "STORAGE_115_COOKIE",
                         "u115_cookie", "U115_COOKIE", "PAN115_COOKIE"):
                val = getattr(settings, attr, None)
                if val:
                    logger.info(f"【115整理】从 settings.{attr} 获取到 cookie")
                    return val

            # 方式2：从数据库站点/存储配置读取
            try:
                from app.db.systemconfig_oper import SystemConfigOper
                sc = SystemConfigOper()
                for key in ("115Cookie", "Pan115Cookie", "u115", "storage_u115"):
                    val = sc.get(key)
                    if val:
                        logger.info(f"【115整理】从 SystemConfig[{key!r}] 获取到 cookie")
                        return val if isinstance(val, str) else str(val)
            except Exception:
                pass

            # 方式3：从 u115 模块实例读取 cookie 属性
            u115 = self._get_u115_instance()
            if u115:
                for attr in ("cookie", "_cookie", "cookies", "_cookies",
                             "access_token", "_access_token"):
                    val = getattr(u115, attr, None)
                    if val:
                        logger.info(f"【115整理】从 u115.{attr} 获取到认证信息")
                        return val if isinstance(val, str) else str(val)

            logger.error("【115整理】所有方式均无法获取 115 cookie")
            return None

        except Exception as e:
            logger.error(f"【115整理】_get_115_cookie 异常: {e}", exc_info=True)
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
                        f"当前层: {[item.name for item in current_items[:10]]}，目标: {path}"
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
