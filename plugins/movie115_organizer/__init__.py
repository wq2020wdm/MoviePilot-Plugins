import time
from typing import List, Dict, Tuple, Any
from app.plugins import _PluginBase
from app.core.config import settings
from app.log import logger
from app.schemas import NotificationType

# 自动适配 115 助手位置
try:
    from app.helper.p115 import P115Helper
except ImportError:
    from app.modules.p115 import P115Helper

class Movie115Organizer(_PluginBase):
    # --- V2 必须定义的类属性 ---
    plugin_id = "movie115_organizer"
    plugin_name = "115 目录洗白整理"
    plugin_desc = "监控115路径，自动清理小文件、按@符号重命名并移动归档。"
    plugin_icon = "folder.png"
    plugin_version = "1.1.0"
    plugin_author = "YourName"
    plugin_order = 20
    auth_level = 1

    # 配置变量初始化
    _enabled = False
    _cron = None
    _monitor_path = None
    _target_path = None
    _threshold = 500
    _notify = True

    def init_plugin(self, config: dict = None):
        """保存设置后，MP V2 会自动调用此方法热重载配置"""
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._monitor_path = config.get("monitor_path")
            self._target_path = config.get("target_path")
            # 兼容字符串或数字
            try:
                self._threshold = float(config.get("threshold") or 500)
            except ValueError:
                self._threshold = 500
            self._notify = config.get("notify")

    def get_id_by_path(self, p115, path: str):
        """115 路径转 ID"""
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
        """手动触发或定时触发的主逻辑"""
        if not self._enabled:
            logger.info("【115整理】插件未启用，跳过运行")
            return

        p115 = P115Helper()
        m_id = self.get_id_by_path(p115, self._monitor_path)
        t_id = self.get_id_by_path(p115, self._target_path)

        if not m_id or not t_id:
            logger.error(f"【115整理】找不到路径: 监控({self._monitor_path}) 目标({self._target_path})")
            return

        try:
            items = p115.get_file_list(m_id)
            for item in items:
                if not item.get('is_dir'): continue
                
                folder_id = item.get('id')
                folder_name = item.get('name')
                
                # 1. 清理小文件
                sub_files = p115.get_file_list(folder_id)
                for sf in sub_files:
                    if not sf.get('is_dir'):
                        size_mb = sf.get('size', 0) / (1024 * 1024)
                        if size_mb < self._threshold:
                            p115.delete_file(sf.get('id'))
                            logger.info(f"【115整理】已删除垃圾文件: {sf.get('name')} ({size_mb:.2f}MB)")

                # 2. 重命名
                new_name = folder_name
                if '@' in folder_name:
                    new_name = folder_name.split('@')[-1]
                    if new_name != folder_name:
                        p115.rename_file(folder_id, new_name)
                        logger.info(f"【115整理】已重命名: {folder_name} -> {new_name}")

                # 3. 移动
                p115.move_file(folder_id, t_id)
                logger.info(f"【115整理】已归档至目标目录: {new_name}")

                if self._notify:
                    self.post_message(
                        mtype=NotificationType.SiteMessage,
                        title="115 整理任务完成",
                        text=f"文件夹 {new_name} 已洗白并归档。"
                    )
        except Exception as e:
            logger.error(f"【115整理】运行报错: {str(e)}")

    def get_service(self) -> List[Dict[str, Any]]:
        """注册 V2 定时任务"""
        if self._enabled and self._cron:
            from apscheduler.triggers.cron import CronTrigger
            return [{
                "id": "Movie115Organizer",
                "name": "115 整理服务",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.execute
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """V2 专用表单定义"""
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                                {'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件'}}
                            ]},
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                                {'component': 'VSwitch', 'props': {'model': 'notify', 'label': '启用通知'}}
                            ]}
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                                {'component': 'VTextField', 'props': {'model': 'monitor_path', 'label': '监控路径 (如 /Movies/Temp)'}}
                            ]},
                            {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                                {'component': 'VTextField', 'props': {'model': 'target_path', 'label': '归档路径 (如 /Movies/Library)'}}
                            ]}
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                                {'component': 'VCronField', 'props': {'model': 'cron', 'label': '定时任务周期'}}
                            ]},
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                                {'component': 'VTextField', 'props': {'model': 'threshold', 'label': '清理阈值 (MB)'}}
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
        """Bot 指令"""
        return [{
            "command": "run_115_clean",
            "data": "run_115_clean",
            "description": "手动触发 115 整理",
            "handler": self.execute
        }]

    def stop_service(self):
        pass
