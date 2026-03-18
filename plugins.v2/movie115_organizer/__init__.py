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
    plugin_version = "1.4.2"
    plugin_author = "wq2020wdm"
    plugin_order = 30
    auth_level = 1

    _lock = Lock()
    _u115_inst = None  # 缓存 U115Pan 实例

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
    #  获取 U115Pan 实例（优先从 MoviePilot 模块系统取已认证实例）
    # ═══════════════════════════════════════════════════════════

    def _get_u115(self):
        """获取已认证的 U115Pan 实例，并打印所有方法供诊断。"""
        if movie115_organizer._u115_inst is not None:
            return movie115_organizer._u115_inst

        import app.modules.filemanager.storages.u115 as u115_mod
        from app.modules.filemanager.storages.u115 import U115Pan

        inst = None

        # ── 优先：从 MoviePilot 模块管理器取已认证单例 ────────
        try:
            from app.helper.module import ModuleHelper
            inst = ModuleHelper.get_instance(U115Pan)
            if inst:
                logger.info("【115整理】从 ModuleHelper 获取到 U115Pan 单例")
        except Exception as e:
            logger.info(f"【115整理】ModuleHelper 获取失败: {e}")

        # ── 次选：从 StorageChain 内部模块列表找 ──────────────
        if inst is None:
            try:
                sc = StorageChain()
                for attr in ("_modules", "modules", "_storages", "storages"):
                    container = getattr(sc, attr, None)
                    if not container:
                        continue
                    items = container.values() if isinstance(container, dict) else container
                    for item in items:
                        if isinstance(item, U115Pan):
                            inst = item
                            logger.info(f"【115整理】从 StorageChain.{attr} 找到 U115Pan 实例")
                            break
                    if inst:
                        break
            except Exception as e:
                logger.info(f"【115整理】StorageChain 内部查找失败: {e}")

        # ── 备用：直接实例化（可能未登录，但方法可用于诊断）──
        if inst is None:
            try:
                inst = U115Pan()
                logger.info("【115整理】直接实例化 U115Pan（可能未登录）")
            except Exception as e:
                logger.error(f"【115整理】U115Pan() 实例化失败: {e}", exc_info=True)
                return None

        # 打印全部方法，重点标注 move 相关
        all_methods = sorted([m for m in dir(inst) if not m.startswith("_")])
        move_methods = [m for m in all_methods if any(k in m.lower() for k in ("move", "mv", "transfer", "copy"))]
        logger.info(f"【115整理】U115Pan 全部方法: {all_methods}")
        logger.info(f"【115整理】U115Pan move相关方法: {move_methods}")

        # 打印所有属性，帮助找 cookie/client
        attrs = {}
        for a in all_methods:
            try:
                v = getattr(inst, a)
                if not callable(v):
                    attrs[a] = str(v)[:80]
            except Exception:
                pass
        logger.info(f"【115整理】U115Pan 非方法属性: {attrs}")

        movie115_organizer._u115_inst = inst
        return inst

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

        files = storage.list_files(folder) or []
        all_files = [f for f in files if f.type == "file"]
        logger.info(f"【115整理】[{fname}] {len(all_files)} 个文件: {[(f.name, self._fmt(f.size)) for f in all_files]}")
        if not all_files:
            logger.info(f"【115整理】[{fname}] 为空（下载中），跳过")
            return False

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

        files_after = storage.list_files(folder) or []
        remaining = [f for f in files_after if f.type == "file"]
        still_small = [f for f in remaining if (f.size or 0) < threshold_bytes]
        logger.info(f"【115整理】[{fname}] 删除后剩余{len(remaining)}个，仍小于阈值:{len(still_small)}个")
        if not remaining:
            logger.info(f"【115整理】[{fname}] 删完为空，可能下载中，跳过")
            return False
        if still_small:
            logger.warning(f"【115整理】[{fname}] 小文件未删完，跳过")
            return False

        for bf in [f for f in remaining if (f.size or 0) >= threshold_bytes]:
            if "@" in bf.name:
                new_fname = bf.name.split("@")[-1]
                logger.info(f"【115整理】[{fname}] 重命名: {bf.name!r} → {new_fname!r}")
                try:
                    ok = storage.rename_file(bf, new_fname)
                    logger.info(f"【115整理】[{fname}] 重命名{'成功' if ok else '失败'}")
                except Exception as e:
                    logger.error(f"【115整理】[{fname}] 重命名异常: {e}", exc_info=True)

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
    #  移动
    # ═══════════════════════════════════════════════════════════

    def _do_move(self, storage: StorageChain, src: FileItem, dst: FileItem) -> bool:
        src_id = getattr(src, "fileid", None)
        dst_id = getattr(dst, "fileid", None)
        logger.info(f"【115整理】move fileid: src={src_id} dst={dst_id}")

        # ── 方案1：U115Pan 实例的 move 方法 ────────────────────
        u115 = self._get_u115()
        if u115 is not None:
            move_methods = [m for m in dir(u115)
                            if not m.startswith("_")
                            and any(k in m.lower() for k in ("move", "mv"))]
            for mname in move_methods:
                method = getattr(u115, mname)
                # 依次尝试不同参数形式
                for args in (([src_id], dst_id), (src_id, dst_id), (src, dst)):
                    try:
                        logger.info(f"【115整理】尝试 U115Pan.{mname}{args}")
                        result = method(*args)
                        logger.info(f"【115整理】U115Pan.{mname} 结果: {result}")
                        if result not in (None, False):
                            return True
                        if isinstance(result, (dict, list)) and result == {} or result == []:
                            return True
                    except TypeError:
                        continue
                    except Exception as e:
                        logger.warning(f"【115整理】U115Pan.{mname}{args} 异常: {e}")
                        break

        # ── 方案2：直接调 115 WebAPI（用 cookie）───────────────
        logger.info("【115整理】方案2: 115 WebAPI /files/move")
        cookie = self._get_cookie(u115)
        if cookie:
            try:
                import requests
                resp = requests.post(
                    "https://webapi.115.com/files/move",
                    data={"pid": dst_id, "fid[0]": src_id},
                    headers={
                        "Cookie": cookie,
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Referer": "https://115.com",
                    },
                    timeout=15,
                )
                rj = resp.json()
                logger.info(f"【115整理】WebAPI 响应: {rj}")
                if rj.get("state") is True or rj.get("errno") == 0:
                    return True
                logger.error(f"【115整理】WebAPI 返回失败: {rj}")
            except Exception as e:
                logger.error(f"【115整理】WebAPI 异常: {e}", exc_info=True)
        else:
            logger.error("【115整理】方案2：无法获取 cookie")

        return False

    def _get_cookie(self, u115_inst=None) -> Optional[str]:
        """从 U115Pan 实例或 MoviePilot 配置中获取 115 cookie。"""

        # 从 U115Pan 实例属性中找
        if u115_inst is not None:
            for attr in ("cookie", "_cookie", "cookies", "_cookies",
                         "access_token", "_access_token", "credentials"):
                val = getattr(u115_inst, attr, None)
                if val and isinstance(val, str) and len(val) > 10:
                    logger.info(f"【115整理】cookie 来自 U115Pan.{attr}")
                    return val
                if val and isinstance(val, dict):
                    # dict 类型的 cookie
                    cookie_str = "; ".join(f"{k}={v}" for k, v in val.items())
                    if cookie_str:
                        logger.info(f"【115整理】cookie(dict) 来自 U115Pan.{attr}")
                        return cookie_str

            # 尝试 U115Pan 内部的 client/driver 对象
            for sub_attr in ("client", "_client", "driver", "_driver", "api"):
                sub = getattr(u115_inst, sub_attr, None)
                if sub is None:
                    continue
                for attr in ("cookie", "_cookie", "cookies", "_cookies"):
                    val = getattr(sub, attr, None)
                    if val and isinstance(val, str) and len(val) > 10:
                        logger.info(f"【115整理】cookie 来自 U115Pan.{sub_attr}.{attr}")
                        return val

        # 从 MoviePilot SystemConfigOper 读取
        try:
            from app.db.systemconfig_oper import SystemConfigOper
            sc = SystemConfigOper()
            # 尝试常见 key 名
            for key in ("115Cookie", "115_cookie", "Pan115Cookie",
                        "StorageU115", "storage_u115", "u115_cookie",
                        "U115_COOKIE", "PAN115_COOKIE"):
                try:
                    val = sc.get(key)
                    if val:
                        logger.info(f"【115整理】cookie 来自 SystemConfig[{key!r}]")
                        return val if isinstance(val, str) else str(val)
                except Exception:
                    pass

            # 尝试通过 SystemConfigKey 枚举
            try:
                from app.schemas.types import SystemConfigKey
                all_keys = [k for k in dir(SystemConfigKey) if not k.startswith("_")]
                logger.info(f"【115整理】SystemConfigKey 枚举值: {all_keys}")
                for k in all_keys:
                    if "115" in k or "pan" in k.lower() or "storage" in k.lower():
                        val = sc.get(getattr(SystemConfigKey, k))
                        if val:
                            logger.info(f"【115整理】cookie 来自 SystemConfigKey.{k}")
                            return val if isinstance(val, str) else str(val)
            except Exception as e:
                logger.info(f"【115整理】SystemConfigKey 枚举查找: {e}")

        except Exception as e:
            logger.warning(f"【115整理】SystemConfigOper 获取失败: {e}")

        # 从 settings 读取
        try:
            from app.core.config import settings
            settings_attrs = [a for a in dir(settings) if "115" in a or "pan115" in a.lower()]
            logger.info(f"【115整理】settings 中含'115'的属性: {settings_attrs}")
            for attr in settings_attrs:
                val = getattr(settings, attr, None)
                if val:
                    logger.info(f"【115整理】cookie 来自 settings.{attr}")
                    return str(val)
        except Exception as e:
            logger.warning(f"【115整理】settings 查找失败: {e}")

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
