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
    plugin_version = "1.4.3"
    plugin_author = "wq2020wdm"
    plugin_order = 30
    auth_level = 1

    _lock = Lock()
    _u115_inst = None

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
    #  获取 U115Pan 实例
    # ═══════════════════════════════════════════════════════════

    def _get_u115(self):
        if movie115_organizer._u115_inst is not None:
            return movie115_organizer._u115_inst
        try:
            from app.modules.filemanager.storages.u115 import U115Pan
            inst = U115Pan()
            # 打印 move 方法的真实签名
            try:
                sig = inspect.signature(inst.move)
                logger.info(f"【115整理】U115Pan.move 签名: {sig}")
                src = inspect.getsource(inst.move)
                logger.info(f"【115整理】U115Pan.move 源码:\n{src[:600]}")
            except Exception as e:
                logger.info(f"【115整理】无法读取 move 签名/源码: {e}")
            movie115_organizer._u115_inst = inst
            return inst
        except Exception as e:
            logger.error(f"【115整理】获取 U115Pan 失败: {e}", exc_info=True)
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
        ok = self._do_move(folder, target_item)
        logger.info(f"【115整理】[{fname}] 移动结果: {'✅成功' if ok else '❌失败'}")
        return ok

    # ═══════════════════════════════════════════════════════════
    #  移动：三种方案
    # ═══════════════════════════════════════════════════════════

    def _do_move(self, src: FileItem, dst: FileItem) -> bool:
        src_id = getattr(src, "fileid", None)
        dst_id = getattr(dst, "fileid", None)
        logger.info(f"【115整理】move: src_id={src_id} dst_id={dst_id}")

        u115 = self._get_u115()

        # ── 方案1：用 inspect 读取 move 签名后正确调用 ─────────
        if u115 is not None and hasattr(u115, "move"):
            try:
                sig = inspect.signature(u115.move)
                params = list(sig.parameters.keys())
                logger.info(f"【115整理】方案1: U115Pan.move 参数列表={params}")

                # 根据参数名猜测正确的调用方式
                # 常见形式: move(file_item, target) / move(fileids, pid) / move(src_fileid, dst_fileid)
                if len(params) == 1:
                    # 可能只有一个参数，是 fileitem
                    result = u115.move(src)
                elif len(params) == 2:
                    p0, p1 = params[0], params[1]
                    if "item" in p0 or "file" in p0.lower():
                        result = u115.move(src, dst)
                    elif "id" in p0 or "cid" in p0 or "pid" in p1:
                        result = u115.move(src_id, dst_id)
                    else:
                        # 都试一遍
                        result = None
                        for args in ((src, dst), (src_id, dst_id), ([src_id], dst_id)):
                            try:
                                result = u115.move(*args)
                                logger.info(f"【115整理】方案1 move{args} 结果: {result}")
                                break
                            except TypeError:
                                continue
                else:
                    result = None

                logger.info(f"【115整理】方案1 结果: {result}")
                if result not in (None, False):
                    return True
                if isinstance(result, dict) and result.get("state"):
                    return True

            except Exception as e:
                logger.warning(f"【115整理】方案1异常: {e}", exc_info=True)

        # ── 方案2：用 U115Pan 内置 session(httpx.Client) 调 OpenAPI ──
        # session 已配置好 Bearer token，直接调即可
        logger.info("【115整理】方案2: 用 U115Pan.session 调 OpenAPI")
        if u115 is not None:
            session = getattr(u115, "session", None)
            base_url = getattr(u115, "base_url", "https://proapi.115.com")
            if session is not None:
                # 115 OpenAPI 移动文件夹接口候选
                endpoints = [
                    "/open/folder/move",
                    "/open/files/move",
                    "/open/ufile/move",
                ]
                for ep in endpoints:
                    url = f"{base_url}{ep}"
                    # 参数形式1：file_ids[]=xxx&pid=yyy
                    payloads = [
                        {"file_ids[]": src_id, "pid": dst_id},
                        {"fid[]": src_id, "pid": dst_id},
                        {"file_id": src_id, "pid": dst_id},
                        {"cid": src_id, "pid": dst_id},
                    ]
                    for payload in payloads:
                        try:
                            logger.info(f"【115整理】POST {url} data={payload}")
                            resp = session.post(url, data=payload, timeout=15)
                            rj = resp.json()
                            logger.info(f"【115整理】响应: {rj}")
                            if rj.get("state") is True or rj.get("errno") == 0:
                                logger.info(f"【115整理】方案2成功: {ep}")
                                return True
                            # errno 非0 且不是404，说明接口存在但参数错，继续试其他 payload
                            if rj.get("errno") not in (None, 404, 10004, 20130827):
                                break  # 接口正确，但操作失败，不必换 payload
                        except Exception as e:
                            logger.warning(f"【115整理】POST {url} {payload} 异常: {e}")

        # ── 方案3：用 access_token 直接 requests 调 OpenAPI ───
        logger.info("【115整理】方案3: requests + Bearer token 调 OpenAPI")
        if u115 is not None:
            token = getattr(u115, "access_token", None)
            base_url = getattr(u115, "base_url", "https://proapi.115.com")
            if token:
                import requests
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/x-www-form-urlencoded",
                }
                endpoints = ["/open/folder/move", "/open/files/move", "/open/ufile/move"]
                payloads = [
                    {"file_ids[]": src_id, "pid": dst_id},
                    {"fid[]": src_id, "pid": dst_id},
                    {"file_id": src_id, "pid": dst_id},
                ]
                for ep in endpoints:
                    for payload in payloads:
                        try:
                            url = f"{base_url}{ep}"
                            logger.info(f"【115整理】方案3 POST {url} data={payload}")
                            resp = requests.post(url, data=payload, headers=headers, timeout=15)
                            rj = resp.json()
                            logger.info(f"【115整理】方案3 响应: {rj}")
                            if rj.get("state") is True or rj.get("errno") == 0:
                                logger.info(f"【115整理】方案3成功: {ep}")
                                return True
                            if rj.get("errno") not in (None, 404, 10004):
                                break
                        except Exception as e:
                            logger.warning(f"【115整理】方案3 POST {ep} 异常: {e}")

        logger.error("【115整理】所有移动方案均失败")
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
