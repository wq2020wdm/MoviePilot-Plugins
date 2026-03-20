import os
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

from app.chain.storage import StorageChain
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.schemas.file import FileItem
from app.schemas.types import EventType


class movie115_organizer(_PluginBase):
    plugin_id = "movie115_organizer"
    plugin_name = "115 目录洗白整理"
    plugin_desc = "监控115网盘目录，自动删除小文件、去除@前缀重命名、移动到目标路径，并生成STRM文件。"
    plugin_icon = "https://raw.githubusercontent.com/wq2020wdm/MoviePilot-Plugins/main/icons/98tang.png"
    plugin_version = "1.4.8"
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
    _strm_enabled: bool = False
    _strm_local_path: str = ""
    _strm_template: str = "http://10.0.0.5:7811/redirect?path={cloud_file}&pickcode={pick_code}"
    _mdcx_container: str = ""

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled           = config.get("enabled", False)
            self._cron              = config.get("cron", "0 */2 * * *")
            self._monitor_paths     = config.get("monitor_paths", "")
            self._target_path       = config.get("target_path", "")
            try:
                self._size_threshold_mb = int(config.get("size_threshold_mb", 500))
            except Exception:
                self._size_threshold_mb = 500
            self._notify            = config.get("notify", True)
            self._run_once          = config.get("run_once", False)
            self._strm_enabled      = config.get("strm_enabled", False)
            self._strm_local_path   = config.get("strm_local_path", "")
            self._strm_template     = config.get("strm_template",
                "http://10.0.0.5:7811/redirect?path={cloud_file}&pickcode={pick_code}")
            self._mdcx_container    = config.get("mdcx_container", "")

        if self._run_once:
            self._run_once = False
            self.update_config({"run_once": False, **self._current_config()})
            self.execute()

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        pass

    # ═══════════════════════════════════════════════════════════
    #  详情页（默认打开页）
    # ═══════════════════════════════════════════════════════════

    def get_page(self) -> List[dict]:
        """返回插件详情页，默认打开时展示。"""
        # 状态徽章
        status_color = "success" if self._enabled else "default"
        status_text  = "运行中" if self._enabled else "已停用"
        strm_text    = "已开启" if self._strm_enabled else "未开启"

        # 监控目录列表
        monitor_list = [p.strip() for p in self._monitor_paths.splitlines() if p.strip()]

        monitor_chips = []
        for p in monitor_list:
            monitor_chips.append({
                "component": "VChip",
                "props": {"class": "ma-1", "size": "small", "color": "primary", "variant": "tonal"},
                "text": p
            })
        if not monitor_chips:
            monitor_chips = [{"component": "span", "props": {"class": "text-caption text-disabled"},
                               "text": "未配置"}]

        return [
            {
                "component": "VRow",
                "content": [
                    # ── 左侧：状态卡片 ────────────────────────
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 4},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal", "class": "mb-3"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "content": [
                                            {
                                                "component": "div",
                                                "props": {"class": "d-flex align-center justify-space-between mb-2"},
                                                "content": [
                                                    {"component": "span", "props": {"class": "text-subtitle-2"},
                                                     "text": "插件状态"},
                                                    {"component": "VChip",
                                                     "props": {"color": status_color, "size": "small", "label": True},
                                                     "text": status_text},
                                                ]
                                            },
                                            {
                                                "component": "div",
                                                "props": {"class": "d-flex align-center justify-space-between mb-2"},
                                                "content": [
                                                    {"component": "span", "props": {"class": "text-caption text-medium-emphasis"},
                                                     "text": "Cron 计划"},
                                                    {"component": "span", "props": {"class": "text-caption font-weight-bold"},
                                                     "text": self._cron or "未设置"},
                                                ]
                                            },
                                            {
                                                "component": "div",
                                                "props": {"class": "d-flex align-center justify-space-between mb-2"},
                                                "content": [
                                                    {"component": "span", "props": {"class": "text-caption text-medium-emphasis"},
                                                     "text": "垃圾文件阈值"},
                                                    {"component": "span", "props": {"class": "text-caption font-weight-bold"},
                                                     "text": f"{self._size_threshold_mb} MB"},
                                                ]
                                            },
                                            {
                                                "component": "div",
                                                "props": {"class": "d-flex align-center justify-space-between mb-2"},
                                                "content": [
                                                    {"component": "span", "props": {"class": "text-caption text-medium-emphasis"},
                                                     "text": "STRM 生成"},
                                                    {"component": "VChip",
                                                     "props": {
                                                         "color": "success" if self._strm_enabled else "default",
                                                         "size": "x-small", "label": True
                                                     },
                                                     "text": strm_text},
                                                ]
                                            },
                                            {
                                                "component": "div",
                                                "props": {"class": "d-flex align-center justify-space-between"},
                                                "content": [
                                                    {"component": "span", "props": {"class": "text-caption text-medium-emphasis"},
                                                     "text": "发送通知"},
                                                    {"component": "VChip",
                                                     "props": {
                                                         "color": "success" if self._notify else "default",
                                                         "size": "x-small", "label": True
                                                     },
                                                     "text": "是" if self._notify else "否"},
                                                ]
                                            },
                                            {
                                                "component": "div",
                                                "props": {"class": "d-flex align-center justify-space-between mt-2"},
                                                "content": [
                                                    {"component": "span", "props": {"class": "text-caption text-medium-emphasis"},
                                                     "text": "刮削容器"},
                                                    {"component": "span", "props": {"class": "text-caption font-weight-bold"},
                                                     "text": self._mdcx_container or "未配置"},
                                                ]
                                            },
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    # ── 右侧：路径信息 ────────────────────────
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 8},
                        "content": [
                            {
                                "component": "VCard",
                                "props": {"variant": "tonal", "class": "mb-3"},
                                "content": [
                                    {
                                        "component": "VCardText",
                                        "content": [
                                            {
                                                "component": "div",
                                                "props": {"class": "mb-3"},
                                                "content": [
                                                    {"component": "div",
                                                     "props": {"class": "text-subtitle-2 mb-1"},
                                                     "text": "📂 监控目录"},
                                                    {
                                                        "component": "div",
                                                        "props": {"class": "d-flex flex-wrap"},
                                                        "content": monitor_chips
                                                    }
                                                ]
                                            },
                                            {
                                                "component": "VDivider",
                                                "props": {"class": "my-2"}
                                            },
                                            {
                                                "component": "div",
                                                "props": {"class": "mb-3"},
                                                "content": [
                                                    {"component": "div",
                                                     "props": {"class": "text-subtitle-2 mb-1"},
                                                     "text": "📁 移动目标路径"},
                                                    {"component": "span",
                                                     "props": {"class": "text-caption"},
                                                     "text": self._target_path or "未配置"}
                                                ]
                                            },
                                            {
                                                "component": "VDivider",
                                                "props": {"class": "my-2"}
                                            },
                                            {
                                                "component": "div",
                                                "content": [
                                                    {"component": "div",
                                                     "props": {"class": "text-subtitle-2 mb-1"},
                                                     "text": "🎬 STRM 本地根目录"},
                                                    {"component": "span",
                                                     "props": {"class": "text-caption"},
                                                     "text": self._strm_local_path or "未配置"}
                                                ]
                                            },
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                ]
            },
            # ── 说明提示 ──────────────────────────────────────
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
                                    "density": "compact",
                                    "text": (
                                        "💡 使用说明：\n"
                                        "① 点击右下角 ⚙️ 齿轮图标进入配置页设置参数\n"
                                        "② Telegram Bot 发送 /run_115_clean 可立即触发整理\n"
                                        "③ 流程：扫描子文件夹 → 删除垃圾文件 → 重命名(去@前缀) → 移动到目标路径 → 生成STRM\n"
                                        "④ 移动若遇到限速(770004)属正常现象，等待后重试即可"
                                    )
                                }
                            }
                        ]
                    }
                ]
            }
        ]

    # ═══════════════════════════════════════════════════════════
    #  Telegram Bot 命令
    # ═══════════════════════════════════════════════════════════

    def get_command(self) -> List[Dict[str, Any]]:
        return [
            {
                "cmd": "/run_115_clean",
                "event": EventType.PluginAction,
                "desc": "立即整理 115 目录",
                "category": "整理",
                "data": {"action": "run_115_clean"},
            }
        ]

    @eventmanager.register(EventType.PluginAction)
    def handle_command(self, event: Event):
        if not event or not event.event_data:
            return
        if event.event_data.get("action") != "run_115_clean":
            return
        logger.info("【115整理】收到 Bot 指令，立即执行")
        self.post_message(
            mtype=NotificationType.SiteMessage,
            title="115整理",
            text="已收到指令，开始执行整理任务…"
        )
        import threading
        threading.Thread(target=self.execute, daemon=True).start()

    # ═══════════════════════════════════════════════════════════
    #  获取 U115Pan 实例
    # ═══════════════════════════════════════════════════════════

    def _get_u115(self):
        if movie115_organizer._u115_inst is not None:
            return movie115_organizer._u115_inst
        try:
            from app.modules.filemanager.storages.u115 import U115Pan
            inst = U115Pan()
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
            total_strm = 0

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
                    moved, strm_count = self._process_folder(storage, folder)
                    if moved:
                        total_moved += 1
                        total_strm += strm_count

            logger.info(f"【115整理】本次共归档 {total_moved} 个文件夹，生成 STRM {total_strm} 个")
            if self._notify and total_moved > 0:
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title="115目录洗白整理完成",
                    text=f"本次共整理并移动了 {total_moved} 个文件夹，生成 STRM {total_strm} 个。"
                )

            # 仅在实际生成了 STRM 文件时才重启 mdcx
            if total_strm > 0 and self._mdcx_container.strip():
                self._restart_mdcx(self._mdcx_container.strip())
        finally:
            self._lock.release()

    # ═══════════════════════════════════════════════════════════
    #  单文件夹处理
    # ═══════════════════════════════════════════════════════════

    def _process_folder(self, storage: StorageChain, folder: FileItem) -> Tuple[bool, int]:
        """返回 (是否成功移动, 成功生成的STRM数量)"""
        fname = folder.name
        threshold_bytes = self._size_threshold_mb * 1024 * 1024
        logger.info(f"【115整理】>>> 开始处理: {fname}")

        files = storage.list_files(folder) or []
        all_files = [f for f in files if f.type == "file"]
        logger.info(f"【115整理】[{fname}] {len(all_files)} 个文件: {[(f.name, self._fmt(f.size)) for f in all_files]}")
        if not all_files:
            logger.info(f"【115整理】[{fname}] 为空（下载中），跳过")
            return False, 0

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
            return False, 0
        if still_small:
            logger.warning(f"【115整理】[{fname}] 小文件未删完，跳过")
            return False, 0

        # 重命名大文件，记录 (最终文件名, pickcode)
        strm_targets: List[Tuple[str, str]] = []
        for bf in [f for f in remaining if (f.size or 0) >= threshold_bytes]:
            pickcode = getattr(bf, "pickcode", None) or ""
            if "@" in bf.name:
                new_fname = bf.name.split("@")[-1]
                logger.info(f"【115整理】[{fname}] 重命名: {bf.name!r} → {new_fname!r}")
                try:
                    ok = storage.rename_file(bf, new_fname)
                    logger.info(f"【115整理】[{fname}] 重命名{'成功' if ok else '失败'}")
                    strm_targets.append((new_fname if ok else bf.name, pickcode))
                except Exception as e:
                    logger.error(f"【115整理】[{fname}] 重命名异常: {e}", exc_info=True)
                    strm_targets.append((bf.name, pickcode))
            else:
                strm_targets.append((bf.name, pickcode))

        # 移动
        target_path = self._target_path.rstrip("/")
        logger.info(f"【115整理】[{fname}] 开始移动 → {target_path}")
        ok = self._do_move(folder, target_path)
        logger.info(f"【115整理】[{fname}] 移动结果: {'✅成功' if ok else '❌失败'}")
        if not ok:
            return False, 0

        # 生成 STRM，统计实际成功数量
        strm_count = 0
        if self._strm_enabled and self._strm_local_path.strip():
            for file_name, pickcode in strm_targets:
                if self._generate_strm(fname, file_name, pickcode, target_path):
                    strm_count += 1

        return True, strm_count

    # ═══════════════════════════════════════════════════════════
    #  STRM 生成
    # ═══════════════════════════════════════════════════════════

    def _generate_strm(self, folder_name: str, file_name: str,
                       pickcode: str, cloud_target_path: str) -> bool:
        """生成 STRM 文件，返回是否成功。"""
        try:
            local_dir = Path(self._strm_local_path.rstrip("/")) / folder_name
            local_dir.mkdir(parents=True, exist_ok=True)
            stem = Path(file_name).stem
            strm_file = local_dir / f"{stem}.strm"
            cloud_file = f"{cloud_target_path}/{folder_name}/{file_name}"
            content = (self._strm_template
                       .replace("{cloud_file}", cloud_file)
                       .replace("{pick_code}", pickcode))
            strm_file.write_text(content, encoding="utf-8")
            logger.info(f"【115整理】STRM 生成成功: {strm_file}")
            logger.info(f"【115整理】STRM 内容: {content}")
            return True
        except Exception as e:
            logger.error(f"【115整理】STRM 生成失败 ({folder_name}/{file_name}): {e}", exc_info=True)
            return False

    # ═══════════════════════════════════════════════════════════
    #  重启 mdcx 容器（触发自动刮削）
    # ═══════════════════════════════════════════════════════════

    def _restart_mdcx(self, container_name: str):
        """
        通过 Docker Unix Socket 重启指定容器。
        等效于：curl --unix-socket /var/run/docker.sock -X POST http://localhost/containers/{name}/restart
        需要 MoviePilot 容器挂载了 /var/run/docker.sock。
        """
        import socket
        import json

        sock_path = "/var/run/docker.sock"
        if not os.path.exists(sock_path):
            logger.error(f"【115整理】Docker socket 不存在: {sock_path}，无法重启 {container_name}")
            return

        try:
            logger.info(f"【115整理】正在重启容器: {container_name}")
            # 构造 HTTP over Unix socket 请求
            request = (
                f"POST /containers/{container_name}/restart HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Content-Length: 0\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            )
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect(sock_path)
            sock.sendall(request.encode("utf-8"))

            # 读取响应
            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            sock.close()

            # 解析 HTTP 状态码
            status_line = response.decode("utf-8", errors="ignore").split("\r\n")[0]
            logger.info(f"【115整理】Docker 重启响应: {status_line}")

            if "204" in status_line:
                logger.info(f"【115整理】✅ 容器 {container_name} 重启成功")
            else:
                logger.warning(f"【115整理】容器 {container_name} 重启响应异常: {status_line}")

        except Exception as e:
            logger.error(f"【115整理】重启容器 {container_name} 失败: {e}", exc_info=True)

    # ═══════════════════════════════════════════════════════════
    #  移动
    # ═══════════════════════════════════════════════════════════

    def _do_move(self, src: FileItem, target_path: str) -> bool:
        u115 = self._get_u115()
        if u115 is None:
            logger.error("【115整理】无法获取 U115Pan 实例")
            return False
        dst_path = Path(target_path)
        logger.info(f"【115整理】U115Pan.move({src.name!r}, {dst_path}, {src.name!r})")
        try:
            ok = u115.move(src, dst_path, src.name)
            logger.info(f"【115整理】U115Pan.move 返回: {ok}")
            return bool(ok)
        except Exception as e:
            logger.error(f"【115整理】U115Pan.move 异常: {e}", exc_info=True)
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
            "enabled": self._enabled,
            "cron": self._cron,
            "monitor_paths": self._monitor_paths,
            "target_path": self._target_path,
            "size_threshold_mb": self._size_threshold_mb,
            "notify": self._notify,
            "strm_enabled": self._strm_enabled,
            "strm_local_path": self._strm_local_path,
            "strm_template": self._strm_template,
            "mdcx_container": self._mdcx_container,
        }

    # ═══════════════════════════════════════════════════════════
    #  V2 接口
    # ═══════════════════════════════════════════════════════════

    def get_api(self) -> List[dict]: return []

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
                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                            {"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                            {"component": "VSwitch", "props": {"model": "notify", "label": "发送通知"}}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                            {"component": "VSwitch", "props": {"model": "strm_enabled", "label": "生成STRM"}}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [
                            {"component": "VSwitch", "props": {"model": "run_once", "label": "保存后运行一次"}}]},
                    ]},
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                            {"component": "VTextField", "props": {"model": "cron", "label": "Cron 表达式",
                                "hint": "建议间隔≥30分钟，如 0 */2 * * *"}}]},
                        {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [
                            {"component": "VTextField", "props": {"model": "size_threshold_mb",
                                "label": "垃圾文件阈值 (MB)", "type": "number"}}]},
                    ]},
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12}, "content": [
                            {"component": "VTextarea", "props": {"model": "monitor_paths",
                                "label": "监控目录（每行一个115路径）",
                                "placeholder": "/CloudNAS/temp/小姐姐",
                                "rows": 3}}]},
                    ]},
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12}, "content": [
                            {"component": "VTextField", "props": {"model": "target_path",
                                "label": "115 移动目标路径",
                                "placeholder": "/CloudNAS/电影"}}]},
                    ]},
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12}, "content": [
                            {"component": "VTextField", "props": {"model": "strm_local_path",
                                "label": "STRM 本地根目录（需开启生成STRM）",
                                "placeholder": "/media/strm/电影"}}]},
                    ]},
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12}, "content": [
                            {"component": "VTextField", "props": {"model": "strm_template",
                                "label": "STRM 内容模板",
                                "hint": "{cloud_file}=云端完整路径  {pick_code}=115 pickcode"}}]},
                    ]},
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12}, "content": [
                            {"component": "VTextField", "props": {
                                "model": "mdcx_container",
                                "label": "mdcx 容器名",
                                "placeholder": "mdcx",
                                "hint": "需在 mdcx 里开启「启动软件自动刮削」选项，生成STRM后将自动重启该容器触发刮削（留空则不重启）"
                            }}]},
                    ]},
                    {"component": "VRow", "content": [
                        {"component": "VCol", "props": {"cols": 12}, "content": [
                            {"component": "VAlert", "props": {
                                "type": "info", "variant": "tonal",
                                "text": (
                                    "流程：扫描监控目录子文件夹 → 删除小于阈值的垃圾文件 → "
                                    "大文件名含@则去除@前缀重命名 → 移动整个文件夹到目标路径 → "
                                    "（可选）在本地生成 .strm 文件。\n"
                                    "Telegram Bot 指令：/run_115_clean  可立即触发整理。"
                                )}}],
                        },
                    ]},
                ]
            }
        ], {
            "enabled": False,
            "cron": "0 */2 * * *",
            "monitor_paths": "",
            "target_path": "",
            "size_threshold_mb": 500,
            "notify": True,
            "run_once": False,
            "strm_enabled": False,
            "strm_local_path": "",
            "strm_template": "http://10.0.0.5:7811/redirect?path={cloud_file}&pickcode={pick_code}",
            "mdcx_container": "",
        }
