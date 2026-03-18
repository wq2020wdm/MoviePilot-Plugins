import time
from typing import List, Dict, Tuple, Any, Optional
from app.plugins import _PluginBase
from app.log import logger
from app.schemas import NotificationType
# 注意：V2 的 115 助手路径通常在 app.helper.p115 或 app.modules.p115
try:
    from app.helper.p115 import P115Helper
except ImportError:
    from app.modules.p115 import P115Helper

class Movie115Organizer(_PluginBase):
    # --- 插件元数据 ---
    plugin_name = "115 目录洗白整理"
    plugin_desc = "监控115路径，自动清理小文件并重命名移动。"
    plugin_icon = "folder.png" # 确保这个图标在 MP 中存在或使用通用图标
    plugin_version = "1.0.0"
    plugin_author = "YourName"
    plugin_order = 20
    auth_level = 1

    # 私有属性存储配置
    _enabled = False
    _cron = None
    _monitor_path = None
    _target_path = None
    _threshold = 500
    _notify = True

    def init_plugin(self, config: dict = None):
        """初始化配置"""
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._monitor_path = config.get("monitor_path")
            self._target_path = config.get("target_path")
            self._threshold = float(config.get("threshold") or 500)
            self._notify = config.get("notify")

    def get_id_by_path(self, p115, path: str):
        """路径转 ID 逻辑"""
        if not path: return None
        if not path.startswith('/'): return path
        
        parts = [p for p in path.split('/') if p]
        current_id = '0'
        for part in parts:
            found = False
            items = p115.get_file_list(current_id)
            if not items: break
            for item in items:
                if item.get('is_dir') and item.get('name') == part:
                    current_id = item.get('id')
                    found = True
                    break
            if not found: return None
        return current_id

    def execute(self):
        """主执行逻辑"""
        if not self._enabled:
            return

        p115 = P115Helper()
        m_id = self.get_id_by_path(p115, self._monitor_path)
        t_id = self.get_id_by_path(p115, self._target_path)

        if not m_id or not t_id:
            logger.error(f"【115整理】路径转换失败，请检查配置")
            return

        try:
            items = p115.get_file_list(m_id)
            for item in items:
                if not item.get('is_dir'): continue
                
                folder_id = item.get('id')
                folder_name = item.get('name')
                
                # 清理小文件
                sub_files = p115.get_file_list(folder_id)
                for sf in sub_files:
                    if not sf.get('is_dir'):
                        size_mb = sf.get('size', 0) / (1024 * 1024)
                        if size_mb < self._threshold:
                            p115.delete_file(sf.get('id'))
                            logger.info(f"【115整理】已删除广告文件: {sf.get('name')}")

                # 重命名
                new_name = folder_name
                if '@' in folder_name:
                    new_name = folder_name.split('@')[-1]
                    if new_name != folder_name:
                        p115.rename_file(folder_id, new_name)

                # 移动
                p115.move_file(folder_id, t_id)
                logger.info(f"【115整理】已成功移动: {new_name}")

                if self._notify:
                    self.post_message(
                        mtype=NotificationType.SiteMessage,
                        title="115 目录整理完成",
                        text=f"处理并归档: {new_name}"
                    )
        except Exception as e:
            logger.error(f"【115整理】运行异常: {str(e)}")

    def get_service(self) -> List[Dict[str, Any]]:
        """注册定时服务 (Cron)"""
        if self._enabled and self._cron:
            from apscheduler.triggers.cron import CronTrigger
            return [{
                "id": "Movie115Organizer",
                "name": "115 整理定时服务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.execute,
                "kwargs": {}
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """构建 V2 版本的表单界面"""
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                                {'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件'}}
                            ]},
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 4}, 'content': [
                                {'component': 'VSwitch', 'props': {'model': 'notify', 'label': '开启通知'}}
                            ]}
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                                {'component': 'VTextField', 'props': {'model': 'monitor_path', 'label': '监控路径或ID'}}
                            ]},
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                                {'component': 'VTextField', 'props': {'model': 'target_path', 'label': '目标路径或ID'}}
                            ]}
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                                {'component': 'VCronField', 'props': {'model': 'cron', 'label': '运行周期'}}
                            ]},
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                                {'component': 'VTextField', 'props': {'model': 'threshold', 'label': '体积阈值 (MB)'}}
                            ]}
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "notify": True,
            "threshold": 500,
            "cron": "*/30 * * * *"
        }

    def get_command(self) -> List[Dict[str, Any]]:
        """注册 Bot 命令"""
        return [{
            "command": "run_115_clean",
            "data": "run_115_clean",
            "description": "立即运行 115 洗白整理",
            "handler": self.execute
        }]

    def stop_service(self):
        pass
