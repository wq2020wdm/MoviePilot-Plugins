import time
from typing import List, Dict, Tuple, Any
from app.plugins import _PluginBase
from app.log import logger
from app.schemas import NotificationType

# 修复核心：更全面的 115 模块路径探测
try:
    from app.helper.p115 import P115Helper
except ImportError:
    try:
        from app.modules.p115 import P115Helper
    except ImportError:
        # 如果还是找不到，定义一个空类防止插件彻底崩溃导致无法进入设置界面
        P115Helper = None
        logger.error("【115整理】未能在系统中找到 P115Helper 模块，请确认 MP 版本是否支持 115")

class Movie115Organizer(_PluginBase):
    # V2 必须定义的类属性
    plugin_id = "movie115_organizer"
    plugin_name = "115 目录洗白整理"
    plugin_desc = "监控115路径，自动清理小文件、按@符号重命名并移动归档。"
    plugin_icon = "folder.png"
    plugin_version = "1.2.1"
    plugin_author = "YourName"
    plugin_order = 10
    auth_level = 1

    _enabled = False
    _cron = None
    _monitor_path = None
    _target_path = None
    _threshold = 500
    _notify = True

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._monitor_path = config.get("monitor_path")
            self._target_path = config.get("target_path")
            try:
                self._threshold = float(config.get("threshold") or 500)
            except (ValueError, TypeError):
                self._threshold = 500
            self._notify = config.get("notify")

    def get_id_by_path(self, p115, path: str):
        """路径转 ID"""
        if not path: return None
        # 如果填的是纯数字 ID 则直接返回
        if path.isdigit(): return path
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
        if not self._enabled:
            return

        if P115Helper is None:
            logger.error("【115整理】核心助手模块缺失，无法运行")
            return

        p115 = P115Helper()
        m_id = self.get_id_by_path(p115, self._monitor_path)
        t_id = self.get_id_by_path(p115, self._target_path)

        if not m_id or not t_id:
            logger.error(f"【115整理】配置路径无效，请检查监控路径和目标路径")
            return

        try:
            items = p115.get_file_list(m_id)
            for item in items:
                if not item.get('is_dir'): continue
                
                fid, fname = item.get('id'), item.get('name')
                
                # 1. 清理
                sub_files = p115.get_file_list(fid)
                for sf in sub_files:
                    if not sf.get('is_dir'):
                        if (sf.get('size', 0) / (1024 * 1024)) < self._threshold:
                            p115.delete_file(sf.get('id'))

                # 2. 重命名与移动
                new_name = fname.split('@')[-1] if '@' in fname else fname
                if new_name != fname:
                    p115.rename_file(fid, new_name)
                
                p115.move_file(fid, t_id)
                logger.info(f"【115整理】处理成功: {new_name}")

                if self._notify:
                    self.post_message(
                        mtype=NotificationType.SiteMessage,
                        title="115 整理完成",
                        text=f"已归档: {new_name}"
                    )
        except Exception as e:
            logger.error(f"【115整理】运行报错: {str(e)}")

    def get_service(self) -> List[Dict[str, Any]]:
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
                                {'component': 'VSwitch', 'props': {'model': 'notify', 'label': '发送通知'}}
                            ]}
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                                {'component': 'VTextField', 'props': {'model': 'monitor_path', 'label': '监控路径 (如 /Temp)'}}
                            ]},
                            {'component': 'VCol', 'props': {'cols': 12}, 'content': [
                                {'component': 'VTextField', 'props': {'model': 'target_path', 'label': '归档路径 (如 /Media)'}}
                            ]}
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                                {'component': 'VCronField', 'props': {'model': 'cron', 'label': '周期'}}
                            ]},
                            {'component': 'VCol', 'props': {'cols': 12, 'md': 6}, 'content': [
                                {'component': 'VTextField', 'props': {'model': 'threshold', 'label': '清理阈值 (MB)'}}
                            ]}
                        ]
                    }
                ]
            }
        ], {"enabled": False, "notify": True, "threshold": 500, "cron": "*/30 * * * *"}

    def get_command(self) -> List[Dict[str, Any]]:
        return [{
            "command": "run_115_clean",
            "data": "run_115_clean",
            "description": "立即整理 115",
            "handler": self.execute
        }]

    def stop_service(self):
        pass
