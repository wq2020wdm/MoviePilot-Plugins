import os
import re
import time
import requests
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
    plugin_desc = "深度清理正则洗白，移动生成STRM，支持离线下载，联动OpenList强制刷新与mdcx刮削。"
    plugin_icon = "https://raw.githubusercontent.com/wq2020wdm/MoviePilot-Plugins/main/icons/98tang.png"
    plugin_version = "2.7.1"
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
    
    # OpenList/AList 联动配置
    _openlist_url: str = ""
    _openlist_token: str = ""
    _openlist_mount_path: str = ""

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
            self._openlist_url      = config.get("openlist_url", "")
            self._openlist_token    = config.get("openlist_token", "")
            self._openlist_mount_path= config.get("openlist_mount_path", "")

        if self._run_once:
            self._run_once = False
            self.update_config({"run_once": False, **self._current_config()})
            self.execute()

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        pass

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
            monitor_chips = [{"component": "span", "props": {"class": "text-caption text-disabled"}, "text": "未配置"}]

        return [
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [
                    {"component": "VCard", "props": {"variant": "tonal", "class": "mb-3"}, "content": [
                        {"component": "VCardText", "content": [
                            {"component": "div", "props": {"class": "d-flex align-center justify-space-between mb-2"}, "content": [
                                {"component": "span", "props": {"class": "text-subtitle-2"}, "text": "插件状态"},
                                {"component": "VChip", "props": {"color": status_color, "size": "small", "label": True}, "text": status_text},
                            ]},
                            {"component": "div", "props": {"class": "d-flex align-center justify-space-between mb-2"}, "content": [
                                {"component": "span", "props": {"class": "text-caption text-medium-emphasis"}, "text": "Cron 计划"},
                                {"component": "span", "props": {"class": "text-caption font-weight-bold"}, "text": self._cron or "未设置"},
                            ]},
                            {"component": "div", "props": {"class": "d-flex align-center justify-space-between mb-2"}, "content": [
                                {"component": "span", "props": {"class": "text-caption text-medium-emphasis"}, "text": "STRM 生成"},
                                {"component": "VChip", "props": {"color": "success" if self._strm_enabled else "default", "size": "x-small", "label": True}, "text": strm_text},
                            ]},
                        ]}
                    ]}
                ]},
                {"component": "VCol", "props": {"cols": 12, "md": 8}, "content": [
                    {"component": "VCard", "props": {"variant": "tonal", "class": "mb-3"}, "content": [
                        {"component": "VCardText", "content": [
                            {"component": "div", "props": {"class": "mb-3"}, "content": [
                                {"component": "div", "props": {"class": "text-subtitle-2 mb-1"}, "text": "📂 监控目录"},
                                {"component": "div", "props": {"class": "d-flex flex-wrap"}, "content": monitor_chips}
                            ]},
                            {"component": "VDivider", "props": {"class": "my-2"}},
                            {"component": "div", "props": {"class": "mb-3"}, "content": [
                                {"component": "div", "props": {"class": "text-subtitle-2 mb-1"}, "text": "📁 移动目标路径"},
                                {"component": "span", "props": {"class": "text-caption"}, "text": self._target_path or "未配置"}
                            ]},
                            {"component": "VDivider", "props": {"class": "my-2"}},
                            {"component": "div", "props": {"class": "mb-3"}, "content": [
                                {"component": "div", "props": {"class": "text-subtitle-2 mb-1"}, "text": "⬇️ 离线下载默认目录"},
                                {"component": "span", "props": {"class": "text-caption"}, "text": self._cloud_download_dir or "未配置"}
                            ]},
                            {"component": "VDivider", "props": {"class": "my-2"}},
                            {"component": "div", "props": {"class": "mb-3"}, "content": [
                                {"component": "div", "props": {"class": "text-subtitle-2 mb-1"}, "text": "🍪 115 独立 Cookie (逃生舱)"},
                                {"component": "span", "props": {"class": "text-caption"}, "text": "已配置 (强制接管离线功能)" if self._u115_cookie else "未配置 (尝试自动捕获底仓)"}
                            ]},
                        ]}
                    ]}
                ]}
            ]},
            {"component": "VRow", "content": [
                {"component": "VCol", "props": {"cols": 12}, "content": [
                    {"component": "VAlert", "props": {
                        "type": "info", "variant": "tonal", "density": "compact",
                        "text": "💡 使用说明：\n① Bot 发送 /run_115_clean 立即触发整理。\n② Bot 发送 /cd 链接1 链接2 [目录] 进行115离线下载。\n   成功触发 /cd 后，系统将自动发起一次目录整理。"
                    }}
                ]}
            ]}
        ]

    def get_command(self) -> List[Dict[str, Any]]:
        return [
            {"cmd": "/run_115_clean", "event": EventType.PluginAction, "desc": "立即整理 115 目录", "category": "整理", "data": {"action": "run_115_clean"}},
            {"cmd": "/cd", "event": EventType.PluginAction, "desc": "115 离线下载", "category": "下载", "data": {"action": "cd"}}
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

    def _parse_cd_args(self, text: str) -> Tuple[List[str], str]:
        text = re.sub(r'(?<!^)(https?://|ftp://|magnet:\?|ed2k://)', r' \1', str(text), flags=re.IGNORECASE)
        parts = text.split()
        urls, dir_parts = [], []
        for part in parts:
            if re.match(r'^(https?://|ftp://|magnet:\?|ed2k://)', part, re.IGNORECASE):
                urls.append(part)
            else:
                dir_parts.append(part)
        return urls, " ".join(dir_parts).strip()

    def _handle_cd_task(self, text: str):
        urls, target_dir = self._parse_cd_args(text)
        if not target_dir:
            target_dir = self._cloud_download_dir

        if not urls:
            self.post_message(mtype=NotificationType.SiteMessage, title="115离线下载",
                              text=f"未识别到支持的链接。(支持 http/ftp/magnet/ed2k 开头)\n提取到的内容为: {text[:50]}")
            return
        if not target_dir:
            self.post_message(mtype=NotificationType.SiteMessage, title="115离线下载",
                              text="指令未指定目录，且未配置默认云下载目录，无法执行离线下载。")
            return

        logger.info(f"【115离线】共发现 {len(urls)} 个链接，目标路径: {target_dir}")
        storage = StorageChain()
        folder_item = self._get_fileitem(storage, target_dir)
        if not folder_item:
            self.post_message(mtype=NotificationType.SiteMessage, title="115离线下载",
                              text=f"目标目录获取失败或在115中不存在: {target_dir}")
            return

        cid_str = None
        for attr in ['fileid', 'id', 'item_id', 'fid', 'wp_path_id']:
            val = getattr(folder_item, attr, None)
            if val is not None and str(val).strip() not in ("", "0"):
                cid_str = str(val).strip()
                break
        
        if not cid_str and hasattr(folder_item, 'extra') and isinstance(folder_item.extra, dict):
            for attr in ['fileid', 'id', 'item_id', 'fid']:
                val = folder_item.extra.get(attr)
                if val is not None and str(val).strip() not in ("", "0"):
                    cid_str = str(val).strip()
                    break
        
        cid_int = int(cid_str) if cid_str and cid_str.isdigit() else 0
        if cid_int == 0:
            logger.warning(f"【115离线】警告：未能提取到 {target_dir} 的真实目录ID，将退化使用根目录(0)。")
        else:
            logger.info(f"【115离线】精准捕获真实目标目录ID: {cid_int}")

        u115 = self._get_u115()
        cookie = self._u115_cookie.strip()
        
        if not cookie and u115 and hasattr(u115, 'get_config'):
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
            except Exception as e:
                logger.error(f"【115离线】P115Client 初始化报错: {e}")

        success_count = 0
        fail_count = 0

        for url in urls:
            try:
                is_success = False
                res = None

                payload = {
                    "url[0]": url.strip(),
                    "wp_path_id": cid_int
                }

                if p115_client:
                    try:
                        res = p115_client.offline_add_urls(payload)
                        if self._is_offline_success(res):
                            is_success = True
                    except Exception as e:
                        logger.debug(f"P115Client Payload 调用失败: {e}")
                
                if not is_success and u115:
                    if hasattr(u115, 'client') and hasattr(u115.client, 'offline_add_urls'):
                        try:
                            res = u115.client.offline_add_urls(payload)
                            if self._is_offline_success(res):
                                is_success = True
                        except Exception as e:
                            logger.debug(f"u115.client Payload 调用失败: {e}")

                if not is_success and hasattr(u115, '_request_api'):
                    try:
                        res = u115._request_api(
                            url="https://proapi.115.com/app/lixian/add_task_url",
                            method="POST",
                            data=payload
                        )
                        if self._is_offline_success(res):
                            is_success = True
                    except Exception as e:
                        logger.debug(f"_request_api 盲打失败: {e}")

                if not is_success:
                    raise NotImplementedError("底层离线接口调用全部失效，请确认填入了正确的 115 独立 Cookie。")

                logger.info(f"【115离线】添加任务成功 (最终目录ID: {cid_int})，返回结果: {res}")
                success_count += 1

            except Exception as e:
                logger.error(f"【115离线】添加任务失败 {url[:60]}: {e}", exc_info=True)
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

    @staticmethod
    def _is_offline_success(res) -> bool:
        if res is True:
            return True
        if isinstance(res, dict):
            if res.get("state"):
                return True
            data = res.get("data")
            if isinstance(data, dict) and data.get("state"):
                return True
        return False

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

    @staticmethod
    def _extract_bango(filename: str) -> str:
        has_c = bool(re.search(r'[-_]C\b', filename, re.IGNORECASE))
        has_uc = bool(re.search(r'[-_]UC\b|破解版|无码', filename, re.IGNORECASE))

        clean_name = re.sub(r'\[.*?\]|.*?@|@.*?(\s|-|_)', '', filename)
        clean_name = re.sub(r'(-C|_C|-FHD|-HD|-1080p|_1080p|_4K|-uncensored|[-_]UC|破解版|无码).*', '', clean_name, flags=re.IGNORECASE)
        clean_name = re.sub(r'[_ ]', '-', clean_name)

        bango = ""
        patterns = [
            r'([a-zA-Z]{2,6}-\d{2,5})',
            r'(\d{1,2}[a-zA-Z]{2,5}-\d{2,5})',
            r'(FC2-PPV-\d{5,8}|FC2-\d{5,8})',
            r'([a-zA-Z]{2,6})(\d{2,5})',
        ]

        for pattern in patterns:
            match = re.search(pattern, clean_name, re.IGNORECASE)
            if match:
                bango = match.group(1).upper()
                if '-' not in bango and re.match(r'^[A-Z]+\d+$', bango):
                    letter_part = re.search(r'^[A-Z]+', bango).group()
                    num_part = re.search(r'\d+$', bango).group()
                    bango = f"{letter_part}-{num_part}"
                break
                
        if not bango:
            bango = Path(clean_name).stem.upper()

        if has_c:
            bango += "-C"
        if has_uc:
            bango += "-UC"

        return bango

    def _deep_clean_folder(self, storage: StorageChain, folder: FileItem, threshold_bytes: int) -> bool:
        """递归清理文件夹内的垃圾文件和空子文件夹"""
        files_and_dirs = storage.list_files(folder) or []
        has_valid_content = False
        
        for item in files_and_dirs:
            if item.type == "dir":
                is_subfolder_valid = self._deep_clean_folder(storage, item, threshold_bytes)
                if is_subfolder_valid:
                    has_valid_content = True
                else:
                    try:
                        storage.delete_file(item)
                        logger.debug(f"【115整理】已删除空壳子文件夹: {item.name}")
                    except Exception as e:
                        logger.warning(f"【115整理】删除空壳子文件夹 {item.name} 失败: {e}")
                        has_valid_content = True 
                        
            elif item.type == "file":
                if (item.size or 0) < threshold_bytes:
                    try:
                        storage.delete_file(item)
                        logger.debug(f"【115整理】已深层删除垃圾文件: {folder.name}/{item.name}")
                    except Exception as e:
                        logger.warning(f"【115整理】深层删除垃圾文件 {item.name} 失败: {e}")
                        has_valid_content = True
                else:
                    has_valid_content = True

        return has_valid_content

    # =========================================================================
    # 🔥 核心增强：强制要求 OpenList / AList 去 115 拉取最新文件数据
    # =========================================================================
    def _refresh_openlist_cache(self, cloud_target_path: str):
        if not self._openlist_url.strip() or not self._openlist_token.strip() or not self._openlist_mount_path.strip():
            return
        
        openlist_url = self._openlist_url.rstrip('/')
        mount_name = self._openlist_mount_path.rstrip('/')
        
        # 组合绝对路径，例如: /115网盘/影视/电影
        full_openlist_path = f"{mount_name}/{cloud_target_path.strip('/')}"
        
        # 改为使用 list 接口，配合 refresh: True 强制穿透缓存同步上游数据
        api_endpoint = f"{openlist_url}/api/fs/list"
        
        headers = {
            "Authorization": self._openlist_token.strip(),
            "Content-Type": "application/json"
        }
        payload = {
            "path": full_openlist_path,
            "password": "",
            "page": 1,
            "per_page": 0,
            "refresh": True  # 核心参数：告诉 AList 去 115 重新拉取
        }
        
        try:
            # 增加超时时间以等待 AList 向上游拉取完毕
            response = requests.post(api_endpoint, headers=headers, json=payload, timeout=15)
            if response.status_code == 200:
                logger.info(f"【OpenList联动】成功强制拉取最新数据: {full_openlist_path}")
            else:
                logger.warning(f"【OpenList联动】强制拉取最新数据失败: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"【OpenList联动】请求 API 发生异常: {e}")

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
                logger.info(f"【115整理】[{monitor_path}] 共发现 {len(subfolders)} 个番号文件夹需检查。")

                for folder in subfolders:
                    moved, strm_count = self._process_folder(storage, folder)
                    if moved:
                        total_moved += 1
                        total_strm += strm_count

            if self._notify and total_moved > 0:
                self.post_message(mtype=NotificationType.SiteMessage, title="115目录洗白整理完成",
                                  text=f"本次共整理并移动了 {total_moved} 个文件夹，生成 STRM {total_strm} 个。")
            
            # 🔥 确实发生了文件移动，触发强制同步
            if total_moved > 0:
                self._refresh_openlist_cache(self._target_path)
            
            if total_strm > 0 and self._mdcx_container.strip():
                self._restart_mdcx(self._mdcx_container.strip())
        finally:
            self._lock.release()

    def _process_folder(self, storage: StorageChain, folder: FileItem) -> Tuple[bool, int]:
        fname = folder.name
        threshold_bytes = self._size_threshold_mb * 1024 * 1024
        logger.info(f"【115整理】>>> 开始处理番号文件夹: {fname}")

        has_valid_content = self._deep_clean_folder(storage, folder, threshold_bytes)
        
        if not has_valid_content:
            logger.info(f"【115整理】>>> 文件夹 [{fname}] 清理后为空，跳过后续移动。")
            return False, 0

        files_after = storage.list_files(folder) or []
        remaining_files = [f for f in files_after if f.type == "file" and (f.size or 0) >= threshold_bytes]
        
        if not remaining_files:
            logger.info(f"【115整理】>>> 文件夹 [{fname}] 主目录下无有效大视频，跳过移动。")
            return False, 0

        strm_targets: List[Tuple[str, str]] = []
        need_pickcode = "{pick_code}" in self._strm_template
        
        folder_needs_rename = False
        new_folder_name = fname
        
        for bf in remaining_files:
            pickcode = (getattr(bf, "pickcode", None) or "") if need_pickcode else ""
            
            pure_bango = self._extract_bango(bf.name)
            ext = Path(bf.name).suffix
            new_file_name = f"{pure_bango}{ext}"
            
            if bf.name != new_file_name:
                try:
                    ok = storage.rename_file(bf, new_file_name)
                    strm_targets.append((new_file_name if ok else bf.name, pickcode))
                    logger.info(f"【115整理】洗白重命名成功: {bf.name} -> {new_file_name}")
                    
                    folder_needs_rename = True
                    new_folder_name = pure_bango
                except Exception as e:
                    logger.warning(f"【115整理】重命名文件失败 {bf.name}: {e}")
                    strm_targets.append((bf.name, pickcode))
            else:
                strm_targets.append((bf.name, pickcode))

        if folder_needs_rename and fname != new_folder_name:
            try:
                storage.rename_file(folder, new_folder_name)
                logger.info(f"【115整理】父文件夹同步重命名成功: {fname} -> {new_folder_name}")
                folder.name = new_folder_name
            except Exception as e:
                logger.warning(f"【115整理】重命名父文件夹失败 {fname}: {e}")

        target_path = self._target_path.rstrip("/")
        logger.info(f"【115整理】开始移动整个文件夹 [{folder.name}] 到 -> {target_path}")
        ok = self._do_move(folder, target_path)
        if not ok:
            logger.error(f"【115整理】文件夹 [{folder.name}] 移动失败。")
            return False, 0
        logger.info(f"【115整理】文件夹 [{folder.name}] 移动成功！")

        strm_count = 0
        if self._strm_enabled and self._strm_local_path.strip():
            for file_name, pickcode in strm_targets:
                if self._generate_strm(folder.name, file_name, pickcode, target_path):
                    strm_count += 1
        return True, strm_count

    def _generate_strm(self, folder_name: str, file_name: str, pickcode: str, cloud_target_path: str) -> bool:
        try:
            local_dir = Path(self._strm_local_path.rstrip("/")) / folder_name
            local_dir.mkdir(parents=True, exist_ok=True)
            stem = Path(file_name).stem
            strm_file = local_dir / f"{stem}.strm"
            cloud_file = f"{cloud_target_path}/{folder_name}/{file_name}"
            content = self._strm_template.replace("{cloud_file}", cloud_file).replace("{pick_code}", pickcode)
            strm_file.write_text(content, encoding="utf-8")
            return True
        except Exception:
            return False

    def _restart_mdcx(self, container_name: str):
        import socket
        sock_path = "/var/run/docker.sock"
        if not os.path.exists(sock_path): return
        try:
            request = (f"POST /containers/{container_name}/restart HTTP/1.1\r\n"
                       f"Host: localhost\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect(sock_path)
            sock.sendall(request.encode("utf-8"))
            sock.close()
        except Exception:
            pass

    def _do_move(self, src: FileItem, target_path: str) -> bool:
        u115 = self._get_u115()
        if u115 is None: return False
        try:
            return bool(u115.move(src, Path(target_path), src.name))
        except Exception:
            return False

    def _get_fileitem(self, storage: StorageChain, path: str) -> Optional[FileItem]:
        try:
            parts = [p for p in path.strip("/").split("/") if p]
            if not parts: return None
            logger.info(f"【115整理】开始逐层解析云端路径: /{'/'.join(parts)}")
            root_item = FileItem(storage="u115", fileid="0", path="/", type="dir", name="")
            current_items = storage.list_files(root_item) or []
            if not current_items:
                logger.error("【115整理】解析失败：无法获取115根目录数据。")
                return None
            current_item = None
            for i, part in enumerate(parts):
                matched = next((item for item in current_items if item.name == part), None)
                if not matched:
                    avail_dirs = [item.name for item in current_items if item.type == 'dir'][:10]
                    logger.warning(f"【115整理】路径断裂：在当前层级找不到名为 '{part}' 的文件夹。当前可用文件夹包含: {avail_dirs}...")
                    return None
                current_item = matched
                if i < len(parts) - 1:
                    current_items = storage.list_files(current_item) or []
            logger.info(f"【115整理】路径解析成功！目标文件夹 [{current_item.name}]")
            return current_item
        except Exception as e:
            logger.error(f"【115整理】路径解析异常: {e}", exc_info=True)
            return None

    @staticmethod
    def _fmt(size_bytes: Optional[int]) -> str:
        if not size_bytes: return "0B"
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024: return f"{size_bytes:.1f}{unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f}TB"

    def _current_config(self) -> dict:
        return {
            "enabled": self._enabled, "cron": self._cron,
            "monitor_paths": self._monitor_paths, "target_path": self._target_path,
            "cloud_download_dir": self._cloud_download_dir, "u115_cookie": self._u115_cookie,
            "size_threshold_mb": self._size_threshold_mb, "notify": self._notify,
            "strm_enabled": self._strm_enabled, "strm_local_path": self._strm_local_path,
            "strm_template": self._strm_template, "mdcx_container": self._mdcx_container,
            "openlist_url": self._openlist_url, "openlist_token": self._openlist_token,
            "openlist_mount_path": self._openlist_mount_path,
        }

    def get_api(self) -> List[dict]: return []

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._cron:
            from apscheduler.triggers.cron import CronTrigger
            return [{"id": "movie115_organizer_task", "name": "115整理服务",
                     "trigger": CronTrigger.from_crontab(self._cron), "func": self.execute}]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {"component": "VForm", "content": [
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "notify", "label": "发送通知"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "strm_enabled", "label": "生成STRM"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 3}, "content": [{"component": "VSwitch", "props": {"model": "run_once", "label": "保存后运行一次"}}]},
                ]},
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "cron", "label": "Cron 表达式", "hint": "建议间隔≥30分钟，如 0 */2 * * *"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 6}, "content": [{"component": "VTextField", "props": {"model": "size_threshold_mb", "label": "垃圾文件阈值 (MB)", "type": "number"}}]},
                ]},
                {"component": "VRow", "content": [{"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextarea", "props": {"model": "monitor_paths", "label": "监控目录（每行一个115路径）", "placeholder": "格式如: /接收/temp/小姐姐", "rows": 3}}]}]},
                {"component": "VRow", "content": [{"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextField", "props": {"model": "target_path", "label": "115 移动目标路径", "placeholder": "格式如: /影视/电影"}}]}]},
                {"component": "VRow", "content": [{"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextField", "props": {"model": "cloud_download_dir", "label": "115云下载目录 (离线下载默认路径)", "placeholder": "格式如: /云下载"}}]}]},
                {"component": "VRow", "content": [{"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextField", "props": {"model": "u115_cookie", "label": "115 独立 Cookie (离线逃生舱)", "hint": "若你的系统架构阻断了自动提取，请在此手动填入115网页端 Cookie以强行接管离线功能"}}]}]},
                {"component": "VRow", "content": [{"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextField", "props": {"model": "strm_local_path", "label": "STRM 本地根目录（需开启生成STRM）", "placeholder": "/media/strm/电影"}}]}]},
                {"component": "VRow", "content": [{"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextField", "props": {"model": "strm_template", "label": "STRM 内容模板", "hint": "{cloud_file}=云端完整路径  {pick_code}=115 pickcode"}}]}]},
                
                # AList/OpenList 联动配置
                {"component": "VRow", "content": [
                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VTextField", "props": {"model": "openlist_url", "label": "OpenList 地址 (选填)", "placeholder": "http://ip:5244", "hint": "整理后自动刷新缓存"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VTextField", "props": {"model": "openlist_token", "label": "OpenList Token (选填)"}}]},
                    {"component": "VCol", "props": {"cols": 12, "md": 4}, "content": [{"component": "VTextField", "props": {"model": "openlist_mount_path", "label": "115挂载节点名 (选填)", "placeholder": "/115网盘"}}]},
                ]},
                
                {"component": "VRow", "content": [{"component": "VCol", "props": {"cols": 12}, "content": [{"component": "VTextField", "props": {"model": "mdcx_container", "label": "mdcx 容器名", "placeholder": "mdcx", "hint": "生成STRM后将自动重启该容器触发刮削（留空则不重启）"}}]}]},
            ]}
        ], {
            "enabled": False, "cron": "0 */2 * * *", "monitor_paths": "", "target_path": "",
            "cloud_download_dir": "", "u115_cookie": "", "size_threshold_mb": 500,
            "notify": True, "run_once": False, "strm_enabled": False, "strm_local_path": "",
            "strm_template": "http://10.0.0.5:7811/redirect?path={cloud_file}&pickcode={pick_code}",
            "mdcx_container": "", "openlist_url": "", "openlist_token": "", "openlist_mount_path": "",
        }
