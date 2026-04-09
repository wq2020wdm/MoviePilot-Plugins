import os
import re
import time
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
    plugin_desc = "监控115网盘目录，自动删除小文件、去@重命名、移动到目标路径生成STRM，支持离线下载。"
    plugin_icon = "https://raw.githubusercontent.com/wq2020wdm/MoviePilot-Plugins/main/icons/98tang.png"
    plugin_version = "1.7.5"
    plugin_author = "wq2020wdm"
    plugin_order = 30
    auth_level = 1

    _lock = Lock()
    _u115_inst = None

    _enabled: bool = False
    _cron: str = "0 */2 * * *"
    _monitor_paths: str = ""
    _target_path: str = ""
    _cloud_download_dir: str = ""
    _u115_cookie: str = ""  
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
            self._cloud_download_dir= config.get("cloud_download_dir", "")
            self._u115_cookie       = config.get("u115_cookie", "")
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
        status_color = "success" if self._enabled else "default"
        status_text  = "运行中" if self._enabled else "已停用"
        strm_text    = "已开启" if self._strm_enabled else "未开启"

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
                                                    {"component": "span", "props": {"class": "text-subtitle-2"}, "text": "插件状态"},
                                                    {"component": "VChip", "props": {"color": status_color, "size": "small", "label": True}, "text": status_text},
                                                ]
                                            },
                                            {
                                                "component": "div",
                                                "props": {"class": "d-flex align-center justify-space-between mb-2"},
                                                "content": [
                                                    {"component": "span", "props": {"class": "text-caption text-medium-emphasis"}, "text": "Cron 计划"},
                                                    {"component": "span", "props": {"class": "text-caption font-weight-bold"}, "text": self._cron or "未设置"},
                                                ]
                                            },
                                            {
                                                "component": "div",
                                                "props": {"class": "d-flex align-center justify-space-between mb-2"},
                                                "content": [
                                                    {"component": "span", "props": {"class": "text-caption text-medium-emphasis"}, "text": "STRM 生成"},
                                                    {"component": "VChip", "props": {"color": "success" if self._strm_enabled else "default", "size": "x-small", "label": True}, "text": strm_text},
                                                ]
                                            },
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
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
                                                    {"component": "div", "props": {"class": "text-subtitle-2 mb-1"}, "text": "📂 监控目录"},
                                                    {"component": "div", "props": {"class": "d-flex flex-wrap"}, "content": monitor_chips}
                                                ]
                                            },
                                            {"component": "VDivider", "props": {"class": "my-2"}},
                                            {
                                                "component": "div",
                                                "props": {"class": "mb-3"},
                                                "content": [
                                                    {"component": "div", "props": {"class": "text-subtitle-2 mb-1"}, "text": "📁 移动目标路径"},
                                                    {"component": "span", "props": {"class": "text-caption"}, "text": self._target_path or "未配置"}
                                                ]
                                            },
                                            {"component": "VDivider", "props": {"class": "my-2"}},
                                            {
                                                "component": "div",
                                                "props": {"class": "mb-3"},
                                                "content": [
                                                    {"component": "div", "props": {"class": "text-subtitle-2 mb-1"}, "text": "⬇️ 离线下载默认目录"},
                                                    {"component": "span", "props": {"class": "text-caption"}, "text": self._cloud_download_dir or "未配置"}
                                                ]
                                            },
                                            {"component": "VDivider", "props": {"class": "my-2"}},
                                            {
                                                "component": "div",
                                                "props": {"class": "mb-3"},
                                                "content": [
                                                    {"component": "div", "props": {"class": "text-subtitle-2 mb-1"}, "text": "🍪 115 独立 Cookie (逃生舱)"},
                                                    {"component": "span", "props": {"class": "text-caption"}, "text": "已配置 (强制接管离线功能)" if self._u115_cookie else "未配置 (尝试自动捕获底仓)"}
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
                                        "① Bot 发送 /run_115_clean 立即触发整理。\n"
                                        "② Bot 发送 /cd 链接1 链接2 [目录] 进行115离线下载。\n"
                                        "   成功触发 /cd 后，系统将自动发起一次目录整理。"
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
            },
            {
                "cmd": "/cd",
                "event": EventType.PluginAction,
                "desc": "115 离线下载",
                "category": "下载",
                "data": {"action": "cd"},
            }
        ]

    @eventmanager.register(EventType.PluginAction)
    def handle_command(self, event: Event):
        if not event or not event.event_data:
            return
        action = event.event_data.get("action")

        if action == "run_115_clean":
            logger.info("【115整理】收到 Bot 指令，立即执行整理任务")
            self.post_message(mtype=NotificationType.SiteMessage, title="115整理", text="已收到指令，开始执行整理任务…")
            import threading
            threading.Thread(target=self.execute, daemon=True).start()

        elif action == "cd":
            arg_str = event.event_data.get("arg_str", "")
            
            if not arg_str:
                self.post_message(mtype=NotificationType.SiteMessage, title="115离线下载", text="未提供下载链接。")
                return

            logger.info(f"【115离线】获取到Bot参数: {arg_str}")

            import threading
            threading.Thread(target=self._handle_cd_task, args=(arg_str,), daemon=True).start()

    # ═══════════════════════════════════════════════════════════
    #  离线下载核心逻辑
    # ═══════════════════════════════════════════════════════════

    def _parse_cd_args(self, text: str) -> Tuple[List[str], str]:
        text = re.sub(r'(?<!^)(https?://|ftp://|magnet:\?|ed2k://)', r' \1', str(text), flags=re.IGNORECASE)
        parts = text.split()
        urls = []
        dir_parts = []

        for part in parts:
            if re.match(r'^(https?://|ftp://|magnet:\?|ed2k://)', part, re.IGNORECASE):
                urls.append(part)
            else:
                dir_parts.append(part)

        target_dir = " ".join(dir_parts).strip()
        return urls, target_dir

    def _handle_cd_task(self, text: str):
        urls, target_dir = self._parse_cd_args(text)
        
        if not target_dir:
            target_dir = self._cloud_download_dir

        if not urls:
            self.post_message(
                mtype=NotificationType.SiteMessage, 
                title="115离线下载", 
                text=f"未识别到支持的链接。(支持 http/ftp/magnet/ed2k 开头)\n提取到的内容为: {text[:50]}"
            )
            return

        if not target_dir:
            self.post_message(mtype=NotificationType.SiteMessage, title="115离线下载", text="指令未指定目录，且未配置默认云下载目录，无法执行离线下载。")
            return

        logger.info(f"【115离线】共发现 {len(urls)} 个链接，目标路径: {target_dir}")
        storage = StorageChain()
        folder_item = self._get_fileitem(storage, target_dir)
        
        if not folder_item:
            self.post_message(mtype=NotificationType.SiteMessage, title="115离线下载", text=f"目标目录获取失败或在115中不存在: {target_dir}")
            return

        # ---- 全方位提取真实目录 ID ----
        cid = None
        for attr in ['fileid', 'id', 'item_id', 'fid', 'wp_path_id']:
            val = getattr(folder_item, attr, None)
            if val and str(val) != "0":
                cid = str(val)
                break
                
        if not cid and hasattr(folder_item, 'extra') and isinstance(folder_item.extra, dict):
            for attr in ['fileid', 'id', 'item_id', 'fid']:
                val = folder_item.extra.get(attr)
                if val and str(val) != "0":
                    cid = str(val)
                    break
                    
        if not cid:
            cid = "0"
            logger.warning(f"【115离线】警告：未能提取到 {target_dir} 的真实目录ID，将退化使用根目录(0)。")
        else:
            logger.info(f"【115离线】精准捕获真实目标目录ID: {cid}")

        u115 = self._get_u115()
        if not u115:
            self.post_message(mtype=NotificationType.SiteMessage, title="115离线下载", text="获取 115 实例失败，无法发起离线。")
            return

        cookie = self._u115_cookie.strip()
        if not cookie and hasattr(u115, 'get_config'):
            try:
                conf = u115.get_config()
                if isinstance(conf, dict):
                    cookie = conf.get("cookie", "") or conf.get("cookies", "")
            except Exception:
                pass
        if not cookie:
            cookie = os.getenv("U115_COOKIE", "")

        p115_client = None
        if cookie:
            try:
                from p115client import P115Client
                p115_client = P115Client(cookie)
                logger.info("【115离线】检测到 Cookie，成功初始化 P115Client 独立逃生舱。")
            except ImportError:
                logger.warning("【115离线】找到 Cookie 但系统中未安装 p115client 依赖库。")
            except Exception as e:
                logger.error(f"【115离线】P115Client 初始化报错: {e}")

        success_count = 0
        fail_count = 0

        for url in urls:
            try:
                res = None
                is_success = False
                
                # 策略1：独立逃生舱多态尝试 (绝对优先 wp_path_id)
                if p115_client:
                    try:
                        # 官方115目录入参标准名为 wp_path_id
                        res = p115_client.offline_add_urls([url], wp_path_id=cid)
                        is_success = True
                    except TypeError:
                        try:
                            # 某些远古版本库可能是 pid
                            res = p115_client.offline_add_urls([url], pid=cid)
                            is_success = True
                        except Exception:
                            pass
                
                # 策略2：系统底层 API 盲打 (优先 wp_path_id)
                if not is_success and hasattr(u115, '_request_api'):
                    try:
                        logger.info("【115离线】尝试使用底层 _request_api 盲打 App 接口...")
                        res = u115._request_api(
                            url="https://proapi.115.com/app/lixian/add_task_url",
                            method="POST",
                            data={"url": url, "wp_path_id": str(cid)}
                        )
                        if isinstance(res, dict) and res.get("state"):
                            is_success = True
                    except Exception:
                        pass
                
                # 策略3：传统的反射多态兼容 (优先 wp_path_id 并且补上 folder_id)
                if not is_success:
                    _c = getattr(u115, "client", getattr(u115, "_client", getattr(u115, "pan", None)))
                    
                    if hasattr(_c, 'offline_add_urls'):
                        try:
                            res = _c.offline_add_urls([url], wp_path_id=cid)
                        except TypeError:
                            res = _c.offline_add_urls([url], pid=cid)
                        is_success = True
                    
                    elif hasattr(u115, 'add_offline_task'):
                        try:
                            res = u115.add_offline_task(url, folder_id=cid)
                        except TypeError:
                            try:
                                res = u115.add_offline_task(url, wp_path_id=cid)
                            except TypeError:
                                res = u115.add_offline_task(url, pid=cid)
                        is_success = True
                        
                    elif hasattr(_c, 'offline_add_task'):
                        try:
                            res = _c.offline_add_task([url], wp_path_id=cid)
                        except TypeError:
                            res = _c.offline_add_task([url], pid=cid)
                        is_success = True
                        
                    elif hasattr(_c, 'offline') and hasattr(_c.offline, 'add_url'):
                        res = _c.offline.add_url(url, cid=cid)
                        is_success = True
                        
                    else:
                        raise NotImplementedError("没有任何可用的底层离线下载通道。请在插件设置中填入 [115 独立 Cookie] 启用逃生舱功能。")

                if is_success:
                    logger.info(f"【115离线】添加任务成功 (目标目录ID: {cid})，返回: {res}")
                    success_count += 1
            except Exception as e:
                logger.error(f"【115离线】添加任务失败 {url[:40]}: {e}", exc_info=True)
                fail_count += 1

        self.post_message(
            mtype=NotificationType.SiteMessage,
            title="115离线下载完成",
            text=f"任务添加完毕。\n✅ 成功: {success_count} 个\n❌ 失败: {fail_count} 个\n📁 目标目录: {target_dir}"
        )

        if success_count > 0:
            logger.info("【115离线】有任务添加成功，5秒后自动发起一次目录整理...")
            import threading
            def trigger_clean():
                time.sleep(5)
                self.execute()
            threading.Thread(target=trigger_clean, daemon=True).start()

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
                logger.info("【115整理】监控目录未配置，已退出整理任务。")
                return
            if not self._target_path.strip():
                logger.info("【115整理】目标移动路径未配置，已退出整理任务。")
                return

            storage = StorageChain()
            total_moved = 0
            total_strm = 0

            for monitor_path in paths:
                logger.info(f"【115整理】======================================")
                logger.info(f"【115整理】开始扫描 115 目录: {monitor_path}")
                parent_item = self._get_fileitem(storage, monitor_path)
                
                if not parent_item:
                    logger.warning(f"【115整理】跳过目录 {monitor_path}：底层 API 无法在该层级获取到对应网盘节点。")
                    continue

                children = storage.list_files(parent_item) or []
                subfolders = [c for c in children if c.type == "dir"]
                
                logger.info(f"【115整理】[{monitor_path}] 共发现 {len(subfolders)} 个子文件夹需检查。")

                for folder in subfolders:
                    moved, strm_count = self._process_folder(storage, folder)
                    if moved:
                        total_moved += 1
                        total_strm += strm_count

            if self._notify and total_moved > 0:
                self.post_message(
