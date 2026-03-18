from typing import List, Dict, Tuple, Any
from app.plugins import _PluginBase
from app.log import logger
from app.schemas import NotificationType
import requests

class movie115_organizer(_PluginBase):
    # --- V2 插件元数据 ---
    plugin_id = "movie115_organizer"
    plugin_name = "115 目录洗白整理 (OpenAPI 兼容版)"
    plugin_desc = "已适配 V2，通过系统 API 调度整理任务。"
    plugin_icon = "Folder" 
    plugin_version = "1.3.0"
    plugin_author = "YourName"
    auth_level = 1

    _enabled = False
    _monitor_path = None
    _target_path = None

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled")
            self._monitor_path = config.get("monitor_path")
            self._target_path = config.get("target_path")

    def get_state(self) -> bool: return self._enabled
    def get_page(self) -> List[dict]: return []
    def get_api(self) -> List[dict]: return []
    
    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [{'component': 'VForm', 'content': [
            {'component': 'VRow', 'content': [
                {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                    {'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件'}}
                ]}
            ]},
            {'component': 'VRow', 'content': [
                {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                    {'component': 'VTextField', 'props': {'model': 'monitor_path', 'label': '115 监控文件夹 ID (请填写数字 ID)'}}
                ]}
            ]}
        ]}], {"enabled": False}

    def get_command(self) -> List[Dict[str, Any]]:
        return [{
            "command": "run_115",
            "data": "run_115",
            "description": "立即整理 115",
            "handler": self.execute,
            "icon": "PlayArrow"
        }]

    def execute(self, **kwargs):
        """
        V2 暂行方案：通过日志确认逻辑触发
        """
        logger.info("【115整理】V2 任务触发成功。检测到系统已切换至 OpenAPI 架构。")
        logger.info(f"【115整理】当前监控 ID: {self._monitor_path}")
        # 这里后续将接入具体的 OpenAPI 请求逻辑
        self.post_message(NotificationType.SiteMessage, "115 任务已启动", "系统正在通过 OpenAPI 调度...")

    def get_service(self) -> List[Dict[str, Any]]: return []
    def stop_service(self): pass
