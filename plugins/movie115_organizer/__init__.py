import time
from app.plugins import _PluginBase
from app.modules.p115 import P115Helper
from app.core.config import settings

class Movie115Organizer(_PluginBase):
    # --- 插件元数据 (替代 config.json) ---
    plugin_id = "movie115_organizer"
    plugin_name = "115 目录洗白整理"
    plugin_desc = "监控115路径，自动清理小文件，按@符号重命名并移动归档。"
    plugin_icon = "folder.png"
    plugin_order = 15
    auth_group = "admin"

    def get_fields(self):
        """定义 MoviePilot 界面上的输入框"""
        return [
            {
                'component': 'VSwitch',
                'prop': {
                    'label': '启用插件',
                },
                'name': 'enabled'
            },
            {
                'component': 'VTextField',
                'prop': {
                    'label': '定时任务 (Cron)',
                    'placeholder': '例如: */30 * * * * (每30分钟运行一次)'
                },
                'name': 'cron'
            },
            {
                'component': 'VTextField',
                'prop': {
                    'label': '监控路径或ID',
                    'placeholder': '例如: /我的网盘/下载 或 115数字ID'
                },
                'name': 'monitor_path'
            },
            {
                'component': 'VTextField',
                'prop': {
                    'label': '目标保存路径或ID',
                    'placeholder': '例如: /我的网盘/电影 或 115数字ID'
                },
                'name': 'target_path'
            },
            {
                'component': 'VTextField',
                'prop': {
                    'label': '体积阈值 (MB)',
                    'placeholder': '小于此大小的文件将被删除'
                },
                'name': 'threshold',
                'default': '500'
            },
            {
                'component': 'VSwitch',
                'prop': {
                    'label': '启用通知',
                },
                'name': 'notify'
            }
        ]

    def get_id_by_path(self, p115, path: str):
        """路径转 ID 逻辑：递归查找路径对应的 115 ID"""
        if not path: return None
        if not path.startswith('/'): return path # 已经是 ID 格式
        
        parts = [p for p in path.split('/') if p]
        current_id = '0' # 115 根目录
        for part in parts:
            found = False
            items = p115.get_file_list(current_id)
            for item in items:
                if item.get('is_dir') and item.get('name') == part:
                    current_id = item.get('id')
                    found = True
                    break
            if not found:
                self.log_error(f"115 路径未找到: {part}")
                return None
        return current_id

    def execute(self):
        """插件主逻辑"""
        config = self.get_config()
        if not config.get('enabled'):
            self.log_info("插件未启用")
            return

        m_path = config.get('monitor_path')
        t_path = config.get('target_path')
        threshold = float(config.get('threshold') or 500)

        p115 = P115Helper()
        
        # 1. 路径预解析
        m_id = self.get_id_by_path(p115, m_path)
        t_id = self.get_id_by_path(p115, t_path)

        if not m_id or not t_id:
            self.log_error("无法获取有效的监控或目标目录 ID")
            return

        self.log_info(f"开始执行 115 扫描任务，监控 ID: {m_id}")

        try:
            # 2. 获取列表
            items = p115.get_file_list(m_id)
            for item in items:
                if not item.get('is_dir'): continue # 只处理目录
                
                folder_id = item.get('id')
                folder_name = item.get('name')
                
                # 3. 清理小文件
                sub_files = p115.get_file_list(folder_id)
                for sf in sub_files:
                    if not sf.get('is_dir'):
                        size_mb = sf.get('size', 0) / (1024 * 1024)
                        if size_mb < threshold:
                            p115.delete_file(sf.get('id'))
                            self.log_info(f"[{folder_name}] 已删除广告/小文件: {sf.get('name')}")

                # 4. 重命名逻辑 (删除 @ 符号及以前的部分)
                new_name = folder_name
                if '@' in folder_name:
                    new_name = folder_name.split('@')[-1]
                    if new_name != folder_name:
                        p115.rename_file(folder_id, new_name)
                        self.log_info(f"文件夹重命名: {folder_name} -> {new_name}")

                # 5. 移动
                p115.move_file(folder_id, t_id)
                self.log_info(f"任务归档成功: {new_name}")

                # 6. 通知
                if config.get('notify'):
                    self.post_message(title="115 自动整理报告", text=f"已成功洗白并移动文件夹: {new_name}")

        except Exception as e:
            self.log_error(f"执行过程中发生异常: {str(e)}")

    def get_command(self):
        """注册 Telegram Bot 命令"""
        return [
            {
                "command": "run_115_clean",
                "data": "run_115_clean",
                "description": "立即运行 115 洗白整理任务",
                "handler": self.execute
            }
        ]

    def stop_service(self):
        pass
